//! Where the app's words go when nobody is watching a terminal.
//!
//! Launched from Finder, the shell's stdout leads nowhere, and with it every
//! line the server prints. So everything the shell says about the server, and
//! everything the server says, is appended to the same file the CLI version
//! uses --- `~/.cc-center/app.log` (or `$CC_CENTER_STATE/app.log`) --- which
//! means `bin/cc-center-app logs -f` reads it too. Lines still go to stdout as
//! well, for a terminal run with `CC_CENTER_DEBUG=1`.
//!
//! One generation of rotation: a file over 5 MB is moved aside at startup.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

const ROTATE_OVER: u64 = 5 * 1024 * 1024;

static FILE: OnceLock<Mutex<Option<File>>> = OnceLock::new();

pub fn path() -> PathBuf {
    let state = std::env::var_os("CC_CENTER_STATE")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cc-center")))
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    state.join("app.log")
}

/// Open (and if needed rotate) the log. Called once at startup; a failure just
/// means lines go to stdout only.
pub fn open() {
    let p = path();
    if let Some(dir) = p.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(meta) = std::fs::metadata(&p) {
        if meta.len() > ROTATE_OVER {
            let _ = std::fs::rename(&p, p.with_extension("log.1"));
        }
    }
    let file = OpenOptions::new().create(true).append(true).open(&p).ok();
    let _ = FILE.set(Mutex::new(file));
    line(&format!("=== app start {} (pid {}) ===", stamp(), std::process::id()));
}

/// One line: to the file with a timestamp, and to stdout as is.
pub fn line(text: &str) {
    println!("{text}");
    if let Some(slot) = FILE.get() {
        if let Ok(mut guard) = slot.lock() {
            if let Some(f) = guard.as_mut() {
                let _ = writeln!(f, "{} {}", stamp(), text);
            }
        }
    }
}

fn stamp() -> String {
    chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string()
}
