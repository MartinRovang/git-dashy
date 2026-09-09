"""The GUI server. ponytail: the payload shape and the guard — the HTML is eyeballed, not asserted."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from dashy.core.state import State
from dashy.ui import web

from conftest import PR

# ponytail: conftest points urllib at a recorder so no test can reach the network. These tests are the
# one exception — the server under test IS on a socket — so they hold the real opener, captured at
# import time before the fixture swaps it. The guard stays on for everybody else.
URLOPEN = urllib.request.urlopen


@pytest.fixture
def served():
	"""A real server over a State with one PR in it, and its token."""
	state = State(interval=999, model="m")
	state.sections = [("MINE", [dict(PR, status="· awaiting review", checks="✓")], None),
	                  ("ASSIGNED", None, "boom\nsecond line")]
	token = "t0ken"
	srv = web.handler(state, token, "<html>page</html>")
	import http.server
	httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), srv)
	httpd.daemon_threads = True
	threading.Thread(target=httpd.serve_forever, daemon=True).start()
	yield f"http://127.0.0.1:{httpd.server_port}", token, state
	httpd.shutdown()


def get(url, token=None, host=None):
	req = urllib.request.Request(url)
	if token:
		req.add_header("X-Dashy-Token", token)
	if host:
		req.add_header("Host", host)
	return URLOPEN(req)


def test_payload_flattens_sections_and_carries_status():
	state = State(interval=999, model="m")
	state.sections = [("MINE", [dict(PR, status="· awaiting review")], None),
	                  ("ASSIGNED", None, "boom\nsecond line")]
	state.reviews = {"u": "3 findings"}
	d = web.payload(state)
	assert [s["name"] for s in d["sections"]] == ["MINE", "ASSIGNED"]
	pr = d["sections"][0]["prs"][0]
	assert (pr["number"], pr["repo"], pr["author"]) == (7, "a/b", "me")
	assert pr["status"] == "· awaiting review"
	assert pr["review"] == "3 findings"  # the review status rides along with its PR
	assert not pr["busy"]
	assert d["sections"][1]["prs"] == [] and d["sections"][1]["error"].startswith("boom")
	# json.dumps is what the handler does with it — a value that will not serialise must fail here
	json.dumps(d)


def test_busy_follows_in_flight():
	state = State(interval=999, model="m")
	state.sections = [("MINE", [dict(PR)], None)]
	state.running.add("u")
	assert web.payload(state)["sections"][0]["prs"][0]["busy"]
	assert web.payload(state)["running"] == 1


def test_no_token_is_refused(served):
	base, token, _ = served
	with pytest.raises(urllib.error.HTTPError) as e:
		get(f"{base}/api/state")
	assert e.value.code == 403
	with pytest.raises(urllib.error.HTTPError) as e:
		get(f"{base}/api/state?token=wrong")
	assert e.value.code == 403


def test_token_in_header_or_query_is_accepted(served):
	base, token, _ = served
	assert json.load(get(f"{base}/api/state", token))["sections"][0]["name"] == "MINE"
	assert json.load(get(f"{base}/api/state?token=" + token))["running"] == 0
	assert get(f"{base}/?token={token}").read() == b"<html>page</html>"


def test_foreign_host_is_refused(served):
	"""DNS rebinding: a name that resolves to 127.0.0.1 still arrives with its own Host."""
	base, token, _ = served
	with pytest.raises(urllib.error.HTTPError) as e:
		get(f"{base}/api/state?token={token}", host="evil.example.com")
	assert e.value.code == 403


def post(url, body, token):
	req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
	req.add_header("X-Dashy-Token", token)
	req.add_header("Content-Type", "application/json")
	return URLOPEN(req)


def test_review_starts_the_known_pr_only(served, monkeypatch):
	base, token, state = served
	started = []
	monkeypatch.setattr(state, "start_review", started.append)
	assert json.load(post(f"{base}/api/review", {"url": "u"}, token))["ok"]
	assert started and started[0]["number"] == 7
	# a url that is not on the board is not something the GUI may start a paid run against
	with pytest.raises(urllib.error.HTTPError) as e:
		post(f"{base}/api/review", {"url": "https://elsewhere/1"}, token)
	assert e.value.code == 404
	assert len(started) == 1


def test_review_will_not_double_start(served, monkeypatch):
	base, token, state = served
	monkeypatch.setattr(state, "start_review", lambda pr: None)
	state.running.add("u")
	with pytest.raises(urllib.error.HTTPError) as e:
		post(f"{base}/api/review", {"url": "u"}, token)
	assert e.value.code == 409


def test_orphan_exit_kills_the_server_when_its_parent_dies(tmp_path):
	"""ponytail: the desktop shell can be SIGKILLed, and then nothing in it runs. The server has to
	notice on its own — or it polls GitHub forever with your token and no window to close."""
	import subprocess
	import sys
	import time

	# a grandchild that outlives its parent: the parent exits at once, the child watches and should follow
	script = tmp_path / "child.py"
	script.write_text(
		"import os, sys, time\n"
		f"sys.path.insert(0, {os.getcwd()!r})\n"
		"from dashy.ui import web\n"
		"web.watch_parent(0.05)\n"
		"print(os.getpid(), flush=True)\n"
		"time.sleep(30)\n")
	parent = tmp_path / "parent.py"
	parent.write_text(
		"import subprocess, sys\n"
		f"p = subprocess.Popen([sys.executable, {str(script)!r}], stdout=subprocess.PIPE)\n"
		"print(p.stdout.readline().decode().strip(), flush=True)\n")

	out = subprocess.run([sys.executable, str(parent)], capture_output=True, timeout=30)
	pid = int(out.stdout.decode().strip())
	for _ in range(100):  # the parent is already gone; the watchdog polls at 50ms
		time.sleep(0.05)
		try:
			os.kill(pid, 0)
		except OSError:
			return  # gone, as it must be
	os.kill(pid, 9)
	raise AssertionError(f"server {pid} outlived its parent")


def test_desktop_binary_prefers_release_then_debug_then_path(tmp_path, monkeypatch):
	monkeypatch.delenv("GITDASHY_DESKTOP", raising=False)
	monkeypatch.setattr(web, "DESKTOP", str(tmp_path))
	monkeypatch.setattr(web.shutil, "which", lambda _n: None)
	assert web.desktop_binary() == ""  # nothing built: the caller falls back to the browser

	def build(profile):
		d = tmp_path / profile
		d.mkdir(exist_ok=True)
		exe = d / "gitdashy-desktop"
		exe.write_text("#!/bin/sh\n")
		exe.chmod(0o755)
		return str(exe)

	debug = build("debug")
	assert web.desktop_binary() == debug
	release = build("release")
	assert web.desktop_binary() == release  # release wins once it exists

	monkeypatch.setattr(web, "DESKTOP", str(tmp_path / "nowhere"))
	monkeypatch.setattr(web.shutil, "which", lambda _n: "/usr/bin/gitdashy-desktop")
	assert web.desktop_binary() == "/usr/bin/gitdashy-desktop"  # else whatever is on PATH


def test_desktop_binary_env_override_must_be_executable(tmp_path, monkeypatch):
	dud = tmp_path / "not-executable"
	dud.write_text("")
	monkeypatch.setenv("GITDASHY_DESKTOP", str(dud))
	# ponytail: a pointed-at path that cannot run is an error to fall back from, not one to exec
	assert web.desktop_binary() == ""
	dud.chmod(0o755)
	assert web.desktop_binary() == str(dud)


def test_launch_desktop_forwards_flags_but_not_gui_or_browser(monkeypatch):
	seen = []
	monkeypatch.setattr(web.os, "execv", lambda exe, args: seen.append((exe, args)))
	web.launch_desktop("/opt/app", ["gitdashy", "--gui", "--browser", "--demo", "--interval", "60"])
	assert seen == [("/opt/app", ["/opt/app", "--demo", "--interval", "60"])]


def test_launch_desktop_pins_the_running_checkout(monkeypatch):
	"""ponytail: without this the shell runs `gitdashy` from PATH — a different, possibly older build.
	That build has no --gui, so it opens curses with no terminal and dies on cbreak()."""
	seen, env = [], {}
	monkeypatch.setattr(web.os, "execv", lambda exe, args: seen.append(args))
	monkeypatch.setattr(web.os, "environ", env)
	monkeypatch.setattr(web.os.path, "isfile", lambda p: True)
	monkeypatch.setattr(web.os.path, "realpath", lambda p: "/checkout/prs.py")
	web.launch_desktop("/opt/app", ["gitdashy", "--gui"])
	assert env["GITDASHY_BIN"] == "/checkout/prs.py"
	assert seen == [["/opt/app"]]


def test_launch_desktop_respects_an_explicit_binary(monkeypatch):
	env = {"GITDASHY_BIN": "/somewhere/else"}
	monkeypatch.setattr(web.os, "execv", lambda exe, args: None)
	monkeypatch.setattr(web.os, "environ", env)
	monkeypatch.setattr(web.os.path, "isfile", lambda p: True)
	web.launch_desktop("/opt/app", ["gitdashy", "--gui"])
	assert env["GITDASHY_BIN"] == "/somewhere/else"  # setdefault, so the caller's choice stands


def test_url_line_reaches_a_pipe_promptly():
	"""The desktop shell learns the URL by reading this line off a pipe.

	ponytail: python block-buffers stdout when it is not a tty, so without an explicit flush the line
	sits in the buffer, the shell blocks on readline forever, and the splash hangs on its last step.
	This test spawns the server exactly as the shell does — a pipe, not a terminal.
	"""
	import subprocess
	import sys

	repo = os.getcwd()
	p = subprocess.Popen([sys.executable, os.path.join(repo, "prs.py"), "--gui", "--no-open", "--demo"],
	                     stdout=subprocess.PIPE, cwd=repo)
	try:
		# communicate() with a timeout is the only way to bound a blocking read portably
		out, _ = p.communicate(timeout=15)
	except subprocess.TimeoutExpired:
		p.kill()
		out, _ = p.communicate()
		assert b"http://127.0.0.1:" in out, "the URL line never reached the pipe — stdout was not flushed"
	else:
		assert b"http://127.0.0.1:" in out
	finally:
		if p.poll() is None:
			p.kill()
