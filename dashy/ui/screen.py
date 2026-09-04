"""The curses screen: colours, one draw() per tick, and the key loop."""
import curses
import difflib
import os
import subprocess
import random
import textwrap
import threading
import time
from datetime import datetime, timezone

from .. import HERE, VERSION, config
from ..core import github, knowledge, log, memory, review as review_mod, team, update
from ..core.state import State, in_flight
from . import art
from .rows import age, rows

LESS_PROMPT = "review of %f  |  q close  j/k scroll  /search"
COLORS = [  # (pair, 256-colour fg, 8-colour fg, bg256, bg8)
	(1, 244, curses.COLOR_WHITE, -1, -1),          # dim
	(2, 75, curses.COLOR_CYAN, -1, -1),            # section header
	(3, 203, curses.COLOR_RED, -1, -1),            # error / changes requested
	(4, 78, curses.COLOR_GREEN, -1, -1),           # approved
	(5, 221, curses.COLOR_YELLOW, -1, -1),         # draft / in flight
	(6, 111, curses.COLOR_BLUE, -1, -1),           # repo ref
	(7, 252, curses.COLOR_WHITE, 237, curses.COLOR_BLACK),   # bars
	(8, 16, curses.COLOR_BLACK, 75, curses.COLOR_CYAN),      # bar badge
	(9, 244, curses.COLOR_WHITE, 237, curses.COLOR_BLACK),   # dim on bar
	(10, 221, curses.COLOR_YELLOW, 237, curses.COLOR_BLACK), # yellow on bar
	(11, 78, curses.COLOR_GREEN, 237, curses.COLOR_BLACK),   # green on bar
	(12, 203, curses.COLOR_RED, 237, curses.COLOR_BLACK),    # red on bar
	(13, 111, curses.COLOR_BLUE, 237, curses.COLOR_BLACK),   # blue on bar
	# the secondary header sits on a darker grey than the primary so the two rows read as two bars
	(15, 252, curses.COLOR_WHITE, 235, curses.COLOR_BLACK),  # bar 2
	(16, 244, curses.COLOR_WHITE, 235, curses.COLOR_BLACK),  # dim on bar 2
	(17, 221, curses.COLOR_YELLOW, 235, curses.COLOR_BLACK), # yellow on bar 2
	(18, 78, curses.COLOR_GREEN, 235, curses.COLOR_BLACK),   # green on bar 2
	(19, 203, curses.COLOR_RED, 235, curses.COLOR_BLACK),    # red on bar 2
	(20, 255, curses.COLOR_BLACK, 240, curses.COLOR_WHITE),  # group label chip on bar 2: light grey, so it does not compete with the app badge
	(21, 233, curses.COLOR_BLACK, -1, -1),         # header fade: ▀ in a darker grey over the terminal bg
	(22, 75, curses.COLOR_CYAN, 235, curses.COLOR_BLACK),    # cyan on bar 2 (Session label)
	(23, 176, curses.COLOR_MAGENTA, -1, -1),       # the agent itself: its role, and the keys that drive it
	(24, 73, curses.COLOR_CYAN, -1, -1),           # a PR number
	(25, 238, curses.COLOR_BLACK, -1, -1),         # rules and separators, one step above the background
	(26, 252, curses.COLOR_WHITE, 236, curses.COLOR_BLACK),  # the selected row: a tint, not reverse video
	(27, 244, curses.COLOR_WHITE, 236, curses.COLOR_BLACK),  # dim on the selected row
]
# ponytail: a theme swaps the 256-colour values above, nothing else. Keys are the accents (cyan, red, green, yellow,
# blue) and the bar greys; anything not listed keeps the default. New theme = one more line.
THEMES = {
	"dashy": {},
	"dracula": {75: 117, 203: 210, 78: 84, 221: 228, 111: 141, 237: 236, 235: 234, 240: 61},
	"gruvbox": {75: 108, 203: 167, 78: 142, 221: 214, 111: 109, 237: 237, 235: 235, 240: 243},
	"nord": {75: 110, 203: 174, 78: 108, 221: 222, 111: 146, 237: 238, 235: 236, 240: 60},
}

# age, repo, pr, author and state are fixed; the title takes what is left. Mirrors the design's grid.
# ponytail: reviewers has a column of its own. Folded into the state cell they all took the state's
# colour, and state is the LAST column — so at any real width they were pushed off the right edge
# and never appeared at all. zip(COLS, cells) also silently drops a cell when the two disagree.
COLS = ((4, "age"), (18, "repo"), (5, "pr"), (0, "title"), (12, "author"), (14, "reviewers"),
        (4, "ci"), (20, "state"))
PANE = 62  # columns the detail pane wants
PANE_MIN = 150  # total width below which there is no room for it and the list gets everything
PANE_MIN_H = 16  # and rows: a pane beside a four-row list is worth less than the four rows
# ponytail: the design labels these ⏎ pane / r review, and that is now what they do. The branch had
# declined — "⏎ has meant review since the first version" — and took p for the pane, which #8 later
# shipped as pre-review. Taken deliberately rather than by redraw: one release of churn on the two keys
# used most, instead of a permanent divergence between the design and the thing. f refresh, v read.
KEYS = (("nav", "j/k move · ⏎ pane · o open · ␣ fold"), ("run", "r review · p pre-review · Y copy path · a auto"),
        ("config", "m model · d depth · e effort · i interval"), ("app", "Z dream · f refresh · v view · T team · u update · q quit"))


ANCHORS = {}  # setting key -> (y, x) where its label was last drawn; dropdowns hang from it
SCROLLING = [False]  # draw() sets it when the selected title is a marquee; main() ticks faster while it is


def C(n):
	return curses.color_pair(n)


def settings(state):
	"""key -> (label, options, current, set, show). ponytail: one table drives the header hints and the dropdowns."""
	return {
		"m": ("Model", config.MODELS, state.model, lambda v: setattr(state, "model", v), str),
		"d": ("Depth", config.DEPTHS, config.DEPTH, lambda v: setattr(config, "DEPTH", v), str),
		"e": ("Effort", config.EFFORTS, config.EFFORT, lambda v: setattr(config, "EFFORT", v), lambda v: v or "default"),
		"s": ("Summaries", config.SUBS, state.subs, lambda v: setattr(state, "subs", v), str),
		"t": ("History", config.WINDOWS, state.window, lambda v: setattr(state, "window", v), lambda v: f"{v}h" if v else "all"),
		"i": ("Refresh", config.INTERVALS, state.interval, lambda v: setattr(state, "interval", v),
		      lambda v: f"{v}s" if v < 60 or v % 60 else f"{v // 60}m"),  # --interval 45 / 90 read as given
	}


def set_theme(name):
	config.THEME = name
	init_colors()  # init_pair repaints live, no redraw needed


def header_groups(state):
	"""The settings groups on the second header row: (label, key, rows), rows = (setting key, name, value, tone).
	tone: None / "on" (yellow) / "err" (red). ponytail: one table, drawn as pairs and listed by the group key."""
	st = settings(state)
	def row(k):
		label, _, current, _, show = st[k]
		return (k, label, show(current), None)
	# ponytail: "Agent" rather than "Reviewer" — the group is what drives the model, and reviewing is
	# only what it happens to be doing. The design's "role reviewer" is left out on purpose: there is
	# one role, so it would be a label dressed as a control.
	reviewer = [row("m"), row("d"), row("e")]
	view = [row("s"), ("D", "Drafts", "shown" if state.drafts else "hidden", "on" if state.drafts else None), row("t")]
	# Memory is your own dir, in a team or not; the team is a second source read alongside it, shown below
	know = [("L", "Memory", knowledge.show(knowledge.effective()) + knowledge.history_note(), None),
	        ("T", "Team", team.ERROR[:40] if team.ERROR else (team.NAME or "off"),  # ponytail: clipped, T shows it whole
	         "err" if team.ERROR else ("on" if team.on() else None))]
	if knowledge.store_moved():  # ponytail: a row only once it says something — at the default it just repeats Memory
		know.append(("C", "Store", knowledge.show(config.TEAM), None))
	return [("Agent", "R", reviewer), ("View", "V", view), ("Knowledge", "K", know)]


def init_colors():
	curses.curs_set(0)
	curses.use_default_colors()
	many = curses.COLORS >= 256
	theme = THEMES.get(config.THEME, {})
	for pair, fg256, fg8, bg256, bg8 in COLORS:
		fg256, bg256 = theme.get(fg256, fg256), theme.get(bg256, bg256)
		curses.init_pair(pair, fg256 if many else fg8, (bg256 if many else bg8) if bg256 != -1 else -1)


def splash(scr, h, w, spin):
	"""ponytail: centred one-liners, clipped by addnstr — no layout engine."""
	def mid(y, t, attr):
		if 4 <= y < h - 2:
			scr.addnstr(y, max(0, (w - len(t)) // 2), t, w - 1, attr)
	logo = w >= art.LOGO_W + 2 and h >= 8 + len(art.LOGO)
	y0 = h // 2 - (4 + (len(art.LOGO) + 1 if logo else 0)) // 2
	mid(y0, f"{spin}  fetching pull requests…", C(5) | curses.A_BOLD)
	mid(y0 + 2, "created by", C(1))
	mid(y0 + 3, art.NAME, C(6) | curses.A_BOLD)
	for i, line in enumerate(art.LOGO if logo else []):
		mid(y0 + 5 + i, line, C(5) | curses.A_BOLD)


REVIEWER_COLOR = {}  # glyph -> colour pair, filled after init_colors: ✓ green, ✗ red, · pending yellow, ~ dim


def _wrap_keys(keys, room):
	"""Fit "a · b · c" into two lines of `room`, breaking only at a separator. Returns (first, second).

	ponytail: BOTH lines are fitted here. Slicing the second one at the call site cut it mid-key —
	"p pre-review · a au" — which is the specific thing wrapping instead of dropping was meant to avoid.
	A key that fits on neither line is dropped whole; a truncated key name is worse than a missing one,
	because it still looks like an instruction.
	"""
	parts = [p for p in keys.split(" · ") if p]
	lines, line = [], ""
	for part in parts:
		nxt = part if not line else f"{line} · {part}"
		if len(nxt) <= room:
			line = nxt
			continue
		lines.append(line)
		line = part if len(part) <= room else ""
		if len(lines) == 2:
			break
	if line and len(lines) < 2:
		lines.append(line)
	return (lines + ["", ""])[:2]


def draw(scr, state, sel, prompt=None, now=None):
	"""ponytail: `now` is a seam, not a feature. Everything animated here reads the clock — the spinner
	frame, the refresh countdown, the marquee offset — so a test that asserts on a rendered row is
	asserting on what time it happens to be. That is why test_draw_truncates_long_title_on_narrow_screen
	has been failing about one run in three. Pass a fixed `now` and the frame is decided."""
	if not REVIEWER_COLOR:
		REVIEWER_COLOR.update({"✓": C(4), "✗": C(3), "·": C(5), "~": C(1)})
	scr.erase()
	ANCHORS.clear()  # stale anchors would hang a dropdown from a chip that is no longer drawn
	now = time.time() if now is None else now
	SCROLLING[0] = False
	h, w = scr.getmaxyx()
	with state.lock:
		sections, fetched_at, reviews = state.sections, state.fetched_at, dict(state.reviews)
	rs = rows(sections, state.window, state.subs, state.drafts, state.expanded)
	if h < 12:  # ponytail: on a short terminal a column header costs a PR, and the PR is the point
		rs = [r for r in rs if r[0] != "cols"]
	prs = [i for i, (k, _) in enumerate(rs) if k == "pr"]
	sel = max(0, min(sel, len(prs) - 1)) if prs else 0
	cur = prs[sel] if prs else -1
	# ponytail: naive scroll keeps the selected row on screen, no smooth scrolling
	top = max(0, cur - max(0, h - 8)) if cur >= 0 else 0  # keeps the selected row one above the last list row (h - 3)
	current = rs[cur][1] if cur >= 0 else None
	# ponytail: the pane only exists where there is room for it AND the list still. Below that the list
	# wins, because a dashboard you cannot read the rows of is not improved by a panel beside them.
	# ponytail: height as well as width. A pane needs rows to be worth anything, and gating on width
	# alone put one beside a four-row list. Belt and braces with line()'s own bound: this decides
	# whether a pane makes sense, that one guarantees it cannot fault if the decision is ever wrong.
	pane_w = PANE if getattr(state, "pane", True) and w >= PANE_MIN and h >= PANE_MIN_H else 0
	lw = w - pane_w
	title_w = max(12, lw - 2 - sum(c for c, _ in COLS if c) - len(COLS))

	if h < 5 or w < 8:  # ponytail: three header rows plus a TWO-row footer; draw nothing rather than fault
		scr.noutrefresh()
		return sel, (rs[cur][1] if cur >= 0 else None)

	# header: primary row = identity + live state, secondary row = settings grouped by what they steer,
	# then a half-block fade row and a blank one so the list starts under a soft edge with some air
	total = sum(len(p) for _, p, _ in sections if p)
	spin = art.SPINNER[int(now * 8) % len(art.SPINNER)]  # ponytail: frame from the clock, no animation state
	rspin = art.REFRESH_SPINNER[int(now * 10) % len(art.REFRESH_SPINNER)]  # 10fps under the 20fps tick: every frame lands on a redraw
	ago = None if fetched_at is None else age(datetime.fromtimestamp(fetched_at, timezone.utc).isoformat())
	status = f"{spin} fetching…" if ago is None else "updated just now" if ago == "now" else f"updated {ago} ago"
	nxt = "" if fetched_at is None else f"{rspin} refreshing…" if state.fetching else \
		f"next refresh {max(0, int(fetched_at + state.interval - now))}s / {state.interval // 60}m"
	vals = list(reviews.values())
	running = sum(1 for v in vals if in_flight(v))  # ponytail: any verb, so a pre-review counts
	scr.addnstr(0, 0, " " * (w - 1), w - 1, C(7))
	scr.addnstr(1, 0, " " * (w - 1), w - 1, C(15))
	scr.addnstr(2, 0, "▀" * (w - 1), w - 1, C(21))  # ponytail: upper half-block = a half-row gradient step, no true gradients in a tty

	def bar(y, x, parts, stop=None):
		"""Draw (text, attr[, key]) pieces left to right from x; a piece that would not fit is dropped along with
		the rest. A piece tagged with a setting key records where it landed, so its dropdown can hang there."""
		stop = w - 1 if stop is None else stop
		for text, attr, *tag in parts:
			if x + len(text) > stop:
				break
			scr.addnstr(y, x, text, stop - x, attr)
			for key in tag[0] if tag else "":  # a chip carries its group key plus every setting folded under it
				ANCHORS[key] = (y, x)
			x += len(text)
		return x

	def hint(key, attr=C(17)):  # ? toggles: the key that changes a setting, shown right before it
		return [(key + " ", attr | curses.A_BOLD)] if state.hints and key else []
	badge = f" ▌ gitdashy v{VERSION} "
	# row 0 in pieces, each droppable on its own so content wins over air on a narrow screen: the refresh
	# countdown goes first, then the status, agents, the PR count, AUTO; the badge and the update prompt are
	# not on the ladder — they are clipped, and the prompt wins when the two cannot share the row
	left = {"badge": [(badge, C(8) | curses.A_BOLD)], "prs": [(f"    {total} PRs", C(7) | curses.A_BOLD)],
	        "agents": [(f"  ·  {running} agents running", C(10) | curses.A_BOLD if running else C(9))]}
	right = {"status": hint("r", C(10)) + [(status, C(9))],
	         "nxt": [("  ·  ", C(9))] + hint("i", C(10)) + [(nxt, C(9), "i")] if nxt else [],
	         "auto": [("   ", C(7)), (" AUTO ", C(5) | curses.A_REVERSE | curses.A_BOLD)] if state.auto else [],
	         "update": [("   ", C(7)), (f" ↑ update to v{state.update} · u ", C(4) | curses.A_REVERSE | curses.A_BOLD)] if state.update else []}
	def width(parts):
		return sum(len(p[0]) for p in parts)
	def flat(d):
		return [piece for parts in d.values() for piece in parts]
	drop = ["nxt", "status", "agents", "prs", "auto"]
	while drop and 2 + width(flat(left)) + 3 + width(flat(right)) > w - 1:
		key = drop.pop(0)
		(left if key in left else right)[key] = []
	rparts = flat(right)
	if rparts and rparts[0][0].strip() == "":
		rparts = rparts[1:]  # no leading gap once the status is gone
	bar(0, 2, flat(left), stop=max(2, w - 1 - width(rparts) - 1))
	bar(0, max(2, w - 1 - width(rparts)), rparts)  # clipped by bar's stop when even this is too much

	spacings = [("   │   ", "        "), ("  │  ", "     "), (" │ ", "   ")]  # (between pairs, between groups), loosest first
	sep, gap = [(t, C(16)) for t, _ in spacings], [(t, C(15)) for _, t in spacings]
	tones = {None: C(15), "on": C(17), "err": C(19)}
	def kv(key, value, attr=C(15), k=""):  # dim key, bright value, so "Depth adaptive" reads as a pair and not one phrase
		return hint(k) + [(key + " ", C(16), k), (value, attr)]
	def render(label, key, rows, level, space):
		"""A group at one of its levels: "full" = gear chip + pairs, "chip" = the chip with a caret, "off" = nothing.
		ponytail: the chip is a badge like the app's, so a group reads as one block; collapsed, it anchors every key in it."""
		keys = key + "".join(k for k, *_ in rows)
		if level == "off":
			return []
		if level == "chip":
			return hint(key) + [(f" ☰ {label} ", C(20) | curses.A_BOLD, keys)]  # folded: a menu, so the burger says so
		parts = hint(key) + [(f" {label} ", C(20) | curses.A_BOLD, key), ("  ", C(15))]
		for i, (k, name, value, tone) in enumerate(rows):
			parts += ([sep[space]] if i else []) + kv(name, value, tones[tone], k)
		return parts
	session = [("Session", C(22) | curses.A_BOLD), ("  ", C(15))] \
		+ kv("✓", str(sum(v.startswith('✓') for v in vals)), C(18) | curses.A_BOLD) + [("   ", C(15))] \
		+ kv("✗", str(sum(v.startswith('✗') for v in vals)), C(19) | curses.A_BOLD) + [("   ", C(15))] \
		+ kv("~", str(sum(v.startswith('~') for v in vals)), C(15) | curses.A_BOLD) + [("   ", C(15))] \
		+ kv("!", str(sum(v.startswith('error') for v in vals)), C(19) | curses.A_BOLD)
	# session sits left, its label ending where the app badge ends; the groups stack against the right edge and
	# degrade one step at a time when the row is too narrow: the spacing tightens twice, then View folds to a
	# chip, then Reviewer, then both nest under one Settings chip, then that goes too — content always wins over air
	x = bar(1, 2 + len(badge) - len("Session"), session)
	groups = header_groups(state)
	levels = {key: "full" for _, key, _ in groups}
	levels["space"], levels["nest"] = 0, "no"
	steps = [("space", 1), ("space", 2), ("K", "chip"), ("V", "chip"), ("R", "chip"), ("nest", "chip"), ("nest", "off")]
	def stack():
		if levels["nest"] == "chip":  # both groups under one chip; it anchors every key so S/R/V and the settings all hang from it
			keys = "S" + "".join(key + "".join(k for k, *_ in rows) for _, key, rows in groups)
			return hint("S") + [(" ☰ Settings ", C(20) | curses.A_BOLD, keys)]
		if levels["nest"] == "off":
			return []
		drawn = [render(label, key, rows, levels[key], levels["space"]) for label, key, rows in groups]
		drawn = [g for g in drawn if g]
		return [piece for g in drawn for piece in g + [gap[levels["space"]]]][:-1] if drawn else []
	parts = stack()
	while parts and x + 3 + width(parts) > w - 3 and steps:
		key, level = steps.pop(0)
		levels[key] = level
		parts = stack()
	if parts and x + 3 + width(parts) <= w - 3:
		bar(1, w - 3 - width(parts), parts)

	if not rs and fetched_at is None:
		splash(scr, h, w, spin)

	for y, (kind, payload) in enumerate(rs[top:top + max(0, h - 5)], start=4):
		i = top + y - 4
		if kind == "cols":
			x = 1
			for width_, name in COLS:
				room = title_w if width_ == 0 else width_
				if x + room > lw:
					break
				scr.addnstr(y, x, name.upper()[:room], room, C(25))
				x += room + 1
		elif kind == "head":
			label, count, right = payload
			scr.addnstr(y, 1, label, lw - 2, C(2) | curses.A_BOLD)
			x = min(lw - 2, 2 + len(label))
			if count:
				scr.addnstr(y, x, count, max(1, lw - 1 - x), C(1))
				x += len(count) + 1
			# ponytail: a rule to the right edge, so a heading reads as a band and not a floating word
			rule = lw - 2 - x - (len(right) + 1 if right else 0)
			if rule > 2:
				scr.addnstr(y, x, "─" * rule, rule, C(25))
			if right and lw - 2 - len(right) > x:
				scr.addnstr(y, lw - 2 - len(right), right, len(right), C(1))
		elif kind == "queue":
			label, count, note_ = payload
			scr.addnstr(y, 3, label, lw - 4, C(1))
			x = min(lw - 2, 4 + len(label))
			scr.addnstr(y, x, count, max(1, lw - 1 - x), C(1))
			if lw - 2 > x + 3:
				scr.addnstr(y, x + 3, note_, lw - 3 - x, C(25))
		elif kind == "err":
			scr.addnstr(y, 3, payload, lw - 4, C(3))
		elif kind == "empty":
			scr.addnstr(y, 3, "none", lw - 4, C(1))
		elif kind == "sub":
			x = 3 + COLS[0][0] + COLS[1][0] + COLS[2][0]
			t = " ".join(payload.split())
			room = min(70, lw - 1 - x - 2)  # ponytail: hard cap so a wordy model can't clog the list
			if room > 4:
				scr.addnstr(y, x, "↳ ", 2, C(5))
				scr.addnstr(y, x + 2, t if len(t) <= room else t[:room - 1] + "…", room, C(1) | curses.A_ITALIC)
		elif kind == "pr":
			p = payload
			is_cur = i == cur
			# ponytail: a tint plus a left marker, not reverse video — reverse repaints the whole row and
			# throws away every colour in it, which is most of what tells you what a row is.
			base, dim = (C(26), C(27)) if is_cur else (0, C(1))
			if is_cur:
				scr.addnstr(y, 0, " " * (lw - 1), lw - 1, C(26))
				scr.addnstr(y, 0, "▌", 1, C(2) | curses.A_BOLD)
			# ponytail: carried over from main, not the branch's older order — what THIS session is doing
			# wins over what was fetched, or a pre-review runs to completion with the row never saying so.
			st = reviews.get(p["url"]) or p.get("status") or p.get("prev", "")
			if in_flight(st):
				st = f"{spin} {st[:-3]}…"  # ponytail: same clock-driven frame as the header, any verb
			st_attr = C(3) if st.startswith("error") else C(4) if st.startswith("✓") else \
				C(3) if st.startswith("✗") else C(5)
			tag = ("▸+" + str(p["more"])) if p.get("more") else "▾" if p.get("open") else ""
			cells = [(age(p["updatedAt"]).rjust(COLS[0][0]), dim),
			         (p["repository"]["name"], dim),
			         (("#" + str(p["number"])).rjust(COLS[2][0]), C(24)),
			         (("└ " if p.get("child") else "") + ("draft " if p.get("isDraft") else "") + p["title"],
			          base | curses.A_BOLD if is_cur else 0),
			         (p.get("author", {}).get("login", ""), dim),
			         (p.get("reviewers", ""), dim),   # ponytail: overpainted per chip below, by glyph
			         # ponytail: CI on the head commit, from #9 — the third feature that landed on main
			         # while this grid was being rebuilt. A column of its own, like the chips: folding it
			         # into a neighbour is what made the chips invisible.
			         (("ci" + p["checks"]) if p.get("checks") else "",
			          REVIEWER_COLOR.get(p.get("checks", ""), C(5)) | curses.A_BOLD),
			         (st + ("  " + tag if tag else ""), st_attr | curses.A_BOLD)]
			assert len(cells) == len(COLS)  # ponytail: zip drops a cell silently when they disagree, and
			                                # this pair has now been wrong once in each direction
			x = 1
			for (width_, name), (text, attr) in zip(COLS, cells):
				room = title_w if width_ == 0 else width_
				# ponytail: the ellipsis has to be measured against the space actually left, not the
				# column's nominal width. They differ on a narrow terminal, and the write was clipped
				# afterwards — so the "…" was the character that got cut, leaving a hard truncation
				# with nothing to say the text continues.
				room = min(room, lw - 1 - x)
				if x >= lw - 1 or room < 1:
					break
				# ponytail: the marquee arrived after this grid was designed, so it is carried in rather
				# than lost — the selected row scrolls its overflowing title, every other row clips.
				if name == "title" and is_cur and len(text) > room > 4:
					SCROLLING[0] = True
					t = art.marquee(text, room, now)
				else:
					t = text if len(text) <= room else text[:max(0, room - 1)] + "…"
				scr.addnstr(y, x, t.ljust(room), room, base if is_cur else attr)
				if name == "reviewers" and t.strip():
					# ponytail: the cell is drawn first so layout is decided in one place, then each chip
					# is overpainted in its own glyph's colour — ✓ green, ✗ red, · pending. That is what
					# REVIEWER_COLOR is for, and it sat populated and unused once the chips moved here.
					cx = x
					for chip in t.split():
						if x + room - cx < len(chip):
							break
						scr.addnstr(y, cx, chip, len(chip),
						            base if is_cur else REVIEWER_COLOR.get(chip[0], C(1)) | curses.A_BOLD)
						cx += len(chip) + 1
				x += (title_w if width_ == 0 else width_) + 1

	if pane_w:
		detail(scr, state, h, w - pane_w, pane_w, current)

	for fy in (h - 2, h - 1):
		scr.addnstr(fy, 0, " " * (w - 1), w - 1, C(7))
	if prompt:
		scr.addnstr(h - 1, 0, prompt, w - 1, C(8) | curses.A_BOLD)
	else:
		# ponytail: TWO rows, in columns, wrapping — the design's shape. One row meant dropping whole
		# groups from the right, so `f refresh` and `q quit` were invisible on any normal terminal.
		# Wrapping keeps every key on screen; a column too narrow for its own label is what gets dropped.
		col = max(18, (w - 2) // len(KEYS))
		x = 1
		for i, (label, keys) in enumerate(KEYS):
			if x + col > w - 1:
				break
			if i:  # ponytail: a rule between columns, spanning both rows, as the design draws it
				for fy in (h - 2, h - 1):
					scr.addnstr(fy, x - 1, "│", 1, C(25))
			scr.addnstr(h - 2, x, label.upper(), min(len(label), w - 1 - x), C(9))
			room = col - len(label) - 3  # ponytail: -3 not -2, so a full-width cell keeps a gap before the rule
			line, rest = _wrap_keys(keys, room)
			scr.addnstr(h - 2, x + len(label) + 1, line, max(1, min(len(line), w - 1 - x - len(label) - 1)), C(7))
			if rest:
				scr.addnstr(h - 1, x + len(label) + 1, rest, max(1, min(len(rest), w - 1 - x - len(label) - 1)), C(7))
			x += col
	# ponytail: noutrefresh, from main and NOT the branch's scr.refresh(). getch() flushes the frame, and
	# a popup drawn on top flushes once with it; refreshing here too pushed a popup-less frame every tick,
	# which is what stuttered the dropdowns. Taking the branch's footer wholesale would have put it back.
	scr.noutrefresh()
	return sel, (rs[cur][1] if cur >= 0 else None)


TONE = {"ok": (4, "✓"), "fail": (3, "✗"), "run": (5, "~"), "skip": (1, "·")}
FIND = {"blocking": 3, "note": 5, "nit": 1}


def detail(scr, state, h, x0, width, pr):
	"""The selected PR, beside the list: what it is, what CI thinks, what the review found, what you can do.

	ponytail: draws only what it has. Branch and checks arrive from a background fetch, findings only
	from a review that ran — every section is skipped rather than shown empty, so the pane never pads
	itself out with rows that say nothing.
	"""
	def at(y, x, text, n, attr=0):
		"""Every write in this pane, bounded. The ONLY one that touches the screen.

		ponytail: line() bounded the writes that went through it, and nine did not — the model tag, the
		summary fallback, ACTIONS, the title wrap. The commit that added line() said it "guarantees it
		cannot fault"; that was true of a third of the pane, and a review of a PR with a summary and no
		findings — every entry written before the findings field existed — still killed the dashboard.
		A guarantee that depends on remembering to use it is not one.
		"""
		# ponytail: x is clamped to the pane on BOTH sides. Only the model tag is right-aligned, and it
		# cannot outgrow 62 columns today — but "cannot happen today" is how the last two bounds here
		# were argued, and both turned out to be reachable.
		x = max(x0, x)
		if 4 <= y < h - 2 and x < x0 + width:
			scr.addnstr(y, x, text, max(1, min(n, x0 + width - x)), attr)

	def line(y, cells):
		x = x0 + 2
		for text, attr in cells:
			if not text or x >= x0 + width - 1:
				continue
			at(y, x, text, x0 + width - 1 - x, attr)
			x += len(text) + 1
	for y in range(4, h - 2):  # ponytail: from the first list row, not through the header's breathing space
		at(y, x0, "│", 1, C(25))
	if pr is None:
		at(4, x0 + 2, "no row selected", width - 3, C(1))
		return
	y = 4
	line(y, [("SELECTED PR", C(25)), ("", 0)])
	if width > 34:
		at(y, x0 + width - 10, "⏎ close", 8, C(25))
	y += 2
	line(y, [("#" + str(pr["number"]), C(24)), (pr["repository"]["name"], C(1)),
	         ("· " + age(pr["updatedAt"]), C(1))])
	y += 1
	title = pr["title"]
	for chunk in textwrap.wrap(title, max(10, width - 4))[:2]:
		at(y, x0 + 2, chunk, width - 3, 0)
		y += 1
	d = state.want_detail(pr) or {}
	who = pr.get("author", {}).get("login", "")
	stats = []
	if d.get("add") is not None:
		stats = [("+" + str(d["add"]), C(4)), ("−" + str(d["del"]), C(3)), (str(d["files"]) + " files", C(1))]
	line(y, [(who, C(1))] + ([(d["branch"], C(23))] if d.get("branch") else []) + stats)
	y += 2
	if d.get("checks"):
		line(y, [("CHECKS", C(25))])
		y += 1
		cells = []
		for c in d["checks"][:4]:
			pair, mark = TONE.get(c["state"], TONE["run"])
			cells += [(mark, C(pair)), (c["name"], C(1))]
		line(y, cells)
		y += 2
	rev = pr.get("review") or {}
	if not rev:
		rev = log.last(pr["url"]) or {}
	if rev:
		line(y, [("AI REVIEW", C(25))])
		if width > 30:
			at(y, x0 + width - 2 - len(t := f"{rev.get('model', '')} {log.tag(rev)}".strip()), t, len(t), C(25))
		y += 1
		verdict = config.STATUS.get(rev.get("verdict"), "")
		line(y, [(verdict, C(4) if verdict.startswith("✓") else C(3) if verdict.startswith("✗") else C(5))])
		y += 1
		found = log.findings(rev)
		if found:
			counts = [(f"{sum(1 for f in found if f['kind'] == k)} {k}", C(FIND[k]))
			          for k in ("blocking", "note", "nit") if any(f["kind"] == k for f in found)]
			line(y, counts)
			y += 1
			for f in found:
				if y >= h - 6:
					break
				at(y, x0 + 2, f["kind"][:8].ljust(9), 9, C(FIND[f["kind"]]))
				room = width - 13
				# ponytail: the file's BASENAME, not its path. "features/library/ui/LibraryLanding.tsx:19"
				# is most of a pane on its own, so the finding itself — the part you actually read — was
				# always the half that got truncated away. The directory is recoverable; the point is not.
				loc = f["loc"].rsplit("/", 1)[-1] if f["loc"] else ""
				txt = (loc + "  " if loc else "") + f["text"]
				at(y, x0 + 11, txt if len(txt) <= room else txt[:room - 1] + "…", room, C(1))
				y += 1
			if len(found) > 0 and width > 26:
				at(y, x0 + width - 12, "v read all", 10, C(25))
		elif rev.get("summary"):
			for chunk in textwrap.wrap(rev["summary"], max(10, width - 4))[:2]:
				at(y, x0 + 2, chunk, width - 3, C(1))
				y += 1
		y += 1
	# ponytail: pre-reviews live on disk under a derived name, so this survives a restart with nothing
	# remembered — the pane just asks the filesystem. Before this they were invisible: you had to press
	# p and hope, with no way to tell a fresh one from a review of a diff you have since pushed over.
	# ponytail: MINE only, because `p` is bound on MINE only. The pane offered "read the pre-review" for
	# any section, so a file left beside a non-MINE row promised a key that does nothing there.
	# ponytail: and `pr` is not None here — the pane returned on that sixty lines up.
	pre_at, pre_moved = review_mod.self_review_state(pr) if pr.get("section") == "MINE" else (0.0, False)
	if pre_at and y < h - 6:
		moved = pre_moved
		line(y, [("PRE-REVIEW", C(25)),
		         ("· stale, the PR moved since" if moved else "· current", C(3) if moved else C(1))])
		y += 1
		when = datetime.fromtimestamp(pre_at).strftime("%d %b %H:%M")
		line(y, [(when, C(1)), ("— p to read, Y to copy its path", C(25))])
		y += 2
	if y < h - 5:
		line(y, [("ACTIONS", C(25))])
		y += 1
		acts = [("r", "review this PR"),
		        ("p", "re-run: the PR moved since" if pre_moved else
		               "read the pre-review" if pre_at else "pre-review, posting nothing"),
		        ("o", "open in browser")]
		if pre_at:
			acts.append(("Y", "copy the pre-review path"))
		if pr.get("review") or log.last(pr["url"]):
			acts.append(("v", "read the full review"))  # ponytail: offered only when there is one
		for key, what in acts:
			if y >= h - 3:
				break
			at(y, x0 + 2, key, 2, C(23) | curses.A_BOLD)
			at(y, x0 + 5, what, width - 6, C(1))
			y += 1


def panel(scr, title, lines, footer, accent=4):
	"""Centred bordered box. ponytail: addnstr and box-drawing chars, no curses windows or panels."""
	h, w = scr.getmaxyx()
	inner = max([len(l) for l in [title, footer] + [t for t, _ in lines]] + [34]) + 6
	inner = min(inner, w - 4)
	top = max(1, h // 2 - (len(lines) + 6) // 2)
	x = max(0, (w - inner) // 2)
	def row(y, left, right="", attr=0, attr2=None):
		scr.addnstr(y, x, "│" + " " * (inner - 2) + "│", inner, C(accent))
		scr.addnstr(y, x + 3, left, inner - 6, attr)
		if right:
			scr.addnstr(y, x + inner - 3 - len(right), right, len(right), attr if attr2 is None else attr2)
	scr.addnstr(top, x, "╭" + "─" * (inner - 2) + "╮", inner, C(accent))
	scr.addnstr(top, x + 3, f" {title} ", inner - 6, C(accent) | curses.A_BOLD)
	row(top + 1, "")
	for i, (left, right) in enumerate(lines):
		row(top + 2 + i, left, right, C(1) if i else curses.A_BOLD, C(4) | curses.A_BOLD if i == 0 else C(1))
	row(top + 2 + len(lines), "")
	row(top + 3 + len(lines), footer, "", C(5) | curses.A_BOLD)
	scr.addnstr(top + 4 + len(lines), x, "╰" + "─" * (inner - 2) + "╯", inner, C(accent))
	scr.refresh()


def update_screen(scr, state, sel):
	"""Full-screen update prompt. Returns True if the user said yes."""
	def show(footer, extra=()):
		draw(scr, state, sel, prompt=" ")
		panel(scr, "update available", [
			(f"v{VERSION}  →  v{state.update}", "new release"),
			("", ""),
			("Installs the release tag into", ""),
			(HERE, ""),
			("and restarts gitdashy.", ""),
			*extra,
		], footer)
	show("[y] update now     [n] later")
	scr.timeout(-1)
	try:
		k = scr.getch()
		if k not in (ord("y"), 10, 13, curses.KEY_ENTER):
			return False
		show("installing… this takes a second")
		err = update.apply_update(state.update)  # re-execs on success
		show("[any key] back", extra=[("", ""), (f"failed: {err}", "")])
		scr.getch()
		return False
	finally:
		scr.timeout(500)


def popup(scr, y, x, title, lines, idx, marked=None):
	"""Bordered list hanging from (y, x): title on the top edge, ▸ on line idx, line `marked` in green.
	ponytail: addnstr and box chars like panel(), clipped at the footer."""
	h, w = scr.getmaxyx()
	if h < 4 or w < 8:  # same guard as draw(): nothing rather than a curses fault
		return scr.refresh()
	inner = max([len(l) for l in lines] + [len(title)]) + 6  # "│ ▸ text │"
	x = max(0, min(x, w - inner - 1))
	def put(row, text, attr):
		if row < h - 2:
			scr.addnstr(row, x, text, inner, attr)
	put(y + 1, "╭" + "─" * (inner - 2) + "╮", C(6))
	if y + 1 < h - 2:
		scr.addnstr(y + 1, x + 2, f" {title} ", inner - 4, C(6) | curses.A_BOLD)
	for i, line in enumerate(lines):
		body = f" {'▸' if i == idx else ' '} {line}".ljust(inner - 2)
		put(y + 2 + i, "│" + body + "│", C(6))
		if y + 2 + i < h - 1:
			scr.addnstr(y + 2 + i, x + 1, body, inner - 2,
			            curses.A_REVERSE | curses.A_BOLD if i == idx else C(4) | curses.A_BOLD if i == marked else 0)
	put(y + 2 + len(lines), "╰" + "─" * (inner - 2) + "╯", C(6))
	scr.refresh()


def dropdown(scr, state, sel, key):
	"""Pick a setting's value from a list hanging under its header label (or its group's chip when collapsed).
	j/k or the key itself moves, Enter picks, Esc keeps. The dashboard keeps ticking behind it."""
	label, options, current, set_, show = settings(state)[key]
	options = list(options) + ([current] if current not in options else [])  # --model can name anything
	idx = options.index(current)
	while True:
		draw(scr, state, sel, prompt=f" {label}:  j/k or {key} move   ⏎ pick   esc keep")
		y, x = ANCHORS.get(key, (1, 3))  # after draw: the label's place at this width
		popup(scr, y, x, label, [show(o) for o in options], idx, options.index(current))
		k = scr.getch()
		if k in (ord("j"), curses.KEY_DOWN, ord(key)):
			idx = (idx + 1) % len(options)
		elif k in (ord("k"), curses.KEY_UP):
			idx = (idx - 1) % len(options)
		elif k in (10, 13, curses.KEY_ENTER):
			set_(options[idx])
			return True
		elif k in (27, ord("q")):
			return False


def group_menu(scr, state, sel, key):
	"""List a header group's settings under its chip. j/k moves, Enter opens that setting (its dropdown, the
	drafts toggle, or team setup) and Esc comes back here; Esc again closes."""
	idx = 0
	while True:
		label, _, rows = next(g for g in header_groups(state) if g[1] == key)  # re-read: a pick changes the values
		idx = min(idx, len(rows) - 1)  # the Team row can go away under us
		draw(scr, state, sel, prompt=f" {label}:  j/k move   ⏎ open   esc close")
		y, x = ANCHORS.get(key, (1, 3))
		name_w = max(len(name) for _, name, _, _ in rows)
		popup(scr, y, x, label, [f"{name.ljust(name_w)}   {value}" for _, name, value, _ in rows], idx)
		k = scr.getch()
		if k in (ord("j"), curses.KEY_DOWN):
			idx = (idx + 1) % len(rows)
		elif k in (ord("k"), curses.KEY_UP):
			idx = (idx - 1) % len(rows)
		elif k in (10, 13, curses.KEY_ENTER):
			sk = rows[idx][0]
			if sk in settings(state):
				dropdown(scr, state, sel, sk)
			elif sk == "D":
				state.drafts = not state.drafts
			elif sk == "T":
				team_setup(scr, state, sel)
			elif sk in ("L", "C"):
				set_path(scr, state, sel, sk)
		elif k in (27, ord("q")):
			return


def settings_menu(scr, state, sel):
	"""The two settings groups as a menu under the Settings chip (or the first chip when they are not nested).
	Enter opens a group, Esc closes."""
	idx = 0
	while True:
		groups = header_groups(state)
		draw(scr, state, sel, prompt=" Settings:  j/k move   ⏎ open   esc close")
		y, x = ANCHORS.get("S", ANCHORS.get(groups[0][1], (1, 3)))
		popup(scr, y, x, "Settings", [f"{label} ▸" for label, _, _ in groups], idx)
		k = scr.getch()
		if k in (ord("j"), curses.KEY_DOWN):
			idx = (idx + 1) % len(groups)
		elif k in (ord("k"), curses.KEY_UP):
			idx = (idx - 1) % len(groups)
		elif k in (10, 13, curses.KEY_ENTER):
			group_menu(scr, state, sel, groups[idx][1])
		elif k in (27, ord("q")):
			return


def esc_menu(scr, state, sel):
	"""Esc: a btop-style menu in the middle of the screen. Enter cycles the theme, toggles notifications,
	refreshes, or quits. Returns True to quit."""
	idx = 0
	while True:
		items = [f"Theme    {config.THEME:<8}", f"Notify   {'on' if config.NOTIFY else 'off':<8}", "Refresh", "Quit"]
		draw(scr, state, sel, prompt=" Menu:  j/k move   ⏎ pick   esc close   q quit")
		h, w = scr.getmaxyx()
		popup(scr, h // 2 - 4, (w - len(items[0]) - 6) // 2, "gitdashy", items, idx)
		k = scr.getch()
		if k in (ord("j"), curses.KEY_DOWN):
			idx = (idx + 1) % len(items)
		elif k in (ord("k"), curses.KEY_UP):
			idx = (idx - 1) % len(items)
		elif k == ord("q"):
			return True
		elif k in (10, 13, curses.KEY_ENTER):
			if idx == 0:
				names = list(THEMES)
				set_theme(names[(names.index(config.THEME) + 1) % len(names)] if config.THEME in names else names[0])
			elif idx == 1:
				config.NOTIFY = not config.NOTIFY
			elif idx == 2:
				state.wake.set()
				return False
			else:
				return True
		elif k == 27:
			return False


def add_reviewer(scr, state, sel, pr):
	"""Pick a collaborator of the PR's repo (or type a login when gh cannot list them) and request their review."""
	repo, number = pr["repository"]["nameWithOwner"], pr["number"]
	draw(scr, state, sel, prompt=f" {art.SPINNER[0]} fetching collaborators of {repo}…")
	scr.refresh()  # show the prompt before blocking on gh
	me = pr.get("author", {}).get("login")
	options = [c for c in github.collaborators(repo) if c != me]
	if not options:
		login = ask(scr, state, sel, f" reviewer login for #{number}: ")
	else:
		idx = 0
		while True:
			draw(scr, state, sel, prompt=f" reviewer for #{number}:  j/k move   ⏎ request   esc cancel")
			popup(scr, 4, 3, f"request review · {repo}#{number}", options, idx)
			k = scr.getch()
			if k in (ord("j"), curses.KEY_DOWN):
				idx = (idx + 1) % len(options)
			elif k in (ord("k"), curses.KEY_UP):
				idx = (idx - 1) % len(options)
			elif k in (10, 13, curses.KEY_ENTER):
				login = options[idx]
				break
			elif k in (27, ord("q")):
				return
	if not login:
		return
	err = github.request_review(repo, number, login)
	draw(scr, state, sel, prompt=f" ✓ asked {login} to review #{number}" if not err else f" ✗ {err}"[:200])
	scr.refresh()
	curses.napms(900)
	curses.flushinp()  # keys mashed during the flash would each fire another request
	if not err:
		state.wake.set()  # refetch so the new reviewer shows on the row


def confirm(scr, state, sel, question):
	"""Draw the question in the footer and block for y/n."""
	draw(scr, state, sel, prompt=question)
	scr.refresh()
	scr.timeout(-1)
	yes = scr.getch() == ord("y")
	scr.timeout(500)
	return yes


def edit_memory(scr, state, sel, repo):
	"""Open general (repo=None) or per-repo memory in $EDITOR. Reviews read it back next run."""
	path = memory.path(repo)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	team.pull_dir(config.MEMORY_DIR, "mine")  # ponytail: n/g edit YOUR memory; pulling the team's did nothing
	# ponytail: $EDITOR writes the file itself, so no helper of ours sees it. Starting history HERE
	# commits the state before the edit, which is exactly the version you want back if you regret it.
	memory.history()
	err = shell_out(scr, [os.environ.get("EDITOR", "nano"), path])
	if err:
		confirm(scr, state, sel, f" {err}  — set $EDITOR to one you have  [any key]")
	# ponytail: n/g edit YOUR memory, which is no longer inside the team checkout — push the one we wrote
	team.push_dir(config.MEMORY_DIR, f"memory: {repo or 'general'} edited", "mine")


def ask(scr, state, sel, question):
	"""Footer text input. Returns '' on empty/escape."""
	draw(scr, state, sel, prompt=question)
	scr.refresh()
	curses.echo()
	scr.timeout(-1)
	try:
		return scr.getstr(scr.getmaxyx()[0] - 1, len(question) + 1, 200).decode().strip()
	except (KeyboardInterrupt, UnicodeDecodeError):
		return ""
	finally:
		curses.noecho()
		scr.timeout(500)


def share_screen(scr, state, sel):
	"""Facts of yours the team does not have yet: t shares one, x forgets it.

	ponytail: one at a time, not a list. A fact is a sentence you have to actually read to judge, and a
	column of clipped sentences is exactly how something wrong gets waved through into everyone's context.
	"""
	i = 0
	while True:
		items = memory.shareable()
		if not items:
			confirm(scr, state, sel, f" nothing of yours the team is missing{'' if team.on() else ' — you are not in a team'}  [any key]")
			return
		index = memory.pools()  # ponytail: one scan per redraw, not one per fact
		items.sort(key=lambda rf: -len(memory.backers(index, *rf)))  # what two people found comes first
		i %= len(items)
		repo, fact = items[i]
		who = memory.backers(index, repo, fact)
		mark = f"★ {len(who)} people found this" if len(who) > 1 else "yours"
		body = [(l, "") for l in textwrap.wrap(fact, 62)] or [("", "")]
		draw(scr, state, sel, prompt=" ")
		panel(scr, f"share with {team.NAME or 'the team'}  ·  {i + 1}/{len(items)}",
		      [(repo or "general", mark), ("", ""), *body],
		      "[t] share   [x] forget   [j/k] move   [esc] close")
		k = scr.getch()
		if k in (ord("j"), curses.KEY_DOWN):
			i += 1
		elif k in (ord("k"), curses.KEY_UP):
			i -= 1
		elif k == ord("t"):
			memory.share(repo, fact)
			team.push(f"memory: share {repo or 'general'}")
		elif k == ord("x"):
			memory.forget(repo, fact)
			team.push_dir(config.MEMORY_DIR, f"memory: forget {repo or 'general'}", "mine")
			team.push(f"memory: withdraw {repo or 'general'}")  # forget also withdraws it from the pool
		elif k in (27, ord("q")):
			return


def set_path(scr, state, sel, which):
	"""Point Memory (L) or Store (C) at another directory.

	ponytail: the filesystem keeps the setting — the old location becomes a symlink to the new one, so it
	survives a restart without a config file, the same way team mode persists as a .git in a known folder.
	"""
	what, cur, live = ("Memory", config.LOCAL_MEMORY, team.on()) if which == "L" else ("Store", config.TEAM, False)
	note = "  (the team's memory is in use; this applies when you leave)" if live else ""
	tail = ", or a git repo to clone" if which == "L" else ""
	new = ask(scr, state, sel, f" {what} directory{tail} [{knowledge.tilde(cur)}]{note}:")
	if not new:
		return
	try:
		if knowledge.is_remote(new):
			if which != "L":
				confirm(scr, state, sel, " Store is a local directory — T is what clones a team repo  [any key]")
				return
			if not confirm(scr, state, sel, f" clone {new} into {knowledge.tilde(cur)}, keeping the facts already there? [y/n]"):
				return
			err = knowledge.adopt(new)
		elif knowledge.inside_git(new) and not confirm(
				scr, state, sel, f" {new} sits in a git repo that does not ignore it — memory could be committed. continue? [y/n]"):
			return
		else:
			err = knowledge.set_local(new) if which == "L" else knowledge.set_store(new)
	except OSError as e:  # ponytail: whatever the typo was, say it on the footer — never unwind out of curses
		err = str(e)
	if err:
		confirm(scr, state, sel, f" {err}  [any key]")


def shell_out(scr, cmd, text_in=None):
	"""Leave curses, run something interactive, and come back — whatever happens. "" or an error string.

	ponytail: the restore lives in a finally. Every one of these used to be endwin / run / refresh in a
	straight line, so a pager or editor that is not installed raised between the second and third, the
	refresh never ran, and the exception unwound out of main() with the terminal already out of curses
	mode — no echo, no carriage return on newline. You get a shell that prints ^M and stair-steps its
	output, which does not look like a crash in a program you just quit.
	"""
	curses.endwin()
	try:
		subprocess.run(cmd, input=text_in, text=True)
		return ""
	except OSError as e:
		return f"{cmd[0]}: {e.strerror or e}"
	finally:
		scr.refresh()


def page(scr, state, sel, path, label):
	"""Show a file in less. ponytail: same route the dream's [v] takes — one pager, one set of habits."""
	try:
		with open(path) as f:
			body = f.read()
	except OSError as e:
		return confirm(scr, state, sel, f" {e}  [any key]")
	# ponytail: a fixed label, not the filename. less reads a bare "." in a -P string as the
	# end-of-conditional token and eats it, so "acme__api__7.md" rendered as "acme__api__7md".
	err = shell_out(scr, ["less", "-R", "-P", LESS_PROMPT.replace("%f", label)], body)
	if err:
		confirm(scr, state, sel, f" {err}  [any key]")


def copy_pre_review(scr, state, sel, pr):
	"""`Y`: put the pre-review's PATH on the clipboard. Returns what it copied, or "".

	ponytail: the path, not the contents — it is a file you open in an editor or hand to something
	else, and a whole review on the clipboard is not what anyone wants to paste.
	ponytail: a function, not eight lines in the key loop. The test for this pressed nothing and called
	github.copy itself, so neither branch of the handler ran — which is how the p handler shipped a
	NameError. A handler that cannot be driven is a handler that is not tested.
	"""
	at, _moved = review_mod.self_review_state(pr)
	if not at:
		draw(scr, state, sel, prompt=f" no pre-review of #{pr['number']} yet — p runs one")
		scr.refresh()
		return ""
	path = review_mod.self_review_path(pr["repository"]["nameWithOwner"], pr["number"])
	tool = github.copy(path)
	draw(scr, state, sel, prompt=f" ✓ copied {path}  (via {tool})" if tool != "terminal"
	     else f" sent {path} to the terminal (OSC 52)")
	scr.refresh()
	return path


def pre_review(scr, state, sel, pr):
	"""`p` on one of your own rows: read the pre-review if it still fits the diff, else offer a fresh one.

	ponytail: a function, not eight lines inside main()'s key loop. It shipped referring to a module
	screen.py does not import and crashed the moment anyone pressed p — no test could reach it where it
	was, which is exactly what the review of #8 said about this handler and about cli.self_review.
	ponytail: MINE only. GitHub refuses to let you approve your own PR, and a verdict on someone else's
	work that is never posted helps nobody — this is a pass before you ask a person.
	"""
	repo, n = pr["repository"]["nameWithOwner"], pr["number"]
	# ponytail: shared with the pane, not repeated — see review.self_review_state
	at, moved = review_mod.self_review_state(pr)
	if in_flight(state.reviews.get(pr["url"], "")):
		return
	if at and not moved:
		return page(scr, state, sel, review_mod.self_review_path(repo, n), f"pre-review of #{n}")
	if confirm(scr, state, sel, (f" #{n} changed since its pre-review. Run again? [y/n]" if moved
	                             else f" Pre-review #{n}? Nothing is posted. [y/n]")):
		state.start_self_review(pr)


def team_setup(scr, state, sel):
	if team.on():
		name = team.NAME
		# ponytail: a symlinked TEAM keeps its checkout — only the link goes. Said here, because the
		# prompt is the last place anyone reads before agreeing to something that deletes files.
		where = (f"the checkout at {knowledge.tilde(os.path.realpath(config.TEAM))} is kept"
		         if os.path.islink(config.TEAM) else f"files in {config.TEAM} are deleted")
		if not confirm(scr, state, sel, f" team {name} · {where} · leave and go back to local memory? [y/n]"):
			return
		err = knowledge.leave()
		if err:
			confirm(scr, state, sel, f" {err}  [any key]")
		else:
			state.wake.set()  # ponytail: REVIEWED must reload from the solo log, the team one is gone
		return
	repo = ask(scr, state, sel, " Team repo (owner/name, a local path, or a git URL; owner/name is created if missing):")
	if not repo:
		return
	err = team.setup(repo)
	if err and "/" in repo and not os.path.isdir(repo):  # ponytail: any clone failure of owner/name → offer to create
		if confirm(scr, state, sel, f" {err} · create {repo} as a private repo? [y/n]"):
			err = team.setup(repo, create=True)
	if err:
		confirm(scr, state, sel, f" {err}  [any key]")
	else:
		state.wake.set()  # reload REVIEWED from the team log


DREAM_SKY = "˖ ⋆ ✧ ✦ ☾ · ° ˚ z Z"


def dream_screen(scr, state, sel):
	"""Modal: run memory.dream() in a thread, animate while it runs, then offer to apply the result."""
	box = [None]  # (summary, files) or Exception
	def run():
		try:
			box[0] = memory.dream(state.model)  # module attr: --demo and tests swap it
		except Exception as e:  # noqa: BLE001 — surfaced in the panel
			box[0] = e
	threading.Thread(target=run, daemon=True).start()
	rng, t0 = random.Random(0), time.time()
	scr.timeout(120)
	try:
		while box[0] is None:
			draw(scr, state, sel, prompt=" ")
			sky = [("".join(rng.choice(DREAM_SKY) if rng.random() < 0.18 else " " for _ in range(44)), "") for _ in range(4)]
			spin = art.SPINNER[int((time.time() - t0) * 8) % len(art.SPINNER)]
			panel(scr, "dreaming", sky + [("", ""), (f"{spin}  {state.model} is tidying the memories…", f"{int(time.time() - t0)}s")],
			      "[esc] wake up without changes", accent=6)
			if scr.getch() == 27:
				return
		if isinstance(box[0], Exception):
			err = (getattr(box[0], "stderr", None) or str(box[0])).strip().splitlines() or ["?"]
			panel(scr, "dream failed", [("", ""), (err[-1][:70], "")], "[any key] back", accent=3)
			scr.timeout(-1)
			scr.getch()
			return
		summary, before, new = box[0]  # what the dream saw, not what is on disk now
		lines = [(l[:70], "") for l in summary.splitlines() if l.strip()] + [("", "")]
		# ponytail: a file going to zero is a DELETION, and it read as one more row in a list of line
		# counts. A dream emptied general.md — eight cross-repo facts — and "8 → 0" scrolled past among
		# the tidies. Marked, coloured, and sorted to the top so it cannot be the row you skim over.
		gone = sorted(n for n, t in new.items() if not t.strip() and before[n].strip())
		lines += [(n[:-3].replace("__", "/"),
		           f"{len(before[n].splitlines())} → DELETED" if n in gone
		           else f"{len(before[n].splitlines())} → {len(new[n].splitlines())}")
		          for n in sorted(new, key=lambda n: (n not in gone, n))]
		scr.timeout(-1)
		k = None
		while k not in (ord("y"), ord("n"), 27):
			draw(scr, state, sel, prompt=" ")
			panel(scr, "dream over", lines,
			      ("[y] accept — DELETES %d file%s   [v] view full   [n/esc] discard"
			       % (len(gone), "" if len(gone) == 1 else "s")) if gone else
			      "[y] accept and rewrite memory   [v] view full   [n/esc] discard",
			      accent=3 if gone else 6)
			k = scr.getch()
			if k == ord("v"):
				if err := shell_out(scr, ["less", "-R", "-P", LESS_PROMPT.replace("%f", "the dream")],
				                     dream_detail(summary, before, new)):
					confirm(scr, state, sel, f" {err}  [any key]")
		if k == ord("y") and gone:
			# ponytail: a second, separate yes for deletion only. Tidying and destroying arrived on the
			# same keypress, and the one you want almost always is the tidy — so the destructive half
			# rode in on it. This names the files; nothing else in the dream needs naming.
			what = ", ".join(n[:-3].replace("__", "/") for n in gone)
			lost = sum(len(before[n].splitlines()) for n in gone)
			if not confirm(scr, state, sel,
			               f" DELETE {what} — {lost} fact{'s' if lost != 1 else ''} lost. Sure? [y/n]"):
				return
		if k == ord("y"):
			team.pull_dir(config.MEMORY_DIR, "mine")  # a dream rewrites both sources, so both are pulled
			team.pull()
			memory.write(new)
			# ponytail: a dream is the one write that can DELETE, so it is the one that must confirm its
			# own record landed. Silently failing here is what left eight facts deleted with no commit
			# saying so, and the next review's push carrying the blame.
			# ponytail: BOTH sources. A dream rewrites mine/ and team/, so checking only mine left a
			# team file that was emptied and not committed exactly as silent as the bug this fixes.
			# `or` and not `and`: the first failure is the one worth naming, and both are checked
			# because push_dir runs either way.
			mine_err = team.push_dir(config.MEMORY_DIR, "memory: dream cleanup", "mine")
			team_err = team.push("memory: dream cleanup")
			if err := (mine_err or team_err):
				confirm(scr, state, sel,
				        f" memory rewritten, but NOT committed: {err} — a backup is in ~/.prs_backups  [any key]")
	finally:
		scr.timeout(500)


def dream_detail(summary, before, new):
	"""Summary plus a unified diff per changed file, for less."""
	out = [summary.strip(), ""]
	for n, t in new.items():
		if t.strip() != before[n].strip():
			out += list(difflib.unified_diff(before[n].splitlines(), t.strip().splitlines(),
			                                 n[:-3].replace("__", "/"), "after the dream", lineterm="", n=99))
			out.append("")
	return "\n".join(out) or "nothing changed"


def snapshot(state):
	"""Everything the settings row can change, in the shape config.save writes."""
	return {"model": state.model, "interval": state.interval, "subs": state.subs, "window": state.window,
	        "drafts": state.drafts, "depth": config.DEPTH, "effort": config.EFFORT, "notify": config.NOTIFY, "theme": config.THEME}


def main(scr, interval, auto, model):
	init_colors()
	scr.timeout(500)
	state = State(interval, model)
	team.activate()
	if auto:
		state.set_auto(True)  # baseline is empty, so everything currently review-requested gets reviewed too
	threading.Thread(target=state.loop, daemon=True).start()
	sel, current, saved = 0, None, snapshot(state)
	while True:
		# ponytail: any in-flight verb, not the one literal string — a pre-review spins for the same reason
		spinning = state.fetched_at is None or state.fetching or any(in_flight(v) for v in state.reviews.values())
		sel, current = draw(scr, state, sel)  # ponytail: redraw every tick, cheap enough
		scr.refresh()  # draw() only stages; noutrefresh clears the touched flag so getch() would not flush it
		scr.timeout(50 if spinning else 150 if SCROLLING[0] else 500)  # spin smoothly while busy, glide the marquee, else idle
		k = scr.getch()
		if snapshot(state) != saved:  # ponytail: one save site; any key path that changed a setting lands here
			saved = snapshot(state)
			config.save(saved)
		if k == ord("q") or (k == 27 and esc_menu(scr, state, sel)):
			config.save(snapshot(state))  # the menu may have changed the theme or notify on the way out
			return
		if k in (ord("j"), curses.KEY_DOWN):
			sel += 1
		elif k in (ord("k"), curses.KEY_UP):
			sel -= 1
		elif k == ord("f"):
			state.wake.set()  # ponytail: f for fetch. r became review, and o was already open-in-browser
		elif k == ord("a"):
			n = 0 if state.auto else len(state.pending_rr())
			state.set_auto(not state.auto, include_existing=n > 0 and confirm(
				scr, state, sel, f" Auto on. Also review the {n} already listed? [y/n]"))
		elif k == ord("D"):
			state.drafts = not state.drafts
		elif k == ord("?"):
			state.hints = not state.hints
		elif k == ord(" ") and current and current["section"] == "REVIEWED":
			state.expanded ^= {current["url"]}
		elif k in (ord("m"), ord("d"), ord("e"), ord("s"), ord("t"), ord("i")):
			dropdown(scr, state, sel, chr(k))
		elif k in (ord("R"), ord("V"), ord("K")):
			group_menu(scr, state, sel, chr(k))
		elif k in (ord("L"), ord("C")):
			set_path(scr, state, sel, chr(k))
		elif k == ord("S"):
			settings_menu(scr, state, sel)
		elif k == ord("o") and current:
			github.open_in_browser(current["url"])
		elif k == ord("+") and current and current["section"] == "MINE":
			add_reviewer(scr, state, sel, current)
		elif k == ord("p") and current and current["section"] == "MINE":
			pre_review(scr, state, sel, current)
		elif k == ord("Y") and current:
			copy_pre_review(scr, state, sel, current)
		elif k == ord("y") and current:
			tool = github.copy(current["url"])
			draw(scr, state, sel, prompt=f" ✓ copied {current['url']}  (via {tool})" if tool != "terminal" else
			     f" sent {current['url']} to the terminal (OSC 52) — if nothing landed, install wl-clipboard or xclip")
			scr.refresh()
			curses.napms(600)  # ponytail: a blocking flash beats a timed footer state
			curses.flushinp()  # spamming y queues keypresses that would each copy and flash again
		elif k == ord("g") or (k == ord("n") and current):
			edit_memory(scr, state, sel, None if k == ord("g") else current["repository"]["nameWithOwner"])
		elif k == ord("Z"):
			dream_screen(scr, state, sel)
		elif k == ord("P"):
			share_screen(scr, state, sel)
		elif k == ord("T"):
			team_setup(scr, state, sel)
		elif k == ord("u") and state.update:
			update_screen(scr, state, sel)
		elif k == ord("v") and current and (current.get("review") or log.last(current["url"])):
			# ponytail: reading a past review moved off ⏎ with the rest. `v` because the dream already
			# uses [v] view full for exactly this — one idiom for "show me the whole thing in less".
			# ponytail: any row with a review, not only a REVIEWED one. The pane shows a summary of the
			# findings for the selected PR whatever section it is in, so the key that opens the whole
			# thing has to reach as far as the summary does.
			rev = current.get("review") or log.last(current["url"])
			if err := shell_out(scr, ["less", "-R", "-P", LESS_PROMPT.replace("%f", f"#{current['number']}")],
			                     log.detail(rev)):
				confirm(scr, state, sel, f" {err}  [any key]")
		elif k == ord("r") and current and current["section"] == "REVIEW REQUESTED":
			# ponytail: r is REVIEW now, as the design always labelled it. Refresh moved to f. This is a
			# muscle-memory break on two keys people use constantly, taken deliberately rather than by
			# redraw — the branch talked itself out of it once and left ⏎ and r meaning the older thing.
			if in_flight(state.reviews.get(current["url"], "")) or current["url"] in state.reviews:
				pass  # already reviewed or in flight
			elif confirm(scr, state, sel, f" Claude review + post verdict on #{current['number']}? [y/n]"):
				state.start_review(current)
		elif k in (10, 13, curses.KEY_ENTER):
			# ponytail: ⏎ is the pane, which is what the design drew. Say so when there is no room for
			# one — the key silently doing nothing reads as the app being broken, not the terminal.
			h_, w_ = scr.getmaxyx()
			if w_ >= PANE_MIN and h_ >= PANE_MIN_H:
				state.pane = not state.pane
			else:
				draw(scr, state, sel, prompt=f" the detail pane needs {PANE_MIN}x{PANE_MIN_H}; this is {w_}x{h_}")
				scr.refresh()
				curses.napms(900)
