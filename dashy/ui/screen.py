"""The curses screen: colours, one draw() per tick, and the key loop."""
import curses
import subprocess
import threading
import time
from datetime import datetime, timezone

from .. import HERE, VERSION, config
from ..core import github, log, update
from ..core.state import State
from . import art
from .rows import age, rows

LESS_PROMPT = "review of %f  |  q close  j/k scroll  /search"
FOOTER = " j/k move  o open  ⏎ review / details  a auto  m model  t window  s summaries  u update  r refresh  q quit"
COLORS = [  # (pair, 256-colour fg, 8-colour fg, bg256, bg8)
	(1, 244, curses.COLOR_WHITE, -1, -1),          # dim
	(2, 75, curses.COLOR_CYAN, -1, -1),            # section header
	(3, 203, curses.COLOR_RED, -1, -1),            # error / changes requested
	(4, 78, curses.COLOR_GREEN, -1, -1),           # approved
	(5, 221, curses.COLOR_YELLOW, -1, -1),         # draft / in flight
	(6, 111, curses.COLOR_BLUE, -1, -1),           # repo ref
	(7, 252, curses.COLOR_WHITE, 236, curses.COLOR_BLACK),   # bars
	(8, 16, curses.COLOR_BLACK, 75, curses.COLOR_CYAN),      # bar badge
]


def C(n):
	return curses.color_pair(n)


def init_colors():
	curses.curs_set(0)
	curses.use_default_colors()
	many = curses.COLORS >= 256
	for pair, fg256, fg8, bg256, bg8 in COLORS:
		curses.init_pair(pair, fg256 if many else fg8, (bg256 if many else bg8) if bg256 != -1 else -1)


def splash(scr, h, w, spin):
	"""ponytail: centred one-liners, clipped by addnstr — no layout engine."""
	def mid(y, t, attr):
		if 2 <= y < h - 1:
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
	h, w = scr.getmaxyx()
	with state.lock:
		sections, fetched_at, reviews = state.sections, state.fetched_at, dict(state.reviews)
	rs = rows(sections, state.window, state.subs)
	prs = [i for i, (k, _) in enumerate(rs) if k == "pr"]
	sel = max(0, min(sel, len(prs) - 1)) if prs else 0
	cur = prs[sel] if prs else -1
	# ponytail: naive scroll keeps the selected row on screen, no smooth scrolling
	top = max(0, cur - (h - 5)) if cur >= 0 else 0
	all_prs = [p for k, p in rs if k == "pr"]
	one_owner = len({p["repository"]["nameWithOwner"].split("/")[0] for p in all_prs}) == 1
	def refof(p):  # ponytail: hide the org when every PR shares it
		return f"{p['repository']['name'] if one_owner else p['repository']['nameWithOwner']}#{p['number']}"
	ref_w = max([len(refof(p)) for p in all_prs] + [10])
	auth_w = max([len(p.get("author", {}).get("login", "")) for p in all_prs] + [4])

	# header bar
	total = sum(len(p) for _, p, _ in sections if p)
	spin = art.SPINNER[int(time.time() * 8) % len(art.SPINNER)]  # ponytail: frame from the clock, no animation state
	status = f"{spin} fetching…" if fetched_at is None else \
		f"updated {age(datetime.fromtimestamp(fetched_at, timezone.utc).isoformat())} ago"
	scr.addnstr(0, 0, " " * (w - 1), w - 1, C(7))
	scr.addnstr(0, 1, f" PRs {total} ", w - 2, C(8) | curses.A_BOLD)
	x0 = min(w - 2, 10 + len(str(total)))
	scr.addnstr(0, x0, f"  {status}", max(1, w - 2 - x0), C(7))
	if state.auto:
		scr.addnstr(0, max(0, w - 8), " AUTO ", 7, C(5) | curses.A_REVERSE | curses.A_BOLD)
	if state.update:
		badge = f" ↑ v{state.update} · u "
		scr.addnstr(0, max(0, w - 9 - len(badge)), badge, len(badge), C(4) | curses.A_REVERSE | curses.A_BOLD)

	# stats strip
	vals = list(reviews.values())
	running = sum(v == "reviewing..." for v in vals)
	nxt = "" if fetched_at is None else f"next refresh {max(0, int(fetched_at + state.interval - time.time()))}s"
	x = 1
	for label, n, attr in (
		("agents running", running, C(5) | (curses.A_BOLD if running else 0)),
		("v" + VERSION, "", C(1)),
		("model: " + state.model, "", C(6)),
		("summaries: " + state.subs, "", C(6)),
		("approved", sum(v.startswith("✓") for v in vals), C(4)),
		("changes", sum(v.startswith("✗") for v in vals), C(3)),
		("commented", sum(v.startswith("~") for v in vals), C(1)),
		("errors", sum(v.startswith("error") for v in vals), C(3)),
	):
		txt = f"{n} {label}".strip()
		if x + len(txt) < w - 1:
			scr.addnstr(1, x, str(n), w - 1 - x, attr | curses.A_BOLD)
			scr.addnstr(1, x + len(str(n)), f" {label}   ", w - 1 - x - len(str(n)), C(1))
		x += len(txt) + 3
	if nxt and x + len(nxt) < w - 1:
		scr.addnstr(1, w - 1 - len(nxt), nxt, len(nxt), C(1))

	if not rs and fetched_at is None:
		splash(scr, h, w, spin)

	for y, (kind, payload) in enumerate(rs[top:top + h - 3], start=2):
		i = top + y - 2
		if kind == "head":
			name, count = payload.rsplit(" (", 1)
			scr.addnstr(y, 1, name, w - 2, C(2) | curses.A_BOLD)
			scr.addnstr(y, min(w - 2, 1 + len(name)), f" ({count}", w - 2 - len(name), C(1))
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
			put(age(p["updatedAt"]).rjust(4), C(1))
			put("  ")
			put(ref, C(6), ref_w)
			put("  ")
			put("draft " if p.get("isDraft") else "", C(5))
			title_w = w - 1 - x - auth_w - 3 - (len(st) + 3 if st else 0)
			t = p["title"]
			put(t if len(t) <= title_w else t[:max(0, title_w - 1)] + "…", curses.A_BOLD if is_cur else 0, title_w)
			put("  ")
			put(p.get("author", {}).get("login", ""), C(1), auth_w)
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


def confirm(scr, state, sel, question):
	"""Draw the question in the footer and block for y/n."""
	draw(scr, state, sel, prompt=question)
	scr.timeout(-1)
	yes = scr.getch() == ord("y")
	scr.timeout(500)
	return yes


def cycle_through(values, current):
	return values[(values.index(current) + 1) % len(values)] if current in values else values[0]


def main(scr, interval, auto, model):
	init_colors()
	scr.timeout(500)
	state = State(interval, model)
	if auto:
		state.set_auto(True)  # baseline is empty, so everything currently review-requested gets reviewed too
	threading.Thread(target=state.loop, daemon=True).start()
	sel, current = 0, None
	while True:
		scr.timeout(80 if state.fetched_at is None else 500)  # spin smoothly until the first fetch lands
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
			state.set_auto(not state.auto)
		elif k == ord("s"):
			state.subs = cycle_through(config.SUBS, state.subs)
		elif k == ord("t"):
			state.window = cycle_through(config.WINDOWS, state.window)
		elif k == ord("m"):
			state.model = cycle_through(config.MODELS, state.model)
		elif k == ord("o") and current:
			github.open_in_browser(current["url"])
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
