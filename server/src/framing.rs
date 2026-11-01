use tokio::io::{AsyncRead, AsyncReadExt};
use serde_json::Value;
use crate::error::{Error, Result};

pub const JSON_LIMIT: usize = 8 * 1024 * 1024;

pub async fn json_line(reader: &mut (impl AsyncRead + Unpin), limit: usize) -> Result<Value> {
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 8192];
    loop {
        let length = reader.read(&mut chunk).await?;
        if length == 0 { return Err(Error::backend("response ended before its final newline")); }
        let newline = chunk[..length].iter().position(|byte| *byte == b'\n');
        let consumed = newline.map_or(length, |offset| offset + 1);
        if bytes.len().checked_add(consumed).is_none_or(|length| length > limit) {
            return Err(Error::backend("response exceeds byte limit"));
        }
        bytes.extend_from_slice(&chunk[..consumed]);
        if newline.is_some() {
            return serde_json::from_slice(&bytes).map_err(|error| Error::backend(format!("invalid JSON response: {error}")));
        }
    }
}

pub fn result(envelope: Value) -> Result<Value> {
    if let Some(error) = envelope.get("error").filter(|error| !error.is_null()) {
        return Err(Error::backend(error.get("message").and_then(Value::as_str).unwrap_or("backend returned an error")));
    }
    envelope.get("result").cloned().ok_or_else(|| Error::backend("response has no result field"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncWriteExt;
    use std::time::Duration;

    #[tokio::test]
    async fn reads_bytewise_utf8_only_after_newline() {
        let (mut reader, mut writer) = tokio::io::duplex(1);
        let task = tokio::spawn(async move {
            for byte in "{\"result\":\"猫café\"}\n".as_bytes() {
                writer.write_all(&[*byte]).await.unwrap();
                tokio::task::yield_now().await;
            }
        });
        assert_eq!(result(json_line(&mut reader, JSON_LIMIT).await.unwrap()).unwrap(), "猫café");
        task.await.unwrap();
    }

    #[tokio::test]
    async fn rejects_unterminated_oversized_and_invalid_utf8() {
        for mut input in [b"{}".as_slice(), b"{\"x\":\"\xff\"}\n", b"123456789\n"] {
            assert!(json_line(&mut input, 8).await.is_err());
        }
        let (mut reader, _writer) = tokio::io::duplex(1);
        assert!(tokio::time::timeout(Duration::from_millis(20), json_line(&mut reader, JSON_LIMIT)).await.is_err());
    }

    #[test]
    fn backend_errors_are_not_successes() {
        let error = result(serde_json::json!({"error":{"message":"pane is busy"}})).unwrap_err();
        assert_eq!(error.1, "pane is busy");
        assert!(result(serde_json::json!({})).is_err());
    }
}
