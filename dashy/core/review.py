"""Run Claude headless on a PR and post its verdict."""
import datetime
import json
import os
import pathlib
import random
import tempfile
import subprocess
from urllib.parse import quote

from .. import config
from . import github, log, memory, team

PROMPT = """Review pull request {repo}#{number}. Use `gh pr view {number} --repo {repo}` and
`gh pr diff {number} --repo {repo}` to read it. Look for bugs, logic errors, security issues and missing tests.
{depth}{project}{memory}{prev}
Respond with ONLY a JSON object, no prose, no code fences:
{{"verdict": "approve" | "request_changes" | "comment", "summary": "<one line, max 12 words: what the PR changes>",
 "body": "<markdown review, concise, list concrete findings with file:line>",
 "findings": [{{"kind": "blocking" | "note" | "nit", "loc": "<file:line, or the file alone>", "text": "<one line, max 12 words>"}}],
 "depth_used": "low" | "medium" | "high", "depth_reason": "<one line: why that depth, e.g. '3-line docs change' or 'touches auth and db migration'>",
 "memory": "<0-3 short lines of overarching facts about this repo worth remembering for future reviews (architecture, conventions, effects on other repos or the database, which authors own which areas); never what this PR itself did; not already in memory; usually empty string>"}}
"findings" is the same review as "body", one line each, so a dashboard can list them: every blocking
finding must appear there. Empty list when there is nothing to report.
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


MARKER = "GITDASHY_SELFCHECK_MARKER"


def self_check(model):
	"""Prove the three things a review depends on. Returns [(name, ok, detail)].

	ponytail: unit tests assert the flags are passed; only a real call proves claude honours them. If
	--safe-mode stopped suppressing CLAUDE.md the reviews would not fail, they would just quietly inherit
	whatever is on the machine — so nothing else would ever tell us.
	"""
	out = []
	with tempfile.TemporaryDirectory() as d:
		with open(os.path.join(d, "CLAUDE.md"), "w") as f:
			f.write(f"# local\n\nThe marker is {MARKER}.\n")
		cmd = ["claude", "-p", f"Run the bash command: echo TOOLOK\nThen reply with exactly three words: "
		                        f"your codename, then YES or NO for whether your instructions mention {MARKER}, "
		                        f"then the command's output. Nothing else.",
		       "--output-format", "json", SAFE, "--append-system-prompt", "Your codename is SCOPED.",
		       "--allowedTools", "Bash(echo:*)", "--model", model]
		try:
			raw = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300, cwd=d).stdout
			said = json.loads(raw)["result"].strip()
		except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, KeyError, OSError) as e:
			return [("could not run claude", False, (getattr(e, "stderr", None) or str(e)).strip()[:120])]
	out.append(("--append-system-prompt reaches the model", "SCOPED" in said, said[:80]))
	out.append(("--safe-mode hides the local CLAUDE.md", MARKER not in said and " NO " in f" {said} ", said[:80]))
	out.append(("--allowedTools still runs tools", "TOOLOK" in said, said[:80]))
	return out


SELF_HEADER = """# Pre-review — {repo}#{n}

> **Not posted.** gitdashy reviewed your own PR at your request, {at}, with {model} at {depth} depth.
> Nothing was sent to GitHub and nothing was logged. Findings wait in the self drafts pool; a later real
> review that lands on the same fact by itself confirms it.

**Verdict (advisory):** {verdict} — {summary}

---

"""

SELF_DIR = os.path.expanduser("~/.prs_reviews")  # ponytail: outside every synced tree — scratch, not memory


def self_review_path(repo, n):
	"""Where a pre-review of this PR lives. One place that knows the name, and it is deterministic."""
	return os.path.join(SELF_DIR, f"{memory.slug(repo)[:-3]}__{n}.md")


def self_review_at(repo, n):
	"""When the pre-review on disk was written, or 0.0 when there is none.

	ponytail: the FILESYSTEM is the state. It was an in-memory dict, so restarting gitdashy left every
	pre-review on disk unreachable — `p` would silently run a new one over a file already sitting there.
	A deterministic name means nothing has to be remembered across a restart.
	"""
	try:
		return os.path.getmtime(self_review_path(repo, n))
	except OSError:
		return 0.0


def _verdict(repo, n, model, prev=None):
	"""Build the prompt, run the reviewer, return its parsed verdict. Raises on failure.

	ponytail: one implementation, because a pre-review that reasons differently from the real one is
	worth nothing as a preview of it. The only differences are what the caller does with the result.
	"""
	mem, brief = memory.read(repo), memory.project()
	prompt = PROMPT.format(repo=repo, number=n, depth=DEPTH[config.DEPTH],
	                       project="\n\nWhat this is being built for, and for whom:\n" + brief if brief else "",
	                       memory="\n\nMemory from earlier reviews, trust it:\n" + mem if mem else "",
	                       prev=PREV.format(at=prev["at"][:10], verdict=prev["verdict"], body=prev["body"]) if prev else "")
	if config.INSTRUCTIONS:  # read per review, so the file can be edited while gitdashy runs
		with open(config.INSTRUCTIONS) as f:
			prompt += "\n\nAdditional instructions from the reviewer:\n" + f.read()
	with tempfile.TemporaryDirectory() as here:
		# ponytail: run from a directory of our own. A review reads the PR through gh and nothing from
		# disk, so the launch directory is not merely irrelevant — inheriting it is a liability. It can
		# have been DELETED since (a checkout in that tree is enough), and claude then refuses to start
		# at all: "the current working directory was deleted", every review failing for no visible
		# reason. --safe-mode already ignores what is in it; this stops it mattering that it exists.
		out = subprocess.run(
			["claude", "-p", prompt, "--output-format", "json", SAFE, "--append-system-prompt", LENS,
			 "--allowedTools", TOOLS, "--model", model] + (["--effort", config.EFFORT] if config.EFFORT else []),
			capture_output=True, text=True, check=True, timeout=TIMEOUT, cwd=here,
		).stdout
	result = json.loads(out)
	text = result["result"].strip()
	verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
	verdict["cost"], verdict["ms"] = result.get("total_cost_usd"), result.get("duration_ms")  # claude reports both
	if config.DEPTH == "adaptive" and verdict.get("depth_used"):
		verdict["body"] += f"\n\n_Dashy reviewed at **{verdict['depth_used']}** depth: {verdict.get('depth_reason', '')}_"
	return verdict


def self_review(pr, model):
	"""Pre-review your own PR. Posts NOTHING. Returns (status, path-to-the-written-review).

	ponytail: nothing is posted, and not only because GitHub refuses to let you approve your own PR —
	a verdict on your own work is not a review, it is a second opinion from the same head. It goes to a
	file you read and act on, and the PR stays clean for whoever actually reviews it.
	ponytail: findings go to the SELF drafts pool, which never promotes on its own. See memory.append_self.
	"""
	repo, n = pr["repository"]["nameWithOwner"], pr["number"]
	try:
		verdict = _verdict(repo, n, model)  # ponytail: no `prev` — PREV claims "you already reviewed this",
		                                    # and a real reviewer's verdict is not ours to speak for
		os.makedirs(SELF_DIR, exist_ok=True)
		dest = self_review_path(repo, n)
		kept = memory.append_self(repo, verdict.get("memory"))
		if kept:
			# ponytail: every other writer pushes after writing — review(), remember, edit_memory, the dream.
			# Scratch or not, a memory dir backed by git must not depend on the next unrelated write to
			# carry these across; that is the kind of silent exception nobody remembers is there.
			team.push_dir(config.MEMORY_DIR, f"memory: pre-review {repo}#{n}", "mine")
		with open(dest, "w") as f:
			f.write(SELF_HEADER.format(repo=repo, n=n, at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
			                           model=model, depth=config.DEPTH,
			                           verdict=config.STATUS.get(verdict["verdict"], verdict["verdict"]),
			                           summary=verdict.get("summary", "")) + verdict["body"] + "\n")
		return f"{config.STATUS.get(verdict['verdict'], verdict['verdict'])} (not posted)" + (
			f" · {len(kept)} waiting" if kept else ""), dest
	except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as e:
		return "error: " + ((getattr(e, "stderr", None) or str(e)).strip().splitlines() or ["?"])[-1][:80], ""


def review(pr, model):
	"""Review the PR, post the verdict, log it. Returns a status string for the row."""
	repo, n = pr["repository"]["nameWithOwner"], pr["number"]
	try:
		prev = log.last(pr["url"])
		what = f"Re-reviewing (was {config.STATUS[prev['verdict']]} on {prev['at'][:10]})" if prev else "Reviewing"
		github.comment(repo, n, HELLO.format(sprite=sprite(), what=what, model=model, effort=config.EFFORT or "default", depth=config.DEPTH,
		                                     why=WHY.get(config.DEPTH, "set by the reviewer")))
		verdict = _verdict(repo, n, model, prev)
		github.post_review(repo, n, verdict["verdict"], verdict["body"])
		promoted = memory.append(repo, verdict.get("memory"))  # drafts, and whatever a second review confirmed
		status = log.log_review(pr, model, verdict)
		team.push(f"review {repo}#{n}: {verdict['verdict']}")
		team.push_dir(config.MEMORY_DIR, f"memory: {repo}#{n}" + (f", {len(promoted)} confirmed" if promoted else ""), "mine")
		return status
	except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as e:
		return "error: " + ((getattr(e, "stderr", None) or str(e)).strip().splitlines() or ["?"])[-1][:80]
