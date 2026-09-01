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
	out.append(("REVIEWED", log.reviewed(), None))  # ponytail: not deduped, a reviewed PR may still be open above
	return out


def post_review(repo, number, verdict, body):
	"""Post the verdict on the PR. Raises CalledProcessError / TimeoutExpired on failure."""
	subprocess.run(["gh", "pr", "review", str(number), "--repo", repo, VERDICT_FLAG[verdict], "--body", body],
	               capture_output=True, text=True, check=True, timeout=60)


def open_in_browser(url):
	subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
