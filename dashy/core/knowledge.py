"""Where knowledge is read and written: the local memory dir and the team checkout.

ponytail: the filesystem carries the setting, not a config file. Pointing memory somewhere new
replaces the dir with a symlink to it — the same trick as team mode, which is "on" because
~/.prs_team happens to contain a .git. Env vars still win; they are set before we ever run.
"""
import os
import re
import shutil
import subprocess
import time

from .. import config
from . import log, mirror, team

DEFAULT_STORE = os.path.expanduser("~/.prs_team")


def tilde(path):
	home = os.path.expanduser("~")
	return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def show(path):
	"""`path` for the header: ~-shortened, and "→ target" when it is a symlink pointing elsewhere."""
	target = os.path.realpath(path) if os.path.islink(path) else ""
	return tilde(path) + (f" → {tilde(target)}" if target and target != path else "")


def history_note():
	"""" · no history (why)" for the Memory row, or "".

	ponytail: on the row rather than announced once. A safety net that is OFF should say so every time
	you look at it — a message you scrolled past is indistinguishable from never having been told.
	"""
	why = team.no_history(config.MEMORY_DIR)
	return f" · no history ({why})" if why else ""


def store_moved():
	"""True when the team checkout is not where it would be by default — only then is it worth a header row."""
	return os.path.islink(config.TEAM) or config.TEAM != DEFAULT_STORE


def effective():
	"""The memory dir your own facts live in. ponytail: team mode does NOT repoint this any more — the
	team is a second source that memory.sources() reads alongside, not a replacement for yours."""
	return config.MEMORY_DIR


def is_remote(s):
	"""True when `s` names a git remote rather than a local directory.

	ponytail: an existing directory always wins, so a real path is never mistaken for a repo. `./x/y`
	is a path because of the dot; a bare `x/y` that does not exist is read as owner/name, as T does.
	"""
	s = s.strip()
	if not s or os.path.isdir(os.path.expanduser(s)):
		return False
	# ponytail: the owner half may not START with a dot, or "./notes" matched owner/name and gitdashy
	# went to clone it from GitHub. The docstring above had claimed the dot made it a path since the
	# day it was written; nothing implemented that, and nothing checked.
	return bool("://" in s or re.match(r"^[^@/\s]+@[^:/\s]+:", s) or re.match(r"^[\w-][\w.-]*/[\w.-]+$", s))


def abspath(path):
	"""os.path.abspath, but it cannot raise. ponytail: a relative path needs the cwd, and a cwd that
	has been deleted makes getcwd() throw — which, inside curses, used to take the whole dashboard down."""
	try:
		return os.path.abspath(os.path.expanduser(path))
	except OSError:
		return os.path.expanduser(path)


def inside_git(path):
	"""True when memory written at `path` would land in a repo that does not ignore it.

	ponytail: asked before the directory is created, so a typo does not leave a stray dir behind.
	"""
	try:
		return mirror.tracked(abspath(path))
	except OSError:
		return False  # cannot even resolve it; whatever writes next will say why, more clearly than this


def repoint(path, new, env):
	"""Make `path` a symlink to `new`, moving what is already there. Returns "" or an error string."""
	if os.environ.get(env):
		return f"{env} is set in the environment; unset it to change this here"
	new = abspath(new)
	if os.path.realpath(path) == new:
		return ""
	if os.path.lexists(new) and not os.path.isdir(new):
		return f"{tilde(new)} exists and is not a directory"
	try:
		os.makedirs(new, exist_ok=True)
		if os.path.islink(path):
			os.remove(path)  # only a pointer; there is nothing under it to move
		elif os.path.isdir(path):
			for name in os.listdir(path):
				if not os.path.lexists(os.path.join(new, name)):
					shutil.move(os.path.join(path, name), os.path.join(new, name))
			if os.listdir(path):  # ponytail: same name on both sides — refuse rather than pick a winner
				return f"{tilde(path)} still holds files that also exist in {tilde(new)}; merge them by hand"
			os.rmdir(path)
		elif os.path.lexists(path):
			return f"{tilde(path)} exists and is not a directory"
		os.symlink(new, path)
	except OSError as e:
		return str(e)
	return ""


def adopt(url, dest=None):
	"""Make your memory directory a checkout of `url`, keeping the facts already in it. "" or an error.

	ponytail: clone to a sibling, move what is there across, then swap. Cloning straight in is not an
	option — git wants an empty directory, and yours holds the facts you are trying to keep.
	"""
	dest = dest or config.LOCAL_MEMORY
	if os.environ.get("PRS_MEMORY"):
		return "PRS_MEMORY is set in the environment; unset it to change this here"
	# ponytail: local-only history is not "already a checkout" — every memory dir has it now, since it is
	# what makes a bad dream recoverable. Only a checkout with an ORIGIN is already pointed somewhere.
	if team.is_repo(dest) and team.has_remote(dest):
		return f"{tilde(dest)} is already a checkout of {team._url(dest)}"
	if os.path.islink(dest):
		return f"{tilde(dest)} points at {tilde(os.path.realpath(dest))}; point it back to a plain directory first"
	# ponytail: your memory dir gets pushed, and it holds drafts/. Making it the TEAM repo would publish
	# every unconfirmed guess to everyone — the one thing the whole design promises never happens.
	if url and team.on() and team.same_remote(url, team._url(config.TEAM)):
		return "that is the team repo — your memory holds drafts, which are yours alone. Use a different one."
	keep = sorted(n for n in os.listdir(dest) if n != ".git") if os.path.isdir(dest) else []
	tmp = dest + ".incoming"
	shutil.rmtree(tmp, ignore_errors=True)
	err = team.clone(url, tmp)
	if err:
		shutil.rmtree(tmp, ignore_errors=True)
		return err
	# ponytail: EVERY collision is found before ANYTHING moves. Checking inside the move loop meant a
	# clash on the third name rmtree'd a tmp that already held the first two — your facts and your
	# drafts/, deleted, while the message said "merge it by hand" as though nothing had happened.
	# Sorted order made it the likely path, not an exotic one: a memory repo has a general.md, and
	# "general.md" sorts after "acme__api.md" and "drafts".
	clash = [n for n in keep if os.path.lexists(os.path.join(tmp, n))]
	if clash:
		shutil.rmtree(tmp, ignore_errors=True)  # safe here, and only here: nothing of yours is in it yet
		return f"{', '.join(clash)} exists in both {tilde(dest)} and the repo; merge it by hand"
	moved, old_git = [], ""
	try:
		for name in keep:
			shutil.move(os.path.join(dest, name), os.path.join(tmp, name))
			moved.append(name)
		if os.path.isdir(os.path.join(dest, ".git")):
			# ponytail: your local history has a different root than the repo you are adopting, so it
			# cannot be merged in — but it is the record of everything before today and it is not ours
			# to delete. It goes to a sibling with a name that never collides, and stays until you
			# remove it. `git -C <that> log` still reads it.
			old_git = f"{dest}.local-history-{int(time.time())}"
			shutil.move(os.path.join(dest, ".git"), old_git)
		if os.path.isdir(dest):
			os.rmdir(dest)
		os.rename(tmp, dest)
	except OSError as e:
		# ponytail: put back what moved. A half-moved memory dir is the same loss by a slower route —
		# the files exist, but nothing reads them from there and nothing says where they went.
		os.makedirs(dest, exist_ok=True)
		if old_git and os.path.isdir(old_git):
			try:
				shutil.move(old_git, os.path.join(dest, ".git"))
			except OSError:
				pass
		for name in reversed(moved):
			try:
				shutil.move(os.path.join(tmp, name), os.path.join(dest, name))
			except OSError:
				pass
		return str(e)
	team.union_attrs(dest)
	team.push_dir(dest, "gitdashy: memory from " + os.uname().nodename, "mine")
	return ""


def set_local(new):
	"""Point the solo memory dir at `new`. Returns "" or an error string."""
	err = repoint(config.LOCAL_MEMORY, new, "PRS_MEMORY")
	if not err:
		config.MEMORY_DIR = config.LOCAL_MEMORY  # ponytail: always yours now, in a team or not
	return err


def set_store(new):
	"""Point the team checkout dir at `new`. Returns "" or an error string."""
	if team.on():
		return "leave the team first — the refresh thread is pulling in that folder"
	return repoint(config.TEAM, new, "PRS_TEAM")


def unpushed():
	"""Work in the team checkout the remote does not have. -1 when that cannot be told.

	ponytail: commits ahead AND a dirty tree. A push that failed earlier — no git identity configured,
	say — leaves files staged but uncommitted, which is zero commits ahead and still someone's work.
	leave() deletes this directory, so the question has to be "is anything here unsaved", not "how many
	commits".
	"""
	r = subprocess.run(["git", "-C", config.TEAM, "log", "--oneline", "@{u}..HEAD"],
	                   capture_output=True, text=True, timeout=60)
	if r.returncode != 0:
		return -1
	dirty = subprocess.run(["git", "-C", config.TEAM, "status", "--porcelain"],
	                       capture_output=True, text=True, timeout=60)
	if dirty.returncode != 0 or dirty.stdout.strip():
		return -1  # uncommitted work is as unsaved as an unpushed commit, and we cannot count it
	return len(r.stdout.strip().splitlines())


def rmtree_owned(path):
	"""Delete a directory this program created. Returns "" or an error string.

	ponytail: rmtree is `rm -rf` with no confirmation and no trash, so it gets a gate rather than a
	comment. Three refusals, each for a way the path can stop being the thing we think we own:
	a symlink (deleting what it POINTS AT is never what "remove this directory" meant), something
	that is not a directory, and a path shallow enough to be a home or a filesystem root.
	ponytail: errors come back instead of being ignored. A half-deleted tree is precisely the state
	that poisons the next run, and ignore_errors=True is what stops anyone finding out.
	"""
	real = os.path.realpath(path)
	if os.path.islink(path):
		# ponytail: says refusing, because it refuses. The old wording described an action it never took,
		# which is the kind of message that gets believed by the first caller to reach it.
		return f"refusing: {tilde(path)} is a link to {tilde(real)}; remove the link if that is what you meant"
	if not os.path.isdir(path):
		return "" if not os.path.lexists(path) else f"{tilde(path)} is not a directory"
	if real in (os.sep, os.path.realpath(os.path.expanduser("~"))) or os.path.dirname(real) == real:
		return f"refusing to delete {tilde(real)}"
	try:
		shutil.rmtree(path)
	except OSError as e:
		return str(e)
	return ""


def leave():
	"""Drop the team checkout and go back to solo memory. Returns "" or an error string."""
	if not team.on():
		return "not in a team"
	ahead = unpushed()
	if ahead != 0:  # ponytail: -1 (no upstream, no git) is also "do not delete" — the log may exist only here
		return f"{config.TEAM} has {ahead if ahead > 0 else 'possibly'} unpushed reviews; push them first"
	# ponytail: a symlinked TEAM used to be resolved with realpath and deleted at the far end. If you
	# pointed it at a checkout you actually work in, "leave the team" deleted that repo. The link is
	# ours to remove; what it points at is yours, and it is said out loud rather than silently kept.
	if os.path.islink(config.TEAM):
		try:
			os.remove(config.TEAM)
		except OSError as e:
			return str(e)
	else:
		err = rmtree_owned(config.TEAM)
		if err:
			return err
	config.LOG = log.LOG = config.LOCAL_LOG  # MEMORY_DIR never moved, so there is nothing to move back
	team.NAME = team.ERROR = ""
	return ""
