"""Mirror the shared review memory into a repo, so an agent session there reads what the reviews learned.

ponytail: a copy, not a symlink. Claude Code confines CLAUDE.md imports to the project tree — `~`,
absolute and symlinked paths are all refused — so a real file inside the repo is the only way in.
"""
import datetime
import os
import subprocess

from .. import config
from . import memory, team

NAMES = ("general.md", "repo.md")  # the only names sync() ever writes or removes
HEADER = """> **Shared team memory — read-only mirror.** PR reviews write these facts; `gitdashy sync-memory`
> copies them here. Edits to this file are lost at the next sync — change the source, not the mirror.
>
> source: `{src}` · synced: {at}

"""


def tracked(path, names=NAMES):
	"""True when git would commit a file written at `path`: inside a repo and not ignored.

	ponytail: `path` need not exist — check-ignore is pure path matching, so we ask about the real target
	but run git from the nearest directory that does exist. Fail-safe: anything but a clean "ignored"
	answer counts as tracked, so a broken git call refuses rather than leaking memory into someone's history.
	"""
	base = os.path.abspath(path)
	while not os.path.isdir(base) and os.path.dirname(base) != base:
		base = os.path.dirname(base)
	if subprocess.run(["git", "-C", base, "rev-parse", "--show-toplevel"],
	                  capture_output=True, timeout=60).returncode != 0:
		return False  # not a git repo: nothing to leak into
	# ponytail: EVERY name we write, not just the first. An ignore rule matching general.md but not
	# repo.md would answer "ignored" and we would then commit the other one — the exact leak this prevents.
	return any(subprocess.run(["git", "-C", base, "check-ignore", "-q", os.path.join(os.path.abspath(path), n)],
	                          capture_output=True, timeout=60).returncode != 0 for n in names)


def sync(into, repo="", pull=True, general=False):
	"""Mirror `repo`'s memory into `into`, and the general file too when asked. One-line report.

	ponytail: per-repo only by default. Cross-repo facts belong in a user-level instruction file, which
	loads them live everywhere; mirroring them per repo as well would put every general fact in context
	twice. general=True is for anyone who has not wired that route and wants it all in the repo.

	ponytail: pull=False for callers on a clock (a SessionStart hook) — mirrors whatever the last
	gitdashy refresh pulled, instead of risking a network round trip inside their timeout.
	"""
	if pull:
		team.pull()  # newest shared memory first; a no-op when team mode is off
	# ponytail: ask BEFORE creating anything, or a refusal leaves the tree it refused to write in. And a
	# SessionStart hook calls this: an exception there is a broken hook, so failures come back as the report.
	try:
		if tracked(into, NAMES if general else NAMES[1:]):
			return f"gitdashy: refused — git would commit {into}; ignore that path before mirroring team memory there"
		os.makedirs(into, exist_ok=True)
	except OSError as e:
		return f"gitdashy: refused — {e}"
	at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
	try:
		return _write(into, repo, general, at)
	except OSError as e:
		return f"gitdashy: refused — {e}"


def _write(into, repo, general, at):
	src = " + ".join(label for label, _ in memory.sources())  # ponytail: the mirror shows what a review sees
	wrote = []
	for name, scope in zip(NAMES, (None if general else "", repo if repo else "")):
		dst = os.path.join(into, name)
		text = memory.scope_text(scope) if scope is None or scope else ""
		if text:
			with open(dst, "w") as f:
				f.write(HEADER.format(src=src, at=at) + text + "\n")
			wrote.append(name)
		elif os.path.exists(dst):
			os.remove(dst)  # ponytail: a mirror never outlives its source, or it becomes a rumour
	where = f"team {team.NAME}" if team.on() else config.MEMORY_DIR
	return (f"gitdashy: mirrored {', '.join(wrote) or 'nothing'} into {into}"
	        f" from {where}{' for ' + repo if repo else ''}{' · ' + team.ERROR if team.ERROR else ''}")
