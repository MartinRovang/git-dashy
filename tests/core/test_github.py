import json
import subprocess
import time

import pytest

from dashy.core import github, log

from conftest import PR, Result


def test_fetch_dedups_sorts_and_appends_reviewed(monkeypatch):
	a = dict(PR, url="a", updatedAt="2020-01-01T00:00:00Z")
	b = dict(PR, url="b", updatedAt="2021-01-01T00:00:00Z")
	per_flag = {"--author=@me": [a, b], "--review-requested=@me": [a], "--assignee=@me": []}
	def fake_run(cmd, **kw):
		if cmd[1] == "api":
			return Result(json.dumps({"data": {"search": {"nodes": [
				{"url": "b", "reviewDecision": "APPROVED"},
				{"url": "a", "reviewDecision": "CHANGES_REQUESTED", "reviewRequests": {"totalCount": 1}}]}}}))
		return Result(json.dumps(per_flag[cmd[4]]))
	monkeypatch.setattr(subprocess, "run", fake_run)
	secs = github.fetch()
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]
	assert [p["url"] for p in secs[0][1]] == ["b", "a"]  # newest first
	assert [p["status"] for p in secs[0][1]] == ["✓ approved", "↻ re-review requested"]  # own PRs carry github's review decision
	assert github.own_status({"reviewDecision": "CHANGES_REQUESTED", "reviewRequests": {"totalCount": 0}}) == "✗ changes requested"
	assert github.own_status({"reviewDecision": "REVIEW_REQUIRED", "reviewRequests": {"totalCount": 2}}) == "· awaiting review"
	assert github.own_status({}) == ""
	assert secs[1][1] == []  # a already shown under MINE
	assert secs[3] == ("REVIEWED", [], None)


def test_fetch_reports_errors_per_section(monkeypatch):
	def fake_run(cmd, **kw):
		if cmd[4] == "--author=@me":
			raise subprocess.CalledProcessError(1, cmd, stderr="gh: not logged in\n")
		return Result("[]")
	monkeypatch.setattr(subprocess, "run", fake_run)
	secs = github.fetch()
	assert secs[0] == ("MINE", None, "gh: not logged in")
	assert secs[1] == ("REVIEW REQUESTED", [], None)


def test_fetch_handles_bad_json(monkeypatch):
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: Result("not json"))
	name, ps, err = github.fetch()[0]
	assert ps is None and err


# ---- review ----


def test_detail_normalises_checks_and_never_raises(monkeypatch):
	import json as _json
	payload = {"headRefName": "feat/kb", "additions": 412, "deletions": 96, "changedFiles": 14,
	           "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"},
	                                 {"name": "lint", "conclusion": "FAILURE"},
	                                 {"context": "e2e", "state": "PENDING"},
	                                 {"name": "odd", "conclusion": "WHAT"},
	                                 {"conclusion": "SUCCESS"}]}
	monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result(_json.dumps(payload)))
	d = github.detail("a/b", 7)
	assert d["branch"] == "feat/kb" and (d["add"], d["del"], d["files"]) == (412, 96, 14)
	assert d["checks"] == [{"name": "ci", "state": "ok"}, {"name": "lint", "state": "fail"},
	                       {"name": "e2e", "state": "run"}, {"name": "odd", "state": "run"}]
	def boom(*a, **k):
		raise subprocess.TimeoutExpired("gh", 1)
	monkeypatch.setattr(subprocess, "run", boom)
	assert github.detail("a/b", 7) == {}  # a pane is decoration; the row is still right


def test_want_detail_fetches_once_off_the_draw_thread(monkeypatch):
	from dashy.core.state import State
	calls = []
	monkeypatch.setattr(github, "detail", lambda repo, n: calls.append((repo, n)) or {"branch": "b"})
	st = State(60)
	assert st.want_detail(None) is None
	# first ask starts a fetch and returns nothing yet
	assert st.want_detail(dict(PR)) is None
	for _ in range(200):
		if dict(PR)["url"] in st.details:
			break
		time.sleep(0.005)
	assert st.want_detail(dict(PR)) == {"branch": "b"}
	st.want_detail(dict(PR))
	assert len(calls) == 1  # cached, not refetched on every draw
