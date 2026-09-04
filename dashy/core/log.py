"""The review log: ~/.prs_reviewed.jsonl, one JSON object per review."""
import json
from datetime import datetime, timezone

from .. import config

LOG = config.LOG  # module attr so --demo and tests can point it elsewhere


def reviewed():
	"""PR dicts from the log, newest first, each with a 'review' entry and a 'status' string."""
	try:
		lines = open(LOG).read().splitlines()
	except FileNotFoundError:
		return []
	out = []
	for line in reversed(lines):
		e = json.loads(line)
		out.append({"title": "?", "isDraft": False, **e["pr"], "review": e, "tag": tag(e),
		            "status": config.STATUS[e["verdict"]], "updatedAt": e["at"]})
	return out


def last(url):
	"""The newest log entry for this PR url, or None."""
	return next((p["review"] for p in reviewed() if p["url"] == url), None)


def tag(e):
	"""'adaptive/medium $0.42 3m' — depth[/effort] the review ran with, then what it cost; '' for old entries."""
	t = e.get("depth", "") + ("/" + e["effort"] if e.get("effort") else "")
	if e.get("cost") is not None:
		t += f" ${e['cost']:.2f}"
	if e.get("ms"):
		s = round(e["ms"] / 1000)
		t += f" {s // 60}m" if s >= 60 else f" {s}s"
	return t.strip()


def log_review(pr, model, verdict, at=None):
	entry = {"at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model, "pr": pr,
	         "depth": config.DEPTH, "effort": config.EFFORT, "head": pr.get("head", ""),
	         "cost": verdict.get("cost"), "ms": verdict.get("ms"),
	         "verdict": verdict["verdict"], "summary": verdict.get("summary", ""), "body": verdict["body"]}
	with open(LOG, "a") as f:
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
		if e.get("head") and p.get("head"):
			changed = p["head"] != e["head"]
		else:
			changed = datetime.fromisoformat(p["updatedAt"].replace("Z", "+00:00")) > datetime.fromisoformat(e["at"])
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
