"""Sections -> flat draw rows, plus the age helper the rows are labelled with."""
from datetime import datetime, timedelta, timezone


def age(iso):
	s = (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
	for unit, div in (("d", 86400), ("h", 3600), ("m", 60)):
		if s >= div:
			return f"{int(s // div)}{unit}"
	return "now"


def rows(sections, window=None, subs="all", drafts=True, expanded=()):
	"""Flatten to draw rows: (kind, payload). Selectable rows are ('pr', pr).
	window: hours of REVIEWED to show. subs: 'all' / 'open' (no summaries under REVIEWED) / 'off'.
	drafts: False hides draft PRs. expanded: urls whose older REVIEWED entries are unfolded under the newest."""
	out = []
	summaries = {p["url"]: p["review"]["summary"] for n, prs, _ in sections if n == "REVIEWED" for p in prs or []}
	cutoff = datetime.now(timezone.utc) - timedelta(hours=window) if window else None
	for name, prs, err in sections:
		label = name
		if not drafts and prs:
			prs = [p for p in prs if not p.get("isDraft")]
		if name == "REVIEWED" and cutoff:
			prs = [p for p in prs or [] if datetime.fromisoformat(p["review"]["at"]) >= cutoff]
			label = f"{name} · last {window}h"
		if name == "REVIEWED" and prs:
			prs = stack(prs, expanded)
		out.append(("head", f"{label} ({len(prs) if prs is not None else '!'})"))
		for p in prs or []:
			p["section"] = name
		if err:
			out.append(("err", err.splitlines()[0][:200]))
		elif not prs:
			out.append(("empty", "  none"))
		for p in prs or []:
			out.append(("pr", p))
			summary = p["review"]["summary"] if name == "REVIEWED" else summaries.get(p["url"])
			if summary and (subs == "all" or (subs == "open" and name != "REVIEWED")):
				out.append(("sub", summary))  # ponytail: one line, truncated in draw
		out.append(("blank", ""))
	return out


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
