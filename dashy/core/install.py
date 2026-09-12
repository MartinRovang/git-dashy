"""Wiring the knowledge system into a machine, and into a repo.

ponytail: reviews read and write memory with no setup at all — that half needs no installing. What needs
installing is the half that reaches a regular coding session, which until now lived in one person's private
agent corpus, and so was unreachable for everyone else.
"""
import json
import os
import shlex
import shutil
import subprocess

from .. import HERE, config
from . import knowledge, memory, mirror, team

BEGIN, END = "<!-- gitdashy:begin -->", "<!-- gitdashy:end -->"
CBEGIN, CEND = "<!-- gitdashy:corpus:begin -->", "<!-- gitdashy:corpus:end -->"  # a separate block: one can go without the other
CORPUS_HOME = os.path.expanduser("~/.agent-corpus")  # where an installed corpus lives, independent of gitdashy
IMPORT = "@prs-memory/general.md"  # the line that says the wiring is already there, block or not
REGISTRY = os.path.expanduser("~/.prs_mirrors")  # one JSON object per line; the filesystem keeps the setting
STALE = "@prs-team/"  # the pre-2026-09-08 block imported one team globally; its presence means "rewrite"
BLOCK = f"""{BEGIN}
# Review memory

Cross-repo facts gitdashy's PR reviews have earned — yours. Written only once two independent
observations agreed, so trust them, but they are what the code turned out to be, not rules. The brief
(what the work is for) and a team's facts arrive per repo, through that repo's own mirror: which brief
and which team apply is a property of the repo you are in, not of the machine, and a review of that
repo is told exactly the same ones.

{IMPORT}
{END}
"""


def claude_dir():
	return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def links():
	"""(link, target) for the one path a session reads memory through.

	ponytail: ONE link now. There used to be a second, `prs-team`, pointing at one team's memory so its
	general facts loaded into every session on the machine. That was the wrong scope the moment there
	were two teams and a dangling link once there were — and even with one it told a repo bound to
	another team, or to none, how that team works. Team knowledge rides the per-repo mirror, through
	the binding, like everything else that is the team's. See stale_team_link() for the retirement.
	"""
	return [(os.path.join(claude_dir(), "prs-memory"), config.LOCAL_MEMORY)]


def stale_team_link():
	"""The retired `prs-team` symlink, if this machine still has one and it is ours. "" otherwise.

	ponytail: ours means it points into the team store — the plural one, or the pre-plural checkout —
	which is the only place install ever pointed it. Anything else there is someone's own and is left alone.
	"""
	link = os.path.join(claude_dir(), "prs-team")
	if not os.path.islink(link):
		return ""
	target = os.path.abspath(os.path.join(os.path.dirname(link), os.readlink(link)))
	# ponytail: an EMPTY store root means "no store configured", NOT "everywhere". abspath("") is the
	# CURRENT WORKING DIRECTORY, so a blank PRS_TEAMS/PRS_TEAM — which is exactly what `--demo` sets —
	# made every link under cwd count as ours, and retire() deletes what it matches. Launch the demo from
	# $HOME with a hand-made ~/.claude/prs-team -> ~/work/notes and the link is gone. A relative root is
	# refused for the same reason: it is only meaningful against a cwd this has no business trusting.
	roots = (config.TEAMS, os.path.join(config.TEAM, "memory") if config.TEAM else "")
	stores = [os.path.abspath(r) for r in roots if r and os.path.isabs(r)]
	return link if any(target == st or target.startswith(st + os.sep) for st in stores) else ""


def retire(dry=False):
	"""Retire the pre-2026-09-08 team link and the imports that went through it. [] when nothing to do.

	ponytail: one callable, because it runs from two places — `gitdashy install`, and every LAUNCH,
	the way team.migrate() moves a pre-plural checkout. Nothing re-runs install after an update, so a
	migration that waits for it waits forever on most machines; meanwhile a one-team user had the
	team's general facts in context twice, and leaking into repos bound to no team.
	ponytail: it rewrites only text between markers it wrote. A hand-wired import is REPORTED, never
	touched — and reported here rather than skipped, because the link it went through is gone and the
	loader drops a dangling @import without a word. A retirement that leaves one behind half happened.
	ponytail: NEVER RAISES, the same contract team.migrate() states one line above it in the launch
	path — it runs before the first draw, so an exception here is a dashboard that never appears, every
	launch, over a migration the user never asked for. A read-only ~/.claude, a CLAUDE.md whose realpath
	is in a dotfiles checkout, a root-owned file: each of those is a report line, not a traceback. The
	failure is SAID rather than swallowed, because a migration that silently did not happen is one the
	user finds out about when their session stops loading the team's facts.
	"""
	out, did = [], "would " if dry else ""
	if old := stale_team_link():
		out.append(f"{did}retire {knowledge.tilde(old)} — a team's facts AND its project brief reach a "
		           "session through its repo's mirror now, so a repo with no mirror gets neither: "
		           "`gitdashy init --into .agent/team --loader CLAUDE.local.md` wires one")
		if not dry:
			try:
				os.remove(old)
			except OSError as e:
				out[-1] = f"gitdashy: could not retire {knowledge.tilde(old)} — {e.strerror or e}; remove it by hand"
	md = os.path.join(claude_dir(), "CLAUDE.md")
	text = _read(md)
	# ponytail: fence-aware on BOTH sides, like _strip_blocks. A raw `STALE in text` took this branch for
	# a CLAUDE.md that merely QUOTED the old block in a code sample — docs/install.md shows exactly that
	# — stripped nothing, and appended BLOCK again on every run, never reaching "ok".
	if STALE in _inside_blocks(text):
		out.append(f"{did}update the import block in {knowledge.tilde(md)} — the team imports are per repo now")
		if not dry:
			try:
				_write_text(md, _strip_blocks(text, BEGIN, END).rstrip("\n") + "\n\n" + BLOCK)
			except OSError as e:
				out[-1] = (f"gitdashy: could not update the import block in {knowledge.tilde(md)} — "
				           f"{e.strerror or e}; the team imports are per repo now")
	if hand_wired_team_import(text):
		out.append(f"NOTE  {knowledge.tilde(md)} still imports {STALE}… outside a block we wrote — "
		           "those lines point at nothing now; remove them by hand")
	return out


def _inside_blocks(text):
	"""The lines inside every begin..end block we wrote, fence-aware — a quoted block is text, not ours."""
	return _split_blocks(text, BEGIN, END)[0]


def hand_wired_team_import(text=None):
	"""True while CLAUDE.md imports @prs-team/ outside a block we wrote and outside a fence."""
	text = _read(os.path.join(claude_dir(), "CLAUDE.md")) if text is None else text
	return any(STALE in l for l, out in _outside(_strip_blocks(text, BEGIN, END)) if out)


def corpus_remembers(ident=None):
	"""Whether the installed identity ever tells a session to `gitdashy remember`. None when there is none.

	ponytail: a heuristic, and it says what it looked for. gitdashy cannot write a user's corpus, and
	the one this tool was built next to lacked the instruction for months while every draft sat at (1)
	— the pipeline's second observer was silent and nothing said so. A check that reports beats a
	sentence in a README the reader is assumed to have followed.
	"""
	ident = ident or os.path.join(claude_dir(), "identity")
	if not os.path.isdir(ident):
		return None
	# ponytail: listdir raises on a directory it cannot read, and this is reached from every draw
	# through session_notes(). isdir() above answers "is it there", never "can it be opened".
	try:
		names = [n for n in os.listdir(ident) if n.endswith(".md")]
	except OSError:
		return None  # unreadable is not "a corpus without the instruction" — it is nothing we can say
	return any("gitdashy remember" in _read(os.path.join(ident, n)) for n in names)


_NOTES = (None, [])  # (fingerprint, notes) — see session_notes


def _notes_key():
	"""A stat-level fingerprint of every file session_notes() would read.

	ponytail: (mtime_ns, size) per file, the same key log._CACHE uses — mtime alone re-reads on a
	same-nanosecond rewrite, size alone misses an edit that keeps the length. The DIRECTORY's own mtime
	is not enough: it moves when a file is added or removed, not when one is edited in place.
	"""
	def stamp(path):
		"""(mtime_ns, size), or why not. ponytail: the miss is part of the KEY — "gone" and "back again"
		have to differ, or a file deleted and restored between draws reads as no change at all."""
		try:
			st = os.stat(path)
			return st.st_mtime_ns, st.st_size
		except OSError:
			return None

	d = claude_dir()
	ident = os.path.join(d, "identity")
	try:
		names = sorted(n for n in os.listdir(ident) if n.endswith(".md"))
	except OSError:
		names = []
	return (d, tuple((n, stamp(os.path.join(ident, n))) for n in names), stamp(os.path.join(d, "CLAUDE.md")))


def session_notes():
	"""Short standing notes for the Knowledge row: what a session here is NOT being told, and why.

	ponytail: CACHED on a stat-level key, because header_groups() calls this and draw() calls that on
	every tick — 50ms while anything spins. Uncached it read every identity/*.md in full, read the whole
	CLAUDE.md, and ran _strip_blocks() over it, twice a second for the life of the process. The values
	beside it on that row cost one stat each; this now costs the same order, and still notices an edit.
	"""
	global _NOTES
	if _NOTES[0] != (key := _notes_key()):
		out = []
		if corpus_remembers() is False:
			out.append("corpus never says `gitdashy remember`")
		if hand_wired_team_import():
			out.append("CLAUDE.md imports @prs-team by hand")
		# ponytail: the TEAM gates are not here any more. They started as notes because a withheld
		# agents.md was invisible, and a note is the wrong shape for them: it states a problem the
		# reader cannot act on from where they are reading it. memory.pending_answers() drives a row
		# of its own on the same group, and ⏎ on that row asks the question again. What is left here
		# is what it says on the docstring — things about THIS MACHINE's wiring, which no keypress in
		# the dashboard can fix.
		_NOTES = (key, out)
	return _NOTES[1]


def _fence(line, open_at):
	"""Track a fenced code block across one line. Returns the new state: None, or (char, width).

	ponytail: three loops each toggled on "```" alone. "~~~" was invisible to all of them, and any
	divergence between reader and writer corrupts text — which is how the escaping bug got in. One
	function, so a fence means exactly one thing everywhere it matters.
	ponytail: a closing fence is the same character, at least as wide, and carries nothing after it.
	An info string ("```python") can only open, so a fenced block quoting one is not closed by it.
	"""
	bare = line.lstrip()
	for ch in ("`", "~"):
		if bare.startswith(ch * 3):
			width = len(bare) - len(bare.lstrip(ch))
			if open_at is None:
				return (ch, width)
			if open_at[0] == ch and width >= open_at[1] and not bare[width:].strip():
				return None
	return open_at


def _outside(text):
	"""(line, is_outside_a_fence) for every line, so markers inside a code block stay text."""
	at = None
	for line in text.splitlines():
		was = at
		at = _fence(line, at)
		yield line, was is None and at is None


def _split_blocks(text, begin, end):
	"""(inside, outside): the lines within every begin..end block we wrote, and the text without them.

	ponytail: ONE walk answers both, and that is not only a saving. They used to be two functions with
	two different ideas of an UNCLOSED block: _inside_blocks treated everything after a lone `begin` as
	ours, while this loop treats it as not ours and leaves it alone. retire() asked the first and acted
	with the second, so a CLAUDE.md holding an unclosed marker over an `@prs-team/` line reported
	"ours", stripped nothing, and appended BLOCK — and on the NEXT launch the strip ran from the user's
	own unclosed marker to the appended END and deleted everything in between. Answering both questions
	from one walk is what makes that disagreement unrepresentable.

	ponytail: a config copied between machines, or two installs racing, leaves the block twice —
	and removing one of two is worse than removing none, because it reads as a clean uninstall.
	ponytail: markers inside a fence are text. CLAUDE.md is the user's own file, and quoting our
	install block in a code sample is a normal thing to do — eating it on uninstall is data loss
	in the same file this whole path exists to protect.
	"""
	held = []
	while True:
		lines = list(_outside(text))
		at = next((i for i, (l, out) in enumerate(lines) if out and begin in l), None)
		if at is None:
			break
		close = next((i for i, (l, out) in enumerate(lines) if i >= at and out and end in l), None)
		if close is None:
			break  # ponytail: an unclosed marker is NOT a block of ours — nothing held, nothing stripped
		held += [l for l, _ in lines[at + 1:close]]
		head = "\n".join(l for l, _ in lines[:at])
		tail = "\n".join(l for l, _ in lines[close + 1:])
		# ponytail: splitlines() drops the terminator, so rejoining a file that ended in a newline gave
		# it back without one. It is a user-owned file; leave it shaped the way they had it.
		body = head.rstrip("\n") + ("\n" if head.strip() else "") + tail.lstrip("\n").rstrip("\n")
		text = body + "\n" if text.endswith("\n") and not body.endswith("\n") else body
	return "\n".join(held), text


def _strip_blocks(text, begin, end):
	"""Every begin..end block gone, not just the first.

	ponytail: a config copied between machines, or two installs racing, leaves the block twice —
	and removing one of two is worse than removing none, because it reads as a clean uninstall.
	ponytail: markers inside a fence are text. CLAUDE.md is the user's own file, and quoting our
	install block in a code sample is a normal thing to do — eating it on uninstall is data loss
	in the same file this whole path exists to protect.
	"""
	return _split_blocks(text, begin, end)[1]


def _read(p):
	"""A file's text, or "" for any reason it cannot be read.

	ponytail: OSError, not FileNotFoundError. "Not there" and "there but unreadable" are the same answer
	to every one of this module's twenty callers — none of them can do anything with the difference —
	and the narrow catch made an existing-but-unreadable file raise instead. That reached two places
	that must never raise: retire(), which runs before the first draw, and session_notes(), which
	row() calls on EVERY draw. One `sudo claude` leaves a root-owned ~/.claude/CLAUDE.md and the
	dashboard is gone, every tick, until someone chowns it back.
	ponytail: reading is the only thing widened. A WRITE that fails still reports — see retire(), where
	each write says what it could not do. Silence is right for a read and wrong for a write.
	"""
	try:
		return open(p).read()
	except OSError:
		return ""


def _unwrite(path, before):
	"""Put a file back the way it was — gone, if it was not there."""
	if before:
		with open(path, "w") as f:
			f.write(before)
	else:
		try:
			os.remove(path)
		except OSError:
			pass


def _write_text(path, text):
	"""Replace a file atomically, through a symlink and keeping its mode.

	ponytail: writing to a temp file and renaming truncates nothing, so an error partway leaves the old
	file rather than half the new one — which is what `json.dump` into an opened "w" did to settings.json,
	leaving it unparseable and every later run refusing on it.
	ponytail: os.replace onto the PATH swaps a symlink out for a plain file. ~/.claude/CLAUDE.md living in
	a dotfiles checkout is exactly the setup people have, and the old open(path, "w") wrote through the
	link. Resolve first, so the target is rewritten and the link stays a link.
	ponytail: and copy the mode across. settings.json is where Claude Code keeps env blocks with API keys
	in them; a 0600 file coming back 0664 because of the umask is a real leak, quietly.
	"""
	real = os.path.realpath(path)
	tmp = real + ".gitdashy.tmp"
	# ponytail: created 0600 and widened only to whatever the target already was. Writing at the umask
	# first put a 0600 settings.json — env blocks, API keys — on disk as 0644 for the length of the
	# write, in the same directory. Narrow first is free; the other order has a window.
	# ponytail: O_CREAT ignores the mode when the file already exists, and a .gitdashy.tmp left by a
	# crash would then keep whatever mode it had. Removing it first makes the 0600 unconditional.
	# ponytail: a target that does not exist yet is CREATED 0600 rather than at the umask. These are
	# one user's own config files and one of them holds API keys, so narrow is the right default.
	try:
		os.remove(tmp)
	except OSError:
		pass
	fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
	with os.fdopen(fd, "w") as f:
		f.write(text)
	if os.path.exists(real):
		shutil.copymode(real, tmp)
	os.replace(tmp, real)


def _write_json(path, data):
	"""ponytail: one writer, so the symlink and mode rules cannot hold in one place and not the other."""
	_write_text(path, json.dumps(data, indent=2))


def _ours(link, target):
	return os.path.islink(link) and os.path.realpath(link) == os.path.realpath(target)


def explain():
	"""What install would change on this machine, in the user's own paths. Returns report lines."""
	d, out = claude_dir(), []
	out.append("gitdashy install wires this machine so every agent session reads what your reviews learned.")
	out.append("")
	out.append("Reviews do not need this. They read memory through their own prompt and always have; this is")
	out.append("only so a coding session sees the same facts. It is additive, and reversible.")
	out.append("")
	out.append("It will:")
	for link, target in links():
		state = ("already correct" if _ours(link, target)
		         else "EXISTS, will be left alone" if os.path.lexists(link) else "new")
		out.append(f"  · symlink {knowledge.tilde(link)} -> {knowledge.tilde(target)}   [{state}]")
	md = os.path.join(d, "CLAUDE.md")
	n = BLOCK.count("\n@")  # ponytail: counted, not written down — "four" outlived the block it described
	out.append(f"  · append {n} import{'s' if n != 1 else ''} to {knowledge.tilde(md)}, inside a marked block"
	           + ("   [already there]" if IMPORT in _read(md) else "   [new]"))
	# ponytail: the consent screen must name the migration it is asking consent for. It said nothing
	# about removing a symlink and rewriting a block in the user's own config — this install's whole
	# promise is that it shows what it will touch first. The same call apply() makes, in dry mode.
	for line in retire(dry=True):
		out.append("  · " + line[len("would "):] if line.startswith("would ") else "  · " + line)
	out.append("")
	out.append("It will NOT: install hooks, touch settings.json, change any repo, or send anything anywhere.")
	out.append("Cross-repo facts load live through the symlink — the session reads the same file a review")
	out.append("writes, so nothing is copied and nothing goes stale. Per-repo facts are separate: run")
	out.append("`gitdashy init` inside a repo to add those, or leave them out.")
	out.append("")
	out.append("Reverse it any time with `gitdashy install --uninstall`, which removes only what it wrote.")
	return out


def apply(dry=False):
	"""Wire this machine so every session reads review memory. Returns report lines."""
	d = claude_dir()
	if not os.path.isdir(d):
		return [f"FAIL  no agent config directory at {knowledge.tilde(d)} — is claude installed?"]
	out, did = [], "would " if dry else ""
	for link, target in links():
		if _ours(link, target):
			out.append(f"ok    {knowledge.tilde(link)} already points at {knowledge.tilde(target)}")
		elif os.path.lexists(link):  # ponytail: never replace something we did not make
			out.append(f"SKIP  {knowledge.tilde(link)} exists and is not ours — left alone")
		else:
			pending = "" if os.path.isdir(target) else "   (waits until there is one)"
			out.append(f"{did}link  {knowledge.tilde(link)} -> {knowledge.tilde(target)}{pending}")
			if not dry:
				# ponytail: make your own memory dir rather than leaving a link to nothing. The team's is
				# left dangling on purpose — it exists once you join, and a missing import is skipped.
				if target == config.LOCAL_MEMORY:
					os.makedirs(target, exist_ok=True)
				os.symlink(target, link)
	out += retire(dry)
	md = os.path.join(d, "CLAUDE.md")
	text = _read(md)
	if IMPORT in text:
		out.append(f"ok    {knowledge.tilde(md)} already imports the review memory")
	else:
		out.append(f"{did}add   the import block to {knowledge.tilde(md)}")
		if not dry:
			with open(md, "a") as f:
				f.write(("\n" if text and not text.endswith("\n") else "") + "\n" + BLOCK)
	return out


def remove(dry=False):
	"""Undo exactly what apply() wrote, and say so when something is not ours to undo."""
	out, did = [], "would " if dry else ""
	for link, target in links():
		if _ours(link, target):
			out.append(f"{did}remove  {knowledge.tilde(link)}")
			if not dry:
				os.remove(link)
		elif os.path.lexists(link):
			out.append(f"SKIP    {knowledge.tilde(link)} is not the link we made — left alone")
		else:
			out.append(f"ok      {knowledge.tilde(link)} is not there")
	if old := stale_team_link():
		out.append(f"{did}remove  {knowledge.tilde(old)}   (the retired team link)")
		if not dry:
			os.remove(old)
	md = os.path.join(claude_dir(), "CLAUDE.md")
	text = _read(md)
	if BEGIN in text and END in text:
		out.append(f"{did}remove  the import block from {knowledge.tilde(md)}")
		if not dry:
			_write_text(md, _strip_blocks(text, BEGIN, END))
	elif IMPORT in text:
		out.append(f"SKIP    {knowledge.tilde(md)} imports the memory but not in a block we wrote — remove it by hand")
	else:
		out.append(f"ok      {knowledge.tilde(md)} does not import it")
	return out


def registered():
	"""[(into, repo, root, loader)] for every mirror this machine refreshes, newest entry per path.

	ponytail: deduplicated on READ, so register can append without a lock. The hook runs at every
	session start, and two starting together would otherwise interleave a read-modify-write and drop one.
	ponytail: root and loader may be "" — entries written before they were recorded.
	ponytail: append-only in both directions, so the file only grows. Self-limiting per path — a
	tombstoned entry leaves this list, so refresh writes one tombstone and not one per tick — but
	repeated init/--forget cycles never shrink it. Compact on read-then-rewrite if it ever matters.
	"""
	seen = {}
	for line in _read(REGISTRY).splitlines():
		if not line.strip():
			continue
		try:
			e = json.loads(line)
		except ValueError:
			# ponytail: the format before this was "<into>\t<repo>". Dropping such a line silently read
			# the whole registry back as empty, so every mirror on that machine stopped refreshing with
			# nothing said. Only reachable on a machine that ran this branch before the change — which
			# is exactly the machines reviewing it.
			part = line.split("\t")
			if not part[0].startswith(os.sep):
				continue  # a line we cannot read is not a reason to lose the rest
			e = {"into": part[0], "repo": part[1] if len(part) > 1 else ""}
		into = e.get("into") or e.get("forget")
		if not isinstance(into, str) or not into:
			continue
		if "forget" in e:
			seen.pop(into, None)
		else:
			seen[into] = (into, e.get("repo", ""), e.get("root", ""), e.get("loader", ""))
	return list(seen.values())


def register(into, repo, root="", loader=""):
	"""Remember to refresh this mirror. Returns True when it was not already known."""
	into = os.path.abspath(os.path.expanduser(into))
	if any(i == into for i, *_ in registered()):
		return False
	# ponytail: JSON, because the fields are PATHS. A tab in a directory name is legal and split a
	# delimited line into the wrong fields silently; a quote or a newline would have been worse.
	with open(REGISTRY, "a") as f:  # one append, atomic enough; duplicates die on read
		f.write(json.dumps({"into": into, "repo": repo, "root": root, "loader": loader}) + "\n")
	return True


def unregister(into):
	"""Stop refreshing this mirror. Returns True when it was known."""
	into = os.path.abspath(os.path.expanduser(into))
	known = registered()
	if not any(e[0] == into for e in known):
		return False
	# ponytail: append a tombstone rather than rewrite. register() appends without a lock because the
	# hook runs at every session start; a truncating rewrite here would drop an entry appended during it.
	with open(REGISTRY, "a") as f:
		f.write(json.dumps({"forget": into}) + "\n")
	return True


def _toplevel(path):
	"""The git repo `path` sits in, or "" — asked from the nearest directory that exists."""
	base = path
	while not os.path.isdir(base) and os.path.dirname(base) != base:
		base = os.path.dirname(base)
	r = subprocess.run(["git", "-C", base, "rev-parse", "--show-toplevel"],
	                   capture_output=True, text=True, timeout=60)
	return r.stdout.strip() if r.returncode == 0 else ""


def _gitdir(root):
	"""The directory holding info/exclude. ponytail: NOT <root>/.git — in a linked worktree or a
	submodule that is a FILE, and building a path through it raises NotADirectoryError. Ask git."""
	r = subprocess.run(["git", "-C", root, "rev-parse", "--git-common-dir"],
	                   capture_output=True, text=True, timeout=60)
	d = r.stdout.strip()
	if r.returncode != 0 or not d:
		return ""
	return d if os.path.isabs(d) else os.path.join(root, d)


def _exclude(root, rel):
	"""Keep the mirror out of git via info/exclude.

	ponytail: never .gitignore. That file is tracked and belongs to everyone; a mirror is one machine's
	local copy, and committing an ignore rule for it puts your setup in someone else's history.
	"""
	gd = _gitdir(root)
	if not gd:
		return "FAIL  cannot find the git directory — refusing to write a mirror git could commit"
	p = os.path.join(gd, "info", "exclude")
	rule = rel.rstrip("/") + "/"
	if rule in _read(p).splitlines():
		return f"ok    {rule} is already excluded"
	os.makedirs(os.path.dirname(p), exist_ok=True)
	with open(p, "a") as f:
		f.write(f"\n# gitdashy mirror (local, never commit)\n{rule}\n")
	return f"added {rule} to .git/info/exclude"


def _import(loader, line):
	text = _read(loader)
	if line in text.splitlines():
		return f"ok    {knowledge.tilde(loader)} already imports {line}"
	if os.path.dirname(loader):
		os.makedirs(os.path.dirname(loader), exist_ok=True)
	with open(loader, "a") as f:
		f.write(("\n" if text and not text.endswith("\n") else "")
		        + f"\n# gitdashy: this repo's review memory (read-only mirror)\n{line}\n")
	return f"added {line} to {knowledge.tilde(loader)}"


def _unimport(loader, into):
	"""Take out the import line we added, and the comment above it. The mirror files stay.

	ponytail: what makes a mirror live is the import, not the file. Removing that leaves readable facts
	behind rather than reaching outside the agent config to delete data — while making sure no session
	goes on reading memory that nothing refreshes any more.
	"""
	line = "@" + os.path.relpath(into, os.path.dirname(loader)) + "/repo.md"
	kept, text = [], _read(loader)
	for l in text.splitlines():
		if l.strip() == line or l.strip() == "# gitdashy: this repo's review memory (read-only mirror)":
			continue
		kept.append(l)
	if kept != text.splitlines():
		_write_text(loader, "\n".join(kept).rstrip("\n") + "\n" if any(k.strip() for k in kept) else "")


def wire_repo(into, loader, repo):
	"""Wire one repo: ignore the mirror, import it, keep it fresh, write it now. Returns report lines."""
	out = []
	into = os.path.abspath(os.path.expanduser(into))
	loader = os.path.abspath(os.path.expanduser(loader))
	root = _toplevel(into)
	out.append(_exclude(root, os.path.relpath(into, root)) if root else
	           f"note  {knowledge.tilde(into)} is not inside a git repo — nothing to exclude")
	out.append(_import(loader, "@" + os.path.relpath(into, os.path.dirname(loader)) + "/repo.md"))
	out.append(f"added {knowledge.tilde(into)} to the refresh list, as {repo}"
	           if register(into, repo, root, loader)
	           else f"ok    {knowledge.tilde(into)} is already refreshed every tick")
	out.append("      " + mirror.sync(into, repo, pull=False))
	return out


def corpus_files(corpus):
	"""The identity markdown a corpus offers, sorted, or [] when it has none."""
	d = os.path.join(corpus, "identity")
	return sorted(f for f in os.listdir(d) if f.endswith(".md") and not f.endswith(".template")) \
		if os.path.isdir(d) else []


def corpus_block(corpus):
	names = corpus_files(corpus)
	body = "\n".join(f"@identity/{n}" for n in names)
	return f"""{CBEGIN}
# Agent corpus

How this machine's agent works: what to establish before changing code, when to stop and ask,
and who it is working with. Installed by `gitdashy install --full` from {os.path.basename(corpus)}.

{body}
{CEND}
"""


def full_explain(corpus, url=""):
	"""What a full install changes. Returns report lines."""
	d, out = claude_dir(), []
	# ponytail: explain what apply will DO. apply imports from CORPUS_HOME when it exists; reading the
	# shipped corpus here named the wrong files and the wrong cost to anyone who had pointed
	# CORPUS_HOME at their own corpus — "import 3 files: AGENT.md, AGENTS.md, RULES.md" on a machine
	# about to import six others.
	src = CORPUS_HOME if os.path.isdir(CORPUS_HOME) else corpus
	# ponytail: with --corpus URL and no CORPUS_HOME yet, the remote's identity/ cannot be read before the
	# clone. Naming the SHIPPED corpus's files and token cost here described a corpus that was about to be
	# replaced by a different one — an unknown is disclosed as unknown, never filled in with a stand-in.
	unread = bool(url) and not os.path.isdir(CORPUS_HOME)
	names = corpus_files(src)
	words = sum(len(open(os.path.join(src, "identity", n)).read().split()) for n in names)
	out.append("gitdashy install --full puts an agent corpus on this machine, so every coding session")
	out.append("works to the same discipline — and adds the review-memory wiring `install` does.")
	out.append("")
	out.append("This is the big one. Read it before agreeing.")
	out.append("")
	out.append("It will:")
	out.append(f"  · {'clone ' + url if url else 'copy the corpus gitdashy ships'} to {knowledge.tilde(CORPUS_HOME)}"
	           + ("   [EXISTS, will be left alone]" if os.path.isdir(CORPUS_HOME) else "   [new]"))
	out.append(f"  · symlink {knowledge.tilde(os.path.join(d, 'identity'))} -> that corpus's identity/")
	into = knowledge.tilde(os.path.join(d, "CLAUDE.md"))
	out.append(f"  · import that corpus's identity/*.md into {into} — which files, and how many, cannot be"
	           if unread else
	           f"  · import {len(names)} files into {into}: {', '.join(names)}")
	if unread:
		out.append("    known until it is cloned")
	out.append("  · seed USER.md from the template, for you to fill in, if it is not there already")
	out.append(f"  · register a SessionStart and a Stop hook in {knowledge.tilde(os.path.join(d, 'settings.json'))}")
	out.append("  · everything plain `gitdashy install` does, for review memory")
	out.append("")
	out.append("What that costs, every session on this machine, permanently:")
	out.append("  · however many tokens that corpus's identity/ holds — unknown until it is cloned"
	           if unread else
	           f"  · about {int(words * 1.35):,} tokens of instructions, before you have typed anything")
	out.append("  · one hook at the start of every session, and one at the end, in every repo")
	out.append("")
	out.append("The SessionStart hook seeds .agent/ notes in a repo, excludes them from git (via")
	out.append(".git/info/exclude, never the tracked .gitignore), and mirrors that repo's review memory.")
	out.append("It writes nothing that git can see, and exits quietly if it is not in a repo.")
	out.append("")
	# ponytail: the Stop hook can BLOCK a stop, which is a thing done TO the session rather than for it,
	# so the consent screen says so in those words. #31's blocking finding was this same rule one door
	# along: a hook that gained a new kind of power and a consent screen that still described the old one.
	out.append("The Stop hook reads the session transcript when a session ends and, if you interrupted")
	out.append("or refused tool calls enough times, asks the agent once to write down what it learned.")
	out.append("It can hold a session open for that one question — never twice, and never on a routine")
	out.append("session. It reads only the transcript, and sends nothing anywhere.")
	out.append("")
	# ponytail: until this corpus shipped a bin/, the corpus was DATA — markdown imported into context,
	# templates copied. The hook now RUNS a script out of it, so a --corpus URL is no longer only text you
	# read: it is code that executes at every session start. That is a different thing to agree to, and
	# consent that does not name it is not consent to it.
	out.append("It also RUNS one script from that corpus if it ships an executable bin/budget-check.sh —")
	out.append("shell, at every session start, in every repo. A corpus is code you run, not only text you")
	out.append("read: `--corpus URL` grants that to whoever can push to it.")
	out.append("")
	out.append("`gitdashy install --full --uninstall` reverses all of it. The corpus is left on disk,")
	out.append("because by then you may have edited it.")
	return out


# ponytail: gitdashy's own, not the corpus's. Pointing at <corpus>/bin/ meant any corpus that did not
# happen to ship this exact file registered a hook to a missing command — in every session, everywhere.
HOOK = os.path.join(HERE, "dashy", "hooks", "claude-session-start.sh")
# ponytail: enough path to be ours. A bare filename would match — and uninstall would delete —
# somebody else's hook that happened to be called the same thing.
HOOK_MATCH = os.path.join("dashy", "hooks", "claude-session-start.sh")
STOP_HOOK = os.path.join(HERE, "dashy", "hooks", "claude-stop.sh")
STOP_MATCH = os.path.join("dashy", "hooks", "claude-stop.sh")

# ponytail: a TABLE, so a third hook is a row rather than a fourth copy of the register block and a
# fourth branch in uninstall. (event, script, match, what the status line says, whether it is passed
# the corpus home). The Stop hook takes no argument: everything it judges comes in on stdin.
HOOK_TABLE = (("SessionStart", HOOK, HOOK_MATCH, "Preparing repo notes", True),
              ("Stop", STOP_HOOK, STOP_MATCH, "Checking what this session learned", False))


def _count(settings, event):
	"""How many of `event`'s hooks there are in total, across every group."""
	return sum(len(g.get("hooks", [])) for g in settings.get("hooks", {}).get(event, []))


def _hooks(settings, script, event):
	"""`event`'s groups with our hook taken out. Empty groups are dropped.

	ponytail: callers compare HOOK counts, never group counts. Ours can end up sharing a group with
	somebody else's — then the group survives, the count of groups is unchanged, and a group-count
	check concludes we were never installed and appends a second copy.
	"""
	out = []
	for group in settings.get("hooks", {}).get(event, []):
		kept = [h for h in group.get("hooks", []) if script not in str(h.get("command", ""))]
		if kept:
			out.append({**group, "hooks": kept})
	return out


def full_apply(corpus, url="", dry=False):
	"""Install a corpus and its hook, then the memory wiring. Returns report lines.

	ponytail: every failure below unwinds what this run had already done. Returning halfway used to
	leave the clone, the symlink or the imports in place — and the worst of those poisoned the command
	for good, because the next run short-circuits on CORPUS_HOME existing and fails identically forever,
	naming no directory to delete. A half-install that reports FAIL is not a safe state to leave.
	"""
	d, out, did = claude_dir(), [], "would " if dry else ""
	undo = []

	def fail(msg):
		# ponytail: an undo that failed is reported, not swallowed. A half-unwound install is the one
		# state worth naming out loud — it is what the next run trips over, and silence here was how
		# the original poisoning stayed invisible for four review rounds.
		done, left = [], []
		for what, act in reversed(undo):
			try:
				err = act()
			except OSError as e:
				err = str(e)
			(left.append(f"{what} ({err})") if err else done.append(what))
		return out + [f"FAIL  {msg}"] \
		           + ([f"      undone: {', '.join(done)}"] if done else []) \
		           + ([f"      COULD NOT UNDO: {', '.join(left)} — remove by hand"] if left else [])

	def go():
		if not os.path.isdir(d):
			return [f"FAIL  no agent config directory at {knowledge.tilde(d)} — is claude installed?"]
		if os.path.isdir(CORPUS_HOME):
			out.append(f"ok    {knowledge.tilde(CORPUS_HOME)} is already there — left as it is")
		else:
			out.append(f"{did}install  the corpus into {knowledge.tilde(CORPUS_HOME)}")
			if not dry:
				from . import team
				try:
					err = team.clone(url, CORPUS_HOME) if url else (shutil.copytree(corpus, CORPUS_HOME) and "")
				except OSError as e:  # ponytail: the clone path checked its error; the copy path did not, and
					err = str(e)      # then symlinked, imported and hooked a directory that was never there
				if os.path.isdir(CORPUS_HOME):
					# ponytail: the path is bound HERE, as a default argument. `lambda: rmtree(CORPUS_HOME)`
					# reads the global when the undo runs, so anything that rebinds it between these two
					# points retargets a recursive delete — and the tests rebind it routinely.
					undo.append((knowledge.tilde(CORPUS_HOME),
					             lambda p=CORPUS_HOME: knowledge.rmtree_owned(p)))
				if err:
					return fail(err)
		home = CORPUS_HOME if not dry or os.path.isdir(CORPUS_HOME) else corpus
		link = os.path.join(d, "identity")
		ident = os.path.join(home, "identity")
		if _ours(link, ident):
			out.append(f"ok    {knowledge.tilde(link)} already points at the corpus")
		elif os.path.lexists(link):
			out.append(f"SKIP  {knowledge.tilde(link)} exists and is not ours — left alone, so nothing is imported")
		elif not os.path.isdir(ident):
			# ponytail: refuse rather than leave a link to nothing. An identity that is not there imports
			# nothing, and a dangling symlink in the agent config is worse than an install that stopped.
			# ponytail: checked on a dry run too — --dry-run is what you reach for to find out why the real
			# one failed, and it used to answer with a clean plan in exactly the state that had broken it.
			return fail(f"{knowledge.tilde(ident)} has no identity/ — nothing to install")
		else:
			out.append(f"{did}link  {knowledge.tilde(link)} -> {knowledge.tilde(ident)}")
			if not dry:
				os.symlink(ident, link)
				undo.append((knowledge.tilde(link), lambda: os.remove(link)))
		user, tmpl = os.path.join(ident, "USER.md"), os.path.join(ident, "USER.md.template")
		if os.path.exists(user):
			out.append("ok    USER.md is already filled in")
		elif os.path.exists(tmpl):
			out.append(f"{did}seed  USER.md from the template — fill it in, it is the highest-value file here")
			if not dry:
				shutil.copy(tmpl, user)
				# ponytail: an unwound install must not leave a template in a corpus that was already
				# there. Only reachable when CORPUS_HOME pre-existed — otherwise the clone's own undo
				# takes it — which is exactly the case where the directory is not ours to litter.
				undo.append((f"the seeded {knowledge.tilde(user)}", lambda: os.remove(user)))
		if corpus_remembers(ident) is False:
			# ponytail: said at the one moment they could act on it. gitdashy cannot write their corpus;
			# it can say that without this line the reviews have no second observer and every draft
			# they ever file stays at (1). The dashboard's Knowledge row keeps saying it afterwards.
			out.append("NOTE  this corpus never tells a session to `gitdashy remember` — add that to its AGENT.md,"
			           " or nothing a session learns reaches the reviews")
		md = os.path.join(d, "CLAUDE.md")
		text = _read(md)
		if CBEGIN in text:
			out.append(f"ok    {knowledge.tilde(md)} already imports the corpus")
		else:
			out.append(f"{did}add   the corpus imports to {knowledge.tilde(md)}")
			if not dry:
				with open(md, "a") as f:
					f.write(("\n" if text and not text.endswith("\n") else "") + "\n" + corpus_block(home))
				# ponytail: read FIRST. `open(md, "w").write(_strip_blocks(_read(md), ...))` evaluates the
				# callee before the argument, so the truncation happened before the read and the undo
				# wrote back an empty file — the user's whole global CLAUDE.md, destroyed by the code
				# added to stop this path leaving things behind.
				# ponytail: a file we created is removed, not left empty. Undo means the state before.
				undo.append((f"the imports in {knowledge.tilde(md)}", lambda: _unwrite(md, text)))
		sp = os.path.join(d, "settings.json")
		try:
			settings = json.loads(_read(sp) or "{}")
		except ValueError:
			return fail(f"{knowledge.tilde(sp)} is not valid JSON — fix it first")
		wrote = False
		for event, script, match, saying, takes_home in HOOK_TABLE:
			if _count({"hooks": {event: _hooks(settings, match, event)}}, event) != _count(settings, event):
				out.append(f"ok    the {event} hook is already registered")
			elif not (os.path.isfile(script) and os.access(script, os.X_OK)):
				# ponytail: only reachable if gitdashy's own install is damaged — but reported per hook,
				# because one missing script must not silently cost you the other.
				out.append(f"SKIP  {knowledge.tilde(script)} is missing or not executable — no {event} hook")
			else:
				out.append(f"{did}hook  register {event} -> {knowledge.tilde(script)}")
				if not dry:
					cmd = shlex.quote(script) + (f" {shlex.quote(home)}" if takes_home else "")
					settings.setdefault("hooks", {}).setdefault(event, []).append(
						{"hooks": [{"type": "command", "command": cmd,
						            "timeout": 10, "statusMessage": saying}]})
					wrote = True
		if wrote:
			_write_json(sp, settings)
		return out + [""] + (apply(dry) if not dry else [f"{did}do    everything plain `install` does"])

	# ponytail: only fail() unwound, so anything that RAISED walked out past the undo list — a
	# permission error on the symlink left the fresh clone at CORPUS_HOME, which is the exact
	# poisoning the unwinding was added to remove, reached by a different door. Every exit from
	# this function now goes through fail(), whether it was chosen or thrown.
	try:
		return go()
	except OSError as e:
		return fail(str(e))


def full_remove(dry=False):
	"""Reverse a full install. The corpus itself stays: by now you may have edited it."""
	d, out, did = claude_dir(), [], "would " if dry else ""
	link, ident = os.path.join(d, "identity"), os.path.join(CORPUS_HOME, "identity")
	if _ours(link, ident):
		out.append(f"{did}remove  {knowledge.tilde(link)}")
		if not dry:
			os.remove(link)
	elif os.path.lexists(link):
		out.append(f"SKIP    {knowledge.tilde(link)} is not the link we made — left alone")
	md = os.path.join(d, "CLAUDE.md")
	text = _read(md)
	if CBEGIN in text and CEND in text:
		out.append(f"{did}remove  the corpus imports from {knowledge.tilde(md)}")
		if not dry:
			_write_text(md, _strip_blocks(text, CBEGIN, CEND))
	sp = os.path.join(d, "settings.json")
	try:
		settings = json.loads(_read(sp) or "{}")
	except ValueError:
		out.append(f"SKIP    {knowledge.tilde(sp)} is not valid JSON — remove the hook by hand")
		settings = None
	# ponytail: every hook in the table, not the one this branch happened to add. An uninstall that
	# leaves a Stop hook pointing into a checkout the user then deletes fails at the end of every
	# session, forever, with nothing naming gitdashy as the cause.
	dropped = False
	for event, _, match, _, _ in HOOK_TABLE if settings is not None else ():
		if not settings.get("hooks", {}).get(event):
			continue
		kept = _hooks(settings, match, event)
		if _count({"hooks": {event: kept}}, event) != _count(settings, event):
			out.append(f"{did}remove  the {event} hook from {knowledge.tilde(sp)}")
			if not dry:
				settings["hooks"][event] = kept
				if not kept:
					settings["hooks"].pop(event)
				dropped = True
	if dropped:
		_write_json(sp, settings)
	known = registered()
	if known:
		out.append(f"{did}forget  {len(known)} mirror{'s' if len(known) > 1 else ''} — the import each repo"
		           f" uses is removed, the facts themselves are left as a snapshot you can read or delete")
		if not dry:
			for into, _repo, _root, loader in known:
				if loader and os.path.exists(loader):
					_unimport(loader, into)
				unregister(into)
	out.append(f"note    {knowledge.tilde(CORPUS_HOME)} is left on disk — you may have edited it")
	return out + [""] + remove(dry)


# ponytail: ONLY what is true of you whatever you are working on. "Role" and "What you own" used to be
# here and are not: a person's role differs per project, and what they own is a property OF a project —
# which put a paragraph about one product into a file every session in every repo loads. The corpus's
# own USER.md is the proof: ten of its thirteen sections are about one platform, and its cross-cutting
# section says so out loud ("these are cross-cutting: they hold in every repo"). Ownership moved to the
# project brief, where it is scoped by binding and where a review of that repo is actually told it.
ASK_YOU = (("Name", "what you would like to be called"),
           ("How you work", "where you want friction and where you do not"))
ASK_PROJECT = (("The project", "what it is, and who uses it"),
               ("Why it matters", "the outcome that makes the work worth doing"),
               ("Constraints", "regulatory, contractual, performance — anything with real consequences"),
               ("How the code is shaped", "what a newcomer would otherwise learn the hard way"),
               # ponytail: WHO, not only what. Ownership is a property of the project, so it belongs
               # here rather than in USER.md — and a review of one of these repos is told it, which is
               # the point. Shared with the team when the brief is a team's: on a small team "who
               # answers for this" is something everyone benefits from and nobody should have to ask.
               ("Who does what", "who owns which parts, you included"))


SETUP_MARK = "<!-- written by gitdashy setup -->"


def _add(out, key, buf):
	"""ponytail: a heading twice APPENDS. Overwriting meant a rewrite deleted the first one silently."""
	body = _unescape("\n".join(buf)).strip()
	out[key] = (out[key] + "\n\n" + body).strip() if key in out else body


def sections(text):
	"""{heading: body} from a brief, in file order. Anything above the first `## ` is not a section.

	ponytail: fences and escapes both matter, because the bodies are text somebody typed. A "## " inside
	a code block is not a heading, and one that compose() escaped was never a heading — miss either and
	setup, which rewrites the file from what this returns, silently rearranges what you wrote.
	"""
	out, key, buf = {}, None, []
	for line, plain in _outside(text):
		if line.startswith("## ") and plain:
			if key is not None:
				_add(out, key, buf)
			key, buf = line[3:].strip(), []
		elif key is not None:
			buf.append(line)
	if key is not None:
		_add(out, key, buf)
	return out


def _escape(body):
	"""Keep an answer from becoming structure, without touching what is inside a fence.

	ponytail: the reader learned about fences and the writer did not, so a hand-written code block
	containing `# a comment` was written back as `\\# a comment` — round-tripping through sections()
	while rendering a literal backslash on disk, and USER.md is read by the agent as-is, not through
	sections(). A guard only half of a round trip knows about corrupts the other half.
	"""
	out, at = [], None
	for l in body.splitlines():
		was, at = at, _fence(l, at)
		out.append(("\\" + l) if l.startswith("#") and was is None and at is None else l)
	# ponytail: an answer with an unclosed fence would otherwise bleed into the sections after it —
	# the writer tracks fences per body, the reader per file, so the next read hands back one section
	# swallowing the rest. Closing it here keeps the writer's invariant local to the answer.
	if at is not None:
		out.append(at[0] * at[1])
	return "\n".join(out)


def _unescape(body):
	# ponytail: a literal "\#" somebody typed comes back as "#". Lossy, and rarer than the corruption
	# escaping prevents — but it is a real edit to their text, so it is written down rather than assumed.
	out = []
	for l, plain in _outside(body):
		out.append(l[1:] if l.startswith("\\#") and plain else l)
	return "\n".join(out)


def compose(title, lead, answers, extra=()):
	"""A markdown brief from (heading, body) pairs, dropping the empty ones.

	ponytail: `extra` carries sections nobody asked about — added by hand, or by an older version that
	asked different questions. A rewrite that only knew its own questions would delete them.
	"""
	# ponytail: escaped, so an answer containing "## Role" is a line of prose and not a second section.
	# Without it a typed answer could forge headings that the next read would hand back as real ones.
	body = "".join(f"## {k}\n\n{_escape(v)}\n\n" for k, v in list(answers) + list(extra) if v)
	return f"{SETUP_MARK}\n# {title}\n\n{lead}\n\n{body}" if body else ""


def setup_done(corpus_home=None, project=True):
	"""True when there is nothing left to offer. `project` False asks only about USER.md.

	ponytail: the install-time offer passes project=False, because it no longer asks the project
	question — and a "done" that still waited on a brief nobody was going to be asked for would offer
	the prompt forever on a machine that answered everything it was asked.
	"""
	from . import memory
	home = corpus_home or CORPUS_HOME
	user = os.path.join(home, "identity", "USER.md")
	tmpl = os.path.join(home, "identity", "USER.md.template")
	mine = _read(user).strip() and _read(user).strip() != _read(tmpl).strip()  # a seeded template is not done
	return bool(mine) and (memory.brief_written() if project else True)


def setup(ask, corpus_home=None, project=True):
	"""Walk the briefs a corpus needs, writing only what was answered. Returns report lines.

	ponytail: asked rather than templated. A blank template is a template nobody fills in, and an agent
	that knows neither who you are nor what the work is for reasons from the code alone — which is the
	one thing it can already see.
	ponytail: `project` False skips the project brief, and `install --full` passes it. WHO YOU ARE is a
	property of the machine; WHAT THE WORK IS FOR is a property of a repo, and a person works on more
	than one. Asked once at install time it wrote a single ~/.prs_memory/project.md that every repo
	bound to no team then read — one product's brief in every review of every other, which is the exact
	failure brief() was rewritten to stop. Binding already scopes it; `gitdashy setup` still asks.
	"""
	out = []
	home = corpus_home or CORPUS_HOME
	user = os.path.join(home, "identity", "USER.md")
	# ponytail: a marker only compose() writes. Sniffing for the words "gitdashy setup" matched the
	# shipped TEMPLATE, which says them in prose — so the file install --full seeds looked like one
	# setup had written, and editing its blanks in place then lost everything on the next run.
	# ponytail: the file install --full SEEDS is the shipped template, byte for byte. It is not empty
	# and carries no marker, so the guard below read it as a file someone had written by hand and
	# refused to ask — leaving the guided path dead in exactly the case it exists for. Untouched
	# template means untouched; one edit of their own and it is theirs again.
	seeded = _read(user).strip() == _read(os.path.join(home, "identity", "USER.md.template")).strip()
	if os.path.exists(user) and _read(user).strip() and not seeded and SETUP_MARK not in _read(user):
		# ponytail: the brief refuses when it exists; this file must too, or re-running to change one
		# line destroys the rest. Only a file setup itself wrote is safe to rewrite.
		out.append(f"ok     {knowledge.tilde(user)} is yours already — edit it directly to change it")
	elif os.path.isdir(os.path.dirname(user)):
		# ponytail: blank means KEEP, not erase. compose() rewrites the whole file, so a run that only
		# answered one question used to delete every other section — including ones added by hand — while
		# three separate messages promised every question was skippable.
		have = sections(_read(user))
		got = []
		for k, hint in ASK_YOU:
			now = have.get(k, "")
			said = ask(f"{k} — {hint}" + (f"\n  [now: {now.splitlines()[0][:60]}]" if now else ""))
			got.append((k, said or now))
		# ponytail: the file's own order, then anything new. Appending the unasked ones moved a section
		# written above ## Name to the bottom on every single run — the file never settled.
		said = dict(got)
		order = list(have) + [k for k, _ in ASK_YOU if k not in have]
		text = compose("Who you are", "Written by `gitdashy setup`. Edit it freely; it is yours.",
		               [(k, said.get(k, have.get(k, ""))) for k in order])
		if text:
			_write_text(user, text)
			out.append(f"wrote  {knowledge.tilde(user)}")
		else:
			out.append(f"ok     {knowledge.tilde(user)} left as it was — nothing answered")
	else:
		out.append(f"SKIP   no corpus at {knowledge.tilde(home)} — run `gitdashy install --full` first")
	from . import bind, memory, team
	if not project:
		# ponytail: BEFORE anything that works out which brief would be written. Below this the code
		# resolves a team and says "writing your own brief" — a sentence about a file nobody is being
		# asked for, printed to someone who just declined to be asked. The pointer matters more than the
		# skip: they will look for the question they are used to, so this says where it went.
		return out + ["",
		              "note   no project brief asked for here — what the work is for belongs to a repo, not",
		              "       to this machine. `gitdashy setup` writes one; `gitdashy teams --new NAME` and",
		              "       `gitdashy bind --owner OWNER --team NAME` give each project its own."]

	# ponytail: a team brief now reaches only the repos BOUND to that team, so the old line — "every
	# review reads it" — became false the moment selection stopped being "yours and theirs, always".
	# Saying which reviews read it is the part someone acts on.
	# ponytail: the brief of the team the repo you STAND IN is bound to — that is what "a brief belongs to
	# a repo" means once there is a keyboard in front of it. Picking the team by COUNT wrote the team's
	# brief from inside an unbound side project whenever exactly one team was joined, and yours from
	# inside a bound repo whenever two were: which file this wrote was decided by how many teams you were
	# in, not by where you were. Every note names a command that exists; setup parses no arguments.
	here = team.origin_slug(".")
	slug = bind.of(here) if here else ""
	if not here:
		out.append("note   no git origin here — writing your own brief; run this inside a repo bound to a team "
		           "to write that team's")
	elif not slug:
		out.append(f"note   {here} is bound to no team — writing your own brief; `gitdashy bind` binds it to one")
	elif not bind.team_dir(slug):
		out.append(f"note   {here} is bound to team {slug}, which this machine has not joined — writing your own brief")
		slug = ""
	mine = not slug
	dest = memory.brief_path(slug)
	# ponytail: says what it COVERS, not just whose it is. "yours" reads as "scoped to me" and it is the
	# opposite — one file, every unbound repo, so a second project inherits the first one's brief.
	whose = ("yours, and EVERY repo bound to no team reads it — one brief for all of them, so bind each "
	         "project to its own team (`gitdashy teams --new`, `gitdashy bind`) if you have more than one"
	         if mine else
	         f"the team's, shared with everyone in {slug}, and every review of a repo bound to it — {here} "
	         "included — reads it")
	if os.path.exists(dest):
		out.append(f"ok     {knowledge.tilde(dest)} already written — edit it directly to change it")
		return out
	out.append("")
	out.append(f"Now what the work is for. This brief is {whose}.")
	got = [(k, ask(f"{k} — {hint}")) for k, hint in ASK_PROJECT]
	text = compose("What is being built", "Written by `gitdashy setup`. Reviews of the repos it covers read this.", got)
	if not text:
		return out + ["ok     no brief written — `gitdashy setup` again whenever you want one"]
	os.makedirs(os.path.dirname(dest), exist_ok=True)
	_write_text(dest, text)
	out.append(f"wrote  {knowledge.tilde(dest)}")
	if not mine:
		team.push_dir(team.dir_of(slug), "memory: the project brief", "sync")
	return out
