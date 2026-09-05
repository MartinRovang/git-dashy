import os
import re
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
	st.sections = [("MINE", [dict(PR, url="m", checks="✗")], None),
	               ("REVIEW REQUESTED", [dict(PR, url="r", number=8, title="Needs eyes", isDraft=True)], None),
	               ("ASSIGNED", None, "boom"), ("REVIEWED", [], None)]
	st.fetched_at, st.drafts = __import__("time").time(), True
	st.reviews["r"] = "✓ approved"
	sel, cur = ui.draw(screen, st, 1)
	out = screen.text()
	assert sel == 1 and cur["url"] == "r"
	assert "2 PRs" in out and "MINE" in out and "1 open" in out and "assigned" in out and "boom" in out
	assert "AGE" in out and "REPO" in out and "TITLE" in out and "STATE" in out  # the column header
	assert "▌" in out and "#8" in out and "draft" in out and "✓ approved" in out
	# ponytail: main's CI chip, kept — it landed in #9 while this grid was being rebuilt. Found by
	# searching the rendered rows rather than by line number, which the column header shifted.
	rows = [screen.line(y) for y in range(4, screen.h - 2)]
	assert any("ci✗" in r for r in rows), "a row with failing checks shows the chip"
	assert any(r.strip() and "ci" not in r for r in rows), "a row without checks does not"
	assert "gitdashy v" + ui.VERSION in out and "next refresh" in out
	assert "Agent   Model " + st.model in out and "Depth " + config.DEPTH in out and "View   Summaries all" in out and "Drafts shown" in out
	assert "Session  ✓ 1   ✗ 0   ~ 0   ! 0" in out
	assert screen.line(2).startswith("▀▀▀") and screen.line(3).strip() in ("", "│")
	assert "AGE" in screen.line(4) and "MINE" in screen.line(5)


def test_draw_clamps_selection_and_handles_empty(screen):
	st = State(60)
	sel, cur = ui.draw(screen, st, 5)
	assert sel == 0 and cur is None and "fetching" in screen.text()
	st.sections = [("MINE", [dict(PR)], None)]
	sel, cur = ui.draw(screen, st, 99)
	assert sel == 0 and cur["url"] == "u"
	sel, cur = ui.draw(screen, st, -3)
	assert sel == 0


def test_draw_survives_tiny_terminals(screen):
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [dict(PR)], None), ("REVIEWED", [], None)], time.time()
	# ponytail: the sweep stopped at w=40, so it never reached PANE_MIN and never drew the pane at all —
	# which is why a crash on every terminal under 16 rows wide enough for one got through. It has to
	# cover the widths where the pane turns on, with a detail and a review present to fill it.
	st.details[(dict(PR)["url"], dict(PR)["updatedAt"])] = {
		"branch": "feat/x", "add": 4, "del": 2, "files": 3,
		"checks": [{"name": f"c{i}", "state": "ok"} for i in range(9)]}
	# ponytail: a review with a SUMMARY AND NO FINDINGS — every entry written before the findings field
	# existed, so the common case. The findings loop bounds itself; the summary fallback did not, and a
	# sweep over a PR with no review at all could never reach either.
	log.log_review(dict(PR), "opus", {"verdict": "approve", "body": "b",
	                                  "summary": "a summary long enough to wrap onto a second line here"})
	for h in range(1, 20):
		for w in list(range(1, 40)) + list(range(140, 240, 6)):
			screen.h, screen.w = h, w
			sel, cur = ui.draw(screen, st, 0)  # FakeScr asserts every addnstr lands on screen
			assert sel == 0 and cur["url"] == "u"
	# ponytail: 7, not 6 — the footer is two rows now, so the shortest terminal that still shows a row
	# is one taller than it was. Below h=5 draw() bails entirely rather than fault.
	screen.h, screen.w = 7, 120
	ui.draw(screen, st, 0)
	# a short terminal drops the column header rather than the row it describes
	assert "▌" in screen.text() and "b" in screen.line(4) and "AGE" not in screen.text()


def test_draw_prompt_replaces_footer(screen):
	st = State(60)
	ui.draw(screen, st, 0, prompt=" sure? [y/n]")
	assert screen.line(screen.h - 1).strip() == "sure? [y/n]"


def test_draw_truncates_long_title_on_narrow_screen(screen):
	screen.w = 40
	st = State(60)
	st.sections = [("MINE", [dict(PR, title="x" * 200), dict(PR, url="v", title="y" * 200)], None)]
	ui.draw(screen, st, 1, now=1000.0)  # ponytail: a fixed clock — the marquee made this ~1-in-3 flaky
	out = screen.text()
	assert "xxxxx…" in out and "yyyyy…" not in out and "yyyyy" in out


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
	st.interval = 90  # --interval in seconds that is not a whole minute
	screen.getch = _keys(27)
	ui.dropdown(screen, st, 0, "i")
	assert "▸ 90s" in screen.text() and "  5m" in screen.text()
	screen.h, screen.w = 20, 2
	ui.popup(screen, 1, 0, "t", ["x"], 0)  # must not raise


def test_strip_shows_refreshing_while_fetch_in_flight(screen):
	screen.w = 180  # wide enough for the whole strip incl. the version
	st = State(60)
	st.sections, st.fetched_at, st.fetching = [("MINE", [], None)], time.time(), True
	ui.draw(screen, st, 0)
	assert "refreshing" in screen.text() and "next refresh" not in screen.text()


def test_strip_collapses_groups_to_chips_on_narrow_screens(screen, monkeypatch):
	# ponytail: "│ Voices off" widened the Agent group by 23, so the pins moved up by that
	monkeypatch.setattr(ui.knowledge, "store_moved", lambda: False)  # conftest moves TEAM; pin the optional row off
	monkeypatch.setattr(ui.knowledge, "effective", lambda: "~/.prs_memory")  # layout, not paths: keep it stable
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	def row1(w):
		screen.w = w
		ui.draw(screen, st, 0)
		return screen.line(1)
	out = row1(263)
	assert out.index("Session") + len("Session") == screen.line(0).index("v" + ui.VERSION) + len("v" + ui.VERSION) + 1  # chip edge incl. its padding
	assert out.rstrip().endswith("Team off") and "☰" not in out and "Memory ~/.prs_memory" in out
	assert "Agent" in out
	assert out.index("Agent") < out.index("View") < out.index("Knowledge")
	out = row1(247)
	assert "Team off" in out and "  │  " in out and "   │   " not in out  # spacing tightens before anything folds
	out = row1(207)
	assert "History 4h" in out and out.rstrip().endswith("☰ Knowledge")  # Knowledge folds first, it is the least-touched
	out = row1(167)
	assert "Effort medium" in out and out.rstrip().endswith("☰ Knowledge") and "☰ View" in out and "Summaries" not in out
	out = row1(120)
	assert "☰ Agent" in out and "☰ View" in out and "☰ Knowledge" in out and "Model" not in out
	assert ui.ANCHORS["m"] == ui.ANCHORS["R"] and ui.ANCHORS["t"] == ui.ANCHORS["V"]  # folded keys hang from the chip
	assert ui.ANCHORS["L"] == ui.ANCHORS["T"] == ui.ANCHORS["K"]
	out = row1(80)
	assert "☰ Settings" in out and "Reviewer" not in out and "View" not in out  # all three nested under one chip
	assert ui.ANCHORS["R"] == ui.ANCHORS["V"] == ui.ANCHORS["K"] == ui.ANCHORS["m"] == ui.ANCHORS["S"]
	out = row1(55)
	assert "Session" in out and "Settings" not in out


def test_settings_menu_opens_a_group(screen):
	screen.w = 70
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	seen = []
	def getch():
		seen.append(screen.text())
		return [ord("j"), 10, 27, 27][len(seen) - 1]  # to View, open it, back to Settings, close
	screen.getch, screen.timeout = getch, lambda t: None
	ui.settings_menu(screen, st, 0)
	assert "▸ Agent ▸" in seen[0] and "  View ▸" in seen[0] and "Settings:  j/k move" in seen[0]
	assert "Summaries   all" in seen[2] and "View:  j/k move" in seen[2]
	assert "▸ View ▸" in seen[3] and "Settings:  j/k move" in seen[3]


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
	assert "▸ Model    opus" in seen[0] and "Depth    adaptive" in seen[0] and "Agent:  j/k move" in seen[0]
	assert "▸ adaptive" in seen[2] and "Depth:  j/k or d move" in seen[2]
	assert "Depth    low" in seen[4] and "Agent:  j/k move" in seen[4]  # back in the group with the new value


def test_group_menu_toggles_drafts(screen):
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	screen.getch, screen.timeout = _keys(ord("j"), 10, 27), lambda t: None
	ui.group_menu(screen, st, 0, "V")
	assert st.drafts is True


def test_group_menu_index_survives_a_row_disappearing(screen, monkeypatch):
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	moved = [True]
	monkeypatch.setattr(ui.knowledge, "store_moved", lambda: moved[0])
	monkeypatch.setattr(ui.team, "ERROR", "")
	keys = iter([ord("j"), ord("j"), 10, 27, 27])
	seen = []
	def getch():
		seen.append(screen.text())
		k = next(keys)
		if len(seen) == 2:
			moved[0] = False  # the Store row vanishes while the cursor sits on it
		return k
	screen.getch, screen.timeout = getch, lambda t: None
	opened = []
	monkeypatch.setattr(ui, "team_setup", lambda *a: opened.append("T"))
	ui.group_menu(screen, st, 0, "K")  # Enter with idx past the end must clamp to the last row, not raise
	assert "Store" in seen[1] and "Store" not in seen[2]
	assert opened == ["T"]  # clamped onto the last surviving row, which is Team


def test_dropdown_anchor_is_fresh_each_draw(screen):
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	screen.w = 200
	ui.draw(screen, st, 0)
	assert "m" in ui.ANCHORS
	screen.w = 50  # everything but Session gone: no anchors, dropdown falls back to the row start
	ui.draw(screen, st, 0)
	assert "m" not in ui.ANCHORS and "R" not in ui.ANCHORS


def test_strip_shows_update_and_auto_badges(screen):
	screen.w = 180
	st = State(60)
	st.sections, st.fetched_at, st.update = [("MINE", [], None)], time.time(), "9.9.9"
	st.set_auto(True)
	ui.draw(screen, st, 0)
	out = screen.line(0)
	assert "update to v9.9.9 · u" in out and "AUTO" in out and "0 agents running" in out
	assert out.index("PRs") < out.index("agents running") < out.index("updated") < out.index("next refresh") < out.index("AUTO")
	# narrower: the countdown, status, agents, PR count and AUTO go one by one; the badge and the update prompt stay
	def row0(w):
		screen.w = w
		ui.draw(screen, st, 0)
		return screen.line(0)
	out = row0(120)
	assert "gitdashy" in out and "update to v9.9.9 · u" in out and "AUTO" in out and "next refresh" not in out and "updated" in out
	out = row0(80)
	assert "gitdashy v" in out and "update to v9.9.9 · u" in out and out.rstrip().endswith("· u")
	out = row0(60)
	assert "gitdashy" in out and "update to v9.9.9 · u" in out and "PRs" not in out and "AUTO" not in out
	out = row0(30)
	assert "update to v9.9.9 · u" in out  # when even the badge and the prompt cannot share the row, the prompt wins


def test_hints_show_each_settings_key(screen, monkeypatch):
	monkeypatch.setattr(ui.knowledge, "store_moved", lambda: False)
	monkeypatch.setattr(ui.knowledge, "effective", lambda: "~/.prs_memory")  # layout, not paths: keep it stable
	screen.w = 250  # three groups, each key spelled out: nothing folds only well past 200
	st = State(60)
	st.sections, st.fetched_at, st.hints = [("MINE", [], None)], time.time(), True
	ui.draw(screen, st, 0)
	out = screen.text()
	assert "m Model " + st.model in out and "d Depth" in out and "e Effort" in out
	assert "s Summaries" in out and "D Drafts" in out and "t History" in out and "i next refresh" in out and "r updated" in out
	assert "L Memory ~/.prs_memory" in out and "T Team off" in out
	st.hints = False
	ui.draw(screen, st, 0)
	assert "m Model" not in screen.text() and "i next refresh" not in screen.text()


def test_dream_screen_shows_animation_then_summary_and_writes_on_y(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "a__b.md").write_text("- x\n- x\n")  # confirmed facts; drafts are not dreamt about
	def slow_dream(model):
		time.sleep(0.3)
		return "merged dupes", {"mine/a__b.md": "- x\n- x\n"}, {"mine/a__b.md": "- x"}
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
	(tmp_path / "a__b.md").write_text("- x\n")
	monkeypatch.setattr(ui.memory, "dream", lambda m: ("s", {"mine/a__b.md": "- x\n"}, {"mine/a__b.md": "- changed"}))
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


def test_esc_menu_theme_notify_refresh_quit(screen, monkeypatch):
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	monkeypatch.setattr(ui, "init_colors", lambda: None)
	monkeypatch.setattr(config, "THEME", "dashy")
	monkeypatch.setattr(config, "NOTIFY", True)
	# Enter cycles the theme, j+Enter toggles notify, j+Enter refreshes and closes
	screen.getch, screen.timeout = _keys(10, ord("j"), 10, ord("j"), 10), lambda t: None
	assert ui.esc_menu(screen, st, 0) is False and st.wake.is_set()
	assert config.THEME == "dracula" and config.NOTIFY is False
	assert "▸ Refresh" in screen.text() and "Theme    dracula" in screen.text() and "Notify   off" in screen.text()
	row = next(y for y in range(screen.h) if "gitdashy" in screen.line(y) and "╭" in screen.line(y))
	assert abs(row - screen.h // 2) <= 3 and abs(screen.line(row).index("╭") - screen.w // 2) <= 12  # mid-screen, not on the header
	screen.getch = _keys(27)
	assert ui.esc_menu(screen, st, 0) is False  # esc closes without quitting
	screen.getch = _keys(ord("k"), 10)
	assert ui.esc_menu(screen, st, 0) is True  # k wraps to Quit


def _team(monkeypatch, tmp_path):
	mine, shared = tmp_path / "mine", tmp_path / "team" / "memory"
	mine.mkdir(parents=True)
	shared.mkdir(parents=True)
	monkeypatch.setattr(config, "MEMORY_DIR", str(mine))
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "team"))
	monkeypatch.setattr(ui.team, "on", lambda: True)
	monkeypatch.setattr(ui.team, "NAME", "org/t")
	return mine, shared


def test_share_screen_shares_one_fact_and_forgets_another(screen, monkeypatch, st, tmp_path):
	mine, shared = _team(monkeypatch, tmp_path)
	(mine / "a__b.md").write_text("- worth sharing\n- keep to myself\n")
	pushes = []
	monkeypatch.setattr(ui.team, "push", lambda m: pushes.append(("team", m)))
	monkeypatch.setattr(ui.team, "push_dir", lambda d, m, l="sync": pushes.append(("mine", m)))
	seen = []
	def getch():
		seen.append(screen.text())
		return next(keys)
	keys = iter([ord("t"), ord("x"), 27])
	screen.getch, screen.timeout = getch, lambda t: None
	ui.share_screen(screen, st, 0)
	assert "share with org/t" in seen[0] and "worth sharing" in seen[0] and "1/2" in seen[0]
	assert (shared / "a__b.md").read_text() == "- worth sharing\n"  # t shared exactly the one on screen
	assert (mine / "a__b.md").read_text() == "- worth sharing\n"  # sharing copies; x forgot only the other one
	# t pushes the team repo; x touches both, since forgetting also withdraws the pooled evidence
	assert [w for w, _ in pushes] == ["team", "mine", "team"]


def test_share_screen_says_so_when_there_is_nothing_to_share(screen, monkeypatch, st, tmp_path):
	mine, shared = _team(monkeypatch, tmp_path)
	(mine / "a__b.md").write_text("- already theirs\n")
	(shared / "a__b.md").write_text("- already theirs\n")
	screen.getch, screen.timeout = _keys(27), lambda t: None
	ui.share_screen(screen, st, 0)
	assert "nothing of yours the team is missing" in screen.text()


def test_share_screen_never_offers_a_draft(screen, monkeypatch, st, tmp_path):
	mine, _ = _team(monkeypatch, tmp_path)
	ui.memory.append("a/b", "one review said so")  # a draft is not yours to share
	screen.getch, screen.timeout = _keys(27), lambda t: None
	ui.share_screen(screen, st, 0)
	assert "nothing of yours the team is missing" in screen.text()


def test_share_screen_puts_what_two_people_found_first(screen, monkeypatch, st, tmp_path):
	mine, shared = _team(monkeypatch, tmp_path)
	(mine / "a__b.md").write_text("- only I found this\n- both of us found this\n")
	pool = tmp_path / "team" / "memory" / "pool"
	(pool / "me").mkdir(parents=True)
	(pool / "martin").mkdir(parents=True)
	(pool / "me" / "a__b.md").write_text("- both of us found this\n")
	(pool / "martin" / "a__b.md").write_text("- Both of us found this.\n")  # reworded, still the same fact
	screen.getch, screen.timeout = _keys(27), lambda t: None
	ui.share_screen(screen, st, 0)
	out = screen.text()
	assert "both of us found this" in out and "★ 2 people found this" in out  # corroborated one is shown first
	assert "1/2" in out


def test_set_path_clones_a_git_url_and_asks_first(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mine"))
	got = []
	monkeypatch.setattr(ui.knowledge, "adopt", lambda u: got.append(u) or "")
	monkeypatch.setattr(ui.knowledge, "set_local", lambda p: pytest.fail("a URL must not be treated as a path"))
	monkeypatch.setattr(ui, "ask", lambda *a: "git@github.com:NilsPontus/Np_Claude_Agentic.git")
	screen.getch, screen.timeout = _keys(ord("y")), lambda t: None
	ui.set_path(screen, st, 0, "L")
	assert got == ["git@github.com:NilsPontus/Np_Claude_Agentic.git"]
	assert "clone git@github.com:NilsPontus/Np_Claude_Agentic.git" in screen.text()


def test_set_path_declining_the_clone_changes_nothing(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mine"))
	monkeypatch.setattr(ui.knowledge, "adopt", lambda u: pytest.fail("must not clone after n"))
	monkeypatch.setattr(ui, "ask", lambda *a: "https://github.com/org/mem.git")
	screen.getch, screen.timeout = _keys(ord("n")), lambda t: None
	ui.set_path(screen, st, 0, "L")


def test_set_path_sends_a_url_for_the_store_back_to_T(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(ui.knowledge, "adopt", lambda u: pytest.fail("the store is not cloned here"))
	monkeypatch.setattr(ui.knowledge, "set_store", lambda p: pytest.fail("a URL is not a directory"))
	monkeypatch.setattr(ui, "ask", lambda *a: "git@github.com:org/team.git")
	screen.getch, screen.timeout = _keys(ord(" ")), lambda t: None
	ui.set_path(screen, st, 0, "C")
	assert "T is what clones a team repo" in screen.text()


def test_set_path_reports_a_broken_path_instead_of_crashing(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mine"))
	def boom(p):
		raise OSError(2, "No such file or directory")
	monkeypatch.setattr(ui.knowledge, "set_local", boom)
	monkeypatch.setattr(ui, "ask", lambda *a: "/some/where")
	screen.getch, screen.timeout = _keys(ord(" ")), lambda t: None
	ui.set_path(screen, st, 0, "L")  # must not unwind out of curses
	assert "No such file or directory" in screen.text()


def test_marquee_scrolls_only_when_overflowing():
	from dashy.ui import art
	assert art.marquee("short", 10, 0.0) == "short"
	assert art.marquee("x", 0, 0.0) == ""
	frames = [art.marquee("abcdefghij", 4, t, cps=1) for t in range(20)]  # integer ticks, no float rounding
	assert frames[0] == "abcd" and frames[1] == "bcde" and all(len(f) == 4 for f in frames)
	assert "j   ·   " [:4] in frames and frames[17] == frames[0]  # wraps: 10 chars + 7-char gap


def test_draw_marquees_selected_overflowing_title(screen):
	screen.w = 60
	st = State(60)
	st.sections = [("MINE", [dict(PR, title="A" * 30 + "B" * 30 + "C" * 30)], None)]
	st.fetched_at = time.time()
	ui.draw(screen, st, 0)
	assert ui.SCROLLING[0] and "…" not in screen.line(4)
	st.sections = [("MINE", [dict(PR, title="tiny")], None)]
	ui.draw(screen, st, 0)
	assert not ui.SCROLLING[0]


def test_add_reviewer_picks_a_collaborator_and_requests_them(screen, monkeypatch, st):
	st.sections, st.fetched_at = [("MINE", [dict(PR)], None)], time.time()
	asked = []
	monkeypatch.setattr(ui.github, "collaborators", lambda repo: ["me", "alice", "bob"])
	monkeypatch.setattr(ui.github, "request_review", lambda repo, n, login: asked.append((repo, n, login)) or "")
	monkeypatch.setattr(ui.curses, "napms", lambda ms: None, raising=False)
	monkeypatch.setattr(ui.curses, "flushinp", lambda: None, raising=False)
	screen.getch, screen.timeout = _keys(ord("j"), 10), lambda t: None  # down past alice, enter on bob
	ui.add_reviewer(screen, st, 0, dict(PR))
	assert asked == [("a/b", 7, "bob")] and "✓ asked bob" in screen.text() and st.wake.is_set()



def test_a_live_status_wins_over_the_fetched_one(monkeypatch):
	"""A MINE row always carries GitHub's decision, which used to short-circuit what this session is doing.

	So a pre-review started, ran and finished with the row still reading '· awaiting review'.
	"""
	st = State(0)
	pr = dict(PR, url="u", section="MINE")
	pr["status"] = "· awaiting review"
	st.sections = [("MINE", [pr], None)]
	st.reviews["u"] = "pre-reviewing..."
	st.running.add("u")
	scr = FakeScr()
	ui.C = lambda n: 0
	ui.draw(scr, st, 0)
	painted = "\n".join(scr.line(y) for y in range(scr.h))
	assert "pre-reviewing" in painted
	assert "awaiting review" not in painted


def test_any_in_flight_verb_counts_as_running(monkeypatch):
	"""Four places matched the literal 'reviewing...', so a pre-review was invisible to all of them.

	Behavioural, not a source check: the previous version of this test asserted on inspect.getsource
	and broke on a refactor that was correct, which is the failure mode of pinning wording.
	"""
	from dashy.core.state import in_flight
	# ponytail: membership, not a suffix. The status channel also carries a truncated stderr line, so
	# an error ending in "..." used to count as a running agent — permanently, on wording nobody owns.
	probe = State(60)
	probe.running.add("u")
	assert in_flight(probe, "u") and not in_flight(probe, "other")
	probe.reviews["other"] = "error: connection reset by peer..."
	assert not in_flight(probe, "other"), "an error is not an agent, whatever it ends with"

	st = State(0)
	pr = dict(PR, url="u", section="MINE")
	pr["status"] = "· awaiting review"
	st.sections = [("MINE", [pr], None)]
	st.reviews["u"] = "pre-reviewing..."
	scr = FakeScr()
	ui.C = lambda n: 0
	ui.draw(scr, st, 0)
	assert "1 agent" in scr.text() or "1 running" in scr.text() or "pre-reviewing" in scr.text()


def test_the_pane_shows_the_selected_pr_and_folds_away(screen, monkeypatch):
	screen.w, screen.h = 190, 26
	st = State(60)
	pr = dict(PR, number=949, title="feat(viewer): user-customisable keyboard shortcuts", url="u949")
	st.sections, st.fetched_at = [("MINE", [pr], None)], time.time()
	# ponytail: keyed by (url, updatedAt) now — a PR that moved has a different branch head, diff size
	# and CI result, and keying on the url alone kept showing the old ones until a restart.
	st.details[("u949", pr["updatedAt"])] = {"branch": "feat/kb", "add": 412, "del": 96, "files": 14,
	                                         "checks": [{"name": "ci", "state": "ok"}, {"name": "e2e", "state": "run"}]}
	ui.draw(screen, st, 0)
	out = screen.text()
	assert "SELECTED PR" in out and "#949" in out and "feat/kb" in out
	assert "+412" in out and "−96" in out and "14 files" in out
	assert "CHECKS" in out and "✓ ci" in out and "~ e2e" in out
	assert "ACTIONS" in out and "open in browser" in out
	st.pane = False
	ui.draw(screen, st, 0)
	assert "SELECTED PR" not in screen.text()  # ⏎ folds it away and the list takes the width


def test_the_pane_never_appears_on_a_narrow_terminal(screen):
	screen.w, screen.h = 120, 26
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [dict(PR)], None)], time.time()
	ui.draw(screen, st, 0)
	assert "SELECTED PR" not in screen.text()  # the list wins where there is not room for both


def test_the_pane_draws_a_review_it_has_findings_for(screen):
	screen.w, screen.h = 190, 30
	st = State(60)
	pr = dict(PR, url="u1", review={"verdict": "request_changes", "model": "opus", "depth": "high",
	                                "summary": "adds a retry loop",
	                                "findings": [{"kind": "blocking", "loc": "keymap.ts:88", "text": "duplicate binding"},
	                                             {"kind": "nit", "loc": "", "text": "table misaligned"}]})
	st.sections, st.fetched_at = [("MINE", [pr], None)], time.time()
	st.details["u1"] = {}
	ui.draw(screen, st, 0)
	out = screen.text()
	assert "AI REVIEW" in out and "changes requested" in out
	assert "1 blocking" in out and "1 nit" in out
	assert "keymap.ts:88  duplicate binding" in out


def test_the_pane_says_so_when_nothing_is_selected(screen):
	screen.w, screen.h = 190, 26
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	ui.draw(screen, st, 0)
	assert "no row selected" in screen.text()


def test_a_clipped_cell_keeps_its_ellipsis(screen):
	"""The grid measured the ellipsis against the column's nominal width and clipped the write after.

	So on a narrow terminal the "…" was the character that got cut, and the text ended mid-word with
	nothing saying it continued.
	"""
	screen.w = 40
	st = State(60)
	# ponytail: two rows, and the UNSELECTED one is the subject — a selected row marquees rather than
	# clipping, so asserting on it would be asserting on what time it is.
	st.sections = [("MINE", [dict(PR, title="x" * 200), dict(PR, url="v", title="y" * 200)], None)]
	ui.draw(screen, st, 1, now=1000.0)
	body = "\n".join(screen.line(y) for y in range(screen.h))
	assert "…" in body, "a truncated cell must say so"
	assert "xxxxx" in body


def test_reviewer_chips_survive_the_grid(screen):
	"""They arrived after the design was drawn and it has no column for them."""
	st = State(60)
	st.sections = [("MINE", [dict(PR, reviewers="✓bob ·alice")], None)]
	ui.draw(screen, st, 0)
	body = "\n".join(screen.line(y) for y in range(screen.h))
	assert "✓bob" in body and "·alice" in body


def test_the_footer_tells_you_the_keys_that_moved(screen):
	"""⏎ is the pane now, r is review, f is refresh. Three keys changed meaning, so the footer must say so.

	Behavioural rather than a getsource check: what matters is that a user reads the new binding, not
	that the handler is spelled a particular way. A source assertion passes on code that never runs.
	"""
	st = State(60)
	st.sections = [("MINE", [dict(PR)], None)]
	ui.draw(screen, st, 0)
	foot = screen.line(screen.h - 2) + " " + screen.line(screen.h - 1)  # two rows, as the design draws it
	assert "⏎ pane" in foot and "r review" in foot and "f refresh" in foot
	assert "⏎ review" not in foot and "r refresh" not in foot   # the old meanings are gone from the footer


def test_the_footer_wraps_into_two_rows_and_never_cuts_a_key(screen):
	"""The design puts the keys on two rows in columns. Wrapping keeps them all on screen; the earlier
	one-row version dropped whole groups, so `f refresh` and `q quit` were invisible at any normal width.

	A key that fits on neither line is dropped WHOLE — a truncated key name is worse than a missing one,
	because it still reads as an instruction.
	"""
	st = State(60)
	st.sections = [("MINE", [dict(PR)], None)]
	for w in (165, 120, 100, 80, 60):
		screen.w = w
		ui.draw(screen, st, 0, now=1000.0)
		rows = [screen.line(screen.h - 2).replace("│", " "), screen.line(screen.h - 1).replace("│", " ")]
		for r in rows:
			assert not r.rstrip().endswith("·"), f"w={w}: ends mid-list"
			for frag in ("pre-rev", "refres", "interva", "updat"):
				bad = [t for t in r.split() if t.startswith(frag) and t not in ("pre-review", "refresh", "interval", "update")]
				assert not bad, f"w={w}: key cut mid-word: {bad}"
		assert "NAV" in rows[0]
	screen.w = 165
	ui.draw(screen, st, 0, now=1000.0)
	both = screen.line(screen.h - 2) + " " + screen.line(screen.h - 1)
	assert all(g in both for g in ("NAV", "RUN", "CONFIG", "APP"))  # nothing dropped when there is room
	assert "│" in screen.line(screen.h - 2)                          # a rule between columns


def _pr_at(iso="2026-09-04T09:00:00Z", **kw):
	return dict(PR, updatedAt=iso, repository={"nameWithOwner": "acme/api", "name": "api"}, number=7, **kw)


def test_pre_review_offers_reads_and_reruns(monkeypatch, screen, tmp_path):
	"""This handler shipped calling a module screen.py does not import and crashed on the first keypress.

	It was eight lines inside main()'s key loop, where no test could reach it — which is exactly what
	#8's review said about it. It is a function now, and this drives all four of its paths.
	"""
	from dashy.core import review as review_mod
	monkeypatch.setattr(review_mod, "SELF_DIR", str(tmp_path))
	st = State(60)
	started, paged, asked = [], [], []
	monkeypatch.setattr(State, "start_self_review", lambda self, pr: started.append(pr["number"]))
	monkeypatch.setattr(ui, "page", lambda *a: paged.append((a[3], a[4])))  # (path, label)
	monkeypatch.setattr(ui, "confirm", lambda s, st_, sel, msg: asked.append(msg) or True)

	# 1. nothing on disk -> offers, and running is what happens
	ui.pre_review(screen, st, 0, _pr_at())
	assert started == [7] and paged == [] and "Nothing is posted" in asked[0]

	# 2. a pre-review that still describes this diff -> read it back, do not re-run
	path = review_mod.self_review_path("acme/api", 7)
	open(path, "w").write("# pre-review\n")
	os.utime(path, (4e9, 4e9))  # ponytail: mtime AFTER the PR's updatedAt, or it reads as stale
	ui.pre_review(screen, st, 0, _pr_at("2026-09-04T09:00:00Z"))
	assert started == [7] and len(paged) == 1
	assert paged[0] == (path, "pre-review of #7")   # the file it wrote, found again by name

	# 3. the PR moved since -> offer a fresh one, and say why
	ui.pre_review(screen, st, 0, _pr_at("2099-01-01T00:00:00Z"))
	assert started == [7, 7] and "changed since its pre-review" in asked[-1]

	# 4. already in flight -> do nothing at all
	st.reviews[_pr_at()["url"]] = "pre-reviewing..."
	st.running.add(_pr_at()["url"])
	ui.pre_review(screen, st, 0, _pr_at())
	assert started == [7, 7] and len(paged) == 1


def test_reviewer_chips_get_their_own_column_and_their_own_colours(screen, monkeypatch):
	"""Folded into the state cell they took the state's colour — and state is the LAST column, so at any
	real width they were pushed off the right edge and never appeared at all.

	This also pins COLS against cells: zip() drops a cell silently when the two disagree, and that pair
	has now been wrong in each direction — 7 cells with 6 columns, then 7 columns with 6 cells.
	"""
	painted = []
	real = screen.addnstr
	monkeypatch.setattr(screen, "addnstr", lambda y, x, s, n, a=0: painted.append((s.rstrip(), a)) or real(y, x, s, n, a))
	monkeypatch.setattr(ui, "C", lambda k: k)          # colour pair number, so chips are distinguishable
	ui.REVIEWER_COLOR.clear()
	ui.REVIEWER_COLOR.update({"✓": 4, "✗": 3, "·": 5, "~": 1})
	screen.w = 150
	st = State(60)
	# ponytail: two rows, asserting on the UNSELECTED one — a selected row paints everything in the
	# selection tint on purpose, so its chips share a colour by design rather than by the bug.
	st.sections = [("MINE", [dict(PR, url="u1"), dict(PR, url="u2", reviewers="✓bob ✗carol",
	                                                  status="✓ approved")], None)]
	ui.draw(screen, st, 0, now=1000.0)

	chips = {s: a for s, a in painted if s in ("✓bob", "✗carol")}
	assert set(chips) == {"✓bob", "✗carol"}, "every chip that fits must be painted"
	assert len(set(chips.values())) == 2, "each glyph keeps its own colour, not the state's"
	assert "REVIEWERS" in screen.text()

	# more reviewers than the column holds: the overflow is elided, never half a name
	painted.clear()
	st.sections = [("MINE", [dict(PR, url="u1"), dict(PR, url="u3", reviewers="✓bob ✗carol ·dave",
	                                                  status="✓ approved")], None)]
	ui.draw(screen, st, 0, now=1000.0)
	assert "…" in screen.text()
	assert not [s for s, _ in painted if s.startswith("·dav") and s != "·dave"]


def test_cells_and_cols_stay_the_same_length(screen):
	"""zip() truncates in silence, and that is exactly how the status ended up under REVIEWERS."""
	st = State(60)
	st.sections = [("MINE", [dict(PR, reviewers="✓bob")], None)]
	ui.draw(screen, st, 0, now=1000.0)   # the assert lives in draw(); this fails loudly if they diverge
	assert len(ui.COLS) == 8   # age repo pr title author reviewers ci state


def test_a_finding_shows_its_text_not_mostly_its_path(screen):
	"""A full path is most of a pane on its own, so the finding itself was the half that got truncated.

	The directory is recoverable from the file name; what the review actually said is not.
	"""
	screen.w, screen.h = 190, 26
	st = State(60)
	pr = dict(PR, url="u9", number=966)
	st.sections, st.fetched_at = [("MINE", [pr], None)], time.time()
	st.details[("u9", pr["updatedAt"])] = {"branch": "feat/library", "add": 214, "del": 138, "files": 25}
	# ponytail: findings are a structured field, not parsed out of the body prose
	log.log_review(pr, "opus", {"verdict": "comment", "summary": "s", "body": "b", "findings": [
		{"kind": "note", "loc": "features/library/ui/LibraryLanding.tsx:19",
		 "text": "Count is derived on the client and can disagree with the API"}]})
	ui.draw(screen, st, 0, now=1000.0)
	out = screen.text()

	assert "LibraryLanding.tsx:19" in out                       # the file, short
	assert "features/library/ui/LibraryLanding" not in out      # not the whole path
	assert "Count is derived" in out                            # and the finding itself is readable
	assert "v read all" in out                                  # with a way to open the whole review


def test_v_reads_the_review_from_any_row_that_has_one(screen, monkeypatch):
	"""The pane summarises findings for the selected PR whatever section it is in, so the key that
	opens the whole thing has to reach as far as the summary does — it was REVIEWED-only."""
	pr = dict(PR, url="u10", number=970, section="MINE")
	log.log_review(pr, "opus", {"verdict": "approve", "summary": "s", "body": "b",
	                            "findings": [{"kind": "nit", "loc": "a.ts:1", "text": "x"}]})
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [pr], None)], time.time()
	screen.w, screen.h = 190, 26
	ui.draw(screen, st, 0, now=1000.0)
	assert "read the full review" in screen.text()   # offered on a MINE row, not just REVIEWED


def test_the_pane_never_writes_over_the_footer_or_off_the_screen(screen):
	"""Two writes bypassed line() — the summary fallback and the model tag — so a review with a summary
	and NO findings walked past the last list row.

	That is every entry written before the findings field existed, so the common case rather than an
	edge one. The findings loop bounds itself, which is why the case with findings looked fine.
	At h=16 the stray write lands ON screen and overprints the footer; taller and it goes off it.
	"""
	st = State(60)
	pr = dict(PR, url="uS")
	st.sections, st.fetched_at = [("MINE", [pr], None)], time.time()
	st.details[("uS", pr["updatedAt"])] = {"branch": "feat/x", "add": 4, "del": 2, "files": 3,
	                                       "checks": [{"name": f"check-{i}", "state": "ok"} for i in range(9)]}
	log.log_review(pr, "opus", {"verdict": "approve", "body": "b",
	                            "summary": " ".join(["a summary long enough to wrap several times"] * 6)})
	for h in (16, 17, 18, 20, 24):
		screen.h, screen.w = h, 190
		ui.draw(screen, st, 0, now=1000.0)          # must not raise
		foot = screen.line(h - 2) + screen.line(h - 1)
		assert "NAV" in foot, f"h={h}: the footer was overwritten by the pane"
		assert "summary long enough" not in foot, f"h={h}: pane text landed on the footer"


def test_a_missing_pager_does_not_strand_the_terminal(monkeypatch, screen, tmp_path):
	"""endwin / run / refresh in a straight line meant a missing pager raised between the second and
	third: no refresh, and the exception unwound out of main() with the terminal already out of curses
	mode. What you get is a shell that echoes ^M and stair-steps its output.
	"""
	restored = []
	monkeypatch.setattr(ui.curses, "endwin", lambda: restored.append("endwin"), raising=False)
	monkeypatch.setattr(screen, "refresh", lambda: restored.append("refresh"))
	def missing(cmd, **kw):
		raise FileNotFoundError(2, "No such file or directory")
	monkeypatch.setattr(ui.subprocess, "run", missing)

	err = ui.shell_out(screen, ["less", "-R"], "body")

	assert "less" in err and "No such file" in err       # reported, not raised
	assert restored == ["endwin", "refresh"], "the screen must come back even when the command does not"


def test_shell_out_restores_on_success_too(monkeypatch, screen):
	seen = []
	monkeypatch.setattr(ui.curses, "endwin", lambda: seen.append("endwin"), raising=False)
	monkeypatch.setattr(screen, "refresh", lambda: seen.append("refresh"))
	monkeypatch.setattr(ui.subprocess, "run", lambda cmd, **kw: seen.append(("ran", cmd[0])))
	assert ui.shell_out(screen, ["less"], "x") == ""
	assert seen == ["endwin", ("ran", "less"), "refresh"]


def test_editing_memory_with_no_editor_reports_instead_of_crashing(monkeypatch, screen, tmp_path):
	"""The exact case this PR exists for: $EDITOR unset and nano missing.

	It raised NameError right after shell_out returned — confirm(scr, state, sel, …) inside a function
	that had neither. The terminal was restored by the finally and the dashboard died anyway, which is
	the same class as the `p` handler: an error path no test drives.
	"""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.setattr(ui.curses, "endwin", lambda: None, raising=False)
	monkeypatch.setattr(ui.subprocess, "run",
	                    lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError(2, "No such file or directory")))
	said = []
	monkeypatch.setattr(ui, "confirm", lambda s, st, sl, msg: said.append(msg) or True)
	st = State(60)

	ui.edit_memory(screen, st, 0, None)          # must not raise

	assert said and "set $EDITOR" in said[0]


def test_every_name_in_the_ui_resolves():
	"""Two NameErrors shipped today, both on error paths nothing drives — `p` and the editor fallback.

	symtable knows about closures, comprehensions and globals, so this is the check that a test suite
	cannot give you: a name read by a function that is neither local, free from an enclosing scope, a
	module global, nor a builtin. It costs nothing and it is exactly the class that keeps escaping.
	"""
	import symtable, builtins, pathlib, dashy
	# ponytail: anchored on the package, not the cwd. Path("dashy") resolves relative to wherever pytest
	# was started, so from outside the repo root it globbed nothing and the test PASSED — a guard against
	# vacuous tests that was itself vacuous. The count assert below is the belt to that brace.
	root = pathlib.Path(dashy.__file__).parent
	mods = sorted(root.rglob("*.py"))
	assert len(mods) >= 8, f"found only {len(mods)} modules under {root} — this test cannot pass vacuously"
	bad = []
	for mod_path in mods:
		src = mod_path.read_text()
		top = symtable.symtable(src, str(mod_path), "exec")
		names = {s.get_name() for s in top.get_symbols()}
		# ponytail: module dunders are always bound at runtime but are not symtable symbols
		builtin = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__spec__"}

		def walk(tab, enclosing):
			here = {s.get_name() for s in tab.get_symbols() if s.is_local() or s.is_parameter()}
			for s in tab.get_symbols():
				n = s.get_name()
				if (s.is_referenced() and not s.is_local() and not s.is_parameter() and not s.is_free()
				        and n not in names and n not in builtin and n not in enclosing):
					bad.append(f"{mod_path}:{tab.get_name()}() reads {n!r}")
			for child in tab.get_children():
				walk(child, enclosing | here)

		walk(top, set())
	assert not bad, "names that resolve to nothing:\n  " + "\n  ".join(bad)


def _with_pre_review(monkeypatch, tmp_path, pr, mtime):
	from dashy.core import review as review_mod
	monkeypatch.setattr(review_mod, "SELF_DIR", str(tmp_path))
	path = review_mod.self_review_path(pr["repository"]["nameWithOwner"], pr["number"])
	os.makedirs(tmp_path, exist_ok=True)
	open(path, "w").write("# pre-review\n")
	os.utime(path, (mtime, mtime))
	return path


def test_the_pane_says_a_pre_review_exists_and_whether_it_is_current(monkeypatch, screen, tmp_path):
	"""They were invisible: you pressed p and hoped, with no way to tell a fresh one from a review of a
	diff you have since pushed over. Derived from the filesystem, so it survives a restart."""
	screen.w, screen.h = 190, 26
	st = State(60)
	pr = dict(PR, url="uP", number=902, updatedAt="2026-09-04T09:00:00Z")
	st.sections, st.fetched_at = [("MINE", [pr], None)], time.time()

	ui.draw(screen, st, 0, now=1000.0)
	assert "PRE-REVIEW" not in screen.text()          # nothing on disk, nothing claimed

	_with_pre_review(monkeypatch, tmp_path, pr, 4e9)   # written after the PR was updated
	ui.draw(screen, st, 0, now=1000.0)
	out = screen.text()
	assert "PRE-REVIEW" in out and "current" in out and "stale" not in out
	assert "read the pre-review" in out and "open the pre-review" in out

	moved = dict(pr, updatedAt="2099-01-01T00:00:00Z")  # the PR moved after the pre-review
	st.sections = [("MINE", [moved], None)]
	ui.draw(screen, st, 0, now=1000.0)
	out = screen.text()
	assert "stale, the PR moved since" in out
	assert "re-run: the PR moved since" in out, "ACTIONS must promise what p actually does"


def test_Y_copies_the_path_and_says_so_when_there_is_none(monkeypatch, screen, tmp_path):
	"""The path, not the contents — a file you open or hand on, not something to paste.

	ponytail: this DRIVES the handler. The first version called github.copy itself and pressed nothing,
	so neither branch ran — which is exactly how the p handler shipped a NameError.
	"""
	from dashy.core import review as review_mod
	monkeypatch.setattr(review_mod, "SELF_DIR", str(tmp_path))
	pr = dict(PR, url="uY", number=903, section="MINE")
	repo = pr["repository"]["nameWithOwner"]      # ponytail: from the fixture, not assumed
	opened, said = [], []
	monkeypatch.setattr(ui.github, "open_in_browser", lambda t: opened.append(t))
	monkeypatch.setattr(ui, "draw", lambda s, st, sl, prompt=None, now=None: said.append(prompt or ""))
	st = State(60)

	assert ui.open_pre_review(screen, st, 0, pr) == ""          # none yet: nothing opened
	assert opened == [] and "no pre-review of #903 yet" in said[-1]

	path = _with_pre_review(monkeypatch, tmp_path, pr, 4e9)
	assert ui.open_pre_review(screen, st, 0, pr) == path
	assert opened == [path] and "opened" in said[-1]
	# derived from owner, repo and number — nothing is remembered, so a restart finds it again
	assert path.endswith(f"{repo.replace('/', '__')}__903.md")
	assert path == review_mod.self_review_path(repo, 903)


def test_a_dream_that_deletes_needs_a_second_yes(screen, monkeypatch, st, tmp_path):
	"""A file going to zero read as one more row of line counts. A dream emptied general.md — eight
	cross-repo facts — and "8 → 0" scrolled past among the tidies on a single keypress.
	"""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "general.md").write_text("".join(f"- fact {i}\n" for i in range(8)))
	(tmp_path / "a__b.md").write_text("- x\n- x\n")
	before = {"mine/general.md": (tmp_path / "general.md").read_text(), "mine/a__b.md": "- x\n- x\n"}
	monkeypatch.setattr(ui.memory, "dream",
	                    lambda m: ("tidied", before, {"mine/general.md": "", "mine/a__b.md": "- x"}))
	asked = []
	monkeypatch.setattr(ui, "confirm", lambda s, st_, sl, msg: asked.append(msg) or False)  # say NO
	shown = []
	def getch():
		shown.append(screen.text())
		return ord("y")
	screen.getch, screen.timeout = getch, lambda t: None

	ui.dream_screen(screen, st, 0)

	panel = shown[-1]
	assert "DELETED" in panel, "a deletion must not render as a line count"
	assert "DELETES 1 file" in panel, "the footer must say what accepting destroys"
	assert asked and "DELETE mine/general" in asked[0] and "8 facts lost" in asked[0]
	# and saying no to the second prompt leaves everything alone
	assert (tmp_path / "general.md").read_text().count("- fact") == 8
	assert (tmp_path / "a__b.md").read_text() == "- x\n- x\n"


def test_a_dream_that_only_tidies_still_takes_one_yes(screen, monkeypatch, st, tmp_path):
	"""The gate must not fire on an ordinary tidy, or it becomes the prompt everyone learns to skip."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "a__b.md").write_text("- x\n- x\n")
	monkeypatch.setattr(ui.memory, "dream",
	                    lambda m: ("merged", {"mine/a__b.md": "- x\n- x\n"}, {"mine/a__b.md": "- x"}))
	asked = []
	monkeypatch.setattr(ui, "confirm", lambda s, st_, sl, msg: asked.append(msg) or True)
	screen.getch, screen.timeout = lambda: ord("y"), lambda t: None

	ui.dream_screen(screen, st, 0)

	assert not asked, "no deletion, so no second prompt"
	assert open(ui.memory.path("a/b")).read() == "- x\n"


def test_every_setting_key_opens_its_dropdown(screen, monkeypatch):
	"""x was in the settings table but not in main()'s key list, so it drew a hint and did nothing."""
	opened = []
	monkeypatch.setattr(ui, "dropdown", lambda scr, st, sel, key: opened.append(key))
	monkeypatch.setattr(ui, "init_colors", lambda: None)
	monkeypatch.setattr(ui.team, "activate", lambda: None)
	monkeypatch.setattr(ui.threading.Thread, "start", lambda self: None)
	monkeypatch.setattr(config, "SETTINGS", "")
	keys = list(ui.settings(State(60)))
	screen.getch, screen.timeout = _keys(*[ord(k) for k in keys], ord("q")), lambda t: None
	ui.main(screen, 60, False, "opus")
	assert opened == keys


def test_voices_dropdown_is_a_checklist(screen, monkeypatch):
	"""A list-valued setting: Enter toggles the row and stays open, Esc closes; the list is rebuilt in
	option order whatever order the boxes were ticked in."""
	monkeypatch.setattr(config, "VOICE", ["review"])
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	screen.w = 263
	screen.getch, screen.timeout = _keys(ord("j"), ord("j"), ord("j"), 10, ord("k"), ord("k"), 10, 10, 27), lambda t: None
	assert ui.dropdown(screen, st, 0, "x") is True  # bot on, ponytail on then off again, close
	assert config.VOICE == ["review", "bot"]
	assert "[x] review" in screen.text() and "[ ] ponytail" in screen.text() and "[x] bot" in screen.text()
	assert ui.snapshot(st)["voice"] == ["review", "bot"]
	screen.getch = _keys(10, ord("j"), ord("j"), ord("j"), 10, 27)  # untick review, then try to untick bot
	ui.dropdown(screen, st, 0, "x")
	assert config.VOICE == ["bot"]  # the last box will not untick
