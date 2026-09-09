"""Everything that talks to GitHub.

ponytail: urllib against the API, no `gh` binary and no requests. One dependency less to install, and
every failure arrives as an Error (an OSError) instead of a string parsed out of someone's stderr.
"""
import base64
import json
import logging
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


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
	"""Drop the token when a 3xx leaves the host it was issued for.

	ponytail: urllib copies every header onto the redirected request, so the host check in `call()` would
	guard only the request this code builds, not the one urllib may end up sending. Installed globally
	because `urlopen` is what the rest of the module calls — one opener, no plumbing through every site.
	"""
	def redirect_request(self, req, fp, code, msg, headers, newurl):
		new = super().redirect_request(req, fp, code, msg, headers, newurl)
		if new and urllib.parse.urlparse(newurl).hostname != urllib.parse.urlparse(req.full_url).hostname:
			new.headers = {k: v for k, v in new.headers.items() if k.lower() != "authorization"}
		return new


urllib.request.install_opener(urllib.request.build_opener(_StripAuthOnRedirect))


class Error(OSError):
	"""ponytail: an OSError, so every `except OSError` already guarding these calls still catches it."""


def token():
	"""The API token: $GH_TOKEN or $GITHUB_TOKEN. ponytail: the environment, and nothing else — gh's own
	token store is gh's, and reading it would make an uninstall of gh look like a gitdashy failure."""
	return next((os.environ[v].strip() for v in ("GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(v)), "")


SCOPE = "PRS_API_REPO"  # set on a review's own subprocess: the repo it is reviewing
SCOPE_TEAM = "PRS_API_TEAM"  # and the team whose other repos it may read too, "" for none. See scoped().
QUALIFIERS = ("repo:", "user:", "org:", "owner:")  # search terms that choose WHERE to look, not what for


def repo_of(path):
	"""The owner/name a `/repos/...` path addresses, "" when it addresses something else."""
	part = path.lstrip("/").split("/")
	return f"{part[1]}/{part[2]}" if len(part) >= 3 and part[0].lower() == "repos" and part[1] and part[2] else ""


def scoped(path, repo, team=""):
	"""`path`, rewritten so it can only read `repo`. Raises ValueError when it cannot be. "" = unscoped.

	ponytail: the reviewer's input is an untrusted diff and its output is published on that diff's PR, so
	"read anything the token can" closes a loop — steer the read, and the answer is posted for you. The
	read is GET-only and host-pinned already; this is the third side, and the one that was open.
	ponytail: the scope arrives in the ENVIRONMENT, not in the argv the model writes. There is nothing a
	prompt can say that widens it, and nothing to keep in step with the --allowedTools pattern.
	ponytail: "" means unscoped, which is a person at a terminal. Their own `gitdashy api /user/repos`
	is not the threat and refusing it would only teach them to work around this.
	"""
	if not repo:
		return path
	p = path if path.startswith("/") else "/" + path
	head, _, query = p.partition("?")
	# ponytail: no dot segments, in any spelling. api.github.com 404s /repos/<scope>/../../user today, so
	# the prefix check is not bypassable there — but that is the SERVER refusing, not us, and GitHub
	# Enterprise can sit behind a proxy that normalises before it forwards. A boundary that holds only
	# because the far end happens to be strict is one deployment away from not holding. Unquoted for the
	# test and never for the request, so what is sent is still exactly what was asked for.
	if any(urllib.parse.unquote(seg) == ".." for seg in head.split("/")):
		raise ValueError(f"a review may not use .. in a path, and {head} does")
	want, low = f"/repos/{repo}".lower(), head.lower()
	# ponytail: the separator matters. Bare startswith let /repos/acme/api-secrets through on a scope of
	# acme/api — a neighbouring repo, which is exactly the kind an attacker would guess at.
	if low == want or low.startswith(want + "/"):
		return p
	# ponytail: and a repo DECLARED to belong with this one. Resolved through bind.of rather than against
	# a list built here, so the precedence — an exact binding, then a deliberate unbind, then the owner
	# rule — is the one bind already publishes, and a repo excluded from an owner rule stays excluded. A
	# second copy of that ordering is how the two would come to disagree about one repo.
	if team and (other := repo_of(head)) and other.lower() != repo.lower():
		from . import bind  # ponytail: lazy — bind reaches team, which reaches log, which reaches here
		# ponytail: the name has to BE its own key before the key is looked up. bind.key strips a `.git`
		# suffix and slug_of folds `:`, so /repos/acme/shared-lib.git/... resolved to a bound sibling and
		# was then sent verbatim — the check normalising one string and the request carrying another.
		# GitHub 404s that form today, which is the same "the server saves us" argument as the dots above.
		if bind.key(other) == other.lower() and bind.of(other) == team:
			return p
	# ponytail: search stays on the repo under review even when reads are wider. Several repo: qualifiers
	# would have to OR for that to be safe, and leaning a boundary on GitHub's query semantics is what
	# the refusal loop below already declines to do. A sibling is read by path, not searched.
	if low.rstrip("/") == "/search/code":
		return "/search/code?" + scoped_query(query, repo)
	if low.startswith("/search/"):
		# ponytail: "outside it" describes a repo path, and a model reading it about /search/repositories
		# learns nothing it can act on. Say which search there is.
		raise ValueError(f"a review may only search code, in {repo} — {head} is not /search/code")
	raise ValueError(f"a review may only read {repo}" + (f" and the repos bound to {team}" if team else "")
	                 + f", and {head} is outside it")


def scoped_query(query, repo):
	"""A /search/code query string forced to `repo`. Raises ValueError when it names anywhere else.

	ponytail: REWRITTEN, not merely checked. A `q` carrying no qualifier at all searches every repo the
	token can see, so refusing only the ones that name someone else would leave the default — the form a
	model reaches for first — wide open. parse_qsl also turns `+` back into a space, which is how the
	qualifier in "q=SECRET+user:victim" becomes visible as a term rather than hiding inside one.
	"""
	parts = urllib.parse.parse_qsl(query)
	# ponytail: one pass. A foreign qualifier is refused rather than silently narrowed — the forced
	# repo: ANDs, so it would return nothing anyway, but that is GitHub's query semantics holding the
	# line rather than us, and a model handed an empty result cannot tell "nobody uses this symbol"
	# from "you asked the wrong question".
	terms = []
	for t in " ".join(v for k, v in parts if k == "q").split():
		if not t.lower().startswith(QUALIFIERS):
			terms.append(t)
		elif t.lower() != f"repo:{repo}".lower():
			raise ValueError(f"a review may only search {repo}, so {t} cannot be asked for")
	if not terms:
		# ponytail: a qualifier on its own is a 422 from GitHub, which reads as the scoping being broken.
		raise ValueError("a code search needs something to search for, not just a repo")
	terms += [f"repo:{repo}"]
	return urllib.parse.urlencode([("q", " ".join(terms))] + [(k, v) for k, v in parts if k != "q"])


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
	logging.getLogger(__name__).debug("%s %s", method, url)
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
			out[n["author"]["login"]] = n.get("state")
	for n in (node.get("reviewRequests") or {}).get("nodes") or []:
		r = (n or {}).get("requestedReviewer") or {}
		# ponytail: a fresh request supersedes an older review — EXCEPT a comment. GitHub clears the
		# request when a review approves or requests changes, so a reviewer in BOTH lists really was
		# asked again. Commenting clears nothing, so the standing request is the ORIGINAL one, and
		# stomping it made every comment invisible to everyone but the person who left it.
		# Compare the STATE, not the glyph: DISMISSED has no glyph and must not read as a comment.
		if r.get("login") and out.get(r["login"]) != "COMMENTED":
			out[r["login"]] = "PENDING"
	return " ".join(REVIEW_GLYPH.get(s, "·") + who for who, s in out.items())


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


GITHUB = "https://github.com/"  # ponytail: the header is scoped to this remote — an unscoped extraHeader
                                # would hand the token to ANY http remote the checkout later talks to.


def git_auth():
	"""Env that lets a clone reach a private repo with the same token the API uses.

	ponytail: GIT_CONFIG_* in the environment, not `-c` in argv — argv is world-readable in `ps` for the
	length of a clone. It does not survive into the new checkout, so `persist_auth()` writes it there.
	"""
	return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{GITHUB}.extraHeader",
	        "GIT_CONFIG_VALUE_0": f"Authorization: {h}"} if (h := git_header()) else {}


def git_header():
	"""The Authorization value git's smart-http endpoint accepts for token(). "" without a token.

	ponytail: Basic, not Bearer. The REST API takes a PAT either way; the git endpoint answers Bearer
	with "remote: invalid credentials" and only takes the token as a Basic password — the form gh's
	own credential helper sends. One place, so the clone and the persisted config cannot drift.
	"""
	tok = token()
	return "Basic " + base64.b64encode(f"x-access-token:{tok}".encode()).decode() if tok else ""


def persist_auth(dest):
	"""Put the token in a fresh checkout's config so later pulls on the refresh tick stay authorised.

	ponytail: appended by hand rather than `git config`, which would put the token back in argv — the
	thing git_auth() exists to avoid. chmod first: the secret is never on disk world-readable.
	"""
	h, cfg = git_header(), os.path.join(dest, ".git", "config")
	if not h or not os.path.isfile(cfg):
		return
	os.chmod(cfg, 0o600)
	with open(cfg, "a") as f:
		f.write(f'[http "{GITHUB}"]\n\textraHeader = Authorization: {h}\n')


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
			# ponytail: reviewers on EVERY section, not just MINE. A "~alice" only ever painted on my
			# own rows, so a comment on someone else's PR was visible to nobody looking at it. status
			# stays MINE-only — own_status reads reviewDecision as "what is blocking ME", which is not
			# the question an assigned or requested row asks.
			p["reviewers"] = reviewers(n)
			if name == "MINE":
				p["status"] = own_status(n)
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
