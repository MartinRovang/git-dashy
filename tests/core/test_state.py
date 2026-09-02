import pytest

from dashy import config
from dashy.core import github, log, review as review_mod, state, update
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
