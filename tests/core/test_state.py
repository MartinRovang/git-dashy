import os
import sys
import threading
import time

import pytest

from dashy import config
from dashy.core import github, log, review as review_mod, state, team, update
from dashy.core.state import State

from conftest import PR, fake_http, gql_nodes


def test_loop_forgets_stale_verdict_but_not_in_flight(monkeypatch):
	log.log_review(dict(PR, url="a"), "opus", {"verdict": "approve", "body": ""}, at="2020-01-01T00:00:00+00:00")
	log.log_review(dict(PR, url="b"), "opus", {"verdict": "approve", "body": ""}, at="2020-01-01T00:00:00+00:00")
	st = State(0)
	st.reviews = {"a": "✓ approved", "b": "reviewing..."}
	st.running.add("b")   # ponytail: in-flight is the SET now, not the "..." on the status string
	rr = [dict(PR, url="a", updatedAt="2021-01-01T00:00:00Z"), dict(PR, url="b", updatedAt="2021-01-01T00:00:00Z")]
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", rr, None), ("REVIEWED", log.reviewed(), None)])
	assert st.reviews == {"b": "reviewing..."}


# ---- State ----

def test_start_review_marks_in_flight_then_result(monkeypatch):
	import threading
	done = threading.Event()
	def fake_review(pr, model):
		done.wait(5)
		return "✓ approved"
	monkeypatch.setattr(review_mod, "review", fake_review)
	st = State(0, model="sonnet")
	st.start_review(dict(PR))
	assert st.reviews["u"] == "reviewing..."
	done.set()
	assert st.wake.wait(5)
	assert st.reviews["u"] == "✓ approved"


@pytest.mark.parametrize("start, target", [("start_review", "review"), ("start_self_review", "self_review")])
def test_a_review_that_raises_clears_the_row(monkeypatch, start, target):
	def boom(pr, model):
		raise RuntimeError("worker died")
	monkeypatch.setattr(review_mod, target, boom)
	st = State(0, model="sonnet")
	getattr(st, start)(dict(PR))
	assert st.wake.wait(5)
	assert st.reviews["u"] == "error: worker died"
	assert not st.running and "u" not in st.started_at  # nothing spins, nothing is kept


def test_start_review_uses_model_at_start_time(monkeypatch):
	models = []
	monkeypatch.setattr(review_mod, "review", lambda pr, model: models.append(model) or "x")
	st = State(0, model="opus")
	st.start_review(dict(PR))
	st.wake.wait(5)
	assert models == ["opus"]


def one_loop(st, monkeypatch, sections):
	monkeypatch.setattr(github, "fetch", lambda: sections)
	monkeypatch.setattr(st.wake, "wait", lambda t: (_ for _ in ()).throw(SystemExit))
	with pytest.raises(SystemExit):
		st.loop()


def test_auto_reviews_only_new(monkeypatch):
	started = []
	monkeypatch.setattr(State, "start_review", lambda self, p: started.append(p["url"]))
	old, new = {"url": "old"}, {"url": "new"}
	st = State(0)
	st.sections = [("REVIEW REQUESTED", [old], None)]
	st.set_auto(True)
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [old, new], None)])
	assert started == ["new"]


def test_auto_off_reviews_nothing(monkeypatch):
	started = []
	monkeypatch.setattr(State, "start_review", lambda self, p: started.append(p["url"]))
	st = State(0)
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [{"url": "new"}], None)])
	assert started == [] and st.fetched_at is not None


def test_auto_skips_already_reviewed(monkeypatch):
	started = []
	monkeypatch.setattr(State, "start_review", lambda self, p: started.append(p["url"]))
	st = State(0)
	st.set_auto(True)
	st.reviews["done"] = "✓ approved"
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [{"url": "done"}, {"url": "new"}], None)])
	assert started == ["new"]


def test_set_auto_off_clears_baseline():
	st = State(0)
	st.set_auto(True)
	assert st.auto and st.auto_baseline == set()
	st.set_auto(False)
	assert not st.auto and st.auto_baseline is None

def test_loop_records_available_release(monkeypatch):
	monkeypatch.setattr(update, "update_available", lambda: "1.2.3")
	st = State(0)
	one_loop(st, monkeypatch, [("MINE", [], None)])
	assert st.update == "1.2.3"


def test_loop_wait_reads_interval_each_slice(monkeypatch):
	st = State(600)
	monkeypatch.setattr(github, "fetch", lambda: [("MINE", [], None)])
	monkeypatch.setattr(update, "update_available", lambda: "")
	monkeypatch.setattr(config, "SPLASH_MIN", 0)
	# ponytail: this used to raise on the second wait unconditionally, so it never observed whether the
	# loop EXITED because of the new interval — it pinned the slice size and nothing else. Hoisting the
	# deadline out of the condition then broke `i` with the suite still green. Counting TICKS is what
	# distinguishes "noticed" from "kept waiting": one tick means the shrink was ignored.
	ticks, waits = [], []
	monkeypatch.setattr(State, "tick", lambda self, t0: ticks.append(t0) or setattr(self, "fetched_at", time.time()))
	def wait(t):
		waits.append(t)
		st.interval = 0  # shrink mid-wait: loop must notice and refetch instead of sleeping 600 slices
		if len(ticks) > 1:
			raise SystemExit  # it noticed: a second refresh started
		assert len(waits) < 6, "the wait ignored the new interval"  # else this spins for 600 slices
		return False
	monkeypatch.setattr(st.wake, "wait", wait)
	with pytest.raises(SystemExit):
		st.loop()
	assert waits == [1, 1] and len(ticks) == 2


def test_set_auto_include_existing_reviews_listed_prs(monkeypatch):
	started = []
	monkeypatch.setattr(State, "start_review", lambda self, p: started.append(p["url"]))
	st = State(0)
	st.sections = [("REVIEW REQUESTED", [{"url": "old"}, {"url": "done"}], None)]
	st.reviews["done"] = "✓ approved"
	assert st.pending_rr() == ["old"]
	st.set_auto(True, include_existing=True)
	assert st.auto_baseline == set() and st.wake.is_set()
	st.wake.clear()
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [{"url": "old"}, {"url": "done"}], None)])
	assert started == ["old"]


def test_notifies_only_new_after_first_fetch(monkeypatch):
	import dashy.core.state as state_mod
	sent = []
	monkeypatch.setattr(state_mod, "notify", lambda p, section: sent.append((p["url"], section)))
	st = State(0)
	one_loop(st, monkeypatch, [("ASSIGNED", [{"url": "old"}], None)])
	assert sent == []  # first fetch is the baseline, no notification storm on startup
	one_loop(st, monkeypatch, [("ASSIGNED", [{"url": "old"}], None), ("REVIEW REQUESTED", [{"url": "new"}], None)])
	assert sent == [("new", "REVIEW REQUESTED")]


def test_a_failed_fetch_does_not_renotify_the_whole_list(monkeypatch):
	import dashy.core.state as state_mod
	sent = []
	monkeypatch.setattr(state_mod, "notify", lambda p, section: sent.append(p["url"]))
	st = State(0)
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [{"url": "a"}, {"url": "b"}], None), ("ASSIGNED", [], None)])
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", None, "rate limited"), ("ASSIGNED", [], None)])
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [{"url": "a"}, {"url": "b"}], None), ("ASSIGNED", [], None)])
	assert sent == [] and st.known == {"a", "b"}


def test_notify_cmd_pins_the_payload_contract():
	cmd = state.notify_cmd(PR, "ASSIGNED")
	assert cmd[0] == "notify-send" and "-A" in cmd and cmd[-2] == "#7 T" and cmd[-1] == "<b>b</b> · me assigned you"
	assert "wants a review" in state.notify_cmd(PR, "REVIEW REQUESTED")[-1]
	assert all(f in github.ROW for f in ("number", "title", "repository", "author"))
	with pytest.raises(TypeError):
		state.notify_cmd(dict(PR, author=None), "ASSIGNED")  # a deleted account; notify() swallows this


def test_notify_off_stays_quiet(monkeypatch):
	import dashy.core.state as state_mod
	sent = []
	monkeypatch.setattr(state_mod, "notify", lambda p, section: sent.append(p["url"]))
	monkeypatch.setattr(config, "NOTIFY", False)
	st = State(0)
	one_loop(st, monkeypatch, [("ASSIGNED", [], None)])
	one_loop(st, monkeypatch, [("ASSIGNED", [{"url": "new"}], None)])
	assert sent == [] and st.known == {"new"}


def test_the_tick_keeps_a_backup_of_memory(monkeypatch):
	"""Memory is the one thing here that cannot be recreated, so a copy rides the normal refresh."""
	from dashy.core import memory
	order = []
	monkeypatch.setattr(team, "pull", lambda: order.append("pull"))
	monkeypatch.setattr(memory, "backup", lambda reason="tick": order.append(f"backup:{reason}"))
	st = State(0)
	one_loop(st, monkeypatch, [])
	# ponytail: asserted by RUNNING the loop, not by reading its source. A source check passes on code
	# that never executes, which is the one thing a test of "does the tick do this" must not do.
	assert order == ["pull", "backup:tick"]  # and after the pull, so the copy includes what arrived


def test_a_failed_backup_leaves_no_orphan_part_file(monkeypatch, tmp_path):
	"""prune only sees .tar.gz, so a stray .part would sit there forever."""
	from dashy.core import memory
	import tarfile
	mem, backups = tmp_path / "mem", tmp_path / "backups"
	mem.mkdir()
	(mem / "general.md").write_text("- a fact\n")
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.setattr(memory, "BACKUPS", str(backups))
	real_add = tarfile.TarFile.add
	monkeypatch.setattr(tarfile.TarFile, "add",
	                    lambda self, *a, **k: (_ for _ in ()).throw(tarfile.TarError("disk")))
	assert memory.backup("test") == ""
	monkeypatch.setattr(tarfile.TarFile, "add", real_add)
	assert os.path.isdir(backups) and os.listdir(backups) == []


def test_an_empty_memory_dir_setting_does_not_tar_the_cwd(monkeypatch, tmp_path):
	"""PRS_MEMORY= (set but empty) made os.walk(".") archive whatever directory you happened to be in."""
	from dashy.core import memory
	monkeypatch.setattr(config, "MEMORY_DIR", "")
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.setattr(memory, "BACKUPS", str(tmp_path / "b"))
	monkeypatch.chdir(tmp_path)
	(tmp_path / "secret.md").write_text("not memory\n")
	assert memory.backup("test") == ""
	assert not os.path.exists(tmp_path / "b")


def test_pane_detail_is_refetched_when_the_pr_moves(monkeypatch):
	"""Keyed by url alone, the pane kept the old branch head and CI result until a restart."""
	from dashy.core import github as gh
	calls = []
	monkeypatch.setattr(gh, "detail", lambda repo, n: calls.append(n) or {"branch": f"head-{len(calls)}"})
	st = State(60)
	pr = {"url": "u", "updatedAt": "2026-09-04T09:00:00Z", "number": 7,
	      "repository": {"nameWithOwner": "acme/api"}}

	assert st.want_detail(pr) is None                       # first ask starts a fetch
    # the thread is the only async part; wait for it rather than sleeping a fixed time
	for _ in range(400):
		if st.want_detail(pr):
			break
		time.sleep(0.005)
	assert st.want_detail(pr) == {"branch": "head-1"}
	st.want_detail(pr)
	assert len(calls) == 1                                   # cached while the PR has not moved

	moved = dict(pr, updatedAt="2026-09-04T10:00:00Z")
	assert st.want_detail(moved) is None                     # it moved, so it is fetched again
	for _ in range(400):
		if st.want_detail(moved):
			break
		time.sleep(0.005)
	assert st.want_detail(moved) == {"branch": "head-2"}
	assert len(calls) == 2
	assert len(st.details) == 1                              # and the stale revision is not kept


def test_a_finished_verdict_stops_masking_once_the_pr_moves(monkeypatch):
	"""`stale` comes from log.mark_rereviews, which only ever names REVIEW REQUESTED urls — so a
	finished pre-review masked GitHub's decision on a MINE row until restart, and a colleague
	approving your PR never showed there.

	Baselined on the FIRST fetch after the work finishes, not when it finishes: posting a review bumps
	updatedAt itself, so comparing against the value we held would sweep our own verdict a tick later.
	"""
	st = State(0)
	st.reviews["m"] = "✗ changes requested (not posted)"      # a finished pre-review
	same = [("MINE", [dict(PR, url="m", updatedAt="2026-01-01T00:00:00Z")], None)]
	one_loop(st, monkeypatch, same)
	assert st.reviews == {"m": "✗ changes requested (not posted)"}, "unchanged PR keeps the verdict"
	assert st.seen_at["m"] == "2026-01-01T00:00:00Z"           # baselined, not swept

	st.wake = __import__("threading").Event()
	moved = [("MINE", [dict(PR, url="m", updatedAt="2026-06-06T00:00:00Z")], None)]
	one_loop(st, monkeypatch, moved)
	assert st.reviews == {} and "m" not in st.seen_at, "the PR moved, so the verdict is stale"


def test_an_in_flight_run_is_never_swept(monkeypatch):
	"""It is the set that says in-flight, so a status string is not consulted at all."""
	st = State(0)
	st.reviews["m"] = "pre-reviewing..."
	st.running.add("m")
	one_loop(st, monkeypatch, [("MINE", [dict(PR, url="m", updatedAt="2026-01-01T00:00:00Z")], None)])
	assert st.reviews == {"m": "pre-reviewing..."} and "m" not in st.seen_at


def test_an_error_that_ends_in_dots_is_not_an_agent(monkeypatch):
	"""in_flight sniffed a suffix on a channel that also carries a truncated stderr line, so an error
	whose wording happened to end in "..." pinned the UI, counted as running, and blocked a retry —
	permanently, on text nobody controls.
	"""
	from dashy.core.state import in_flight
	st = State(0)
	st.reviews["m"] = "error: could not resolve host github.com..."
	assert not in_flight(st, "m")
	one_loop(st, monkeypatch, [("MINE", [dict(PR, url="m", updatedAt="2026-01-01T00:00:00Z")], None)])
	assert st.seen_at.get("m") == "2026-01-01T00:00:00Z"   # treated as finished, so it can go stale


def test_a_pr_without_updatedAt_does_not_kill_the_refresh_thread(monkeypatch):
	"""A KeyError here runs on the refresh thread and takes the whole loop down with it."""
	st = State(0)
	st.reviews["m"] = "✓ approved"
	one_loop(st, monkeypatch, [("MINE", [{"url": "m", "number": 1, "title": "t",
	                                      "repository": {"nameWithOwner": "a/b", "name": "b"}}], None)])
	assert st.reviews == {"m": "✓ approved"}     # no field, so it simply never goes stale


def test_a_fetch_already_running_when_the_verdict_lands_cannot_baseline_it(monkeypatch):
	"""Posting a review bumps updatedAt, so a fetch that STARTED before the verdict landed carries the
	pre-post value. Baselining on it would sweep our own verdict on the very next tick — which the
	comment claimed to avoid by taking "the next fetch", and did not.
	"""
	st = State(0)
	st.reviews["m"] = "✓ approved"
	st.done_at["m"] = time.time() + 60          # the work finished AFTER this fetch began
	one_loop(st, monkeypatch, [("MINE", [dict(PR, url="m", updatedAt="pre-post")], None)])
	assert "m" not in st.seen_at, "a fetch older than the verdict must not become the baseline"
	assert st.reviews == {"m": "✓ approved"}

	st.done_at["m"] = 0                          # a later fetch, properly after
	st.wake = __import__("threading").Event()
	one_loop(st, monkeypatch, [("MINE", [dict(PR, url="m", updatedAt="post")], None)])
	assert st.seen_at["m"] == "post"


def test_auto_does_not_re_review_on_a_fetch_older_than_the_verdict(monkeypatch):
	"""The re-review sweep reads the fetch's REVIEWED section, which a fetch that started before the
	verdict landed does not carry — so it still sees the OLD log entry, calls the PR pushed-to-since,
	drops the verdict we wrote a second ago, and auto reviews the same head twice.
	"""
	started = []
	monkeypatch.setattr(State, "start_review", lambda self, p: started.append(p["url"]))
	log.log_review(dict(PR, url="a", head="old"), "opus", {"verdict": "approve", "body": ""})
	st = State(0)
	st.set_auto(True, include_existing=True)
	st.reviews["a"] = "✓ approved"      # the re-review of head "new" that just finished
	st.done_at["a"] = time.time() + 60  # ...after this fetch began
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [dict(PR, url="a", head="new")], None),
	                           ("REVIEWED", log.reviewed(), None)])
	assert st.reviews == {"a": "✓ approved"} and started == []


def test_auto_does_not_re_review_the_same_head_when_updatedat_moves(monkeypatch):
	"""`gh search` reads a lagging index, so the tick after a verdict can still carry the pre-post
	updatedAt and the one after it the post-post one — and any reply on the thread bumps it too. The
	head has not moved, so neither is a reason to review again. mark_rereviews already says so by head;
	the updatedAt sweep must not overrule it on REVIEW REQUESTED rows.
	"""
	started = []
	monkeypatch.setattr(State, "start_review", lambda self, p: started.append(p["url"]))
	log.log_review(dict(PR, url="a", head="h1"), "opus", {"verdict": "approve", "body": ""})
	st = State(0)
	st.set_auto(True, include_existing=True)
	st.reviews["a"] = "✓ approved"
	st.done_at["a"] = time.time() - 60
	for at in ("t1", "t2"):
		one_loop(st, monkeypatch, [("REVIEW REQUESTED", [dict(PR, url="a", head="h1", updatedAt=at)], None),
		                           ("REVIEWED", log.reviewed(), None)])
	assert st.reviews == {"a": "✓ approved"} and started == []


def test_auto_keeps_the_verdict_when_graphql_fails_and_the_row_has_no_head(monkeypatch):
	"""`head` is only set when the graphql call succeeds. A tick without it must not fall back to
	updatedAt — that flips the key and drops the verdict, and the next line reviews the same head again.
	"""
	started = []
	monkeypatch.setattr(State, "start_review", lambda self, p: started.append(p["url"]))
	log.log_review(dict(PR, url="a", head="h1"), "opus", {"verdict": "approve", "body": ""})
	st = State(0)
	st.set_auto(True, include_existing=True)
	st.reviews["a"] = "✓ approved"
	st.done_at["a"] = time.time() - 60
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [dict(PR, url="a", head="h1", updatedAt="t1")], None),
	                           ("REVIEWED", log.reviewed(), None)])
	row = {k: v for k, v in dict(PR, url="a", updatedAt="t2").items() if k != "head"}
	one_loop(st, monkeypatch, [("REVIEW REQUESTED", [row], None), ("REVIEWED", log.reviewed(), None)])
	assert st.reviews == {"a": "✓ approved"} and started == []


def _settle(fn, tries=400):
	for _ in range(tries):
		if (got := fn()) is not None:
			return got
		time.sleep(0.005)
	raise AssertionError("the background read never landed")


def test_want_diff_reads_the_diff_off_the_draw_thread(monkeypatch):
	"""`gh pr diff` ran inside draw(), which is called twenty times a second — a slow PR froze the TUI."""
	from dashy.core import diff
	import subprocess as sp
	calls = []
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(sp, "run", lambda cmd, **k: calls.append(cmd) or sp.CompletedProcess(
		cmd, 0, "diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+b\n", ""))
	st = State(60)
	found = [{"kind": "note", "loc": "x.py:2", "text": "t"}]

	assert st.want_diff("a/b", 7, "sha1", found) is None      # the first ask starts it and returns
	files, marks = _settle(lambda: st.want_diff("a/b", 7, "sha1", found))
	assert [f["path"] for f in files] == ["x.py"] and len(marks) == 1
	st.want_diff("a/b", 7, "sha1", found)
	assert len(calls) == 1                                    # cached while head and findings hold

	assert st.want_diff("a/b", 7, "sha2", found) is None       # a push re-reads it
	_settle(lambda: st.want_diff("a/b", 7, "sha2", found))
	assert len(calls) == 2 and len(st.diffs) == 1              # and the old revision is not kept


def test_want_diff_re_reads_when_the_review_changes(monkeypatch):
	"""A re-review changes what is marked without moving the head; the pane showed the old round's marks."""
	from dashy.core import diff
	import subprocess as sp
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(sp, "run", lambda cmd, **k: sp.CompletedProcess(
		cmd, 0, "diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+b\n", ""))
	st = State(60)
	one = [{"kind": "note", "loc": "x.py:2", "text": "first round"}]
	two = [{"kind": "blocking", "loc": "x.py:2", "text": "second round"}]
	_settle(lambda: st.want_diff("a/b", 7, "sha", one))
	_, marks = _settle(lambda: st.want_diff("a/b", 7, "sha", two))
	assert [m["text"] for m in marks] == ["second round"]


def test_want_diff_anchors_once_however_often_it_is_asked(monkeypatch):
	"""anchor() TAGS the lines it marks, so anchoring twice over one parse doubles every note."""
	from dashy.core import diff
	import subprocess as sp
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(sp, "run", lambda cmd, **k: sp.CompletedProcess(
		cmd, 0, "diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+b\n", ""))
	st = State(60)
	found = [{"kind": "note", "loc": "x.py:2", "text": "t"}]
	_settle(lambda: st.want_diff("a/b", 7, "sha", found))
	for _ in range(20):                                        # what draw() does
		files, _m = st.want_diff("a/b", 7, "sha", found)
	tagged = [len(l.get("marks") or []) for f in files for h in f["hunks"] for l in h["lines"]]
	assert max(tagged) == 1


def test_f_can_actually_retry_a_diff_gh_failed_on(monkeypatch):
	"""retry() cleared diff._CACHE while THIS cache went on answering from the failure above it.

	The old retry() tests drove diff.fetch directly and never came through State, which is exactly why
	they passed while one gh blip pinned "no diff to show" for the rest of the session.
	"""
	from dashy.core import diff
	import subprocess as sp
	calls = []
	monkeypatch.setattr(diff, "_CACHE", {})
	def flaky(cmd, **k):
		calls.append(cmd)
		if len(calls) == 1:
			raise OSError("no network")
		return sp.CompletedProcess(cmd, 0, "diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+b\n", "")
	monkeypatch.setattr(sp, "run", flaky)
	st = State(60)
	found = [{"kind": "note", "loc": "x.py:2", "text": "t"}]

	files, marks = _settle(lambda: st.want_diff("a/b", 7, "sha", found))
	assert files == [] and len(marks) == 1       # no diff, and the finding survives as an orphan
	for _ in range(5):
		assert st.want_diff("a/b", 7, "sha", found)[0] == []
	assert len(calls) == 1                       # cached at both layers: no retry storm

	diff.retry()                                 # what f does
	files, _m = _settle(lambda: st.want_diff("a/b", 7, "sha", found))
	assert [f["path"] for f in files] == ["x.py"] and len(calls) == 2


def test_retry_survives_a_fetch_landing_while_it_runs(monkeypatch):
	"""f runs on the UI thread while workers write the cache — a size change mid-iteration unwound curses.

	ponytail: the window is made WIDE on purpose. A few dozen entries and the comprehension finishes
	inside one GIL slice, so the race never shows and the test passes against the unguarded code —
	which is a test that proves nothing. Twenty thousand entries and a tiny switch interval make the
	interleaving the common case: verified to fail without the lock, and to pass with it.
	"""
	from dashy.core import diff
	import subprocess as sp
	import threading as th
	monkeypatch.setattr(diff, "_CACHE", {(f"a/b", i, "s"): None for i in range(20000)})
	monkeypatch.setattr(sp, "run", lambda cmd, **k: (_ for _ in ()).throw(OSError("down")))
	old_interval = sys.getswitchinterval()
	sys.setswitchinterval(1e-6)
	stop, boom = th.Event(), []
	def churn():
		i = 100000
		while not stop.is_set():
			diff.fetch("a/b", i, "s")     # a NEW key every time: the cache changes SIZE under retry()
			i += 1
	t = th.Thread(target=churn, daemon=True)
	t.start()
	try:
		for _ in range(60):
			try:
				diff.retry()
			except RuntimeError as e:      # "dictionary changed size during iteration"
				boom.append(e)
				break
	finally:
		stop.set()
		t.join(2)
		sys.setswitchinterval(old_interval)
	assert not boom, boom


def ticks(st, monkeypatch, tick, stop_after=3):
	"""Run loop() for `stop_after` waits, then break out. ponytail: SystemExit, so the guard under
	test — which catches Exception — cannot swallow the thing ending the test."""
	monkeypatch.setattr(State, "tick", tick)
	seen = []
	def wait(t):
		seen.append(1)
		if len(seen) >= stop_after:
			raise SystemExit
		return False
	monkeypatch.setattr(st.wake, "wait", wait)
	with pytest.raises(SystemExit):
		st.loop()
	return len(seen)


def test_a_tick_that_raises_is_reported_and_retried(monkeypatch):
	"""The refresh thread must outlive anything one tick can throw.

	ponytail: it did not. One unreadable line in the review log unwound out of the thread's run() and
	nothing restarts it — fetching stayed True, so the dashboard reported a refresh in progress
	forever, and f only sets an event nobody was waiting on any more.
	"""
	st = State(0)
	st.fetching = True
	tried = []
	def boom(self, t0):
		tried.append(t0)
		raise RuntimeError("gh: could not resolve host\nsecond line")
	ticks(st, monkeypatch, boom)
	assert len(tried) > 1                    # it kept trying
	assert st.fetching is False              # and stopped claiming to be mid-refresh
	assert st.error == "second line"         # ponytail: last line, like every other error surface here


def test_a_first_tick_that_raises_still_schedules_the_next(monkeypatch):
	"""ponytail: fetched_at is None until a tick lands, and `fetched_at + interval` raised TypeError
	on the very line scheduling the retry — the second way this thread could die, and the one a guard
	around the work alone would not have caught."""
	st = State(60)
	assert st.fetched_at is None
	ticks(st, monkeypatch, lambda self, t0: (_ for _ in ()).throw(ValueError("nope")))
	assert st.error == "nope"


def test_a_tick_that_lands_clears_the_last_failure(monkeypatch):
	st = State(0)
	st.error = "gh: could not resolve host"
	one_loop(st, monkeypatch, [("MINE", [], None)])
	assert st.error == ""


@pytest.mark.parametrize("api_ok", [True, False])
def test_fetch_survives_a_log_line_it_cannot_read(monkeypatch, api_ok):
	"""The end-to-end shape of the freeze: fetch() reads the log with no handler of its own.

	ponytail: BOTH of fetch()'s paths. The early return for a failed API call appends REVIEWED too, so
	testing only the happy one would leave the branch a broken log is most likely to be taken WITH —
	an outage and a half-written append arrive together — completely uncovered.
	ponytail: this pinned github.subprocess before drop-gh, which patched a seam fetch() no longer has.
	It still passed, because no token means the API raises anyway; a test that cannot fail is worse
	than no test, so it now stubs what fetch() actually calls.
	"""
	with open(log.LOG, "w") as f:
		f.write('{"at":"2020-01-0\n')  # a torn append
	if api_ok:
		monkeypatch.setattr(github.urllib.request, "urlopen",
		                    fake_http(lambda url, body: gql_nodes([], [], [])))
		monkeypatch.setattr(github, "_me", "tester")
	else:
		monkeypatch.setattr(github, "gql",
		                    lambda *a, **kw: (_ for _ in ()).throw(github.Error("github unreachable")))
	assert github.fetch()[-1] == ("REVIEWED", [], None)


def test_a_failed_tick_waits_a_full_interval_before_retrying(monkeypatch):
	"""ponytail: fetched_at is the last SUCCESSFUL fetch, so once a tick failed the deadline it implies
	is already in the past — the loop fell straight out of the wait and retried every second, hammering
	gh for as long as the outage lasted. A failed attempt backs off from ITSELF."""
	st = State(300)
	st.fetched_at = time.time() - 600  # a good fetch, long enough ago that its deadline has passed
	tries, waits = [], []
	def boom(self, t0):
		tries.append(t0)
		raise RuntimeError("gh exploded")
	monkeypatch.setattr(State, "tick", boom)
	def wait(t):
		waits.append(t)
		if len(waits) >= 4:
			raise SystemExit  # four slices in and still waiting: backing off, not hammering
		return False
	monkeypatch.setattr(st.wake, "wait", wait)
	with pytest.raises(SystemExit):
		st.loop()
	assert len(tries) == 1 and st.error == "gh exploded"


def _quiet_tick(monkeypatch):
	monkeypatch.setattr(state.github, "fetch", lambda: [])
	monkeypatch.setattr(state.log, "mark_rereviews", lambda data: [])
	monkeypatch.setattr(state.update, "update_available", lambda: "")
	monkeypatch.setattr(state, "refresh_mirrors", lambda: None)


def test_the_tick_sweeps_drafts_after_pulling(monkeypatch):
	"""The pull is what brings a teammate's pooled drafts down, so the sweep is started right after it."""
	from dashy.core import memory
	order, done = [], threading.Event()
	monkeypatch.setattr(state.team, "pull", lambda: order.append("pull"))
	monkeypatch.setattr(memory, "sweep", lambda model: (order.append(f"sweep:{model}"), done.set()) and [])
	_quiet_tick(monkeypatch)
	st = state.State(60, "opus")
	st.tick(time.time())
	assert done.wait(5) and order == ["pull", "sweep:opus"]


def test_facts_arriving_with_the_pull_are_counted_per_team(monkeypatch, tmp_path):
	"""team.pull() fast-forwards silently and the mirror is overwritten in place, so a fact arriving —
	the moment to read it — passed with nothing on screen saying one had."""
	from dashy.core import memory
	from conftest import a_team
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	(mem / "a__b.md").write_text("- uses tabs\n")
	_quiet_tick(monkeypatch)
	monkeypatch.setattr(state.team, "pull",
	                    lambda: (mem / "a__b.md").write_text("- uses tabs\n- and no DDL here\n- nor there\n"))
	st = state.State(60, "opus")

	def one_tick():
		# ponytail: cleared before each. start_sweep() sets it and the thread clears it, so whether the
		# NEXT tick counts anything at all came down to which won — and a test whose subject is skipped
		# by a race passes with the code removed, which is what this one did.
		st.sweeping.clear()
		st.tick(time.time())

	one_tick()
	assert st.arrived == {"org-t": 2}
	one_tick()
	assert st.arrived == {"org-t": 2}  # a second pull that brought nothing does not inflate it
	# ponytail: a teammate rewriting the brief is not the team learning thirty things. project.md and
	# agents.md are prose people wrote and change for reasons that have nothing to do with the reviews.
	monkeypatch.setattr(state.team, "pull", lambda: [
		(mem / "project.md").write_text("# What we are building\n\nA longer brief than before.\n"),
		(mem / "agents.md").write_text("# For agent sessions\n\nFile what you work out.\n")])
	one_tick()
	assert st.arrived == {"org-t": 2}


def test_a_promotion_landing_mid_sweep_is_not_reported_as_a_teammates(monkeypatch, tmp_path):
	"""The sweep writes YOUR promoted facts into the team's files. Counted blind, one of those inside
	the pull window reads as something a colleague sent you, which is the one thing the badge is for."""
	from conftest import a_team
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	(mem / "a__b.md").write_text("- uses tabs\n")
	_quiet_tick(monkeypatch)
	monkeypatch.setattr(state.team, "pull",
	                    lambda: (mem / "a__b.md").write_text("- uses tabs\n- one this machine promoted\n"))
	st = state.State(60, "opus")
	st.sweeping.set()  # an earlier tick's sweep is still running
	st.tick(time.time())
	assert st.arrived == {}


def test_a_slow_sweep_never_delays_the_pr_list(monkeypatch):
	"""It ran on the refresh thread, after the pull and BEFORE github.fetch, with a 300s model timeout —
	so the first sweep after an upgrade held the whole list for as long as the model took. Catching the
	exception was never the risk; the wait was."""
	from dashy.core import memory
	started, release = threading.Event(), threading.Event()
	def slow(model):
		started.set()
		release.wait(5)
		return []
	monkeypatch.setattr(state.team, "pull", lambda: None)
	monkeypatch.setattr(memory, "sweep", slow)
	_quiet_tick(monkeypatch)
	st = state.State(60, "opus")
	st.tick(time.time())
	assert started.wait(5)                    # it is running
	assert st.fetched_at is not None          # and the list landed anyway
	assert st.fetching is False
	release.set()


def test_only_one_sweep_runs_at_a_time(monkeypatch):
	"""A slow sweep must not have a second started on top of it: both write the same pool files and
	both push the same checkout."""
	from dashy.core import memory
	calls, release = [], threading.Event()
	def slow(model):
		calls.append(model)
		release.wait(5)
		return []
	monkeypatch.setattr(state.team, "pull", lambda: None)
	monkeypatch.setattr(memory, "sweep", slow)
	_quiet_tick(monkeypatch)
	st = state.State(60, "opus")
	st.tick(time.time())
	st.tick(time.time())
	st.tick(time.time())
	assert len(calls) == 1
	release.set()


def test_a_sweep_that_throws_never_stops_the_refresh(monkeypatch):
	"""It calls a model. A refresh that dies because of it would take the PR list down with it."""
	from dashy.core import memory
	rang = threading.Event()
	monkeypatch.setattr(state.team, "pull", lambda: None)
	def boom(model):
		rang.set()
		raise ZeroDivisionError("boom")
	monkeypatch.setattr(memory, "sweep", boom)
	_quiet_tick(monkeypatch)
	st = state.State(60, "opus")
	st.tick(time.time())
	assert rang.wait(5)
	assert st.fetched_at is not None and st.error == ""
	# ponytail: the flag clears in a `finally` AFTER the exception, so checking it the instant the body
	# ran is a race with the thread's own cleanup — wait for it, with a deadline.
	deadline = time.time() + 5
	while st.sweeping.is_set() and time.time() < deadline:
		time.sleep(0.01)
	assert not st.sweeping.is_set(), "a sweep that threw left the guard set, so none can run again"
