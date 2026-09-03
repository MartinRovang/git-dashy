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


def tracked(path):
	"""True when git would commit a file written at `path`: inside a repo and not ignored.

	ponytail: fail-safe — anything but a clean "ignored" answer counts as tracked, so a broken
	git call refuses the mirror rather than leaking team memory into someone's history.
	"""
	if subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
	                  capture_output=True, timeout=60).returncode != 0:
		return False  # not a git repo: nothing to leak into
	return subprocess.run(["git", "-C", path, "check-ignore", "-q", os.path.join(path, NAMES[0])],
	                      capture_output=True, timeout=60).returncode != 0


def sync(into, repo="", pull=True):
	"""Copy general + `repo` memory into `into` as read-only mirrors. Returns a one-line report.

	ponytail: pull=False for callers on a clock (a SessionStart hook) — mirrors whatever the last
	gitdashy refresh pulled, instead of risking a network round trip inside their timeout.
	"""
	if pull:
		team.pull()  # newest shared memory first; a no-op when team mode is off
	os.makedirs(into, exist_ok=True)
	if tracked(into):
		return f"gitdashy: refused — git would commit {into}; ignore that path before mirroring team memory there"
	at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
	wrote = []
	for name, src in zip(NAMES, (memory.path(), memory.path(repo) if repo else "")):
		dst = os.path.join(into, name)
		try:
			text = open(src).read().strip() if src else ""
		except OSError:
			text = ""
		if text:
			with open(dst, "w") as f:
				f.write(HEADER.format(src=src, at=at) + text + "\n")
			wrote.append(name)
		elif os.path.exists(dst):
			os.remove(dst)  # ponytail: a mirror never outlives its source, or it becomes a rumour
	where = f"team {team.NAME}" if team.on() else config.MEMORY_DIR
	return (f"gitdashy: mirrored {', '.join(wrote) or 'nothing'} into {into}"
	        f" from {where}{' for ' + repo if repo else ''}{' · ' + team.ERROR if team.ERROR else ''}")
