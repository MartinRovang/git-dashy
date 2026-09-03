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
from ..core import github, knowledge, log, memory, team, update
from ..core.state import State
from . import art
from .rows import age, rows

LESS_PROMPT = "review of %f  |  q close  j/k scroll  /search"
FOOTER = " j/k move  o open  ⏎ review / details  ␣ fold  a auto  m model  d depth  e effort  t window  i interval  s summaries  D drafts  S/R/V settings  n/g memory  Z dream  T team  u update  r refresh  ? keys  q quit"
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
]


ANCHORS = {}  # setting key -> (y, x) where its label was last drawn; dropdowns hang from it


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


def header_groups(state):
	"""The settings groups on the second header row: (label, key, rows), rows = (setting key, name, value, tone).
	tone: None / "on" (yellow) / "err" (red). ponytail: one table, drawn as pairs and listed by the group key."""
	st = settings(state)
	def row(k):
		label, _, current, _, show = st[k]
		return (k, label, show(current), None)
	reviewer = [row("m"), row("d"), row("e")]
	view = [row("s"), ("D", "Drafts", "shown" if state.drafts else "hidden", "on" if state.drafts else None), row("t")]
	# Memory is your own dir, in a team or not; the team is a second source read alongside it, shown below
	know = [("L", "Memory", knowledge.show(knowledge.effective()), None),
	        ("T", "Team", team.ERROR[:40] if team.ERROR else (team.NAME or "off"),  # ponytail: clipped, T shows it whole
	         "err" if team.ERROR else ("on" if team.on() else None))]
	if knowledge.store_moved():  # ponytail: a row only once it says something — at the default it just repeats Memory
		know.append(("C", "Store", knowledge.show(config.TEAM), None))
	return [("Reviewer", "R", reviewer), ("View", "V", view), ("Knowledge", "K", know)]


def init_colors():
	curses.curs_set(0)
	curses.use_default_colors()
	many = curses.COLORS >= 256
	for pair, fg256, fg8, bg256, bg8 in COLORS:
		curses.init_pair(pair, fg256 if many else fg8, (bg256 if many else bg8) if bg256 != -1 else -1)


def splash(scr, h, w, spin):
	"""ponytail: centred one-liners, clipped by addnstr — no layout engine."""
	def mid(y, t, attr):
		if 4 <= y < h - 1:
			scr.addnstr(y, max(0, (w - len(t)) // 2), t, w - 1, attr)
	logo = w >= art.LOGO_W + 2 and h >= 8 + len(art.LOGO)
	y0 = h // 2 - (4 + (len(art.LOGO) + 1 if logo else 0)) // 2
	mid(y0, f"{spin}  fetching pull requests…", C(5) | curses.A_BOLD)
	mid(y0 + 2, "created by", C(1))
	mid(y0 + 3, art.NAME, C(6) | curses.A_BOLD)
	for i, line in enumerate(art.LOGO if logo else []):
		mid(y0 + 5 + i, line, C(5) | curses.A_BOLD)


def draw(scr, state, sel, prompt=None):
	scr.erase()
	ANCHORS.clear()  # stale anchors would hang a dropdown from a chip that is no longer drawn
	h, w = scr.getmaxyx()
	with state.lock:
		sections, fetched_at, reviews = state.sections, state.fetched_at, dict(state.reviews)
	rs = rows(sections, state.window, state.subs, state.drafts, state.expanded)
	prs = [i for i, (k, _) in enumerate(rs) if k == "pr"]
	sel = max(0, min(sel, len(prs) - 1)) if prs else 0
	cur = prs[sel] if prs else -1
	# ponytail: naive scroll keeps the selected row on screen, no smooth scrolling
	top = max(0, cur - max(0, h - 7)) if cur >= 0 else 0  # keeps the selected row one above the last list row (h - 2)
	all_prs = [p for k, p in rs if k == "pr"]
	one_owner = len({p["repository"]["nameWithOwner"].split("/")[0] for p in all_prs}) == 1
	def refof(p):  # ponytail: hide the org when every PR shares it
		return f"{p['repository']['name'] if one_owner else p['repository']['nameWithOwner']}#{p['number']}"
	ref_w = max([len(refof(p)) for p in all_prs] + [10])
	auth_w = max([len(p.get("author", {}).get("login", "")) for p in all_prs] + [4])

	if h < 4 or w < 8:  # ponytail: the header alone needs three rows plus the footer; draw nothing rather than fault
		scr.refresh()
		return sel, (rs[cur][1] if cur >= 0 else None)

	# header: primary row = identity + live state, secondary row = settings grouped by what they steer,
	# then a half-block fade row and a blank one so the list starts under a soft edge with some air
	total = sum(len(p) for _, p, _ in sections if p)
	spin = art.SPINNER[int(time.time() * 8) % len(art.SPINNER)]  # ponytail: frame from the clock, no animation state
	rspin = art.REFRESH_SPINNER[int(time.time() * 10) % len(art.REFRESH_SPINNER)]  # 10fps under the 20fps tick: every frame lands on a redraw
	ago = None if fetched_at is None else age(datetime.fromtimestamp(fetched_at, timezone.utc).isoformat())
	status = f"{spin} fetching…" if ago is None else "updated just now" if ago == "now" else f"updated {ago} ago"
	nxt = "" if fetched_at is None else f"{rspin} refreshing…" if state.fetching else \
		f"next refresh {max(0, int(fetched_at + state.interval - time.time()))}s / {state.interval // 60}m"
	vals = list(reviews.values())
	running = sum(v == "reviewing..." for v in vals)
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
		if kind == "head":
			name, count = payload.rsplit(" (", 1)
			scr.addnstr(y, 1, name, w - 2, C(2) | curses.A_BOLD)
			scr.addnstr(y, min(w - 2, 1 + len(name)), f" ({count}", max(1, w - 2 - len(name)), C(1))
		elif kind == "err":
			scr.addnstr(y, 3, payload, w - 4, C(3))
		elif kind == "empty":
			scr.addnstr(y, 3, "none", w - 4, C(1))
		elif kind == "sub":
			x = 11 + ref_w
			t = " ".join(payload.split())
			room = min(70, w - 1 - x - 2)  # ponytail: hard cap so a wordy model can't clog the list
			if room > 4:
				scr.addnstr(y, x, "↳ ", 2, C(5))
				scr.addnstr(y, x + 2, t if len(t) <= room else t[:room - 1] + "…", room, C(1) | curses.A_ITALIC)
		elif kind == "pr":
			p = payload
			is_cur = i == cur
			base = curses.A_REVERSE if is_cur else 0  # ponytail: reverse video works on any theme
			ref = refof(p)
			st = p.get("status") or reviews.get(p["url"]) or p.get("prev", "")
			if st == "reviewing...":
				st = f"{spin} reviewing…"  # ponytail: same clock-driven frame as the header
			st_attr = C(3) if st.startswith("error") else C(4) if st.startswith("✓") else C(3) if st.startswith("✗") else C(5)
			x = 1
			def put(text, attr=0, pad=0):
				nonlocal x
				if x < w - 1:
					scr.addnstr(y, x, text.ljust(pad), w - 1 - x, base if is_cur else attr)  # colour pairs don't OR
				x += max(len(text), pad)
			if is_cur:
				scr.addnstr(y, 0, " " * (w - 1), w - 1, base)
			put("▸ " if is_cur else "  ", C(5) | curses.A_BOLD)
			if p.get("child"):
				put("    └ ", C(1))  # ponytail: fixed indent, tree is only ever one level deep
			put(age(p["updatedAt"]).rjust(4), C(1))
			put("  ")
			put(ref, C(6), ref_w)
			put("  ")
			put("draft " if p.get("isDraft") else "", C(5))
			tag = p.get("tag", "") + (f"  ▸ +{p['more']}" if p.get("more") else "  ▾" if p.get("open") else "")
			title_w = w - 1 - x - auth_w - 3 - (len(st) + 3 if st else 0) - (len(tag) + 2 if tag else 0)
			t = p["title"]
			put(t if len(t) <= title_w else t[:max(0, title_w - 1)] + "…", curses.A_BOLD if is_cur else 0, title_w)
			put("  ")
			put(p.get("author", {}).get("login", ""), C(1), auth_w)
			if tag:
				put("  ")
				put(tag, C(1))
			if st:
				put("  ")
				put(st, st_attr | curses.A_BOLD)

	foot = prompt or FOOTER
	scr.addnstr(h - 1, 0, " " * (w - 1), w - 1, C(7))
	scr.addnstr(h - 1, 0, foot, w - 1, (C(8) | curses.A_BOLD) if prompt else C(7))
	scr.refresh()
	return sel, (rs[cur][1] if cur >= 0 else None)


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
		if row < h - 1:
			scr.addnstr(row, x, text, inner, attr)
	put(y + 1, "╭" + "─" * (inner - 2) + "╮", C(6))
	if y + 1 < h - 1:
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


def confirm(scr, state, sel, question):
	"""Draw the question in the footer and block for y/n."""
	draw(scr, state, sel, prompt=question)
	scr.timeout(-1)
	yes = scr.getch() == ord("y")
	scr.timeout(500)
	return yes


def edit_memory(scr, repo):
	"""Open general (repo=None) or per-repo memory in $EDITOR. Reviews read it back next run."""
	path = memory.path(repo)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	team.pull()
	curses.endwin()
	subprocess.run([os.environ.get("EDITOR", "nano"), path])
	scr.refresh()
	# ponytail: n/g edit YOUR memory, which is no longer inside the team checkout — push the one we wrote
	team.push_dir(config.MEMORY_DIR, f"memory: {repo or 'general'} edited", "mine")


def ask(scr, state, sel, question):
	"""Footer text input. Returns '' on empty/escape."""
	draw(scr, state, sel, prompt=question)
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


def team_setup(scr, state, sel):
	if team.on():
		name = team.NAME
		if not confirm(scr, state, sel, f" team {name} · files in {config.TEAM} · leave and go back to local memory? [y/n]"):
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
		summary, new = box[0]
		before = memory.files()
		lines = [(l[:70], "") for l in summary.splitlines() if l.strip()] + [("", "")]
		lines += [(n[:-3].replace("__", "/"), f"{len(before[n].splitlines())} → {len(t.splitlines())}") for n, t in new.items()]
		scr.timeout(-1)
		k = None
		while k not in (ord("y"), ord("n"), 27):
			draw(scr, state, sel, prompt=" ")
			panel(scr, "dream over", lines, "[y] accept and rewrite memory   [v] view full   [n/esc] discard", accent=6)
			k = scr.getch()
			if k == ord("v"):
				curses.endwin()
				subprocess.run(["less", "-R", "-P", LESS_PROMPT.replace("%f", "the dream")],
				               input=dream_detail(summary, before, new), text=True)
				scr.refresh()
		if k == ord("y"):
			team.pull()
			memory.write(new)
			team.push_dir(config.MEMORY_DIR, "memory: dream cleanup", "mine")  # a dream rewrites both sources
			team.push("memory: dream cleanup")
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


def main(scr, interval, auto, model):
	init_colors()
	scr.timeout(500)
	state = State(interval, model)
	team.activate()
	if auto:
		state.set_auto(True)  # baseline is empty, so everything currently review-requested gets reviewed too
	threading.Thread(target=state.loop, daemon=True).start()
	sel, current = 0, None
	while True:
		spinning = state.fetched_at is None or state.fetching or "reviewing..." in state.reviews.values()
		scr.timeout(50 if spinning else 500)  # spin smoothly while fetching, refreshing or reviewing
		sel, current = draw(scr, state, sel)  # ponytail: redraw every tick, cheap enough
		k = scr.getch()
		if k in (ord("q"), 27):
			return
		if k in (ord("j"), curses.KEY_DOWN):
			sel += 1
		elif k in (ord("k"), curses.KEY_UP):
			sel -= 1
		elif k == ord("r"):
			state.wake.set()
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
		elif k == ord("g") or (k == ord("n") and current):
			edit_memory(scr, None if k == ord("g") else current["repository"]["nameWithOwner"])
		elif k == ord("Z"):
			dream_screen(scr, state, sel)
		elif k == ord("P"):
			share_screen(scr, state, sel)
		elif k == ord("T"):
			team_setup(scr, state, sel)
		elif k == ord("u") and state.update:
			update_screen(scr, state, sel)
		elif k in (10, 13, curses.KEY_ENTER) and current:
			if current["section"] == "REVIEWED":
				curses.endwin()
				subprocess.run(["less", "-R", "-P", LESS_PROMPT.replace("%f", f"#{current['number']}")],
				               input=log.detail(current["review"]), text=True)
				scr.refresh()
			elif current["section"] != "REVIEW REQUESTED":
				github.open_in_browser(current["url"])
			elif current["url"] in state.reviews:
				pass  # already reviewed or in flight
			elif confirm(scr, state, sel, f" Claude review + post verdict on #{current['number']}? [y/n]"):
				state.start_review(current)
