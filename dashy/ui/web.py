"""The GUI server: a localhost HTTP API over the State, and the one page that drives it.

ponytail: stdlib http.server, no framework. One State, one refresh thread, and one JSON route per thing
the dashboard can do — the page polls /api/state and posts back. The desktop shell is a window pointed
at this same URL, so there is one UI to maintain and it works in a browser with no Rust toolchain.
"""
import base64
import difflib
import http.server
import json
import logging
import os
import platform
import secrets
import shutil
import threading
import time
import urllib.request
import webbrowser
from urllib.parse import parse_qs, urlparse

from .. import HERE, VERSION, config
from ..core import bind, diff, github, install, knowledge, log, memory, review as review_mod, team, update
from ..core.state import State, in_flight

LOG = logging.getLogger(__name__)
HERE_UI = os.path.dirname(os.path.realpath(__file__))
PAGE = os.path.join(HERE_UI, "gui.html")
LOGO = os.path.join(HERE_UI, "head.png")  # the mascot, cut from the repo's logo.png
# where the compiled desktop shell is looked for, in order. ponytail: no config key — a built binary
# is either in the checkout or on PATH, and $GITDASHY_DESKTOP covers anyone who put it elsewhere.
DESKTOP = os.path.join(HERE_UI, "..", "..", "desktop", "src-tauri", "target")
THEMES = ["dashy", "dracula", "gruvbox", "nord"]  # the page carries the palettes; this is what the picker offers


def snapshot(state):
	"""Everything the settings can change, in the shape config.save writes."""
	return {"model": state.model, "interval": state.interval, "subs": state.subs, "window": state.window,
	        "drafts": state.drafts, "depth": config.DEPTH, "effort": config.EFFORT, "notify": config.NOTIFY,
	        "theme": config.THEME, "voice": list(config.VOICE), "hunter": list(config.HUNTER)}


def _label(resolve, p):
	return resolve(p.get("repository", {}).get("nameWithOwner", ""))


def payload(state):
	"""Everything one frame of the GUI needs, as plain JSON."""
	with state.lock:
		sections = list(state.sections)
		reviews, busy, since = dict(state.reviews), set(state.running), dict(state.started_at)
		error, arrived = state.error, dict(state.arrived)
	resolve = bind.resolver()  # ponytail: ONE resolver per frame, like the curses screen used to
	summaries = {p["url"]: p["review"]["summary"] for n, prs, _ in sections if n == "REVIEWED" for p in prs or []}
	out = []
	for name, prs, err in sections:
		rows = []
		for p in prs or []:
			url = p.get("url", "")
			pre = review_mod.self_review_state(p) if name == "MINE" else (0.0, False)
			rows.append({
				"url": url,
				"number": p.get("number"),
				"title": p.get("title", ""),
				"repo": p.get("repository", {}).get("nameWithOwner", ""),
				"author": p.get("author", {}).get("login", ""),
				"updatedAt": p.get("updatedAt", ""),
				"isDraft": bool(p.get("isDraft")),
				"status": p.get("status", ""),
				"prev": p.get("prev", ""),
				"checks": p.get("checks", ""),
				"reviewers": p.get("reviewers", ""),
				"review": reviews.get(url, ""),
				"busy": url in busy,
				"since": since.get(url),
				"team": _label(resolve, p),
				"summary": p["review"]["summary"] if name == "REVIEWED" else summaries.get(url, ""),
				"reviewAt": p["review"]["at"] if name == "REVIEWED" else "",
				"pre": {"at": pre[0], "moved": pre[1]} if pre[0] else None,
			})
		out.append({"name": name, "prs": rows, "error": err or ""})
	names = team.joined()
	return {
		"version": VERSION,
		"sections": out,
		"fetchedAt": state.fetched_at,
		"interval": state.interval,
		"fetching": bool(state.fetching),
		"error": error,
		"auto": bool(state.auto),
		"pending": len(state.pending_rr()),
		"model": state.model,
		"running": len(busy),
		"update": state.update,
		"settings": snapshot(state),
		"options": {"model": config.MODELS, "depth": config.DEPTHS, "effort": config.EFFORTS, "voice": config.VOICES,
		            "hunter": config.HUNTERS, "subs": config.SUBS, "window": config.WINDOWS,
		            "interval": config.INTERVALS, "theme": THEMES},
		"knowledge": {
			"memory": knowledge.show(knowledge.effective()) + knowledge.history_note(),
			"store": knowledge.show(config.TEAMS) if knowledge.store_moved() else "",
			"teams": [{"key": k, "name": team.info(k)["name"], "arrived": arrived.get(k, 0)} for k in names],
			"teamError": team.ERROR,
			"notes": install.session_notes(),
		},
		"asks": getattr(state, "asks", []),
		"notices": getattr(state, "notices", []),
	}


def detail(state, pr):
	"""The side pane's frame for one PR: its size, its CI checks, the brief and the last review of it.

	ponytail: the same want_detail the curses pane used, so a row costs one GitHub query per revision.
	It answers pending while that query is in flight; the page keeps polling and the pane fills in.
	"""
	d = state.want_detail(pr)
	rev = pr.get("review") or log.last(pr["url"]) or {}
	repo = pr.get("repository", {}).get("nameWithOwner", "")
	text, whose = memory.brief(repo, bind.of(repo))
	pre_at, pre_moved = review_mod.self_review_state(pr) if pr.get("section") == "MINE" else (0.0, False)
	return {
		"url": pr["url"],
		"pending": d is None,
		"branch": (d or {}).get("branch", ""),
		"add": (d or {}).get("add"),
		"del": (d or {}).get("del"),
		"files": (d or {}).get("files"),
		"checks": (d or {}).get("checks") or [],
		"brief": {"whose": whose, "empty": not text},
		"pre": {"at": pre_at, "moved": pre_moved} if pre_at else None,
		"review": {
			"verdict": config.STATUS.get(rev.get("verdict"), ""),
			"summary": rev.get("summary", ""),
			"model": rev.get("model", ""),
			"tag": log.tag(rev),
			"at": rev.get("at", ""),
			"findings": log.findings(rev),
			"text": log.detail(rev) if rev.get("pr") else rev.get("body", ""),
		} if rev else None,
	}


def code_rows(files, marks, scoped):
	"""The code tab as a flat list of rows, so the page can window it and jump between marks.

	ponytail: ported from the curses pane as-is. Every mark gets a row: on its line when the diff has
	that line, as an orphan when it does not — a finding is kept only if it can be read.
	"""
	rows, landed = [], set()
	for f in files:
		rows.append({"kind": "file", "path": f["path"], "add": f["add"], "dele": f["dele"]})
		for hunk in f["hunks"]:
			rows.append({"kind": "hunk", "header": hunk["header"]})
			for l in hunk["lines"]:
				rows.append({"kind": "line", "n": l["n"], "sign": l["sign"], "text": l["text"], "del": l["del"],
				             "mark": diff.worst(l)})
				for m in (l.get("marks") or []) if scoped else []:
					rows.append({"kind": "note", "mark": m["kind"], "text": m["text"]})
					landed.add(id(m))
		rows.append({"kind": "gap"})
	for m in [m for m in marks if id(m) not in landed] if scoped else []:
		rows.append({"kind": "orphan", "mark": m["kind"], "text": m["text"], "loc": m["loc"],
		             "why": "not in this diff" if m["file"] is None else "line not in this diff"})
	return rows


def code(state, pr, scope, context):
	"""The review against the code it is about. pending until the diff has been read."""
	rev = pr.get("review") or log.last(pr["url"]) or {}
	if not rev:
		return {"url": pr["url"], "pending": False, "rows": [], "empty": "no review yet — r reviews this PR, p pre-reviews it"}
	repo = pr.get("repository", {}).get("nameWithOwner", "")
	got = state.want_diff(repo, pr.get("number"), pr.get("head", ""), log.findings(rev))
	if got is None:
		return {"url": pr["url"], "pending": True, "rows": []}
	files, marks = got
	if not files:
		return {"url": pr["url"], "pending": False, "rows": [],
		        "empty": "no diff to show — GitHub could not read it, or nothing changed"}
	scoped = scope == "marks"
	rows = code_rows(diff.narrow(files, context) if scoped else files, marks, scoped)
	if scoped and not rows:
		return {"url": pr["url"], "pending": False, "rows": [], "empty": "the review marked nothing — D shows the whole diff"}
	return {"url": pr["url"], "pending": False, "rows": rows}


def find_pr(state, url):
	with state.lock:
		sections = list(state.sections)
	for name, prs, _err in sections:
		for p in prs or []:
			if p.get("url") == url:
				p["section"] = name
				return p
	return None


class Fail(Exception):
	"""An error the page should read: (status, message)."""
	def __init__(self, code, msg):
		super().__init__(msg)
		self.code = code


def need_pr(state, url):
	pr = find_pr(state, url)
	if pr is None:
		raise Fail(404, "no such pr")
	return pr


# ---------------------------------------------------------------- background jobs (dream, scan)

JOBS = {}  # name -> {"t0", "thread", "result", "error"}


def start_job(name, fn):
	"""Run fn on a thread; job() reports on it. One at a time per name."""
	if (j := JOBS.get(name)) and j["thread"].is_alive():
		return
	j = JOBS[name] = {"t0": time.time(), "result": None, "error": ""}
	def run():
		try:
			j["result"] = fn()
		except Exception as e:  # noqa: BLE001 — surfaced in the panel
			LOG.exception("%s failed", name)
			j["error"] = (getattr(e, "stderr", None) or str(e)).strip().splitlines()[-1:] or ["?"]
			j["error"] = j["error"][0][:120]
	j["thread"] = threading.Thread(target=run, daemon=True)
	j["thread"].start()


def job(name):
	j = JOBS.get(name)
	if not j:
		return {"running": False, "idle": True}
	return {"running": j["thread"].is_alive(), "elapsed": int(time.time() - j["t0"]), "error": j["error"],
	        "result": None if j["thread"].is_alive() else j["result"]}


def dream_detail(summary, before, new):
	"""Summary plus a unified diff per changed file."""
	out = [summary.strip(), ""]
	for n, t in new.items():
		if t.strip() != before[n].strip():
			out += list(difflib.unified_diff(before[n].splitlines(), t.strip().splitlines(),
			                                 n[:-3].replace("__", "/"), "after the dream", lineterm="", n=99))
			out.append("")
	return "\n".join(out) or "nothing changed"


def dream_result(got):
	summary, before, new = got
	gone = sorted(n for n, t in new.items() if not t.strip() and before[n].strip())
	files = [{"name": n[:-3].replace("__", "/"), "before": len(before[n].splitlines()),
	          "after": len(new[n].splitlines()), "deleted": n in gone}
	         for n in sorted(new, key=lambda n: (n not in gone, n))]
	return {"summary": summary, "files": files, "lost": sum(len(before[n].splitlines()) for n in gone),
	        "detail": dream_detail(summary, before, new), "new": new}


# ---------------------------------------------------------------- routes: GET


def get_state(state, q):
	return payload(state)


def get_pr(state, q):
	return detail(state, need_pr(state, q("url")))


def get_diff(state, q):
	try:
		context = int(q("context") or diff.CONTEXTS[0])
	except ValueError:
		context = diff.CONTEXTS[0]
	return code(state, need_pr(state, q("url")), q("scope") or "marks", context)


def get_prereview(state, q):
	pr = need_pr(state, q("url"))
	at, moved = review_mod.self_review_state(pr)
	if not at:
		raise Fail(404, f"no pre-review of #{pr['number']} yet — p runs one")
	path = review_mod.self_review_path(pr["repository"]["nameWithOwner"], pr["number"])
	with open(path, encoding="utf-8") as f:
		return {"path": path, "text": f.read(), "moved": moved}


def get_memory(state, q):
	repo = q("repo") or None
	path = memory.path(repo)
	team.pull_dir(config.MEMORY_DIR, "mine")
	try:
		with open(path, encoding="utf-8") as f:
			text = f.read()
	except OSError:
		text = ""
	return {"repo": repo or "general", "path": knowledge.tilde(path), "text": text}


def get_drafts(state, q):
	items = memory.waiting()
	items.sort(key=lambda r: ((r[0] or ""), r[3] == "self", -r[1]))
	return {"promoteAt": memory.PROMOTE_AT,
	        "items": [{"repo": repo, "n": n, "fact": fact, "kind": kind, "team": bind.of(repo) if repo else ""}
	                  for repo, n, fact, kind in items]}


def _pair(repo, a, b):
	live = {r[2]: r for r in memory.drafts(repo)}
	a, b = live.get(a[2]), live.get(b[2])
	if a is None or b is None:
		return None
	would, says = memory.would_merge(a, b)
	return {"repo": repo, "a": a[2], "b": b[2], "would": would, "says": says, "promotes": would >= memory.PROMOTE_AT}


def get_overlaps(state, q):
	"""The scan's state. The model reads the candidates on a thread; pairs are re-read live on each poll."""
	j = job("overlaps")
	if j.get("running") or j.get("idle") or j["error"]:
		return j
	pairs = [p for repo, _r, a, b in j["result"] if (p := _pair(repo, a, b))]
	return {**j, "result": pairs}


def get_share(state, q):
	about = q("about")
	items = memory.in_team(about)
	index = memory.pools()
	items.sort(key=lambda r: (r[2], -len(memory.backers(index, r[0], r[1]))))
	return {"inTeam": team.on(), "items": [
		{"repo": repo, "fact": fact, "sent": sent, "backers": memory.backers(index, repo, fact),
		 "team": bind.of(repo) if repo else bind.of(about)} for repo, fact, sent in items]}


def _used_for(key):
	owners_ = sorted(o + "/*" for o, t in bind.owners().items() if t.lower() == key.lower())
	repos = [r for r, t in bind.bindings().items() if t.lower() == key.lower()]
	head = ", ".join(owners_[:3]) + (f" +{len(owners_) - 3}" if len(owners_) > 3 else "")
	tail = f"{len(repos)} repo{'' if len(repos) == 1 else 's'}" if repos else ""
	return " · ".join(x for x in (head, tail) if x)


def get_teams(state, q):
	"""The teams joined, one dict each. Opening the list clears the arrival badge, as T did."""
	arrived = state.take_arrivals()
	if key := q("brief"):
		path = os.path.join(team.dir_of(key), "memory", memory.PROJECT)
		os.makedirs(os.path.dirname(path), exist_ok=True)
		team.seed_project(path)
		with open(path, encoding="utf-8") as f:
			return {"key": key, "path": knowledge.tilde(path), "text": f.read()}
	out = []
	for key in team.joined():
		d, it = team.dir_of(key), team.info(key)
		url = team.origin_url(d)
		out.append({"key": key, "name": it["name"], "description": it["description"],
		            "checkout": knowledge.tilde(os.path.realpath(d)), "linked": os.path.islink(d),
		            "remote": team.redacted(url) if url else "", "used": _used_for(key),
		            "arrived": arrived.get(key, 0), "undecided": bind.undecided(team.covers(key))})
	return {"teams": out, "error": team.ERROR}


def get_bind(state, q):
	repo = q("repo")
	if not repo:
		raise Fail(400, "no row selected")
	kind, to = bind.why(repo)
	return {"repo": repo, "kind": kind, "to": to, "owner": bind.key(repo).split("/")[0],
	        "teams": [{"key": k, "name": team.info(k)["name"]} for k in team.joined()]}


def get_dream(state, q):
	j = job("dream")
	if j.get("result") is not None:
		return {**j, "result": {k: v for k, v in j["result"].items() if k != "new"}}
	return j


def get_collaborators(state, q):
	pr = need_pr(state, q("url"))
	me = pr.get("author", {}).get("login")
	return {"logins": [c for c in github.collaborators(pr["repository"]["nameWithOwner"]) if c != me]}


# ---------------------------------------------------------------- routes: POST


def post_review(state, body):
	pr = need_pr(state, body.get("url", ""))
	if in_flight(state, pr["url"]):
		raise Fail(409, "already running")
	# pre-review reads the diff and posts nothing; review posts the verdict. Same row, same spinner.
	(state.start_self_review if body.get("self") else state.start_review)(pr)
	return {"ok": True}


def post_auto(state, body):
	# ponytail: include_existing only when the page says so — it asks first, with the count, as `a` did.
	state.set_auto(bool(body.get("on")), include_existing=bool(body.get("includeExisting")))
	return {"ok": True}


def post_refresh(state, body):
	diff.retry()  # f means "look again", so a diff GitHub failed to read is worth retrying
	state.wake.set()
	return {"ok": True}


def post_open(state, body):
	"""Open a PR, or its pre-review file, with the desktop. Only things on the board — never a free path."""
	pr = need_pr(state, body.get("url", ""))
	if body.get("pre"):
		at, _moved = review_mod.self_review_state(pr)
		if not at:
			raise Fail(404, f"no pre-review of #{pr['number']} yet — p runs one")
		path = review_mod.self_review_path(pr["repository"]["nameWithOwner"], pr["number"])
		github.open_in_browser(path)
		return {"ok": True, "opened": path}
	github.open_in_browser(pr["url"])
	return {"ok": True, "opened": pr["url"]}


def post_copy(state, body):
	pr = need_pr(state, body.get("url", ""))
	return {"ok": True, "tool": github.copy(pr["url"])}


def post_memory(state, body):
	repo = body.get("repo") or None
	repo = None if repo == "general" else repo
	path = memory.path(repo)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	team.pull_dir(config.MEMORY_DIR, "mine")
	memory.history()  # the state before the edit is the version you want back if you regret it
	with open(path, "w", encoding="utf-8") as f:
		f.write(str(body.get("text", "")))
	err = team.push_dir(config.MEMORY_DIR, f"memory: {repo or 'general'} edited", "mine")
	return {"ok": True, "error": err or ""}


def post_drafts(state, body):
	repo, fact = body.get("repo") or None, str(body.get("fact", ""))
	if body.get("op") == "promote":
		memory.promote(repo, fact)
		team.push_dir(config.MEMORY_DIR, f"memory: accepted for {repo or 'general'}", "mine")
		team.push(f"memory: evidence for {repo or 'general'}")
	elif body.get("op") == "drop":
		memory.drop(repo, fact)
		team.push_dir(config.MEMORY_DIR, f"memory: dropped a draft for {repo or 'general'}", "mine")
	else:
		raise Fail(400, "op must be promote or drop")
	return {"ok": True}


def post_overlaps(state, body):
	op = body.get("op")
	if op == "start":
		model = state.model
		def scan():
			pairs = memory.overlaps()
			if not pairs:
				return []
			# ponytail: `is None` — an empty list is the model saying none match, which is an answer;
			# only "not asked" falls back to every candidate, for a person to read.
			got = memory.judged(pairs, model)
			return pairs if got is None else got
		start_job("overlaps", scan)
		return {"ok": True}
	if op == "merge":
		repo = body.get("repo") or None
		keep, drop = (0, (), str(body.get("keep", ""))), (0, (), str(body.get("drop", "")))
		n = memory.merge(repo, keep, drop)
		team.push_dir(config.MEMORY_DIR, f"memory: folded two drafts for {repo or 'general'}", "mine")
		team.push(f"memory: evidence for {repo or 'general'}")
		return {"ok": True, "count": n}
	raise Fail(400, "op must be start or merge")


def post_share(state, body):
	repo, fact, about = body.get("repo") or None, str(body.get("fact", "")), body.get("about", "")
	if body.get("op") == "send":
		memory.share(repo, fact, about)
		team.push(f"memory: share {repo or 'general'}")
	elif body.get("op") == "forget":
		memory.forget(repo, fact, about)
		team.push_dir(config.MEMORY_DIR, f"memory: forget {repo or 'general'}", "mine")
		team.push(f"memory: withdraw {repo or 'general'}")
	else:
		raise Fail(400, "op must be send or forget")
	return {"ok": True}


def _cover(key, owner):
	"""Bind owner/* here, then declare it in the team. Local first: the cheap, reversible half."""
	owner = bind.owner_key(owner)
	if not owner:
		raise Fail(400, "an owner is one name, like neomedsys")
	if err := bind.bind_owner(owner, key) or team.cover(key, owner):
		raise Fail(400, err)


def post_teams(state, body):
	op, key = body.get("op"), body.get("key", "")
	if op == "new":
		name = str(body.get("name", "")).strip()
		if not name:
			raise Fail(400, "a team needs a name")
		if err := team.start(name, str(body.get("desc", ""))):
			raise Fail(400, err)
		key = team.key_of(name)
		if owner := body.get("owner"):
			_cover(key, owner)
		state.wake.set()
		return {"ok": True, "key": key}
	if op == "join":
		before = set(team.joined())
		if err := team.setup(str(body.get("repo", "")).strip()):
			raise Fail(400, err)
		state.wake.set()
		fresh = sorted(set(team.joined()) - before)
		return {"ok": True, "key": fresh[0] if fresh else "", "warning": f"joined, but could not publish: {team.ERROR[:70]}" if team.ERROR else ""}
	if not team.dir_of(key):
		raise Fail(404, f"not in team {key!r}")
	if op == "connect":
		if err := team.connect(key, str(body.get("url", ""))):
			raise Fail(400, err)
		return {"ok": True, "remote": team.redacted(str(body.get("url", "")))}
	if op == "describe":
		if err := team.write_info(key, team.info(key)["name"], str(body.get("desc", ""))):
			raise Fail(400, err)
		team.push_dir(team.dir_of(key), f"team: describe {key}", "sync")
		return {"ok": True}
	if op == "cover":
		_cover(key, str(body.get("owner", "")))
		state.wake.set()
		return {"ok": True}
	if op == "brief":
		path = os.path.join(team.dir_of(key), "memory", memory.PROJECT)
		os.makedirs(os.path.dirname(path), exist_ok=True)
		with open(path, "w", encoding="utf-8") as f:
			f.write(str(body.get("text", "")))
		err = team.push_dir(team.dir_of(key), f"memory: the brief for {key}", "sync")
		return {"ok": True, "error": err or ""}
	if op == "claim":
		kind, v = bind.target(str(body.get("target", "")))
		if body.get("yes"):
			err = bind.bind_owner(v, key) if kind == "owner" else bind.bind(v, key)
		else:
			err = bind.forget_owner(v) if kind == "owner" else bind.forget(v)
		if err:
			raise Fail(400, err)
		state.wake.set()
		return {"ok": True}
	if op == "leave":
		if err := knowledge.leave(key):
			raise Fail(400, err)
		state.wake.set()
		return {"ok": True}
	raise Fail(400, "unknown team op")


def post_bind(state, body):
	repo, op, to = body.get("repo", ""), body.get("op"), body.get("team", "")
	if not repo:
		raise Fail(400, "no row selected")
	if op == "forget":
		err = bind.forget(repo)
	elif op == "owner":
		err = bind.bind_owner(bind.key(repo).split("/")[0], to)
	elif op == "bind":
		err = bind.bind(repo, to)
	else:
		raise Fail(400, "op must be bind, owner or forget")
	if err:
		raise Fail(400, err)
	state.wake.set()
	return {"ok": True}


def post_dream(state, body):
	op = body.get("op")
	if op == "start":
		model = state.model
		start_job("dream", lambda: dream_result(memory.dream(model)))  # module attr: --demo and tests swap it
		return {"ok": True}
	j = JOBS.get("dream")
	if op == "discard":
		JOBS.pop("dream", None)
		return {"ok": True}
	if op == "apply":
		if not j or j["thread"].is_alive() or j["result"] is None:
			raise Fail(409, "no dream to apply")
		team.pull_dir(config.MEMORY_DIR, "mine")  # a dream rewrites both sources, so both are pulled
		team.pull()
		memory.write(j["result"]["new"])
		JOBS.pop("dream", None)
		err = team.push_dir(config.MEMORY_DIR, "memory: dream cleanup", "mine") or team.push("memory: dream cleanup")
		return {"ok": True, "error": f"memory rewritten, but NOT committed: {err} — a backup is in ~/.prs_backups" if err else ""}
	raise Fail(400, "op must be start, apply or discard")


def post_request_review(state, body):
	pr = need_pr(state, body.get("url", ""))
	login = str(body.get("login", "")).strip()
	if not login:
		raise Fail(400, "a login is needed")
	if err := github.request_review(pr["repository"]["nameWithOwner"], pr["number"], login):
		raise Fail(400, err)
	state.wake.set()  # refetch so the new reviewer shows on the row
	return {"ok": True}


def post_consent(state, body):
	"""Answer one launch-time ask: whether a team may receive facts, or its agents.md may reach sessions."""
	kind, key, yes = body.get("kind"), body.get("key", ""), bool(body.get("yes"))
	if kind == "publishing":
		memory.allow_publishing(key, yes)
	elif kind == "agents":
		memory.allow_agents(key, str(body.get("text", "")), yes)  # what was SHOWN is what gets recorded
	else:
		raise Fail(400, "kind must be publishing or agents")
	state.asks = [a for a in getattr(state, "asks", []) if not (a["kind"] == kind and a["key"] == key)]
	state.wake.set()
	return {"ok": True}


def post_path(state, body):
	"""Point Memory (L) or Store (C) somewhere else. Answers {confirm} first when the move needs a yes."""
	which, new, force = body.get("which"), str(body.get("path", "")).strip(), bool(body.get("force"))
	if which not in ("L", "C") or not new:
		raise Fail(400, "which must be L or C, with a path")
	cur = config.LOCAL_MEMORY if which == "L" else config.TEAMS
	try:
		if knowledge.is_remote(new):
			if which != "L":
				raise Fail(400, "Store is a local directory — T is what clones a team repo")
			if not force:
				return {"confirm": f"clone {new} into {knowledge.tilde(cur)}, keeping the facts already there?"}
			err = knowledge.adopt(new)
		elif knowledge.inside_git(new) and not force:
			return {"confirm": f"{new} sits in a git repo that does not ignore it — memory could be committed. continue?"}
		else:
			err = knowledge.set_local(new) if which == "L" else knowledge.set_store(new)
	except OSError as e:
		err = str(e)
	if err:
		raise Fail(400, err)
	return {"ok": True}


def post_update(state, body):
	"""Install the newest release and re-exec. The reply goes out first; the page reconnects."""
	if not state.update:
		raise Fail(409, "already on the newest release")
	def go():
		time.sleep(0.3)
		if tok := getattr(state, "token", ""):
			os.environ["GITDASHY_GUI_TOKEN"] = tok  # the re-exec'd server must answer on the same token
		if err := update.apply_update(state.update):  # re-execs on success
			state.notices = getattr(state, "notices", []) + [f"update failed: {err}"]
	threading.Thread(target=go, daemon=True).start()
	return {"ok": True}


def post_quit(state, body):
	threading.Timer(0.2, os._exit, [0]).start()  # ponytail: the request threads are daemons with nothing to flush
	return {"ok": True}


def post_notices(state, body):
	state.notices = []
	return {"ok": True}


def post_settings(state, body):
	"""Apply the settings the page can change, and persist them.

	ponytail: values off the wire, so every one is checked here. The interval drives a loop that hits
	the GitHub API — 0 would spin it flat out against your rate limit, and a string would raise inside
	the refresh thread, where nothing is watching.
	"""
	if "interval" in body:
		try:
			n = int(body["interval"])
		except (TypeError, ValueError):
			raise Fail(400, "interval must be a number")
		if not 30 <= n <= 86400:
			raise Fail(400, "interval must be 30s to a day")
		state.interval = n
		state.wake.set()  # a shorter interval should not wait out the longer one it replaced
	if "model" in body:
		name = str(body["model"]).strip()
		if not name or len(name) > 60:
			raise Fail(400, "bad model")
		state.model = name
	for key, options, const in (("depth", config.DEPTHS, "DEPTH"), ("effort", config.EFFORTS, "EFFORT"),
	                            ("theme", THEMES, "THEME")):
		if key in body:
			if body[key] not in options:
				raise Fail(400, f"{key} must be one of {', '.join(o or 'default' for o in options)}")
			setattr(config, const, body[key])
	for key, options, const in (("voice", config.VOICES, "VOICE"), ("hunter", config.HUNTERS, "HUNTER")):
		if key in body:
			got = body[key] if isinstance(body[key], list) else []
			if set(got) - set(options):
				raise Fail(400, f"{key} must be from {', '.join(options)}")
			new = [v for v in options if v in got]  # ponytail: rebuilt in option order
			if key == "voice" and not new:
				raise Fail(400, "at least one voice stays on, or nothing gets posted")
			setattr(config, const, new)
	if "subs" in body:
		if body["subs"] not in config.SUBS:
			raise Fail(400, f"subs must be one of {', '.join(config.SUBS)}")
		state.subs = body["subs"]
	if "window" in body:
		if body["window"] not in config.WINDOWS:
			raise Fail(400, "window must be one of the offered hours, or null for all")
		state.window = body["window"]
	if "drafts" in body:
		state.drafts = bool(body["drafts"])
	if "notify" in body:
		config.NOTIFY = bool(body["notify"])
	config.save(snapshot(state))
	return {"ok": True}


GETS = {"/api/state": get_state, "/api/pr": get_pr, "/api/diff": get_diff, "/api/prereview": get_prereview,
        "/api/memory": get_memory, "/api/drafts": get_drafts, "/api/overlaps": get_overlaps, "/api/share": get_share,
        "/api/teams": get_teams, "/api/bind": get_bind, "/api/dream": get_dream, "/api/collaborators": get_collaborators}
POSTS = {"/api/review": post_review, "/api/auto": post_auto, "/api/settings": post_settings, "/api/refresh": post_refresh,
         "/api/open": post_open, "/api/copy": post_copy, "/api/memory": post_memory, "/api/drafts": post_drafts,
         "/api/overlaps": post_overlaps, "/api/share": post_share, "/api/teams": post_teams, "/api/bind": post_bind,
         "/api/dream": post_dream, "/api/request-review": post_request_review, "/api/consent": post_consent,
         "/api/path": post_path, "/api/update": post_update, "/api/quit": post_quit, "/api/notices": post_notices}


def handler(state, token, page):
	class H(http.server.BaseHTTPRequestHandler):
		protocol_version = "HTTP/1.1"

		def log_message(self, fmt, *args):
			LOG.debug("gui %s", fmt % args)

		def send(self, code, body, ctype="application/json"):
			body = body if isinstance(body, bytes) else body.encode()
			self.send_response(code)
			self.send_header("Content-Type", ctype)
			self.send_header("Content-Length", str(len(body)))
			# ponytail: the page talks to its own origin only; nothing here is meant to be embedded.
			self.send_header("X-Frame-Options", "DENY")
			self.end_headers()
			self.wfile.write(body)

		def guard(self, parts):
			"""A shared secret on every request, and a localhost Host.

			ponytail: this server answers with your PRs and starts Claude runs that cost money, so it is
			a trust boundary even on loopback. Any page you happen to have open can POST to 127.0.0.1
			without reading the reply, and can reach it by a hostname that resolves there (DNS
			rebinding) — the token stops the first, the Host check stops the second.
			"""
			host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
			if host not in ("127.0.0.1", "localhost", "::1"):
				self.send(403, '{"error":"bad host"}')
				return False
			got = self.headers.get("X-Dashy-Token") or (parts.get("token") or [""])[0]
			if not secrets.compare_digest(got, token):
				self.send(403, '{"error":"bad token"}')
				return False
			return True

		def answer(self, fn, *args):
			"""Run a route and send what it says. A Fail is a status the page reads; anything else is a 500
			with its last line, so a broken action is a flash on the page, not a dead poll."""
			try:
				out = fn(state, *args)
			except Fail as e:
				return self.send(e.code, json.dumps({"error": str(e)}))
			except Exception as e:  # noqa: BLE001 — see above
				LOG.exception("%s failed", fn.__name__)
				return self.send(500, json.dumps({"error": (str(e).strip().splitlines() or [type(e).__name__])[-1][:160]}))
			self.send(200, json.dumps(out))

		def do_GET(self):
			u = urlparse(self.path)
			parts = parse_qs(u.query)
			if not self.guard(parts):
				return
			if u.path == "/":
				return self.send(200, page, "text/html; charset=utf-8")
			if fn := GETS.get(u.path):
				return self.answer(fn, lambda k: (parts.get(k) or [""])[0])
			self.send(404, '{"error":"not found"}')

		def do_POST(self):
			u = urlparse(self.path)
			parts = parse_qs(u.query)
			if not self.guard(parts):
				return
			fn = POSTS.get(u.path)
			if fn is None:
				return self.send(404, '{"error":"not found"}')
			try:
				n = int(self.headers.get("Content-Length") or 0)
				body = json.loads(self.rfile.read(n) or b"{}")
				if not isinstance(body, dict):
					raise ValueError
			except (ValueError, json.JSONDecodeError):
				return self.send(400, '{"error":"bad body"}')
			self.answer(fn, body)

	return H


def desktop_binary():
	"""The compiled desktop shell, or "" when it has not been built.

	ponytail: release before debug, then PATH. Nothing here builds it — a `cargo build` triggered by a
	dashboard launch is a five-minute surprise on a machine that may not even have rust.
	"""
	if env := os.environ.get("GITDASHY_DESKTOP"):
		return env if os.access(env, os.X_OK) else ""
	for profile in ("release", "debug"):
		exe = os.path.join(DESKTOP, profile, "gitdashy-desktop")
		if os.access(exe, os.X_OK):
			return exe
	return shutil.which("gitdashy-desktop") or ""


INSTALLED = os.environ.get("GITDASHY_INSTALL", os.path.expanduser("~/.prs_desktop"))  # downloaded shell lands here
RELEASES = "https://github.com/MartinRovang/git-dashy/releases/latest/download/"


def asset_name():
	"""The release asset for this machine — ci.yml names them the same way."""
	system = {"darwin": "macos"}.get(platform.system().lower(), platform.system().lower())
	arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
	return f"gitdashy-desktop-{system}-{arch}" + (".exe" if system == "windows" else "")


def download_desktop(url=None):
	"""Fetch the prebuilt shell for this OS into INSTALLED; the path, or "" when there is none to fetch.

	ponytail: one GET, no version check — `latest` is whatever CI last attached, and a shell that
	is already installed is found by desktop_binary() before this runs. Delete INSTALLED to refetch.
	"""
	exe = os.path.join(INSTALLED, asset_name())
	if os.access(exe, os.X_OK):
		return exe
	os.makedirs(INSTALLED, exist_ok=True)
	print(f"gitdashy: downloading the desktop app to {exe}")
	try:
		with urllib.request.urlopen(url or RELEASES + asset_name(), timeout=60) as r, open(exe + ".part", "wb") as f:
			shutil.copyfileobj(r, f)
	except OSError as e:
		print(f"gitdashy: download failed ({e}), opening in your browser instead")
		if os.path.exists(exe + ".part"):
			os.remove(exe + ".part")
		return ""
	os.replace(exe + ".part", exe)
	os.chmod(exe, 0o755)
	return exe


ENTRY = os.path.join(HERE_UI, "..", "..", "prs.py")  # the entry point for THIS copy of the package


def launch_desktop(exe, argv):
	"""Hand this process over to the desktop shell, which starts its own server behind a splash.

	ponytail: execv, not spawn — the shell IS the app from here on. A parent that only waits is a
	process in the tree doing nothing, and it would break the shell's own parent-death watchdog.

	ponytail: GITDASHY_BIN is pinned to the checkout that is running RIGHT NOW. The shell otherwise
	falls back to `gitdashy` on PATH, which on a machine with an older install is a different build.
	"""
	if os.path.isfile(ENTRY):
		os.environ.setdefault("GITDASHY_BIN", os.path.realpath(ENTRY))
	drop = {"--gui", "--browser"}
	os.execv(exe, [exe] + [a for a in argv[1:] if a not in drop])


def watch_parent(interval=1.0):
	"""Exit when the process that started us goes away.

	ponytail: os.getppid() alone — no signals, no psutil. An orphan is reparented, so a changed ppid is
	the whole test. This exists because the desktop shell spawns this server: if that window is killed
	outright, nothing in it runs, and what is left is a server holding your token and polling GitHub
	forever with no UI attached to close it. Only the shell asks for this.
	"""
	first = os.getppid()

	def run():
		while os.getppid() == first:
			time.sleep(interval)
		LOG.info("parent %s exited, shutting down", first)
		os._exit(0)  # ponytail: the request threads are daemons with nothing to flush

	threading.Thread(target=run, daemon=True).start()


def launch_asks():
	"""The launch-time consent questions, as the page shows them: publishing per team, then agents.md."""
	asks = []
	for key, drafts, facts_ in memory.unasked():
		waiting = " · ".join(x for x in (f"{drafts} draft{'' if drafts == 1 else 's'}" if drafts else "",
		                                 f"{facts_} fact{'' if facts_ == 1 else 's'}" if facts_ else "") if x)
		asks.append({"kind": "publishing", "key": key, "name": team.info(key)["name"], "waiting": waiting})
	for key, text in memory.unacked_agents():
		asks.append({"kind": "agents", "key": key, "name": team.info(key)["name"], "text": text,
		             "path": knowledge.tilde(os.path.join(bind.team_dir(key), memory.AGENTS))})
	return asks


def main(interval, auto, model, open_browser=True, port=0, orphan_exit=False, token=""):
	"""Serve the GUI and block. Returns only when interrupted.

	port and token are supplied by the desktop shell, which picked them before spawning us and polls
	until we answer on them. Left empty, we choose our own — what a person running in a browser gets.
	"""
	if orphan_exit:
		watch_parent()
	state = State(interval, model)
	state.notices = []
	# ponytail: BEFORE activate(), which lists teams by looking in TEAMS. A move of the user's files is
	# said on the page and acknowledged; a refusal stays on the Knowledge row.
	if moved := team.migrate():
		line = moved[len("gitdashy: "):]
		if line.startswith("moved your team checkout"):
			state.notices.append(line[:160])
		else:
			team.ERROR = line[:60]
	if done := next((l for l in install.retire() if not l.startswith("NOTE")), ""):
		state.notices.append(done[:160])
	team.activate()
	state.asks = launch_asks()
	if auto:
		state.set_auto(True)
	threading.Thread(target=state.loop, daemon=True).start()

	with open(PAGE, encoding="utf-8") as f:
		page = f.read()
	# ponytail: inlined, not served. An <img src> carries no token, so a route for it would have to be
	# a hole in the guard — and the mark is 3 KB. One substitution beats an exempted endpoint.
	with open(LOGO, "rb") as f:
		page = page.replace("{{LOGO}}", "data:image/png;base64," + base64.b64encode(f.read()).decode())
	token = token or secrets.token_urlsafe(24)
	state.token = token
	srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler(state, token, page))
	srv.daemon_threads = True
	url = f"http://127.0.0.1:{srv.server_port}/?token={token}"
	# ponytail: flush=True because a person may be reading this line to open the URL themselves, and
	# python block-buffers stdout when it is piped.
	print(f"gitdashy {VERSION} gui — {url}\n  ctrl-c to stop", flush=True)
	if open_browser:
		threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
	try:
		srv.serve_forever()
	except KeyboardInterrupt:
		print()
	finally:
		srv.shutdown()
