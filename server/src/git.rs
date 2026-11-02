use std::{collections::BTreeMap, path::{Path, PathBuf}, process::{Output, Stdio}, sync::Arc, time::Duration};
use tokio::{io::{AsyncRead, AsyncReadExt}, process::{Child, Command}, sync::{Semaphore, oneshot, watch}};
use serde_json::{json, Value};
use crate::{error::{Error, Result}, validation};

pub const OUTPUT_LIMIT: usize = 1024 * 1024;

pub fn arguments(value: &Value) -> Result<Vec<String>> {
    let arguments = validation::argv(value)?;
    let words: Vec<&str> = arguments.iter().map(String::as_str).collect();
    let mut offset = 0;
    let mut git_dir = false;
    while offset < words.len() {
        match words[offset] {
            "--no-optional-locks" => offset += 1,
            "-c" if words.get(offset+1).is_some_and(|value| matches!(*value,"core.fsmonitor=false"|"diff.autoRefreshIndex=false")) => offset += 2,
            "--git-dir" if !git_dir && words.get(offset+1).is_some_and(|value| safe_absolute(value)) => { git_dir=true; offset += 2; },
            _ => break,
        }
    }
    let words = &words[offset..];
    if git_dir && !words.first().is_some_and(|value| matches!(*value,"config"|"rev-parse"|"cat-file"|"rev-list")) { return Err(Error::bad("--git-dir is restricted to repository inspection")); }
    let accepted = match words {
        ["status", "--porcelain=v1", "-z"] | ["status", "--porcelain=v1", "--branch", "-z"] => true,
        ["status","--porcelain"] | ["status","--porcelain=v2","--branch"] | ["status","--porcelain=v1","-z","--no-renames","--untracked-files=normal"] | ["status","--porcelain=v1","-z","--no-renames","--untracked-files=all"] => true,
        ["rev-parse", "--show-toplevel"] | ["rev-parse", "--git-dir"] | ["rev-parse", "--is-inside-work-tree"] => true,
        ["rev-parse","--absolute-git-dir"] | ["rev-parse","--path-format=absolute","--git-common-dir"] => true,
        ["rev-parse",revision] => revision.starts_with("HEAD:") && revision.len()<=8192 && !revision.contains('\0'),
        ["rev-parse", "--verify", reference] | ["rev-parse", "--abbrev-ref", reference] => safe_ref(reference),
        ["show-ref", "--heads"] | ["show-ref", "--heads", "--tags"] => true,
        ["show-ref","--verify","--quiet",reference] => safe_ref(reference),
        ["for-each-ref", "--format=%(refname:short)", "refs/heads"] => true,
        ["for-each-ref","--format=%(refname:short)%09%(committerdate:unix)","refs/heads"] => true,
        ["rev-list", "--count", reference] => safe_ref(reference),
        ["rev-list","-n1","--all","--not","--remotes"] => true,
        ["rev-list","-n1","--all","--not","--remotes",reference] => safe_ref(reference),
        ["cat-file", "-t", reference] | ["cat-file", "-s", reference] => safe_ref(reference),
        ["cat-file","-e","HEAD:.gitmodules"] => true,
        ["config", "--get-regexp", "^branch\\..*\\.(remote|merge)$"] => true,
        ["config","-z","--get-regexp","^submodule\\..*\\.path$"] | ["config","--blob","HEAD:.gitmodules","-z","--get-regexp","^submodule\\..*\\.path$"] => true,
        ["worktree", "list", "--porcelain"] => true,
        ["worktree", "add", "--", path, reference] => safe_absolute(path) && safe_ref(reference),
        ["worktree","add",path,reference] => safe_absolute(path) && safe_ref(reference),
        ["worktree","add","-b",branch,path,"HEAD"] => safe_absolute(path) && safe_ref(branch),
        ["worktree", "add", "-b", branch, "--", path, reference] => safe_ref(branch) && safe_absolute(path) && safe_ref(reference),
        ["worktree", "remove", "--", path] => safe_absolute(path),
        ["worktree","remove",path] | ["worktree","remove","--force",path] => safe_absolute(path),
        ["add", "-A"] | ["add", "--all"] => true,
        ["commit", "-m", message] => !message.is_empty(),
        ["diff", tail @ ..] => valid_diff(tail),
        _ => false,
    };
    if !accepted { return Err(Error::bad("unsupported Git argument form; see docs/GIT.md")); }
    Ok(arguments)
}

fn safe_ref(reference: &str) -> bool {
    !reference.is_empty() && reference.len() <= 512 && !reference.starts_with('-')
        && reference.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"/._-~^@{}".contains(&byte))
}

fn safe_absolute(path: &str) -> bool { path.starts_with('/') && path.len() <= 8192 && !path.contains('\0') }

fn valid_diff(arguments: &[&str]) -> bool {
    let mut seen = std::collections::HashSet::new();
    let mut index = 0;
    while index < arguments.len() {
        let argument = arguments[index];
        if argument == "--" {
            return arguments[index + 1..].iter().all(|path| !path.is_empty() && !path.starts_with(':'));
        }
        if !matches!(argument, "HEAD" | "-z" | "--no-renames" | "--cached" | "--stat" | "--numstat" | "--name-only" | "--name-status" | "--no-color" | "--no-ext-diff" | "--no-textconv" | "--unified=3") || !seen.insert(argument) {
            return false;
        }
        index += 1;
    }
    true
}

pub async fn read_output(mut reader: impl AsyncRead + Unpin) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    let mut buffer = [0_u8; 8192];
    loop {
        let length = reader.read(&mut buffer).await?;
        if length == 0 { return Ok(output); }
        if output.len() + length > OUTPUT_LIMIT { return Err(Error::bad("Git output exceeds one MiB")); }
        output.extend_from_slice(&buffer[..length]);
    }
}

fn command(cwd: &Path) -> Command {
    let mut command = Command::new("git");
    for (name, _) in std::env::vars_os() {
        if name.to_string_lossy().starts_with("GIT_") { command.env_remove(name); }
    }
    command.args(["--no-pager", "-c", "core.fsmonitor=false", "-c", "diff.external=", "-c", "core.pager=cat", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false"]);
    command.current_dir(cwd).env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_CONFIG_NOSYSTEM", "1").env("GIT_CONFIG_GLOBAL", "/dev/null")
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
    command
}

fn identity_arguments(output: &[u8]) -> Result<Vec<String>> {
    let text = std::str::from_utf8(output).map_err(|_| Error::bad("Git identity must use UTF-8"))?;
    if !text.is_empty() && !text.ends_with('\0') { return Err(Error::bad("incomplete Git identity response")); }
    let mut fields = BTreeMap::new();
    for record in text.split_terminator('\0') {
        let (key, value) = record.split_once('\n').ok_or_else(|| Error::bad("Git identity must have a value"))?;
        if !matches!(key, "user.name" | "user.email" | "author.name" | "author.email" | "committer.name" | "committer.email") {
            return Err(Error::bad("unexpected Git identity field"));
        }
        if value.len() > 8192 { return Err(Error::bad("Git identity field exceeds 8192 bytes")); }
        fields.insert(key, value);
    }
    for role in ["author", "committer"] {
        for field in ["name", "email"] {
            let specific = format!("{role}.{field}");
            let fallback = format!("user.{field}");
            if fields.get(specific.as_str()).or_else(|| fields.get(fallback.as_str())).is_none_or(|value| value.trim().is_empty()) {
                return Err(Error(axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                    format!("Configure Git {role} {field} before committing ({fallback} or {specific})")));
            }
        }
    }
    Ok(fields.into_iter().flat_map(|(key, value)| ["-c".into(), format!("{key}={value}")]).collect())
}

async fn run_child(command: &mut Command, owned: &mut Option<Child>) -> Result<Output> {
    *owned = Some(command.spawn()?);
    let child = owned.as_mut().unwrap();
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let (stdout, stderr, status) = tokio::try_join!(
        read_output(stdout), read_output(stderr),
        async { child.wait().await.map_err(Error::from) }
    )?;
    Ok(Output { status, stdout, stderr })
}

fn response(output: Output) -> Value {
    json!({"code":output.status.code().unwrap_or(-1), "stdout":String::from_utf8_lossy(&output.stdout), "stderr":String::from_utf8_lossy(&output.stderr)})
}

pub struct Jobs { capacity: Arc<Semaphore>, shutdown: watch::Receiver<bool> }

impl Jobs {
    pub fn new(shutdown: watch::Receiver<bool>) -> Self {
        Self { capacity: Arc::new(Semaphore::new(4)), shutdown }
    }

    pub async fn run(&self, request: Value) -> Result<Value> {
        validation::object(&request, &["cwd", "args"])?;
        let cwd: PathBuf = crate::extras::absolute_path(&request["cwd"])?;
        let arguments = arguments(&request["args"])?;
        if *self.shutdown.borrow() { return Err(Error::unavailable("server is stopping")); }
        let permit = self.capacity.clone().try_acquire_owned()
            .map_err(|_| Error(axum::http::StatusCode::TOO_MANY_REQUESTS, "four Git jobs are already running".into()))?;
        let mut shutdown = self.shutdown.clone();
        let (mut sender, receiver) = oneshot::channel();
        tokio::spawn(async move {
            let _permit = permit;
            let mut child = None;
            let result = {
                let operation = async {
                    let mut command = command(&cwd);
                    let mut index = 0;
                    while index < arguments.len() {
                        match arguments[index].as_str() { "--no-optional-locks" => index += 1, "-c" | "--git-dir" => index += 2, _ => break }
                    }
                    if arguments.get(index).is_some_and(|argument| argument == "commit") {
                        let mut identity = self::command(&cwd);
                        for name in ["GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"] {
                            match std::env::var_os(name) {
                                Some(value) => { identity.env(name, value); }
                                None => { identity.env_remove(name); }
                            }
                        }
                        identity.args(["config", "--includes", "--null", "--get-regexp", "^(user|author|committer)\\.(name|email)$"]);
                        let output = run_child(&mut identity, &mut child).await?;
                        if !output.status.success() && output.status.code() != Some(1) { return Ok(response(output)); }
                        command.args(identity_arguments(&output.stdout)?)
                            .args(["-c", "user.useConfigOnly=true"]).env_remove("EMAIL");
                    }
                    if arguments.get(index).is_some_and(|argument| argument == "diff") {
                        command.args(&arguments[..=index]).args(["--no-ext-diff", "--no-textconv"]).args(&arguments[index+1..]);
                    } else {
                        command.args(&arguments);
                    }
                    run_child(&mut command, &mut child).await.map(response)
                };
                tokio::select! {
                    biased;
                    _ = sender.closed() => Err(Error::unavailable("Git request was cancelled")),
                    _ = shutdown.wait_for(|value| *value) => Err(Error::unavailable("server is stopping")),
                    result = tokio::time::timeout(Duration::from_secs(15), operation) => result.unwrap_or_else(|_| Err(Error::unavailable("Git timed out after 15 seconds"))),
                }
            };
            if let Some(child) = child.as_mut() && !matches!(child.try_wait(), Ok(Some(_))) {
                let _ = child.start_kill();
                if tokio::time::timeout(Duration::from_secs(2), child.wait()).await.is_err() {
                    eprintln!("owned Git child {:?} did not reap within two seconds", child.id());
                }
            }
            let _ = sender.send(result);
        });
        receiver.await.map_err(|_| Error::unavailable("Git supervisor ended unexpectedly"))?
    }

    pub async fn drain(&self) -> Result<()> {
        let _all = tokio::time::timeout(Duration::from_secs(3), self.capacity.acquire_many(4)).await
            .map_err(|_| Error::unavailable("Git jobs did not drain within three seconds"))?
            .map_err(|_| Error::unavailable("Git supervisor closed"))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_option_injection_and_unbounded_command_families() {
        for arguments in [
            vec!["push"], vec!["status", "--help"], vec!["diff", "--output=/tmp/steal"],
            vec!["-c", "alias.exploit=!evil", "exploit"], vec!["diff", "--ext-diff"],
            vec!["diff", "--textconv"], vec!["worktree", "remove", "--force", "--force", "/tmp/work"],
            vec!["rev-parse", "--verify", "--help"], vec!["config", "user.email", "changed"],
            vec!["diff", "--", ":(attr:secret)"],
        ] { assert!(super::arguments(&json!(arguments)).is_err(), "accepted {arguments:?}"); }
        assert!(super::arguments(&json!(["diff", "--cached", "--no-color", "--", "-filename"])).is_ok());
        assert!(super::arguments(&json!(["commit", "-m", "message with ' quotes"])).is_ok());
    }

    #[tokio::test]
    async fn output_is_bounded() {
        let bytes = vec![b'a'; OUTPUT_LIMIT + 1];
        assert!(read_output(bytes.as_slice()).await.is_err());
        assert_eq!(read_output(&b"ok"[..]).await.unwrap(), b"ok");
    }

    #[test]
    fn identity_is_narrow_and_last_configured_value_wins() {
        assert_eq!(identity_arguments(b"user.name\nGlobal\0user.name\nLocal = Name\0user.email\nlocal@example.invalid\0").unwrap(),
            vec!["-c", "user.email=local@example.invalid", "-c", "user.name=Local = Name"]);
        for output in [b"".as_slice(), b"core.hooksPath\n/tmp/hooks\0", b"user.name\0", b"user.name\ntruncated", b"user.name\n\xff\0",
            b"user.name\nName\0user.email\n \0", b"user.name\nName\0user.email\nfixture@example.invalid\0author.email\n\0"] {
            assert!(identity_arguments(output).is_err());
        }
        assert!(identity_arguments(format!("user.name\n{}\0", "x".repeat(8193)).as_bytes()).is_err());
    }
}
