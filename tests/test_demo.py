import os
import subprocess
import time

import pytest

from dashy import demo
from dashy.core import github, log, review as review_mod
from dashy.ui import screen as ui
from dashy.core.state import State

from conftest import FakeScr


def test_demo_is_self_contained(monkeypatch, tmp_path):
	monkeypatch.setenv("TMPDIR", str(tmp_path))
	monkeypatch.setattr(time, "sleep", lambda s: None)
	monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("demo must not shell out")))
	demo.install()
	assert log.LOG.startswith(str(tmp_path))
	secs = github.fetch()
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]
	assert len(secs[3][1]) == 3  # seeded history, #180 twice
	assert [p["more"] for k, p in ui.rows(secs) if k == "pr" and p["section"] == "REVIEWED"] == [0, 1]  # #44, then #180 with one folded
	assert len(github.fetch()[1][1]) == 4 and len(github.fetch()[1][1]) == 5  # a new PR shows up on the 3rd refresh (+1 re-requested)
	assert log.mark_rereviews(github.fetch()) == ["https://github.com/acme/infra/pull/44"]
	p = secs[1][1][0]
	statuses = [review_mod.review(p, "opus") for _ in range(4)]
	assert statuses[:3] == ["✓ approved", "✗ changes requested", "~ commented"] and statuses[3].startswith("error:")
	assert len(log.reviewed()) == 6
	st = State(0)
	st.sections = github.fetch()
	ui.C = lambda n: 0
	ui.draw(FakeScr(), st, 0)  # renders without raising


def test_demo_swaps_every_call_out_including_the_pre_reviewer(monkeypatch, tmp_path):
	"""`p` on a demo row called review.self_review, which demo.install() did not swap.

	With claude on PATH that spawned a real `claude -p` against acme/api#101, which then shells out to
	gh — against a README that promises no gh and no claude. The other test asserts no subprocess.run;
	this one asserts the attr itself is not the real function, which is what actually went wrong.
	"""
	monkeypatch.setenv("TMPDIR", str(tmp_path))
	real_review, real_self = review_mod.review, review_mod.self_review
	demo.install()
	try:
		assert review_mod.review is not real_review
		assert review_mod.self_review is not real_self, "demo must swap the pre-reviewer too"
		status, dest = review_mod.self_review({"repository": {"nameWithOwner": "acme/api"},
		                                       "number": 101, "url": "u"}, "opus")
		assert "not posted" in status and os.path.isfile(dest)
		assert str(tmp_path) in dest  # and it wrote into the demo's own temp dir, not ~/.prs_reviews
	finally:
		review_mod.review, review_mod.self_review = real_review, real_self
