mod boundary;
mod backend;
mod events;
mod error;
mod extras;
mod framing;
mod git;
mod graphics;
mod live;
mod machines;
mod plugin;
mod terminal_probe;
mod validation;
mod watch;

use std::{path::PathBuf, sync::Arc};
use axum::{body::Bytes, extract::{DefaultBodyLimit, Path, State, Query, WebSocketUpgrade}, http::{header, HeaderMap, Method, StatusCode, Uri}, middleware, response::{IntoResponse, Response}, routing::{get, post}, Json, Router};
use serde_json::{json, Value};
use error::{Error, Result};

include!(concat!(env!("OUT_DIR"), "/assets.rs"));

struct Services {
    backend: Option<Arc<backend::Backend>>,
    shutdown: tokio::sync::watch::Receiver<bool>,
    attachments: Arc<tokio::sync::Semaphore>,
    subscriptions: Arc<tokio::sync::Semaphore>,
    machines:machines::Machines,
    mods: extras::Mods,
    uploads: extras::Uploads,
    git: git::Jobs,
    watches: watch::Watches,
}

fn parse_json(bytes: &[u8]) -> Result<Value> {
    serde_json::from_slice(bytes).map_err(|error| Error::bad(format!("invalid JSON: {error}")))
}

fn backend_unavailable() -> Error {
    Error::unavailable("No backend in diagnostic mode. Start without --diagnostics to connect to original Herdr.")
}

fn selected(services: &Services) -> Result<Arc<backend::Backend>> { services.backend.clone().ok_or_else(backend_unavailable) }

async fn state(State(services): State<Arc<Services>>, Query(query): Query<std::collections::HashMap<String,String>>) -> Result<Json<Value>> {
    selected(&services)?.state(query.get("tab").map(String::as_str)).await.map(Json)
}

async fn terminal(State(services): State<Arc<Services>>, Path(pane): Path<String>, upgrade: WebSocketUpgrade) -> Result<Response> {
    let backend = selected(&services)?;
    backend.pane(&pane).await?;
    let permit = services.attachments.clone().try_acquire_owned().map_err(|_| Error::unavailable(format!("{} terminal attachments are already active", live::ATTACHMENT_LIMIT)))?;
    Ok(upgrade.max_message_size(live::MESSAGE_LIMIT).on_upgrade(move |socket| live::attach(socket, backend, pane, permit, services.shutdown.clone())))
}

async fn events(State(services): State<Arc<Services>>, upgrade: WebSocketUpgrade) -> Result<Response> {
    let backend = selected(&services)?;
    let permit = services.subscriptions.clone().try_acquire_owned().map_err(|_| Error::unavailable("event/watch capacity reached"))?;
    Ok(upgrade.max_message_size(16384).on_upgrade(move |socket| async move { let _permit = permit; events::serve(socket, backend, services.shutdown.clone()).await; }))
}

async fn graphics(State(services): State<Arc<Services>>, Path(pane): Path<String>, upgrade:WebSocketUpgrade)->Result<Response>{
    let backend=selected(&services)?;backend.pane(&pane).await?;
    let permit=services.subscriptions.clone().try_acquire_owned().map_err(|_|Error::unavailable("graphics capacity reached"))?;
    Ok(upgrade.max_message_size(1024).on_upgrade(move |socket|async move{let _permit=permit;graphics::serve(socket,backend,pane,services.shutdown.clone()).await;}))
}

async fn command(State(services): State<Arc<Services>>, bytes: Bytes) -> Result<Json<Value>> {
    let value = parse_json(&bytes)?;
    let (method, params) = validation::command(&value)?;
    selected(&services)?.request(method, params).await.map(Json)
}

async fn files(bytes: Bytes) -> Result<Json<Value>> {
    let value = parse_json(&bytes)?;
    tokio::task::spawn_blocking(move || extras::files(&value)).await
        .map_err(|_| Error::unavailable("file worker failed"))?.map(Json)
}

async fn upload(State(services): State<Arc<Services>>, headers: HeaderMap, bytes: Bytes) -> Result<Json<Value>> {
    let mime = headers.get(header::CONTENT_TYPE).and_then(|value| value.to_str().ok())
        .ok_or_else(|| Error::bad("image Content-Type is required"))?.to_owned();
    tokio::task::spawn_blocking(move || services.uploads.save(&mime, &bytes)).await
        .map_err(|_| Error::unavailable("image worker failed"))?
        .map(|path| Json(json!({"path":path})))
}

async fn git(State(services): State<Arc<Services>>, bytes: Bytes) -> Result<Response> {
    let result = services.git.run(parse_json(&bytes)?).await?;
    if result["code"] != 0 {
        let mut result = result;
        result["error"] = json!("Git exited unsuccessfully; see stderr");
        return Ok((StatusCode::UNPROCESSABLE_ENTITY, Json(result)).into_response());
    }
    Ok(Json(result).into_response())
}

async fn watch(State(services): State<Arc<Services>>, bytes: Bytes) -> Result<Json<Value>> {
    let value=parse_json(&bytes)?;let mut shutdown=services.shutdown.clone();
    tokio::select!{result=services.watches.poll(value)=>result.map(Json),_=async{let _=shutdown.wait_for(|value|*value).await;}=>Err(Error::unavailable("server stopping"))}
}

async fn mods(State(services): State<Arc<Services>>) -> Json<Value> { Json(json!(services.mods.names())) }

async fn mod_asset(State(services): State<Arc<Services>>, Path(path): Path<String>) -> Result<Response> {
    let (mime, bytes) = tokio::task::spawn_blocking(move || services.mods.read(&path)).await
        .map_err(|_| Error::unavailable("panel asset worker failed"))??;
    Ok(([(header::CONTENT_TYPE, mime)], bytes).into_response())
}

async fn agents() -> Json<Value> { Json(json!(extras::agent_catalog())) }

async fn agent_start(State(services): State<Arc<Services>>, bytes: Bytes) -> Result<Json<Value>> {
    let value = parse_json(&bytes)?;
    validation::object(&value, &["pane", "program"])?;
    validation::text(&value["pane"], "pane", 512, false)?;
    let program = validation::text(&value["program"], "program", 128, false)?;
    if !extras::agent_catalog().contains(&program) { return Err(Error::bad("program is not an installed supported agent")); }
    selected(&services)?.launch(value["pane"].as_str().unwrap(), vec![program.to_owned()]).await.map(Json)
}

async fn terminal_launch(State(services): State<Arc<Services>>, bytes: Bytes) -> Result<Json<Value>> {
    let value = parse_json(&bytes)?;
    validation::object(&value, &["pane", "argv"])?;
    validation::text(&value["pane"], "pane", 512, false)?;
    let argv = validation::argv(&value["argv"])?;
    selected(&services)?.launch(value["pane"].as_str().unwrap(), argv).await.map(Json)
}

async fn child_terminal(State(services): State<Arc<Services>>, bytes: Bytes) -> Result<Json<Value>> {
    let value = parse_json(&bytes)?;
    validation::object(&value, &["parent", "name", "argv"])?;
    validation::text(&value["parent"], "parent", 128, false)?;
    if let Some(name) = value.get("name").filter(|name| name.is_string()) {
        validation::text(name, "name", 128, true)?;
    }
    validation::argv(&value["argv"])?;
    selected(&services)?.child(&value).await.map(Json)
}

async fn terminal_action(State(services): State<Arc<Services>>, bytes: Bytes) -> Result<Json<Value>> {
    let value = parse_json(&bytes)?;
    validation::object(&value, &["action", "terminal", "space", "confirmed"])?;
    validation::text(&value["terminal"], "terminal", 512, false)?;
    if let Some(space) = value.get("space") { validation::text(space, "space", 512, false)?; }
    if let Some(confirmed) = value.get("confirmed") {
        if !confirmed.is_boolean() { return Err(Error::bad("confirmed must be Boolean")); }
    }
    match value["action"].as_str() {
        Some("kill") if value["confirmed"] != true => return Err(Error::bad("kill requires confirmed:true")),
        Some("park" | "keep" | "kill") => (),
        _ => return Err(Error::bad("action must be park, keep, or kill")),
    }
    selected(&services)?.action(&value).await.map(Json)
}

async fn machines_post(State(services):State<Arc<Services>>,bytes: Bytes) -> Result<Json<Value>> {
    services.machines.action(&parse_json(&bytes)?).await.map(Json)
}
async fn machines_get(State(services):State<Arc<Services>>)->Json<Value>{Json(services.machines.catalog().await)}

async fn static_asset(method: Method, uri: Uri) -> Response {
    if method != Method::GET { return Error(StatusCode::METHOD_NOT_ALLOWED, "method not allowed".into()).into_response(); }
    let path = if uri.path() == "/" { "/index.html" } else { uri.path() };
    match ASSETS.binary_search_by_key(&path, |(name, _, _)| *name) {
        Ok(index) => {
            let (_, mime, bytes) = ASSETS[index];
            ([(header::CONTENT_TYPE, mime)], bytes).into_response()
        }
        Err(_) => Error(StatusCode::NOT_FOUND, "asset not found".into()).into_response(),
    }
}

fn router(services: Arc<Services>) -> Router {
    Router::new()
        .route("/api/state", get(state))
        .route("/api/command", post(command))
        .route("/terminal/{pane}", get(terminal))
        .route("/graphics/{pane}", get(graphics))
        .route("/api/events", get(events))
        .route("/api/files", post(files))
        .route("/api/watch", post(watch))
        .route("/api/git", post(git))
        .route("/api/clipboard-image", post(upload))
        .route("/api/agents", get(agents))
        .route("/api/agent-start", post(agent_start))
        .route("/api/terminal-launch", post(terminal_launch))
        .route("/api/child-terminal", post(child_terminal))
        .route("/api/terminal-action", post(terminal_action))
        .route("/api/mods", get(mods))
        .route("/user-mods/{*path}", get(mod_asset))
        .route("/api/machines", get(machines_get).post(machines_post))
        .fallback(static_asset)
        .method_not_allowed_fallback(|| async { Error(StatusCode::METHOD_NOT_ALLOWED, "method not allowed".into()) })
        .layer(DefaultBodyLimit::disable())
        .layer(middleware::from_fn(boundary::guard))
        .with_state(services)
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut terminate) => { tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = terminate.recv() => {} } }
            Err(error) => { eprintln!("cannot register SIGTERM: {error}"); let _ = tokio::signal::ctrl_c().await; }
        }
    }
}

async fn run() -> Result<()> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let mut plugin=if arguments.first().is_some_and(|argument|argument=="plugin"){
        if arguments.len()!=2{return Err(Error::bad("plugin requires one action"));}
        match plugin::prepare(&arguments[1]).await?{Some(runtime)=>Some(runtime),None=>return Ok(())}
    }else{None};
    if arguments == ["--help"] {
        println!("Herdr Web 0.1.0\n\nUsage: herdr-web\n       herdr-web plugin start|stop|restart|status\n       herdr-web --diagnostics\n\nConnects to original Herdr 0.9.5 (public protocol 22).\nEnvironment: HERDR_BIN, HERDR_SESSION, HERDR_CONFIG_PATH, HERDR_WEB_MODS, PORT.\nDefault listener: http://127.0.0.1:8004; PORT=0 selects an available port.\nPlugin actions require the context supplied by Herdr.\nDiagnostic mode has no backend but retains local file/Git privileges.\nSee README.md for operation and docs/ACCEPTANCE.md for verification status.");
        return Ok(());
    }
    if plugin.is_none()&&!arguments.is_empty() && arguments != ["--diagnostics"] { return Err(Error::bad("usage: herdr-web [--diagnostics] | plugin start|stop|restart|status")); }
    let backend=if let Some(plugin)=&plugin{Some(plugin.backend.clone())}else if arguments.is_empty(){Some(backend::Backend::discover().await?)}else{None};
    let port=if let Some(plugin)=&plugin{plugin.port}else{std::env::var("PORT").unwrap_or_else(|_|"8004".into()).parse::<u16>().map_err(|_|Error::bad("PORT must be an integer from 0 through 65535"))?};
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port)).await?;
    let address = listener.local_addr()?;
    let mods = std::env::var_os("HERDR_WEB_MODS").map(PathBuf::from);
    let (shutdown, receiver) = tokio::sync::watch::channel(false);
    let subscriptions=Arc::new(tokio::sync::Semaphore::new(64));
    let services = Arc::new(Services {
        backend, shutdown: receiver.clone(),
        machines:machines::Machines::new(receiver.clone()),
        attachments: Arc::new(tokio::sync::Semaphore::new(live::ATTACHMENT_LIMIT)),
        subscriptions:subscriptions.clone(),
        mods: extras::Mods::discover(mods.as_deref())?,
        uploads: extras::Uploads::create()?,
        git: git::Jobs::new(receiver),
        watches: watch::Watches::new(subscriptions),
    });
    if let Some(backend) = &services.backend { eprintln!("Selected original Herdr socket: {}", backend.socket.display()); }
    println!("http://{address}");
    if let Some(plugin)=&mut plugin{plugin.start(format!("http://{address}"),shutdown.clone()).await?;}
    let app = router(services.clone());
    let mut stopping=shutdown.subscribe();
    let graceful = axum::serve(listener, app).with_graceful_shutdown(async move {
        tokio::select!{_=shutdown_signal()=>(),_=async{let _=stopping.wait_for(|value|*value).await;}=>()}
        shutdown.send_replace(true);
    });
    graceful.await?;
    services.machines.stop().await;
    services.git.drain().await?;
    let _=tokio::time::timeout(std::time::Duration::from_secs(5),services.attachments.acquire_many(live::ATTACHMENT_LIMIT as u32)).await;
    let _=tokio::time::timeout(std::time::Duration::from_secs(5),services.subscriptions.acquire_many(64)).await;
    drop(plugin);
    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await { eprintln!("herdr-web: {error}"); std::process::exit(1); }
}
