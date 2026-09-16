//! cc-center as a menu bar app.
//!
//! The shell does four things and nothing else: it starts the Python server as
//! a sidecar (`sidecar`), forwards the page's API calls to it and turns its
//! event stream into Tauri events (`bridge`), keeps a tray icon with the
//! number of sessions waiting on you (`tray`), and owns one window that hides
//! instead of closing. Everything the tool actually knows lives in the
//! sidecar; that is what lets the Python move to Rust one module at a time
//! without the page noticing.

mod bridge;
mod log;
mod sidecar;
mod tray;

use tauri::{Manager, WindowEvent};

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .invoke_handler(tauri::generate_handler![
            bridge::api,
            bridge::stream_state,
            bridge::quit,
            bridge::app_info,
        ])
        .setup(|app| {
            // A menu bar app: no Dock icon, the window comes and goes.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            log::open();
            tray::build(app.handle())?;

            // The window first, so a double-click shows something at once; the
            // server takes a few seconds to come up and the page waits for it.
            if let Some(window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                window.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        // Closing the window must not stop the watcher.
                        api.prevent_close();
                        tray::hide_window(&handle);
                    }
                });
            }
            tray::show_window(app.handle());

            let state = sidecar::Sidecar::new(app.handle().clone());
            app.manage(state);
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                sidecar::adopt_login_shell_path();
                if let Some(state) = handle.try_state::<sidecar::Sidecar>() {
                    state.start();
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building cc-center")
        .run(|app, event| match event {
            // The app icon clicked while we are already running (Launchpad, Dock,
            // a second double-click): bring the window back.
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Reopen { .. } => tray::show_window(app),
            tauri::RunEvent::Exit => {
                if let Some(state) = app.try_state::<sidecar::Sidecar>() {
                    state.stop();
                }
                log::line("=== app exit ===");
            }
            _ => {}
        });
}
