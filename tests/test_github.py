import json
import subprocess

import pytest

from dashy import github, log

from conftest import PR, Result


def test_fetch_dedups_sorts_and_appends_reviewed(monkeypatch):
	a = dict(PR, url="a", updatedAt="2020-01-01T00:00:00Z")
	b = dict(PR, url="b", updatedAt="2021-01-01T00:00:00Z")
	per_flag = {"--author=@me": [a, b], "--review-requested=@me": [a], "--assignee=@me": []}
	def fake_run(cmd, **kw):
		return Result(json.dumps(per_flag[cmd[4]]))
	monkeypatch.setattr(subprocess, "run", fake_run)
	secs = github.fetch()
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]
	assert [p["url"] for p in secs[0][1]] == ["b", "a"]  # newest first
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
