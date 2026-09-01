"""Sections -> flat draw rows, plus the age helper the rows are labelled with."""
from datetime import datetime, timedelta, timezone


def age(iso):
	s = (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
	for unit, div in (("d", 86400), ("h", 3600), ("m", 60)):
		if s >= div:
			return f"{int(s // div)}{unit}"
	return "now"


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
