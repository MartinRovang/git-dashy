"""Where knowledge is read and written: the local memory dir and the team checkout.

ponytail: the filesystem carries the setting, not a config file. Pointing memory somewhere new
replaces the dir with a symlink to it — the same trick as team mode, which is "on" because
~/.prs_team happens to contain a .git. Env vars still win; they are set before we ever run.
"""
import os
import shutil
import subprocess

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


def store_moved():
	"""True when the team checkout is not where it would be by default — only then is it worth a header row."""
	return os.path.islink(config.TEAM) or config.TEAM != DEFAULT_STORE


def effective():
	"""The memory dir reviews actually read and write right now. Team mode has already repointed it."""
	return config.MEMORY_DIR


def inside_git(path):
	"""True when memory written at `path` would land in a repo that does not ignore it.

	ponytail: asked before the directory is created, so a typo does not leave a stray dir behind.
	"""
	return mirror.tracked(os.path.abspath(os.path.expanduser(path)))


def repoint(path, new, env):
	"""Make `path` a symlink to `new`, moving what is already there. Returns "" or an error string."""
	if os.environ.get(env):
		return f"{env} is set in the environment; unset it to change this here"
	new = os.path.abspath(os.path.expanduser(new))
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


def set_local(new):
	"""Point the solo memory dir at `new`. Returns "" or an error string."""
	err = repoint(config.LOCAL_MEMORY, new, "PRS_MEMORY")
	if not err and not team.on():
		config.MEMORY_DIR = config.LOCAL_MEMORY  # solo: the effective dir is the one we just repointed
	return err


def set_store(new):
	"""Point the team checkout dir at `new`. Returns "" or an error string."""
	if team.on():
		return "leave the team first — the refresh thread is pulling in that folder"
	return repoint(config.TEAM, new, "PRS_TEAM")


def unpushed():
	"""How many commits the team checkout has that its remote does not. -1 when that cannot be told."""
	r = subprocess.run(["git", "-C", config.TEAM, "log", "--oneline", "@{u}..HEAD"],
	                   capture_output=True, text=True, timeout=60)
	return len(r.stdout.strip().splitlines()) if r.returncode == 0 else -1


def leave():
	"""Drop the team checkout and go back to solo memory. Returns "" or an error string."""
	if not team.on():
		return "not in a team"
	ahead = unpushed()
	if ahead != 0:  # ponytail: -1 (no upstream, no git) is also "do not delete" — the log may exist only here
		return f"{config.TEAM} has {ahead if ahead > 0 else 'possibly'} unpushed reviews; push them first"
	try:
		shutil.rmtree(os.path.realpath(config.TEAM))
		if os.path.islink(config.TEAM):
			os.remove(config.TEAM)
	except OSError as e:
		return str(e)
	config.MEMORY_DIR = config.LOCAL_MEMORY
	config.LOG = log.LOG = config.LOCAL_LOG
	team.NAME = team.ERROR = ""
	return ""
