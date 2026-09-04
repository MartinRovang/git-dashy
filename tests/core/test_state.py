import os

import pytest

from dashy import config
from dashy.core import github, log, review as review_mod, state, team, update
from dashy.core.state import State

from conftest import PR


def test_loop_forgets_stale_verdict_but_not_in_flight(monkeypatch):
	log.log_review(dict(PR, url="a"), "opus", {"verdict": "approve", "body": ""}, at="2020-01-01T00:00:00+00:00")
	log.log_review(dict(PR, url="b"), "opus", {"verdict": "approve", "body": ""}, at="2020-01-01T00:00:00+00:00")
	st = State(0)
	st.reviews = {"a": "✓ approved", "b": "reviewing..."}
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
	waits = []
	def wait(t):
		waits.append(t)
		st.interval = 0  # shrink mid-wait: loop must notice and refetch instead of sleeping 600 slices
		if len(waits) > 1:
			raise SystemExit
		return False
	monkeypatch.setattr(st.wake, "wait", wait)
	with pytest.raises(SystemExit):
		st.loop()
	assert waits == [1, 1]


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
	assert all(f in github.FIELDS for f in ("number", "title", "repository", "author"))
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
