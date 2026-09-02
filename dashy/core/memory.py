"""Review memory: ~/.prs_memory/general.md plus one <owner>__<repo>.md per repo. Plain text, edit freely."""
import os

from .. import config


def path(repo=None):
	return os.path.join(config.MEMORY_DIR, (repo.replace("/", "__") if repo else "general") + ".md")


def read(repo):
	"""General + repo memory as one prompt block, '' when both are empty."""
	parts = []
	for label, p in (("General", path()), (repo, path(repo))):
		try:
			text = open(p).read().strip()
		except FileNotFoundError:
			text = ""
		if text:
			parts.append(f"## {label}\n{text}")
	return "\n\n".join(parts)


def append(repo, text):
	text = (text or "").strip()
	if not text:
		return
	os.makedirs(config.MEMORY_DIR, exist_ok=True)
	with open(path(repo), "a") as f:
		f.write("".join(f"- {l.strip().lstrip('-• ')}\n" for l in text.splitlines() if l.strip()))


DREAM = """You are tidying the review memory of a code-review bot. Below are its memory files, one per repo plus a
general one. Rewrite them: merge duplicates, drop contradictions, stale or vague lines, keep every concrete durable
fact, move repo-independent lines to general. Keep only overarching knowledge: how the repo is structured and why,
conventions, how it affects other repos or the database, which authors own which areas. Drop per-PR trivia (what one
PR changed, one-off bugs, "X is dead after #N") and anything derivable from git history. Keep the "- " bullet style,
one fact per line. Files not listed below must not be invented; return a file with empty content to delete it.

{files}

Respond with ONLY a JSON object, no prose, no code fences:
{{"summary": "<2-5 short lines: what you merged, dropped or moved>", "files": {{"<file name>": "<new content>", ...}}}}"""
TIMEOUT = 600


def files():
	"""{file name: content} for every memory file, general first."""
	try:
		names = sorted(f for f in os.listdir(config.MEMORY_DIR) if f.endswith(".md"))
	except FileNotFoundError:
		return {}
	names.sort(key=lambda n: n != "general.md")
	return {n: open(os.path.join(config.MEMORY_DIR, n)).read() for n in names}


def dream(model):
	"""Ask Claude to clean up all memory files. Returns (summary, {file name: new content}); raises on failure."""
	import json
	import subprocess
	before = files()
	if not before:
		raise ValueError("no memory to dream about")
	prompt = DREAM.format(files="\n\n".join(f"### {n}\n{t}" for n, t in before.items()))
	out = subprocess.run(["claude", "-p", prompt, "--output-format", "json", "--model", model]
	                     + (["--effort", config.EFFORT] if config.EFFORT else []),
	                     capture_output=True, text=True, check=True, timeout=TIMEOUT).stdout
	text = json.loads(out)["result"].strip()
	got = json.loads(text[text.index("{"):text.rindex("}") + 1])
	new = {n: str(got["files"].get(n, t)) for n, t in before.items()}  # ponytail: unknown names dropped, missing kept
	return str(got["summary"]), new


def write(new):
	"""Overwrite memory files from a dream(); empty content deletes the file."""
	for n, t in new.items():
		p = os.path.join(config.MEMORY_DIR, n)
		if t.strip():
			with open(p, "w") as f:
				f.write(t.strip() + "\n")
		elif os.path.exists(p):
			os.remove(p)
