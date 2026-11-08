use std::{collections::HashMap, fs::File, io::{Read, Write}, os::fd::{FromRawFd, RawFd},
    sync::{Arc, atomic::{AtomicBool, AtomicUsize, Ordering}, mpsc as blocking}, time::{Duration, Instant}};
use axum::{body::Bytes, extract::ws::{WebSocket, Message, CloseFrame}};
use tokio::sync::{mpsc, watch, Mutex, OwnedSemaphorePermit, Semaphore};
use portable_pty::{CommandBuilder, PtySize, native_pty_system};
use crate::{backend::Backend, error::{Error, Result}, terminal_probe::{AttachmentReadiness, CapabilityFilter, REPLY}, terminal_sync::Screen};

pub const ATTACHMENT_LIMIT: usize = 32;
pub const VIEWER_LIMIT: usize = 128;
pub const INPUT_QUEUE: usize = 8;
pub const OUTPUT_QUEUE: usize = 32;
pub const OUTPUT_BUDGET: usize = 16 * 1024 * 1024;
pub const INPUT_LIMIT: usize = 65_536;
pub const MESSAGE_LIMIT: usize = 1024 * 1024;

struct Attachment {
    child: Box<dyn portable_pty::Child + Send + Sync>,
    stopped: Arc<AtomicBool>,
}
impl Drop for Attachment {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::Release);
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

enum Output { Ready, Bytes(Bytes), Size(u16, u16), Error(String) }

pub struct Attachments {
    entries: Mutex<HashMap<String, Arc<Shared>>>,
    slots: Arc<Semaphore>,
    viewers: Arc<Semaphore>,
}
struct Shared {
    input: mpsc::Sender<Command>,
    closing: AtomicBool,
    done: watch::Receiver<bool>,
}
enum Command { Join(Viewer), Input(Arc<AtomicBool>, Input) }
struct Viewer {
    alive: Arc<AtomicBool>,
    output: mpsc::Sender<Output>,
    ended: watch::Sender<Option<String>>,
    size: Option<(u16, u16)>,
    ready: bool,
    joined: Instant,
    queued: Arc<AtomicUsize>,
}
impl Viewer {
    fn end(&self, message: &str) {
        self.alive.store(false, Ordering::Release);
        self.ended.send_replace(Some(message.to_owned()));
    }
    fn send(&self, output: Output) -> bool {
        let bytes = match &output { Output::Bytes(bytes) => bytes.len(), _ => 0 };
        if self.queued.try_update(Ordering::AcqRel, Ordering::Acquire, |size| size.checked_add(bytes).filter(|size| *size <= OUTPUT_BUDGET)).is_err() {
            self.end("This browser cannot keep up with terminal output. Click Retry to reconnect.");
            return false;
        }
        if self.output.try_send(output).is_ok() { true } else {
            self.queued.fetch_sub(bytes, Ordering::AcqRel);
            self.end("This browser cannot keep up with terminal output. Click Retry to reconnect.");
            false
        }
    }
}
pub struct Subscription {
    shared: Arc<Shared>,
    alive: Arc<AtomicBool>,
    output: mpsc::Receiver<Output>,
    ended: watch::Receiver<Option<String>>,
    queued: Arc<AtomicUsize>,
    _permit: OwnedSemaphorePermit,
}
impl Drop for Subscription {
    fn drop(&mut self) { self.alive.store(false, Ordering::Release); }
}
impl Attachments {
    pub fn new() -> Arc<Self> {
        Arc::new(Self { entries: Mutex::new(HashMap::new()), slots: Arc::new(Semaphore::new(ATTACHMENT_LIMIT)), viewers: Arc::new(Semaphore::new(VIEWER_LIMIT)) })
    }
    pub async fn subscribe(self: &Arc<Self>, backend: Arc<Backend>, terminal: String, shutdown: watch::Receiver<bool>) -> Result<Subscription> {
        let permit = self.viewers.clone().try_acquire_owned().map_err(|_| Error::unavailable("128 terminal browser connections are already active"))?;
        loop {
            let mut entries = self.entries.lock().await;
            let shared = if let Some(shared) = entries.get(&terminal) {
                if shared.closing.load(Ordering::Acquire) {
                    let mut done = shared.done.clone();
                    drop(entries);
                    tokio::time::timeout(Duration::from_secs(5), done.wait_for(|done| *done)).await
                        .map_err(|_| Error::unavailable("Previous terminal attachment is still stopping"))?
                        .map_err(|_| Error::unavailable("Terminal attachment stopped unexpectedly"))?;
                    continue;
                }
                shared.clone()
            } else {
                let slot = self.slots.clone().try_acquire_owned().map_err(|_| Error::unavailable("32 terminal attachments are already active"))?;
                let (input, commands) = mpsc::channel(32);
                let (done, completed) = watch::channel(false);
                let shared = Arc::new(Shared { input, closing: AtomicBool::new(false), done: completed });
                entries.insert(terminal.clone(), shared.clone());
                tokio::spawn(run_shared(self.clone(), shared.clone(), backend.clone(), terminal.clone(), commands, done, slot, shutdown.clone()));
                shared
            };
            let (output, received) = mpsc::channel(OUTPUT_QUEUE);
            let (ended, ending) = watch::channel(None);
            let alive = Arc::new(AtomicBool::new(true));
            let queued = Arc::new(AtomicUsize::new(0));
            let viewer = Viewer { alive: alive.clone(), output, ended, size: None, ready: false, joined: Instant::now(), queued: queued.clone() };
            shared.input.try_send(Command::Join(viewer)).map_err(|_| Error::unavailable("Terminal connection queue is busy"))?;
            return Ok(Subscription { shared, alive, output: received, ended: ending, queued, _permit: permit });
        }
    }
    pub async fn drain(&self) {
        let _ = tokio::time::timeout(Duration::from_secs(5), self.slots.acquire_many(ATTACHMENT_LIMIT as u32)).await;
    }
}

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

struct Pty {
    owner: Attachment,
    input: blocking::SyncSender<Input>,
    output: mpsc::Receiver<Output>,
    reader: tokio::task::JoinHandle<()>,
    writer: tokio::task::JoinHandle<()>,
}
async fn start_pty(backend: Arc<Backend>, terminal: String) -> Result<Pty> {
    let (owner, master, mut reader, mut writer) = tokio::task::spawn_blocking(move || -> Result<_> {
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
            Ok((Attachment { child, stopped }, pair.master, reader, writer))

    }).await.map_err(|_| Error::backend("attachment setup failed"))??;
    let pid = owner.child.process_id().ok_or_else(|| Error::backend("attachment has no process ID"))?;

        let stopped = owner.stopped.clone();
        let (input, receiver) = blocking::sync_channel(INPUT_QUEUE);
        let (output, received) = mpsc::channel(OUTPUT_QUEUE);
        let writer_stop = stopped.clone();
        let writer_output = output.clone();
        let writer_task = tokio::task::spawn_blocking(move || {
            while !writer_stop.load(Ordering::Acquire) {
                let value = match receiver.recv_timeout(Duration::from_millis(100)) { Ok(value) => value, Err(blocking::RecvTimeoutError::Timeout) => continue, Err(_) => break };
                let result = match value {
                    Input::Resize { cols, rows } => master.resize(PtySize { cols, rows, pixel_width:0, pixel_height:0 }).map_err(|error| error.to_string()),
                    Input::Redraw => {
                        if unsafe { libc::kill(pid as i32, libc::SIGWINCH) } == 0 { Ok(()) }
                        else { Err(std::io::Error::last_os_error().to_string()) }
                    }
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
                        if !bytes.is_empty() && !emit(&output, Output::Bytes(bytes.into()), &reader_stop) { break; }
                        if ready && !emit(&output, Output::Ready, &reader_stop) { break; }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(2)),
                    Err(_) => break,
                }
            }
            let pending = filter.finish();
            if !pending.is_empty() { emit(&output, Output::Bytes(pending.into()), &reader_stop); }
            emit(&output, Output::Error("Attachment ended; the terminal may be busy in another client. Retry without takeover.".into()), &reader_stop);
        });

    Ok(Pty { owner, input, output: received, reader: reader_task, writer: writer_task })
}

// Each terminal has one actor and one CLI child. Browser queues never block the
// actor or a peer. Keep the registry entry until the old child has been reaped.
async fn run_shared(registry: Arc<Attachments>, shared: Arc<Shared>, backend: Arc<Backend>, terminal: String,
    mut commands: mpsc::Receiver<Command>, done: watch::Sender<bool>, _slot: OwnedSemaphorePermit, mut shutdown: watch::Receiver<bool>) {
    let mut viewers = Vec::<Viewer>::new();
    let result = start_pty(backend, terminal.clone()).await;
    let ending = match result {
        Err(error) => error.1,
        Ok(mut pty) => {
            let mut size = (80, 24);
            let mut screen = Screen::new();
            let mut redraw = false;
            let mut tick = tokio::time::interval(Duration::from_millis(100));
            let ending = loop {
                tokio::select! {
                    _ = async { let _ = shutdown.wait_for(|value| *value).await; } => break "Web server is stopping".to_owned(),
                    command = commands.recv() => match command {
                        Some(Command::Join(mut viewer)) => {
                            viewer.send(Output::Size(size.0, size.1));
                            if screen.ready && viewer.send(Output::Bytes(screen.snapshot().into())) { viewer.ready = viewer.send(Output::Ready); }
                            viewers.push(viewer);
                            // Herdr repaints on SIGWINCH even when its size is
                            // unchanged. Restore renderer details, such as OSC 8
                            // targets, that the bounded text snapshot omits.
                            redraw = true;
                        }
                        Some(Command::Input(alive, input)) => {
                            if let Some(viewer) = viewers.iter_mut().find(|viewer| Arc::ptr_eq(&viewer.alive, &alive) && viewer.alive.load(Ordering::Acquire)) {
                                match input {
                                    Input::Resize { cols, rows } => viewer.size = Some((cols, rows)),
                                    Input::Text(_) if viewer.ready => {
                                        if pty.input.try_send(input).is_err() { viewer.end("Terminal input queue is busy. Click Retry to reconnect."); }
                                    }
                                    _ => (),
                                }
                            }
                        }
                        None => break "Browser connections ended".to_owned(),
                    },
                    output = pty.output.recv() => match output {
                        Some(Output::Ready) => (),
                        Some(Output::Bytes(bytes)) => {
                            let bytes = match screen.push(&bytes) {
                                Ok(Some(complete)) => Bytes::from(complete),
                                Ok(None) => continue,
                                Err(error) => break error.to_owned(),
                            };
                            for viewer in &mut viewers {
                                if !viewer.alive.load(Ordering::Acquire) { continue; }
                                if viewer.ready { viewer.send(Output::Bytes(bytes.clone())); }
                                else if screen.ready && viewer.send(Output::Bytes(bytes.clone())) { viewer.ready = viewer.send(Output::Ready); }
                            }
                        }
                        Some(Output::Error(error)) => break error,
                        None => break "Terminal output ended".to_owned(),
                        _ => (),
                    },
                    _ = tick.tick() => {
                        if pty.owner.stopped.load(Ordering::Acquire) { break "Terminal output stalled for five seconds".to_owned(); }
                        for viewer in &viewers {
                            if !viewer.ready && viewer.joined.elapsed() > Duration::from_secs(10) { viewer.end("Terminal did not produce a complete screen. Click Retry to reconnect."); }
                        }
                    },
                }
                viewers.retain(|viewer| viewer.alive.load(Ordering::Acquire));
                if viewers.is_empty() {
                    // A join is enqueued under this same lock, so it cannot be
                    // stranded between the empty check and retirement.
                    let _entries = registry.entries.lock().await;
                    if commands.is_empty() {
                        shared.closing.store(true, Ordering::Release);
                        break "Browser disconnected".to_owned();
                    }
                }
                let sizes: Vec<_> = viewers.iter().filter_map(|viewer| viewer.size).collect();
                if let (Some(cols), Some(rows)) = (sizes.iter().map(|s| s.0).min(), sizes.iter().map(|s| s.1).min()) {
                    if (cols, rows) != size {
                        if pty.input.try_send(Input::Resize { cols, rows }).is_ok() {
                            size = (cols, rows);
                            screen.resize(cols, rows);
                            for viewer in &viewers { viewer.send(Output::Size(cols, rows)); }
                        }
                    }
                }
                if redraw && screen.ready && pty.input.try_send(Input::Redraw).is_ok() { redraw = false; }
            };
            shared.closing.store(true, Ordering::Release);
            for viewer in &viewers { viewer.end(&ending); }
            pty.owner.stopped.store(true, Ordering::Release);
            drop(pty.input);
            let _ = tokio::task::spawn_blocking(move || drop(pty.owner)).await;
            let _ = tokio::time::timeout(Duration::from_secs(2), async { let _ = pty.reader.await; let _ = pty.writer.await; }).await;
            ending
        }
    };
    shared.closing.store(true, Ordering::Release);
    commands.close();
    while let Some(command) = commands.recv().await {
        if let Command::Join(viewer) = command { viewer.end(&ending); }
    }
    drop(_slot);
    registry.entries.lock().await.remove(&terminal);
    done.send_replace(true);
}

pub async fn attach(mut socket: WebSocket, mut subscription: Subscription, mut shutdown: watch::Receiver<bool>) {
    let result: Result<()> = async {
        loop {
            tokio::select! {
                _ = async { let _ = shutdown.wait_for(|value| *value).await; } => return Err(Error::backend("Web server is stopping")),
                ending = subscription.ended.changed() => {
                    let message = subscription.ended.borrow().clone().unwrap_or_else(|| "Terminal attachment ended".to_owned());
                    let _ = ending;
                    return Err(Error::backend(message));
                }
                output = subscription.output.recv() => {
                    let message = match output {
                        Some(Output::Ready) => Message::Text("{\"ready\":true}".into()),
                        Some(Output::Size(cols, rows)) => Message::Text(serde_json::json!({"size":{"cols":cols,"rows":rows}}).to_string().into()),
                        Some(Output::Bytes(bytes)) => {
                            subscription.queued.fetch_sub(bytes.len(), Ordering::AcqRel);
                            Message::Binary(bytes)
                        }
                        _ => return Err(Error::backend("Terminal output ended")),
                    };
                    tokio::time::timeout(Duration::from_secs(5), socket.send(message)).await
                        .map_err(|_| Error::backend("Browser output stalled for five seconds"))?
                        .map_err(|_| Error::backend("Browser disconnected"))?;
                }
                message = socket.recv() => match message {
                    Some(Ok(Message::Text(text))) => {
                        let value = validate_input(&serde_json::from_str(&text).map_err(|_| Error::bad("invalid terminal JSON"))?)?;
                        subscription.shared.input.try_send(Command::Input(subscription.alive.clone(), value))
                            .map_err(|_| Error::unavailable("Terminal input queue is busy"))?;
                    }
                    Some(Ok(Message::Ping(bytes))) => {
                        let _ = tokio::time::timeout(Duration::from_secs(5), socket.send(Message::Pong(bytes))).await;
                    }
                    Some(Ok(Message::Pong(_))) => (),
                    _ => return Ok(()),
                }
            }
        }
    }.await;
    drop(subscription);
    if let Err(error) = result {
        let retry = error.1 == "Web server is stopping";
        let _ = tokio::time::timeout(Duration::from_secs(1), socket.send(Message::Text(serde_json::json!({"error":error.1,"retry":retry}).to_string().into()))).await;
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
    Redraw,
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
