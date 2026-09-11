//! The desktop window: a splash page, the in-process server behind it, then the dashboard. Port of the
//! old desktop/src-tauri/src/lib.rs, minus the child process: the server is a thread in this binary.
//!
//! ponytail: ONE window, never hidden. A main window built with visible(false) and shown later has dead
//! title bar buttons on Linux (tauri-apps/tauri#11856), so the splash is a page in the main window.
//!
//! ponytail: the shell owns no UI of its own beyond the splash. It points the window at the same server
//! `gitdashy --browser` serves. One UI, in the browser and here.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use tauri::{Emitter, Listener, Manager};

use crate::state::State;

/// How long the first fetch gets before the dashboard is shown anyway, still fetching.
const FETCH_TIMEOUT: Duration = Duration::from_secs(120);
const POLL_EVERY: Duration = Duration::from_millis(300);
/// How long the splash stays up once the last step is ticked, so the fill is seen before the handover.
const HANDOVER_BEAT: Duration = Duration::from_millis(500);

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
            // ponytail: the splash says when its listeners are up. Emitting the steps from setup raced
            // the page — they all fired before it loaded, so only the first step ever spun.
            let listening = Arc::new(AtomicBool::new(false));
            let flag = listening.clone();
            handle.listen("gitdashy-splash-ready", move |_| flag.store(true, Ordering::SeqCst));
            // ponytail: off the main thread. Waiting for the fetch in setup means the splash never paints.
            std::thread::spawn(move || {
                // ponytail: the server and the socket are up before this window exists, so the splash
                // starts with those two ticked. The first fetch is the only phase left to run.
                // Past FETCH_TIMEOUT it opens anyway, still fetching: a slow GitHub is not a failure.
                let until = Instant::now() + FETCH_TIMEOUT;
                while Instant::now() < until && !fetched(&state) {
                    std::thread::sleep(POLL_EVERY);
                }
                // ponytail: hold the done signal until the splash is listening, so a first fetch that
                // lands before the page does is not missed the way the old step events were.
                let ready_until = Instant::now() + Duration::from_secs(5);
                while !listening.load(Ordering::SeqCst) && Instant::now() < ready_until {
                    std::thread::sleep(Duration::from_millis(20));
                }
                // tick the last step, then a beat so it is seen before the dashboard replaces the page
                let _ = handle.emit("gitdashy-done", ());
                std::thread::sleep(HANDOVER_BEAT);
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
