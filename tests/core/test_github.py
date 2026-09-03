import json
import subprocess

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


def test_copy_uses_first_clipboard_tool_on_path(monkeypatch):
	from dashy.core import github
	ran = []
	monkeypatch.setattr(github.shutil, "which", lambda c: c == "xclip")
	monkeypatch.setattr(github.subprocess, "run", lambda cmd, **kw: ran.append((cmd, kw["input"])))
	assert github.copy("https://x/pr/1") == "xclip"
	assert ran == [(["xclip", "-selection", "clipboard"], "https://x/pr/1")]
	monkeypatch.setattr(github.shutil, "which", lambda c: None)
	assert github.copy("u") is None and len(ran) == 1
