use std::{path::PathBuf, sync::Arc, time::Duration};
use serde_json::{json, Value};
use tokio::{io::AsyncWriteExt, net::UnixStream, process::Command, sync::Mutex};
use crate::{error::{Error, Result}, framing, validation};

pub const PARKED: &str = "__herdr_web_parked__";
pub struct Backend {
    pub binary: PathBuf,
    pub socket: PathBuf,
    pub config: Option<std::ffi::OsString>,
    pub home: PathBuf,
    pub moves: Mutex<()>,
    launches: Mutex<()>,
}

impl Backend {
    pub async fn discover() -> Result<Arc<Self>> {
        let binary = std::env::var_os("HERDR_BIN").unwrap_or_else(|| "herdr".into());
        let requested = std::env::var("HERDR_SESSION").unwrap_or_else(|_| "default".into());
        let mut command = Command::new(&binary);
        for (key, _) in std::env::vars_os() {
            if key.to_string_lossy().starts_with("HERDR_") { command.env_remove(key); }
        }
        if let Some(config) = std::env::var_os("HERDR_CONFIG_PATH") { command.env("HERDR_CONFIG_PATH", config); }
        command.args(["session", "list", "--json"]).kill_on_drop(true);
        let output = tokio::time::timeout(Duration::from_secs(4), command.output()).await
            .map_err(|_| Error::unavailable("Herdr session discovery timed out"))??;
        if !output.status.success() { return Err(Error::backend(String::from_utf8_lossy(&output.stderr))); }
        if output.stdout.len() > framing::JSON_LIMIT { return Err(Error::backend("session list exceeds eight MiB")); }
        let catalog: Value = serde_json::from_slice(&output.stdout).map_err(|_| Error::backend("invalid session list JSON"))?;
        let sessions = catalog["sessions"].as_array().ok_or_else(|| Error::backend("session list has no sessions array"))?;
        let matches: Vec<_> = sessions.iter().filter(|session| session["name"] == requested).collect();
        if matches.len() != 1 || matches[0]["running"] != true {
            return Err(Error::unavailable(format!("Herdr session {requested:?} must identify exactly one running server; start it with herdr --session NAME first")));
        }
        let socket = matches[0]["socket_path"].as_str().ok_or_else(|| Error::backend("session has no socket path"))?;
        Self::connect(PathBuf::from(binary), PathBuf::from(socket)).await
    }

    pub async fn connect(binary: PathBuf, socket: PathBuf) -> Result<Arc<Self>> {
        let backend = Arc::new(Self {
            binary, socket: socket.canonicalize()?, config: std::env::var_os("HERDR_CONFIG_PATH"),
            home: std::env::var_os("HOME").map(PathBuf::from).ok_or_else(|| Error::bad("HOME is required"))?,
            moves: Mutex::new(()),
            launches: Mutex::new(()),
        });
        backend.snapshot().await?;
        Ok(backend)
    }

    pub async fn request(&self, method: &str, params: Value) -> Result<Value> {
        tokio::time::timeout(Duration::from_secs(4), async {
            let mut stream = UnixStream::connect(&self.socket).await?;
            let mut bytes = serde_json::to_vec(&json!({"id":"web", "method":method, "params":params})).unwrap();
            bytes.push(b'\n');
            stream.write_all(&bytes).await?;
            framing::result(framing::json_line(&mut stream, framing::JSON_LIMIT).await?)
        }).await.map_err(|_| Error::backend(format!("Herdr {method} timed out after four seconds")))?
    }

    pub async fn snapshot(&self) -> Result<Value> {
        let result = self.request("session.snapshot", json!({})).await?;
        let snapshot = result.get("snapshot").ok_or_else(|| Error::backend("missing session snapshot"))?;
        if snapshot["protocol"] != 22 { return Err(Error::backend(format!("incompatible Herdr JSON protocol {}; expected 22", snapshot["protocol"]))); }
        for key in ["workspaces", "tabs", "panes"] {
            if !snapshot[key].is_array() { return Err(Error::backend(format!("invalid snapshot {key}"))); }
        }
        Ok(snapshot.clone())
    }

    pub async fn state(&self, requested: Option<&str>) -> Result<Value> {
        let snapshot = self.snapshot().await?;
        let tabs = snapshot["tabs"].as_array().unwrap();
        let selected = requested.and_then(|id| tabs.iter().find(|tab| tab["tab_id"] == id))
            .or_else(|| tabs.iter().find(|tab| tab["tab_id"] == snapshot["focused_tab_id"]))
            .or_else(|| tabs.first());
        let layout = if let Some(tab) = selected {
            self.request("layout.export", json!({"tab_id":tab["tab_id"]})).await?["layout"].clone()
        } else { Value::Null };
        Ok(json!({"snapshot":snapshot, "layout":layout, "home":self.home}))
    }

    pub async fn pane(&self, pane_id: &str) -> Result<Value> {
        let snapshot = self.snapshot().await?;
        find_pane(&snapshot, "pane_id", pane_id)
    }

    pub async fn launch(&self, pane: &str, argv: Vec<String>) -> Result<Value> {
        let _launch = self.launches.lock().await;
        self.pane(pane).await?;
        if argv.is_empty() { return Ok(json!({"launched":false})); }
        let command = argv.iter().map(|argument| validation::shell_quote(argument)).collect::<Vec<_>>().join(" ");
        self.request("pane.send_input", json!({"pane_id":pane,"text":command,"keys":["enter"]})).await?;
        Ok(json!({"launched":true}))
    }

    pub async fn action(&self, request: &Value) -> Result<Value> {
        let _guard = self.moves.lock().await;
        let snapshot = self.snapshot().await?;
        let terminal = request["terminal"].as_str().unwrap();
        let pane = find_pane(&snapshot, "terminal_id", terminal)?;
        if request["action"] == "kill" {
            return self.request("pane.close", json!({"pane_id":pane["pane_id"]})).await;
        }
        let parked = request["action"] == "park";
        let workspace = snapshot["workspaces"].as_array().unwrap().iter().find(|workspace| {
            if parked { workspace["label"] == PARKED }
            else if let Some(space) = request["space"].as_str() { workspace["workspace_id"] == space && workspace["label"] != PARKED }
            else { workspace["label"] == "Terminals" }
        });
        if request["space"].is_string() && !parked && workspace.is_none() { return Err(Error::bad("destination space does not exist")); }
        if workspace.is_some_and(|workspace| workspace["workspace_id"] == pane["workspace_id"]) {
            return Ok(json!({"terminal":terminal,"pane":pane["pane_id"],"unchanged":true}));
        }
        let destination = workspace.map(|workspace| json!({"type":"new_tab", "workspace_id":workspace["workspace_id"]}))
            .unwrap_or_else(|| json!({"type":"new_workspace","label":if parked { PARKED } else { "Terminals" }}));
        self.request("pane.move", json!({"pane_id":pane["pane_id"],"destination":destination,"focus":false})).await
    }

    pub async fn child(&self, request: &Value) -> Result<Value> {
        let _guard = self.moves.lock().await;
        let snapshot = self.snapshot().await?;
        let parent_id = request["parent"].as_str().unwrap();
        let parent = find_pane(&snapshot, "terminal_id", parent_id)?;
        let panes = snapshot["panes"].as_array().unwrap();
        if panes.iter().filter(|pane| pane["tokens"]["web_parent"].is_string()).count() >= 64 { return Err(Error::bad("session already has 64 managed child terminals")); }
        validate_parent_chain(panes,parent_id)?;
        let name = request["name"].as_str().filter(|name| !name.trim().is_empty()).unwrap_or("Worker terminal");
        let cwd = parent["foreground_cwd"].as_str().or_else(|| parent["cwd"].as_str()).unwrap_or(self.home.to_str().unwrap_or("/"));
        let destination = snapshot["workspaces"].as_array().unwrap().iter().find(|space| space["label"] == PARKED);
        let created = if let Some(space) = destination {
            self.request("tab.create", json!({"workspace_id":space["workspace_id"],"label":name,"cwd":cwd,"focus":false})).await?
        } else {
            self.request("workspace.create", json!({"label":PARKED,"cwd":cwd,"focus":false})).await?
        };
        let child = &created["root_pane"];
        let pane_id = child["pane_id"].as_str().ok_or_else(|| Error::backend("created child has no pane ID"))?;
        let result = async {
            let (parent_head,parent_tail)=metadata_parts(parent_id);
            let (name_head,name_tail)=metadata_parts(name);
            self.request("pane.report_metadata", json!({"pane_id":pane_id,"source":"herdr-web","tokens":{"web_parent":parent_head,"web_parent_tail":parent_tail,"web_name":name_head,"web_name_tail":name_tail}})).await?;
            self.request("pane.rename", json!({"pane_id":pane_id,"label":name})).await?;
            self.launch(pane_id, validation::argv(&request["argv"])?).await?;
            Ok(json!({"terminal":child["terminal_id"],"pane":pane_id,"parent":parent_id}))
        }.await;
        if result.is_err() { let _ = self.request("pane.close", json!({"pane_id":pane_id})).await; }
        result
    }
}

pub fn find_pane(snapshot: &Value, field: &str, id: &str) -> Result<Value> {
    snapshot["panes"].as_array().and_then(|panes| panes.iter().find(|pane| pane[field] == id)).cloned()
        .ok_or_else(|| Error::bad(format!("no current pane has {field} {id:?}")))
}

fn metadata_parts(value:&str)->(&str,&str){let boundary=value.char_indices().nth(80).map_or(value.len(),|(offset,_)|offset);value.split_at(boundary)}

fn validate_parent_chain(panes:&[Value],parent:&str)->Result<()>{
    let mut seen=std::collections::HashSet::new();
    let mut current=parent.to_owned();
    let mut depth=1;
    loop{
        depth+=1;
        if depth>32||!seen.insert(current.clone()){return Err(Error::bad("parent chain is cyclic or exceeds 32 levels"));}
        let Some(pane)=panes.iter().find(|pane|pane["terminal_id"]==current)else{break;};
        let Some(next)=pane["tokens"]["web_parent"].as_str()else{break;};
        current=format!("{}{}",next,pane["tokens"]["web_parent_tail"].as_str().unwrap_or(""));
    }
    Ok(())
}

#[cfg(test)]
mod tests{
    use super::*;
    use tokio::{io::AsyncReadExt,net::UnixListener};

    struct SocketDirectory(PathBuf);
    impl Drop for SocketDirectory{fn drop(&mut self){let _=std::fs::remove_dir_all(&self.0);}}

    fn endpoint()->(SocketDirectory,UnixListener,Backend){
        use std::os::unix::fs::DirBuilderExt;
        static NEXT:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
        let sequence=NEXT.fetch_add(1,std::sync::atomic::Ordering::Relaxed);
        let directory=std::env::temp_dir().join(format!("herdr-web-rpc-{}-{sequence}",std::process::id()));
        std::fs::DirBuilder::new().mode(0o700).create(&directory).unwrap();
        let socket=directory.join("api.sock");
        let listener=UnixListener::bind(&socket).unwrap();
        let backend=Backend{binary:PathBuf::from("unused-test-binary"),socket,config:None,home:directory.clone(),moves:Mutex::new(()),launches:Mutex::new(())};
        (SocketDirectory(directory),listener,backend)
    }

    #[tokio::test]
    async fn unix_requests_preserve_fragmented_utf8_and_backend_errors(){
        let (_directory,listener,backend)=endpoint();
        let peer=tokio::spawn(async move{
            for response in ["{\"result\":\"猫 café\"}\n","{\"error\":{\"message\":\"busy 猫\"}}\n"]{
                let (mut connection,_)=listener.accept().await.unwrap();
                let request=framing::json_line(&mut connection,16384).await.unwrap();
                assert_eq!(request["method"],"session.snapshot");
                assert_eq!(request["params"]["label"],"日本語");
                for byte in response.as_bytes(){connection.write_all(&[*byte]).await.unwrap();tokio::time::sleep(Duration::from_millis(1)).await;}
            }
        });
        assert_eq!(backend.request("session.snapshot",json!({"label":"日本語"})).await.unwrap(),"猫 café");
        assert_eq!(backend.request("session.snapshot",json!({"label":"日本語"})).await.unwrap_err().1,"busy 猫");
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn request_timeout_closes_a_partially_read_unix_connection(){
        let (_directory,listener,backend)=endpoint();
        let peer=tokio::spawn(async move{
            let (mut connection,_)=listener.accept().await.unwrap();
            framing::json_line(&mut connection,16384).await.unwrap();
            connection.write_all(b"{\"result\":").await.unwrap();
            assert_eq!(connection.read(&mut [0u8;1]).await.unwrap(),0);
        });
        let started=tokio::time::Instant::now();
        let error=backend.request("session.snapshot",json!({})).await.unwrap_err();
        assert_eq!(error.0,axum::http::StatusCode::BAD_GATEWAY);
        assert!(error.1.contains("timed out after four seconds"));
        assert!(started.elapsed()>=Duration::from_millis(3900));
        assert!(started.elapsed()<Duration::from_secs(8));
        tokio::time::timeout(Duration::from_secs(2),peer).await.unwrap().unwrap();
    }

    #[tokio::test]
    async fn real_socket_responses_require_newline_and_respect_eight_mib(){
        let (_directory,listener,backend)=endpoint();
        let peer=tokio::spawn(async move{
            for oversized in [false,true]{
                let (mut connection,_)=listener.accept().await.unwrap();
                framing::json_line(&mut connection,16384).await.unwrap();
                if oversized{let mut response=vec![b' ';framing::JSON_LIMIT];response.push(b'\n');let _=connection.write_all(&response).await;}
                else{connection.write_all(b"{\"result\":true}").await.unwrap();}
            }
        });
        assert!(backend.request("session.snapshot",json!({})).await.unwrap_err().1.contains("final newline"));
        assert!(backend.request("session.snapshot",json!({})).await.unwrap_err().1.contains("exceeds byte limit"));
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn snapshot_rejects_a_different_public_protocol(){
        let (_directory,listener,backend)=endpoint();
        let peer=tokio::spawn(async move{
            let (mut connection,_)=listener.accept().await.unwrap();
            framing::json_line(&mut connection,16384).await.unwrap();
            connection.write_all(b"{\"result\":{\"snapshot\":{\"protocol\":23,\"workspaces\":[],\"tabs\":[],\"panes\":[]}}}\n").await.unwrap();
        });
        assert!(backend.snapshot().await.unwrap_err().1.contains("protocol 23; expected 22"));
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn simultaneous_requests_use_independent_connections(){
        let (_directory,listener,backend)=endpoint();
        let peer=tokio::spawn(async move{
            let mut requests=Vec::new();
            for _ in 0..2{let (mut connection,_)=listener.accept().await.unwrap();let request=framing::json_line(&mut connection,16384).await.unwrap();requests.push((connection,request));}
            for (mut connection,request) in requests{let response=json!({"result":request["params"]["token"]}).to_string()+"\n";connection.write_all(response.as_bytes()).await.unwrap();}
        });
        let (first,second)=tokio::time::timeout(Duration::from_secs(2),async{tokio::join!(backend.request("session.snapshot",json!({"token":"first"})),backend.request("session.snapshot",json!({"token":"second"})))}).await.unwrap();
        assert_eq!(first.unwrap(),"first");assert_eq!(second.unwrap(),"second");peer.await.unwrap();
    }

    #[test]
    fn metadata_preserves_128_byte_names_with_backend_80_character_tokens(){
        for value in ["x".repeat(128),"猫".repeat(42)]{let (head,tail)=metadata_parts(&value);assert!(head.chars().count()<=80&&tail.chars().count()<=80);assert_eq!(format!("{head}{tail}"),value);}
    }
    #[test]
    fn child_depth_includes_the_new_child_and_cycles_are_rejected(){
        let mut panes=vec![json!({"terminal_id":"root","tokens":{}})];
        let mut parent="root".to_owned();
        for index in 1..31{let id=format!("child-{index}");panes.push(json!({"terminal_id":id,"tokens":{"web_parent":parent}}));parent=id;}
        assert!(validate_parent_chain(&panes,&parent).is_ok());
        panes.push(json!({"terminal_id":"one-too-many","tokens":{"web_parent":parent}}));
        assert!(validate_parent_chain(&panes,"one-too-many").is_err());
        assert!(validate_parent_chain(&[json!({"terminal_id":"cycle","tokens":{"web_parent":"cycle"}})],"cycle").is_err());
    }
}
