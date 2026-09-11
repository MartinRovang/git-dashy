//! The desktop shell: one window showing a splash page, and a `gitdashy --no-open` server behind it.
//!
//! ponytail: ONE window, never hidden. A main window built with visible(false) and shown later has dead
//! title bar buttons on Linux (tauri-apps/tauri#11856), so the splash is a page in the main window.
//!
//! ponytail: the shell owns no UI of its own beyond the splash. It starts the same server
//! `gitdashy --browser` starts and points the window at it. One UI, in the browser and here.
//!
//! ponytail: the shell CHOOSES the port and token and hands them to the server, then polls until the
//! server answers on them. It used to read the URL off the server's stdout, which made the handshake
//! depend on the child flushing a pipe — python block-buffers stdout when it is not a tty, so a
//! missing flush hung the splash forever with no way to tell that apart from a slow start. Deciding
//! the address up front means there is nothing to parse and a failure is a timeout, not a hang.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Emitter, Manager};

/// How long the server gets to answer before the splash reports failure.
const READY_TIMEOUT: Duration = Duration::from_secs(30);
const POLL_EVERY: Duration = Duration::from_millis(100);
/// How long the first fetch gets before the dashboard is shown anyway, still fetching.
const FETCH_TIMEOUT: Duration = Duration::from_secs(120);

/// The server process, so it can be killed when the window closes.
// ponytail: a child that outlives its window is a python server holding your token and polling
// GitHub forever, with no UI to close. It gets killed on exit, not left to the OS.
struct Server(Mutex<Option<Child>>);

fn binary() -> String {
    std::env::var("GITDASHY_BIN").unwrap_or_else(|_| "gitdashy".into())
}

/// A free port on loopback.
// ponytail: bind, read the number, drop. There is a gap between dropping this and the server binding
// in which something else could take it — vanishingly unlikely on loopback, and the alternative is
// passing a socket across a process boundary. A lost race surfaces as the startup timeout below.
fn free_port() -> Result<u16, String> {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr().map(|a| a.port()))
        .map_err(|e| format!("could not reserve a port: {e}"))
}

fn token() -> Result<String, String> {
    let mut raw = [0u8; 24];
    getrandom::fill(&mut raw).map_err(|e| format!("no randomness for the session token: {e}"))?;
    Ok(raw.iter().map(|b| format!("{b:02x}")).collect())
}

/// The server's state, once it answers 200 on this port with this token. None until then.
// ponytail: a raw GET over TcpStream, not an http client crate. One request to one known loopback
// address; HTTP/1.0 with Connection: close, so the body is simply everything after the blank line.
fn state(port: u16, token: &str) -> Option<serde_json::Value> {
    let mut sock = TcpStream::connect(("127.0.0.1", port)).ok()?;
    let _ = sock.set_read_timeout(Some(Duration::from_secs(5)));
    let req = format!(
        "GET /api/state HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Dashy-Token: {token}\r\nConnection: close\r\n\r\n"
    );
    sock.write_all(req.as_bytes()).ok()?;
    let mut raw = Vec::new();
    sock.read_to_end(&mut raw).ok()?;
    let text = String::from_utf8_lossy(&raw);
    let (head, body) = text.split_once("\r\n\r\n")?;
    if !(head.starts_with("HTTP/1.") && head.get(9..12) == Some("200")) {
        return None;
    }
    serde_json::from_str(body).ok()
}

/// True once the server has finished its first fetch, or given up on it.
// ponytail: an error counts as done. A machine with no token or no network would otherwise sit on
// the splash for the whole FETCH_TIMEOUT, and the dashboard is where that error is explained.
fn fetched(v: &serde_json::Value) -> bool {
    v["fetchedAt"].is_number() || v["error"].as_str().is_some_and(|e| !e.is_empty())
}

/// Start the server on a port we chose, and wait for it to answer there and fetch once.
/// `step` is told each phase as it begins, for the splash.
fn start(extra: &[String], step: &dyn Fn(&str)) -> Result<(Child, String), String> {
    step("spawn_server");
    let port = free_port()?;
    let token = token()?;
    let mut child = Command::new(binary())
        .arg("--no-open")
        .arg("--port")
        .arg(port.to_string())
        .args(extra)
        // ponytail: the token goes in the ENVIRONMENT. argv is world-readable in ps, and this token
        // starts review runs that cost money.
        .env("GITDASHY_GUI_TOKEN", &token)
        .stdout(Stdio::null())
        .spawn()
        .map_err(|e| {
            format!(
                "could not run {}: {e}. Install it, or set GITDASHY_BIN.",
                binary()
            )
        })?;

    step("await_socket");
    let deadline = Instant::now() + READY_TIMEOUT;
    while Instant::now() < deadline {
        // a server that has already exited will never answer; its status says more than a timeout
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("gitdashy exited before serving ({status})"));
        }
        if state(port, &token).is_some() {
            step("fetch_pull_requests");
            // ponytail: the first fetch happens behind the splash, so the dashboard opens populated.
            // Past FETCH_TIMEOUT it opens anyway, still fetching: a slow GitHub is not a failure.
            let until = Instant::now() + FETCH_TIMEOUT;
            while Instant::now() < until && !state(port, &token).is_some_and(|v| fetched(&v)) {
                if let Ok(Some(status)) = child.try_wait() {
                    return Err(format!("gitdashy exited while fetching ({status})"));
                }
                std::thread::sleep(POLL_EVERY * 3);
            }
            return Ok((child, format!("http://127.0.0.1:{port}/?token={token}")));
        }
        std::thread::sleep(POLL_EVERY);
    }
    let _ = child.kill();
    let _ = child.wait();
    Err(format!(
        "gitdashy did not answer on port {port} within {}s",
        READY_TIMEOUT.as_secs()
    ))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Server(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            // ponytail: off the main thread — start() blocks until the server answers, and blocking
            // setup means the splash never paints.
            std::thread::spawn(move || {
                let extra: Vec<String> = std::env::args().skip(1).collect();
                let splash = handle.clone();
                let step = move |name: &str| {
                    let _ = splash.emit("gitdashy-step", name);
                };
                match start(&extra, &step) {
                    Ok((child, url)) => {
                        *handle.state::<Server>().0.lock().unwrap() = Some(child);
                        // ponytail: the page's Quit stops the server; a window over a dead server is a
                        // blank pane nobody can close from inside, so the shell follows it out.
                        let watcher = handle.clone();
                        std::thread::spawn(move || loop {
                            std::thread::sleep(Duration::from_millis(500));
                            let gone = match watcher.state::<Server>().0.lock().unwrap().as_mut() {
                                Some(c) => matches!(c.try_wait(), Ok(Some(_))),
                                None => return,
                            };
                            if gone {
                                watcher.exit(0);
                                return;
                            }
                        });
                        if let Some(main) = handle.get_webview_window("main") {
                            match url.parse() {
                                Ok(parsed) => {
                                    let _ = main.navigate(parsed);
                                }
                                Err(e) => {
                                    let _ =
                                        main.emit("gitdashy-error", format!("bad url {url}: {e}"));
                                }
                            }
                        }
                    }
                    // the splash page is what is showing, so a failure is said there
                    Err(e) => {
                        if let Some(main) = handle.get_webview_window("main") {
                            let _ = main.emit("gitdashy-error", e);
                        }
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        // ponytail: RunEvent::Exit, not the main window's Destroyed — it fires for every graceful way
        // out (last window closed, quit, ctrl-c), where Destroyed only covers one of them. The server
        // also watches its own parent, because nothing here runs if this process is SIGKILLed.
        .run(|handle, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(mut child) = handle.state::<Server>().0.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait(); // reap it, or it lingers as a zombie
                }
            }
        });
}
