use axum::{http::StatusCode, response::{IntoResponse, Response}, Json};
use serde_json::json;

#[derive(Debug)]
pub struct Error(pub StatusCode, pub String);

pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    pub fn bad(message: impl Into<String>) -> Self { Self(StatusCode::BAD_REQUEST, message.into()) }
    pub fn backend(message: impl Into<String>) -> Self { Self(StatusCode::BAD_GATEWAY, message.into()) }
    pub fn unavailable(message: impl Into<String>) -> Self { Self(StatusCode::SERVICE_UNAVAILABLE, message.into()) }
    #[cfg(not(target_os = "linux"))]
    pub fn unsupported(message: impl Into<String>) -> Self { Self(StatusCode::NOT_IMPLEMENTED, message.into()) }
}

impl std::fmt::Display for Error {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.1)
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(error: std::io::Error) -> Self {
        Self(match error.kind() {
            std::io::ErrorKind::NotFound => StatusCode::NOT_FOUND,
            std::io::ErrorKind::PermissionDenied => StatusCode::FORBIDDEN,
            _ => StatusCode::INTERNAL_SERVER_ERROR,
        }, error.to_string())
    }
}

impl IntoResponse for Error {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error": self.1}))).into_response()
    }
}
