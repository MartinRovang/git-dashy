"""A PR's diff, parsed, with the review's findings anchored to the lines they name.

ponytail: the pane used to list findings as text — `auto.py:141  verdict dropped mid-sweep` — and a
line reference you have to go and look up somewhere else is a reference nobody follows. The diff is
what the reviewer read; putting each finding back on the line it is about is the whole feature.
"""
import re
import subprocess
import threading

MARK = {"blocking": "◆", "note": "◇", "nit": "·"}  # severity order; the pane paints them
ORDER = ["blocking", "note", "nit"]
TIMEOUT = 20
# ponytail: the CYCLE is the source of the default, not a second copy of it. c used to step a dict
# {3: 8, 8: 0, 0: 3} while State carried its own literal 3 — two defaults for one number, and moving
# either one turned the key that cycles context into a KeyError inside draw().
CONTEXTS = [3, 8, 0]   # lines kept either side of a marked line; c steps this ring; [0] is the default

# ponytail: None is "gh failed", "" is "gh succeeded and the diff was empty". One dict says both, and
# retry() is then the difference between them — a parallel _FAILED set was a second place to forget.
# The lock is not decoration: fetch() now runs on a worker thread while f calls retry() on the UI one,
# and a set changing size mid-iteration raised straight out of the key handler and unwound curses.
_CACHE = {}   # (repo, number, head) -> diff text | None. Keyed by HEAD, so a push invalidates it
_LOCK = threading.Lock()
_GEN = 0   # bumped by retry(); part of the key State caches on, so f reaches past ITS cache too

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def rank(kind):
	"""Severity order, least severe last for anything ORDER does not name.

	ponytail: ORDER.index raises, and it is reached from inside draw(). log.KINDS is the list of kinds
	and it is a dict someone will add a row to — a fourth kind should sort last and paint plainly, not
	take the dashboard down on the next redraw.
	"""
	return ORDER.index(kind) if kind in ORDER else len(ORDER)


def fetch(repo, number, head=""):
	"""The unified diff for one PR, cached on its head sha. "" when gh cannot produce one.

	ponytail: never raises. It is reached from a keypress, and a PR whose diff gh will not print — too
	large, a fork it cannot see, no network — must leave an empty pane with a reason on it rather than
	take the dashboard down. "" is that reason; the caller says so.
	"""
	key = (repo, number, head)
	with _LOCK:
		if key in _CACHE:
			return _CACHE[key] or ""
	try:
		# ponytail: OUTSIDE the lock. It is a subprocess with a 20s ceiling, and holding the lock across
		# it would park the UI thread's retry() behind a network call for as long as gh takes.
		r = subprocess.run(["gh", "pr", "diff", str(number), "--repo", repo],
		                   capture_output=True, text=True, timeout=TIMEOUT)
		got = r.stdout if r.returncode == 0 else None
	except (subprocess.SubprocessError, OSError):
		# ponytail: the failure is CACHED too. Returning "" uncached meant a PR whose diff times out
		# re-ran the subprocess on every look at it and never got past it — the one input for which
		# the cache existed was the one input it did not cover.
		got = None
	with _LOCK:
		_CACHE[key] = got
	return got or ""


def generation():
	"""Bumped every time retry() drops a failure, so caches ABOVE this one can key on it and follow.

	ponytail: clearing _CACHE was not enough. State caches (files, marks) per PR and answers from that
	before fetch() is ever reached, so f cleared the layer nothing was reading and the pane went on
	showing "no diff to show" for the rest of the session. The remedy has to reach the layer that
	actually answers, and a generation in the key is how it does that without State knowing why.
	"""
	return _GEN


def retry():
	"""Forget the diffs gh FAILED to produce, so the next look tries again. Keeps the ones it read.

	ponytail: caching a failure stops the retry storm but makes a dropped network permanent for the
	life of the process. f already means "go and look again", so it clears these and nothing else — a
	diff that really is empty is not re-fetched twenty times because someone pressed refresh.
	"""
	global _GEN
	with _LOCK:
		failed = [k for k, v in _CACHE.items() if v is None]
		for key in failed:
			del _CACHE[key]
		if failed:
			_GEN += 1
	return len(failed)


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
		elif raw.startswith("+++ b/") and cur is not None and hunk is None:
			# ponytail: `hunk is None` or an ADDED LINE reading "+++ b/x" rewrites the path of the file it
			# is in. A PR body or doc quoting a diff does exactly that, and this repo writes them constantly.
			cur["path"] = raw[6:]  # the authoritative name; the `diff --git` line quotes odd paths
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

	Each mark is {kind, loc, text, path, n, file} — `file` is the index of the file it is in, or None
	when the finding names a file this diff does not touch.

	ponytail: a finding that lands nowhere is KEPT. A review's most important line is sometimes about a
	file the diff does not contain — something missing, something the change should have touched — and
	silently dropping it would make the pane quietly less honest than the summary. The pane holds up
	the other half of that: it gives every mark a row, tagged onto its line or listed on its own.
	"""
	marks = []
	for f in findings:
		path, n = _where(f.get("loc", ""))
		hit = next((i for i, d in enumerate(files) if _same_file(path, d["path"])), None)
		mark = {**f, "path": path, "n": n, "file": hit}
		if hit is not None and n:
			# ponytail: the same dict object goes onto the line AND into marks, which is what lets the
			# pane ask "did this one land" by identity rather than by re-deriving the match.
			for h in files[hit]["hunks"]:
				if (l := next((l for l in h["lines"] if l["n"] == n and not l["del"]), None)) is not None:
					l.setdefault("marks", []).append(mark)
					break
		marks.append(mark)
	marks.sort(key=lambda m: (m["file"] is None, m["file"] or 0, m["n"], rank(m["kind"])))
	return marks


def worst(line):
	"""The mark a line should paint with — the most severe one on it. "" when it carries none."""
	got = line.get("marks") or []
	return min((m["kind"] for m in got), key=rank, default="")


def narrow(files, context=CONTEXTS[0]):
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
