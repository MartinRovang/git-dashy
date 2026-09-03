"""Team sync: log + memory live in a git checkout (~/.prs_team) that everyone pushes to.
ponytail: git is the sync server. Appends merge with the union driver, so parallel reviews never conflict."""
import os
import shutil
import subprocess
import threading

from .. import config
from . import log

ERROR = ""  # last git failure, shown in the header until the next success
NAME = ""  # owner/name of the team repo, for the stats strip
_lock = threading.Lock()  # review threads push concurrently; git wants one writer


def on():
	return bool(config.TEAM) and os.path.isdir(os.path.join(config.TEAM, ".git"))  # "" (demo) is never a team


def _git(*args, cwd=None):
	return subprocess.run(["git", "-C", cwd or config.TEAM, *args], capture_output=True, text=True, timeout=120)


def _note(r):
	global ERROR
	ERROR = "" if r.returncode == 0 else "sync: " + ((r.stderr or r.stdout).strip().splitlines() or ["git failed"])[-1][:60]
	return r.returncode == 0


def pull():
	if on():
		with _lock:
			_note(_git("pull", "--rebase", "-q"))


def push(msg):
	if not on():
		return
	with _lock:
		_git("add", "-A")
		if _git("diff", "--cached", "--quiet").returncode == 0:
			return  # nothing new
		if not _note(_git("commit", "-qm", msg)):
			return
		if not _note(_git("push", "-q", "-u", "origin", "HEAD")):  # rejected: someone pushed first, merge and retry
			_note(_git("pull", "--rebase", "-q")) and _note(_git("push", "-q"))


def origin_slug(path):
	"""owner/name from the git remote at `path`, "" when there is no repo or no origin."""
	r = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=60)
	url = r.stdout.strip().rstrip("/").removesuffix(".git").replace(":", "/")
	return "/".join(url.split("/")[-2:]) if r.returncode == 0 and url else ""


def activate():
	"""Point log + memory at the team checkout. Called at startup and after setup()."""
	global NAME
	if not on():
		return
	config.LOG = log.LOG = os.path.join(config.TEAM, "reviewed.jsonl")
	config.MEMORY_DIR = os.path.join(config.TEAM, "memory")
	NAME = origin_slug(config.TEAM)


def setup(repo, create=False):
	"""Clone (or create private + clone) the team repo, seed it with local log/memory. Returns '' or an error."""
	if create and not _note(subprocess.run(["gh", "repo", "create", repo, "--private"], capture_output=True, text=True)):
		return ERROR
	local = os.path.isdir(repo) or "://" in repo or "@" in repo
	cmd = ["git", "clone", "-q", repo, config.TEAM] if local else ["gh", "repo", "clone", repo, config.TEAM]
	if not _note(subprocess.run(cmd, capture_output=True, text=True)):
		return ERROR
	with open(os.path.join(config.TEAM, ".gitattributes"), "a+") as f:
		f.seek(0)
		if "merge=union" not in f.read():
			f.write("*.jsonl merge=union\n*.md merge=union\n")
	old_log, old_mem = log.LOG, config.MEMORY_DIR
	activate()
	if os.path.isfile(old_log) and not os.path.exists(config.LOG):
		shutil.copy(old_log, config.LOG)
	if os.path.isdir(old_mem) and not os.path.exists(config.MEMORY_DIR):
		shutil.copytree(old_mem, config.MEMORY_DIR)
	push("gitdashy: join " + (os.environ.get("USER") or "team"))
	return ERROR
