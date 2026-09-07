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
	"""True when this machine has joined any team. ponytail: plural now — `joined()` is the real answer,
	and this stays because a dozen call sites only ever asked the yes/no."""
	return bool(joined())


def _git(*args, cwd):
	"""ponytail: same protection as a clone. These are the calls that run on every refresh tick, from the
	daemon thread — a pull that stops to ask for a credential would hang the dashboard with nothing on
	screen to say why, which is the whole reason _remote exists."""
	# ponytail: cwd is REQUIRED now. It used to default to the one team checkout, and with several there
	# is no such default — a silent wrong-directory git call is worse than a TypeError at the call site.
	return _remote(["git", "-C", cwd, *args], timeout=120)


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
	ponytail: each half is asked for SEPARATELY and only the missing one is supplied. Checking email
	alone meant a config with an email and no name — user.useConfigOnly, or an empty gecos field —
	got nothing, and the commit failed where the unconditional pair had worked. A fallback that only
	fires all-or-nothing is not a fallback for a half-configured machine.
	ponytail: `git config` reads config files. Local, bounded, and it cannot prompt.
	"""
	out = []
	for key, val in (("user.name", "gitdashy"), ("user.email", "gitdashy@localhost")):
		r = _git("config", key, cwd=d)
		if r.returncode != 0 or not r.stdout.strip():
			out += ["-c", f"{key}={val}"]
	return out


def inside_other_repo(d):
	"""True when `d` sits inside a git repo that is not `d` itself.

	ponytail: `git init` there would nest a repo inside someone's notes or dotfiles checkout, which
	surprises their tooling and is not ours to do. is_repo only looks for .git in the directory itself,
	so it cannot see this.
	"""
	r = _git("rev-parse", "--show-toplevel", cwd=d)
	top = r.stdout.strip()
	return r.returncode == 0 and bool(top) and os.path.realpath(top) != os.path.realpath(d)


_NO_HISTORY = {}  # d -> why. ponytail: cached, or every write pays a rev-parse forever — is_repo can
                  # never become true for a directory we have decided not to initialise.


def no_history(d):
	"""Why `d` has no history and will not get any, or "" when it has some or has not been tried."""
	return "" if is_repo(d) else _NO_HISTORY.get(d, "")


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
	if not d or not os.path.isdir(d):
		return False
	if d in _NO_HISTORY:
		return False
	if inside_other_repo(d):
		# ponytail: `git init ~` is a normal dotfiles setup, which makes the DEFAULT ~/.prs_memory
		# nested — so this is not the exotic case the docs framed it as. Recorded rather than
		# discarded, so the Memory row can say the net is off instead of it being silently absent.
		_NO_HISTORY[d] = "inside another git repo"
		return False
	with _lock:
		if _git("init", "-q", cwd=d).returncode != 0:
			_NO_HISTORY[d] = "git init failed"
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
	# ponytail: returns "" or WHY it did nothing. It used to return None on every path, so a commit that
	# failed was indistinguishable from one that was not needed. A dream emptied general.md, its commit
	# did not happen, and the deletion sat uncommitted until an unrelated review's push swept it in under
	# that review's message — so `git log` blamed a review for a dream's damage. A caller that is about
	# to destroy something has to be able to ask whether the record of it was actually written.
	if not is_repo(d):
		return "not a git checkout"
	with _lock:
		_git("add", "-A", cwd=d)
		if _git("diff", "--cached", "--quiet", cwd=d).returncode == 0:
			return ""  # nothing new
		if not _note(_git(*_ident(d), "commit", "-qm", msg, cwd=d), label):
			return ERROR or "commit failed"
	if not has_remote(d):
		return ""  # ponytail: committed, which is the half that protects you. Nothing to push to.
	with _lock:
		if not _note(_git("push", "-q", "-u", "origin", "HEAD", cwd=d), label):  # rejected: someone pushed first, merge and retry
			if not (_note(_git("pull", "--rebase", "-q", cwd=d), label) and _note(_git("push", "-q", cwd=d), label)):
				# ponytail: names the PUSH, with the last git message after it. ERROR alone was whichever
				# of the two failed, so a pull that failed reported itself as the push's reason.
				return f"push failed: {ERROR}" if ERROR else "push failed"  # committed locally, nothing lost
	return ""


def pull():
	"""ponytail: every joined team. One failing does not stop the rest — _note keeps the last reason."""
	for d in dirs():
		pull_dir(d)


def push(msg):
	"""Commit and push every joined team. Returns "" or the first reason one did not.

	ponytail: passes the reason up so a caller that deletes can check it — but NOT being in a team is
	the normal state, not a failure. Returning "not a git checkout" from here made the dream warn that
	memory was uncommitted every single time, on a machine with no team, which is a warning nobody would
	read twice.
	"""
	return next((e for e in (push_dir(d, msg) for d in dirs()) if e), "")


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


def dirname(slug):
	"""The directory name a team slug lives under. ponytail: the same __ convention as memory.slug()."""
	return (slug or "").replace("/", "__")


def slug_of_dir(name):
	""""owner__name" -> "owner/name"."""
	return (name or "").replace("__", "/")


def joined():
	"""[slug] for every team checkout on this machine, sorted. [] when none.

	ponytail: the FILESYSTEM is the registry, as it is for memory and the store — a directory that
	happens to hold a .git is a team, exactly as ~/.prs_team being a checkout was "team mode is on".
	No config file to fall out of step with what is actually on disk.
	"""
	try:
		names = sorted(os.listdir(config.TEAMS))
	except OSError:
		return []
	return [slug_of_dir(n) for n in names if is_repo(os.path.join(config.TEAMS, n))]


def dir_of(slug):
	"""The checkout for `slug`, "" when this machine has not joined it."""
	if not slug:
		return ""
	d = os.path.join(config.TEAMS, dirname(slug))
	return d if is_repo(d) else ""


def dirs():
	"""Every joined team's checkout directory."""
	return [os.path.join(config.TEAMS, dirname(s)) for s in joined()]


def log_of(slug):
	"""Where reviews of `slug`'s repos are logged, or your own log when slug is "".

	ponytail: log.LOG for yours, not config.LOCAL_LOG. They are the same path in a real run, and NOT
	the same under --demo or a test, both of which point log.LOG somewhere throwaway. Reading through
	the constant while everything else reads the module attr sent test writes to the real
	~/.prs_reviewed.jsonl — which is the one file in this system nobody would think to check.
	"""
	from . import log
	d = dir_of(slug)
	return os.path.join(d, "reviewed.jsonl") if d else log.LOG


def migrate():
	"""Move a pre-plural ~/.prs_team into ~/.prs_teams/<slug>/. Returns a one-line report, or "".

	ponytail: this is an automatic move of a directory holding somebody's unpushed work, at startup,
	inside curses — the class of operation that has destroyed data twice in this repo. So it refuses on
	everything it cannot prove safe rather than trying to cope: no slug to key it by, a destination that
	already exists, uncommitted or unpushed work, or a home it cannot create.
	ponytail: os.rename, never a copy-then-delete. A failure leaves the source exactly where it was,
	and there is no window in which the only copy is half-written.
	ponytail: never raises. It runs before the first draw; an exception here is a dashboard that never
	appears, over a directory the user could have moved by hand.
	"""
	src = config.TEAM
	if not is_repo(src) or not config.TEAMS:
		return ""  # nothing to migrate, which is every machine that installed after this
	slug = origin_slug(src)
	if not slug:
		return f"gitdashy: {src} has no origin, so it cannot be keyed by slug — move it by hand"
	dest = os.path.join(config.TEAMS, dirname(slug))
	if os.path.lexists(dest):
		return f"gitdashy: {dest} already exists — {src} was left alone"
	# ponytail: the same test knowledge.leave() uses before it deletes anything. A migration that moves
	# a checkout with unpushed reviews in it is a migration that can lose them if the move half-fails.
	from . import knowledge, memory
	ahead = knowledge.unpushed(src)
	if ahead != 0:
		return f"gitdashy: {src} has {ahead if ahead > 0 else 'possibly'} unpushed reviews — push them, then restart"
	memory.backup("migrate")  # ponytail: before, not after. Never raises; see memory.backup.
	try:
		os.makedirs(config.TEAMS, exist_ok=True)
		os.rename(src, dest)
	except OSError as e:
		return f"gitdashy: could not move {src} to {dest}: {e}"
	return f"gitdashy: moved your team checkout to {dest}"


def activate():
	"""Point log + memory at the team checkout. Called at startup and after setup()."""
	global NAME
	if not on():
		return
	# ponytail: the log is PER TEAM now — log.logs() reads yours plus every joined one and merges. There
	# is no single config.LOG to point somewhere, which is why this line went rather than moved.
	NAME = ", ".join(joined())  # ponytail: for the header only. Resolution goes through the slug.
	# ponytail: lazy, and only these two lines need it — bind and memory both import this module, so an
	# import at the top is a cycle. Seeding lives HERE, on the one function that says "a team is now
	# known", rather than at each of the six entry points that call it: a bootstrap only some callers
	# perform is the bootstrap that is missing on the path nobody tested.
	from . import bind, install, memory
	# ponytail: the review log AND the mirror registry. Seeding from the log alone missed the one route
	# that is not reviewing — a repo wired with `gitdashy init` and never reviewed stayed unbound, so
	# mirror._write resolved sources(repo) to yours alone and, because a mirror never outlives its
	# source, DELETED the general.md and repo.md already sitting in that repo on the next refresh tick.
	# ponytail: but only registry repos the team ALREADY HOLDS FACTS FOR. logged_repos() is disclosure-
	# neutral by construction — the team can see those names already. The registry is not: it is every
	# repo `gitdashy init` ever wired, personal side projects included, and binding one makes its facts
	# poolable and shareable. Seeding the whole registry fixed a deletion by GRANTING DISCLOSURE that
	# neither of the old rules gave, which is a fix carried past its reason. This set is exactly the old
	# disclosure test, and exactly the set whose repo.md the mirror would otherwise strip: a repo the
	# team has no facts about only ever had general.md mirrored, and dropping that IS the new rule.
	# ponytail: lazy, and install imports knowledge -> team, so a top-level import here is a cycle.
	# ponytail: per team, and only for teams that can be seeded unambiguously. With several joined, a
	# repo in one team's log is that team's; a repo in two is left alone rather than guessed at.
	for slug in joined():
		theirs = os.path.join(dir_of(slug), "memory")
		known = [r for _, r, *_ in install.registered() if r and os.path.exists(memory.path(r, theirs))]
		bind.seed(slug, sorted(memory.logged_repos(log_of(slug))) + known)


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
	# ponytail: the slug decides the directory, so a team is found by name rather than by being THE one.
	# It comes from the URL before the clone, because after it the directory has to already be right.
	slug = slug_of(repo) if "/" in repo else ""
	if not slug:
		return f"cannot tell an owner/name from {repo!r} — a team is keyed by its slug"
	dest = os.path.join(config.TEAMS, dirname(slug))
	if is_repo(dest):
		return f"already in {slug}"
	os.makedirs(config.TEAMS, exist_ok=True)
	err = clone(repo, dest)
	if err:
		return err
	union_attrs(dest)
	old_log = config.LOCAL_LOG
	activate()
	os.makedirs(os.path.join(dest, "memory"), exist_ok=True)
	seed_project(os.path.join(dest, "memory", "project.md"))
	config.LOG = log_of(slug)
	if os.path.isfile(old_log) and not os.path.exists(config.LOG):
		shutil.copy(old_log, config.LOG)  # the log is shared history; memory is not seeded, it is proposed
		# ponytail: again, because the log only exists NOW. activate() seeds bindings from it, and on a
		# first join it ran against a checkout that had none — so the repos you review would stay bound to
		# nothing until the next launch, which is the one session where you would notice and blame the join.
		activate()
	push("gitdashy: join " + (os.environ.get("USER") or "team"))
	return ERROR
