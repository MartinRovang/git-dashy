"""The GUI: a localhost HTTP server over the same State the curses screen reads.

ponytail: stdlib http.server, no framework. One State, one refresh thread, three endpoints — the
browser polls /api/state and posts to /api/review. The Tauri shell is a window pointed at this same
URL, so there is one UI to maintain and it works with no Rust toolchain installed.
"""
import base64
import http.server
import json
import logging
import os
import secrets
import shutil
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlparse

from .. import HERE, VERSION, config
from ..core import team
from ..core.state import State, in_flight

LOG = logging.getLogger(__name__)
HERE_UI = os.path.dirname(os.path.realpath(__file__))
PAGE = os.path.join(HERE_UI, "gui.html")
LOGO = os.path.join(HERE_UI, "head.png")  # the mascot, cut from the repo's logo.png
# where the compiled desktop shell is looked for, in order. ponytail: no config key — a built binary
# is either in the checkout or on PATH, and $GITDASHY_DESKTOP covers anyone who put it elsewhere.
DESKTOP = os.path.join(HERE_UI, "..", "..", "desktop", "src-tauri", "target")


def payload(state):
	"""Everything one frame of the GUI needs, as plain JSON."""
	with state.lock:
		sections = list(state.sections)
	out = []
	for name, prs, err in sections:
		rows = []
		for p in prs or []:
			url = p.get("url", "")
			rows.append({
				"url": url,
				"number": p.get("number"),
				"title": p.get("title", ""),
				"repo": p.get("repository", {}).get("nameWithOwner", ""),
				"author": p.get("author", {}).get("login", ""),
				"updatedAt": p.get("updatedAt", ""),
				"isDraft": bool(p.get("isDraft")),
				"status": p.get("status", ""),
				"checks": p.get("checks", ""),
				"reviewers": p.get("reviewers", ""),
				"review": state.reviews.get(url, ""),
				"busy": in_flight(state, url),
			})
		out.append({"name": name, "prs": rows, "error": err or ""})
	return {
		"version": VERSION,
		"sections": out,
		"fetchedAt": state.fetched_at,
		"fetching": bool(state.fetching),
		"auto": bool(state.auto),
		"model": state.model,
		"running": len(state.running),
	}


def find_pr(state, url):
	with state.lock:
		sections = list(state.sections)
	for _name, prs, _err in sections:
		for p in prs or []:
			if p.get("url") == url:
				return p
	return None


def handler(state, token, page):
	class H(http.server.BaseHTTPRequestHandler):
		protocol_version = "HTTP/1.1"

		def log_message(self, fmt, *args):
			LOG.debug("gui %s", fmt % args)

		def send(self, code, body, ctype="application/json"):
			body = body if isinstance(body, bytes) else body.encode()
			self.send_response(code)
			self.send_header("Content-Type", ctype)
			self.send_header("Content-Length", str(len(body)))
			# ponytail: the page talks to its own origin only; nothing here is meant to be embedded.
			self.send_header("X-Frame-Options", "DENY")
			self.end_headers()
			self.wfile.write(body)

		def guard(self, parts):
			"""A shared secret on every request, and a localhost Host.

			ponytail: this server answers with your PRs and starts Claude runs that cost money, so it is
			a trust boundary even on loopback. Any page you happen to have open can POST to 127.0.0.1
			without reading the reply, and can reach it by a hostname that resolves there (DNS
			rebinding) — the token stops the first, the Host check stops the second.
			"""
			host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
			if host not in ("127.0.0.1", "localhost", "::1"):
				self.send(403, '{"error":"bad host"}')
				return False
			got = self.headers.get("X-Dashy-Token") or (parts.get("token") or [""])[0]
			if not secrets.compare_digest(got, token):
				self.send(403, '{"error":"bad token"}')
				return False
			return True

		def do_GET(self):
			u = urlparse(self.path)
			parts = parse_qs(u.query)
			if not self.guard(parts):
				return
			if u.path == "/":
				return self.send(200, page, "text/html; charset=utf-8")
			if u.path == "/api/state":
				return self.send(200, json.dumps(payload(state)))
			self.send(404, '{"error":"not found"}')

		def do_POST(self):
			u = urlparse(self.path)
			parts = parse_qs(u.query)
			if not self.guard(parts):
				return
			if u.path != "/api/review":
				return self.send(404, '{"error":"not found"}')
			try:
				n = int(self.headers.get("Content-Length") or 0)
				body = json.loads(self.rfile.read(n) or b"{}")
			except (ValueError, json.JSONDecodeError):
				return self.send(400, '{"error":"bad body"}')
			pr = find_pr(state, body.get("url", ""))
			if pr is None:
				return self.send(404, '{"error":"no such pr"}')
			if in_flight(state, pr["url"]):
				return self.send(409, '{"error":"already running"}')
			state.start_review(pr)
			self.send(200, '{"ok":true}')

	return H


def desktop_binary():
	"""The compiled desktop shell, or "" when it has not been built.

	ponytail: release before debug, then PATH. Nothing here builds it — a `cargo build` triggered by a
	dashboard launch is a five-minute surprise on a machine that may not even have rust.
	"""
	if env := os.environ.get("GITDASHY_DESKTOP"):
		return env if os.access(env, os.X_OK) else ""
	for profile in ("release", "debug"):
		exe = os.path.join(DESKTOP, profile, "gitdashy-desktop")
		if os.access(exe, os.X_OK):
			return exe
	return shutil.which("gitdashy-desktop") or ""


ENTRY = os.path.join(HERE_UI, "..", "..", "prs.py")  # the entry point for THIS copy of the package


def launch_desktop(exe, argv):
	"""Hand this process over to the desktop shell, which starts its own server behind a splash.

	ponytail: execv, not spawn — the shell IS the app from here on. A parent that only waits is a
	process in the tree doing nothing, and it would break the shell's own parent-death watchdog.

	ponytail: GITDASHY_BIN is pinned to the checkout that is running RIGHT NOW. The shell otherwise
	falls back to `gitdashy` on PATH, which on a machine with an older install is a different build —
	one without --gui, which drops into curses with no terminal attached and dies on cbreak(). Running
	./prs.py --gui from a checkout must run that checkout.
	"""
	if os.path.isfile(ENTRY):
		os.environ.setdefault("GITDASHY_BIN", os.path.realpath(ENTRY))
	drop = {"--gui", "--browser"}
	os.execv(exe, [exe] + [a for a in argv[1:] if a not in drop])


def watch_parent(interval=1.0):
	"""Exit when the process that started us goes away.

	ponytail: os.getppid() alone — no signals, no psutil. An orphan is reparented, so a changed ppid is
	the whole test. This exists because the desktop shell spawns this server: if that window is killed
	outright, nothing in it runs, and what is left is a server holding your token and polling GitHub
	forever with no UI attached to close it. Only the shell asks for this; a server you started in a
	terminal yourself is left alone.
	"""
	first = os.getppid()

	def run():
		while os.getppid() == first:
			time.sleep(interval)
		LOG.info("parent %s exited, shutting down", first)
		os._exit(0)  # ponytail: the request threads are daemons with nothing to flush

	threading.Thread(target=run, daemon=True).start()


def main(interval, auto, model, open_browser=True, port=0, orphan_exit=False, token=""):
	"""Serve the GUI and block. Returns only when interrupted.

	port and token are supplied by the desktop shell, which picked them before spawning us and polls
	until we answer on them. Left empty, we choose our own — what a person running --gui gets.
	"""
	if orphan_exit:
		watch_parent()
	state = State(interval, model)
	if moved := team.migrate():
		LOG.info("%s", moved)
	team.activate()
	if auto:
		state.set_auto(True)
	threading.Thread(target=state.loop, daemon=True).start()

	with open(PAGE, encoding="utf-8") as f:
		page = f.read()
	# ponytail: inlined, not served. An <img src> carries no token, so a route for it would have to be
	# a hole in the guard — and the mark is 3 KB. One substitution beats an exempted endpoint.
	with open(LOGO, "rb") as f:
		page = page.replace("{{LOGO}}", "data:image/png;base64," + base64.b64encode(f.read()).decode())
	token = token or secrets.token_urlsafe(24)
	srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler(state, token, page))
	srv.daemon_threads = True
	url = f"http://127.0.0.1:{srv.server_port}/?token={token}"
	# ponytail: flush=True because a person may be reading this line to open the URL themselves, and
	# python block-buffers stdout when it is piped (into `tee`, a log, a terminal multiplexer).
	# The desktop shell no longer depends on it: it picks the port and token and polls until we
	# answer, precisely so the handshake cannot hang on a buffer nobody flushed.
	print(f"gitdashy {VERSION} gui — {url}\n  ctrl-c to stop", flush=True)
	if open_browser:
		threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
	try:
		srv.serve_forever()
	except KeyboardInterrupt:
		print()
	finally:
		srv.shutdown()
