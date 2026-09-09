import os
import subprocess
import time

import pytest

from dashy import config
from dashy.core import bind, log
from dashy.ui import screen as ui
from dashy.core.review import review
from dashy.core.state import State

from conftest import FakeScr, PR, a_team, claude_out


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


def _keys_seen(screen, seen, *ks):
	"""Like _keys, but records what the screen showed at the moment each key was asked for.

	ponytail: screen.text() after a modal returns is the LAST frame — the list a panel went back to
	before esc closed it — so asserting on the panel itself needs the frame it was waiting on.
	"""
	it = iter(ks)
	def go():
		seen.append(screen.text())
		return next(it)
	return go


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
	# ponytail: "│ Voices off" and "│ Hunters off" widened the Agent group by 39, so the pins moved up by that
	monkeypatch.setattr(ui.knowledge, "store_moved", lambda: False)  # conftest moves TEAM; pin the optional row off
	monkeypatch.setattr(ui.knowledge, "effective", lambda: "~/.prs_memory")  # layout, not paths: keep it stable
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	def row1(w):
		screen.w = w
		ui.draw(screen, st, 0)
		return screen.line(1)
	out = row1(279)
	assert out.index("Session") + len("Session") == screen.line(0).index("v" + ui.VERSION) + len("v" + ui.VERSION) + 1  # chip edge incl. its padding
	assert out.rstrip().endswith("Team off") and "☰" not in out and "Memory ~/.prs_memory" in out
	assert "Agent" in out
	assert out.index("Agent") < out.index("View") < out.index("Knowledge")
	out = row1(263)
	assert "Team off" in out and "  │  " in out and "   │   " not in out  # spacing tightens before anything folds
	out = row1(223)
	assert "History 4h" in out and out.rstrip().endswith("☰ Knowledge")  # Knowledge folds first, it is the least-touched
	out = row1(183)
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
	assert "▸ Model     opus" in seen[0] and "Depth     adaptive" in seen[0] and "Agent:  j/k move" in seen[0]
	assert "▸ adaptive" in seen[2] and "Depth:  j/k or d move" in seen[2]
	assert "Depth     low" in seen[4] and "Agent:  j/k move" in seen[4]  # back in the group with the new value


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
	screen.w = 280  # three groups, each key spelled out: nothing folds only well past 270
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
	mine = tmp_path / "mine"
	mine.mkdir(parents=True)
	monkeypatch.setattr(config, "MEMORY_DIR", str(mine))
	shared = a_team(monkeypatch, tmp_path, "org-t")
	bind.bind("a/b", "org-t")  # ponytail: sharing and pooling follow the binding, so the team must own a/b
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
	assert "share with org-t" in seen[0] and "worth sharing" in seen[0] and "1/2" in seen[0]
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
	pool = shared / "pool"   # ponytail: inside the team's own checkout, wherever a_team put it
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
	monkeypatch.setattr(ui, "ask", lambda *a: "https://github.com/org-mem.git")
	screen.getch, screen.timeout = _keys(ord("n")), lambda t: None
	ui.set_path(screen, st, 0, "L")


def test_set_path_sends_a_url_for_the_store_back_to_T(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(ui.knowledge, "adopt", lambda u: pytest.fail("the store is not cloned here"))
	monkeypatch.setattr(ui.knowledge, "set_store", lambda p: pytest.fail("a URL is not a directory"))
	monkeypatch.setattr(ui, "ask", lambda *a: "git@github.com:org-team.git")
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


def test_draw_keeps_a_hidden_draft_visible_while_it_has_an_agent_or_a_verdict():
	"""draw() must hand rows() what is in flight — and keep the row up long enough to read the verdict."""
	st = State(0)
	pr = dict(PR, url="u", isDraft=True, title="wip thing")
	st.sections = [("MINE", [pr], None)]
	st.drafts = False
	ui.C = lambda n: 0
	scr = FakeScr(30, 190)  # wide enough that the title column is not truncated
	ui.draw(scr, st, 0)
	assert "wip thing" not in scr.text()
	st.running.add("u")
	st.reviews["u"] = "pre-reviewing..."
	ui.draw(scr, st, 0)
	assert "wip thing" in scr.text()
	st.running.discard("u")          # agent done, verdict written
	st.reviews["u"] = "✓ approved"
	ui.draw(scr, st, 0)
	assert "wip thing" in scr.text(), "the row must survive to show the answer it was waiting for"


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
	assert "2 in code" in out   # ponytail: the pane points at the code tab; v is still in the footer


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
	assert opened == [path] and "handed" in said[-1]
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


def test_the_pane_says_which_brief_this_repos_reviews_get(screen, monkeypatch, tmp_path):
	"""A thing that silently selects your context must name what it selected, and why that one."""
	from dashy.core import bind, team
	mine = tmp_path / "mine"
	mine.mkdir(parents=True)
	(mine / "project.md").write_text("My own work.\n")
	monkeypatch.setattr(config, "MEMORY_DIR", str(mine))
	shared = a_team(monkeypatch, tmp_path, "org-t")
	(shared / "project.md").write_text("What the team builds.\n")
	st = State(60)
	monkeypatch.setattr(st, "want_detail", lambda pr: {})  # no background fetch from a draw test
	pr = dict(PR)

	ui.detail(screen, st, 30, 40, 60, pr)
	assert "BRIEF yours · a/b is bound to no team" in screen.text()

	screen.erase()
	bind.bind("a/b", "org-t")
	ui.detail(screen, st, 30, 40, 60, pr)
	assert "BRIEF team org-t" in screen.text()

	screen.erase()
	(shared / "project.md").unlink()
	ui.detail(screen, st, 30, 40, 60, pr)
	assert "BRIEF yours · team org-t has no brief" in screen.text()


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
	screen.getch, screen.timeout = _keys(ord("j"), ord("j"), 10, ord("k"), 10, 10, 27), lambda t: None
	assert ui.dropdown(screen, st, 0, "x") is True  # bot on, caveman on then off again, close
	assert config.VOICE == ["review", "bot"]
	assert "[x] review" in screen.text() and "[ ] caveman" in screen.text() and "[x] bot" in screen.text()
	assert ui.snapshot(st)["voice"] == ["review", "bot"]
	screen.getch = _keys(10, ord("j"), ord("j"), 10, 27)  # untick review, then try to untick bot
	ui.dropdown(screen, st, 0, "x")
	assert config.VOICE == ["bot"]  # the last voice will not untick
	monkeypatch.setattr(config, "HUNTER", ["tests"])
	screen.getch = _keys(ord("j"), ord("j"), 10, 27)  # hunters may all go off
	ui.dropdown(screen, st, 0, "h")
	assert config.HUNTER == [] and ui.snapshot(st)["hunter"] == []


def test_in_flight_row_says_how_long_it_has_been_running():
	"""ponytail: a big diff to a slow model spins for minutes; without this there is no way to tell a
	long answer from a wedged one. It must also FIT the 20-wide STATE column, or it says "3m…"."""
	st = State(0)
	pr = dict(PR, url="u", section="MINE")
	st.sections = [("MINE", [pr], None)]
	st.reviews["u"] = "pre-reviewing..."
	st.running.add("u")
	st.started_at["u"] = 1000.0
	ui.C = lambda n: 0
	def painted(now):
		scr = FakeScr()
		ui.draw(scr, st, 0, now=now)
		return "\n".join(scr.line(y) for y in range(scr.h))
	assert "pre-reviewing… 42s" in painted(1042.0)
	assert "pre-reviewing… 3m" in painted(1000.0 + 3 * 60 + 7)


def _waiting(monkeypatch, tmp_path):
	from dashy.core import memory
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	memory.append("a/b", "one review saw this")
	memory.append("a/b", "one review saw this")   # -> a fact, so it must NOT appear
	memory.append("a/b", "still only a guess")
	memory.append_self("a/b", "a pre-review found this")
	return memory


def test_drafts_screen_shows_what_is_waiting_and_how_close_it_is(screen, monkeypatch, st, tmp_path):
	"""The only store with no window into it. Counts are the point: they say how close a guess is."""
	_waiting(monkeypatch, tmp_path)
	screen.getch, screen.timeout = _keys(27), lambda t: None
	ui.drafts_screen(screen, st, 0)
	out = screen.text()
	assert "still only a guess" in out and "seen 1×" in out and "1 more to go" in out
	assert "1/2" in out                       # the promoted fact is not waiting for anything
	assert "one review saw this" not in out


def test_drafts_screen_sorts_a_pre_review_last_and_marks_it(screen, monkeypatch, st, tmp_path):
	"""A pre-review and the real review are one model on one diff, so it carries no count."""
	_waiting(monkeypatch, tmp_path)
	screen.getch, screen.timeout = _keys(ord("j"), 27), lambda t: None
	ui.drafts_screen(screen, st, 0)
	out = screen.text()
	assert "a pre-review found this" in out and "pre-review · one opinion" in out
	assert "seen 1×" not in out               # no count on this one


def test_drafts_screen_promotes_and_drops(screen, monkeypatch, st, tmp_path):
	memory = _waiting(monkeypatch, tmp_path)
	monkeypatch.setattr(ui.team, "push", lambda m: None)
	monkeypatch.setattr(ui.team, "push_dir", lambda d, m, l="sync": None)
	screen.getch, screen.timeout = _keys(ord("t"), 27), lambda t: None
	ui.drafts_screen(screen, st, 0)
	assert "still only a guess" in memory._facts(memory.path("a/b"))   # accepted by hand
	screen.getch = _keys(ord("x"), 27)
	ui.drafts_screen(screen, st, 0)
	assert memory.waiting() == []                                      # the pre-review one dropped


def test_drafts_screen_says_so_when_nothing_is_waiting(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "empty"))
	screen.getch, screen.timeout = _keys(ord(" ")), lambda t: None
	ui.drafts_screen(screen, st, 0)
	assert "nothing waiting" in screen.text()


def test_a_panels_right_value_never_lands_on_its_own_label(screen):
	"""panel() sized itself on the LEFT texts only, so a long right value overwrote the label.

	It showed up as "me/weekenpre-review · waits…" in the drafts screen. Every panel with a right
	column was one long value away from it; the share screen escaped only because its marks are short.
	"""
	ui.panel(screen, "t", [("me/weekend", "pre-review · waits for a real review to confirm it")], "f")
	out = screen.text()
	assert "me/weekend" in out and "pre-review · waits for a real review to confirm it" in out

	# and the bound holds even when the box cannot grow: the label survives, the value is dropped
	screen.erase()
	screen.w = 44
	ui.panel(screen, "t", [("me/weekend", "x" * 60)], "f")
	assert "me/weekend" in screen.text()


def test_a_panel_never_writes_past_the_bottom_of_a_short_terminal():
	"""top floored at 1 but the bottom row is top + 4 + len(lines). The drafts screen feeds panel()
	model-authored text of unbounded length, which is the caller most likely to find it."""
	# ponytail: from h=2 and w=4, not from h=8. The first version of this swept 8..32 and passed while
	# h<=6 still wrote its bottom border off the screen — the sweep's floor was the bug's hiding place.
	for n in (0, 1, 14, 40):
		body = [(f"line {i} of a long wrapped fact", "") for i in range(n)]
		for h in range(2, 34):
			for w in range(4, 60):
				scr = FakeScr(h=h, w=w)
				ui.panel(scr, "waiting", body, "[t] accept   [x] drop   [esc] close")  # must not raise


def test_drafts_screen_pushes_both_the_fact_and_its_evidence(screen, monkeypatch, st, tmp_path):
	"""Promotion writes YOUR memory dir and the team's pool — two checkouts, two pushes. Mocking both
	to lambda: None proved the keypress ran, not that either landed anywhere."""
	memory = _waiting(monkeypatch, tmp_path)
	pushes = []
	monkeypatch.setattr(ui.team, "push", lambda m: pushes.append(("team", m)))
	monkeypatch.setattr(ui.team, "push_dir", lambda d, m, l="sync": pushes.append(("mine", d, m)))
	screen.getch, screen.timeout = _keys(ord("t"), 27), lambda t: None
	ui.drafts_screen(screen, st, 0)
	assert [p[0] for p in pushes] == ["mine", "team"]
	assert pushes[0][1] == config.MEMORY_DIR          # yours, by path, not "whatever push_dir defaults to"
	assert "accepted" in pushes[0][2] and "a/b" in pushes[0][2]
	assert "evidence" in pushes[1][1]


def test_finished_review_row_stops_spinning():
	"""c873852 unioned verdicts into `busy` so a reviewed draft stays visible — but `busy` also drove the
	spinner, the elapsed counter and the agent count. A finished PR read "⠋ ✓ approved… 0s" forever."""
	st = State(0)
	pr = dict(PR, url="u", section="MINE")
	st.sections = [("MINE", [pr], None)]
	st.reviews["u"] = "✓ approved"  # done: not in running, no started_at
	ui.C = lambda n: 0
	scr = FakeScr()
	ui.draw(scr, st, 0, now=1000.0)
	row = next(scr.line(y) for y in range(scr.h) if "✓ approved" in scr.line(y))
	assert "approved…" not in row and "0s" not in row and "⠋" not in row, row
	assert "0 agents running" in scr.text()


def test_t_can_still_join_once_you_are_already_in_a_team(screen, monkeypatch, st, tmp_path):
	"""It used to return after offering to LEAVE, so a second team could not be joined from anywhere —
	the store, the resolution and the log all handled several while no surface could produce one."""
	from dashy.core import team
	a_team(monkeypatch, tmp_path, "org-one")
	asked, joined = [], []
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: (asked.append(prompt), "org-two")[1])
	monkeypatch.setattr(ui.team, "setup", lambda repo, create=False: joined.append(repo) or "")
	screen.getch, screen.timeout = _keys(ord("a"), 27), lambda t: None
	ui.team_setup(screen, st, 0)
	assert joined == ["org-two"]                       # the join path is reachable
	assert "Existing team" in asked[0]
	assert "org-one" in screen.text() and "1 joined" in screen.text()


def test_t_can_start_a_team_that_does_not_exist_anywhere_yet(screen, monkeypatch, st, tmp_path):
	"""Every other path CLONES something that already exists, so the first person on a team was stuck."""
	from dashy.core import team
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	answers, started = iter(["NeoMedSys review memory", "what we build", ""]), []
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: next(answers))
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: True)
	monkeypatch.setattr(ui.team, "start", lambda name, desc="", at="": started.append((name, desc, at)) or "")
	screen.getch, screen.timeout = _keys(ord("n"), 27), lambda t: None
	ui.team_setup(screen, st, 0)
	assert started == [("NeoMedSys review memory", "what we build", "")]
	assert "none yet" in screen.text()          # and the empty state still offers both routes
	assert "[n] start" in screen.text() and "[a] join" in screen.text()


def test_t_names_the_team_it_is_about_to_delete(screen, monkeypatch, st, tmp_path):
	from dashy.core import knowledge
	a_team(monkeypatch, tmp_path, "org-one")
	(tmp_path / "teams" / "org-two" / ".git").mkdir(parents=True)
	prompts, left = [], []
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: prompts.append(prompt) or True)
	monkeypatch.setattr(ui.knowledge, "leave", lambda slug: left.append(slug) or "")
	screen.getch, screen.timeout = _keys(ord("2"), ord("x"), 27), lambda t: None   # 2 = org-two, then leave
	ui.team_setup(screen, st, 0)
	assert left == ["org-two"]
	assert "team org-two" in prompts[0] and "are deleted" in prompts[0]   # and what leaving removes


def test_no_function_has_code_after_it_returns():
	"""Editing by slice leaves the tail of the old body behind, and Python parses it happily.

	This has now happened three times in this codebase — a dedent that swallowed loop() into a class, an
	anchor that re-indented 380 lines, and a setup() replacement that cut at the first `return` and left
	30 lines of the previous implementation underneath it. Every one parsed, imported and stayed green,
	because a test suite can only see lines that run. Unreachable code is the signature.
	"""
	import ast, pathlib
	import dashy
	root = pathlib.Path(dashy.__file__).parent
	mods = sorted(root.rglob("*.py"))
	assert len(mods) >= 8, f"found only {len(mods)} modules under {root} — this test cannot pass vacuously"
	bad = []
	for f in mods:
		tree = ast.parse(f.read_text())
		for node in ast.walk(tree):
			if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
				continue
			body = node.body
			for i, st in enumerate(body[:-1]):
				if isinstance(st, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
					name = getattr(node, "name", "<module>")
					bad.append(f"{f.name}:{body[i + 1].lineno} unreachable in {name}() after line {st.lineno}")
	assert not bad, "code after a return:\n  " + "\n  ".join(bad)


def _two_teams(monkeypatch, tmp_path):
	from dashy.core import team
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	team.start("NeoMedSys Platform", "precision medicine")
	team.start("Acme Tools", "internal")
	return dict(PR, repository={"nameWithOwner": "NeoMedSys/neo-api", "name": "neo-api"})


def test_b_binds_the_selected_repo_from_the_dashboard(screen, monkeypatch, st, tmp_path):
	"""The TUI could READ a binding everywhere and write one nowhere, so a team started with T was
	inert until you went to a shell."""
	pr = _two_teams(monkeypatch, tmp_path)
	screen.getch, screen.timeout = _keys(ord("2"), 27), lambda t: None
	ui.bind_screen(screen, st, 0, pr)
	assert bind.of("NeoMedSys/neo-api") == "neomedsys-platform"
	assert "Acme Tools" in screen.text() and "NeoMedSys Platform" in screen.text()


def test_b_can_bind_a_whole_owner_and_says_when_a_rule_is_what_matched(screen, monkeypatch, st, tmp_path):
	"""Fifteen repos under one org is fifteen keypresses otherwise, and one more per repo added later."""
	pr = _two_teams(monkeypatch, tmp_path)
	screen.getch, screen.timeout = _keys(ord("o"), ord("1"), 27), lambda t: None
	ui.bind_screen(screen, st, 0, pr)
	assert bind.owners() == {"neomedsys": "acme-tools"} and bind.bindings() == {}
	assert bind.why("NeoMedSys/neo-api") == ("owner", "acme-tools")
	screen.erase()
	screen.getch = _keys(27)
	ui.bind_screen(screen, st, 0, pr)
	assert "via neomedsys/*" in screen.text()   # or the key would look like the repo's own binding


def test_b_unbinds_and_that_beats_an_owner_rule(screen, monkeypatch, st, tmp_path):
	pr = _two_teams(monkeypatch, tmp_path)
	bind.bind_owner("neomedsys", "acme-tools")
	screen.getch, screen.timeout = _keys(ord("x"), 27), lambda t: None
	ui.bind_screen(screen, st, 0, pr)
	assert bind.of("NeoMedSys/neo-api") == ""          # excluded from the rule, which is the point
	assert bind.owners() == {"neomedsys": "acme-tools"}  # and the rule still covers everything else


def test_b_says_so_when_there_is_no_team_or_no_row(screen, monkeypatch, st, tmp_path):
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "none"))
	said = []
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	ui.bind_screen(screen, st, 0, dict(PR))
	ui.bind_screen(screen, st, 0, None)
	assert "no teams yet" in said[0] and "no row selected" in said[1]


def test_t_edits_the_team_brief_every_review_reads(screen, monkeypatch, st, tmp_path):
	"""n/g edit YOUR memory; nothing edited the team's brief, which is the file that reaches the model
	for every repo bound to it. `gitdashy setup` was the only way, and that is not the TUI."""
	from dashy.core import memory, team
	_two_teams(monkeypatch, tmp_path)
	opened, pushed = [], []
	monkeypatch.setattr(ui, "shell_out", lambda scr, cmd: opened.append(cmd[-1]) or "")
	monkeypatch.setattr(ui.team, "push_dir", lambda d, m, l="sync": pushed.append((d, m)))
	screen.getch, screen.timeout = _keys(ord("1"), ord("e"), 27, 27), lambda t: None   # 1 = acme-tools
	ui.team_setup(screen, st, 0)
	assert opened == [os.path.join(team.dir_of("acme-tools"), "memory", memory.PROJECT)]
	assert os.path.exists(opened[0])                    # seeded with a template, not left missing
	assert pushed and pushed[0][0] == team.dir_of("acme-tools")   # and pushed: it is the team's file


def test_t_changes_what_a_team_says_it_is(screen, monkeypatch, st, tmp_path):
	from dashy.core import team
	_two_teams(monkeypatch, tmp_path)
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "now for the platform work")
	monkeypatch.setattr(ui.team, "push_dir", lambda d, m, l="sync": None)
	screen.getch, screen.timeout = _keys(ord("1"), ord("d"), 27, 27), lambda t: None   # 1 = acme-tools
	ui.team_setup(screen, st, 0)
	it = team.info("acme-tools")
	assert it["description"] == "now for the platform work"
	assert it["name"] == "Acme Tools"     # the NAME is the key's origin and is not touched by this


def test_a_completed_migration_is_announced_not_shown_as_an_error(screen, monkeypatch, tmp_path):
	"""team.ERROR is painted with the err attribute, so a move that WORKED showed up red. It is also a
	move of the user's files done without being asked, so it gets said out loud once."""
	from dashy.core import team
	said = []
	monkeypatch.setattr(ui, "init_colors", lambda: None)
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	monkeypatch.setattr(ui.threading.Thread, "start", lambda self: None)
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(team, "ERROR", "")

    # a move that worked
	monkeypatch.setattr(team, "migrate", lambda: "gitdashy: moved your team checkout to /x, and repointed 2 bindings")
	screen.getch, screen.timeout = _keys(ord("q")), lambda t: None
	ui.main(screen, 60, False, "opus")
	assert said and "moved your team checkout" in said[0]
	assert team.ERROR == ""                      # not red, and not on the row

	# a move that refused stays on the row, where it will be read again
	said.clear()
	monkeypatch.setattr(team, "migrate", lambda: "gitdashy: /x has 2 unpushed reviews — push them, then restart")
	screen.getch = _keys(ord("q"))
	ui.main(screen, 60, False, "opus")
	assert "unpushed reviews" in team.ERROR and not said


def test_a_join_that_could_not_publish_says_so_on_screen(screen, monkeypatch, st, tmp_path):
	"""setup() returns "" for a join that worked but could not push, which is right for "did you join"
	— and left the join silent: the only sign was a clipped line on the T row that the next successful
	pull clears."""
	from dashy.core import team
	a_team(monkeypatch, tmp_path, "org-one")
	said = []
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "somewhere/repo.git")
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	monkeypatch.setattr(ui.team, "setup", lambda repo, name="": "")
	monkeypatch.setattr(ui.team, "ERROR", "join: remote rejected the push")
	ui._join_team(screen, st, 0)
	assert said and "could not publish" in said[0] and "remote rejected" in said[0]
	assert st.wake.is_set()          # and it still counts as joined: REVIEWED reloads

	# a clean join says nothing
	said.clear()
	st.wake.clear()
	monkeypatch.setattr(ui.team, "ERROR", "")
	ui._join_team(screen, st, 0)
	assert said == [] and st.wake.is_set()


def test_launch_retires_the_team_link_and_says_so_once(screen, monkeypatch):
	"""Nothing re-runs install after an update, so the migration runs at launch like team.migrate()
	does. Only a CHANGE earns a keypress; a hand-wired import it cannot touch is a standing note."""
	from dashy.core import install
	said = []
	monkeypatch.setattr(ui, "init_colors", lambda: None)
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	monkeypatch.setattr(ui.threading.Thread, "start", lambda self: None)
	monkeypatch.setattr(ui.team, "activate", lambda: None)
	monkeypatch.setattr(ui.team, "migrate", lambda: "")
	monkeypatch.setattr(config, "SETTINGS", "")

	monkeypatch.setattr(install, "retire", lambda: ["retire ~/.claude/prs-team — a team's facts reach a session through its repo's mirror now"])
	screen.getch, screen.timeout = _keys(ord("q")), lambda t: None
	ui.main(screen, 60, False, "opus")
	assert len(said) == 1 and "retire" in said[0]

	said.clear()
	monkeypatch.setattr(install, "retire", lambda: ["NOTE  ~/.claude/CLAUDE.md still imports @prs-team/… by hand"])
	screen.getch = _keys(ord("q"))
	ui.main(screen, 60, False, "opus")
	assert said == []                                                  # not a nag on every launch

	monkeypatch.setattr(install, "retire", lambda: [])
	screen.getch = _keys(ord("q"))
	ui.main(screen, 60, False, "opus")
	assert said == []


def test_launch_really_retires_a_stale_link_and_is_silent_the_second_time(screen, monkeypatch, tmp_path):
	"""The other launch test replaces retire() wholesale, so the migration is only proven against a fake.

	This one runs the real thing against a real stale link, which is the only way the wiring between
	the launch path and stale_team_link() is checked at all.
	"""
	from dashy import config
	from dashy.core import install
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	teams = tmp_path / "teams"
	(teams / "acme" / "memory").mkdir(parents=True)
	os.symlink(str(teams / "acme" / "memory"), str(cfg / "prs-team"))
	monkeypatch.setattr(config, "TEAMS", str(teams))
	monkeypatch.setattr(config, "TEAM", "")
	said = []
	monkeypatch.setattr(ui, "init_colors", lambda: None)
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	monkeypatch.setattr(ui.threading.Thread, "start", lambda self: None)
	monkeypatch.setattr(ui.team, "activate", lambda: None)
	monkeypatch.setattr(ui.team, "migrate", lambda: "")
	monkeypatch.setattr(config, "SETTINGS", "")

	screen.getch, screen.timeout = _keys(ord("q")), lambda t: None
	ui.main(screen, 60, False, "opus")
	assert len(said) == 1 and "retire" in said[0], said
	assert not os.path.lexists(str(cfg / "prs-team"))   # the link is actually gone

	said.clear()
	screen.getch = _keys(ord("q"))
	ui.main(screen, 60, False, "opus")
	assert said == []                                   # idempotent: nothing left to say


DIFF_FIXTURE = """diff --git a/gitdashy/auto.py b/gitdashy/auto.py
--- a/gitdashy/auto.py
+++ b/gitdashy/auto.py
@@ -136,7 +136,12 @@ class Auto:
     def _sweep(self, prs):
         for pr in prs:
-            self.in_flight.discard(pr.id)
+            self.in_flight.discard(pr.id)
+            verdict = self._verdict_for(pr)
             self.persist_verdict(pr, verdict)
diff --git a/CHANGELOG.md b/CHANGELOG.md
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,4 +1,5 @@
 # Changelog
+## 1.34.0
 ## 1.33.2
"""


def _prime(st, pr):
	"""Drive want_diff to completion, the way the dashboard does across successive draws.

	ponytail: the pane fetches OFF the draw thread, so a test that draws once sees "reading the diff…".
	Waiting here rather than stubbing want_diff keeps the threading in the path under test — it is
	where the freeze was.
	"""
	findings = ui.log.findings(pr.get("review"))
	for _ in range(300):
		if st.want_diff("a/b", pr["number"], "", findings) is not None:
			return
		time.sleep(0.01)
	raise AssertionError("the diff never landed")


def _code_pr(monkeypatch, st, findings=None, text=DIFF_FIXTURE):
	from dashy.core import diff
	monkeypatch.setattr(diff, "_CACHE", {("a/b", 23, ""): text})
	pr = dict(PR, number=23, url="u23", repository={"nameWithOwner": "a/b", "name": "git-dashy"},
	          review={"verdict": "approve", "at": "x", "model": "opus", "body": "b",
	                  "findings": findings if findings is not None else [
	                      {"kind": "blocking", "loc": "auto.py:139", "text": "the discard runs before the verdict is written"},
	                      {"kind": "note", "loc": "CHANGELOG.md:2", "text": "entry missing the version bump"}]})
	monkeypatch.setattr(st, "want_detail", lambda p: {})
	st.sections = [("MINE", [pr], None)]
	st.pane_tab = "code"
	st.code_pr = pr["url"]   # the fixture stands for "already looking at this one"; see the reset test
	_prime(st, pr)
	return pr


# ponytail: x0 + width must fit the screen — at() bounds by the PANE, and draw() always derives the
# pane from the terminal width, so a test that asks for a pane wider than its screen is asking for
# something production cannot produce.
PANE_X, PANE_W = 40, 90


def test_the_code_tab_puts_each_finding_on_the_line_it_names(screen, monkeypatch, st):
	"""A finding that cites a line you then have to go and find somewhere else is one nobody follows."""
	pr = _code_pr(monkeypatch, st)
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 30, PANE_X, PANE_W, pr)
	out = screen.text()
	assert "1 summary" in out and "2 code" in out
	assert "gitdashy/auto.py" in out
	assert "verdict = self._verdict_for(pr)" in out              # the line
	assert "blocking" in out and "the discard runs before" in out  # its finding, under it
	assert "◆" in out                                            # and a mark in the gutter


def test_marks_only_hides_what_the_review_did_not_mark(screen, monkeypatch, st):
	pr = _code_pr(monkeypatch, st, findings=[{"kind": "blocking", "loc": "auto.py:139", "text": "x"}])
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 30, PANE_X, PANE_W, pr)
	assert "CHANGELOG" not in screen.text()      # unmarked, so it is not in the way
	screen.erase()
	st.code_scope = "diff"
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 30, PANE_X, PANE_W, pr)
	out = screen.text()
	assert "CHANGELOG.md" in out                  # D shows the whole change
	assert "blocking" not in out                  # and the notes step aside; the marks stay


def test_n_moves_between_marks_and_between_files(screen, monkeypatch, st):
	"""The strip used to move a variable nothing read — the pane drew from the top whatever it said."""
	pr = _code_pr(monkeypatch, st)
	st.code_at = 1
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 22, PANE_X, PANE_W, pr)
	assert "entry missing the version bump" in screen.text()   # scrolled to the second mark
	screen.erase()
	st.code_scope, st.code_at = "diff", 1
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 22, PANE_X, PANE_W, pr)
	out = screen.text()
	assert "CHANGELOG.md" in out and "n/N file" in out          # and to the second FILE


def test_a_finding_about_a_file_outside_the_diff_is_still_shown(screen, monkeypatch, st):
	"""The pane must not be quieter than the summary it replaces."""
	pr = _code_pr(monkeypatch, st, findings=[{"kind": "note", "loc": "gone/away.py:9", "text": "missing"}])
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 30, PANE_X, PANE_W, pr)
	assert "not in this diff" in screen.text() and "gone/away.py:9" in screen.text()


def test_the_code_tab_says_why_it_is_empty(screen, monkeypatch, st):
	pr = _code_pr(monkeypatch, st, text="")
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 30, PANE_X, PANE_W, pr)
	assert "no diff to show" in screen.text()      # not an empty pane you press 2 at again
	screen.erase()
	pr2 = dict(pr); pr2.pop("review")
	monkeypatch.setattr(ui.log, "last", lambda url: None)
	ui.detail(screen, st, 30, PANE_X, PANE_W, pr2)
	assert "no review yet" in screen.text()


def test_the_code_pane_never_writes_outside_itself(screen, monkeypatch, st):
	"""Every width and height, including the ones where the pane has almost no room."""
	pr = _code_pr(monkeypatch, st)
	for scope in ("marks", "diff"):
		st.code_scope = scope
		for h in range(8, 34):
			for w in range(30, 120, 7):
				scr = FakeScr(h=h, w=PANE_X + w + 2)
				ui.detail(scr, st, h, PANE_X, w, pr)   # must not raise


# ponytail: these drive main()'s KEY DISPATCH, not the pane. Every other code-tab test reaches into
# st.code_scope / st.code_at directly, which is exactly why D and n could be swallowed by the global
# handlers above them and still look tested: the pane was right, and nothing could reach it.
def _drive(screen, monkeypatch, keys, tab="summary", review=True):
	"""Run main() over a key sequence and hand back the State it built."""
	box, real = {}, ui.State
	rev = {"verdict": "approve", "at": "x", "model": "opus", "body": "b",
	       "findings": [{"kind": "note", "loc": "a.py:1", "text": "t"}]}
	def make(interval, model=None):
		st = box["st"] = real(interval, model)
		pr = dict(PR, number=23, url="u23", repository={"nameWithOwner": "a/b", "name": "git-dashy"})
		if review:
			pr["review"] = rev
		else:
			pr.pop("review", None)
			monkeypatch.setattr(ui.log, "last", lambda url: None)
		st.sections = [("MINE", [pr], None)]
		st.pane, st.pane_tab = True, tab
		return st
	monkeypatch.setattr(ui, "State", make)
	monkeypatch.setattr(ui, "init_colors", lambda: None)
	monkeypatch.setattr(ui.team, "activate", lambda: None)
	monkeypatch.setattr(ui.team, "migrate", lambda: "")
	monkeypatch.setattr(ui.threading.Thread, "start", lambda self: None)
	monkeypatch.setattr(config, "SETTINGS", "")
	screen.getch, screen.timeout = _keys(*keys, ord("q")), lambda t: None
	ui.main(screen, 60, False, "opus")
	return box["st"]


def test_the_code_tab_keys_are_not_swallowed_by_the_global_ones(screen, monkeypatch):
	"""D and n are each bound twice. An elif chain gives the key to the FIRST branch, and this was last."""
	edited = []
	monkeypatch.setattr(ui, "edit_memory", lambda *a: edited.append(a))
	st = _drive(screen, monkeypatch, [ord("2"), ord("D"), ord("c"), ord("n")])
	assert st.pane_tab == "code"
	assert st.code_scope == "diff"        # D reached the pane
	assert st.drafts is False             # and did NOT toggle the drafts view on the way
	assert st.code_context == 8           # c stepped the ring
	assert st.code_at == 1                # n moved the jump
	assert edited == []                   # and did not open $EDITOR over the top of the dashboard


def test_the_global_D_and_n_still_work_on_the_summary_tab(screen, monkeypatch):
	"""Hoisting the code branch must not take the keys away from the handlers that owned them."""
	edited = []
	monkeypatch.setattr(ui, "edit_memory", lambda scr, st, sel, repo: edited.append(repo))
	st = _drive(screen, monkeypatch, [ord("D"), ord("n")])
	assert st.drafts is True and edited == ["a/b"]
	assert st.code_scope == "marks" and st.code_at == 0   # the pane was not touched


def test_tab_and_the_number_keys_move_between_the_two_faces(screen, monkeypatch):
	st = _drive(screen, monkeypatch, [ord("2")])
	assert st.pane_tab == "code"
	assert _drive(screen, monkeypatch, [ord("2"), ord("1")]).pane_tab == "summary"
	assert _drive(screen, monkeypatch, [9]).pane_tab == "code"
	assert _drive(screen, monkeypatch, [9, 9]).pane_tab == "summary"


def test_the_context_key_cycles_the_whole_ring(screen, monkeypatch):
	from dashy.core import diff
	seen = [_drive(screen, monkeypatch, [ord("2")] + [ord("c")] * n).code_context
	        for n in range(len(diff.CONTEXTS) + 1)]
	assert seen == diff.CONTEXTS + [diff.CONTEXTS[0]]   # steps every value and comes home


def test_every_colour_pair_is_defined_once():
	"""init_colors calls init_pair in order, so a repeat silently repaints the earlier one's meaning."""
	nums = [row[0] for row in ui.COLORS]
	dupes = sorted({n for n in nums if nums.count(n) > 1})
	assert not dupes, f"colour pairs defined twice: {dupes}"


def test_an_unknown_finding_kind_does_not_take_the_pane_down(screen, monkeypatch, st):
	"""log.KINDS decides what a finding may be; adding a row must change how it LOOKS, not whether it runs."""
	monkeypatch.setitem(ui.log.KINDS, "wildcard", "dim")
	pr = _code_pr(monkeypatch, st, findings=[{"kind": "wildcard", "loc": "auto.py:139", "text": "a new kind"}])
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 30, PANE_X, PANE_W, pr)     # must not raise out of draw()
	assert "a new kind" in screen.text()


def test_moving_to_another_pr_resets_the_mark_jump(screen, monkeypatch, st):
	"""code_at counts THIS review's marks; carrying it over landed you on mark 5 of a review with two."""
	pr = _code_pr(monkeypatch, st)
	st.code_at = 1
	screen.w = PANE_X + PANE_W + 2
	ui.detail(screen, st, 30, PANE_X, PANE_W, dict(pr, url="another"))
	assert st.code_at == 0 and st.code_pr == "another"


def test_the_pane_never_runs_gh_on_the_draw_thread(screen, monkeypatch, st):
	"""`gh pr diff` ran inside code_pane, and draw() is called twenty times a second."""
	import subprocess as sp
	import threading as th
	from dashy.core import diff
	where = []
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(sp, "run", lambda cmd, **k: where.append(th.current_thread())
	                    or sp.CompletedProcess(cmd, 0, DIFF_FIXTURE, ""))
	pr = dict(PR, number=99, url="u99", repository={"nameWithOwner": "a/b", "name": "git-dashy"},
	          review={"verdict": "approve", "at": "x", "model": "opus", "body": "b",
	                  "findings": [{"kind": "note", "loc": "auto.py:139", "text": "t"}]})
	monkeypatch.setattr(st, "want_detail", lambda p: {})
	st.sections, st.pane_tab = [("MINE", [pr], None)], "code"
	screen.w = PANE_X + PANE_W + 2

	ui.detail(screen, st, 30, PANE_X, PANE_W, pr)      # one draw: it must return without shelling out
	assert "reading the diff" in screen.text()
	for _ in range(400):
		if where:
			break
		time.sleep(0.005)
	assert where and all(t is not th.main_thread() for t in where)


def test_every_mark_gets_a_row_and_the_chips_agree_with_the_jumps(screen, monkeypatch, st):
	"""chips enumerated marks while jump counted note rows — one orphan and every chip after it lied."""
	from dashy.core import diff
	pr = _code_pr(monkeypatch, st, findings=[
		{"kind": "blocking", "loc": "auto.py:139", "text": "this one is on a line"},
		{"kind": "note", "loc": "gone/away.py:9", "text": "no such file in the diff"},
		{"kind": "nit", "loc": "auto.py:9999", "text": "right file wrong line"}])
	files = diff.parse(DIFF_FIXTURE)
	marks = diff.anchor(files, ui.log.findings(pr["review"]))
	rows = ui.code_rows(files, marks, True)
	anchors = [v for k, v in rows if k in ("note", "orphan")]
	assert len(anchors) == len(marks) == 3          # every mark is reachable, none counted twice

	# ponytail: each mark in turn, because the pane scrolls to the one n is on — "all three on screen"
	# would be a claim about the window, and the claim under test is that each one is REACHABLE.
	# order is anchor.marks' order: on a line, then the same file's unmatched line, then no file at all
	for i, want in enumerate(["this one is on a line", "right file wrong line", "no such file in the diff"]):
		st.code_at = i
		scr = FakeScr(h=34, w=PANE_X + PANE_W + 2)
		ui.detail(scr, st, 34, PANE_X, PANE_W, pr)
		out = scr.text()
		assert want in out, (i, want)
		assert st.code_at == i                       # n reaches it; it is not clamped away
	assert "not in this diff" in out                  # and each says which kind of miss it is


def test_the_code_tab_gives_its_keys_back_when_it_has_nothing_to_show(screen, monkeypatch):
	"""On a row with no review the tab draws "no review yet"; D must still reach the drafts toggle."""
	st = _drive(screen, monkeypatch, [ord("2"), ord("D")], review=False)
	assert st.pane_tab == "code"
	assert st.drafts is True and st.code_scope == "marks"


def test_f_clears_the_diffs_gh_failed_on(screen, monkeypatch):
	"""The key that means "go and look again" has to reach the diff cache, not only the PR list."""
	from dashy.core import diff
	hit = []
	monkeypatch.setattr(diff, "retry", lambda: hit.append(1))
	_drive(screen, monkeypatch, [ord("f")])
	assert hit == [1]


def test_a_file_only_finding_is_reachable_in_the_code_tab(screen, monkeypatch, st):
	"""The empty-scope guard hid exactly the marks code_rows' orphan branch exists to emit.

	It asked `not shown and not any(m["file"] is None ...)`. A mark whose FILE matched but whose LINE
	did not is in neither set — a file-only loc, which the finding schema explicitly allows, or a line
	the diff does not carry. So the pane said "the review marked nothing" and the finding could not be
	reached at all, contradicting anchor()'s promise that a finding landing nowhere is kept.
	"""
	pr = _code_pr(monkeypatch, st, findings=[
		{"kind": "blocking", "loc": "auto.py", "text": "the whole file is the problem"}])
	st.code_at = 0
	scr = FakeScr(h=34, w=PANE_X + PANE_W + 2)
	ui.detail(scr, st, 34, PANE_X, PANE_W, pr)
	out = scr.text()
	assert "the whole file is the problem" in out, out
	assert "the review marked nothing" not in out, out


def test_a_finding_on_a_line_the_diff_does_not_carry_is_reachable_too(screen, monkeypatch, st):
	"""The other half of the same hole: right file, a line outside every hunk."""
	pr = _code_pr(monkeypatch, st, findings=[
		{"kind": "note", "loc": "auto.py:9999", "text": "right file wrong line"}])
	st.code_at = 0
	scr = FakeScr(h=34, w=PANE_X + PANE_W + 2)
	ui.detail(scr, st, 34, PANE_X, PANE_W, pr)
	out = scr.text()
	assert "right file wrong line" in out, out
	assert "the review marked nothing" not in out, out


def test_the_pane_still_says_so_when_the_review_really_marked_nothing(screen, monkeypatch, st):
	"""The guard must not have been turned off — with NO findings at all it still explains itself."""
	pr = _code_pr(monkeypatch, st, findings=[])
	scr = FakeScr(h=34, w=PANE_X + PANE_W + 2)
	ui.detail(scr, st, 34, PANE_X, PANE_W, pr)
	assert "the review marked nothing" in scr.text()


def test_the_sticky_path_names_the_file_the_window_is_inside(screen, monkeypatch, st):
	"""Untested: no assertion anywhere covered "↑ in" or which path it showed.

	Scrolled past a file's own header, the pane has to keep saying which file you are reading, or a
	long diff becomes a wall of lines with no way to tell where you are.

	ponytail: SCOPED and SHORT, which is the only combination that can produce this row. In full scope
	n/N moves by file, so the window always opens on a file header; and in any pane tall enough to hold
	the whole narrowed diff the window is clamped to the top, header included. The row exists for a pane
	shorter than what it is showing, so the test has to ask for one — at h=34 it correctly never appears.
	"""
	pr = _code_pr(monkeypatch, st, findings=[
		{"kind": "note", "loc": "auto.py:139", "text": "somewhere in the middle"}])
	assert st.code_scope == "marks" and st.code_context == 3
	st.code_at = 0
	scr = FakeScr(h=22, w=PANE_X + PANE_W + 2)
	ui.detail(scr, st, 22, PANE_X, PANE_W, pr)
	out = scr.text()
	assert "↑ in" in out, out
	assert "gitdashy/auto.py" in out, out       # the path it names, not merely that it named one
	assert "somewhere in the middle" in out     # and the mark it scrolled to is still on screen


def _know_rows(state):
	"""The Knowledge group's rows, wherever that group sits."""
	return next(rows for _, key, rows in ui.header_groups(state) if key == "K")


def test_every_session_note_is_reachable_and_none_lengthens_the_memory_row(screen, monkeypatch, st):
	"""Notes used to be glued onto Memory's value, then clipped to 40 — and both were wrong.

	Glued on, two notes added ~80 characters to one value and the header degradation loop folds K to a
	chip before it drops anything else, so having a note made the Knowledge group VANISH rather than say
	anything. Clipped to 40, the first note renders at exactly 40 characters, so the SECOND was dropped
	whole — and a note is precisely the thing that must not go missing quietly. As rows they cost the
	header nothing once K is a chip, and group_menu lists them under it.
	"""
	from dashy.core import install
	monkeypatch.setattr(install, "session_notes", lambda: [])
	bare = _know_rows(st)
	monkeypatch.setattr(install, "session_notes",
	                    lambda: ["corpus never says `gitdashy remember`", "CLAUDE.md imports @prs-team by hand"])
	noted = _know_rows(st)

	memory_bare = next(v for _, n, v, _ in bare if n == "Memory")
	memory_noted = next(v for _, n, v, _ in noted if n == "Memory")
	assert memory_noted == memory_bare              # the Memory value is untouched by a note

	values = [v for _, n, v, _ in noted if n == "Note"]
	assert len(values) == 2, noted                  # BOTH, not just the one that fitted in 40 chars
	assert "corpus never says" in values[0] and "imports @prs-team" in values[1]
	assert all(k == "" for k, n, _, _ in noted if n == "Note")   # inert on Enter


def _popup_inner(state):
	"""The width group_menu's popup would ask for. Exactly popup()'s own arithmetic, at screen.py:923."""
	rows = _know_rows(state)
	name_w = max(len(name) for _, name, _, _ in rows)
	lines = [f"{name.ljust(name_w)}   {value}" for _, name, value, _ in rows]
	return max([len(l) for l in lines] + [len("Knowledge")]) + 6


def test_a_session_note_does_not_widen_the_k_popup(screen, monkeypatch, st):
	"""popup() clamps x but never inner, so its width is decided entirely by its longest line.

	ponytail: asserted as a DELTA against the same popup with no notes, not against a fixed column
	count. The absolute width is set by the Memory and Store rows, which carry filesystem paths — under
	pytest those are long tmp_path strings, so an absolute bound would fail on the fixture rather than
	on the thing under test, and pass or fail by how deep the temp directory happened to be.
	ponytail: and asserted on INNER, the number popup() computes. The first version of this test
	rendered into a FakeScr and asserted every row was <= w, which cannot fail: FakeScr's addnstr
	silently drops anything past its right edge and line() slices to w, so it was true by construction
	and passed with the clip removed entirely.
	"""
	from dashy.core import install
	monkeypatch.setattr(install, "session_notes", lambda: [])
	bare = _popup_inner(st)
	monkeypatch.setattr(install, "session_notes",
	                    lambda: ["corpus never says `gitdashy remember`", "CLAUDE.md imports @prs-team by hand"])
	noted = _popup_inner(st)
	# ponytail: a note is its own ROW, so it can only widen the popup if it is longer than the widest
	# row already there — the paths. Glued onto Memory's value it widened it by ~80 every time.
	assert noted == bare, (bare, noted)


def test_a_failed_refresh_says_so_in_the_header(screen):
	"""ponytail: the thread retries forever now instead of dying, so a refresh that cannot reach gh
	would otherwise be indistinguishable from a quiet one — the header would just keep counting up
	from the last good fetch. Same rule the Memory row follows: a net that is off says so."""
	screen.w = 210
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [dict(PR, url="m")], None)], time.time()
	ui.draw(screen, st, 0)
	assert "updated" in screen.text() and "refresh failed" not in screen.text()
	st.error = "gh: could not resolve host"
	ui.draw(screen, st, 0)
	out = screen.text()
	assert "✗ refresh failed: gh: could not resolve host" in out and "updated" not in out
	st.error = ""
	ui.draw(screen, st, 0)
	assert "refresh failed" not in screen.text()


def test_a_failed_refresh_drops_the_countdown_beside_it(screen):
	"""ponytail: fetched_at holds the last SUCCESSFUL fetch, so the countdown was computed off a
	deadline already in the past and rendered "next refresh 0s" next to "✗ refresh failed" — the two
	halves of one row contradicting each other."""
	screen.w = 210
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [dict(PR, url="m")], None)], time.time() - 600
	ui.draw(screen, st, 0)
	assert "next refresh" in screen.text()
	st.error = "gh: could not resolve host"
	ui.draw(screen, st, 0)
	out = screen.text()
	assert "refresh failed" in out and "next refresh" not in out


def test_a_first_refresh_that_failed_still_draws(screen):
	"""fetched_at is None, so the splash path and the countdown are both off — the error is all there is."""
	st = State(60)
	st.error = "gh: not logged in"
	ui.draw(screen, st, 0)
	assert "refresh failed: gh: not logged in" in screen.text()


def test_t_lists_teams_and_a_number_opens_that_teams_own_screen(screen, monkeypatch, st, tmp_path):
	"""Six letter keys on one panel, each starting a footer question that asked WHICH team by typed
	name, was the surface that adopted ~/dev/neomedsys. Now: a list, a number, and the verbs live on
	the team's own screen, which shows what the team has before offering anything."""
	from dashy.core import team
	_two_teams(monkeypatch, tmp_path)
	team.cover("acme-tools", "acme")
	subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(tmp_path / "acme-mem.git")], check=True)
	assert team.connect("acme-tools", str(tmp_path / "acme-mem.git")) == ""
	seen = []
	screen.getch, screen.timeout = _keys_seen(screen, seen, ord("1"), 27, 27), lambda t: None
	ui.team_setup(screen, st, 0)
	assert "1  Acme Tools" in seen[0] and "[1-8] open" in seen[0]     # the list
	out = seen[1]                                                      # the team's own screen
	assert "Acme Tools" in out and "acme-tools" in out
	assert str(tmp_path / "acme-mem.git") in out                      # the WHOLE remote, not its host
	assert ui.team.redacted("https://x:tok@github.com/o/r.git") == "https://github.com/o/r.git"
	assert ui._remote_label("git@github.com:o/r.git") == "o/r"       # the list shows owner/name
	assert "lives at" in out and "remote" in out and "declares" in out and "acme/*" in out
	assert "[e] brief" in out and "[o] cover" in out and "[x] leave" in out
	# ponytail: an empty row is a VALUE — the footer is where a key's meaning lives
	assert "nothing yet" in out and "covers an owner" not in out and "connects one" not in out


def test_t_start_asks_no_where_and_lands_on_the_new_team(screen, monkeypatch, st, tmp_path):
	"""The 'Where?' question is gone from the TUI: the checkout always lands under ~/.prs_teams, and a
	team kept elsewhere is `teams --new --at`, in a shell, on purpose."""
	from dashy.core import team
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	prompts, answers = [], iter(["NeoMedSys", "the NMS project"])
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: (prompts.append(prompt), next(answers))[1])
	seen = []
	screen.getch, screen.timeout = _keys_seen(screen, seen, ord("n"), 27, 27), lambda t: None
	ui.team_setup(screen, st, 0)
	assert team.joined() == ["neomedsys"]
	assert not any("Where" in p for p in prompts)
	assert os.path.realpath(team.dir_of("neomedsys")).startswith(str(tmp_path / "teams"))
	assert "NeoMedSys" in seen[1] and "lives at" in seen[1]   # lands on the team's screen, not the list


def test_t_start_from_a_row_offers_to_cover_its_owner(screen, monkeypatch, st, tmp_path):
	"""A team started with T was inert until someone found the bind key. Started from a PR row, it
	offers the one thing that makes it do something: cover that row's owner, here and for everyone."""
	from dashy.core import team
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	answers = iter(["NeoMedSys", ""])
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: next(answers))
	asked = []
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: asked.append(prompt) or True)
	current = dict(PR, repository={"nameWithOwner": "NeoMedSys/neo-api", "name": "neo-api"})
	screen.getch, screen.timeout = _keys(ord("n"), 27, 27), lambda t: None
	ui.team_setup(screen, st, 0, current)
	assert any("neomedsys/*" in p and "everyone who joins" in p for p in asked)
	assert bind.owners() == {"neomedsys": "neomedsys"}        # bound here
	assert team.covers("neomedsys") == ["neomedsys/*"]        # and declared in the team


def test_t_o_covers_an_owner_from_the_teams_screen(screen, monkeypatch, st, tmp_path):
	from dashy.core import team
	pr = _two_teams(monkeypatch, tmp_path)
	asked = []
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: asked.append(prompt) or True)
	# ponytail: o ASKS now, with the selected row's owner offered as the default; blank takes it.
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: asked.append(prompt) or "")
	screen.getch, screen.timeout = _keys(ord("2"), ord("o"), 27, 27), lambda t: None
	ui.team_setup(screen, st, 0, pr)                           # 2 = neomedsys-platform
	assert any("[neomedsys]" in p for p in asked)              # the default is shown
	assert any("neomedsys/*" in p for p in asked)
	assert team.covers("neomedsys-platform") == ["neomedsys/*"] and bind.owners() == {"neomedsys": "neomedsys-platform"}
	# with no row selected it asks for the owner instead
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "acme")
	screen.getch = _keys(ord("1"), ord("o"), 27, 27)
	ui.team_setup(screen, st, 0)
	assert team.covers("acme-tools") == ["acme/*"]


def test_t_letters_act_on_the_only_team_without_picking_it(screen, monkeypatch, st, tmp_path):
	"""One team joined is the common case; making it press 1 first would be ceremony."""
	from dashy.core import team
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	team.start("Only One")
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "now described")
	monkeypatch.setattr(ui.team, "push_dir", lambda d, m, l="sync": None)
	screen.getch, screen.timeout = _keys(ord("d"), 27), lambda t: None
	ui.team_setup(screen, st, 0)
	assert team.info("only-one")["description"] == "now described"


def test_the_team_panels_spawn_no_process_per_redraw(screen, monkeypatch, st, tmp_path):
	"""getch here inherits main's 50-500ms timeout, so both loops redraw several times a second, once
	per joined team. `git remote get-url` on that path is the waste has_remote's own comment names, and
	it does not go through _remote, so its 60s timeout is unbounded from a draw loop."""
	from dashy.core import team
	_two_teams(monkeypatch, tmp_path)
	spawned = []
	monkeypatch.setattr(team, "_url", lambda p: spawned.append(p) or "")
	screen.getch, screen.timeout = _keys(ord("1"), 27, 27), lambda t: None
	ui.team_setup(screen, st, 0, None)
	assert spawned == []
	# and it still reads the URL that is actually there
	remote = tmp_path / "r.git"
	subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
	assert team.connect("acme-tools", str(remote)) == ""
	assert team.origin_url(team.dir_of("acme-tools")) == str(remote)
	assert team.origin_url(str(tmp_path / "nothing")) == ""


def test_a_password_in_a_remote_never_reaches_the_panel():
	"""bare_url covers only http(s), and host_of needs a dot — so ssh://u:pw@host and a token against a
	dotless host both printed verbatim, one on each branch."""
	for url in ("https://x:tok@localhost/o/r.git", "ssh://u:pw@host/o/r", "https://x:tok@github.com/o/r.git"):
		assert "tok" not in ui._remote_label(url) and "pw" not in ui._remote_label(url), url
		assert "tok" not in ui.team.redacted(url) and "pw" not in ui.team.redacted(url), url
	assert ui.team.redacted("ssh://u:pw@host/o/r") == "ssh://host/o/r"
	assert ui._remote_label("git@github.com:o/r.git") == "o/r"          # nothing to strip, nothing lost


def test_cover_binds_here_before_it_publishes(screen, monkeypatch, st, tmp_path):
	"""team.cover writes AND pushes. If the local rule then fails, the team has published a claim over
	owner/* for everyone who joins while nothing is bound on the machine that asked for it."""
	from dashy.core import team
	_two_teams(monkeypatch, tmp_path)
	monkeypatch.setattr(ui.bind, "bind_owner", lambda o, k: "~/.prs_bindings is not writable")
	said = []
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	st.wake.clear()
	ui._cover(screen, st, 0, "acme-tools", "neomedsys")
	assert any("not writable" in p for p in said)
	assert team.covers("acme-tools") == []          # nothing published on a half-failure
	assert not st.wake.is_set()


def test_o_offers_the_rows_owner_as_a_default_and_takes_another(screen, monkeypatch, st, tmp_path):
	from dashy.core import team
	pr = _two_teams(monkeypatch, tmp_path)
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: True)
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "")      # blank takes the default
	screen.getch, screen.timeout = _keys(ord("1"), ord("o"), 27, 27), lambda t: None
	ui.team_setup(screen, st, 0, pr)
	assert team.covers("acme-tools") == ["neomedsys/*"]
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "someone-else")
	screen.getch = _keys(ord("1"), ord("o"), 27, 27)
	ui.team_setup(screen, st, 0, pr)
	assert team.covers("acme-tools") == ["neomedsys/*", "someone-else/*"]


def test_joining_lands_on_the_team_it_just_joined(screen, monkeypatch, st, tmp_path):
	"""Claimed in the body and untested, including the re-join case where nothing new appeared."""
	from dashy.core import team
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "somewhere/acme.git")
	def fake_setup(repo, name=""):
		(tmp_path / "teams" / "acme" / ".git").mkdir(parents=True, exist_ok=True)
		(tmp_path / "teams" / "acme" / "memory").mkdir(parents=True, exist_ok=True)
		return ""
	monkeypatch.setattr(ui.team, "setup", fake_setup)
	seen = []
	screen.getch, screen.timeout = _keys_seen(screen, seen, 27, 27), lambda t: None
	ui._join_team(screen, st, 0)
	assert "lives at" in seen[0] and "acme" in seen[0]      # the team's screen, not the list
	# joining one you are already in adds nothing, so there is no screen to land on
	seen.clear()
	screen.getch = _keys_seen(screen, seen, 27)
	ui._join_team(screen, st, 0)
	assert not seen or "lives at" not in seen[0]


def test_the_one_team_footer_survives_eighty_columns(monkeypatch, st, tmp_path):
	"""panel() clamps inner to w-4 and draws the footer at inner-6, so on an 80-column terminal a
	93-character footer is cut at 70 and its last two keys never appear — on the branch this whole
	change is built around, the one team everybody has."""
	from conftest import FakeScr
	scr = FakeScr(h=30, w=80)
	monkeypatch.setattr(ui, "C", lambda n: 0)
	_two_teams(monkeypatch, tmp_path)
	from dashy.core import team, knowledge
	assert knowledge.leave("neomedsys-platform") == ""      # leave one, so the single-team branch draws
	scr.getch, scr.timeout = _keys(27), lambda t: None
	ui.team_setup(scr, st, 0, None)
	out = scr.text()
	for key in ("[e]", "[d]", "[c]", "[o]", "[x]", "[n]", "[a]", "[esc]"):
		assert key in out, f"{key} fell off an 80-column footer"


def test_o_says_so_when_the_answer_is_not_an_owner(screen, monkeypatch, st, tmp_path):
	"""_pick_team had a ponytail for exactly this: a typo returning "" looks like a cancel, and the
	screen just comes back with nothing said."""
	from dashy.core import team
	pr = _two_teams(monkeypatch, tmp_path)
	said = []
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "acme/api")
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	screen.getch, screen.timeout = _keys(ord("1"), ord("o"), 27, 27), lambda t: None
	ui.team_setup(screen, st, 0, pr)
	assert any("not an owner" in p for p in said)
	assert team.covers("acme-tools") == []


def test_connect_never_echoes_the_url_it_was_given(screen, monkeypatch, st, tmp_path):
	"""Every drawn remote goes through redacted; the acknowledgement echoed what was typed."""
	_two_teams(monkeypatch, tmp_path)
	said = []
	monkeypatch.setattr(ui, "ask", lambda scr, s, sel, prompt: "https://x:ghp_SECRET@host.example/o/r.git")
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	monkeypatch.setattr(ui.team, "connect", lambda key, url: "")
	ui._connect_team(screen, st, 0, "acme-tools")
	assert said and "ghp_SECRET" not in said[0] and "host.example/o/r.git" in said[0]


def test_origin_url_asks_git_only_when_dot_git_is_a_file(monkeypatch, tmp_path):
	"""A .git that is a FILE is a worktree or a submodule and its config lives elsewhere. That fallback
	is the one path here that may spawn a process, and it was untested."""
	from dashy.core import team
	linked = tmp_path / "linked"
	linked.mkdir()
	(linked / ".git").write_text("gitdir: /somewhere/else\n")
	asked = []
	monkeypatch.setattr(team, "_url", lambda p: asked.append(p) or "git@host:o/r.git")
	assert team.origin_url(str(linked)) == "git@host:o/r.git"
	assert asked == [str(linked)]
	# and a plain directory with no .git at all asks nothing
	asked.clear()
	assert team.origin_url(str(tmp_path / "nothing")) == "" and asked == []
