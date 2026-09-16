//! Between the page and the server.
//!
//! In a browser the page talks HTTP to the server directly. In the app the
//! page has no token and no origin, so every call comes here as the `api`
//! command and is forwarded to the sidecar with the token; and the server's
//! event stream (SSE) is read by a thread here and re-emitted as Tauri events
//! (`cc:sse`, `cc:stream`). The page's `core/api.ts` picks which of the two
//! it is running under; nothing else in the front end knows.
//!
//! When a piece of the server moves to Rust, its route can be answered here
//! instead of forwarded, and the page will not notice.

use std::io::{BufRead, BufReader};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_notification::NotificationExt;

use crate::sidecar::Sidecar;
use crate::tray;

/// A tray refresh is already scheduled; events in a burst share one.
static TRAY_PENDING: AtomicBool = AtomicBool::new(false);

#[derive(serde::Serialize)]
pub struct ApiResponse {
    status: u16,
    body: String,
}

/// One HTTP call to the server, with the token. Waits for the server to come
/// up first, so a call made while it is still starting simply takes longer.
#[tauri::command]
pub async fn api(
    state: State<'_, Sidecar>,
    method: String,
    path: String,
    body: Option<String>,
) -> Result<ApiResponse, String> {
    let port = state
        .wait_port(Duration::from_secs(25))
        .await
        .ok_or_else(|| "the cc-center server did not start".to_string())?;
    let token = state.token.clone();
    tauri::async_runtime::spawn_blocking(move || forward(port, &token, &method, &path, body))
        .await
        .map_err(|e| e.to_string())?
}

fn forward(port: u16, token: &str, method: &str, path: &str, body: Option<String>) -> Result<ApiResponse, String> {
    let url = format!("http://127.0.0.1:{port}{path}");
    let req = ureq::request(method, &url)
        .set("X-CC-Token", token)
        .timeout(Duration::from_secs(120));
    let result = match body {
        Some(b) => req.set("Content-Type", "application/json").send_string(&b),
        None => req.call(),
    };
    match result {
        Ok(r) => Ok(ApiResponse { status: r.status(), body: r.into_string().unwrap_or_default() }),
        Err(ureq::Error::Status(code, r)) => {
            Ok(ApiResponse { status: code, body: r.into_string().unwrap_or_default() })
        }
        Err(e) => Err(format!("cc-center server unreachable: {e}")),
    }
}

/// Whether the event stream is connected right now, for a page that just loaded.
#[tauri::command]
pub fn stream_state(state: State<'_, Sidecar>) -> bool {
    state.stream_open.load(Ordering::SeqCst)
}

#[derive(serde::Serialize)]
pub struct AppInfo {
    port: Option<u16>,
    version: &'static str,
}

#[tauri::command]
pub fn app_info(state: State<'_, Sidecar>) -> AppInfo {
    AppInfo { port: state.port(), version: env!("CARGO_PKG_VERSION") }
}

/// Quit the whole app (the page's "Stop the server" button in app mode).
#[tauri::command]
pub fn quit(app: AppHandle) {
    quit_app(&app);
}

pub fn quit_app(app: &AppHandle) {
    if let Some(state) = app.try_state::<Sidecar>() {
        state.stop();
    }
    app.exit(0);
}

/// A GET from the shell itself (the tray's live list). Blocking; call off the UI thread.
pub fn get(app: &AppHandle, path: &str) -> Option<String> {
    let state = app.try_state::<Sidecar>()?;
    let port = state.port()?;
    let token = state.token.clone();
    match forward(port, &token, "GET", path, None) {
        Ok(r) if r.status < 400 => Some(r.body),
        _ => None,
    }
}

/// Fire-and-forget POST from the tray menu (Collect now).
pub fn post(app: &AppHandle, path: &'static str) {
    let Some(state) = app.try_state::<Sidecar>() else { return };
    let Some(port) = state.port() else { return };
    let token = state.token.clone();
    std::thread::spawn(move || {
        let _ = forward(port, &token, "POST", path, Some("{}".to_string()));
    });
}

/// Read the server's SSE stream and re-emit it as Tauri events, reconnecting
/// while this incarnation of the server is alive. The `attention` event also
/// drives the number on the tray icon.
pub fn run(app: AppHandle, port: u16, token: String, open: Arc<AtomicBool>, alive: Arc<AtomicBool>) {
    std::thread::spawn(move || {
        let url = format!("http://127.0.0.1:{port}/api/events?token={token}");
        // The server pings every 15s; a read that stalls far longer than that
        // is a dead connection, and a reconnect is the fix.
        let agent = ureq::AgentBuilder::new()
            .timeout_read(Duration::from_secs(90))
            .build();
        while alive.load(Ordering::SeqCst) {
            let response = agent.get(&url).call();
            if let Ok(response) = response {
                open.store(true, Ordering::SeqCst);
                let _ = app.emit("cc:stream", serde_json::json!({ "open": true }));
                schedule_tray(&app);
                let reader = BufReader::new(response.into_reader());
                let (mut event, mut data) = (String::new(), String::new());
                for line in reader.lines() {
                    let Ok(line) = line else { break };
                    if line.is_empty() {
                        if !event.is_empty() {
                            dispatch(&app, &event, &data);
                        }
                        event.clear();
                        data.clear();
                    } else if let Some(v) = line.strip_prefix("event: ") {
                        event = v.to_string();
                    } else if let Some(v) = line.strip_prefix("data: ") {
                        data = v.to_string();
                    }
                    // ": ping" and "retry:" lines fall through
                    if !alive.load(Ordering::SeqCst) {
                        break;
                    }
                }
                open.store(false, Ordering::SeqCst);
                let _ = app.emit("cc:stream", serde_json::json!({ "open": false }));
            }
            if !alive.load(Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(Duration::from_secs(2));
        }
    });
}

fn dispatch(app: &AppHandle, event: &str, data: &str) {
    let value: serde_json::Value = serde_json::from_str(data).unwrap_or(serde_json::Value::Null);
    match event {
        // The picture moved: the menu bar list and numbers follow the Agents tab.
        "attention" | "update" | "hosts" => schedule_tray(app),
        // The server decided someone needs you; the app says so in its own name,
        // so the notification carries cc-center's icon and a click brings the
        // window back (macOS reopens the app, see lib.rs).
        "notify" => {
            let title = value["title"].as_str().unwrap_or("cc-center");
            let subtitle = value["subtitle"].as_str().unwrap_or("");
            let body = value["body"].as_str().unwrap_or("");
            let text = if subtitle.is_empty() { body.to_string() } else { format!("{subtitle}\n{body}") };
            let mut n = app.notification().builder().title(title).body(text);
            if value["sound"].as_bool().unwrap_or(false) {
                n = n.sound("default");
            }
            if let Err(e) = n.show() {
                crate::log::line(&format!("[cc-center] notification failed: {e}"));
            }
        }
        _ => {}
    }
    let _ = app.emit("cc:sse", serde_json::json!({ "event": event, "data": value }));
}

/// Coalesce a burst of events into one `/api/live` read, shortly after the last.
fn schedule_tray(app: &AppHandle) {
    if TRAY_PENDING.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(700));
        TRAY_PENDING.store(false, Ordering::SeqCst);
        tray::refresh(&app);
    });
}
