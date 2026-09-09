//! The desktop shell: a splash window, and a `gitdashy --gui` server behind it.
//!
//! ponytail: the shell owns no UI of its own beyond the splash. It starts the same server
//! `gitdashy --gui` starts and points the window at it. One UI, in the browser and here.
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

/// True once the server answers 200 on this port with this token.
// ponytail: a raw GET over TcpStream, not an http client crate. One request to one known loopback
// address, and the only thing being asked is whether it answers.
fn answers(port: u16, token: &str) -> bool {
	let Ok(mut sock) = TcpStream::connect(("127.0.0.1", port)) else {
		return false;
	};
	let _ = sock.set_read_timeout(Some(Duration::from_secs(2)));
	let req = format!(
		"GET /api/state HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Dashy-Token: {token}\r\nConnection: close\r\n\r\n"
	);
	if sock.write_all(req.as_bytes()).is_err() {
		return false;
	}
	let mut head = [0u8; 15]; // "HTTP/1.0 200 OK" is 15 bytes; the status line is all that matters
	let mut got = 0;
	while got < head.len() {
		match sock.read(&mut head[got..]) {
			Ok(0) | Err(_) => return false,
			Ok(n) => got += n,
		}
	}
	head.starts_with(b"HTTP/1.") && head[9..12] == *b"200"
}

/// Start the server on a port we chose, and wait for it to answer there.
fn start(extra: &[String]) -> Result<(Child, String), String> {
	let port = free_port()?;
	let token = token()?;
	let mut child = Command::new(binary())
		.arg("--gui")
		.arg("--no-open")
		.arg("--port")
		.arg(port.to_string())
		.args(extra)
		// ponytail: the token goes in the ENVIRONMENT. argv is world-readable in ps, and this token
		// starts review runs that cost money.
		.env("GITDASHY_GUI_TOKEN", &token)
		.stdout(Stdio::null())
		.spawn()
		.map_err(|e| format!("could not run {}: {e}. Install it, or set GITDASHY_BIN.", binary()))?;

	let deadline = Instant::now() + READY_TIMEOUT;
	while Instant::now() < deadline {
		// a server that has already exited will never answer; its status says more than a timeout
		if let Ok(Some(status)) = child.try_wait() {
			return Err(format!("gitdashy exited before serving ({status})"));
		}
		if answers(port, &token) {
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
				match start(&extra) {
					Ok((child, url)) => {
						*handle.state::<Server>().0.lock().unwrap() = Some(child);
						if let Some(main) = handle.get_webview_window("main") {
							match url.parse() {
								Ok(parsed) => {
									let _ = main.navigate(parsed);
									let _ = main.show();
									let _ = main.set_focus();
								}
								Err(e) => {
									if let Some(s) = handle.get_webview_window("splashscreen") {
										let _ = s.emit("gitdashy-error", format!("bad url {url}: {e}"));
									}
									return;
								}
							}
						}
						if let Some(splash) = handle.get_webview_window("splashscreen") {
							let _ = splash.close();
						}
					}
					// the splash is the only window up, so a failure has to be said there
					Err(e) => {
						if let Some(splash) = handle.get_webview_window("splashscreen") {
							let _ = splash.emit("gitdashy-error", e);
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
