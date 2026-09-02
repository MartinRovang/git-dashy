"""Run Claude headless on a PR and post its verdict."""
import json
import random
import subprocess

from .. import config
from . import github, log, memory, team

PROMPT = """Review pull request {repo}#{number}. Use `gh pr view {number} --repo {repo}` and
`gh pr diff {number} --repo {repo}` to read it. Look for bugs, logic errors, security issues and missing tests.
{depth}{memory}
Respond with ONLY a JSON object, no prose, no code fences:
{{"verdict": "approve" | "request_changes" | "comment", "summary": "<one line, max 12 words: what the PR changes>",
 "body": "<markdown review, concise, list concrete findings with file:line>",
 "depth_used": "low" | "medium" | "high", "depth_reason": "<one line: why that depth, e.g. '3-line docs change' or 'touches auth and db migration'>",
 "memory": "<0-3 short lines of overarching facts about this repo worth remembering for future reviews (architecture, conventions, effects on other repos or the database, which authors own which areas); never what this PR itself did; not already in memory; usually empty string>"}}
Use request_changes only for real defects, approve if it is mergeable, comment if unsure."""
DEPTH = {
	"low": "Depth: minimal. Skim the diff once, flag only obvious defects, keep the body to a few lines.",
	"medium": "Depth: medium. Read the whole diff carefully, check the changed logic and its tests.",
	"high": "Depth: very in-depth. Read the whole diff, then use `gh api` to read the surrounding files the changes touch, "
	        "trace callers, check edge cases, error paths, concurrency and security thoroughly.",
	"adaptive": "Depth: adaptive. Judge from the diff size and risk: a few trivial lines get a quick skim, "
	            "a large or risky change gets a very in-depth review that reads surrounding code via `gh api`.",
}
SPRITE = "https://raw.githubusercontent.com/MartinRovang/git-dashy/main/sprites/sprite_{:03d}.png"
HELLO = """<img src="{sprite}" width="200">

**Dashy is on its way!** Reviewing with model **{model}**, effort **{effort}** and depth **{depth}** ({why})."""
WHY = {"adaptive": "Dashy picks the depth from the diff size and risk"}  # other depths: set by the reviewer
TOOLS = "Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh api:*)"
TIMEOUT = 900


def review(pr, model):
	"""Review the PR, post the verdict, log it. Returns a status string for the row."""
	repo, n = pr["repository"]["nameWithOwner"], pr["number"]
	try:
		mem = memory.read(repo)
		prompt = PROMPT.format(repo=repo, number=n, depth=DEPTH[config.DEPTH],
		                       memory="\n\nMemory from earlier reviews, trust it:\n" + mem if mem else "")
		if config.INSTRUCTIONS:  # read per review, so the file can be edited while gitdashy runs
			with open(config.INSTRUCTIONS) as f:
				prompt += "\n\nAdditional instructions from the reviewer:\n" + f.read()
		github.comment(repo, n, HELLO.format(sprite=SPRITE.format(random.randint(1, 12)), model=model, effort=config.EFFORT or "default", depth=config.DEPTH,
		                                     why=WHY.get(config.DEPTH, "set by the reviewer")))
		out = subprocess.run(
			["claude", "-p", prompt, "--output-format", "json",
			 "--allowedTools", TOOLS, "--model", model] + (["--effort", config.EFFORT] if config.EFFORT else []),
			capture_output=True, text=True, check=True, timeout=TIMEOUT,
		).stdout
		text = json.loads(out)["result"].strip()
		verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
		if config.DEPTH == "adaptive" and verdict.get("depth_used"):
			verdict["body"] += f"\n\n_Dashy reviewed at **{verdict['depth_used']}** depth: {verdict.get('depth_reason', '')}_"
		github.post_review(repo, n, verdict["verdict"], verdict["body"])
		memory.append(repo, verdict.get("memory"))
		status = log.log_review(pr, model, verdict)
		team.push(f"review {repo}#{n}: {verdict['verdict']}")
		return status
	except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as e:
		return "error: " + ((getattr(e, "stderr", None) or str(e)).strip().splitlines() or ["?"])[-1][:80]
