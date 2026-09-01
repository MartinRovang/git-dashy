import json
import subprocess
import pytest
import prs

PR = {"repository": {"nameWithOwner": "a/b", "name": "b"}, "number": 7, "url": "u", "title": "T",
      "isDraft": False, "author": {"login": "me"}, "updatedAt": "2020-01-01T00:00:00Z"}


@pytest.fixture(autouse=True)
def isolated_log(monkeypatch, tmp_path):
	monkeypatch.setattr(prs, "LOG", str(tmp_path / "log.jsonl"))
	monkeypatch.setattr(prs, "SPLASH_MIN", 0)
	monkeypatch.setattr(prs, "update_available", lambda: "")  # no git chatter in tests


REAL_UPDATE_AVAILABLE = prs.update_available  # the fixture stubs the module attr


class Result:
	def __init__(self, stdout=""):
		self.stdout = stdout


def claude_out(**fields):
	return Result(json.dumps({"result": "Sure:\n" + json.dumps(fields)}))


# ---- age / rows ----

def test_age():
	assert prs.age("2020-01-01T00:00:00Z").endswith("d")
	from datetime import datetime, timezone, timedelta
	now = datetime.now(timezone.utc)
	assert prs.age((now - timedelta(hours=3)).isoformat()) == "3h"
	assert prs.age((now - timedelta(minutes=5)).isoformat()) == "5m"
	assert prs.age(now.isoformat()) == "now"


def test_rows_layout_and_section_tagging():
	p = dict(PR)
	rs = prs.rows([("MINE", [p], None), ("REVIEW REQUESTED", [], None), ("ASSIGNED", None, "boom\nmore")])
	assert [k for k, _ in rs] == ["head", "pr", "blank", "head", "empty", "blank", "head", "err", "blank"]
	assert rs[0][1] == "MINE (1)" and rs[6][1] == "ASSIGNED (!)" and rs[7][1] == "boom"
	assert p["section"] == "MINE"


# ---- fetch ----

def test_fetch_dedups_sorts_and_appends_reviewed(monkeypatch):
	a = dict(PR, url="a", updatedAt="2020-01-01T00:00:00Z")
	b = dict(PR, url="b", updatedAt="2021-01-01T00:00:00Z")
	per_flag = {"--author=@me": [a, b], "--review-requested=@me": [a], "--assignee=@me": []}
	def fake_run(cmd, **kw):
		return Result(json.dumps(per_flag[cmd[4]]))
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	secs = prs.fetch()
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]
	assert [p["url"] for p in secs[0][1]] == ["b", "a"]  # newest first
	assert secs[1][1] == []  # a already shown under MINE
	assert secs[3] == ("REVIEWED", [], None)


def test_fetch_reports_errors_per_section(monkeypatch):
	def fake_run(cmd, **kw):
		if cmd[4] == "--author=@me":
			raise subprocess.CalledProcessError(1, cmd, stderr="gh: not logged in\n")
		return Result("[]")
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	secs = prs.fetch()
	assert secs[0] == ("MINE", None, "gh: not logged in")
	assert secs[1] == ("REVIEW REQUESTED", [], None)


def test_fetch_handles_bad_json(monkeypatch):
	monkeypatch.setattr(prs.subprocess, "run", lambda cmd, **kw: Result("not json"))
	name, ps, err = prs.fetch()[0]
	assert ps is None and err


# ---- review ----

def test_review_posts_verdict_and_logs(monkeypatch):
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return claude_out(verdict="request_changes", summary="adds x", body="nope")
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	assert prs.review(dict(PR), "sonnet") == "✗ changes requested"
	assert calls[0][0] == "claude" and calls[0][calls[0].index("--model") + 1] == "sonnet"
	assert calls[1][:6] == ["gh", "pr", "review", "7", "--repo", "a/b"]
	assert "--request-changes" in calls[1] and calls[1][-1] == "nope"
	entry = json.loads(open(prs.LOG).read())
	assert entry["verdict"] == "request_changes" and entry["summary"] == "adds x"
	assert entry["model"] == "sonnet" and entry["pr"]["url"] == "u"


@pytest.mark.parametrize("verdict,flag,status", [
	("approve", "--approve", "✓ approved"),
	("comment", "--comment", "~ commented"),
])
def test_review_verdict_flags(monkeypatch, verdict, flag, status):
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return claude_out(verdict=verdict, body="b")
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	assert prs.review(dict(PR), "opus") == status
	assert flag in calls[1]


def test_review_unparseable_output_is_error_and_not_posted(monkeypatch):
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return Result(json.dumps({"result": "I could not review this"}))
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	assert prs.review(dict(PR), "opus").startswith("error:")
	assert len(calls) == 1 and not __import__("os").path.exists(prs.LOG)


def test_review_unknown_verdict_is_error(monkeypatch):
	monkeypatch.setattr(prs.subprocess, "run", lambda cmd, **kw: claude_out(verdict="lgtm", body="b"))
	assert prs.review(dict(PR), "opus").startswith("error:")


def test_review_gh_failure_surfaces_stderr(monkeypatch):
	def fake_run(cmd, **kw):
		if cmd[0] == "gh":
			raise subprocess.CalledProcessError(1, cmd, stderr="line1\nfatal: nope\n")
		return claude_out(verdict="approve", body="b")
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	assert prs.review(dict(PR), "opus") == "error: fatal: nope"


def test_review_timeout_is_error(monkeypatch):
	def fake_run(cmd, **kw):
		raise subprocess.TimeoutExpired(cmd, 1)
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	assert prs.review(dict(PR), "opus").startswith("error:")


# ---- reviewed log / detail ----

def test_reviewed_empty_when_no_log():
	assert prs.reviewed() == []


def test_reviewed_newest_first_and_detail(monkeypatch):
	monkeypatch.setattr(prs.subprocess, "run", lambda cmd, **kw: claude_out(verdict="approve", summary="adds x", body="lgtm"))
	prs.review(dict(PR, url="first"), "opus")
	prs.review(dict(PR, url="second"), "opus")
	got = prs.reviewed()
	assert [p["url"] for p in got] == ["second", "first"]
	assert got[0]["status"] == "✓ approved" and got[0]["updatedAt"] == got[0]["review"]["at"]
	d = prs.detail(got[0]["review"])
	assert "a/b#7  T" in d and "adds x" in d and "lgtm" in d and "opus" in d
	assert "q close" in d and "author   me" in d and "+00:00" not in d


def test_reviewed_tolerates_sparse_entries():
	with open(prs.LOG, "w") as f:
		f.write(json.dumps({"at": "2020-01-01T00:00:00+00:00", "model": "opus", "verdict": "comment",
		                    "summary": "", "body": "", "pr": {"repository": {"nameWithOwner": "a/b"}, "number": 1, "url": "u"}}) + "\n")
	p = prs.reviewed()[0]
	assert p["title"] == "?" and p["isDraft"] is False and "author" not in p


def test_mark_rereviews_flags_updated_logged_prs_only():
	prs.log_review(dict(PR, url="old"), "opus", {"verdict": "approve", "body": "ok"}, at="2020-01-01T00:00:00+00:00")
	prs.log_review(dict(PR, url="same"), "opus", {"verdict": "comment", "body": "hm"}, at="2020-01-01T00:00:00+00:00")
	old, same, fresh = dict(PR, url="old", updatedAt="2021-01-01T00:00:00Z"), dict(PR, url="same"), dict(PR, url="fresh")
	secs = [("REVIEW REQUESTED", [old, same, fresh], None), ("REVIEWED", prs.reviewed(), None)]
	assert prs.mark_rereviews(secs) == ["old"]
	assert old["prev"] == "↻ re-review · was ✓ approved" and "prev" not in same and "prev" not in fresh


def test_loop_forgets_stale_verdict_but_not_in_flight(monkeypatch):
	prs.log_review(dict(PR, url="a"), "opus", {"verdict": "approve", "body": ""}, at="2020-01-01T00:00:00+00:00")
	prs.log_review(dict(PR, url="b"), "opus", {"verdict": "approve", "body": ""}, at="2020-01-01T00:00:00+00:00")
	st = prs.State(0)
	st.reviews = {"a": "✓ approved", "b": "reviewing..."}
	rr = [dict(PR, url="a", updatedAt="2021-01-01T00:00:00Z"), dict(PR, url="b", updatedAt="2021-01-01T00:00:00Z")]
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", rr, None), ("REVIEWED", prs.reviewed(), None)])
	assert st.reviews == {"b": "reviewing..."}


# ---- State ----

def test_start_review_marks_in_flight_then_result(monkeypatch):
	import threading
	done = threading.Event()
	def fake_review(pr, model):
		done.wait(5)
		return "✓ approved"
	monkeypatch.setattr(prs, "review", fake_review)
	st = prs.State(0, model="sonnet")
	st.start_review(dict(PR))
	assert st.reviews["u"] == "reviewing..."
	done.set()
	assert st.wake.wait(5)
	assert st.reviews["u"] == "✓ approved"


def test_start_review_uses_model_at_start_time(monkeypatch):
	models = []
	monkeypatch.setattr(prs, "review", lambda pr, model: models.append(model) or "x")
	st = prs.State(0, model="opus")
	st.start_review(dict(PR))
	st.wake.wait(5)
	assert models == ["opus"]


def one_loop(st, monkeypatch, sections):
	monkeypatch.setattr(prs, "fetch", lambda: sections)
	monkeypatch.setattr(st.wake, "wait", lambda t: (_ for _ in ()).throw(SystemExit))
	with pytest.raises(SystemExit):
		st.loop()


def test_auto_reviews_only_new(monkeypatch):
	started = []
	monkeypatch.setattr(prs.State, "start_review", lambda self, p: started.append(p["url"]))
	old, new = {"url": "old"}, {"url": "new"}
	st = prs.State(0)
	st.sections = [("REVIEW REQUESTED", [old], None)]
	st.set_auto(True)
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [old, new], None)])
	assert started == ["new"]


def test_auto_off_reviews_nothing(monkeypatch):
	started = []
	monkeypatch.setattr(prs.State, "start_review", lambda self, p: started.append(p["url"]))
	st = prs.State(0)
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [{"url": "new"}], None)])
	assert started == [] and st.fetched_at is not None


def test_auto_skips_already_reviewed(monkeypatch):
	started = []
	monkeypatch.setattr(prs.State, "start_review", lambda self, p: started.append(p["url"]))
	st = prs.State(0)
	st.set_auto(True)
	st.reviews["done"] = "✓ approved"
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [{"url": "done"}, {"url": "new"}], None)])
	assert started == ["new"]


def test_set_auto_off_clears_baseline():
	st = prs.State(0)
	st.set_auto(True)
	assert st.auto and st.auto_baseline == set()
	st.set_auto(False)
	assert not st.auto and st.auto_baseline is None


# ---- draw (fake screen, no terminal) ----

class FakeScr:
	def __init__(self, h=30, w=100):
		self.h, self.w, self.cells = h, w, {}
	def getmaxyx(self):
		return self.h, self.w
	def erase(self):
		self.cells = {}
	def refresh(self):
		pass
	def addnstr(self, y, x, s, n, attr=0):
		assert 0 <= y < self.h and 0 <= x < self.w and n >= 1, (y, x, n)
		for i, ch in enumerate(s[:n]):
			if x + i < self.w:
				self.cells[(y, x + i)] = ch
	def line(self, y):
		return "".join(self.cells.get((y, x), " ") for x in range(self.w)).rstrip()
	def text(self):
		return "\n".join(self.line(y) for y in range(self.h))


@pytest.fixture
def screen(monkeypatch):
	monkeypatch.setattr(prs, "C", lambda n: 0)
	monkeypatch.setattr(prs.curses, "A_REVERSE", 1 << 18, raising=False)
	monkeypatch.setattr(prs.curses, "A_ITALIC", 1 << 23, raising=False)
	return FakeScr()


def test_draw_renders_sections_status_and_selection(screen):
	screen.w = 140
	st = prs.State(60)
	st.sections = [("MINE", [dict(PR, url="m")], None),
	               ("REVIEW REQUESTED", [dict(PR, url="r", number=8, title="Needs eyes", isDraft=True)], None),
	               ("ASSIGNED", None, "boom"), ("REVIEWED", [], None)]
	st.fetched_at = __import__("time").time()
	st.reviews["r"] = "✓ approved"
	sel, cur = prs.draw(screen, st, 1)
	out = screen.text()
	assert sel == 1 and cur["url"] == "r"
	assert "PRs 2" in out and "MINE (1)" in out and "ASSIGNED (!)" in out and "boom" in out
	assert "▸" in out and "b#8" in out and "draft" in out and "✓ approved" in out
	assert "1 approved" in out and "model: " + st.model in out
	assert "next refresh" in out


def test_draw_clamps_selection_and_handles_empty(screen):
	st = prs.State(60)
	sel, cur = prs.draw(screen, st, 5)
	assert sel == 0 and cur is None and "fetching" in screen.text()
	st.sections = [("MINE", [dict(PR)], None)]
	sel, cur = prs.draw(screen, st, 99)
	assert sel == 0 and cur["url"] == "u"
	sel, cur = prs.draw(screen, st, -3)
	assert sel == 0


def test_draw_prompt_replaces_footer(screen):
	st = prs.State(60)
	prs.draw(screen, st, 0, prompt=" sure? [y/n]")
	assert screen.line(screen.h - 1).strip() == "sure? [y/n]"


def test_draw_truncates_long_title_on_narrow_screen(screen):
	screen.w = 40
	st = prs.State(60)
	st.sections = [("MINE", [dict(PR, title="x" * 200)], None)]
	prs.draw(screen, st, 0)  # must not raise
	assert "…" in screen.text()


def test_draw_reviewed_rows_use_logged_status(screen, monkeypatch):
	monkeypatch.setattr(prs.subprocess, "run", lambda cmd, **kw: claude_out(verdict="comment", body="b"))
	prs.review(dict(PR), "opus")
	st = prs.State(60)
	st.sections = [("REVIEWED", prs.reviewed(), None)]
	prs.draw(screen, st, 0)
	assert "~ commented" in screen.text()


def test_draw_summary_under_reviewed_open_pr(screen, monkeypatch):
	monkeypatch.setattr(prs.subprocess, "run", lambda cmd, **kw: claude_out(verdict="approve", summary="Adds a retry loop " * 20, body="b"))
	prs.review(dict(PR), "opus")
	st = prs.State(60)
	st.sections = [("REVIEW REQUESTED", [dict(PR)], None), ("REVIEWED", prs.reviewed(), None)]
	prs.draw(screen, st, 0)
	lines = [l for l in screen.text().splitlines() if "↳" in l]
	assert len(lines) == 2 and all(l.endswith("…") and len(l.split("↳ ")[1]) <= 70 for l in lines)


# ---- demo ----

def test_demo_is_self_contained(monkeypatch, tmp_path):
	monkeypatch.setenv("TMPDIR", str(tmp_path))
	monkeypatch.setattr(prs.time, "sleep", lambda s: None)
	monkeypatch.setattr(prs.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("demo must not shell out")))
	prs.demo()
	assert prs.LOG.startswith(str(tmp_path))
	secs = prs.fetch()
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]
	assert len(secs[3][1]) == 2  # seeded history
	assert len(prs.fetch()[1][1]) == 4 and len(prs.fetch()[1][1]) == 5  # a new PR shows up on the 3rd refresh (+1 re-requested)
	assert prs.mark_rereviews(prs.fetch()) == ["https://github.com/acme/infra/pull/44"]
	p = secs[1][1][0]
	statuses = [prs.review(p, "opus") for _ in range(4)]
	assert statuses[:3] == ["✓ approved", "✗ changes requested", "~ commented"] and statuses[3].startswith("error:")
	assert len(prs.reviewed()) == 5
	st = prs.State(0)
	st.sections = prs.fetch()
	prs.C = lambda n: 0
	prs.draw(FakeScr(), st, 0)  # renders without raising


def test_rows_reviewed_window_filters_but_keeps_summaries():
	from datetime import datetime, timezone, timedelta
	now = datetime.now(timezone.utc)
	def ent(url, hours):
		e = {"at": (now - timedelta(hours=hours)).isoformat(), "model": "opus", "verdict": "approve",
		     "summary": "sum " + url, "body": "", "pr": dict(PR, url=url)}
		return {**e["pr"], "review": e, "status": "✓ approved", "updatedAt": e["at"]}
	secs = [("REVIEW REQUESTED", [dict(PR, url="old")], None), ("REVIEWED", [ent("new", 0.5), ent("old", 5)], None)]
	rs = prs.rows(secs, window=4)
	assert rs[0][1] == "REVIEW REQUESTED (1)" and rs[2] == ("sub", "sum old")  # summary survives the window
	assert ("head", "REVIEWED · last 4h (1)") in rs and [p["url"] for k, p in rs if k == "pr"] == ["old", "new"]
	assert ("head", "REVIEWED (2)") in prs.rows(secs, window=None)
	assert ("head", "REVIEWED · last 1h (0)") not in prs.rows(secs, window=1) and ("head", "REVIEWED · last 1h (1)") in prs.rows(secs, window=1)


def test_rows_subs_modes():
	from datetime import datetime, timezone
	e = {"at": datetime.now(timezone.utc).isoformat(), "model": "opus", "verdict": "approve", "summary": "s", "body": "", "pr": dict(PR)}
	rv = {**e["pr"], "review": e, "status": "✓ approved", "updatedAt": e["at"]}
	secs = [("REVIEW REQUESTED", [dict(PR)], None), ("REVIEWED", [rv], None)]
	count = lambda mode: [k for k, _ in prs.rows(secs, subs=mode)].count("sub")
	assert count("all") == 2 and count("open") == 1 and count("off") == 0


LS_REMOTE = ("abc\trefs/tags/v0.9.0\n" "def\trefs/tags/v1.10.0\n" "fed\trefs/tags/v1.2.0\n")


def test_update_available_offers_newer_release(monkeypatch):
	monkeypatch.setattr(prs.subprocess, "run", lambda cmd, **kw: Result(LS_REMOTE))
	monkeypatch.setattr(prs, "VERSION", "1.2.0")
	assert REAL_UPDATE_AVAILABLE() == "1.10.0"  # numeric compare, not lexical


def test_update_available_silent_when_current(monkeypatch):
	monkeypatch.setattr(prs.subprocess, "run", lambda cmd, **kw: Result(LS_REMOTE))
	monkeypatch.setattr(prs, "VERSION", "1.10.0")
	assert REAL_UPDATE_AVAILABLE() == ""


def test_update_available_is_empty_when_git_fails(monkeypatch):
	def boom(cmd, **kw):
		raise prs.subprocess.CalledProcessError(1, cmd, stderr="no origin")
	monkeypatch.setattr(prs.subprocess, "run", boom)
	assert REAL_UPDATE_AVAILABLE() == ""


def test_loop_records_available_release(monkeypatch):
	monkeypatch.setattr(prs, "update_available", lambda: "1.2.3")
	st = prs.State(0)
	one_loop(st, monkeypatch, [("MINE", [], None)])
	assert st.update == "1.2.3"
