"""Everything that talks to GitHub.

ponytail: urllib against the API, no `gh` binary and no requests. One dependency less to install, and
every failure arrives as an Error (an OSError) instead of a string parsed out of someone's stderr.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import log

API = os.environ.get("GITHUB_API", "https://api.github.com")
# ponytail: graphql does not live under the REST root on Enterprise — /api/v3 and /api/graphql are
# siblings there, while on github.com the two share a host. One replace covers both.
GRAPHQL = API.replace("/api/v3", "/api").rstrip("/") + "/graphql"


class Error(OSError):
	"""ponytail: an OSError, so every `except OSError` already guarding these calls still catches it."""


def token():
	"""The API token: $GH_TOKEN or $GITHUB_TOKEN. ponytail: the environment, and nothing else — gh's own
	token store is gh's, and reading it would make an uninstall of gh look like a gitdashy failure."""
	return next((os.environ[v].strip() for v in ("GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(v)), "")


def call(path, method="GET", body=None, accept="application/vnd.github+json", timeout=30):
	"""One API call, returning the response text. Raises Error on anything that is not a 2xx."""
	url = path if path.startswith("http") else API + path
	tok = token()
	headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
	# ponytail: the token goes to the API host and nowhere else. A review reads untrusted diffs and can
	# choose the path it asks for, so an absolute URL in there must not be a way to post the token out.
	if tok and urllib.parse.urlparse(url).hostname == urllib.parse.urlparse(API).hostname:
		headers["Authorization"] = "Bearer " + tok
	if body is not None:
		headers["Content-Type"] = "application/json"
	req = urllib.request.Request(url, method=method,
	                             data=json.dumps(body).encode() if body is not None else None, headers=headers)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as r:
			return r.read().decode()
	except urllib.error.HTTPError as e:
		detail = ""
		try:
			detail = json.loads(e.read().decode()).get("message", "")
		except (ValueError, OSError):
			pass
		hint = " — no token: export GH_TOKEN=… (scope: repo)" if e.code in (401, 403) and not tok else ""
		raise Error(f"{e.code} {path}: {detail or e.reason}{hint}") from None
	except OSError as e:  # URLError, timeouts, DNS, no network — all OSError already
		raise Error(f"{path}: {e}") from None


def api(path, **kw):
	return json.loads(call(path, **kw))


def gql(query, timeout=60):
	"""GraphQL, returning `data`. Partial data survives partial errors (a missing scope drops fields)."""
	d = json.loads(call(GRAPHQL, "POST", {"query": query}, timeout=timeout))
	if d.get("data") is None:
		raise Error((d.get("errors") or [{}])[0].get("message", "graphql returned no data"))
	return d["data"]


_me = ""


def me():
	"""Your login. ponytail: `@me` is gh/UI sugar the API does not resolve, so the search needs the name."""
	global _me
	if not _me:
		_me = gql("{ viewer { login } }")["viewer"]["login"]
	return _me


SECTIONS = [("MINE", "author:{me}"), ("REVIEW REQUESTED", "review-requested:{me}"), ("ASSIGNED", "assignee:{me}")]
DECISION = {"APPROVED": "✓ approved", "CHANGES_REQUESTED": "✗ changes requested", "REVIEW_REQUIRED": "· awaiting review"}
# ponytail: ONE query for the whole dashboard — the three sections aliased, each carrying the row fields
# and the review decision, reviewers, head commit and CI state that no list endpoint returns.
# ponytail: no `... on Team { slug }` — that field needs read:org, and a token without it failed the WHOLE
# query, so CI, status and reviewers all vanished. Team review requests are simply not shown.
NODE = """{ nodes { ... on PullRequest { number title url updatedAt isDraft
    author { login } repository { nameWithOwner name } headRefOid reviewDecision
    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    reviewRequests(first: 20) { totalCount nodes { requestedReviewer { ... on User { login } } } }
    latestReviews(first: 20) { nodes { author { login } state } } } } }"""
CHECKS = {"SUCCESS": "✓", "FAILURE": "✗", "ERROR": "✗", "PENDING": "●", "EXPECTED": "●"}
REVIEW_GLYPH = {"APPROVED": "✓", "CHANGES_REQUESTED": "✗", "COMMENTED": "~", "PENDING": "·"}
ROW = ("number", "title", "url", "updatedAt", "isDraft", "author", "repository")


def query(who):
	return "{ " + " ".join(f's{i}: search(query: "is:pr is:open {q.format(me=who)}", type: ISSUE, first: 100) {NODE}'
	                       for i, (_, q) in enumerate(SECTIONS)) + " }"


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
		if r.get("login"):
			out[r["login"]] = "·"  # a fresh request supersedes an older review
	return " ".join(g + who for who, g in out.items())


def collaborators(repo):
	"""Logins with access to repo, [] when they cannot be listed (no admin, offline)."""
	try:
		return [c["login"] for c in api(f"/repos/{repo}/collaborators?per_page=100")]
	except (Error, ValueError, KeyError, TypeError):
		return []


def request_review(repo, number, login):
	"""Ask login to review PR number; the error text, or "" on success."""
	try:
		call(f"/repos/{repo}/pulls/{number}/requested_reviewers", "POST", {"reviewers": [login]})
	except Error as e:
		return str(e)
	return ""


def create_repo(repo, private=True):
	"""Create owner/name (an org repo when the owner is not you). "" or the error text."""
	owner, _, name = repo.partition("/")
	try:
		path = "/user/repos" if owner == me() else f"/orgs/{owner}/repos"
		call(path, "POST", {"name": name, "private": private}, timeout=60)
	except (Error, ValueError, KeyError) as e:
		return str(e)
	return ""


def git_auth():
	"""git flags that let a clone/push reach a private repo with the same token the API uses.

	ponytail: -c persists into the clone's own config, so later pulls on the refresh tick stay authorised
	without a credential helper. The token lands in that checkout's .git/config — same machine, same
	token, and the alternative was requiring `gh auth setup-git`.
	"""
	tok = token()
	return ["-c", f"http.extraHeader=Authorization: Bearer {tok}"] if tok else []


VERDICT_EVENT = {"approve": "APPROVE", "request_changes": "REQUEST_CHANGES", "comment": "COMMENT"}


def fetch():
	"""[(section name, [pr] or None, error string or None)] — one entry per SECTIONS, plus REVIEWED."""
	try:
		data = gql(query(me()))
	except (Error, ValueError, KeyError, TypeError) as e:
		err = str(e).strip().splitlines()[0] if str(e).strip() else "github unreachable"
		return [(name, None, err) for name, _ in SECTIONS] + [("REVIEWED", log.reviewed(), None)]
	seen, out = set(), []
	for i, (name, _) in enumerate(SECTIONS):
		prs = []
		for n in (data.get(f"s{i}") or {}).get("nodes") or []:
			if not n or n.get("url") in seen:  # ponytail: dedup across sections, first section wins
				continue
			seen.add(n["url"])
			p = {k: n.get(k) for k in ROW}
			p["checks"] = checks(n)
			if h := n.get("headRefOid"):  # ponytail: absent field reads like a failed call — no head, not ""
				p["head"] = h
			if name == "MINE":
				p["status"], p["reviewers"] = own_status(n), reviewers(n)
			prs.append(p)
		prs.sort(key=lambda p: p["updatedAt"], reverse=True)
		out.append((name, prs, None))
	out.append(("REVIEWED", log.reviewed(), None))  # ponytail: not deduped, a reviewed PR may still be open above
	return out


CHECK = {"SUCCESS": "ok", "COMPLETED": "ok", "NEUTRAL": "ok", "SKIPPED": "skip",
         "FAILURE": "fail", "ERROR": "fail", "TIMED_OUT": "fail", "CANCELLED": "fail",
         "IN_PROGRESS": "run", "QUEUED": "run", "PENDING": "run", "WAITING": "run", "EXPECTED": "run"}
DETAIL_QUERY = """{{ repository(owner: {owner}, name: {name}) {{ pullRequest(number: {number}) {{
    headRefName additions deletions changedFiles
    commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{ contexts(first: 20) {{ nodes {{
      ... on CheckRun {{ name conclusion status }}
      ... on StatusContext {{ context state }} }} }} }} }} }} }} }} }} }}"""


def detail(repo, number):
	"""Branch, diff size and CI checks for ONE pr. {} on any failure.

	ponytail: only ever for the selected row, and one query rather than a pull + a checks call. A pane
	is decoration: if it cannot be had, the row is still right, so nothing here raises.
	"""
	owner, _, name = repo.partition("/")
	try:
		d = gql(DETAIL_QUERY.format(owner=json.dumps(owner), name=json.dumps(name), number=int(number)),
		        timeout=30)["repository"]["pullRequest"]
	except (Error, ValueError, KeyError, TypeError):
		return {}
	checks = []
	for c in contexts(d):
		name = c.get("name") or c.get("context") or ""
		raw_state = (c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
		if name:
			checks.append({"name": name, "state": CHECK.get(raw_state, "run")})
	return {"branch": d.get("headRefName") or "", "add": d.get("additions"), "del": d.get("deletions"),
	        "files": d.get("changedFiles"), "checks": checks[:8]}


def contexts(pr):
	"""The check runs and status contexts on a PR's head commit, [] when it has none."""
	for c in ((pr.get("commits") or {}).get("nodes") or []):
		roll = (c.get("commit") or {}).get("statusCheckRollup") or {}
		return (roll.get("contexts") or {}).get("nodes") or []
	return []


def post_review(repo, number, verdict, body):
	"""Post the verdict on the PR. Raises Error on failure."""
	call(f"/repos/{repo}/pulls/{number}/reviews", "POST",
	     {"event": VERDICT_EVENT[verdict], "body": body}, timeout=60)


def comment(repo, number, body):
	"""Post a plain comment on the PR. Raises Error on failure."""
	call(f"/repos/{repo}/issues/{number}/comments", "POST", {"body": body}, timeout=60)


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


PR_CONTEXT_MAX = 200_000  # chars; a diff bigger than this is cut, since a context window is not free
CONTEXT = {"title": "title", "body": "body", "additions": "additions", "deletions": "deletions",
           "changed_files": "changedFiles", "base": "baseRefName", "head": "headRefName",
           "user": "author", "labels": "labels"}


def context(repo, number):
	"""The PR and its diff as one blob, for a model that cannot read GitHub itself. Raises on failure."""
	pr = api(f"/repos/{repo}/pulls/{number}", timeout=120)
	diff = call(f"/repos/{repo}/pulls/{number}", accept="application/vnd.github.v3.diff", timeout=120)
	flat = {"user": (pr.get("user") or {}).get("login"), "base": (pr.get("base") or {}).get("ref"),
	        "head": (pr.get("head") or {}).get("ref"), "labels": [l.get("name") for l in pr.get("labels") or []]}
	text = "\n".join(f"{name}: {json.dumps(v) if isinstance(v, (dict, list)) else v}"
	                 for key, name in CONTEXT.items() if (v := flat.get(key, pr.get(key))) is not None) + "\n\n" + diff
	return text[:PR_CONTEXT_MAX] + ("\n\n[diff truncated]" if len(text) > PR_CONTEXT_MAX else "")
