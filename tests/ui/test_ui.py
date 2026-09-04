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
	assert "Reviewer   Model " + st.model in out and "Depth " + config.DEPTH in out and "View   Summaries all" in out and "Drafts shown" in out
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


def test_draw_survives_tiny_terminals(screen):
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [dict(PR)], None), ("REVIEWED", [], None)], time.time()
	for h in range(1, 12):
		for w in range(1, 40):
			screen.h, screen.w = h, w
			sel, cur = ui.draw(screen, st, 0)  # FakeScr asserts every addnstr lands on screen
			assert sel == 0 and cur["url"] == "u"
	screen.h, screen.w = 6, 120
	ui.draw(screen, st, 0)
	assert "▸" in screen.text() and "b#" in screen.line(4)  # one list row: the selected PR


def test_draw_prompt_replaces_footer(screen):
	st = State(60)
	ui.draw(screen, st, 0, prompt=" sure? [y/n]")
	assert screen.line(screen.h - 1).strip() == "sure? [y/n]"


def test_draw_truncates_long_title_on_narrow_screen(screen):
	screen.w = 40
	st = State(60)
	st.sections = [("MINE", [dict(PR, title="x" * 200), dict(PR, url="v", title="y" * 200)], None)]
	ui.draw(screen, st, 1)  # must not raise; the unselected row clips, the selected one scrolls
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
	monkeypatch.setattr(ui.knowledge, "store_moved", lambda: False)  # conftest moves TEAM; pin the optional row off
	monkeypatch.setattr(ui.knowledge, "effective", lambda: "~/.prs_memory")  # layout, not paths: keep it stable
	st = State(60)
	st.sections, st.fetched_at = [("MINE", [], None)], time.time()
	def row1(w):
		screen.w = w
		ui.draw(screen, st, 0)
		return screen.line(1)
	out = row1(240)
	assert out.index("Session") + len("Session") == screen.line(0).index("v" + ui.VERSION) + len("v" + ui.VERSION) + 1  # chip edge incl. its padding
	assert out.rstrip().endswith("Team off") and "☰" not in out and "Memory ~/.prs_memory" in out
	assert out.index("Reviewer") < out.index("View") < out.index("Knowledge")
	out = row1(225)
	assert "Team off" in out and "  │  " in out and "   │   " not in out  # spacing tightens before anything folds
	out = row1(200)
	assert "History 4h" in out and out.rstrip().endswith("☰ Knowledge")  # Knowledge folds first, it is the least-touched
	out = row1(160)
	assert "Effort medium" in out and out.rstrip().endswith("☰ Knowledge") and "☰ View" in out and "Summaries" not in out
	out = row1(120)
	assert "☰ Reviewer" in out and "☰ View" in out and "☰ Knowledge" in out and "Model" not in out
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
	assert "▸ Reviewer ▸" in seen[0] and "  View ▸" in seen[0] and "Settings:  j/k move" in seen[0]
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
	assert "▸ Model    opus" in seen[0] and "Depth    adaptive" in seen[0] and "Reviewer:  j/k move" in seen[0]
	assert "▸ adaptive" in seen[2] and "Depth:  j/k or d move" in seen[2]
	assert "Depth    low" in seen[4] and "Reviewer:  j/k move" in seen[4]  # back in the group with the new value


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
	assert in_flight("reviewing...") and in_flight("pre-reviewing...") and in_flight("dreaming...")
	assert not in_flight("") and not in_flight("✓ approved") and not in_flight("error: boom")

	st = State(0)
	pr = dict(PR, url="u", section="MINE")
	pr["status"] = "· awaiting review"
	st.sections = [("MINE", [pr], None)]
	st.reviews["u"] = "pre-reviewing..."
	scr = FakeScr()
	ui.C = lambda n: 0
	ui.draw(scr, st, 0)
	assert "1 agent" in scr.text() or "1 running" in scr.text() or "pre-reviewing" in scr.text()
