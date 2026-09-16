//! The menu bar icon: the app's permanent presence.
//!
//! A template image (macOS tints it for light and dark menu bars) with two
//! numbers as its title --- sessions waiting on you, sessions working --- so
//! the state is readable without opening anything. The menu lists those
//! sessions by name; clicking one opens the window on it. Left click opens the
//! window; the rest of the menu holds the few things worth doing without it.
//!
//! The menu is rebuilt whenever the server says the picture moved (`bridge`
//! calls `refresh`), from `/api/live`, the same two groups the Agents tab shows.

use std::sync::Mutex;

use tauri::image::Image;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_autostart::ManagerExt;

use crate::bridge;

const ID: &str = "main";
const FOCUS: &str = "focus:";

/// One row of `/api/live`.
#[derive(Clone, serde::Deserialize)]
pub struct LiveSession {
    pub session_id: String,
    pub host: String,
    pub name: String,
    pub title: String,
    #[serde(default)]
    pub label: Option<String>,
}

#[derive(Clone, Default, serde::Deserialize)]
pub struct Live {
    #[serde(default)]
    pub waiting: Vec<LiveSession>,
    #[serde(default)]
    pub running: Vec<LiveSession>,
}

/// What the menu currently shows, so a click can be mapped back to a session.
static SHOWN: Mutex<Option<Live>> = Mutex::new(None);

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let menu = menu_for(app, &Live::default())?;
    TrayIconBuilder::with_id(ID)
        .icon(Image::from_bytes(include_bytes!("../icons/tray.png"))?)
        .icon_as_template(true)
        .tooltip("cc-center")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| {
            let id = event.id().as_ref().to_string();
            match id.as_str() {
                "open" => show_window(app),
                "collect" => bridge::post(app, "/api/refresh"),
                "autostart" => {
                    let launcher = app.autolaunch();
                    let on = launcher.is_enabled().unwrap_or(false);
                    let _ = if on { launcher.disable() } else { launcher.enable() };
                    // The item is rebuilt with the menu, so it reads back the real state.
                    refresh(app);
                }
                "quit" => bridge::quit_app(app),
                other => {
                    if let Some(sid) = other.strip_prefix(FOCUS) {
                        show_window(app);
                        let _ = app.emit("cc:focus-session", serde_json::json!({ "session_id": sid }));
                    }
                }
            }
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_window(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn short(s: &str, n: usize) -> String {
    let t = s.trim();
    if t.chars().count() <= n {
        t.to_string()
    } else {
        let cut: String = t.chars().take(n - 1).collect();
        format!("{cut}…")
    }
}

fn menu_for(app: &AppHandle, live: &Live) -> tauri::Result<Menu<tauri::Wry>> {
    let menu = Menu::new(app)?;

    let section = |title: &str, rows: &[LiveSession], menu: &Menu<tauri::Wry>| -> tauri::Result<()> {
        if rows.is_empty() {
            return Ok(());
        }
        menu.append(&MenuItem::with_id(app, format!("head:{title}"), title, false, None::<&str>)?)?;
        for s in rows {
            let text = format!(
                "{} · {} — {}",
                s.name,
                s.host,
                short(if let Some(l) = &s.label { l } else { &s.title }, 48)
            );
            menu.append(&MenuItem::with_id(app, format!("{FOCUS}{}", s.session_id), text, true, None::<&str>)?)?;
        }
        menu.append(&PredefinedMenuItem::separator(app)?)?;
        Ok(())
    };
    section("Waiting on you", &live.waiting, &menu)?;
    section("Working now", &live.running, &menu)?;

    menu.append(&MenuItem::with_id(app, "open", "Open cc-center", true, None::<&str>)?)?;
    menu.append(&MenuItem::with_id(app, "collect", "Collect now", true, None::<&str>)?)?;
    menu.append(&PredefinedMenuItem::separator(app)?)?;
    menu.append(&CheckMenuItem::with_id(
        app,
        "autostart",
        "Start at login",
        true,
        app.autolaunch().is_enabled().unwrap_or(false),
        None::<&str>,
    )?)?;
    menu.append(&PredefinedMenuItem::separator(app)?)?;
    menu.append(&MenuItem::with_id(app, "quit", "Quit cc-center", true, Some("CmdOrCtrl+Q"))?)?;
    Ok(menu)
}

/// Rebuild the menu and the title from what the server says is live now.
pub fn apply(app: &AppHandle, live: Live) {
    if let Some(tray) = app.tray_by_id(ID) {
        if let Ok(menu) = menu_for(app, &live) {
            let _ = tray.set_menu(Some(menu));
        }
        let (w, r) = (live.waiting.len(), live.running.len());
        let _ = tray.set_title(if w + r > 0 { Some(format!("{w} · {r}")) } else { None });
        crate::log::line(&format!("[cc-center] tray: {w} waiting · {r} working"));
    }
    *SHOWN.lock().unwrap() = Some(live);
}

/// Ask the server for the live list and apply it (off the current thread).
pub fn refresh(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        if let Some(text) = bridge::get(&app, "/api/live") {
            if let Ok(live) = serde_json::from_str::<Live>(&text) {
                apply(&app, live);
            }
        }
    });
}

/// Show the window as a regular app: a Dock icon while it is open, and the
/// green button (native full screen) working. An Accessory app cannot go full
/// screen at all; `hide_window` drops back to Accessory so the Dock icon goes
/// away again with the window.
pub fn show_window(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

pub fn hide_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
}
