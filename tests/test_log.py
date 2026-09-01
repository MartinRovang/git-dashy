import json
import subprocess

import pytest

from dashy import log
from dashy.review import review
from dashy.log import detail, log_review, mark_rereviews, reviewed

from conftest import PR, Result, claude_out


def test_reviewed_empty_when_no_log():
	assert reviewed() == []


def test_reviewed_newest_first_and_detail(monkeypatch):
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: claude_out(verdict="approve", summary="adds x", body="lgtm"))
	review(dict(PR, url="first"), "opus")
	review(dict(PR, url="second"), "opus")
	got = reviewed()
	assert [p["url"] for p in got] == ["second", "first"]
	assert got[0]["status"] == "✓ approved" and got[0]["updatedAt"] == got[0]["review"]["at"]
	d = detail(got[0]["review"])
	assert "a/b#7  T" in d and "adds x" in d and "lgtm" in d and "opus" in d
	assert "q close" in d and "author   me" in d and "+00:00" not in d


def test_reviewed_tolerates_sparse_entries():
	with open(log.LOG, "w") as f:
		f.write(json.dumps({"at": "2020-01-01T00:00:00+00:00", "model": "opus", "verdict": "comment",
		                    "summary": "", "body": "", "pr": {"repository": {"nameWithOwner": "a/b"}, "number": 1, "url": "u"}}) + "\n")
	p = reviewed()[0]
	assert p["title"] == "?" and p["isDraft"] is False and "author" not in p


def test_mark_rereviews_flags_updated_logged_prs_only():
	log_review(dict(PR, url="old"), "opus", {"verdict": "approve", "body": "ok"}, at="2020-01-01T00:00:00+00:00")
	log_review(dict(PR, url="same"), "opus", {"verdict": "comment", "body": "hm"}, at="2020-01-01T00:00:00+00:00")
	old, same, fresh = dict(PR, url="old", updatedAt="2021-01-01T00:00:00Z"), dict(PR, url="same"), dict(PR, url="fresh")
	secs = [("REVIEW REQUESTED", [old, same, fresh], None), ("REVIEWED", reviewed(), None)]
	assert mark_rereviews(secs) == ["old"]
	assert old["prev"] == "↻ re-review · was ✓ approved" and "prev" not in same and "prev" not in fresh
