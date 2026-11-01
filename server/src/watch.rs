use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::sync::{Semaphore,OwnedSemaphorePermit};
use serde_json::{json, Value};
use crate::{error::{Error, Result}, validation};

pub struct Watches { capacity: Arc<Semaphore> }

impl Watches {
    pub fn new(capacity:Arc<Semaphore>) -> Self { Self { capacity } }

    pub async fn poll(&self, request: Value) -> Result<Value> {
        validation::object(&request, &["path"])?;
        let path = crate::extras::absolute_path(&request["path"])?;
        let permit = self.capacity.clone().try_acquire_owned().map_err(|_| Error::unavailable("watch capacity reached"))?;
        poll(path,permit).await
    }
}

#[cfg(target_os = "linux")]
async fn poll(path: PathBuf,permit:OwnedSemaphorePermit) -> Result<Value> {
    use std::{fs, os::{fd::FromRawFd, unix::ffi::OsStrExt},sync::atomic::{AtomicBool,Ordering}};
    use tokio::io::unix::AsyncFd;
    struct Cancellation(Arc<AtomicBool>);impl Drop for Cancellation{fn drop(&mut self){self.0.store(true,Ordering::Release);}}
    let cancelled=Arc::new(AtomicBool::new(false));let _cancellation=Cancellation(cancelled.clone());
    let initialized = tokio::task::spawn_blocking({
        let path = path.clone();
        move || -> Result<(fs::File,OwnedSemaphorePermit)> {
            let descriptor = unsafe { libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC) };
            if descriptor < 0 { return Err(std::io::Error::last_os_error().into()); }
            let file = unsafe { fs::File::from_raw_fd(descriptor) };
            let mut folders = vec![path];
            let mut count = 0;
            while let Some(folder) = folders.pop() {
                if cancelled.load(Ordering::Acquire){return Err(Error::unavailable("folder watch cancelled"));}
                if !fs::symlink_metadata(&folder)?.is_dir() { return Err(Error::bad("watch path must be a directory, not a symlink")); }
                count += 1;
                if count > 4096 { return Err(Error::bad("recursive watch exceeds 4,096 folders")); }
                let name = std::ffi::CString::new(folder.as_os_str().as_bytes()).map_err(|_| Error::bad("NUL in folder path"))?;
                let flags = libc::IN_CREATE | libc::IN_DELETE | libc::IN_MODIFY | libc::IN_ATTRIB | libc::IN_MOVED_FROM | libc::IN_MOVED_TO | libc::IN_DELETE_SELF | libc::IN_MOVE_SELF | libc::IN_ONLYDIR | libc::IN_DONT_FOLLOW;
                if unsafe { libc::inotify_add_watch(descriptor, name.as_ptr(), flags) } < 0 { return Err(std::io::Error::last_os_error().into()); }
                for entry in fs::read_dir(folder)? {
                    let entry = entry?;
                    if !entry.file_type()?.is_dir() { continue; }
                    let name = entry.file_name();
                    if matches!(name.to_str(), Some("node_modules" | "target" | ".cache")) { continue; }
                    if name == "objects" && entry.path().parent().is_some_and(|parent| parent.file_name().is_some_and(|name| name == ".git")) { continue; }
                    if folders.len() + count >= 4096 { return Err(Error::bad("recursive watch exceeds 4,096 folders")); }
                    folders.push(entry.path());
                }
            }
            Ok((file,permit))
        }
    }).await.map_err(|_| Error::unavailable("watch setup worker failed"))?;
    let (file,_permit) = match initialized {
        Err(error) if error.0 == axum::http::StatusCode::NOT_FOUND => return Ok(json!({"changed":true,"missing":true})),
        result => result?,
    };
    let descriptor = AsyncFd::new(file)?;
    let changed = match tokio::time::timeout(Duration::from_secs(17), descriptor.readable()).await {
        Ok(result) => { drop(result?); true },
        Err(_) => false,
    };
    if !path.try_exists()? { return Ok(json!({"changed":true,"missing":true})); }
    Ok(json!({"changed":changed}))
}

#[cfg(not(target_os = "linux"))]
async fn poll(_path: PathBuf,_permit:OwnedSemaphorePermit) -> Result<Value> {
    Err(Error::unsupported("recursive folder events require Linux inotify"))
}
