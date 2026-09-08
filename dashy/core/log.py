"""The review log: one JSON object per review. Yours, plus one inside each joined team."""
import json
import os
from datetime import datetime, timezone

from .. import config

LOG = config.LOG  # module attr so --demo and tests can point it elsewhere


def when(iso):
	"""An entry's timestamp as an aware datetime in UTC, whatever offset it carried.

	ponytail: `at` predates the offset, so entries written before it read back NAIVE — and everything
	that compares one compares it against an aware value. Kept as a function of its own because _parse
	leans on it raising: an `at` that is not a timestamp at all comes out as the ValueError that gate
	already catches, rather than as a fourth type check beside the three.
	"""
	at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
	# ponytail: converted to UTC, not merely made aware. reviewed() sorts on the STRING this becomes,
	# so an entry from a machine at +02:00 sorted by literal text rather than by instant — 09:30+02:00
	# is 07:30Z, earlier than 08:00Z, and came back first in a list whose whole job is "newest first".
	# Unreachable while every writer emits UTC, which is exactly how long it stays unreachable.
	return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def logs():
	"""Every log to read: yours, then each joined team's. ponytail: lazy import — team imports this."""
	from . import team
	return [LOG] + [p for p in (team.log_of(s) for s in team.joined()) if p and p != LOG]


_CACHE = {}  # path -> (stat key, parsed entries). ponytail: see reviewed().


def _entries(path):
	"""One log's parsed entries, oldest first, cached on (mtime, size).

	ponytail: reviewed() is called per FRAME by the detail pane and again per keypress, and it reparsed
	the whole file every time. That was tolerable while there was one file; the log is per team now, so
	an N-team machine paid N whole-file parses at ~20fps. The key is the stat, so an append by a review
	thread — or a pull bringing a teammate's — invalidates it without anything having to remember to.
	"""
	try:
		st = os.stat(path)
	except OSError:
		return []
	key = (st.st_mtime_ns, st.st_size)
	if _CACHE.get(path, (None,))[0] != key:
		try:
			with open(path) as f:
				# ponytail: a malformed line is skipped, not fatal. One team's half-written append must
				# not empty the REVIEWED list — and with several logs it is no longer your own file.
				out = [e for e in (_parse(l) for l in f.read().splitlines()) if e]
		except OSError:
			return []
		_CACHE[path] = (key, out)
	return _CACHE[path][1]


def _parse(line):
	try:
		e = json.loads(line)
		# ponytail: every field reviewed() then reaches for. It does e["at"] in the sort key and **e["pr"]
		# in the row, so a line missing "at" — or carrying "pr": 7 — took the dashboard down on every
		# frame. The guard exists precisely so a half-written append cannot do that; checking two of the
		# three fields is checking none of them.
		if not (isinstance(e, dict) and isinstance(e.get("pr"), dict)
		        and isinstance(e.get("at"), str) and e.get("verdict") in config.STATUS):
			return None
		# ponytail: and `at` is PARSED, not merely typed. Being a str is not being a timestamp, and
		# mark_rereviews on the refresh thread and age() on the draw thread both hand it to
		# fromisoformat — so "at": "yesterday" raised in the two threads this gate exists to keep safe.
		# Written back AWARE for the same reason: an entry from before `at` carried an offset read back
		# naive, and comparing it against an aware value is a TypeError. Normalising in the gate means no
		# reader has to know the file holds two shapes — and it settles the sort in reviewed(), where a
		# naive string is a prefix of its own aware form and so sorted before it.
		e["at"] = when(e["at"]).isoformat()
		return e
	except ValueError:
		return None


def reviewed():
	"""PR dicts from every log, newest first, each with a 'review' entry and a 'status' string."""
	# ponytail: newest first, and a STABLE tiebreak. It used to be reversed(lines) on one file, so two
	# entries written in the same second came back later-line-first. Sorting on "at" alone is stable in
	# the wrong direction — it kept the earlier line first — which silently reordered same-second
	# reviews, and log.last() reads the FIRST match. Position within its file breaks the tie.
	got = [(e, i) for path in logs() for i, e in enumerate(_entries(path))]
	got.sort(key=lambda p: (p[0]["at"], p[1]), reverse=True)
	got = [e for e, _ in got]
	return [{"title": "?", "isDraft": False, **e["pr"], "review": e, "tag": tag(e),
	         "status": config.STATUS[e["verdict"]], "updatedAt": e["at"]} for e in got]


def last(url):
	"""The newest log entry for this PR url, or None."""
	return next((p["review"] for p in reviewed() if p["url"] == url), None)


def tag(e):
	"""'adaptive/medium $0.42 3m' — depth[/effort] the review ran with, then what it cost; '' for old entries."""
	t = e.get("depth", "") + ("/" + e["effort"] if e.get("effort") else "")
	if e.get("cost"):  # 0 on a subscription run, not worth a "$0.00"
		t += f" ${e['cost']:.2f}"
	if e.get("ms"):
		s = round(e["ms"] / 1000)
		t += f" {s // 60}m" if s >= 60 else f" {s}s"
	return t.strip()


KINDS = {"blocking": "err", "note": "warn", "nit": "dim"}  # tone the detail pane colours each by


def findings(verdict):
	"""[{kind, loc, text}] the reviewer listed. [] for anything malformed or from before the field existed.

	ponytail: model output, so every field is checked. A review whose findings are junk still has a body,
	and the pane falls back to it — a bad list must not cost you the review itself.
	"""
	out = []
	for f in verdict.get("findings") or []:
		if isinstance(f, dict) and str(f.get("kind", "")).lower() in KINDS and f.get("text"):
			out.append({"kind": str(f["kind"]).lower(), "loc": str(f.get("loc", ""))[:60],
			            "text": " ".join(str(f["text"]).split())[:120]})
	return out[:12]  # ponytail: a pane, not a report — the body has the whole thing


def log_review(pr, model, verdict, at=None):
	# ponytail: both sides added fields to this entry — findings from the pane, head/cost/ms and the
	# checks filter from #9. Neither replaces the other.
	entry = {"at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model,
	         "pr": {k: v for k, v in pr.items() if k != "checks"},  # CI state at review time is stale by the time anyone reads it
	         "depth": config.DEPTH, "effort": config.EFFORT, "head": pr.get("head", ""),
	         "cost": verdict.get("cost"), "ms": verdict.get("ms"),
	         "verdict": verdict["verdict"], "summary": verdict.get("summary", ""), "body": verdict["body"],
	         "findings": findings(verdict)}
	# ponytail: into the log of the team this repo is BOUND to, and yours when it is bound to none. The
	# shared review log is how a teammate's review appears in your list; sending it to a team the repo
	# does not belong to would tell them you reviewed something that is none of their business.
	from . import bind, team
	repo = pr.get("repository", {}).get("nameWithOwner", "")
	dest = team.log_of(bind.of(repo)) if repo else LOG
	os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
	with open(dest, "a") as f:
		f.write(json.dumps(entry) + "\n")
	return config.STATUS[verdict["verdict"]]


def mark_rereviews(sections):
	"""Tag REVIEW REQUESTED rows already in the log and pushed to since as p['prev']. Returns their urls.
	ponytail: by head commit when both sides know it — updatedAt also moves on a comment, which is not a
	reason to review again. Timestamps only for entries logged before heads were."""
	last = {}
	for p in [p for n, prs, _ in sections if n == "REVIEWED" for p in prs or []]:
		last.setdefault(p["url"], p["review"])  # newest first
	out = []
	for p in [p for n, prs, _ in sections if n == "REVIEW REQUESTED" for p in prs or []]:
		e = last.get(p["url"])
		if not e:
			continue
		if e.get("head"):
			# ponytail: the entry knows its head but the row does not — the graphql call failed on this
			# fetch, or returned no node for this PR — and nothing here can tell. Falling through to the
			# timestamp would call it changed — posting a review is itself an update — and auto would
			# re-review every tick. Ceiling: a PR permanently missing from graphql is never re-reviewed.
			if not p.get("head"):
				continue
			changed = p["head"] != e["head"]
		else:
			changed = when(p["updatedAt"]) > when(e["at"])
		if changed:
			p["prev"] = f"↻ re-review · was {config.STATUS[e['verdict']]}"
			out.append(p["url"])
	return out


def detail(e):
	"""The full review as plain text, for piping into less."""
	p, at = e["pr"], datetime.fromisoformat(e["at"]).astimezone().strftime("%Y-%m-%d %H:%M")
	ref = f"{p['repository']['nameWithOwner']}#{p['number']}"
	bar = "─" * min(78, max(len(ref) + len(p["title"]) + 2, 40))
	return (f"{bar}\n{ref}  {p['title']}\n{bar}\n"
	        f"  author   {p.get('author', {}).get('login', '?')}\n  url      {p['url']}\n"
	        f"  reviewed {at} by {e['model']} {tag(e)}  →  {config.STATUS[e['verdict']]}\n\n"
	        f"WHAT THE PR DOES\n\n{e['summary'] or '(no summary)'}\n\n"
	        f"REVIEW\n\n{e['body']}\n\n{bar}\nq close   j/k or ↑/↓ scroll   o open in browser (from the list)\n")
