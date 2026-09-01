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
	assert len(secs[3][1]) == 2  # seeded history
	assert len(github.fetch()[1][1]) == 4 and len(github.fetch()[1][1]) == 5  # a new PR shows up on the 3rd refresh (+1 re-requested)
	assert log.mark_rereviews(github.fetch()) == ["https://github.com/acme/infra/pull/44"]
	p = secs[1][1][0]
	statuses = [review_mod.review(p, "opus") for _ in range(4)]
	assert statuses[:3] == ["✓ approved", "✗ changes requested", "~ commented"] and statuses[3].startswith("error:")
	assert len(log.reviewed()) == 5
	st = State(0)
	st.sections = github.fetch()
	ui.C = lambda n: 0
	ui.draw(FakeScr(), st, 0)  # renders without raising
