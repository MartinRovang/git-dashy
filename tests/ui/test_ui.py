import time

import pytest

from dashy import config
from dashy.core import log
from dashy.ui import screen as ui
from dashy.core.review import review
from dashy.core.state import State

from conftest import PR, FakeScr, claude_out


def test_draw_renders_sections_status_and_selection(screen):
	screen.w = 170  # ponytail: the stats strip drops trailing items on narrower screens
	st = State(60)
	st.sections = [("MINE", [dict(PR, url="m")], None),
	               ("REVIEW REQUESTED", [dict(PR, url="r", number=8, title="Needs eyes", isDraft=True)], None),
	               ("ASSIGNED", None, "boom"), ("REVIEWED", [], None)]
	st.fetched_at = __import__("time").time()
	st.reviews["r"] = "✓ approved"
	sel, cur = ui.draw(screen, st, 1)
	out = screen.text()
	assert sel == 1 and cur["url"] == "r"
	assert "PRs 2" in out and "MINE (1)" in out and "ASSIGNED (!)" in out and "boom" in out
	assert "▸" in out and "b#8" in out and "draft" in out and "✓ approved" in out
	assert "1 approved" in out and "model: " + st.model in out
	assert "next refresh" in out


def test_draw_clamps_selection_and_handles_empty(screen):
	st = State(60)
	sel, cur = ui.draw(screen, st, 5)
	assert sel == 0 and cur is None and "fetching" in screen.text()
	st.sections = [("MINE", [dict(PR)], None)]
	sel, cur = ui.draw(screen, st, 99)
	assert sel == 0 and cur["url"] == "u"
	sel, cur = ui.draw(screen, st, -3)
	assert sel == 0


def test_draw_prompt_replaces_footer(screen):
	st = State(60)
	ui.draw(screen, st, 0, prompt=" sure? [y/n]")
	assert screen.line(screen.h - 1).strip() == "sure? [y/n]"


def test_draw_truncates_long_title_on_narrow_screen(screen):
	screen.w = 40
	st = State(60)
	st.sections = [("MINE", [dict(PR, title="x" * 200)], None)]
	ui.draw(screen, st, 0)  # must not raise
	assert "…" in screen.text()


def test_draw_reviewed_rows_use_logged_status(screen, monkeypatch):
	monkeypatch.setattr(__import__('subprocess'), "run", lambda cmd, **kw: claude_out(verdict="comment", body="b"))
	review(dict(PR), "opus")
	st = State(60)
	st.sections = [("REVIEWED", log.reviewed(), None)]
	ui.draw(screen, st, 0)
	assert "~ commented" in screen.text()


def test_draw_summary_under_reviewed_open_pr(screen, monkeypatch):
	monkeypatch.setattr(__import__('subprocess'), "run", lambda cmd, **kw: claude_out(verdict="approve", summary="Adds a retry loop " * 20, body="b"))
	review(dict(PR), "opus")
	st = State(60)
	st.sections = [("REVIEW REQUESTED", [dict(PR)], None), ("REVIEWED", log.reviewed(), None)]
	ui.draw(screen, st, 0)
	lines = [l for l in screen.text().splitlines() if "↳" in l]
	assert len(lines) == 2 and all(l.endswith("…") and len(l.split("↳ ")[1]) <= 70 for l in lines)


# ---- demo ----


def _keys(*ks):
	it = iter(ks)
	return lambda: next(it)


def test_update_screen_declined(screen, monkeypatch, st):
	st.update = "9.9.9"
	screen.getch, screen.timeout = _keys(ord("n")), lambda t: None
	monkeypatch.setattr(ui.update, "apply_update", lambda v: pytest.fail("must not update on n"))
	assert ui.update_screen(screen, st, 0) is False
	out = screen.text()
	assert "update available" in out and "9.9.9" in out and "[y] update now" in out


def test_update_screen_accepts_and_reports_failure(screen, monkeypatch, st):
	st.update = "9.9.9"
	screen.getch, screen.timeout = _keys(ord("y"), ord(" ")), lambda t: None
	monkeypatch.setattr(ui.update, "apply_update", lambda v: "no such tag v9.9.9")
	assert ui.update_screen(screen, st, 0) is False
	assert "failed: no such tag v9.9.9" in screen.text()


def test_d_and_e_cycle_depth_and_effort(monkeypatch):
	from dashy.ui.screen import cycle_through
	monkeypatch.setattr(config, "DEPTH", "adaptive")
	monkeypatch.setattr(config, "EFFORT", "")
	assert cycle_through(config.DEPTHS, config.DEPTH) == "low"
	assert cycle_through(config.EFFORTS, config.EFFORT) == "low"
	assert cycle_through(config.EFFORTS, "max") == ""
