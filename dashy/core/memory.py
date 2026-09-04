"""Review memory. Two sources — your own and the team's — read together, written apart.

A fact the model proposes is not a fact yet: it is a draft, and it becomes yours only once independent
reviews land on it again. That promotion is automatic, because being wrong there costs only you. Reaching
the team's memory is never automatic — that lands in contexts where nobody who could correct it will see
it happen — so it takes one keypress from you.
"""
import difflib
import hashlib
import json
import os
import re
import tarfile
import time

from .. import config
from . import log, team

QUEUE = "drafts"  # under your own memory dir: unconfirmed facts, and how often each has recurred
POOL = "pool"  # under the team's memory: facts each person has accepted, as evidence only, never read
SETUP_MARK = "<!-- written by gitdashy setup -->"  # install.SETUP_MARK; here to avoid importing it
SELF = os.path.join(QUEUE, "self")  # drafts/self/<repo>.md — what a PRE-review of your own PR proposed
PROJECT = "project.md"  # the team's DECLARED context: what we are building. Written by people, never learned.
PROMOTE_AT = 2  # independent reviews that must land on a fact before it becomes one of yours
NEAR = 0.88  # difflib ratio over TOKENS above which two wordings are the same fact; see _toks


def slug(repo):
	return (repo.replace("/", "__") if repo else "general") + ".md"


def path(repo=None, base=None):
	return os.path.join(base or config.MEMORY_DIR, slug(repo))


def queue_path(repo):
	return os.path.join(config.MEMORY_DIR, QUEUE, slug(repo))


def self_path(repo):
	"""Where a pre-review's findings wait. Under drafts/, so nothing that reads facts can reach them."""
	return os.path.join(config.MEMORY_DIR, SELF, slug(repo))


def self_drafts(repo):
	"""[(count, fact)] a pre-review of your own PR proposed and no real review has confirmed."""
	return [_parse(l) for l in _read(self_path(repo)).splitlines() if l.strip()]


def append_self(repo, text):
	"""Record what a PRE-review found. Never promotes on its own; returns what was kept.

	ponytail: a pre-review and the real review are the same model on the same diff, so counting them as
	two independent observations would make PROMOTE_AT measure one opinion twice. These wait instead.
	A later REAL review that lands on the same fact by itself consumes the entry and contributes its +1
	— two runs, one of which had no idea the other existed, which is the bar the whole gate is for.
	ponytail: under drafts/, and never read into any prompt. Same rule, same reason.
	"""
	proposed = [l.strip().lstrip("-• ").strip() for l in (text or "").splitlines() if l.strip()]
	fresh, settled = [], known(repo)
	for fact in proposed:
		if not any(_same(fact, t) for t in fresh) and not any(_same(fact, t) for t in settled):
			fresh.append(fact)
	if not fresh:
		return []
	items = self_drafts(repo)
	for fact in fresh:
		if not any(_same(t, fact) for _n, t in items):
			items.append((1, fact))  # ponytail: no count here. One pre-review, or ten, is still one opinion.
	_history()
	_rewrite(self_path(repo), "".join(f"- ({n}) {t}\n" for n, t in items))
	return fresh


def _consume_self(repo, fact):
	"""Take a matching pre-review finding out of the pool. True when one was there.

	ponytail: removed once spent, so a single pre-review cannot keep contributing to fact after fact.
	"""
	items = self_drafts(repo)
	kept = [(n, t) for n, t in items if not _same(t, fact)]
	if len(kept) == len(items):
		return False
	_rewrite(self_path(repo), "".join(f"- ({n}) {t}\n" for n, t in kept))
	return True


def project():
	"""What the work is for — yours, then the team's. "" when neither has been written.

	ponytail: two sources, like everything else. It used to be the team's alone, which left anyone
	working on their own with nowhere to put it — and a USER.md template pointing them at a file that
	could not exist.

	ponytail: declared, not learned. Nothing in the promotion pipeline may touch it: it is not a fact
	someone's reviewer noticed twice, it is what somebody says the work is for. So it is excluded from
	the dream, from sharing, and from ever being read as a repo's facts.
	"""
	# ponytail: strip gitdashy's own marker line. It exists so setup can tell its output from yours;
	# a reviewer has no use for it, and everything else in this prompt is there to be read.
	parts = [f"### {label}\n{t}" for label, base in sources()
	         if (t := "\n".join(l for l in _read(os.path.join(base, PROJECT)).splitlines()
	                             if l.strip() != SETUP_MARK).strip())]
	return "\n\n".join(parts)


BACKUPS = os.path.expanduser("~/.prs_backups")  # ponytail: outside every synced tree, so it is never pushed
KEEP_BACKUPS = 30


def _rewrite(p, text):
	"""Replace one memory file, or delete it when nothing is left. Every rewrite goes through here.

	ponytail: _history() used to hang off three named callers, so `forget` and the pool rewrite — which
	do not use any of them — wrote with no history behind them, and the docs said otherwise. Enumerating
	callers is what produced that gap and would produce the next one; the call belongs on the write.
	"""
	_history()
	if text:
		os.makedirs(os.path.dirname(p), exist_ok=True)
		with open(p, "w") as f:
			f.write(text)
	elif os.path.exists(p):
		os.remove(p)


def history():
	"""Start tracking the memory dir now, committing what is there. For writers we do not control."""
	_history()


def _history():
	"""Give the memory dir git history the first time anything writes to it. Cheap when it already has.

	ponytail: skipped in demo mode, which shells out to nothing by design — the demo writes to a throwaway
	memory dir and a `git init` there would be a subprocess the demo promises never to run.
	"""
	if config.MEMORY_DIR and config.SETTINGS:
		team.init_history(config.MEMORY_DIR)


def _everything():
	"""[(arcname, path)] for every file worth keeping a copy of — facts, drafts, both sources."""
	out = []
	for label, base in [("mine", config.MEMORY_DIR)] + ([("team", os.path.join(config.TEAM, "memory"))]
	                                                    if team.on() else []):
		if not base:
			continue  # ponytail: PRS_MEMORY= (set but empty) once made os.walk(".") tar up the cwd
		for root, dirs, names in os.walk(base):
			dirs[:] = [d for d in dirs if d != ".git"]  # ponytail: history, not content; and it is huge
			for n in sorted(names):
				if n.endswith(".md"):
					full = os.path.join(root, n)
					out.append((os.path.join(label, os.path.relpath(full, base)), full))
	return sorted(out)


def backup(reason="tick"):
	"""Keep a compressed copy of every memory file. Returns the archive path, or "" when nothing changed.

	ponytail: memory is the one thing in this system that cannot be recreated — a review can be run
	again, a mirror is derived, a clone can be re-cloned. Markdown is tiny and gzip flattens it, so the
	cost of keeping thirty of these is nothing next to the cost of losing one file once.
	ponytail: skipped when the content hashes the same as the newest archive, or a refresh tick would
	write an identical tarball every minute forever and push the real ones out of the window.
	ponytail: never raises. It runs on the refresh tick and before a dream — a backup that fails must
	not be the thing that stops either of them.
	"""
	try:
		files = _everything() if config.SETTINGS else []  # ponytail: demo memory is throwaway by design
		if not files:
			return ""
		h = hashlib.sha256()
		for arc, full in files:
			h.update(arc.encode() + b"\0")
			with open(full, "rb") as f:
				h.update(f.read() + b"\0")
		digest = h.hexdigest()[:12]
		os.makedirs(BACKUPS, exist_ok=True)
		have = sorted(n for n in os.listdir(BACKUPS) if n.endswith(".tar.gz"))
		if have and have[-1].endswith(f"-{digest}.tar.gz"):
			return ""  # ponytail: identical to the newest one; keeping it twice buys nothing
		name = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{reason}-{digest}.tar.gz"
		dest = os.path.join(BACKUPS, name)
		with tarfile.open(dest + ".part", "w:gz") as t:
			for arc, full in files:
				t.add(full, arcname=arc)
		os.replace(dest + ".part", dest)  # ponytail: named only once complete, so a half-write is never found
		for old in sorted(n for n in os.listdir(BACKUPS) if n.endswith(".tar.gz"))[:-KEEP_BACKUPS]:
			os.remove(os.path.join(BACKUPS, old))
		return dest
	except (OSError, tarfile.TarError):
		try:
			os.remove(dest + ".part")  # ponytail: prune only sees .tar.gz, so a stray .part stays forever
		except (OSError, NameError, UnboundLocalError):
			pass
		return ""


def sources():
	"""(label, dir) for each approved source, yours first. ponytail: drafts/ is deliberately not one."""
	out = [("mine", config.MEMORY_DIR)]
	if team.on():
		out.append(("team " + (team.NAME or "shared"), os.path.join(config.TEAM, "memory")))
	return out


def _read(p):
	# ponytail: only a missing file reads as empty. A permission error or a dangling symlink must raise —
	# repoint() makes memory a symlink, so silently reviewing with no memory at all is a real outcome.
	try:
		return open(p).read().strip()
	except FileNotFoundError:
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


def _toks(s):
	"""Words, lowercased, punctuation dropped.

	ponytail: compare tokens, not characters. These lines are short, so one wrong word leaves a character
	ratio high enough to pass — "format-check" against "type-check" scored 0.886, above any threshold
	loose enough to still match a real rewording. On tokens the two populations separate: measured
	rewordings bottom out at 0.889, different facts top out at 0.857. The margin is thin and this still
	wants a number from real use.
	"""
	words = re.sub(r"[^\w/.\-]+", " ", s.lower().replace("`", "")).split()
	return [w for w in (t.strip(".,;:") for t in words) if w]


def _same(a, b):
	return difflib.SequenceMatcher(None, _toks(a), _toks(b)).ratio() >= NEAR


def _is(a, b):
	"""The same line, ignoring spacing. ponytail: removal must be exact — _same would take a neighbour."""
	return _norm(a) == _norm(b)


def _plain(line):
	""""- a fact" -> "a fact". No counter is read here: only a drafts file has one."""
	return line.strip().lstrip("-• ").strip()


def _parse(line):
	""""- (2) a fact" -> (2, "a fact"). A line with no counter has been seen once.

	ponytail: for drafts only. A confirmed fact may legitimately begin "(2) ..." — "- (2) space indexes
	are 1-based" would otherwise read back without its first two characters, quietly changing what it says.
	"""
	m = re.match(r"^-\s*\((\d+)\)\s*(.*)$", line.strip())
	return (int(m.group(1)), m.group(2).strip()) if m else (1, _plain(line))


def _facts(p):
	"""Facts from a confirmed file or a pool file — never a drafts file, so never a counter."""
	return [_plain(l) for l in _read(p).splitlines() if l.strip()]


def _append_line(p, fact):
	_history()
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
	except FileNotFoundError:
		pass  # no log yet; anything else is worth raising, or evidence silently stops being published
	return out


def team_visible(repo):
	"""True when the team can already see this repo's name, so pooling a fact about it discloses nothing.

	ponytail: the shared review log is what bootstraps this — it fills as you review, where "repos the
	team already has memory for" would have started empty and never filled. But team memory counts too,
	or a repo you only ever code in could never corroborate, despite being just as plainly theirs.
	"""
	if not team.on():
		return False
	if repo is None:
		return True  # a general fact names no repo, so there is nothing to disclose
	return os.path.exists(path(repo, os.path.join(config.TEAM, "memory"))) or repo in logged_repos()


def project_path(mine=True):
	"""Where a project brief is written: yours, or the team's."""
	return os.path.join(config.MEMORY_DIR if mine else os.path.join(config.TEAM, "memory"), PROJECT)


def _pool(repo, fact):
	"""Publish a fact you have accepted, as evidence that you did. Never read into any prompt."""
	if team_visible(repo):
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
	_history()
	p = queue_path(repo)
	_rewrite(p, "".join(f"- ({n}) {t}\n" for n, t in items))


def append(repo, text):
	"""Record what a review proposed; return the facts that just became yours.

	ponytail: drafts are NEVER read back into a prompt. If they were, the reviewer would meet its own
	earlier guess as evidence and agree with itself — the count has to come from rediscovery, not recall.
	That is the whole difference between measuring durability and keeping a tally.
	"""
	proposed = [l.strip().lstrip("-• ").strip() for l in (text or "").splitlines() if l.strip()]
	if not proposed:
		return []
	# ponytail: one review contributes at most +1 to a fact. Without this, a reviewer that words the same
	# thing twice in one call clears the gate by itself — and pools the result as corroborated evidence.
	fresh = []
	for fact in proposed:
		if not any(_same(fact, t) for t in fresh):
			fresh.append(fact)
	items, settled = drafts(repo), known(repo)
	for fact in fresh:
		if any(_same(fact, t) for t in settled):
			continue  # already approved somewhere: proposing it again says nothing new
		# ponytail: a pre-review of your own PR that found this counts as the other observation — two runs,
		# one of which did not know the other existed. Consumed, so one pre-review cannot keep paying out.
		bonus = 1 if _consume_self(repo, fact) else 0
		for i, (n, t) in enumerate(items):
			if _same(t, fact):
				items[i] = (n + 1 + bonus, t)  # the first wording wins; the count is what carries meaning
				break
		else:
			items.append((1 + bonus, fact))
	promoted = [t for n, t in items if n >= PROMOTE_AT]
	# ponytail: facts first, drafts after. The other order loses the observation outright if the second
	# write fails; this one costs a duplicate draft on a crash, which the next round collapses anyway.
	for t in promoted:
		_append_line(path(repo), t)
		_pool(repo, t)
	_write_drafts(repo, [(n, t) for n, t in items if n < PROMOTE_AT])
	return promoted


def shareable():
	"""[(repo, fact)] — facts of yours the team does not have. repo None is the general file."""
	if not team.on():
		return []
	base = os.path.join(config.TEAM, "memory")
	out = []
	for name in sorted(os.listdir(config.MEMORY_DIR)) if os.path.isdir(config.MEMORY_DIR) else []:
		if not name.endswith(".md") or name == PROJECT:
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
	kept = [l.rstrip() for l in _read(p).splitlines() if l.strip() and not _is(_plain(l), fact)]
	_rewrite(p, "\n".join(kept) + "\n" if kept else "")


def forget(repo, fact):
	"""Drop one fact from your own memory, and withdraw it as evidence."""
	_unpool(repo, fact)
	p = path(repo)
	kept = [l.rstrip() for l in _read(p).splitlines() if l.strip() and not _is(_parse(l)[1], fact)]
	_rewrite(p, "\n".join(kept) + "\n" if kept else "")


DREAM = """You are tidying the review memory of a code-review bot. Below are its memory files: "mine/" are one
reviewer's private notes, "team/" are shared with their whole team, and each source has a general file plus one
per repo. Rewrite them: merge duplicates, drop contradictions, stale or vague lines, keep every concrete durable
fact, move repo-independent lines to that source's general file. Keep only overarching knowledge: how a repo is
structured and why, conventions, how it affects other repos or the database, which authors own which areas. Drop
per-PR trivia (what one PR changed, one-off bugs, "X is dead after #N") and anything derivable from git history.
Never move a line from mine/ into team/ — sharing is the reviewer's decision, not yours. Keep the "- " bullet
style, one fact per line. Files not listed below must not be invented; return a file with empty content to delete it.

{files}

Respond with ONLY a JSON object, no prose, no code fences. Every key must be a file name exactly as
listed above, including its "mine/" or "team/" prefix — a key without one names no file and is ignored:
{{"summary": "<2-5 short lines: what you merged, dropped or moved>",
 "files": {{"mine/general.md": "<new content>", "team/<owner>__<repo>.md": "<new content>", ...}}}}"""
TIMEOUT = 600


def files():
	"""{"<source>/<file>": content} for every approved memory file, general first. proposed/ is never included."""
	out = {}
	for label, base in sources():
		key = "mine" if label == "mine" else "team"
		for n in sorted(os.listdir(base)) if os.path.isdir(base) else []:
			if n.endswith(".md") and n != PROJECT:  # the dream tidies learned facts, not a stated brief
				out[f"{key}/{n}"] = open(os.path.join(base, n)).read()
	return dict(sorted(out.items(), key=lambda kv: (not kv[0].endswith("general.md"), kv[0])))


def _base(key):
	"""The directory a files() key belongs to, or "" when it names a source that is not there."""
	where, _, name = key.partition("/")
	if where == "mine":
		return config.MEMORY_DIR
	return os.path.join(config.TEAM, "memory") if where == "team" and team.on() else ""


def dream(model):
	"""Ask Claude to tidy every memory file. Returns (summary, before, after); raises on failure.

	ponytail: `before` comes back with the result rather than being re-read afterwards. A review can
	promote a fact during the ten minutes this may take, and re-reading would then diff against a file
	the model never saw — showing wrong line counts and, on accept, overwriting the new fact.
	"""
	import json
	import subprocess
	import tempfile
	before = files()
	if not before:
		raise ValueError("no memory to dream about")
	prompt = DREAM.format(files="\n\n".join(f"### {n}\n{t}" for n, t in before.items()))
	# ponytail: --safe-mode for the same reason as a review — this call has a JSON contract, not a conversation
	with tempfile.TemporaryDirectory() as here:  # ponytail: same reason as a review — see review.review
		out = subprocess.run(["claude", "-p", prompt, "--output-format", "json", "--safe-mode", "--model", model]
		                     + (["--effort", config.EFFORT] if config.EFFORT else []),
		                     capture_output=True, text=True, check=True, timeout=TIMEOUT, cwd=here).stdout
	text = json.loads(out)["result"].strip()
	got = json.loads(text[text.index("{"):text.rindex("}") + 1])
	sent = got.get("files") or {}
	new = {n: str(sent.get(n, t)) for n, t in before.items()}  # a name we did not list keeps what it had
	# ponytail: say when the model answered with names we never sent. Those edits are dropped, and a
	# silent drop after you press y looks exactly like a dream that decided to change nothing.
	stray = sorted(k for k in sent if k not in before)
	summary = str(got["summary"]) + ("\n\n(ignored " + ", ".join(stray) + " — not files I sent)" if stray else "")
	return summary, before, new


def write(new):
	"""Overwrite memory files from a dream(); empty content deletes the file. You approved this, so it lands.

	ponytail: a keypress here rewrites every file and DELETES any the model returned empty. Both nets go
	down first — a compressed copy outside every synced tree, and a commit — so "you approved this" means
	a decision you can walk back, not one that is final because a model was confident.
	"""
	_history()
	backup("dream")
	for key, t in new.items():
		base = _base(key)
		if not base:
			continue
		p = os.path.join(base, os.path.basename(key.partition("/")[2]))  # ponytail: a name, never a path
		_rewrite(p, t.strip() + "\n" if t.strip() else "")
