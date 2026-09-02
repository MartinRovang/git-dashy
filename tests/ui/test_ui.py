import time

import pytest

from dashy import config
from dashy.core import log
from dashy.ui import screen as ui
from dashy.core.review import review
from dashy.core.state import State

from conftest import PR, FakeScr, claude_out


def test_draw_renders_sections_status_and_selection(screen):
	screen.w = 210  # ponytail: the stats strip drops trailing items on narrower screens
	st = State(60)
	st.sections = [("MINE", [dict(PR, url="m")], None),
	               ("REVIEW REQUESTED", [dict(PR, url="r", number=8, title="Needs eyes", isDraft=True)], None),
	               ("ASSIGNED", None, "boom"), ("REVIEWED", [], None)]
	st.fetched_at, st.drafts = __import__("time").time(), True
	st.reviews["r"] = "✓ approved"
	sel, cur = ui.draw(screen, st, 1)
	out = screen.text()
	assert sel == 1 and cur["url"] == "r"
	assert "2 PRs" in out and "MINE (1)" in out and "ASSIGNED (!)" in out and "boom" in out
	assert "▸" in out and "b#8" in out and "draft" in out and "✓ approved" in out
	assert "gitdashy v" + ui.VERSION in out and "next refresh" in out
	assert "Reviewer   Model " + st.model in out and "Depth " + config.DEPTH in out and "List   Summaries all" in out and "Drafts shown" in out
	assert "Session  ✓ 1   ✗ 0   ~ 0   ! 0" in out
	assert screen.line(2).startswith("▀▀▀") and screen.line(3).strip() == "" and "MINE (1)" in screen.line(4)


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


def test_dropdown_lists_options_under_the_setting_and_picks_on_enter(screen, monkeypatch):
	screen.w = 190
	monkeypatch.setattr(config, "DEPTH", "adaptive")
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	seen = []
	def getch():
		seen.append(screen.text())
		return [ord("j"), ord("d"), 10][len(seen) - 1]  # down, same key = down again, enter
	screen.getch, screen.timeout = getch, lambda t: None
	assert ui.dropdown(screen, st, 0, "d") is True and config.DEPTH == "medium"
	first = seen[0]
	assert "▸ adaptive" in first and "  low" in first and "Depth:  j/k or d move" in first
	y, x = ui.ANCHORS["d"]
	assert screen.line(y).index("Depth ") == x and "╭" in first.splitlines()[y + 1][x:x + 2]
	assert "▸ medium" in seen[2] and "▸ adaptive" not in seen[2]


def test_dropdown_escape_keeps_and_unknown_model_is_listed(screen):
	st = State(60, model="custom-model")
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	screen.getch, screen.timeout = _keys(ord("j"), 27), lambda t: None
	assert ui.dropdown(screen, st, 0, "m") is False and st.model == "custom-model"
	assert "▸ custom-model" in screen.text() or "custom-model" in screen.text()


def test_dropdown_shows_effort_default_and_history_all(screen):
	st = State(60)
	st.sections, st.fetched_at, st.window = [("MINE", [], None)], time.time(), None
	screen.getch, screen.timeout = _keys(27), lambda t: None
	ui.dropdown(screen, st, 0, "t")
	assert "▸ all" in screen.text() and "  4h" in screen.text()
	screen.getch = _keys(27)
	ui.dropdown(screen, st, 0, "e")
	assert "default" in screen.text() and "xhigh" in screen.text()


def test_strip_shows_refreshing_while_fetch_in_flight(screen):
	screen.w = 180  # wide enough for the whole strip incl. the version
	st = State(60)
	st.sections, st.fetched_at, st.fetching = [("MINE", [], None)], time.time(), True
	ui.draw(screen, st, 0)
	assert "refreshing" in screen.text() and "next refresh" not in screen.text()


def test_strip_collapses_groups_to_chips_on_narrow_screens(screen):
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	def row1(w):
		screen.w = w
		ui.draw(screen, st, 0)
		return screen.line(1)
	out = row1(200)
	assert out.index("Session") + len("Session") == screen.line(0).index("v" + ui.VERSION) + len("v" + ui.VERSION) + 1  # chip edge incl. its padding
	assert out.rstrip().endswith("History 4h") and out.index("Reviewer") < out.index("List") and "▾" not in out
	out = row1(150)
	assert "Effort medium" in out and out.rstrip().endswith("List ▾")  # List folds first
	out = row1(100)
	assert "Reviewer ▾" in out and "List ▾" in out and "Model" not in out
	assert ui.ANCHORS["m"] == ui.ANCHORS["R"] and ui.ANCHORS["t"] == ui.ANCHORS["L"]  # folded keys hang from the chip
	out = row1(60)
	assert "Session" in out and "Reviewer" not in out and "List" not in out


def test_group_menu_lists_settings_and_opens_one(screen, monkeypatch):
	screen.w = 100
	monkeypatch.setattr(config, "DEPTH", "adaptive")
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	seen = []
	def getch():
		seen.append(screen.text())
		return [ord("j"), 10, ord("j"), 10, 27][len(seen) - 1]  # to Depth, open it, pick "low", back in the group, close
	screen.getch, screen.timeout = getch, lambda t: None
	ui.group_menu(screen, st, 0, "R")
	assert config.DEPTH == "low"
	assert "▸ Model    opus" in seen[0] and "Depth    adaptive" in seen[0] and "Reviewer:  j/k move" in seen[0]
	assert "▸ adaptive" in seen[2] and "Depth:  j/k or d move" in seen[2]
	assert "Depth    low" in seen[4] and "Reviewer:  j/k move" in seen[4]  # back in the group with the new value


def test_group_menu_toggles_drafts(screen):
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	screen.getch, screen.timeout = _keys(ord("j"), 10, 27), lambda t: None
	ui.group_menu(screen, st, 0, "L")
	assert st.drafts is True


def test_strip_shows_update_and_auto_badges(screen):
	screen.w = 180
	st = State(60)
	st.sections, st.fetched_at, st.update = [("MINE", [], None)], time.time(), "9.9.9"
	st.set_auto(True)
	ui.draw(screen, st, 0)
	out = screen.line(0)
	assert "update to v9.9.9 · u" in out and "AUTO" in out and "0 agents running" in out
	assert out.index("PRs") < out.index("agents running") < out.index("updated") < out.index("next refresh") < out.index("AUTO")


def test_hints_show_each_settings_key(screen):
	screen.w = 190
	st = State(60)
	st.sections, st.fetched_at, st.hints = [("MINE", [], None)], time.time(), True
	ui.draw(screen, st, 0)
	out = screen.text()
	assert "m Model " + st.model in out and "d Depth" in out and "e Effort" in out
	assert "s Summaries" in out and "D Drafts" in out and "t History" in out and "i next refresh" in out and "r updated" in out
	st.hints = False
	ui.draw(screen, st, 0)
	assert "m Model" not in screen.text() and "i next refresh" not in screen.text()


def test_dream_screen_shows_animation_then_summary_and_writes_on_y(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	ui.memory.append("a/b", "x\nx")
	def slow_dream(model):
		time.sleep(0.3)
		return "merged dupes", {"a__b.md": "- x"}
	monkeypatch.setattr(ui.memory, "dream", slow_dream)
	seen = []
	def getch():
		seen.append(screen.text())
		return ord("y")
	screen.getch, screen.timeout = getch, lambda t: None
	ui.dream_screen(screen, st, 0)
	assert any("dreaming" in s and "tidying the memories" in s for s in seen)
	assert "dream over" in seen[-1] and "merged dupes" in seen[-1] and "a/b" in seen[-1] and "2 → 1" in seen[-1]
	assert open(ui.memory.path("a/b")).read() == "- x\n"


def test_dream_screen_discard_and_error(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	ui.memory.append("a/b", "x")
	monkeypatch.setattr(ui.memory, "dream", lambda m: ("s", {"a__b.md": "- changed"}))
	screen.getch, screen.timeout = _keys(ord("j"), ui.curses.KEY_DOWN, ord(" "), 27), lambda t: None  # stray keys ignored
	ui.dream_screen(screen, st, 0)
	assert open(ui.memory.path("a/b")).read() == "- x\n"
	def boom(m):
		raise ValueError("no memory to dream about")
	monkeypatch.setattr(ui.memory, "dream", boom)
	screen.getch = _keys(ord(" "), ord(" "))
	ui.dream_screen(screen, st, 0)
	assert "dream failed" in screen.text() and "no memory to dream about" in screen.text()


def test_dream_detail_diffs_changed_files_only():
	out = ui.dream_detail("merged\ndupes", {"a__b.md": "- x\n- x\n", "general.md": "- g\n"}, {"a__b.md": "- x", "general.md": "- g\n"})
	assert out.startswith("merged\ndupes\n") and "--- a/b" in out and "-- x" in out and "general" not in out
