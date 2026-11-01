use axum::{body::{to_bytes, Body}, extract::Request, http::{header, HeaderMap, HeaderValue, Method, StatusCode, uri::Authority}, middleware::Next, response::{IntoResponse, Response}};
use crate::error::{Error, Result};

pub const CSP: &str = "default-src 'self'; script-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'";

fn single<'headers>(headers: &'headers HeaderMap, name: &str) -> Result<Option<&'headers str>> {
    let mut values = headers.get_all(name).iter();
    let first = values.next();
    if values.next().is_some() { return Err(Error::bad(format!("multiple {name} headers"))); }
    first.map(|value| value.to_str().map_err(|_| Error::bad(format!("invalid {name}")))).transpose()
}

pub fn validate(headers: &HeaderMap, method: &Method, path: &str) -> Result<()> {
    let host = single(headers, "host")?.ok_or_else(|| Error::bad("Host is required"))?;
    if host.contains('@') || host.contains(char::is_whitespace) { return Err(Error::bad("invalid Host authority")); }
    let authority: Authority = host.parse().map_err(|_| Error::bad("invalid Host authority"))?;
    if !matches!(authority.host(), "localhost" | "127.0.0.1" | "[::1]") {
        return Err(Error(StatusCode::FORBIDDEN, "only loopback Host authorities are accepted".into()));
    }
    let suffix = &host[authority.host().len()..];
    if !suffix.is_empty() && (!suffix.starts_with(':') || suffix.len() == 1
        || !suffix[1..].bytes().all(|byte| byte.is_ascii_digit())
        || suffix[1..].parse::<u16>().is_err() || authority.port_u16() == Some(0)) {
        return Err(Error::bad("invalid Host port"));
    }
    let origin = single(headers, "origin")?;
    if origin.is_some_and(|origin| origin != format!("http://{host}")) {
        return Err(Error(StatusCode::FORBIDDEN, "Origin must exactly match the loopback Host".into()));
    }
    let websocket = headers.contains_key(header::UPGRADE) || path == "/api/events"
        || path.starts_with("/terminal/") || path.starts_with("/graphics/");
    if (method == Method::POST || websocket) && origin.is_none() {
        return Err(Error(StatusCode::FORBIDDEN, "matching Origin is required".into()));
    }
    if method == Method::POST && path != "/api/clipboard-image" {
        let content_type = single(headers, "content-type")?.unwrap_or("");
        if !content_type.split(';').next().unwrap_or("").trim().eq_ignore_ascii_case("application/json") {
            return Err(Error(StatusCode::UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json".into()));
        }
    }
    Ok(())
}

pub async fn guard(request: Request, next: Next) -> Response {
    let response = match validate(request.headers(), request.method(), request.uri().path()) {
        Err(error) => error.into_response(),
        Ok(()) => {
            let limit = if request.method() == Method::POST && request.uri().path() == "/api/clipboard-image" { 10 * 1024 * 1024 } else { 16 * 1024 };
            let (parts, body) = request.into_parts();
            match tokio::time::timeout(std::time::Duration::from_secs(10), to_bytes(body, limit)).await {
                Ok(Ok(bytes)) => next.run(Request::from_parts(parts, Body::from(bytes))).await,
                Ok(Err(_)) => Error(StatusCode::PAYLOAD_TOO_LARGE, format!("body exceeds {limit} bytes")).into_response(),
                Err(_) => Error(StatusCode::REQUEST_TIMEOUT, "request body timed out".into()).into_response(),
            }
        }
    };
    let mut response = response;
    for (name, value) in [
        ("cache-control", "no-store"),
        ("x-content-type-options", "nosniff"),
        ("content-security-policy", CSP),
    ] { response.headers_mut().insert(name, HeaderValue::from_static(value)); }
    response
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn authority_and_origin_are_exact() {
        for host in ["localhost", "localhost:8004", "127.0.0.1:80", "[::1]:1234"] {
            let mut headers = HeaderMap::new();
            headers.insert("host", host.parse().unwrap());
            headers.insert("origin", format!("http://{host}").parse().unwrap());
            headers.insert("content-type", "application/json".parse().unwrap());
            assert!(validate(&headers, &Method::POST, "/api/command").is_ok());
        }
        for host in ["evil.localhost", "127.0.0.1.evil", "user@localhost", "localhost:", "localhost:0", "localhost:65536", "localhost:evil", "localhost.", "[::1]@evil"] {
            let mut headers = HeaderMap::new();
            headers.insert("host", host.parse().unwrap());
            assert!(validate(&headers, &Method::GET, "/").is_err(), "accepted {host}");
        }
    }

    #[test]
    fn mutations_and_upgrades_require_origin() {
        let mut headers = HeaderMap::new();
        headers.insert("host", "localhost:8004".parse().unwrap());
        assert!(validate(&headers, &Method::GET, "/").is_ok());
        assert!(validate(&headers, &Method::POST, "/api/files").is_err());
        assert!(validate(&headers, &Method::GET, "/terminal/pane").is_err());
        headers.insert("origin", "http://localhost:8005".parse().unwrap());
        assert!(validate(&headers, &Method::GET, "/").is_err());
        headers.insert("origin", "http://localhost:8004".parse().unwrap());
        assert!(validate(&headers, &Method::POST, "/api/files").is_err());
        headers.insert("content-type", "text/plain".parse().unwrap());
        assert!(validate(&headers, &Method::POST, "/api/files").is_err());
    }
}
