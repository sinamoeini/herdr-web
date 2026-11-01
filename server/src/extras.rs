use std::{fs::{self, OpenOptions}, io::Read, os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt}, path::{Path, PathBuf}, sync::atomic::{AtomicU64, Ordering}};
use serde_json::{Value, json};
use crate::{error::{Error, Result}, validation};

pub const TEXT_LIMIT: u64 = 512 * 1024;
pub const IMAGE_LIMIT: usize = 10 * 1024 * 1024;
static UNIQUE: AtomicU64 = AtomicU64::new(0);

pub fn absolute_path(value: &Value) -> Result<PathBuf> {
    let path = validation::text(value, "path", 8192, false)?;
    let path = PathBuf::from(path);
    if !path.is_absolute() { return Err(Error::bad("path must be absolute")); }
    Ok(path)
}

pub fn files(request: &Value) -> Result<Value> {
    validation::object(request, &["action", "path"])?;
    let path = absolute_path(&request["path"])?;
    match request["action"].as_str() {
        Some("read") => read_text(&path).map(|text| json!({"text":text})),
        Some("list") => {
            let mut entries = Vec::new();
            let mut truncated = false;
            for entry in fs::read_dir(path)? {
                let entry = entry?;
                let Some(name) = entry.file_name().to_str().map(str::to_owned) else { continue; };
                if name == ".git" { continue; }
                if entries.len() == 500 { truncated = true; break; }
                entries.push(json!({"name":name, "dir":entry.file_type()?.is_dir()}));
            }
            entries.sort_by(|left, right| left["name"].as_str().cmp(&right["name"].as_str()));
            Ok(json!({"entries":entries, "truncated":truncated}))
        }
        _ => Err(Error::bad("action must be read or list")),
    }
}

fn read_text(path: &Path) -> Result<String> {
    let file = OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW | libc::O_CLOEXEC).open(path)
        .map_err(|error| if error.raw_os_error() == Some(libc::ELOOP) { Error::bad("symlink files are not previewed") } else { Error::from(error) })?;
    let metadata = file.metadata()?;
    if !metadata.is_file() { return Err(Error::bad("only regular text files can be previewed")); }
    if metadata.len() > TEXT_LIMIT { return Err(Error::bad("text file exceeds 512 KiB")); }
    let mut bytes = Vec::new();
    file.take(TEXT_LIMIT + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > TEXT_LIMIT { return Err(Error::bad("text file grew beyond 512 KiB")); }
    if bytes.iter().any(|byte| *byte < 32 && !matches!(*byte, 9 | 10 | 12 | 13)) {
        return Err(Error::bad("binary content cannot be previewed"));
    }
    String::from_utf8(bytes).map_err(|_| Error::bad("file is not UTF-8 text"))
}

pub fn image_extension(mime: &str, bytes: &[u8]) -> Result<&'static str> {
    if bytes.len() > IMAGE_LIMIT { return Err(Error::bad("image exceeds 10 MiB")); }
    match mime {
        "image/png" if bytes.len() >= 33 && bytes.starts_with(b"\x89PNG\r\n\x1a\n") && &bytes[12..16] == b"IHDR" => Ok("png"),
        "image/jpeg" if bytes.len() >= 4 && bytes.starts_with(b"\xff\xd8\xff") && bytes.ends_with(b"\xff\xd9") => Ok("jpg"),
        "image/gif" if bytes.len() >= 13 && (bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a")) => Ok("gif"),
        "image/webp" if bytes.len() >= 16 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP" && matches!(&bytes[12..16], b"VP8 " | b"VP8L" | b"VP8X") => Ok("webp"),
        _ => Err(Error::bad("image MIME type and PNG, JPEG, GIF, or WebP signature must agree")),
    }
}

pub struct Uploads { directory: PathBuf }

impl Uploads {
    pub fn create() -> Result<Self> {
        for _attempt in 0..64 {
            let unique = UNIQUE.fetch_add(1, Ordering::Relaxed);
            let directory = std::env::temp_dir().join(format!("herdr-web-images-{}-{unique}", std::process::id()));
            match fs::DirBuilder::new().mode(0o700).create(&directory) {
                Ok(()) => return Ok(Self { directory }),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err(Error::unavailable("cannot create a private upload directory"))
    }

    pub fn save(&self, mime: &str, bytes: &[u8]) -> Result<PathBuf> {
        use std::io::Write;
        let extension = image_extension(mime, bytes)?;
        let metadata = fs::symlink_metadata(&self.directory)?;
        if !metadata.is_dir() || metadata.permissions().mode() & 0o777 != 0o700 {
            return Err(Error::unavailable("upload directory is no longer private"));
        }
        let path = self.directory.join(format!("image-{}.{}", UNIQUE.fetch_add(1, Ordering::Relaxed), extension));
        let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC).open(&path)?;
        if let Err(error) = file.write_all(bytes) {
            drop(file);
            let _ = fs::remove_file(&path);
            return Err(error.into());
        }
        Ok(path)
    }
}

pub fn agent_catalog() -> Vec<&'static str> {
    const PROGRAMS: &[&str] = &["claude", "codex", "pi", "opencode", "cursor-agent", "copilot", "amp", "grok", "kimi", "kiro-cli", "hermes", "qodercli", "crush", "gemini", "ollama"];
    let search = std::env::var_os("PATH").unwrap_or_default();
    let directories: Vec<_> = std::env::split_paths(&search).filter(|path| path.is_absolute()).collect();
    PROGRAMS.iter().copied().filter(|program| directories.iter().any(|directory| {
        use std::os::unix::ffi::OsStrExt;
        let path=directory.join(program);
        fs::metadata(&path).is_ok_and(|metadata|metadata.is_file())&&std::ffi::CString::new(path.as_os_str().as_bytes()).is_ok_and(|path|unsafe{libc::access(path.as_ptr(),libc::X_OK)}==0)
    })).collect()
}

pub struct Mods { folders: std::collections::BTreeMap<String, PathBuf> }

impl Mods {
    pub fn discover(root: Option<&Path>) -> Result<Self> {
        let mut folders = std::collections::BTreeMap::new();
        if let Some(root) = root {
            let root = root.canonicalize()?;
            for entry in fs::read_dir(&root)? {
                let entry = entry?;
                let name = entry.file_name().to_string_lossy().into_owned();
                if !safe_mod_name(&name) || !entry.file_type()?.is_dir() { continue; }
                let folder = entry.path().canonicalize()?;
                let main = folder.join("main.js");
                if folder.starts_with(&root) && main.is_file() && main.canonicalize()?.starts_with(&folder) {
                    folders.insert(name, folder);
                }
            }
        }
        Ok(Self { folders })
    }

    pub fn names(&self) -> Vec<&str> { self.folders.keys().map(String::as_str).collect() }

    pub fn read(&self, request: &str) -> Result<(&'static str, Vec<u8>)> {
        let (name, relative) = request.split_once('/').ok_or_else(|| Error::bad("panel asset requires a registered name and file"))?;
        let folder = self.folders.get(name).ok_or_else(|| Error(axum::http::StatusCode::NOT_FOUND, "panel is not registered".into()))?;
        let relative = Path::new(relative);
        if relative.as_os_str().is_empty() || !relative.components().all(|component| matches!(component, std::path::Component::Normal(_))) {
            return Err(Error::bad("invalid panel asset path"));
        }
        let path = folder.join(relative).canonicalize()?;
        if !path.starts_with(folder) { return Err(Error::bad("panel asset escapes its registered folder")); }
        let mime = match path.extension().and_then(|extension| extension.to_str()) {
            Some("js") => "text/javascript; charset=utf-8",
            Some("json") => "application/json",
            Some("css") => "text/css; charset=utf-8",
            Some("svg") => "image/svg+xml",
            Some("png") => "image/png",
            _ => return Err(Error::bad("unsupported panel asset extension")),
        };
        let file = OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW | libc::O_CLOEXEC).open(path)?;
        let metadata = file.metadata()?;
        if !metadata.is_file() || metadata.len() > 2 * 1024 * 1024 { return Err(Error::bad("panel asset must be a regular file no larger than two MiB")); }
        let mut bytes = Vec::new();
        file.take(2 * 1024 * 1024 + 1).read_to_end(&mut bytes)?;
        if bytes.len() > 2 * 1024 * 1024 { return Err(Error::bad("panel asset grew beyond two MiB")); }
        Ok((mime, bytes))
    }
}

fn safe_mod_name(name: &str) -> bool {
    !name.is_empty() && name.len() < 80 && name.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Directory(PathBuf);
    impl Directory {
        fn new() -> Self {
            let directory = std::env::temp_dir().join(format!("herdr-web-test-{}-{}", std::process::id(), UNIQUE.fetch_add(1, Ordering::Relaxed)));
            fs::create_dir(&directory).unwrap();
            Self(directory)
        }
    }
    impl Drop for Directory { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }

    #[test]
    fn text_boundaries_and_nonregular_files() {
        let directory = Directory::new();
        let file = directory.0.join("text");
        fs::write(&file, "猫\n<script>not executable</script>").unwrap();
        assert_eq!(read_text(&file).unwrap(), "猫\n<script>not executable</script>");
        fs::write(&file, [0, 1, 2]).unwrap();
        assert!(read_text(&file).is_err());
        fs::write(&file, vec![b'a'; TEXT_LIMIT as usize + 1]).unwrap();
        assert!(read_text(&file).is_err());
        assert!(read_text(&directory.0).is_err());
        assert!(read_text(Path::new("/dev/null")).is_err());
        let fifo = directory.0.join("fifo");
        use std::os::unix::ffi::OsStrExt;
        let fifo_name = std::ffi::CString::new(fifo.as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(fifo_name.as_ptr(), 0o600) }, 0);
        assert!(read_text(&fifo).is_err());
    }

    #[test]
    fn panel_containment_rejects_traversal_and_symlinks() {
        let directory = Directory::new();
        let panel = directory.0.join("notes");
        fs::create_dir(&panel).unwrap();
        fs::write(panel.join("main.js"), "export default 1").unwrap();
        fs::write(directory.0.join("secret.js"), "secret").unwrap();
        std::os::unix::fs::symlink(directory.0.join("secret.js"), panel.join("escape.js")).unwrap();
        let mods = Mods::discover(Some(&directory.0)).unwrap();
        assert_eq!(mods.names(), vec!["notes"]);
        assert!(mods.read("notes/main.js").is_ok());
        assert!(mods.read("notes/../secret.js").is_err());
        assert!(mods.read("notes/escape.js").is_err());
        assert!(mods.read("unknown/main.js").is_err());
    }

    #[test]
    fn upload_permissions_signatures_and_retention() {
        let uploads = Uploads::create().unwrap();
        let mut png = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR".to_vec();
        png.resize(33, 0);
        assert!(uploads.save("image/jpeg", &png).is_err());
        assert!(uploads.save("image/png", b"not a PNG").is_err());
        let path = uploads.save("image/png", &png).unwrap();
        let directory = uploads.directory.clone();
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(fs::metadata(&directory).unwrap().permissions().mode() & 0o777, 0o700);
        drop(uploads);
        assert!(path.exists());
        fs::remove_dir_all(directory).unwrap();
    }
}
