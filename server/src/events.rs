use std::{sync::Arc, time::Duration};
use axum::extract::ws::{Message, WebSocket};
use tokio::{io::{AsyncBufReadExt, AsyncWriteExt, BufReader}, net::UnixStream, sync::watch};
use serde_json::{json, Value};
use crate::{backend::Backend, error::{Error, Result}, framing};

pub async fn serve(mut browser: WebSocket, backend: Arc<Backend>, mut shutdown: watch::Receiver<bool>) {
    let result: Result<()> = async {
        let mut stream = UnixStream::connect(&backend.socket).await?;
        let snapshot = backend.snapshot().await?;
        let mut subscriptions: Vec<Value> = ["workspace.created","workspace.updated","workspace.renamed","workspace.moved","workspace.closed","tab.created","tab.closed","tab.renamed","tab.moved","pane.created","pane.closed","pane.updated","pane.moved","pane.exited","pane.agent_detected","layout.updated"].iter().map(|kind| json!({"type":kind})).collect();
        for pane in snapshot["panes"].as_array().unwrap() { subscriptions.push(json!({"type":"pane.agent_status_changed","pane_id":pane["pane_id"]})); }
        let request = json!({"id":"web-events","method":"events.subscribe","params":{"subscriptions":subscriptions}}).to_string() + "\n";
        stream.write_all(request.as_bytes()).await?;
        let mut reader = BufReader::new(stream);
        let mut line = Vec::new();
        let mut heartbeat = tokio::time::interval(Duration::from_secs(15));
        loop {
            tokio::select! {
                _ = async { let _ = shutdown.wait_for(|value| *value).await; } => break,
                _ = heartbeat.tick() => {
                    tokio::time::timeout(Duration::from_secs(5), browser.send(Message::Text(json!({"type":"heartbeat"}).to_string().into()))).await.map_err(|_| Error::backend("event browser stalled"))?.map_err(|error| Error::backend(error.to_string()))?;
                }
                message = browser.recv() => match message { None | Some(Err(_)) | Some(Ok(Message::Close(_))) => break, _ => () },
                event = next_event(&mut reader, &mut line) => {
                    let event = event?;
                    if event.get("error").is_some() { return Err(Error::backend(event["error"]["message"].as_str().unwrap_or("event subscription failed"))); }
                    tokio::time::timeout(Duration::from_secs(5), browser.send(Message::Text(json!({"type":"refresh","event":event}).to_string().into()))).await.map_err(|_| Error::backend("event browser stalled"))?.map_err(|error| Error::backend(error.to_string()))?;
                }
            }
        }
        Ok(())
    }.await;
    if let Err(error) = result { let _ = browser.send(Message::Text(json!({"error":error.1}).to_string().into())).await; }
}

async fn next_event(reader: &mut BufReader<UnixStream>, line: &mut Vec<u8>) -> Result<Value> {
    loop {
        let bytes = reader.fill_buf().await?;
        if bytes.is_empty() { return Err(Error::backend("Herdr event connection ended")); }
        let newline = bytes.iter().position(|byte| *byte == b'\n');
        let length = newline.map_or(bytes.len(), |offset| offset + 1);
        if line.len() + length > framing::JSON_LIMIT { return Err(Error::backend("event frame exceeds eight MiB")); }
        line.extend_from_slice(&bytes[..length]);
        reader.consume(length);
        if newline.is_some() {
            let value = serde_json::from_slice(line).map_err(|_| Error::backend("invalid Herdr event JSON"));
            line.clear(); return value;
        }
    }
}
