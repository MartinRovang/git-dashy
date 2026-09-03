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
CLONE = 300  # seconds a clone or repo-create may take before we give up on it


def _remote(cmd, timeout=None):
	"""Run a command that talks to a remote, and never let it wait on a human.

	ponytail: a URL to a private repo makes git ask for a password. Inside curses that prompt is invisible
	and blocks the whole dashboard forever, so prompts are off and the call is bounded — fail, don't hang.
	"""
	timeout = CLONE if timeout is None else timeout
	env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
	env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")  # keeps a user's own setting if they have one
	try:
		return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
	except subprocess.TimeoutExpired:  # ponytail: reason first — _note keeps the first 60 chars for the header
		return subprocess.CompletedProcess(cmd, 1, "", f"timed out after {timeout}s waiting on the remote")
	except OSError as e:
		return subprocess.CompletedProcess(cmd, 1, "", f"{e.strerror or e}: {cmd[0]}")


def is_repo(d):
	return bool(d) and os.path.isdir(os.path.join(d, ".git"))  # "" (demo) is never a repo


def on():
	return is_repo(config.TEAM)


def _git(*args, cwd=None):
	"""ponytail: same protection as a clone. These are the calls that run on every refresh tick, from the
	daemon thread — a pull that stops to ask for a credential would hang the dashboard with nothing on
	screen to say why, which is the whole reason _remote exists."""
	return _remote(["git", "-C", cwd or config.TEAM, *args], timeout=120)


def _note(r, label="sync"):
	global ERROR
	ERROR = "" if r.returncode == 0 else f"{label}: " + ((r.stderr or r.stdout).strip().splitlines() or ["git failed"])[-1][:60]
	return r.returncode == 0


def pull_dir(d, label="sync"):
	"""ponytail: git is the sync server for any checkout, not just the team's — the private one uses it too."""
	if is_repo(d):
		with _lock:
			_note(_git("pull", "--rebase", "-q", cwd=d), label)


def push_dir(d, msg, label="sync"):
	if not is_repo(d):
		return
	with _lock:
		_git("add", "-A", cwd=d)
		if _git("diff", "--cached", "--quiet", cwd=d).returncode == 0:
			return  # nothing new
		if not _note(_git("commit", "-qm", msg, cwd=d), label):
			return
		if not _note(_git("push", "-q", "-u", "origin", "HEAD", cwd=d), label):  # rejected: someone pushed first, merge and retry
			_note(_git("pull", "--rebase", "-q", cwd=d), label) and _note(_git("push", "-q", cwd=d), label)


def pull():
	pull_dir(config.TEAM)


def push(msg):
	push_dir(config.TEAM, msg)


def slug_of(url):
	"""owner/name from a remote URL, path or owner/name. "" when there is nothing to read."""
	u = (url or "").strip().rstrip("/").removesuffix(".git").replace(":", "/")
	return "/".join(u.split("/")[-2:]) if u else ""


def origin_slug(path):
	"""owner/name from the git remote at `path`, "" when there is no repo or no origin."""
	r = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=60)
	return slug_of(r.stdout) if r.returncode == 0 else ""


def activate():
	"""Point log + memory at the team checkout. Called at startup and after setup()."""
	global NAME
	if not on():
		return
	config.LOG = log.LOG = os.path.join(config.TEAM, "reviewed.jsonl")  # the review log really is shared
	NAME = origin_slug(config.TEAM)  # ponytail: MEMORY_DIR stays yours — memory.sources() reads both


def clone(repo, dest):
	"""Clone `repo` into `dest`: owner/name goes through gh, a path or URL through git. "" or an error."""
	local = os.path.isdir(repo) or "://" in repo or "@" in repo
	cmd = ["git", "clone", "-q", repo, dest] if local else ["gh", "repo", "clone", repo, dest]
	return "" if _note(_remote(cmd)) else ERROR


def union_attrs(dest):
	"""Make append-only files merge without conflicts, so two people writing at once never collide."""
	with open(os.path.join(dest, ".gitattributes"), "a+") as f:
		f.seek(0)
		if "merge=union" not in f.read():
			f.write("*.jsonl merge=union\n*.md merge=union\n")


def setup(repo, create=False):
	"""Clone (or create private + clone) the team repo, seed it with the local log. Returns '' or an error."""
	if create and not _note(_remote(["gh", "repo", "create", repo, "--private"])):
		return ERROR
	err = clone(repo, config.TEAM)
	if err:
		return err
	union_attrs(config.TEAM)
	old_log = log.LOG
	activate()
	os.makedirs(os.path.join(config.TEAM, "memory"), exist_ok=True)
	if os.path.isfile(old_log) and not os.path.exists(config.LOG):
		shutil.copy(old_log, config.LOG)  # the log is shared history; memory is not seeded, it is proposed
	push("gitdashy: join " + (os.environ.get("USER") or "team"))
	return ERROR
