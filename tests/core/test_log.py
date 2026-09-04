import json
import subprocess

import pytest

from dashy.core import log
from dashy.core.review import review
from dashy.core.log import detail, log_review, mark_rereviews, reviewed

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


def test_findings_are_kept_but_never_trusted():
	from dashy.core.log import findings
	good = {"findings": [{"kind": "Blocking", "loc": "keymap.ts:88", "text": "duplicate  binding\nnot detected"},
	                     {"kind": "nit", "text": "no loc is fine"}]}
	assert findings(good) == [
		{"kind": "blocking", "loc": "keymap.ts:88", "text": "duplicate binding not detected"},
		{"kind": "nit", "loc": "", "text": "no loc is fine"}]
	assert findings({}) == []  # a review from before the field existed
	assert findings({"findings": None}) == []
	assert findings({"findings": ["a string", {"kind": "bogus", "text": "x"}, {"kind": "nit"}, 7]}) == []
	assert len(findings({"findings": [{"kind": "nit", "text": f"f{i}"} for i in range(40)]})) == 12


def test_mark_rereviews_prefers_the_head_commit_over_the_timestamp():
	log_review(dict(PR, url="c", head="aaa"), "opus", {"verdict": "approve", "body": "ok"}, at="2020-01-01T00:00:00+00:00")
	log_review(dict(PR, url="p", head="aaa"), "opus", {"verdict": "approve", "body": "ok"}, at="2020-01-01T00:00:00+00:00")
	commented = dict(PR, url="c", head="aaa", updatedAt="2021-01-01T00:00:00Z")  # newer, but the same commit
	pushed = dict(PR, url="p", head="bbb", updatedAt="2020-01-01T00:00:00Z")  # same instant, different commit
	secs = [("REVIEW REQUESTED", [commented, pushed], None), ("REVIEWED", reviewed(), None)]
	assert mark_rereviews(secs) == ["p"] and "prev" not in commented


def test_tag_carries_cost_and_duration():
	assert log.tag({"depth": "high", "effort": "max", "cost": 0.4171, "ms": 184_000}) == "high/max $0.42 3m"
	assert log.tag({"depth": "low", "ms": 9_400}) == "low 9s"
	assert log.tag({"depth": "low", "cost": None, "ms": None}) == "low"
	assert log.tag({}) == ""
