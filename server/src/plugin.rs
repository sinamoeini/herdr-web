use std::{fs::{File, OpenOptions}, os::{fd::AsRawFd, unix::{ffi::OsStrExt, fs::OpenOptionsExt}}, path::Path};
use crate::error::{Error, Result};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::{UnixListener,UnixStream}, sync::watch};
use serde_json::{json,Value};
use crate::backend::Backend;

pub const ID: &str = "local.herdr-web";

pub fn instance_key(socket: &Path) -> String {
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in socket.as_os_str().as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

pub struct InstanceLock { file: File }

impl InstanceLock {
    pub fn acquire(path: &Path) -> Result<Self> {
        let file = OpenOptions::new().read(true).write(true).create(true).truncate(false)
            .mode(0o600).custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC).open(path)?;
        let metadata = file.metadata()?;
        use std::os::unix::fs::MetadataExt;
        if !metadata.is_file() || metadata.uid() != unsafe { libc::geteuid() } || metadata.mode() & 0o777 != 0o600 || metadata.nlink() != 1 {
            return Err(Error::bad("instance lock must be an owned private regular file with one link"));
        }
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            let error=std::io::Error::last_os_error();
            if error.kind()==std::io::ErrorKind::WouldBlock{return Err(Error(axum::http::StatusCode::CONFLICT,"plugin server lock is held".into()));}
            return Err(error.into());
        }
        Ok(Self { file })
    }
}

impl Drop for InstanceLock {
    fn drop(&mut self) { let _ = unsafe { libc::flock(self.file.as_raw_fd(), libc::LOCK_UN) }; }
}

pub struct Runtime {
    pub backend:Arc<Backend>,
    pub port:u16,
    root:PathBuf,
    control:PathBuf,
    parent:u32,
    parent_identity:String,
    tasks:Vec<tokio::task::JoinHandle<()>>,
    _lock:InstanceLock,
}

fn required(name:&str)->Result<PathBuf>{std::env::var_os(name).map(PathBuf::from).filter(|value|value.is_absolute()).ok_or_else(||Error::bad(format!("plugin requires absolute {name}")))}
fn parent_identity(parent:u32)->Result<String>{
    #[cfg(target_os="linux")]
    {let text=std::fs::read_to_string(format!("/proc/{parent}/stat"))?;let tail=text.rsplit_once(')').ok_or_else(||Error::bad("invalid parent process identity"))?.1;Ok(tail.split_whitespace().nth(19).ok_or_else(||Error::bad("missing parent start identity"))?.to_owned())}
    #[cfg(not(target_os="linux"))]
    {Ok(parent.to_string())}
}
async fn control(path:&Path,action:&str)->Result<Value>{
    tokio::time::timeout(Duration::from_secs(3),async{
        let mut stream=UnixStream::connect(path).await?;
        stream.write_all(format!("{action}\n").as_bytes()).await?;
        crate::framing::json_line(&mut stream,4096).await
    }).await.map_err(|_|Error::unavailable("plugin control request timed out"))?
}
async fn registration(backend:&Backend,root:&Path)->Result<()> {
    let catalog=backend.request("plugin.list",json!({"plugin_id":ID})).await?;
    let plugin=catalog["plugins"].as_array().and_then(|items|items.iter().find(|plugin|plugin["plugin_id"]==ID)).ok_or_else(||Error::unavailable("plugin is no longer linked"))?;
    if plugin["enabled"]!=true{return Err(Error::unavailable("plugin is disabled"));}
    let registered=plugin["plugin_root"].as_str().ok_or_else(||Error::bad("plugin catalog has no root"))?;
    if Path::new(registered).canonicalize()?!=root{return Err(Error::unavailable("registered plugin root changed"));}
    Ok(())
}

pub async fn prepare(action:&str)->Result<Option<Runtime>>{
    use std::os::unix::fs::{PermissionsExt,MetadataExt};
    if std::env::var("HERDR_PLUGIN_ID").ok().as_deref()!=Some(ID){return Err(Error::bad("HERDR_PLUGIN_ID must be local.herdr-web; invoke through the native Herdr plugin action"));}
    if !["start","stop","restart","status"].contains(&action){return Err(Error::bad("plugin action must be start, stop, restart, or status"));}
    let root=required("HERDR_PLUGIN_ROOT")?.canonicalize()?;
    let socket=required("HERDR_SOCKET_PATH")?.canonicalize()?;
    let binary=required("HERDR_BIN_PATH")?.canonicalize()?;
    let config=required("HERDR_PLUGIN_CONFIG_DIR")?;
    let state=required("HERDR_PLUGIN_STATE_DIR")?;
    std::fs::create_dir_all(&state)?;
    let metadata=std::fs::symlink_metadata(&state)?;
    if !metadata.is_dir()||metadata.uid()!=unsafe{libc::geteuid()}{return Err(Error::bad("plugin state must be an owned directory, not a symlink"));}
    std::fs::set_permissions(&state,std::fs::Permissions::from_mode(0o700))?;
    let key=instance_key(&socket);let lock_path=state.join(format!("{key}.lock"));let control_path=state.join(format!("{key}.sock"));
    if control_path.as_os_str().as_bytes().len()>100{return Err(Error::bad("plugin control socket path exceeds Unix socket limit; use a shorter state directory"));}
    if action=="status"{let value=control(&control_path,"status").await.unwrap_or_else(|_|json!({"running":false}));println!("{value}");return Ok(None);}
    if action=="stop"||action=="restart"{
        match control(&control_path,"stop").await{Ok(value)=>println!("{value}"),Err(error)=>{if action=="stop"{match InstanceLock::acquire(&lock_path){Ok(_)=>{println!("{}",json!({"running":false}));return Ok(None);},Err(_)=>return Err(error)}}}}
        if action=="stop"{return Ok(None);}
    }
    let deadline=tokio::time::Instant::now()+Duration::from_secs(10);
    let lock=loop{
        match InstanceLock::acquire(&lock_path){
            Ok(lock)=>break lock,
            Err(error) if error.0==axum::http::StatusCode::CONFLICT=>{
                if action=="start"{if let Ok(status)=control(&control_path,"status").await{println!("{status}");return Ok(None);}}
                if tokio::time::Instant::now()>=deadline{return Err(Error::unavailable("previous plugin instance did not release its listener lock within ten seconds"));}
                tokio::time::sleep(Duration::from_millis(100)).await;
            },Err(error)=>return Err(error),
        }
    };
    if control_path.exists(){std::fs::remove_file(&control_path)?;}
    let backend=Backend::connect(binary,socket).await?;
    registration(&backend,&root).await?;
    let port=match std::fs::read_to_string(config.join("port")){Ok(value)=>value.trim().parse::<u16>().map_err(|_|Error::bad("plugin port file must contain an integer from 0 through 65535"))?,Err(error) if error.kind()==std::io::ErrorKind::NotFound=>std::env::var("PORT").unwrap_or_else(|_|crate::DEFAULT_PORT.to_string()).parse().map_err(|_|Error::bad("invalid PORT"))?,Err(error)=>return Err(error.into())};
    let parent=unsafe{libc::getppid()} as u32;
    Ok(Some(Runtime{backend,port,root,control:control_path,parent,parent_identity:parent_identity(parent)?,tasks:Vec::new(),_lock:lock}))
}

impl Runtime{
    pub async fn start(&mut self,url:String,shutdown:watch::Sender<bool>)->Result<()> {
        use std::os::unix::fs::PermissionsExt;
        let listener=UnixListener::bind(&self.control)?;std::fs::set_permissions(&self.control,std::fs::Permissions::from_mode(0o600))?;
        let status=json!({"running":true,"url":url,"pid":std::process::id()});println!("{status}");
        let control_shutdown=shutdown.clone();let mut stopped=shutdown.subscribe();
        self.tasks.push(tokio::spawn(async move{loop{tokio::select!{
            _=stopped.changed()=>break,
            connection=listener.accept()=>{
                let Ok((mut stream,_))=connection else{break;};
                let read=tokio::time::timeout(Duration::from_secs(2),async{let mut input=Vec::new();loop{let byte=stream.read_u8().await?;if byte==b'\n'{break;}input.push(byte);if input.len()>1024{return Err(std::io::Error::other("control request too large"));}}Ok::<_,std::io::Error>(input)}).await;
                let Ok(Ok(input))=read else{continue;};
                let stop=input==b"stop";
                let response=if stop||input==b"status"{status.clone()}else{json!({"error":"expected status or stop"})};
                let _=tokio::time::timeout(Duration::from_secs(1),stream.write_all((response.to_string()+"\n").as_bytes())).await;
                if stop{control_shutdown.send_replace(true);break;}
            }
        }}}));
        let backend=self.backend.clone();let root=self.root.clone();let parent=self.parent;let identity=self.parent_identity.clone();let mut stopped=shutdown.subscribe();
        self.tasks.push(tokio::spawn(async move{let mut poll=tokio::time::interval(Duration::from_secs(2));loop{tokio::select!{
            _=stopped.changed()=>break,
            _=poll.tick()=>{let parent_valid=unsafe{libc::getppid()} as u32==parent&&parent_identity(parent).is_ok_and(|current|current==identity);if !parent_valid||registration(&backend,&root).await.is_err(){shutdown.send_replace(true);break;}}
        }}}));Ok(())
    }
}
impl Drop for Runtime{fn drop(&mut self){for task in &self.tasks{task.abort();}let _=std::fs::remove_file(&self.control);}}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_is_stable_and_socket_specific() {
        assert_eq!(instance_key(Path::new("")), "cbf29ce484222325");
        assert_eq!(instance_key(Path::new("a")), "af63dc4c8601ec8c");
        assert_ne!(instance_key(Path::new("/a/socket")), instance_key(Path::new("/b/socket")));
    }

    #[test]
    fn lock_is_exclusive_until_owner_drops_and_rejects_symlinks() {
        let directory = std::env::temp_dir().join(format!("herdr-web-lock-test-{}", std::process::id()));
        std::fs::create_dir(&directory).unwrap();
        let path = directory.join("instance.lock");
        let owner = InstanceLock::acquire(&path).unwrap();
        assert!(InstanceLock::acquire(&path).is_err());
        drop(owner);
        drop(InstanceLock::acquire(&path).unwrap());
        let link = directory.join("symlink.lock");
        std::os::unix::fs::symlink(&path, &link).unwrap();
        assert!(InstanceLock::acquire(&link).is_err());
        std::fs::remove_dir_all(directory).unwrap();
    }
}
