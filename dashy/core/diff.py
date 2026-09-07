"""A PR's diff, parsed, with the review's findings anchored to the lines they name.

ponytail: the pane used to list findings as text — `auto.py:141  verdict dropped mid-sweep` — and a
line reference you have to go and look up somewhere else is a reference nobody follows. The diff is
what the reviewer read; putting each finding back on the line it is about is the whole feature.
"""
import re
import subprocess

MARK = {"blocking": "◆", "note": "◇", "nit": "·"}  # severity order; the pane paints them
ORDER = ["blocking", "note", "nit"]
TIMEOUT = 120
CONTEXT = 3  # lines kept either side of a marked line in "marks only" scope
_CACHE = {}  # (repo, number, head) -> diff text. ponytail: keyed by HEAD, so a push invalidates it

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def fetch(repo, number, head=""):
	"""The unified diff for one PR, cached on its head sha. "" when gh cannot produce one.

	ponytail: never raises. It is reached from a keypress, and a PR whose diff gh will not print — too
	large, a fork it cannot see, no network — must leave an empty pane with a reason on it rather than
	take the dashboard down. "" is that reason; the caller says so.
	"""
	key = (repo, number, head)
	if key in _CACHE:
		return _CACHE[key]
	try:
		r = subprocess.run(["gh", "pr", "diff", str(number), "--repo", repo],
		                   capture_output=True, text=True, timeout=TIMEOUT)
	except (subprocess.SubprocessError, OSError):
		return ""
	_CACHE[key] = r.stdout if r.returncode == 0 else ""
	return _CACHE[key]


def parse(text):
	"""[{path, add, dele, hunks}] from a unified diff. Malformed input yields what it could read.

	ponytail: `n` is the NEW line number, because that is what a review cites — a finding says
	auto.py:141 meaning the file as it will be. A removed line has no new number, so it carries the
	position it sits at and is marked `del`, which keeps it in order without pretending to be a line.
	"""
	files, cur, hunk, new = [], None, None, 0
	for raw in (text or "").splitlines():
		if raw.startswith("diff --git "):
			cur = {"path": raw.split(" b/", 1)[-1] if " b/" in raw else raw[11:], "add": 0, "dele": 0, "hunks": []}
			files.append(cur)
			hunk = None
		elif raw.startswith("+++ b/") and cur is not None:
			cur["path"] = raw[6:]  # ponytail: the authoritative name; the `diff --git` line quotes odd paths
		elif (m := _HUNK.match(raw)) and cur is not None:
			new = int(m.group(2))
			hunk = {"header": raw.rstrip(), "start": new, "lines": []}
			cur["hunks"].append(hunk)
		elif hunk is not None and raw[:1] in ("+", "-", " ", ""):
			sign = raw[:1] or " "
			body = raw[1:] if raw else ""
			if sign == "-":
				hunk["lines"].append({"n": new, "sign": "-", "text": body, "del": True})
				cur["dele"] += 1
			else:
				hunk["lines"].append({"n": new, "sign": sign, "text": body, "del": False})
				if sign == "+":
					cur["add"] += 1
				new += 1
	return [f for f in files if f["hunks"]]


def _where(loc):
	""""a/b.py:141" -> ("a/b.py", 141); "a/b.py" -> ("a/b.py", 0). A file-only finding still lands."""
	loc = (loc or "").strip()
	path, _, tail = loc.rpartition(":")
	return (path, int(tail)) if path and tail.isdigit() else (loc, 0)


def _same_file(a, b):
	"""Whether a finding's path names this diff file. Suffix match, because a review cites a basename
	as often as a full path — `auto.py:141` for `gitdashy/core/auto.py` — and the diff has the truth."""
	a, b = a.strip("/"), b.strip("/")
	return bool(a) and (a == b or b.endswith("/" + a) or a.endswith("/" + b))


def anchor(files, findings):
	"""Put each finding on the line it names. Returns [mark] in file-then-line order, and tags the lines.

	Each mark is {kind, loc, text, path, n, hunk} — `hunk` is the index of the hunk it sits in, or None
	when the finding names a file this diff does not touch.

	ponytail: a finding that lands nowhere is KEPT, with hunk None. A review's most important line is
	sometimes about a file the diff does not contain — something missing, something the change should
	have touched — and silently dropping it would make the pane quietly less honest than the summary.
	"""
	marks = []
	for f in findings:
		path, n = _where(f.get("loc", ""))
		hit = next((i for i, d in enumerate(files) if _same_file(path, d["path"])), None)
		mark = {**f, "path": path, "n": n, "file": hit, "hunk": None}
		if hit is not None:
			for hi, h in enumerate(files[hit]["hunks"]):
				for line in h["lines"]:
					if n and line["n"] == n and not line["del"]:
						mark["hunk"] = hi
						line.setdefault("marks", []).append(mark)
						break
				if mark["hunk"] is not None:
					break
			else:
				mark["hunk"] = 0 if files[hit]["hunks"] and not n else mark["hunk"]
		marks.append(mark)
	marks.sort(key=lambda m: (m["file"] is None, m["file"] or 0, m["n"], ORDER.index(m["kind"])))
	return marks


def worst(line):
	"""The mark a line should paint with — the most severe one on it. "" when it carries none."""
	got = line.get("marks") or []
	return min((m["kind"] for m in got), key=ORDER.index, default="")


def narrow(files, context=CONTEXT):
	"""The same files, keeping only hunks that carry a mark and only lines near one. "marks only".

	ponytail: a review of a 1,400-line diff has four findings in it, and scrolling to them is the work
	the pane exists to remove. Full diff is one keypress away for when the answer is not on the line.
	"""
	out = []
	for f in files:
		hunks = []
		for h in f["hunks"]:
			at = [i for i, l in enumerate(h["lines"]) if l.get("marks")]
			if not at:
				continue
			keep = {i for a in at for i in range(max(0, a - context), min(len(h["lines"]), a + context + 1))}
			hunks.append({**h, "lines": [l for i, l in enumerate(h["lines"]) if i in keep]})
		if hunks:
			out.append({**f, "hunks": hunks})
	return out


def load(repo, number, head, findings):
	"""(files, marks) for a PR — the parsed diff with its review anchored onto it. ([], []) when empty."""
	files = parse(fetch(repo, number, head))
	return files, anchor(files, findings)
