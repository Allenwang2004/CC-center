//! The Python server, started and watched from here.
//!
//! `cc-center-server` is `bin/cc-center-app` frozen by PyInstaller and bundled
//! as a Tauri sidecar (see `desktop/build-sidecar.sh`). It is run as
//! `serve --port 0` so the OS picks a free port, and it prints one line,
//! `ready {"port": …}`, when it is listening --- that line is the handshake.
//!
//! The shell generates the API token and hands it over in the environment, so
//! it is the only client that can call `/api/*`; the page never sees it. If the
//! server dies while the app is up it is started again, the way launchd did for
//! the CLI version.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

use crate::bridge;
use crate::log;

const BINARY: &str = "cc-center-server";
const MAX_RESTARTS: u32 = 5;

pub struct Sidecar {
    app: AppHandle,
    pub token: String,
    port: Arc<Mutex<Option<u16>>>,
    child: Arc<Mutex<Option<CommandChild>>>,
    quitting: Arc<AtomicBool>,
    restarts: Arc<AtomicU32>,
    /// Whether the event stream to the server is currently connected.
    pub stream_open: Arc<AtomicBool>,
}

impl Sidecar {
    pub fn new(app: AppHandle) -> Self {
        let bytes: [u8; 24] = rand::random();
        let token = bytes.iter().map(|b| format!("{b:02x}")).collect();
        Self {
            app,
            token,
            port: Arc::new(Mutex::new(None)),
            child: Arc::new(Mutex::new(None)),
            quitting: Arc::new(AtomicBool::new(false)),
            restarts: Arc::new(AtomicU32::new(0)),
            stream_open: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn port(&self) -> Option<u16> {
        *self.port.lock().unwrap()
    }

    /// The port once the server has said `ready`, or None after `timeout`.
    pub async fn wait_port(&self, timeout: Duration) -> Option<u16> {
        let until = Instant::now() + timeout;
        loop {
            if let Some(p) = self.port() {
                return Some(p);
            }
            if Instant::now() >= until || self.quitting.load(Ordering::SeqCst) {
                return None;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    /// Stop whatever cc-center is already running (the CLI daemon, or an
    /// earlier copy of this app) and start ours. Two watchers over the same
    /// transcripts would notify twice and write the journal twice.
    pub fn start(&self) {
        stop_previous();
        self.spawn();
    }

    fn spawn(&self) {
        let cmd = match self.app.shell().sidecar(BINARY) {
            Ok(c) => c,
            Err(e) => {
                log::line(&format!("[cc-center] sidecar {BINARY} not found: {e}"));
                return;
            }
        };
        let mut cmd = cmd
            .args(["serve", "--no-open", "--port", "0"])
            .env("CC_CENTER_TOKEN".to_string(), self.token.clone())
            .env("CC_CENTER_APP".to_string(), "1".to_string())
            // The server watches this pid and exits when we are gone, however we
            // went: macOS has no way to tie a child's life to its parent's.
            .env("CC_CENTER_PARENT_PID".to_string(), std::process::id().to_string())
            .env("PYTHONUNBUFFERED".to_string(), "1".to_string());
        // The Supabase project is decided when the app is built (desktop/with-env.sh
        // exports .env for cargo); a value already in the environment wins, so a
        // developer can point a build elsewhere without rebuilding.
        for (key, baked) in [
            ("SUPABASE_URL", option_env!("SUPABASE_URL")),
            ("SUPABASE_ANON_KEY", option_env!("SUPABASE_ANON_KEY")),
        ] {
            if std::env::var_os(key).is_none() {
                if let Some(v) = baked {
                    cmd = cmd.env(key.to_string(), v.to_string());
                }
            }
        }

        let (mut rx, child) = match cmd.spawn() {
            Ok(x) => x,
            Err(e) => {
                log::line(&format!("[cc-center] could not start {BINARY}: {e}"));
                return;
            }
        };
        *self.child.lock().unwrap() = Some(child);

        let app = self.app.clone();
        let token = self.token.clone();
        let port = self.port.clone();
        let child_slot = self.child.clone();
        let quitting = self.quitting.clone();
        let restarts = self.restarts.clone();
        let stream_open = self.stream_open.clone();
        let alive = Arc::new(AtomicBool::new(true));

        tauri::async_runtime::spawn(async move {
            while let Some(event) = rx.recv().await {
                match event {
                    CommandEvent::Stdout(bytes) => {
                        let text = String::from_utf8_lossy(&bytes);
                        let line = text.trim();
                        if let Some(json) = line.strip_prefix("ready ") {
                            let p = serde_json::from_str::<serde_json::Value>(json)
                                .ok()
                                .and_then(|v| v["port"].as_u64())
                                .map(|p| p as u16);
                            if let Some(p) = p {
                                log::line(&format!("[cc-center] server ready on 127.0.0.1:{p}"));
                                *port.lock().unwrap() = Some(p);
                                restarts.store(0, Ordering::SeqCst);
                                bridge::run(
                                    app.clone(),
                                    p,
                                    token.clone(),
                                    stream_open.clone(),
                                    alive.clone(),
                                );
                            }
                        } else if !line.is_empty() {
                            log::line(&format!("[server] {line}"));
                        }
                    }
                    CommandEvent::Stderr(bytes) => {
                        let text = String::from_utf8_lossy(&bytes);
                        let text = text.trim_end();
                        if !text.is_empty() {
                            log::line(&format!("[server] {text}"));
                        }
                    }
                    CommandEvent::Terminated(status) => {
                        *port.lock().unwrap() = None;
                        alive.store(false, Ordering::SeqCst);
                        child_slot.lock().unwrap().take();
                        let _ = app.emit("cc:stream", serde_json::json!({ "open": false }));
                        if quitting.load(Ordering::SeqCst) {
                            break;
                        }
                        let n = restarts.fetch_add(1, Ordering::SeqCst) + 1;
                        if n > MAX_RESTARTS {
                            log::line(&format!("[cc-center] server keeps dying (exit {:?}); giving up", status.code));
                            break;
                        }
                        log::line(&format!("[cc-center] server exited ({:?}); restarting in 3s ({n}/{MAX_RESTARTS})", status.code));
                        tokio::time::sleep(Duration::from_secs(3)).await;
                        if let Some(state) = app.try_state::<Sidecar>() {
                            state.spawn();
                        }
                        break;
                    }
                    _ => {}
                }
            }
        });
    }

    /// SIGTERM, so the server clears its pidfile on the way out; SIGKILL as
    /// the fallback if it has not gone after a moment.
    pub fn stop(&self) {
        self.quitting.store(true, Ordering::SeqCst);
        let child = self.child.lock().unwrap().take();
        if let Some(child) = child {
            let pid = child.pid();
            let _ = std::process::Command::new("kill")
                .args(["-TERM", &pid.to_string()])
                .output();
            let until = Instant::now() + Duration::from_secs(3);
            while Instant::now() < until && process_alive(pid) {
                std::thread::sleep(Duration::from_millis(50));
            }
            if process_alive(pid) {
                let _ = child.kill();
            }
        }
        *self.port.lock().unwrap() = None;
    }
}

/// The pidfile the server keeps (`~/.cc-center/app.json`, or under
/// `CC_CENTER_STATE`): if the pid in it is alive, ask it to stop and wait.
/// Done here rather than by running `cc-center-server stop`, which would cost
/// a PyInstaller unpack (about four seconds) just to read one file.
fn stop_previous() {
    let state = std::env::var_os("CC_CENTER_STATE")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".cc-center")));
    let Some(state) = state else { return };
    let Ok(text) = std::fs::read_to_string(state.join("app.json")) else { return };
    let Ok(info) = serde_json::from_str::<serde_json::Value>(&text) else { return };
    let Some(pid) = info["pid"].as_u64().map(|p| p as u32) else { return };
    if pid == std::process::id() || !process_alive(pid) {
        return;
    }
    log::line(&format!("[cc-center] stopping the cc-center already running (pid {pid})"));
    let _ = std::process::Command::new("kill").args(["-TERM", &pid.to_string()]).output();
    let until = Instant::now() + Duration::from_secs(8);
    while Instant::now() < until && process_alive(pid) {
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn process_alive(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// A GUI app on macOS starts with launchd's PATH, which has none of the
/// user's tools --- and the server needs `claude`, `ssh`, `git`, `lsof`. Ask
/// the login shell what PATH it would have and adopt it, so the sidecar
/// inherits the same one a terminal would.
pub fn adopt_login_shell_path() {
    #[cfg(unix)]
    {
        let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
        let out = std::process::Command::new(&shell)
            .args(["-ilc", "printf '__CC_PATH__=%s\\n' \"$PATH\""])
            .stdin(std::process::Stdio::null())
            .output();
        if let Ok(out) = out {
            let text = String::from_utf8_lossy(&out.stdout);
            for line in text.lines() {
                if let Some(path) = line.strip_prefix("__CC_PATH__=") {
                    let path = path.trim();
                    if !path.is_empty() {
                        std::env::set_var("PATH", path);
                        return;
                    }
                }
            }
        }
    }
}
