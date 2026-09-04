"""Everything that shells out to `gh`."""
import base64
import json
import shutil
import subprocess
import sys

from . import log

FIELDS = "number,title,repository,url,updatedAt,isDraft,author"
SECTIONS = [
	("MINE", "--author=@me"),
	("REVIEW REQUESTED", "--review-requested=@me"),
	("ASSIGNED", "--assignee=@me"),
]
DECISION = {"APPROVED": "✓ approved", "CHANGES_REQUESTED": "✗ changes requested", "REVIEW_REQUIRED": "· awaiting review"}
# ponytail: one graphql call for what `gh search` cannot give — review decision, reviewers, head commit and
# CI state — over the same three searches, aliased, so the nodes can be joined back to the sections by url.
NODE = """{ nodes { ... on PullRequest { url headRefOid reviewDecision
    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    reviewRequests(first: 20) { totalCount nodes { requestedReviewer { ... on User { login } ... on Team { slug } } } }
    latestReviews(first: 20) { nodes { author { login } state } } } } }"""
META_QUERY = "{ " + " ".join(f'{a}: search(query: "is:pr is:open {q}", type: ISSUE, first: 100) {NODE}'
                             for a, q in (("mine", "author:@me"), ("rr", "review-requested:@me"), ("asg", "assignee:@me"))) + " }"
CHECKS = {"SUCCESS": "✓", "FAILURE": "✗", "ERROR": "✗", "PENDING": "●", "EXPECTED": "●"}
REVIEW_GLYPH = {"APPROVED": "✓", "CHANGES_REQUESTED": "✗", "COMMENTED": "~", "PENDING": "·"}


def own_status(node):
	"""Row status for my own PR from its reviewDecision + pending review requests."""
	decision, pending = node.get("reviewDecision"), (node.get("reviewRequests") or {}).get("totalCount", 0)
	if decision == "CHANGES_REQUESTED" and pending:
		return "↻ re-review requested"  # I pushed and asked again, reviewer has not looked yet
	return DECISION.get(decision, "")


def checks(node):
	"""CI glyph for the head commit: ✓ green, ✗ failed, ● running, "" when the repo has no checks."""
	for c in (node.get("commits") or {}).get("nodes") or []:
		return CHECKS.get(((c.get("commit") or {}).get("statusCheckRollup") or {}).get("state"), "")
	return ""


def reviewers(node):
	"""'✓bob ·alice' — everyone asked to review or who did, with their latest state (· = not yet)."""
	out = {}
	for n in (node.get("latestReviews") or {}).get("nodes") or []:
		if n and n.get("author"):
			out[n["author"]["login"]] = REVIEW_GLYPH.get(n.get("state"), "~")
	for n in (node.get("reviewRequests") or {}).get("nodes") or []:
		r = (n or {}).get("requestedReviewer") or {}
		if r.get("login") or r.get("slug"):
			out[r.get("login") or r["slug"]] = "·"  # a fresh request supersedes an older review
	return " ".join(g + who for who, g in out.items())


def collaborators(repo):
	"""Logins with access to repo, [] when gh cannot list them (no admin, offline)."""
	try:
		raw = subprocess.run(["gh", "api", f"repos/{repo}/collaborators", "--paginate", "--jq", ".[].login"],
		                     capture_output=True, text=True, check=True, timeout=30).stdout
		return raw.split()
	except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
		return []


def request_review(repo, number, login):
	"""Ask login to review PR number; the gh error text, or "" on success."""
	# ponytail: REST, not `gh pr edit` — that one dies on the Projects-classic deprecation warning
	try:
		r = subprocess.run(["gh", "api", "-X", "POST", f"repos/{repo}/pulls/{number}/requested_reviewers", "-f", f"reviewers[]={login}"],
		                   capture_output=True, text=True, timeout=30)
	except subprocess.TimeoutExpired as e:
		return str(e)
	return "" if r.returncode == 0 else r.stderr.strip()


VERDICT_FLAG = {"approve": "--approve", "request_changes": "--request-changes", "comment": "--comment"}


def fetch():
	"""[(section name, [pr] or None, error string or None)] — one entry per SECTIONS, plus REVIEWED."""
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
	if any(prs for _, prs, _ in out):  # review decision, CI and head commit: search has none of them, one graphql call does
		try:
			raw = subprocess.run(["gh", "api", "graphql", "-f", "query=" + META_QUERY],
			                     capture_output=True, text=True, check=True, timeout=60).stdout
			nodes = {n["url"]: n for s in json.loads(raw)["data"].values() for n in s["nodes"] if n}
			for name, prs, _ in out:
				for p in prs or []:
					n = nodes.get(p["url"], {})
					p["head"], p["checks"] = n.get("headRefOid", ""), checks(n)
					if name == "MINE":
						p["status"], p["reviewers"] = own_status(n), reviewers(n)
		except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, KeyError, AttributeError, TypeError):
			pass  # ponytail: status is decoration, the list still renders without it
	out.append(("REVIEWED", log.reviewed(), None))  # ponytail: not deduped, a reviewed PR may still be open above
	return out


def post_review(repo, number, verdict, body):
	"""Post the verdict on the PR. Raises CalledProcessError / TimeoutExpired on failure."""
	subprocess.run(["gh", "pr", "review", str(number), "--repo", repo, VERDICT_FLAG[verdict], "--body", body],
	               capture_output=True, text=True, check=True, timeout=60)


def comment(repo, number, body):
	"""Post a plain comment on the PR. Raises CalledProcessError / TimeoutExpired on failure."""
	subprocess.run(["gh", "pr", "comment", str(number), "--repo", repo, "--body", body],
	               capture_output=True, text=True, check=True, timeout=60)


def open_in_browser(url):
	subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


CLIPBOARDS = (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["pbcopy"])


def copy(text):
	"""Put text on the clipboard: the first tool on PATH, else the OSC 52 escape most terminals honour.
	Returns what did it ("xclip", "terminal"). ponytail: shell out or one escape, no clipboard library."""
	for cmd in CLIPBOARDS:
		if shutil.which(cmd[0]):
			try:
				if subprocess.run(cmd, input=text, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5).returncode == 0:
					return cmd[0]
			except subprocess.TimeoutExpired:
				pass
			# ponytail: a tool that failed (xclip with no DISPLAY) or hung: try the next one, else the escape
	out = sys.__stdout__  # curses owns sys.stdout's buffer; the raw tty still takes the escape
	out.write(f"\033]52;c;{base64.b64encode(text.encode()).decode()}\a")
	out.flush()
	return "terminal"
