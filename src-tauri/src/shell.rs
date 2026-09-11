//! The desktop window: a splash page, the in-process server behind it, then the dashboard. Port of the
//! old desktop/src-tauri/src/lib.rs, minus the child process: the server is a thread in this binary.
//!
//! ponytail: ONE window, never hidden. A main window built with visible(false) and shown later has dead
//! title bar buttons on Linux (tauri-apps/tauri#11856), so the splash is a page in the main window.
//!
//! ponytail: the shell owns no UI of its own beyond the splash. It points the window at the same server
//! `gitdashy --browser` serves. One UI, in the browser and here.

use std::time::{Duration, Instant};

use tauri::{Emitter, Manager};

use crate::state::State;

/// How long the first fetch gets before the dashboard is shown anyway, still fetching.
const FETCH_TIMEOUT: Duration = Duration::from_secs(120);
const POLL_EVERY: Duration = Duration::from_millis(300);

/// True once the server has finished its first fetch, or given up on it.
// ponytail: an error counts as done. A machine with no token or no network would otherwise sit on
// the splash for the whole FETCH_TIMEOUT, and the dashboard is where that error is explained.
fn fetched(state: &State) -> bool {
    let s = state.0.lock().unwrap_or_else(|e| e.into_inner());
    s.fetched_at.is_some() || !s.error.is_empty()
}

/// Open the window and block until it closes. `state` is already running its refresh loop.
pub fn run(state: State, port: u16, token: String) {
    let url = format!("http://127.0.0.1:{port}/?token={token}");
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(move |app| {
            let handle = app.handle().clone();
            // ponytail: off the main thread. Waiting for the fetch in setup means the splash never paints.
            std::thread::spawn(move || {
                let step = |name: &str| {
                    let _ = handle.emit("gitdashy-step", name);
                };
                // ponytail: the same three names the splash page lists. The server is a thread now, so
                // the first two are over before the window exists; they are told so the bar fills.
                step("spawn_server");
                step("await_socket");
                step("fetch_pull_requests");
                // ponytail: the first fetch happens behind the splash, so the dashboard opens populated.
                // Past FETCH_TIMEOUT it opens anyway, still fetching: a slow GitHub is not a failure.
                let until = Instant::now() + FETCH_TIMEOUT;
                while Instant::now() < until && !fetched(&state) {
                    std::thread::sleep(POLL_EVERY);
                }
                if let Some(main) = handle.get_webview_window("main") {
                    match url.parse() {
                        Ok(parsed) => {
                            // ponytail: the splash keeps the configured size; the dashboard gets the
                            // whole screen. One window, so the growth happens here, at the handoff,
                            // rather than at startup where it would blow up the splash too.
                            let _ = main.maximize();
                            let _ = main.navigate(parsed);
                        }
                        // the splash page is what is showing, so a failure is said there
                        Err(e) => {
                            let _ = main.emit("gitdashy-error", format!("bad url {url}: {e}"));
                        }
                    }
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while building tauri application");
}
