use std::{fs::File, os::fd::{FromRawFd, RawFd}};
use crate::error::Result;

pub const ATTACHMENT_LIMIT: usize = 32;
pub const INPUT_QUEUE: usize = 8;
pub const OUTPUT_QUEUE: usize = 32;
pub const INPUT_LIMIT: usize = 65_536;
pub const MESSAGE_LIMIT: usize = 1024 * 1024;

use std::{io::{Read, Write}, sync::{Arc, atomic::{AtomicBool, Ordering}, mpsc as blocking}, time::{Duration, Instant}};
use axum::extract::ws::{WebSocket, Message, CloseFrame};
use tokio::sync::{mpsc, OwnedSemaphorePermit, watch};
use portable_pty::{CommandBuilder, PtySize, native_pty_system};
use crate::{backend::Backend, error::Error, terminal_probe::{AttachmentReadiness, CapabilityFilter, REPLY}};

struct Attachment {
    child: Box<dyn portable_pty::Child + Send + Sync>,
    stopped: Arc<AtomicBool>,
    _permit: OwnedSemaphorePermit,
}

impl Drop for Attachment {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::Release);
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

enum Output { Ready, Bytes(Vec<u8>), Error(String) }

fn enqueue<T>(sender: &blocking::SyncSender<T>, mut value: T, stopped: &AtomicBool) -> std::result::Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        match sender.try_send(value) {
            Ok(()) => return Ok(()),
            Err(blocking::TrySendError::Disconnected(_)) => return Err("terminal writer closed".into()),
            Err(blocking::TrySendError::Full(pending)) => value = pending,
        }
        if stopped.load(Ordering::Acquire) || Instant::now() >= deadline { return Err("terminal input stalled for five seconds".into()); }
        std::thread::sleep(Duration::from_millis(2));
    }
}

fn emit(sender: &mpsc::Sender<Output>, mut value: Output, stopped: &AtomicBool) -> bool {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        match sender.try_send(value) {
            Ok(()) => return true,
            Err(mpsc::error::TrySendError::Closed(_)) => return false,
            Err(mpsc::error::TrySendError::Full(pending)) => value = pending,
        }
        if stopped.load(Ordering::Acquire) || Instant::now() >= deadline { stopped.store(true, Ordering::Release); return false; }
        std::thread::sleep(Duration::from_millis(2));
    }
}

pub async fn attach(mut socket: WebSocket, backend: Arc<Backend>, pane: String, permit: OwnedSemaphorePermit, mut shutdown: watch::Receiver<bool>) {
    let result = async {
        let pane = backend.pane(&pane).await?;
        let terminal = pane["terminal_id"].as_str().ok_or_else(|| Error::backend("pane has no terminal ID"))?.to_owned();
        let setup = tokio::task::spawn_blocking(move || -> Result<_> {
            let pair = native_pty_system().openpty(PtySize { rows:24, cols:80, pixel_width:0, pixel_height:0 }).map_err(|error| Error::backend(error.to_string()))?;
            let mut command = CommandBuilder::new(&backend.binary);
            command.args(["terminal", "attach", &terminal]);
            for (key, _) in std::env::vars_os() { if key.to_string_lossy().starts_with("HERDR_") { command.env_remove(key); } }
            if let Some(config) = &backend.config { command.env("HERDR_CONFIG_PATH", config); }
            command.env("HERDR_SOCKET_PATH", &backend.socket);
            command.env("TERM", "xterm-256color");
            command.env("COLORTERM", "truecolor");
            command.cwd(&backend.home);
            let descriptor = pair.master.as_raw_fd().ok_or_else(|| Error::backend("PTY master has no Unix descriptor"))?;
            let reader = duplicate_writer(descriptor)?;
            let writer = duplicate_writer(descriptor)?;
            let flags = unsafe { libc::fcntl(descriptor, libc::F_GETFL) };
            if flags < 0 || unsafe { libc::fcntl(descriptor, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 { return Err(std::io::Error::last_os_error().into()); }
            let child = pair.slave.spawn_command(command).map_err(|error| Error::backend(error.to_string()))?;
            drop(pair.slave);
            let stopped = Arc::new(AtomicBool::new(false));
            Ok((Attachment { child, stopped, _permit:permit }, pair.master, reader, writer))
        }).await.map_err(|_| Error::backend("attachment setup failed"))??;
        let (owner, master, mut reader, mut writer) = setup;
        let stopped = owner.stopped.clone();
        let (input, receiver) = blocking::sync_channel(INPUT_QUEUE);
        let (output, mut received) = mpsc::channel(OUTPUT_QUEUE);
        let writer_stop = stopped.clone();
        let writer_output = output.clone();
        let writer_task = tokio::task::spawn_blocking(move || {
            while !writer_stop.load(Ordering::Acquire) {
                let value = match receiver.recv_timeout(Duration::from_millis(100)) { Ok(value) => value, Err(blocking::RecvTimeoutError::Timeout) => continue, Err(_) => break };
                let result = match value {
                    Input::Resize { cols, rows } => master.resize(PtySize { cols, rows, pixel_width:0, pixel_height:0 }).map_err(|error| error.to_string()),
                    Input::Text(text) => {
                        let mut bytes = text.as_bytes();
                        let deadline = Instant::now() + Duration::from_secs(5);
                        let mut failure = None;
                        while !bytes.is_empty() && !writer_stop.load(Ordering::Acquire) {
                            match writer.write(bytes) {
                                Ok(0) => { failure = Some("terminal writer ended".into()); break; }
                                Ok(length) => bytes = &bytes[length..],
                                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                                    if Instant::now() >= deadline { failure = Some("PTY writer stalled for five seconds".into()); break; }
                                    std::thread::sleep(Duration::from_millis(2));
                                }
                                Err(error) => { failure = Some(error.to_string()); break; }
                            }
                        }
                        failure.map_or(Ok(()), Err)
                    }
                };
                if let Err(error) = result { emit(&writer_output, Output::Error(error), &writer_stop); break; }
            }
        });
        let reader_stop = stopped.clone();
        let probe_writer = input.clone();
        let reader_task = tokio::task::spawn_blocking(move || {
            let mut filter = CapabilityFilter::default();
            let mut readiness = AttachmentReadiness::default();
            let mut buffer = [0; 16384];
            while !reader_stop.load(Ordering::Acquire) {
                match reader.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(length) => {
                        let (bytes, answer) = filter.push(&buffer[..length]);
                        if answer {
                            if enqueue(&probe_writer, Input::Text(String::from_utf8(REPLY.to_vec()).unwrap()), &reader_stop).is_err() { break; }
                        }
                        let ready = readiness.push(&bytes);
                        if !bytes.is_empty() && !emit(&output, Output::Bytes(bytes), &reader_stop) { break; }
                        if ready && !emit(&output, Output::Ready, &reader_stop) { break; }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(2)),
                    Err(_) => break,
                }
            }
            let pending = filter.finish();
            if !pending.is_empty() { emit(&output, Output::Bytes(pending), &reader_stop); }
            emit(&output, Output::Error("Attachment ended; the terminal may be busy in another client. Retry without takeover.".into()), &reader_stop);
        });
        let mut stall_check = tokio::time::interval(Duration::from_millis(250));
        let ending = loop {
            tokio::select! {
                _ = async { let _ = shutdown.wait_for(|value| *value).await; } => break "Web server is stopping".to_owned(),
                _ = stall_check.tick() => if stopped.load(Ordering::Acquire) { break "Terminal output stalled for five seconds".to_owned(); },
                output = received.recv() => {
                    let message = match output {
                        Some(Output::Ready) => Message::Text("{\"ready\":true}".into()),
                        Some(Output::Bytes(bytes)) => Message::Binary(bytes.into()),
                        Some(Output::Error(error)) => break error,
                        None => break "Terminal output ended".to_owned(),
                    };
                    match tokio::time::timeout(Duration::from_secs(5), socket.send(message)).await {
                        Ok(Ok(())) => (), _ => break "Browser output stalled or closed".to_owned(),
                    }
                }
                message = socket.recv() => {
                    match message {
                        Some(Ok(Message::Text(text))) => {
                            let value = serde_json::from_str(&text).map_err(|_| Error::bad("invalid terminal JSON"))?;
                            let value = validate_input(&value)?;
                            let sender = input.clone(); let cancelled = stopped.clone();
                            tokio::task::spawn_blocking(move || enqueue(&sender, value, &cancelled)).await.map_err(|_| Error::backend("terminal input task ended"))?.map_err(Error::backend)?;
                        }
                        Some(Ok(Message::Ping(bytes))) => { let _ = tokio::time::timeout(Duration::from_secs(5), socket.send(Message::Pong(bytes))).await; }
                        Some(Ok(Message::Pong(_))) => (),
                        _ => break "Browser disconnected".to_owned(),
                    }
                }
            }
        };
        stopped.store(true, Ordering::Release);
        drop(input);
        tokio::task::spawn_blocking(move || drop(owner)).await.ok();
        let _ = tokio::time::timeout(Duration::from_secs(2), async { let _ = reader_task.await; let _ = writer_task.await; }).await;
        Err::<(), Error>(Error::backend(ending))
    }.await;
    if let Err(error) = result {
        let _ = tokio::time::timeout(Duration::from_secs(1), socket.send(Message::Text(serde_json::json!({"error":error.1}).to_string().into()))).await;
        let reason: String = error.1.chars().take(100).collect();
        let _ = tokio::time::timeout(Duration::from_secs(1), socket.send(Message::Close(Some(CloseFrame { code:1011, reason:reason.into() })))).await;
    }
}

pub fn duplicate_writer(master: RawFd) -> Result<File> {
    let descriptor = unsafe { libc::fcntl(master, libc::F_DUPFD_CLOEXEC, 0) };
    if descriptor < 0 { return Err(std::io::Error::last_os_error().into()); }
    Ok(unsafe { File::from_raw_fd(descriptor) })
}

pub enum Input {
    Text(String),
    Resize { cols: u16, rows: u16 },
}

pub fn validate_input(value: &serde_json::Value) -> Result<Input> {
    use crate::{error::Error, validation};
    match value["type"].as_str() {
        Some("input") => {
            validation::object(value, &["type", "data"])?;
            let text = value["data"].as_str().ok_or_else(|| Error::bad("input data must be a string"))?;
            if text.len() > INPUT_LIMIT { return Err(Error::bad("input exceeds 65,536 bytes")); }
            Ok(Input::Text(text.to_owned()))
        }
        Some("resize") => {
            validation::object(value, &["type", "cols", "rows"])?;
            Ok(Input::Resize {
                cols: validation::integer(&value["cols"], "cols", 1, 500)? as u16,
                rows: validation::integer(&value["rows"], "rows", 1, 200)? as u16,
            })
        }
        _ => Err(Error::bad("unknown terminal message type")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::{Read, Write}, os::{fd::AsRawFd, unix::net::UnixStream}, time::Duration};
    use serde_json::json;

    #[test]
    fn dropping_duplicate_does_not_write_or_close_original_descriptor() {
        let (mut master, mut peer) = UnixStream::pair().unwrap();
        peer.set_read_timeout(Some(Duration::from_millis(30))).unwrap();
        let mut writer = duplicate_writer(master.as_raw_fd()).unwrap();
        writer.write_all(b"unsubmitted").unwrap();
        drop(writer);
        let mut pending = [0; 11];
        peer.read_exact(&mut pending).unwrap();
        assert_eq!(&pending, b"unsubmitted");
        assert!(peer.read(&mut [0; 1]).is_err());
        master.write_all(b"still open").unwrap();
        let mut after = [0; 10];
        peer.read_exact(&mut after).unwrap();
        assert_eq!(&after, b"still open");
    }

    #[test]
    fn terminal_input_is_not_reply_filtered() {
        let text = "\u{1b}[?1016;0$y\u{1b}[?1;2c\u{0}";
        let Input::Text(actual) = validate_input(&json!({"type":"input", "data":text})).unwrap() else { panic!() };
        assert_eq!(actual, text);
        assert!(validate_input(&json!({"type":"resize", "cols":501, "rows":24})).is_err());
        assert!(validate_input(&json!({"type":"resize", "cols":80, "rows":0})).is_err());
    }
}
