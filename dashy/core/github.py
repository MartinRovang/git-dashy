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
DECISION_QUERY = """{ search(query: "is:pr is:open author:@me", type: ISSUE, first: 100) {
  nodes { ... on PullRequest { url reviewDecision
    reviewRequests(first: 20) { totalCount nodes { requestedReviewer { ... on User { login } ... on Team { slug } } } }
    latestReviews(first: 20) { nodes { author { login } state } } } } } }"""
REVIEW_GLYPH = {"APPROVED": "✓", "CHANGES_REQUESTED": "✗", "COMMENTED": "~", "PENDING": "·"}


def own_status(node):
	"""Row status for my own PR from its reviewDecision + pending review requests."""
	decision, pending = node.get("reviewDecision"), (node.get("reviewRequests") or {}).get("totalCount", 0)
	if decision == "CHANGES_REQUESTED" and pending:
		return "↻ re-review requested"  # I pushed and asked again, reviewer has not looked yet
	return DECISION.get(decision, "")


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
	mine = out and out[0][1] or []
	if mine:  # review status of my own PRs: search has no reviewDecision, one graphql call does
		try:
			raw = subprocess.run(["gh", "api", "graphql", "-f", "query=" + DECISION_QUERY],
			                     capture_output=True, text=True, check=True, timeout=60).stdout
			nodes = {n["url"]: n for n in json.loads(raw)["data"]["search"]["nodes"] if n}
			for p in mine:
				n = nodes.get(p["url"], {})
				p["status"], p["reviewers"] = own_status(n), reviewers(n)
		except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, KeyError):
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
	subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
