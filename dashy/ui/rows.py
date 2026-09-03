"""Sections -> flat draw rows, plus the age helper the rows are labelled with."""
from datetime import datetime, timedelta, timezone

QUEUE = {"REVIEW REQUESTED": "review requested", "ASSIGNED": "assigned", "REVIEWED": "reviewed"}


def age(iso):
	s = (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
	for unit, div in (("d", 86400), ("h", 3600), ("m", 60)):
		if s >= div:
			return f"{int(s // div)}{unit}"
	return "now"


def note(prs):
	"""'2 need work · 1 waiting' — what your own PRs are asking of you, or '' when nothing is."""
	if not prs:
		return ""
	work = sum(1 for p in prs if (p.get("status") or "").startswith("✗"))
	wait = sum(1 for p in prs if (p.get("status") or "").startswith(("·", "↻")))
	bits = [f"{work} need work"] if work else []
	bits += [f"{wait} waiting"] if wait else []
	return " · ".join(bits)


def body(prs, err, summaries, subs, name):
	"""The rows under one section heading: an error, an emptiness, or the PRs and their summaries."""
	if err:
		return [("err", err.splitlines()[0][:200])]
	if not prs:
		return [("empty", "none")]
	out = []
	for p in prs:
		out.append(("pr", p))
		summary = p["review"]["summary"] if name == "REVIEWED" else summaries.get(p["url"])
		if summary and (subs == "all" or (subs == "open" and name != "REVIEWED")):
			out.append(("sub", summary))  # ponytail: one line, truncated in draw
	return out


def rows(sections, window=None, subs="all", drafts=True, expanded=()):
	"""Flatten to draw rows: (kind, payload). Selectable rows are ('pr', pr).

	Your own PRs get a section of their own, because they are the ones you can act on. The other three
	are queues: each collapses to a single line while it is empty, so three empty headings do not push
	the list you came for off the screen. A queue with anything in it opens back into a full section.
	"""
	out, queues, live = [("cols", None)], [], False
	summaries = {p["url"]: p["review"]["summary"] for n, prs, _ in sections if n == "REVIEWED" for p in prs or []}
	cutoff = datetime.now(timezone.utc) - timedelta(hours=window) if window else None
	for name, prs, err in sections:
		if not drafts and prs:
			prs = [p for p in prs if not p.get("isDraft")]
		if name == "REVIEWED" and cutoff:
			prs = [p for p in prs or [] if datetime.fromisoformat(p["review"]["at"]) >= cutoff]
		if name == "REVIEWED" and prs:
			prs = stack(prs, expanded)
		for p in prs or []:
			p["section"] = name
		if name == "MINE":
			out.append(("head", ("MINE", "!" if prs is None else f"{len(prs)} open", note(prs))))
			out += body(prs, err, summaries, subs, name)
			out.append(("blank", ""))
		elif err or prs:
			live = True
			queues.append(("head", (QUEUE.get(name, name.lower()), "!" if prs is None else str(len(prs)), "")))
			queues += body(prs, err, summaries, subs, name)
			queues.append(("blank", ""))
		else:
			last = f"none in the last {window}h" if name == "REVIEWED" and window else "none"
			queues.append(("queue", (QUEUE.get(name, name.lower()), "0", last)))
	out.append(("head", ("QUEUES", "", "" if live else "nothing waiting on you")))
	return out + queues


def stack(prs, expanded):
	"""Group REVIEWED entries (newest first) by PR: the newest heads the group with p['more'] = hidden count,
	older ones follow as p['child'] = True only when the url is in expanded."""
	groups = {}
	for p in prs:
		groups.setdefault(p["url"], []).append(p)
	out = []
	for url, g in groups.items():
		head, rest = g[0], g[1:]
		head["more"] = 0 if url in expanded else len(rest)
		head["open"] = bool(rest) and url in expanded
		out.append(head)
		if url in expanded:
			for p in rest:
				p["child"] = True
			out += rest
	return out
