"""Canned PRs and a fake reviewer, so the UI can be eyeballed without gh or claude."""
import os
import time
from datetime import datetime, timedelta, timezone
from itertools import cycle

from . import config
from .core import github, log, memory, review, update


def pr(n, title, repo="acme/api", author="alice", hours=1, draft=False, now=None):
	now = now or datetime.now(timezone.utc)
	return {"number": n, "title": title, "url": f"https://github.com/{repo}/pull/{n}", "isDraft": draft,
	        "repository": {"nameWithOwner": repo, "name": repo.split("/")[1]}, "author": {"login": author},
	        "updatedAt": (now - timedelta(hours=hours)).isoformat()}


def install():
	"""ponytail: swap the three module attrs the app calls out through — no injection framework."""
	config.TEAM = ""  # never sync the demo
	log.LOG = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"prs-demo-{os.getpid()}.jsonl")
	config.MEMORY_DIR = log.LOG[:-6] + "-memory"  # Z dream must never rewrite the real memory
	memory.append(None, "run make lint before flagging style")
	memory.append("acme/api", "uses tabs\nuses tabs\nold CI on jenkins, ignore")
	now = datetime.now(timezone.utc)
	mine = [dict(pr(101, "Add retry to webhook client", hours=2, now=now), status="· awaiting review", reviewers="✓bob ·carol"),
	        dict(pr(98, "WIP: migrate to pydantic v2 and drop the hand-rolled validators in the ingest and export paths",
	                hours=30, draft=True, now=now), reviewers="")]  # long title: overflows most terminals, shows the marquee
	rr = [pr(212, "Fix off-by-one in pagination", "acme/web", "bob", 1, now=now),
	      pr(207, "Cache user lookups in session middleware", "acme/web", "carol", 5, now=now),
	      pr(55, "Rotate signing keys and bump KMS alias", "acme/infra", "dave", 48, now=now)]
	late = pr(213, "Hotfix: null check in export job", "acme/web", "bob", 0, now=now)  # 3rd refresh, exercises auto
	assigned = [pr(300, "Flaky integration test in CI", "acme/api", "erin", 72, now=now)]
	seed = [(pr(180, "Refactor auth middleware", "acme/api", "frank", 26, now=now),  # older review, folds under the newer one
	         {"verdict": "request_changes", "summary": "Splits auth middleware into token parsing and policy checks.",
	          "body": "- `api/auth.py:40` policy check runs before the token is validated"}),
	        (pr(180, "Refactor auth middleware", "acme/api", "frank", 3, now=now),
	         {"verdict": "approve", "summary": "Splits auth middleware into token parsing and policy checks.",
	          "body": "LGTM. Clean split, existing tests still cover both paths."}),
	        (pr(44, "Add S3 lifecycle rules", "acme/infra", "grace", 5, now=now),
	         {"verdict": "request_changes", "summary": "Expires logs after 30 days, moves backups to Glacier.",
	          "body": "- `infra/s3.tf:31` rule also matches the `backups/` prefix, would delete backups after 30d\n"
	                  "- no plan output attached"})]
	for p, v in seed:
		log.log_review(p, "opus", v, at=p["updatedAt"])

	fetches = [0]
	def fake_fetch():
		fetches[0] += 1
		time.sleep(1)
		rr_now = rr + ([late] if fetches[0] >= 3 else []) + [dict(seed[2][0], updatedAt=now.isoformat())]
		return [("MINE", mine, None), ("REVIEW REQUESTED", rr_now, None), ("ASSIGNED", assigned, None),
		        ("REVIEWED", log.reviewed(), None)]

	verdicts = cycle([
		{"verdict": "approve", "summary": "Fixes pagination when the page index is zero.",
		 "body": "LGTM, regression test added."},
		{"verdict": "request_changes", "summary": "Caches user lookups for the lifetime of a session.",
		 "body": "- `web/session.py:88` cache never invalidated on logout\n- missing test for cache miss path"},
		{"verdict": "comment", "summary": "Rotates the signing keys and points the KMS alias at the new key.",
		 "body": "Unsure whether old tokens must stay valid during rollover; please confirm."},
		None,
	])
	def fake_review(p, model):
		time.sleep(5)
		v = next(verdicts)
		return log.log_review(p, model, v) if v else "error: claude: rate limit exceeded, retry in 60s"

	def fake_dream(model):
		time.sleep(4)
		return ("merged 2 duplicate lines about tabs in acme/api\nmoved 'run make lint' to general\ndropped a stale note about the old CI",
		        {n: "\n".join(dict.fromkeys(t.splitlines())) for n, t in memory.files().items()})

	def fake_request_review(repo, number, login):
		time.sleep(1)
		for p in mine:
			if p["number"] == number:
				p["reviewers"] = (p["reviewers"] + f" ·{login}").strip()
		return "" if login != "dave" else "dave is on leave (demo error)"

	github.fetch, review.review, update.update_available, memory.dream = fake_fetch, fake_review, lambda: "", fake_dream
	github.collaborators, github.request_review, github.copy = lambda repo: ["alice", "bob", "carol", "dave", "erin"], fake_request_review, lambda t: "demo"
