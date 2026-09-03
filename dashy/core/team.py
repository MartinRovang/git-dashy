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


def has_remote(d):
	"""True when the checkout at `d` has an origin.

	ponytail: reads .git/config rather than spawning git. This is on the refresh tick now, and a
	subprocess per tick to learn something that changes about once in a checkout's life is waste —
	and `git remote get-url` does not go through _remote, so putting it on the tick path would have
	quietly broken the invariant that every git call there is bounded and cannot prompt.
	ponytail: a .git that is a FILE is a worktree or a submodule; fall back to asking git rather than
	guessing from a path that does not exist.
	"""
	g = os.path.join(d, ".git")
	if os.path.isdir(g):
		try:
			with open(os.path.join(g, "config")) as f:
				return '[remote "origin"]' in f.read()
		except OSError:
			return False  # a .git with no readable config is not something we can push to
	return bool(_url(d)) if os.path.exists(g) else False


def _ident(d):
	"""The `-c` identity pair, and ONLY when the machine has none of its own.

	ponytail: command-line -c has the highest precedence in git, so passing it unconditionally did not
	fall back to the user's identity, it REPLACED it. push_dir is how the shared team repo commits, so
	every shared fact and every reviewed.jsonl append landed as "gitdashy" for every member — pushed,
	and not rewritable afterwards. Attribution there is the whole point: who wrote a fact is who you go
	and ask about it.
	ponytail: `git config user.email` reads config files. Local, bounded, and it cannot prompt.
	"""
	r = _git("config", "user.email", cwd=d)
	if r.returncode == 0 and r.stdout.strip():
		return []
	return ["-c", "user.name=gitdashy", "-c", "user.email=gitdashy@localhost"]


def inside_other_repo(d):
	"""True when `d` sits inside a git repo that is not `d` itself.

	ponytail: `git init` there would nest a repo inside someone's notes or dotfiles checkout, which
	surprises their tooling and is not ours to do. is_repo only looks for .git in the directory itself,
	so it cannot see this.
	"""
	r = _git("rev-parse", "--show-toplevel", cwd=d)
	top = r.stdout.strip()
	return r.returncode == 0 and bool(top) and os.path.realpath(top) != os.path.realpath(d)


def init_history(d):
	"""Give `d` local git history, no remote needed. True when it has one. Never raises.

	ponytail: memory is the one thing here that cannot be recreated — a dream rewrites every file and
	deletes any the model returned empty, and on a default install ~/.prs_memory was a plain directory
	with no history, no remote and no snapshot. `git init` costs nothing and makes every write in the
	system undoable with commands the user already knows.
	ponytail: identity comes from -c, not from their global config. A machine that has never set
	user.email would otherwise fail to commit, which is exactly the machine with no other backup.
	"""
	if is_repo(d):
		return True
	if not d or not os.path.isdir(d) or inside_other_repo(d):
		return False
	with _lock:
		if _git("init", "-q", cwd=d).returncode != 0:
			return False
		_git("add", "-A", cwd=d)
		_git(*_ident(d), "commit", "-qm", "gitdashy: memory as it was before this was tracked", cwd=d)
	return is_repo(d)


def pull_dir(d, label="sync"):
	"""ponytail: git is the sync server for any checkout, not just the team's — the private one uses it too."""
	if is_repo(d) and has_remote(d):  # ponytail: local-only history has nothing to pull and no error to show
		with _lock:
			_note(_git("pull", "--rebase", "-q", cwd=d), label)


def push_dir(d, msg, label="sync"):
	"""Commit, and push when there is somewhere to push to.

	ponytail: the commit is the point, not the push. A memory dir with local-only history must record
	every change — that is what makes a bad dream recoverable — and a missing origin is not an error to
	put on the header, it is the normal state of a machine that has not joined anything.
	"""
	if not is_repo(d):
		return
	with _lock:
		_git("add", "-A", cwd=d)
		if _git("diff", "--cached", "--quiet", cwd=d).returncode == 0:
			return  # nothing new
		if not _note(_git(*_ident(d), "commit", "-qm", msg, cwd=d), label):
			return
	if not has_remote(d):
		return  # ponytail: committed, which is the half that protects you. Nothing to push to.
	with _lock:
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


def host_of(url):
	"""The host a remote URL names, "" for a bare owner/name or a local path."""
	u = (url or "").strip().removeprefix("ssh://").removeprefix("https://").removeprefix("http://")
	u = u.split("@")[-1]
	head = u.replace(":", "/").split("/")[0]
	return head.lower() if "." in head else ""


def same_remote(a, b):
	"""Whether two remotes name the same repository.

	ponytail: owner/name alone is not enough — gitlab.com/org/mem and github.com/org/mem share it. Hosts
	are compared when both carry one, so an ssh URL still matches its own https form.
	"""
	if not slug_of(a) or slug_of(a) != slug_of(b):
		return False
	ha, hb = host_of(a), host_of(b)
	return not ha or not hb or ha == hb


def _url(path):
	"""The origin URL at `path`, "" when there is none. ponytail: asked in passing, so it never raises."""
	try:
		r = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
		                   capture_output=True, text=True, timeout=60)
	except (subprocess.TimeoutExpired, OSError):
		return ""
	return r.stdout.strip() if r.returncode == 0 else ""


def origin_slug(path):
	"""owner/name from the git remote at `path`, "" when there is no repo or no origin."""
	return slug_of(_url(path))


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


def is_own_memory(repo):
	"""Whether `repo` names the directory your memory lives in, or the remote it pushes to.

	ponytail: the mirror of knowledge.adopt's guard — the two must never be the same place, whichever
	you happen to set up second. Your memory holds drafts and is pushed; the team must never receive them.
	"""
	if os.path.isdir(repo) and os.path.realpath(repo) == os.path.realpath(config.MEMORY_DIR):
		return True
	return is_repo(config.MEMORY_DIR) and same_remote(repo, _url(config.MEMORY_DIR))

PROJECT_TEMPLATE = """# What we are building

Fill this in once, together. Everyone who joins this team reads it, and so does every review —
so a reviewer knows what the code is for before it judges whether a change serves it.

Keep it short. This is intent, not documentation: the things that would change a verdict.

## The project

What it is, and who uses it.

## Why it matters

The outcome that makes the work worth doing.

## Constraints that change decisions

Regulatory, contractual, performance, compatibility — anything with real consequences for
what is acceptable, not just what is tidy.

## How this codebase is shaped

The handful of structural facts a newcomer would otherwise learn the hard way.
"""


def seed_project(path):
	"""Give a new team repo a brief to fill in. ponytail: never overwrite — theirs is the real one."""
	if not os.path.exists(path):
		with open(path, "w") as f:
			f.write(PROJECT_TEMPLATE)


def setup(repo, create=False):
	"""Clone (or create private + clone) the team repo, seed it with the local log. Returns '' or an error."""
	if create and not _note(_remote(["gh", "repo", "create", repo, "--private"])):
		return ERROR
	if is_own_memory(repo):
		return "that is your own memory directory, which holds drafts — use a different repo for the team"
	err = clone(repo, config.TEAM)
	if err:
		return err
	union_attrs(config.TEAM)
	old_log = log.LOG
	activate()
	os.makedirs(os.path.join(config.TEAM, "memory"), exist_ok=True)
	seed_project(os.path.join(config.TEAM, "memory", "project.md"))
	if os.path.isfile(old_log) and not os.path.exists(config.LOG):
		shutil.copy(old_log, config.LOG)  # the log is shared history; memory is not seeded, it is proposed
	push("gitdashy: join " + (os.environ.get("USER") or "team"))
	return ERROR
