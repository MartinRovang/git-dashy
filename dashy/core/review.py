"""Run Claude headless on a PR and post its verdict."""
import json
import pathlib
import random
import subprocess
from urllib.parse import quote

from .. import config
from . import github, log, memory, team

PROMPT = """Review pull request {repo}#{number}. Use `gh pr view {number} --repo {repo}` and
`gh pr diff {number} --repo {repo}` to read it. Look for bugs, logic errors, security issues and missing tests.
{depth}{memory}{prev}
Respond with ONLY a JSON object, no prose, no code fences:
{{"verdict": "approve" | "request_changes" | "comment", "summary": "<one line, max 12 words: what the PR changes>",
 "body": "<markdown review, concise, list concrete findings with file:line>",
 "depth_used": "low" | "medium" | "high", "depth_reason": "<one line: why that depth, e.g. '3-line docs change' or 'touches auth and db migration'>",
 "memory": "<0-3 short lines of overarching facts about this repo worth remembering for future reviews (architecture, conventions, effects on other repos or the database, which authors own which areas); never what this PR itself did; not already in memory; usually empty string>"}}
Use request_changes only for real defects, approve if it is mergeable, comment if unsure."""
PREV = """

This is a RE-REVIEW: you already reviewed this PR on {at} with verdict {verdict}. The PR has been updated since.
Your earlier review was:
{body}

Do not treat the PR as new. Say which earlier findings are fixed and which still stand, then review what changed since."""
DEPTH = {
	"low": "Depth: minimal. Skim the diff once, flag only obvious defects, keep the body to a few lines.",
	"medium": "Depth: medium. Read the whole diff carefully, check the changed logic and its tests.",
	"high": "Depth: very in-depth. Read the whole diff, then use `gh api` to read the surrounding files the changes touch, "
	        "trace callers, check edge cases, error paths, concurrency and security thoroughly.",
	"adaptive": "Depth: adaptive. Judge from the diff size and risk: a few trivial lines get a quick skim, "
	            "a large or risky change gets a very in-depth review that reads surrounding code via `gh api`.",
}
SPRITE_DIR = pathlib.Path(__file__).parents[2] / "sprites"  # any .png in here, at any depth, joins the rotation
SPRITE_URL = "https://raw.githubusercontent.com/MartinRovang/git-dashy/main/sprites/"
HELLO = """{sprite}**Dashy is on its way!** {what} with model **{model}**, effort **{effort}** and depth **{depth}** ({why})."""
WHY = {"adaptive": "Dashy picks the depth from the diff size and risk"}  # other depths: set by the reviewer
TOOLS = "Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh api:*)"
# ponytail: --safe-mode drops CLAUDE.md, skills, hooks and MCP for this call. Two reasons: a personal
# CLAUDE.md is a dialogue protocol, and this call has no dialogue — it has a JSON contract it can break by
# answering in prose. And without it the prompt would depend on which directory gitdashy was launched from.
SAFE = "--safe-mode"
LENS = """You are reviewing a pull request. Reason about structure before style.

For every change ask: where does the state live and who owns it; where does feedback or observability live;
what breaks if this is deleted; and when does the timing work — ordering, async boundaries, races. Danger
concentrates in the seams: between services, across process and async boundaries, at database calls, wherever
two systems agree on a contract. Read the definition of a thing, not just the code that uses it — inferring a
type or a contract from a call site is how real defects survive review. Before flagging a deviation, check
whether it is already the established pattern in this codebase; an intentional oddity is not a defect.
Security is structural, not a checklist appended at the end. Watch for duplicated or doubled logic, and say
plainly what you verified first-hand and what you took on trust."""
TIMEOUT = 900


def sprite():
	"""An <img> tag for a random sprite, or "" if the sprites dir is empty."""
	paths = [p.relative_to(SPRITE_DIR).as_posix() for p in SPRITE_DIR.rglob("*.png")]
	return f'<img src="{SPRITE_URL + quote(random.choice(paths))}" width="120">\n\n' if paths else ""


def review(pr, model):
	"""Review the PR, post the verdict, log it. Returns a status string for the row."""
	repo, n = pr["repository"]["nameWithOwner"], pr["number"]
	try:
		mem, prev = memory.read(repo), log.last(pr["url"])
		prompt = PROMPT.format(repo=repo, number=n, depth=DEPTH[config.DEPTH],
		                       memory="\n\nMemory from earlier reviews, trust it:\n" + mem if mem else "",
		                       prev=PREV.format(at=prev["at"][:10], verdict=prev["verdict"], body=prev["body"]) if prev else "")
		if config.INSTRUCTIONS:  # read per review, so the file can be edited while gitdashy runs
			with open(config.INSTRUCTIONS) as f:
				prompt += "\n\nAdditional instructions from the reviewer:\n" + f.read()
		what = f"Re-reviewing (was {config.STATUS[prev['verdict']]} on {prev['at'][:10]})" if prev else "Reviewing"
		github.comment(repo, n, HELLO.format(sprite=sprite(), what=what, model=model, effort=config.EFFORT or "default", depth=config.DEPTH,
		                                     why=WHY.get(config.DEPTH, "set by the reviewer")))
		out = subprocess.run(
			["claude", "-p", prompt, "--output-format", "json", SAFE, "--append-system-prompt", LENS,
			 "--allowedTools", TOOLS, "--model", model] + (["--effort", config.EFFORT] if config.EFFORT else []),
			capture_output=True, text=True, check=True, timeout=TIMEOUT,
		).stdout
		text = json.loads(out)["result"].strip()
		verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
		if config.DEPTH == "adaptive" and verdict.get("depth_used"):
			verdict["body"] += f"\n\n_Dashy reviewed at **{verdict['depth_used']}** depth: {verdict.get('depth_reason', '')}_"
		github.post_review(repo, n, verdict["verdict"], verdict["body"])
		promoted = memory.append(repo, verdict.get("memory"))  # drafts, and whatever a second review confirmed
		status = log.log_review(pr, model, verdict)
		team.push(f"review {repo}#{n}: {verdict['verdict']}")
		team.push_dir(config.MEMORY_DIR, f"memory: {repo}#{n}" + (f", {len(promoted)} confirmed" if promoted else ""), "mine")
		return status
	except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as e:
		return "error: " + ((getattr(e, "stderr", None) or str(e)).strip().splitlines() or ["?"])[-1][:80]
