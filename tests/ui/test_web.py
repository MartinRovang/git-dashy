"""The GUI server. ponytail: the payload shape and the guard — the HTML is eyeballed, not asserted."""
import io
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
	That build may not speak --no-open at all, and would open something else with no terminal."""
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
	p = subprocess.Popen([sys.executable, os.path.join(repo, "prs.py"), "--no-open", "--demo"],
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


def test_self_review_routes_to_the_other_starter(served, monkeypatch):
	"""Pre-review posts nothing, so it must not fall through to the one that does."""
	base, token, state = served
	both = []
	monkeypatch.setattr(state, "start_review", lambda pr: both.append("review"))
	monkeypatch.setattr(state, "start_self_review", lambda pr: both.append("self"))
	post(f"{base}/api/review", {"url": "u", "self": True}, token)
	post(f"{base}/api/review", {"url": "u"}, token)
	assert both == ["self", "review"]


def test_auto_toggles_without_reviewing_the_backlog(served, monkeypatch):
	base, token, state = served
	seen = []
	monkeypatch.setattr(state, "set_auto", lambda on, **kw: seen.append((on, kw)))
	assert json.load(post(f"{base}/api/auto", {"on": True}, token))["ok"]
	post(f"{base}/api/auto", {"on": False}, token)
	# ponytail: include_existing only when the page says so — it asks first, with the count, as `a` did
	assert seen == [(True, {"include_existing": False}), (False, {"include_existing": False})]
	post(f"{base}/api/auto", {"on": True, "includeExisting": True}, token)
	assert seen[-1] == (True, {"include_existing": True})


def test_detail_carries_checks_and_the_last_review(served, monkeypatch):
	base, token, state = served
	monkeypatch.setattr(state, "want_detail", lambda pr: {"branch": "b", "add": 1, "del": 2, "files": 3,
	                                                      "checks": [{"name": "lint", "state": "ok"}]})
	monkeypatch.setattr(web.log, "last", lambda url: {"verdict": "approve", "summary": "s", "model": "m",
	                                                  "at": "2020-01-01T00:00:00Z", "findings": []})
	d = json.load(get(f"{base}/api/pr?url=u", token))
	assert (d["pending"], d["branch"], d["files"]) == (False, "b", 3)
	assert d["checks"] == [{"name": "lint", "state": "ok"}]
	assert d["review"]["verdict"].endswith("approved") and d["review"]["summary"] == "s"
	with pytest.raises(urllib.error.HTTPError) as e:
		get(f"{base}/api/pr?url=nope", token)
	assert e.value.code == 404


def test_detail_is_pending_while_the_fetch_is_in_flight(served, monkeypatch):
	"""want_detail answers None until its thread lands; the page polls, so the pane must say so."""
	base, token, state = served
	monkeypatch.setattr(state, "want_detail", lambda pr: None)
	monkeypatch.setattr(web.log, "last", lambda url: None)
	d = json.load(get(f"{base}/api/pr?url=u", token))
	assert d["pending"] and d["checks"] == [] and d["review"] is None


def test_settings_apply_and_persist(served, monkeypatch):
	base, token, state = served
	saved = []
	monkeypatch.setattr(web.config, "save", saved.append)
	assert json.load(post(f"{base}/api/settings", {"interval": 60, "model": "sonnet"}, token))["ok"]
	assert (state.interval, state.model) == (60, "sonnet")
	assert state.wake.is_set()  # a shorter interval must not wait out the longer one it replaced
	assert saved[-1]["interval"] == 60 and saved[-1]["model"] == "sonnet"


@pytest.mark.parametrize("body", [{"interval": 0}, {"interval": "soon"}, {"interval": 999999}, {"model": ""}])
def test_settings_refuse_junk(served, monkeypatch, body):
	"""The interval drives a loop against the GitHub API; nothing off the wire reaches it unchecked."""
	base, token, state = served
	monkeypatch.setattr(web.config, "save", lambda v: None)
	before = (state.interval, state.model)
	with pytest.raises(urllib.error.HTTPError) as e:
		post(f"{base}/api/settings", body, token)
	assert e.value.code == 400
	assert (state.interval, state.model) == before


def test_payload_offers_the_pickers_their_options():
	d = web.payload(State(interval=999, model="m"))
	assert d["options"]["interval"] == web.config.INTERVALS and d["options"]["model"] == web.config.MODELS
	assert d["settings"]["theme"] in d["options"]["theme"]


def test_download_desktop_fetches_once_then_reuses(tmp_path, monkeypatch, capsys):
	monkeypatch.setattr(web, "INSTALLED", str(tmp_path / "installed"))
	monkeypatch.setattr(web, "asset_name", lambda: "gitdashy-desktop-test")
	fetched = []

	def urlopen(url, timeout=None):
		fetched.append(url)
		if "nowhere" in url:
			raise urllib.error.URLError("no route")
		return io.BytesIO(b"#!/bin/sh\n")

	monkeypatch.setattr(web.urllib.request, "urlopen", urlopen)
	exe = web.download_desktop(url="https://example/asset")
	assert exe == str(tmp_path / "installed" / "gitdashy-desktop-test")
	assert os.access(exe, os.X_OK) and open(exe, "rb").read() == b"#!/bin/sh\n"
	# second call: already there, no fetch — a dead url must not matter
	assert web.download_desktop(url="https://nowhere") == exe and fetched == ["https://example/asset"]
	# ponytail: a failed download is a browser session, not a traceback
	monkeypatch.setattr(web, "INSTALLED", str(tmp_path / "other"))
	assert web.download_desktop(url="https://nowhere") == ""
	assert "browser" in capsys.readouterr().out
	assert not os.listdir(tmp_path / "other")  # no .part left behind


def test_asset_name_matches_ci_naming(monkeypatch):
	monkeypatch.setattr(web.platform, "system", lambda: "Darwin")
	monkeypatch.setattr(web.platform, "machine", lambda: "arm64")
	assert web.asset_name() == "gitdashy-desktop-macos-arm64"
	monkeypatch.setattr(web.platform, "system", lambda: "Windows")
	monkeypatch.setattr(web.platform, "machine", lambda: "AMD64")
	assert web.asset_name() == "gitdashy-desktop-windows-x86_64.exe"


@pytest.mark.parametrize("body", [{"voice": []}, {"voice": ["opera"]}, {"theme": "neon"}, {"depth": "deep"}, {"window": 5}, {"subs": "some"}])
def test_settings_refuse_values_off_the_menu(served, monkeypatch, body):
	base, token, state = served
	monkeypatch.setattr(web.config, "save", lambda v: None)
	with pytest.raises(urllib.error.HTTPError) as e:
		post(f"{base}/api/settings", body, token)
	assert e.value.code == 400


def test_settings_cover_every_key_the_curses_screen_had(served, monkeypatch):
	base, token, state = served
	saved = []
	monkeypatch.setattr(web.config, "save", saved.append)
	body = {"depth": "high", "effort": "low", "voice": ["bot", "review"], "hunter": ["tests"], "subs": "off",
	        "window": None, "drafts": True, "notify": False, "theme": "nord"}
	assert json.load(post(f"{base}/api/settings", body, token))["ok"]
	got = saved[-1]
	assert (got["depth"], got["effort"], got["voice"], got["hunter"]) == ("high", "low", ["review", "bot"], ["tests"])
	assert (got["subs"], got["window"], got["drafts"], got["notify"], got["theme"]) == ("off", None, True, False, "nord")
	assert state.subs == "off" and state.window is None and state.drafts


def test_code_rows_keep_every_mark_on_a_line_or_as_an_orphan():
	from dashy.core import diff
	text = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,3 @@\n a\n+b\n c\n"
	files = diff.parse(text)
	marks = diff.anchor(files, [{"kind": "note", "loc": "x.py:2", "text": "on b"}, {"kind": "nit", "loc": "y.py:1", "text": "elsewhere"}])
	kinds = [r["kind"] for r in web.code_rows(files, marks, scoped=True)]
	assert kinds == ["file", "hunk", "line", "line", "note", "line", "gap", "orphan"]
	assert [r["kind"] for r in web.code_rows(files, marks, scoped=False)] == ["file", "hunk", "line", "line", "line", "gap"]


def test_the_page_only_reviews_and_opens_what_is_on_the_board(served, monkeypatch):
	base, token, state = served
	opened = []
	monkeypatch.setattr(web.github, "open_in_browser", opened.append)
	assert json.load(post(f"{base}/api/open", {"url": "u"}, token))["opened"] == "u"
	with pytest.raises(urllib.error.HTTPError) as e:
		post(f"{base}/api/open", {"url": "/etc/passwd"}, token)
	assert e.value.code == 404 and opened == ["u"]
	# no pre-review file yet, so Y has nothing to hand to the desktop
	with pytest.raises(urllib.error.HTTPError) as e:
		post(f"{base}/api/open", {"url": "u", "pre": True}, token)
	assert e.value.code == 404


def test_consent_answers_one_ask_and_drops_it(served, monkeypatch):
	base, token, state = served
	said = []
	monkeypatch.setattr(web.memory, "allow_publishing", lambda key, yes: said.append(("pub", key, yes)))
	monkeypatch.setattr(web.memory, "allow_agents", lambda key, text, yes: said.append(("agents", key, text, yes)))
	state.asks = [{"kind": "publishing", "key": "t1"}, {"kind": "agents", "key": "t1", "text": "do x"}]
	post(f"{base}/api/consent", {"kind": "publishing", "key": "t1", "yes": False}, token)
	assert said == [("pub", "t1", False)] and json.load(get(f"{base}/api/state", token))["asks"] == [{"kind": "agents", "key": "t1", "text": "do x"}]
	post(f"{base}/api/consent", {"kind": "agents", "key": "t1", "yes": True, "text": "do x"}, token)
	assert said[-1] == ("agents", "t1", "do x", True) and json.load(get(f"{base}/api/state", token))["asks"] == []


def test_dream_runs_on_a_thread_and_only_applies_what_it_showed(served, monkeypatch):
	import time
	base, token, state = served
	monkeypatch.setattr(web.memory, "dream", lambda model: ("tidy", {"mine/a.md": "x\ny\n", "mine/general.md": "g\n"}, {"mine/a.md": "x\n", "mine/general.md": ""}))
	written = []
	monkeypatch.setattr(web.memory, "write", written.append)
	monkeypatch.setattr(web.team, "pull_dir", lambda *a: "")
	monkeypatch.setattr(web.team, "pull", lambda: "")
	monkeypatch.setattr(web.team, "push_dir", lambda *a: "")
	monkeypatch.setattr(web.team, "push", lambda *a: "")
	web.JOBS.clear()
	post(f"{base}/api/dream", {"op": "start"}, token)
	for _ in range(50):
		d = json.load(get(f"{base}/api/dream", token))
		if not d["running"]:
			break
		time.sleep(0.05)
	assert d["result"]["summary"] == "tidy" and d["result"]["lost"] == 1
	assert [(f["name"], f["deleted"]) for f in d["result"]["files"]] == [("mine/general", True), ("mine/a", False)]
	assert "new" not in d["result"]  # the page never sees the file bodies; apply uses what the server held
	assert json.load(post(f"{base}/api/dream", {"op": "apply"}, token))["ok"]
	assert written == [{"mine/a.md": "x\n", "mine/general.md": ""}]
	with pytest.raises(urllib.error.HTTPError) as e:
		post(f"{base}/api/dream", {"op": "apply"}, token)  # a dream applies once
	assert e.value.code == 409


def test_a_broken_route_is_a_status_not_a_dead_poll(served, monkeypatch):
	base, token, state = served
	monkeypatch.setattr(web, "get_drafts", lambda state, q: (_ for _ in ()).throw(RuntimeError("boom\nlast line")))
	monkeypatch.setitem(web.GETS, "/api/drafts", web.get_drafts)
	with pytest.raises(urllib.error.HTTPError) as e:
		get(f"{base}/api/drafts", token)
	assert e.value.code == 500 and json.load(e.value)["error"] == "last line"
