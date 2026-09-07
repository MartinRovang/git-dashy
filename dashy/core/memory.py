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
from . import bind, log, team

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
	_rewrite_counted(self_path(repo), items)
	return fresh


def _consume_self(repo, fact):
	"""Take a matching pre-review finding out of the pool. True when one was there.

	ponytail: removed once spent, so a single pre-review cannot keep contributing to fact after fact.
	"""
	items = self_drafts(repo)
	kept = [(n, t) for n, t in items if not _same(t, fact)]
	if len(kept) == len(items):
		return False
	_rewrite_counted(self_path(repo), kept)
	return True


def _brief_text(p):
	"""One brief file's content, without gitdashy's own marker line.

	ponytail: the marker exists so setup can tell its output from yours. A reviewer has no use for it,
	and everything else in that prompt is there to be read.
	"""
	return "\n".join(l for l in _read(p).splitlines() if l.strip() != SETUP_MARK).strip()


def brief(repo=None, slug=None):
	"""The ONE brief that applies to `repo`, and where it came from: (text, source).

	Two values, deliberately. A caller cannot put a brief in front of a reviewer without also holding
	the answer to "which one, and why that one" — so "you are getting your own, because this repo is
	bound to no team" is something that has to be dropped on purpose rather than a line somebody has to
	remember to add. A line somebody had to remember to add is what put one product's brief into every
	review of every other.

	ponytail: never more than one. This used to return yours AND the team's, concatenated, for every
	repo — two statements of what the work is for in one prompt, which is worse than saying nothing.
	Selection is by binding: declared, visible, and undoable. See core/bind.py for why not the log.
	"""
	mine = _brief_text(brief_path())
	# ponytail: `slug` lets a caller that has ALREADY resolved this repo hand the answer in — the draw
	# path resolves every visible row through one bind.resolver(), and asking again per frame reopens
	# the store for an answer it is holding. None means "look it up", which every other caller wants.
	slug = (bind.of(repo) if repo else "") if slug is None else slug
	if slug:
		d = bind.team_dir(slug)
		if theirs := (_brief_text(os.path.join(d, PROJECT)) if d else ""):
			return theirs, f"team {slug}"
		why = f"team {slug} has no brief" if d else f"not in team {slug}"
	else:
		# ponytail: only worth saying when there IS a brief to explain. With none anywhere, "bound to no
		# team" reads as though binding would produce one, and it would not — "no brief written" is the
		# thing to act on. A bound team we are not in is different: joining it really is the fix.
		why = f"{repo} is bound to no team" if repo and mine else ""
	if not mine:
		return "", why or "no brief written"
	return mine, "yours" + (f" · {why}" if why else "")


def brief_path(slug=""):
	"""Where a brief is written: yours when `slug` is "", else that team's. "" for a team we do not have."""
	if not slug:
		return os.path.join(config.MEMORY_DIR, PROJECT)
	return os.path.join(d, PROJECT) if (d := bind.team_dir(slug)) else ""


def brief_written():
	"""True when a brief exists anywhere — yours, or the team we are in. For "is there anything to ask"."""
	theirs = brief_path(bind.team_key())
	return bool(_brief_text(brief_path()) or (theirs and _brief_text(theirs)))


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
	for label, base in every_source():  # ponytail: a backup copies ALL memory, not one repo's view of it
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


def sources(repo):
	"""(label, dir) for the sources that apply to a review of `repo`, yours first.

	ponytail: SCOPED by the binding, and the argument is required so no caller can get the unscoped set
	by forgetting it. A repo bound to no team is private: it reads your memory alone. Before this, every
	repo on the machine was told how one team conducts reviews — a personal side project included.
	ponytail: `repo` None means "no repo in hand", which is yours alone for the same reason. Reading a
	GENERAL file for a review of repo R still passes R; the scope being read and the repo whose sources
	apply are different questions, and conflating them is how the team's general.md leaked everywhere.
	ponytail: drafts/ is deliberately not a source.
	"""
	out = [("mine", config.MEMORY_DIR)]
	slug = bind.of(repo) if repo else ""  # ponytail: asked once — every bind.of() is a read of the store
	if d := bind.team_dir(slug):
		out.append(("team " + (slug or "shared"), d))
	return out


def every_source():
	"""(label, dir) for every source on this machine, whatever any binding says.

	ponytail: for the readers that must see ALL of memory rather than what applies to one repo — the
	dream tidies every file, and a backup copies every file. Naming them apart from sources() is
	deliberate: giving sources() an "all" flag would make the unscoped set one forgotten argument away,
	and the whole point of the scoping is that it cannot be skipped by accident.
	"""
	return [("mine", config.MEMORY_DIR)] + [(f"team {s}", os.path.join(d, "memory"))
	                                         for s, d in zip(team.joined(), team.dirs())]


def _read(p):
	# ponytail: only a missing file reads as empty. A permission error or a dangling symlink must raise —
	# repoint() makes memory a symlink, so silently reviewing with no memory at all is a real outcome.
	try:
		return open(p).read().strip()
	except FileNotFoundError:
		return ""


def scope_text(scope=None, repo=None):
	"""One scope's memory, from the sources that apply to `repo`, labelled by where each part came from.

	ponytail: two arguments, because they are two questions. `scope` is which FILE (None = the general
	one); `repo` is whose sources apply. A general file read for a review of repo R must still come only
	from the sources bound to R — passing scope as both is exactly how team facts reached every repo.
	"""
	parts = [f"### {label}\n{t}" for label, base in sources(repo) if (t := _read(path(scope, base)))]
	return "\n\n".join(parts)


def read(repo):
	"""General + repo memory from the sources bound to `repo`, as one prompt block. '' when there is none."""
	parts = [f"## {name}\n{t}" for name, r in (("General", None), (repo, repo)) if (t := scope_text(r, repo))]
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


def _the_one_team():
	"""The memory dir for a fact that names no repo. "" when you are in none, or in several.

	ponytail: "" for several on purpose. A general fact is true of every repo a source covers, and with
	two teams that is two different claims; picking one would publish to a team that never asked.
	"""
	got = team.dirs()
	return os.path.join(got[0], "memory") if len(got) == 1 else ""


def pool_path(user, repo):
	"""Your evidence for `repo`, inside the team it is BOUND to. "" when nothing selects one.

	ponytail: evidence is a disclosure, so it goes exactly where the facts go and nowhere else. With
	several teams, publishing to the wrong one is the same error as publishing at all.
	"""
	d = bind.team_dir(bind.of(repo)) if repo else _the_one_team()
	return os.path.join(d, POOL, user, slug(repo)) if d else ""


def logged_repos(where=None):
	"""Repos named in a review log — the team whose log it is can already see these names."""
	out = set()
	try:
		with open(where or log.LOG) as f:
			for line in f:
				try:
					out.add(json.loads(line)["pr"]["repository"]["nameWithOwner"])
				except (ValueError, KeyError, TypeError):
					continue
	except FileNotFoundError:
		pass  # no log yet; anything else is worth raising, or evidence silently stops being published
	return out


def team_visible(repo):
	"""True when this repo belongs to the team, so pooling a fact about it discloses nothing new.

	ponytail: the BINDING, not the shared review log. The log bootstrapped this well enough while it was
	the only rule, but it has no undo — reviewing one PR put a repo in it forever — and it was deciding
	disclosure: whether a fact about your private work is published to other people. That is the last
	place an irreversible side effect belongs. Joining still seeds bindings from the log, so nothing
	stops working; it just becomes something you can see and take back.
	"""
	if not team.joined():
		return False
	if repo is None:
		return bool(_the_one_team())  # names no repo, so no binding selects a team for it
	# ponytail: through team_dir, exactly as every READ resolves it. bool(bind.of(repo)) was true for a
	# binding to ANY team, including one this machine is not in — so a repo bound to org/other had its
	# name and facts written into org/mem's pool and offered for sharing, while sources() and brief()
	# both said it was not ours. One binding meaning "ours" for disclosure and "not ours" for reading is
	# the two-mechanisms-disagree failure this module argues against, in the direction that publishes.
	return bool(bind.team_dir(bind.of(repo)))


def _pool(repo, fact):
	"""Publish a fact you have accepted, as evidence that you did. Never read into any prompt."""
	if team_visible(repo) and (p := pool_path(whoami(), repo)):
		_append_line(p, fact)


def pools():
	"""{user: [(repo, fact)]} across everyone's pool. {} when you are not in a team."""
	out = {}
	for base in team.dirs():  # ponytail: every joined team — corroboration is per fact, not per team
		root = os.path.join(base, "memory", POOL)
		for user in sorted(os.listdir(root)) if os.path.isdir(root) else []:
			d = os.path.join(root, user)
			if not os.path.isdir(d):
				continue
			items = []
			for name in sorted(os.listdir(d)):
				if name.endswith(".md"):
					items += [(_repo_of(name), f) for f in _facts(os.path.join(d, name))]
			if items:
				out.setdefault(user, []).extend(items)
	return out


def backers(index, repo, fact):
	"""Who has accepted this fact, from a pools() index. Two names is two people's reviewers agreeing."""
	return sorted(u for u, items in index.items() if any(r == repo and _same(f, fact) for r, f in items))


def known(repo):
	"""Every approved fact already covering `repo`, across both sources and both scopes."""
	return [f for _, base in sources(repo) for scope in (None, repo) for f in _facts(path(scope, base))]


def already_known(repo, fact):
	"""True when this fact is already approved somewhere that covers `repo`."""
	return any(_same(fact, t) for t in known(repo))


def drafts(repo):
	"""[(count, fact)] for one repo's unconfirmed facts."""
	return [_parse(l) for l in _read(queue_path(repo)).splitlines() if l.strip()]


def _rewrite_counted(p, items):
	"""Replace a counted file — drafts or pre-review findings.

	ponytail: the "- (n) fact" line format was written out at four call sites. One of them drifting
	produces a file the other three cannot read back, and _parse would silently read the whole line as
	the fact with a count of 1.
	"""
	_rewrite(p, "".join(f"- ({n}) {t}\n" for n, t in items))


def _write_drafts(repo, items):
	_rewrite_counted(queue_path(repo), items)  # ponytail: _rewrite reaches _history(); the call here was a second one


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
	if not team.joined():
		return []
	out = []
	for name in sorted(os.listdir(config.MEMORY_DIR)) if os.path.isdir(config.MEMORY_DIR) else []:
		if not name.endswith(".md") or name == PROJECT:
			continue
		repo = _repo_of(name)
		if not team_visible(repo) or not (base := _dest(repo)):
			continue  # ponytail: sharing a fact about a repo the team is not bound to is a disclosure
		theirs = _facts(path(repo, base))
		out += [(repo, f) for f in _facts(path(repo)) if not any(_same(f, t) for t in theirs)]
	return out


def _dest(repo):
	"""The memory dir a fact about `repo` would be shared into. "" when nothing selects one."""
	return (bind.team_dir(bind.of(repo)) if repo else _the_one_team()) or ""


def share(repo, fact):
	"""Put one of your facts into the BOUND team's memory. Returns the file written, or ""."""
	if not (base := _dest(repo)):
		return ""
	dest = path(repo, base)
	_append_line(dest, fact)
	_unpool(repo, fact)  # it is memory now; keeping the evidence would just grow forever
	return dest


def _unpool(repo, fact):
	if not (p := pool_path(whoami(), repo)):
		return
	kept = [l.rstrip() for l in _read(p).splitlines() if l.strip() and not _is(_plain(l), fact)]
	_rewrite(p, "\n".join(kept) + "\n" if kept else "")


def forget(repo, fact):
	"""Drop one fact from your own memory, and withdraw it as evidence."""
	_unpool(repo, fact)
	p = path(repo)
	kept = [l.rstrip() for l in _read(p).splitlines() if l.strip() and not _is(_parse(l)[1], fact)]
	_rewrite(p, "\n".join(kept) + "\n" if kept else "")


def _repo_of(name):
	""""a__b.md" -> "a/b"; "general.md" -> None. The inverse of slug()."""
	return None if name == "general.md" else name[:-3].replace("__", "/")


def waiting():
	"""[(repo, count, fact, kind)] — every observation that is not a fact yet. Newest store last.

	kind is "draft" (a review proposed it; `count` is how many independent reviews have) or "self" (a
	PRE-review of your own PR found it; it holds no count and waits to be consumed by a real review).

	ponytail: reading these is not the thing the invariant forbids. "Never read into a prompt" keeps the
	MODEL from meeting its own guess as evidence and agreeing with itself. A PERSON is not going to
	self-confirm, and this store had no window into it at all — the only way in was `cat`.
	"""
	out = []
	for sub, kind in ((QUEUE, "draft"), (SELF, "self")):
		base = os.path.join(config.MEMORY_DIR, sub)
		for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
			if not name.endswith(".md"):
				continue  # ponytail: drafts/ holds the self/ DIRECTORY too, and listdir returns it
			repo = _repo_of(name)
			for n, fact in (_parse(l) for l in _read(os.path.join(base, name)).splitlines() if l.strip()):
				out.append((repo, n, fact, kind))
	# ponytail: one fact, one row. append_self only checks known(repo) — the settled facts — not the
	# drafts queue, so a review and then a pre-review proposing the same line leaves an entry in BOTH.
	# You would page past the same sentence twice, and t/x removes both, so the list jumps by two.
	# The counted row wins: it is the one carrying how close the fact is, and `self` sorts after it.
	seen, kept = [], []
	for row in sorted(out, key=lambda r: r[3] == "self"):
		if not any(r[0] == row[0] and _same(r[2], row[2]) for r in seen):
			seen.append(row)
			kept.append(row)
	return kept


def drop(repo, fact):
	"""Forget one unconfirmed observation, from whichever queue holds it. True when one went.

	ponytail: the prune the drafts store never had. Everything else self-limits — facts are dropped by
	`forget`, the pool is withdrawn on share or forget — and drafts only ever grew.
	"""
	gone = False
	for p in (queue_path(repo), self_path(repo)):
		items = [_parse(l) for l in _read(p).splitlines() if l.strip()]
		kept = [(n, t) for n, t in items if not _is(t, fact)]
		if len(kept) != len(items):
			gone = True
			_rewrite_counted(p, kept)
	return gone


def promote(repo, fact):
	"""Accept an observation by hand: it becomes one of your facts. Returns the file it landed in.

	ponytail: PROMOTE_AT is a proxy for a judgement you may already have. Recurrence is the right gate
	for something nobody has read — it is the whole reason a model's guess does not become a fact on its
	own — but once a person HAS read the line and knows it is true, requiring a second review to
	rediscover it is asking the machine to re-derive what you can already see. This is the only path
	into your memory that is not recurrence, and it takes a person and a keypress.
	ponytail: pooled like any promotion, so the evidence trail says the same thing either way. Bound
	repos only — _pool checks team_visible.
	"""
	drop(repo, fact)
	if not already_known(repo, fact):
		_append_line(path(repo), fact)
		_pool(repo, fact)
	return path(repo)


DREAM = """You are tidying the review memory of a code-review bot. Below are its memory files: "mine/" are one
reviewer's private notes, "team:<key>/" are shared with one of their teams (there may be several, and they
are different groups of people), and each source has a general file plus one per repo. Rewrite them: merge duplicates, drop contradictions, stale or vague lines, keep every concrete durable
fact, move repo-independent lines to that source's general file. Keep only overarching knowledge: how a repo is
structured and why, conventions, how it affects other repos or the database, which authors own which areas, and —
in a general file — how reviews are conducted here at all: what blocks and what does not, what must be verified
rather than assumed, which classes of change get extra scrutiny. Drop per-PR trivia (what one PR changed, one-off
bugs, "X is dead after #N") and anything derivable from git history.
A general file is EXPECTED to hold lines that name no repo. That is what it is for, not a sign they are stale.
Never move a line from mine/ into a team/, or between two teams — sharing is the reviewer's decision, not yours. Keep the "- " bullet
style, one fact per line. Files not listed below must not be invented.
Returning a file with empty content DELETES it and everything in it. Do that only when every line in it is
genuinely worthless — never merely because the file does not match a category above.

{files}

Respond with ONLY a JSON object, no prose, no code fences. Every key must be a file name exactly as
listed above, including its "mine/" or "team:<key>/" prefix — a key without one names no file and is ignored:
{{"summary": "<2-5 short lines: what you merged, dropped or moved>",
 "files": {{"mine/general.md": "<new content>", "team:<key>/<owner>__<repo>.md": "<new content>", ...}}}}"""
TIMEOUT = 600


def files():
	"""{"<source>/<file>": content} for every approved memory file, general first. proposed/ is never included."""
	out = {}
	for label, base in every_source():  # ponytail: the dream tidies ALL memory, not one repo's view of it
		# ponytail: the SLUG is in the key. With one team "team/" was unambiguous; with several, two
		# teams' general.md would collide on one key and the dream would write one over the other.
		key = "mine" if label == "mine" else "team:" + label[5:]
		for n in sorted(os.listdir(base)) if os.path.isdir(base) else []:
			if n.endswith(".md") and n != PROJECT:  # the dream tidies learned facts, not a stated brief
				out[f"{key}/{n}"] = open(os.path.join(base, n)).read()
	return dict(sorted(out.items(), key=lambda kv: (not kv[0].endswith("general.md"), kv[0])))


def _base(key):
	"""The directory a files() key belongs to, or "" when it names a source that is not there."""
	# ponytail: "mine/x.md" or "team:<key>/x.md". key_of strips everything outside [a-z0-9-], so a key
	# never contains a slash — but split from the RIGHT anyway, so the file name is the last component
	# whatever the source is. An unknown source resolves to "" and is dropped, as before.
	where, _, name = key.rpartition("/")
	if where == "mine":
		return config.MEMORY_DIR
	return bind.team_dir(where[5:]) if where.startswith("team:") else ""


def dream(model):
	"""Ask Claude to tidy every memory file. Returns (summary, before, after); raises on failure.

	ponytail: `before` comes back with the result rather than being re-read afterwards. A review can
	promote a fact during the ten minutes this may take, and re-reading would then diff against a file
	the model never saw — showing wrong line counts and, on accept, overwriting the new fact.
	"""
	import json
	from . import llm
	before = files()
	if not before:
		raise ValueError("no memory to dream about")
	prompt = DREAM.format(files="\n\n".join(f"### {n}\n{t}" for n, t in before.items()))
	text = llm.ask(prompt, model, timeout=TIMEOUT)[0]  # ponytail: no tools and no system prompt — the files are in the prompt
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
