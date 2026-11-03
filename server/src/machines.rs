use serde_json::Value;
use crate::{error::{Error, Result}, validation};
use std::{collections::BTreeMap,sync::Arc,time::Duration,process::Stdio};
use tokio::{io::{AsyncReadExt,AsyncWriteExt},net::{TcpListener,TcpStream},process::{Child,Command},sync::{Mutex,Semaphore,OwnedSemaphorePermit,watch}};
use serde_json::json;

pub struct Destination { pub host: String, pub port: u16 }

pub fn validate(value: &Value) -> Result<Destination> {
    validation::object(value, &["action", "host", "port"])?;
    if !matches!(value["action"].as_str(), Some("connect" | "disconnect")) { return Err(Error::bad("action must be connect or disconnect")); }
    let host = validation::text(&value["host"], "host", 255, false)?;
    if host.starts_with('-') || !host.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"@._-".contains(&byte))
        || host.matches('@').count() > 1 || host.starts_with('@') || host.ends_with('@') {
        return Err(Error::bad("unsupported SSH destination; use an SSH config alias or user@host"));
    }
    let port = value.get("port").map(|value| validation::integer(value, "port", 1, 65535)).transpose()?.unwrap_or(u64::from(crate::DEFAULT_PORT)) as u16;
    Ok(Destination { host: host.to_owned(), port })
}

pub fn arguments(destination: &Destination, local_port: u16) -> Vec<String> {
    ["-N", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes",
        "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ForkAfterAuthentication=no",
        "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-L"]
        .into_iter().map(str::to_owned).chain([
            format!("127.0.0.1:{local_port}:127.0.0.1:{}", destination.port), "--".into(), destination.host.clone(),
        ]).collect()
}

struct Tunnel{child:Option<Child>,info:Value,diagnostics:tokio::task::JoinHandle<()>,_permit:Option<OwnedSemaphorePermit>}
impl Drop for Tunnel{fn drop(&mut self){self.diagnostics.abort();if let Some(mut child)=self.child.take(){let permit=self._permit.take();let _=child.start_kill();tokio::spawn(async move{let _permit=permit;let _=child.wait().await;});}}}
pub struct Machines{tunnels:Arc<Mutex<BTreeMap<(String,u16),Tunnel>>>,capacity:Arc<Semaphore>,shutdown:watch::Receiver<bool>}
impl Machines{
    pub fn new(shutdown:watch::Receiver<bool>)->Self{Self{tunnels:Arc::new(Mutex::new(BTreeMap::new())),capacity:Arc::new(Semaphore::new(8)),shutdown}}
    pub async fn catalog(&self)->Value{let mut tunnels=self.tunnels.lock().await;tunnels.retain(|_,tunnel|tunnel.child.as_mut().is_some_and(|child|matches!(child.try_wait(),Ok(None))));json!(tunnels.values().map(|tunnel|tunnel.info.clone()).collect::<Vec<_>>())}
    pub async fn action(&self,value:&Value)->Result<Value>{
        let destination=validate(value)?;
        let key=(destination.host.clone(),destination.port);
        self.catalog().await;
        if value["action"]=="disconnect"{
            let removed={
                let mut catalog=self.tunnels.lock().await;
                let keys=catalog.keys().filter(|(host,port)|host==&destination.host&&(value.get("port").is_none()||*port==destination.port)).cloned().collect::<Vec<_>>();
                keys.into_iter().filter_map(|key|catalog.remove(&key)).collect::<Vec<_>>()
            };
            let disconnected=!removed.is_empty();
            for mut tunnel in removed{if let Some(mut child)=tunnel.child.take(){let _=child.start_kill();let _=child.wait().await;}}
            return Ok(json!({"disconnected":disconnected}));
        }
        if *self.shutdown.borrow(){return Err(Error::unavailable("server stopping"));}
        if let Some(tunnel)=self.tunnels.lock().await.get(&key){return Ok(tunnel.info.clone());}
        let permit=self.capacity.clone().try_acquire_owned().map_err(|_|Error::unavailable("eight SSH forwards are already active"))?;
        let tunnels=self.tunnels.clone();let mut shutdown=self.shutdown.clone();
        let (mut reply,answer)=tokio::sync::oneshot::channel();
        tokio::spawn(async move{
            let mut registered=None;
            let result:Result<Value>=async{
                let listener=TcpListener::bind((std::net::Ipv4Addr::LOCALHOST,0)).await?;let port=listener.local_addr()?.port();drop(listener);
                let mut child=Command::new("ssh").args(arguments(&destination,port)).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped()).kill_on_drop(true).spawn()?;
                let mut stderr=child.stderr.take().unwrap();let diagnostic=Arc::new(Mutex::new(Vec::new()));let captured=diagnostic.clone();
                let reader=tokio::spawn(async move{let mut bytes=[0;1024];loop{match stderr.read(&mut bytes).await{Ok(0)|Err(_)=>break,Ok(count)=>{let mut output=captured.lock().await;let keep=count.min(8192-output.len());output.extend_from_slice(&bytes[..keep]);}}}});
                let info=json!({"host":destination.host,"port":destination.port,"local_port":port,"url":format!("http://127.0.0.1:{port}"),"pid":child.id()});
                let mut tunnel=Tunnel{child:Some(child),info:info.clone(),diagnostics:reader,_permit:Some(permit)};
                let ready=async{
                    loop{if tunnel.child.as_mut().unwrap().try_wait()?.is_some(){return Err(Error::backend("SSH exited before the forward became ready"));}if forwarded_http_ready(port).await{return Ok(());}tokio::time::sleep(Duration::from_millis(100)).await;}
                };
                let result=tokio::select!{
                    result=tokio::time::timeout(Duration::from_secs(9),ready)=>result.unwrap_or_else(|_|Err(Error::backend("SSH did not become ready within nine seconds"))),
                    _=reply.closed()=>Err(Error::unavailable("SSH request cancelled")),
                    _=async{let _=shutdown.wait_for(|value|*value).await;}=>Err(Error::unavailable("server stopping")),
                };
                if let Err(error)=result{if let Some(mut child)=tunnel.child.take(){let _=child.start_kill();let _=tokio::time::timeout(Duration::from_secs(2),child.wait()).await;}let _=tokio::time::timeout(Duration::from_millis(500),&mut tunnel.diagnostics).await;let details=String::from_utf8_lossy(&diagnostic.lock().await).to_string();return Err(Error::backend(format!("{error}: {details}. Check SSH keys, known_hosts, and the remote loopback listener; host-key checks remain strict.")));}
                let mut catalog=tunnels.lock().await;
                if *shutdown.borrow(){return Err(Error::unavailable("server stopped while SSH was connecting"));}
                if let Some(existing)=catalog.get(&key){return Ok(existing.info.clone());}
                registered=info["pid"].as_u64();catalog.insert(key.clone(),tunnel);Ok(info)
            }.await;
            if reply.send(result).is_err(){if let Some(pid)=registered{let mut catalog=tunnels.lock().await;if catalog.get(&key).is_some_and(|tunnel|tunnel.info["pid"]==pid){catalog.remove(&key);}}}
        });
        answer.await.map_err(|_|Error::unavailable("SSH supervisor ended"))?
    }
    pub async fn stop(&self){self.tunnels.lock().await.clear();let _=tokio::time::timeout(Duration::from_secs(3),self.capacity.acquire_many(8)).await;}
}

async fn forwarded_http_ready(port:u16)->bool{
    tokio::time::timeout(Duration::from_secs(1),async{
        let mut stream=TcpStream::connect((std::net::Ipv4Addr::LOCALHOST,port)).await?;
        stream.write_all(format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n").as_bytes()).await?;
        let mut status=Vec::new();while status.len()<128{let byte=stream.read_u8().await?;if byte==b'\n'{break;}status.push(byte);}
        Ok::<bool,std::io::Error>(status.starts_with(b"HTTP/1.")&&status.split(|byte|*byte==b' ').nth(1)==Some(b"200".as_slice()))
    }).await.is_ok_and(|result|result.unwrap_or(false))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rejects_argument_injection_and_ports() {
        for host in ["-oProxyCommand=evil", "user@host;evil", "host name", "[::1]", "@host", "user@", "a@b@c", "host\n"] {
            assert!(validate(&json!({"action":"connect", "host":host})).is_err());
        }
        for port in [json!(0), json!(65536), json!(-1), json!(1.5), json!("8004"), Value::Null, json!(true)] {
            assert!(validate(&json!({"action":"connect", "host":"test-host", "port":port})).is_err());
        }
        let destination = validate(&json!({"action":"connect", "host":"user@test-host", "port":8004})).unwrap();
        assert_eq!(validate(&json!({"action":"connect","host":"test-host"})).unwrap().port,8000);
        for port in [1,65535]{
            assert_eq!(validate(&json!({"action":"connect","host":"test-host","port":port})).unwrap().port,port as u16);
        }
        let arguments = arguments(&destination, 12345);
        assert!(arguments.iter().any(|argument| argument == "StrictHostKeyChecking=yes"));
        for argument in ["ControlMaster=no","ControlPath=none","ForkAfterAuthentication=no"]{
            assert!(arguments.iter().any(|value|value==argument));
        }
        assert_eq!(&arguments[arguments.len()-2..], &["--", "user@test-host"]);
    }
}
