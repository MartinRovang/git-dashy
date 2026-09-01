#!/usr/bin/env python3
"""Terminal dashboard of open PRs: mine, review-requested, assigned. Refreshes on a timer.

Keys: j/k or arrows move, o open in browser, Enter on a REVIEW REQUESTED row = Claude reviews it and
posts the verdict (approve / request changes / comment) and logs it to ~/.prs_reviewed.jsonl (shown in the
REVIEWED section, Enter there opens summary + review in less), a toggle auto mode (Claude reviews every
review-requested PR that appears after you turn it on), t cycle the REVIEWED window (1h / 4h / 6h / all, default 4h),
s cycle summary lines (all / open PRs only / off), u pull the update git says is waiting and restart,
r refresh, q quit.
Usage: prs.py [--interval SECONDS] [--auto] [--model NAME] [--demo]
--demo: canned PRs and a fake reviewer, nothing touches gh, claude or your real log.
Default model: opus, or PRS_MODEL env var. Key m cycles opus / sonnet / fable at runtime.
Usage: prs.py [--interval SECONDS]  (default 300)
"""
import curses
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import cycle

FIELDS = "number,title,repository,url,updatedAt,isDraft,author"
SECTIONS = [
	("MINE", "--author=@me"),
	("REVIEW REQUESTED", "--review-requested=@me"),
	("ASSIGNED", "--assignee=@me"),
]


def fetch():
	seen, out = set(), []
	for name, flag in SECTIONS:
		try:
			raw = subprocess.run(
				["gh", "search", "prs", "--state=open", flag, "--json", FIELDS, "--limit", "100"],
				capture_output=True, text=True, check=True, timeout=60,
			).stdout
			prs = json.loads(raw)
		except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
			prs, err = [], getattr(e, "stderr", None) or str(e)
			out.append((name, None, err.strip()))
			continue
		prs = [p for p in prs if p["url"] not in seen]  # ponytail: dedup across sections, first section wins
		seen.update(p["url"] for p in prs)
		prs.sort(key=lambda p: p["updatedAt"], reverse=True)
		out.append((name, prs, None))
	out.append(("REVIEWED", reviewed(), None))  # ponytail: not deduped, a reviewed PR may still be open above
	return out


def age(iso):
	s = (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
	for unit, div in (("d", 86400), ("h", 3600), ("m", 60)):
		if s >= div:
			return f"{int(s // div)}{unit}"
	return "now"


REVIEW_PROMPT = """Review pull request {repo}#{number}. Use `gh pr view {number} --repo {repo}` and
`gh pr diff {number} --repo {repo}` to read it. Look for bugs, logic errors, security issues and missing tests.
Respond with ONLY a JSON object, no prose, no code fences:
{{"verdict": "approve" | "request_changes" | "comment", "summary": "<one line, max 12 words: what the PR changes>",
 "body": "<markdown review, concise, list concrete findings with file:line>"}}
Use request_changes only for real defects, approve if it is mergeable, comment if unsure."""
REVIEW_TOOLS = "Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh api:*)"
BANNER = [
	"███╗   ███╗ █████╗ ██████╗ ████████╗██╗███╗   ██╗",
	"████╗ ████║██╔══██╗██╔══██╗╚══██╔══╝██║████╗  ██║",
	"██╔████╔██║███████║██████╔╝   ██║   ██║██╔██╗ ██║",
	"██║╚██╔╝██║██╔══██║██╔══██╗   ██║   ██║██║╚██╗██║",
	"██║ ╚═╝ ██║██║  ██║██║  ██║   ██║   ██║██║ ╚████║",
	"╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝╚═╝  ╚═══╝",
]
SPLASH_MIN = 1.0  # seconds the startup splash stays up even if gh is fast
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
MODELS = ["opus", "sonnet", "fable"]  # ponytail: cycle list, pass any name via --model
DEFAULT_MODEL = os.environ.get("PRS_MODEL", "opus")  # override: PRS_MODEL=opus ./prs.py
LOG = os.environ.get("PRS_LOG", os.path.expanduser("~/.prs_reviewed.jsonl"))  # ponytail: jsonl, one review per line
HERE = os.path.dirname(os.path.realpath(__file__))  # realpath: installed as a symlink on PATH


def update_available():
	"""Commits this checkout is behind its upstream branch. ponytail: git is the package manager, no version file."""
	try:
		def g(*a):
			return subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, check=True, timeout=60).stdout.strip()
		g("fetch", "-q")
		return int(g("rev-list", "--count", "HEAD..@{u}"))
	except Exception:
		return 0  # not a clone, no upstream, offline — nothing to offer


def apply_update():
	"""git pull, then re-exec so the new code is what keeps running. Returns an error string, or never returns."""
	try:
		subprocess.run(["git", "-C", HERE, "pull", "--ff-only", "-q"],
		               capture_output=True, text=True, check=True, timeout=120)
	except subprocess.CalledProcessError as e:
		return (e.stderr or "pull failed").strip().splitlines()[-1][:60]
	except Exception as e:
		return str(e)[:60]
	os.execv(sys.executable, [sys.executable, os.path.realpath(__file__), *sys.argv[1:]])
SUBS = ["all", "open", "off"]  # which rows get a summary line under them
WINDOWS = [1, 4, 6, None]  # hours of REVIEWED history to show, None = all
STATUS = {"approve": "✓ approved", "request_changes": "✗ changes requested", "comment": "~ commented"}


def reviewed():
	"""PR dicts from the log, newest first, each with a 'review' entry and a 'status' string."""
	try:
		lines = open(LOG).read().splitlines()
	except FileNotFoundError:
		return []
	out = []
	for line in reversed(lines):
		e = json.loads(line)
		out.append({"title": "?", "isDraft": False, **e["pr"], "review": e, "status": STATUS[e["verdict"]], "updatedAt": e["at"]})
	return out


def mark_rereviews(sections):
	"""Tag REVIEW REQUESTED rows already in the log and updated since as p['prev']. Returns their urls."""
	last = {}
	for p in [p for n, prs, _ in sections if n == "REVIEWED" for p in prs or []]:
		last.setdefault(p["url"], p["review"])  # newest first
	out = []
	for p in [p for n, prs, _ in sections if n == "REVIEW REQUESTED" for p in prs or []]:
		e = last.get(p["url"])
		if e and datetime.fromisoformat(p["updatedAt"]) > datetime.fromisoformat(e["at"]):
			p["prev"] = f"↻ re-review · was {STATUS[e['verdict']]}"
			out.append(p["url"])
	return out


def detail(e):
	p, at = e["pr"], datetime.fromisoformat(e["at"]).astimezone().strftime("%Y-%m-%d %H:%M")
	ref = f"{p['repository']['nameWithOwner']}#{p['number']}"
	bar = "─" * min(78, max(len(ref) + len(p["title"]) + 2, 40))
	return (f"{bar}\n{ref}  {p['title']}\n{bar}\n"
	        f"  author   {p.get('author', {}).get('login', '?')}\n  url      {p['url']}\n"
	        f"  reviewed {at} by {e['model']}  →  {STATUS[e['verdict']]}\n\n"
	        f"WHAT THE PR DOES\n\n{e['summary'] or '(no summary)'}\n\n"
	        f"REVIEW\n\n{e['body']}\n\n{bar}\nq close   j/k or ↑/↓ scroll   o open in browser (from the list)\n")


LESS_PROMPT = "review of %f  |  q close  j/k scroll  /search"


def log_review(pr, model, verdict, at=None):
	entry = {"at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model, "pr": pr,
	         "verdict": verdict["verdict"], "summary": verdict.get("summary", ""), "body": verdict["body"]}
	with open(LOG, "a") as f:
		f.write(json.dumps(entry) + "\n")
	return STATUS[verdict["verdict"]]


def review(pr, model):
	"""Run claude headless on the PR, post the verdict with gh. Returns a status string for the row."""
	repo, n = pr["repository"]["nameWithOwner"], pr["number"]
	try:
		out = subprocess.run(
			["claude", "-p", REVIEW_PROMPT.format(repo=repo, number=n), "--output-format", "json",
			 "--allowedTools", REVIEW_TOOLS, "--model", model],
			capture_output=True, text=True, check=True, timeout=900,
		).stdout
		text = json.loads(out)["result"].strip()
		verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
		flag = {"approve": "--approve", "request_changes": "--request-changes", "comment": "--comment"}[verdict["verdict"]]
		subprocess.run(["gh", "pr", "review", str(n), "--repo", repo, flag, "--body", verdict["body"]],
		               capture_output=True, text=True, check=True, timeout=60)
		return log_review(pr, model, verdict)
	except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, ValueError) as e:
		return "error: " + ((getattr(e, "stderr", None) or str(e)).strip().splitlines() or ["?"])[-1][:80]


def demo():
	"""ponytail: swap fetch/review for canned data so the UI can be eyeballed without gh or claude."""
	global LOG
	LOG = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"prs-demo-{os.getpid()}.jsonl")
	now = datetime.now(timezone.utc)
	def pr(n, title, repo="acme/api", author="alice", hours=1, draft=False):
		return {"number": n, "title": title, "url": f"https://github.com/{repo}/pull/{n}", "isDraft": draft,
		        "repository": {"nameWithOwner": repo, "name": repo.split("/")[1]}, "author": {"login": author},
		        "updatedAt": (now - timedelta(hours=hours)).isoformat()}
	mine = [pr(101, "Add retry to webhook client", hours=2), pr(98, "WIP: migrate to pydantic v2", hours=30, draft=True)]
	rr = [pr(212, "Fix off-by-one in pagination", "acme/web", "bob", 1),
	      pr(207, "Cache user lookups in session middleware", "acme/web", "carol", 5),
	      pr(55, "Rotate signing keys and bump KMS alias", "acme/infra", "dave", 48)]
	late = pr(213, "Hotfix: null check in export job", "acme/web", "bob", 0)  # appears on 3rd refresh, exercises auto
	assigned = [pr(300, "Flaky integration test in CI", "acme/api", "erin", 72)]
	seed = [(pr(180, "Refactor auth middleware", "acme/api", "frank", 3),
	         {"verdict": "approve", "summary": "Splits auth middleware into token parsing and policy checks.",
	          "body": "LGTM. Clean split, existing tests still cover both paths."}),
	        (pr(44, "Add S3 lifecycle rules", "acme/infra", "grace", 5),
	         {"verdict": "request_changes", "summary": "Expires logs after 30 days, moves backups to Glacier.",
	          "body": "- `infra/s3.tf:31` rule also matches the `backups/` prefix, would delete backups after 30d\n- no plan output attached"})]
	for p, v in seed:
		log_review(p, "opus", v, at=p["updatedAt"])
	fetches = [0]
	def fake_fetch():
		fetches[0] += 1
		time.sleep(1)
		rr_now = rr + ([late] if fetches[0] >= 3 else []) + [dict(seed[1][0], updatedAt=now.isoformat())]  # re-requested
		return [("MINE", mine, None), ("REVIEW REQUESTED", rr_now, None), ("ASSIGNED", assigned, None),
		        ("REVIEWED", reviewed(), None)]
	verdicts = cycle([
		{"verdict": "approve", "summary": "Fixes pagination when the page index is zero.", "body": "LGTM, regression test added."},
		{"verdict": "request_changes", "summary": "Caches user lookups for the lifetime of a session.",
		 "body": "- `web/session.py:88` cache never invalidated on logout\n- missing test for cache miss path"},
		{"verdict": "comment", "summary": "Rotates the signing keys and points the KMS alias at the new key.",
		 "body": "Unsure whether old tokens must stay valid during rollover; please confirm."},
		None,
	])
	def fake_review(p, model):
		time.sleep(5)
		v = next(verdicts)
		return log_review(p, model, v) if v else "error: claude: rate limit exceeded, retry in 60s"
	globals()["fetch"], globals()["review"] = fake_fetch, fake_review
	globals()["update_available"] = lambda: 0


class State:
	def __init__(self, interval, model=DEFAULT_MODEL):
		self.interval, self.sections, self.fetched_at, self.lock = interval, [], None, threading.Lock()
		self.model = model
		self.wake, self.reviews = threading.Event(), {}  # reviews: url -> status string
		self.auto, self.auto_baseline = False, None  # baseline: RR urls present when auto was switched on
		self.window, self.subs = 4, "all"
		self.update = 0  # commits behind upstream, refreshed with each fetch

	def set_auto(self, on):
		with self.lock:
			self.auto = on
			self.auto_baseline = set(self._rr_urls()) if on else None

	def _rr_urls(self):
		return [p["url"] for name, prs, _ in self.sections if name == "REVIEW REQUESTED" for p in prs or []]

	def start_review(self, pr):
		model = self.model
		def run():
			status = review(pr, model)
			with self.lock:
				self.reviews[pr["url"]] = status
			self.wake.set()  # refetch so an approved PR drops off the list
		with self.lock:
			self.reviews[pr["url"]] = "reviewing..."
		threading.Thread(target=run, daemon=True).start()

	def loop(self):
		while True:
			t0 = time.time()
			data = fetch()
			stale = mark_rereviews(data)
			behind = update_available()
			if self.fetched_at is None:
				time.sleep(max(0, SPLASH_MIN - (time.time() - t0)))  # ponytail: let the splash breathe on the first load
			with self.lock:
				self.sections, self.fetched_at, self.update = data, time.time(), behind
				for u in stale:  # forget the old verdict so Enter / auto can review the new push
					if self.reviews.get(u) != "reviewing...":
						self.reviews.pop(u, None)
				new = [p for name, prs, _ in data if name == "REVIEW REQUESTED" for p in prs or []
				       if self.auto and p["url"] not in self.auto_baseline and p["url"] not in self.reviews] if self.auto else []
			for p in new:
				self.start_review(p)
			self.wake.wait(self.interval)
			self.wake.clear()


def rows(sections, window=None, subs="all"):
	"""Flatten to draw rows: (kind, payload). Selectable rows are ('pr', pr).
	window: hours of REVIEWED to show. subs: 'all' / 'open' (no summaries under REVIEWED) / 'off'."""
	out = []
	summaries = {p["url"]: p["review"]["summary"] for n, prs, _ in sections if n == "REVIEWED" for p in prs or []}
	cutoff = datetime.now(timezone.utc) - timedelta(hours=window) if window else None
	for name, prs, err in sections:
		label = name
		if name == "REVIEWED" and cutoff:
			prs = [p for p in prs or [] if datetime.fromisoformat(p["review"]["at"]) >= cutoff]
			label = f"{name} · last {window}h"
		out.append(("head", f"{label} ({len(prs) if prs is not None else '!'})"))
		for p in prs or []:
			p["section"] = name
		if err:
			out.append(("err", err.splitlines()[0][:200]))
		elif not prs:
			out.append(("empty", "  none"))
		for p in prs or []:
			out.append(("pr", p))
			if summaries.get(p["url"]) and (subs == "all" or (subs == "open" and name != "REVIEWED")):
				out.append(("sub", summaries[p["url"]]))  # ponytail: one line, truncated in draw
		out.append(("blank", ""))
	return out


def C(n):
	return curses.color_pair(n)


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
	spin = SPINNER[int(time.time() * 8) % len(SPINNER)]  # ponytail: frame from the clock, no animation state
	status = f"{spin} fetching…" if fetched_at is None else f"updated {age(datetime.fromtimestamp(fetched_at, timezone.utc).isoformat())} ago"
	scr.addnstr(0, 0, " " * (w - 1), w - 1, C(7))
	scr.addnstr(0, 1, f" PRs {total} ", w - 2, C(8) | curses.A_BOLD)
	x0 = min(w - 2, 10 + len(str(total)))
	scr.addnstr(0, x0, f"  {status}", max(1, w - 2 - x0), C(7))
	if state.auto:
		scr.addnstr(0, max(0, w - 8), " AUTO ", 7, C(5) | curses.A_REVERSE | curses.A_BOLD)
	if state.update:
		badge = f" ↑ update ({state.update}) u "
		scr.addnstr(0, max(0, w - 9 - len(badge)), badge, len(badge), C(4) | curses.A_REVERSE | curses.A_BOLD)

	# stats strip
	vals = list(reviews.values())
	running = sum(v == "reviewing..." for v in vals)
	nxt = "" if fetched_at is None else f"next refresh {max(0, int(fetched_at + state.interval - time.time()))}s"
	x = 1
	for label, n, attr in (
		("agents running", running, C(5) | (curses.A_BOLD if running else 0)),
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
		def mid(y, t, attr):  # ponytail: centred one-liner, clipped by addnstr
			if 2 <= y < h - 1:
				scr.addnstr(y, max(0, (w - len(t)) // 2), t, w - 1, attr)
		art = w >= len(BANNER[0]) + 2 and h >= 16
		y0 = h // 2 - (5 if art else 0)
		mid(y0, f"{spin}  fetching pull requests…", C(5) | curses.A_BOLD)
		if art:
			mid(y0 + 2, "C R E A T E D   B Y", C(1))
			for i, line in enumerate(BANNER):
				mid(y0 + 3 + i, line, C(6) | curses.A_BOLD)
		else:
			mid(y0 + 2, "created by MARTIN", C(6) | curses.A_BOLD)

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
	# footer
	foot = prompt or " j/k move  o open  ⏎ review / details  a auto  m model  t window  s summaries  u update  r refresh  q quit"
	scr.addnstr(h - 1, 0, " " * (w - 1), w - 1, C(7))
	scr.addnstr(h - 1, 0, foot, w - 1, (C(8) | curses.A_BOLD) if prompt else C(7))
	scr.refresh()
	return sel, (rs[cur][1] if cur >= 0 else None)


def main(scr, interval, auto, model):
	curses.curs_set(0)
	curses.use_default_colors()
	many = curses.COLORS >= 256
	for n, fg, bg in (
		(1, 244 if many else curses.COLOR_WHITE, -1),                      # dim
		(2, 75 if many else curses.COLOR_CYAN, -1),                        # section header
		(3, 203 if many else curses.COLOR_RED, -1),                        # error / changes requested
		(4, 78 if many else curses.COLOR_GREEN, -1),                       # approved
		(5, 221 if many else curses.COLOR_YELLOW, -1),                     # draft / in flight
		(6, 111 if many else curses.COLOR_BLUE, -1),                       # repo ref
		(7, 252 if many else curses.COLOR_WHITE, 236 if many else curses.COLOR_BLACK),   # bars
		(8, 16 if many else curses.COLOR_BLACK, 75 if many else curses.COLOR_CYAN),     # bar badge
	):
		curses.init_pair(n, fg, bg)
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
			state.subs = SUBS[(SUBS.index(state.subs) + 1) % len(SUBS)]
		elif k == ord("t"):
			state.window = WINDOWS[(WINDOWS.index(state.window) + 1) % len(WINDOWS)]
		elif k == ord("m"):
			state.model = MODELS[(MODELS.index(state.model) + 1) % len(MODELS)] if state.model in MODELS else MODELS[0]
		elif k == ord("o") and current:
			subprocess.Popen(["xdg-open", current["url"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
		elif k == ord("u") and state.update:
			draw(scr, state, sel, prompt=f" pull {state.update} new commit(s) and restart? [y/n]")
			scr.timeout(-1)
			yes = scr.getch() == ord("y")
			scr.timeout(500)
			if yes:
				err = apply_update()  # re-execs on success
				draw(scr, state, sel, prompt=f" update failed: {err} ")
				scr.timeout(-1); scr.getch(); scr.timeout(500)
		elif k in (10, 13, curses.KEY_ENTER) and current:
			if current["section"] == "REVIEWED":
				curses.endwin()
				subprocess.run(["less", "-R", "-P", LESS_PROMPT.replace("%f", f"#{current['number']}")],
				               input=detail(current["review"]), text=True)
				scr.refresh()
			elif current["section"] != "REVIEW REQUESTED":
				subprocess.Popen(["xdg-open", current["url"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
			elif current["url"] in state.reviews:
				pass  # already reviewed or in flight
			else:
				draw(scr, state, sel, prompt=f" Claude review + post verdict on #{current['number']}? [y/n]")
				scr.timeout(-1)
				yes = scr.getch() == ord("y")
				scr.timeout(500)
				if yes:
					state.start_review(current)


if __name__ == "__main__":
	interval = int(sys.argv[sys.argv.index("--interval") + 1]) if "--interval" in sys.argv else 300
	model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else DEFAULT_MODEL
	if "--demo" in sys.argv:
		demo()
	curses.wrapper(main, interval, "--auto" in sys.argv, model)
