"""Everything that shells out to `gh`."""
import json
import subprocess

from . import log

FIELDS = "number,title,repository,url,updatedAt,isDraft,author"
SECTIONS = [
	("MINE", "--author=@me"),
	("REVIEW REQUESTED", "--review-requested=@me"),
	("ASSIGNED", "--assignee=@me"),
]
DECISION = {"APPROVED": "✓ approved", "CHANGES_REQUESTED": "✗ changes requested", "REVIEW_REQUIRED": "· awaiting review"}
DECISION_QUERY = """{ search(query: "is:pr is:open author:@me", type: ISSUE, first: 100) {
  nodes { ... on PullRequest { url reviewDecision reviewRequests { totalCount } } } } }"""


def own_status(node):
	"""Row status for my own PR from its reviewDecision + pending review requests."""
	decision, pending = node.get("reviewDecision"), (node.get("reviewRequests") or {}).get("totalCount", 0)
	if decision == "CHANGES_REQUESTED" and pending:
		return "↻ re-review requested"  # I pushed and asked again, reviewer has not looked yet
	return DECISION.get(decision, "")
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
			status = {n["url"]: own_status(n) for n in json.loads(raw)["data"]["search"]["nodes"] if n}
			for p in mine:
				p["status"] = status.get(p["url"], "")
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
