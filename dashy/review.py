"""Run Claude headless on a PR and post its verdict."""
import json
import subprocess

from . import github, log

PROMPT = """Review pull request {repo}#{number}. Use `gh pr view {number} --repo {repo}` and
`gh pr diff {number} --repo {repo}` to read it. Look for bugs, logic errors, security issues and missing tests.
Respond with ONLY a JSON object, no prose, no code fences:
{{"verdict": "approve" | "request_changes" | "comment", "summary": "<one line, max 12 words: what the PR changes>",
 "body": "<markdown review, concise, list concrete findings with file:line>"}}
Use request_changes only for real defects, approve if it is mergeable, comment if unsure."""
TOOLS = "Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh api:*)"
TIMEOUT = 900


def review(pr, model):
	"""Review the PR, post the verdict, log it. Returns a status string for the row."""
	repo, n = pr["repository"]["nameWithOwner"], pr["number"]
	try:
		out = subprocess.run(
			["claude", "-p", PROMPT.format(repo=repo, number=n), "--output-format", "json",
			 "--allowedTools", TOOLS, "--model", model],
			capture_output=True, text=True, check=True, timeout=TIMEOUT,
		).stdout
		text = json.loads(out)["result"].strip()
		verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
		github.post_review(repo, n, verdict["verdict"], verdict["body"])
		return log.log_review(pr, model, verdict)
	except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, ValueError) as e:
		return "error: " + ((getattr(e, "stderr", None) or str(e)).strip().splitlines() or ["?"])[-1][:80]
