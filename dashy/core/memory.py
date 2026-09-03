"""Review memory. Two sources — your own and the team's — read together, written apart.

A fact the model proposes is not a fact yet: it is a draft, and it becomes yours only once independent
reviews land on it again. That promotion is automatic, because being wrong there costs only you. Reaching
the team's memory is never automatic — that lands in contexts where nobody who could correct it will see
it happen — so it takes one keypress from you.
"""
import difflib
import json
import os
import re

from .. import config
from . import log, team

QUEUE = "drafts"  # under your own memory dir: unconfirmed facts, and how often each has recurred
POOL = "pool"  # under the team's memory: facts each person has accepted, as evidence only, never read
PROMOTE_AT = 2  # independent reviews that must land on a fact before it becomes one of yours
NEAR = 0.82  # difflib ratio above which two wordings count as the same fact


def slug(repo):
	return (repo.replace("/", "__") if repo else "general") + ".md"


def path(repo=None, base=None):
	return os.path.join(base or config.MEMORY_DIR, slug(repo))


def queue_path(repo):
	return os.path.join(config.MEMORY_DIR, QUEUE, slug(repo))


def sources():
	"""(label, dir) for each approved source, yours first. ponytail: drafts/ is deliberately not one."""
	out = [("mine", config.MEMORY_DIR)]
	if team.on():
		out.append(("team " + (team.NAME or "shared"), os.path.join(config.TEAM, "memory")))
	return out


def _read(p):
	try:
		return open(p).read().strip()
	except OSError:
		return ""


def scope_text(repo=None):
	"""One scope's approved memory, merged across sources and labelled by where each part came from."""
	parts = [f"### {label}\n{t}" for label, base in sources() if (t := _read(path(repo, base)))]
	return "\n\n".join(parts)


def read(repo):
	"""General + repo memory from every source as one prompt block, '' when there is none."""
	parts = [f"## {name}\n{t}" for name, r in (("General", None), (repo, repo)) if (t := scope_text(r))]
	return "\n\n".join(parts)


def _norm(s):
	return " ".join(s.lower().replace("`", "").split())


def _same(a, b):
	return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= NEAR


def _parse(line):
	""""- (2) a fact" -> (2, "a fact"). A line with no counter has been seen once."""
	m = re.match(r"^-\s*\((\d+)\)\s*(.*)$", line.strip())
	return (int(m.group(1)), m.group(2).strip()) if m else (1, line.strip().lstrip("-• ").strip())


def _facts(p):
	return [_parse(l)[1] for l in _read(p).splitlines() if l.strip()]


def _append_line(p, fact):
	os.makedirs(os.path.dirname(p), exist_ok=True)
	with open(p, "a") as f:
		f.write(f"- {fact}\n")


def whoami():
	return re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("USER", "")) or "someone"


def pool_path(user, repo):
	return os.path.join(config.TEAM, "memory", POOL, user, slug(repo))


def logged_repos():
	"""Repos named in the shared review log — the team can already see these names."""
	out = set()
	try:
		with open(log.LOG) as f:
			for line in f:
				try:
					out.add(json.loads(line)["pr"]["repository"]["nameWithOwner"])
				except (ValueError, KeyError, TypeError):
					continue
	except OSError:
		pass
	return out


def _pool(repo, fact):
	"""Publish a fact you have accepted, as evidence that you did. Never read into any prompt.

	ponytail: only for repos already named in the shared log. Reviewing there put the repo in front of
	the team already, so this discloses nothing that reviewing did not — and it bootstraps on its own,
	which "repos the team already has memory for" could not, since at the start that set is empty.
	"""
	if team.on() and repo in logged_repos():
		_append_line(pool_path(whoami(), repo), fact)


def pools():
	"""{user: [(repo, fact)]} across everyone's pool. {} when you are not in a team."""
	root = os.path.join(config.TEAM, "memory", POOL)
	out = {}
	for user in sorted(os.listdir(root)) if team.on() and os.path.isdir(root) else []:
		d = os.path.join(root, user)
		if not os.path.isdir(d):
			continue
		items = []
		for name in sorted(os.listdir(d)):
			if name.endswith(".md"):
				repo = None if name == "general.md" else name[:-3].replace("__", "/")
				items += [(repo, f) for f in _facts(os.path.join(d, name))]
		if items:
			out[user] = items
	return out


def backers(index, repo, fact):
	"""Who has accepted this fact, from a pools() index. Two names is two people's reviewers agreeing."""
	return sorted(u for u, items in index.items() if any(r == repo and _same(f, fact) for r, f in items))


def known(repo):
	"""Every approved fact already covering `repo`, across both sources and both scopes."""
	return [f for _, base in sources() for scope in (None, repo) for f in _facts(path(scope, base))]


def already_known(repo, fact):
	"""True when this fact is already approved somewhere that covers `repo`."""
	return any(_same(fact, t) for t in known(repo))


def drafts(repo):
	"""[(count, fact)] for one repo's unconfirmed facts."""
	return [_parse(l) for l in _read(queue_path(repo)).splitlines() if l.strip()]


def _write_drafts(repo, items):
	p = queue_path(repo)
	if not items:
		if os.path.exists(p):
			os.remove(p)
		return
	os.makedirs(os.path.dirname(p), exist_ok=True)
	with open(p, "w") as f:
		f.write("".join(f"- ({n}) {t}\n" for n, t in items))


def append(repo, text):
	"""Record what a review proposed; return the facts that just became yours.

	ponytail: drafts are NEVER read back into a prompt. If they were, the reviewer would meet its own
	earlier guess as evidence and agree with itself — the count has to come from rediscovery, not recall.
	That is the whole difference between measuring durability and keeping a tally.
	"""
	proposed = [l.strip().lstrip("-• ").strip() for l in (text or "").splitlines() if l.strip()]
	if not proposed:
		return []
	items, settled = drafts(repo), known(repo)
	for fact in proposed:
		if any(_same(fact, t) for t in settled):
			continue  # already approved somewhere: proposing it again says nothing new
		for i, (n, t) in enumerate(items):
			if _same(t, fact):
				items[i] = (n + 1, t)  # the first wording wins; the count is what carries meaning
				break
		else:
			items.append((1, fact))
	_write_drafts(repo, [(n, t) for n, t in items if n < PROMOTE_AT])
	promoted = [t for n, t in items if n >= PROMOTE_AT]
	for t in promoted:
		_append_line(path(repo), t)
		_pool(repo, t)
	return promoted


def shareable():
	"""[(repo, fact)] — facts of yours the team does not have. repo None is the general file."""
	if not team.on():
		return []
	base = os.path.join(config.TEAM, "memory")
	out = []
	for name in sorted(os.listdir(config.MEMORY_DIR)) if os.path.isdir(config.MEMORY_DIR) else []:
		if not name.endswith(".md"):
			continue
		repo = None if name == "general.md" else name[:-3].replace("__", "/")
		theirs = _facts(path(repo, base))
		out += [(repo, f) for f in _facts(path(repo)) if not any(_same(f, t) for t in theirs)]
	return out


def share(repo, fact):
	"""Put one of your facts into the team's memory. Returns the file written."""
	dest = path(repo, os.path.join(config.TEAM, "memory"))
	_append_line(dest, fact)
	_unpool(repo, fact)  # it is memory now; keeping the evidence would just grow forever
	return dest


def _unpool(repo, fact):
	p = pool_path(whoami(), repo)
	kept = [l.rstrip() for l in _read(p).splitlines() if l.strip() and not _same(_parse(l)[1], fact)]
	if kept:
		with open(p, "w") as f:
			f.write("\n".join(kept) + "\n")
	elif os.path.exists(p):
		os.remove(p)


def forget(repo, fact):
	"""Drop one fact from your own memory, and withdraw it as evidence."""
	_unpool(repo, fact)
	p = path(repo)
	kept = [l.rstrip() for l in _read(p).splitlines() if l.strip() and not _same(_parse(l)[1], fact)]
	if kept:
		with open(p, "w") as f:
			f.write("\n".join(kept) + "\n")
	elif os.path.exists(p):
		os.remove(p)


DREAM = """You are tidying the review memory of a code-review bot. Below are its memory files: "mine/" are one
reviewer's private notes, "team/" are shared with their whole team, and each source has a general file plus one
per repo. Rewrite them: merge duplicates, drop contradictions, stale or vague lines, keep every concrete durable
fact, move repo-independent lines to that source's general file. Keep only overarching knowledge: how a repo is
structured and why, conventions, how it affects other repos or the database, which authors own which areas. Drop
per-PR trivia (what one PR changed, one-off bugs, "X is dead after #N") and anything derivable from git history.
Never move a line from mine/ into team/ — sharing is the reviewer's decision, not yours. Keep the "- " bullet
style, one fact per line. Files not listed below must not be invented; return a file with empty content to delete it.

{files}

Respond with ONLY a JSON object, no prose, no code fences:
{{"summary": "<2-5 short lines: what you merged, dropped or moved>", "files": {{"<file name>": "<new content>", ...}}}}"""
TIMEOUT = 600


def files():
	"""{"<source>/<file>": content} for every approved memory file, general first. proposed/ is never included."""
	out = {}
	for label, base in sources():
		key = "mine" if label == "mine" else "team"
		for n in sorted(os.listdir(base)) if os.path.isdir(base) else []:
			if n.endswith(".md"):
				out[f"{key}/{n}"] = open(os.path.join(base, n)).read()
	return dict(sorted(out.items(), key=lambda kv: (not kv[0].endswith("general.md"), kv[0])))


def _base(key):
	"""The directory a files() key belongs to, or "" when it names a source that is not there."""
	where, _, name = key.partition("/")
	if where == "mine":
		return config.MEMORY_DIR
	return os.path.join(config.TEAM, "memory") if where == "team" and team.on() else ""


def dream(model):
	"""Ask Claude to clean up all memory files. Returns (summary, {file key: new content}); raises on failure."""
	import json
	import subprocess
	before = files()
	if not before:
		raise ValueError("no memory to dream about")
	prompt = DREAM.format(files="\n\n".join(f"### {n}\n{t}" for n, t in before.items()))
	# ponytail: --safe-mode for the same reason as a review — this call has a JSON contract, not a conversation
	out = subprocess.run(["claude", "-p", prompt, "--output-format", "json", "--safe-mode", "--model", model]
	                     + (["--effort", config.EFFORT] if config.EFFORT else []),
	                     capture_output=True, text=True, check=True, timeout=TIMEOUT).stdout
	text = json.loads(out)["result"].strip()
	got = json.loads(text[text.index("{"):text.rindex("}") + 1])
	new = {n: str(got["files"].get(n, t)) for n, t in before.items()}  # ponytail: unknown names dropped, missing kept
	return str(got["summary"]), new


def write(new):
	"""Overwrite memory files from a dream(); empty content deletes the file. You approved this, so it lands."""
	for key, t in new.items():
		base = _base(key)
		if not base:
			continue
		p = os.path.join(base, key.partition("/")[2])
		if t.strip():
			os.makedirs(base, exist_ok=True)
			with open(p, "w") as f:
				f.write(t.strip() + "\n")
		elif os.path.exists(p):
			os.remove(p)
