"""Team sync: log + memory live in a git checkout (~/.prs_team) that everyone pushes to.
ponytail: git is the sync server. Appends merge with the union driver, so parallel reviews never conflict."""
import json
import os
import re
import shutil
import subprocess
import threading

from .. import config
from . import log

ERROR = ""  # last git failure, shown in the header until the next success
NAME = ""  # the joined team keys, comma-joined, for the header strip only — resolution goes by key
_lock = threading.Lock()  # review threads push concurrently; git wants one writer
CLONE = 300  # seconds a clone or repo-create may take before we give up on it
BRANCH = "main"  # the branch a team gitdashy STARTS uses; a team it clones keeps its own


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


INFO = "team.json"  # inside the checkout: the team's own name and description, shared with everyone


def dirname(key):
	"""The directory a team key lives in. ponytail: keys are already filesystem-safe; this is identity."""
	return key or ""


def key_of(name):
	"""A stable key from a team's name: lowercase, one dash between words. "" when there is no name.

	ponytail: the key is fixed when the team is created and never derived from a remote again. A team
	is local today and gets a git URL tomorrow — that was the exact moment an origin-derived slug broke,
	because bindings point at the team and the team had no identity until it was hosted somewhere. The
	NAME is the identity; the location is a separate, changeable fact.
	ponytail: the display name lives in team.json and can be edited freely, because it is not this.
	"""
	out = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
	return out[:64]


def info(key):
	"""{"name", "description"} the team says about itself, from its own checkout. Falls back to the key.

	ponytail: read from the TEAM, not from local config, so everyone who clones it sees the same name
	and the same description. A checkout written before this file existed still reads — the key is the
	fallback, and the key is what everything resolves by anyway.
	"""
	d = dir_of(key)
	try:
		with open(os.path.join(d, INFO)) as f:
			got = json.load(f)
		if isinstance(got, dict):
			return {"name": _clean(got.get("name"), key), "description": _clean(got.get("description"), "")}
	except (OSError, ValueError):
		pass
	return {"name": key, "description": ""}


def _clean(v, fallback):
	"""One line of printable text, clipped. For anything read out of a team's own files.

	ponytail: team.json comes from a CLONED repo, so anyone with push access to the team writes it, and
	it lands in the curses header and the CLI listing. A newline or a control byte there is theirs to
	choose and mine to refuse — this is the chokepoint every reader goes through.
	"""
	t = "".join(c for c in str(v or "") if c.isprintable()).strip()
	return t[:120] or fallback


def write_info(key, name, description):
	"""Record what the team calls itself. Returns "" or why it could not."""
	if not (d := dir_of(key)):
		return f"not in {key}"
	try:
		with open(os.path.join(d, INFO), "w") as f:
			json.dump({"name": name or key, "description": description or ""}, f, indent=1)
			f.write("\n")
	except OSError as e:
		return str(e)
	return ""


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
	# ponytail: a dotted name is never a team. setup() clones into TEAMS/.joining, and the moment git
	# creates its .git the refresh thread would walk into a half-cloned checkout and pull inside it.
	return [n for n in names if not n.startswith(".") and is_repo(os.path.join(config.TEAMS, n))]


def dir_of(slug):
	"""The checkout for `slug`, "" when this machine has not joined it.

	ponytail: folded, as bind.team_dir already is. Everything above this — info, write_info, connect,
	knowledge.leave, the CLI's --team — answered "not in Org-Mem" about a team you were in, because
	only the resolver had been taught that a key is typed. Folding here covers all of them at once.
	"""
	if not slug:
		return ""
	want = slug.lower()
	for n in joined():
		if n.lower() == want:
			return os.path.join(config.TEAMS, n)
	return ""


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
	# ponytail: the old layout had no name of its own, so the origin is the only thing that can name it
	# — through key_of, because a key is a directory name and owner/name has a slash in it.
	was = origin_slug(src)  # ponytail: EXACTLY what the shipped version wrote into every binding
	key = key_of(was.replace("/", "-"))
	if not key:
		return f"gitdashy: {src} has no origin to name it by — move it into {config.TEAMS} by hand"
	dest = os.path.join(config.TEAMS, dirname(key))
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
	# ponytail: and carry the BINDINGS. The shipped version keyed them on origin_slug — "owner/name",
	# with a slash — and the directory is now "owner-name", so every one of them resolved to nothing:
	# the team's facts stopped being read, brief() said "not in team owner/name" about the team you
	# were in, and pooling and sharing went quiet, with nothing on screen to say so. seed() cannot
	# repair it either, because every bound repo is already in `touched`.
	# ponytail: a rewrite rather than teaching team_dir to also match the old shape — one migration
	# that ends, instead of a fallback that lives in the resolver forever.
	from . import bind
	moved = 0
	for repo, t in bind.bindings().items():
		if t.lower() == was.lower():
			bind.bind(repo, key)
			moved += 1
	for owner, t in bind.owners().items():
		if t.lower() == was.lower():
			bind.bind_owner(owner, key)
			moved += 1
	note = f", and repointed {moved} binding{'' if moved == 1 else 's'}" if moved else ""
	return f"gitdashy: moved your team checkout to {dest}{note}"


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


def looks_local(repo):
	"""True when `repo` names a place on this machine rather than a repo on GitHub.

	ponytail: an existing directory always wins; beyond that a leading /, ./, ../ or ~ makes it a path
	whether or not it exists yet. `os.path.isdir` alone meant a path you were about to CREATE was read
	as owner/name and handed to gh, which answered "Could not resolve to a Repository" — the same
	defect knowledge.is_remote was fixed for, in the function beside it, left unswept.
	"""
	repo = (repo or "").strip()
	return bool(repo) and (os.path.isdir(os.path.expanduser(repo))
	                       or repo.startswith(("/", "./", "../", "~")))


FOOTER = 66  # ponytail: confirm() wraps a message as " {err}  [any key]" and draw() hard-clips the
             # footer at w - 1, so 66 is what survives an 80-column terminal. Not a guess: the fix this
             # replaces was 181 characters and its advice fell off the end of the line.


def bare_url(url):
	"""A URL with any user:password stripped out of the authority. "" for anything that is not one.

	ponytail: `https://x-token:ghp_…@host/o/r.git` is a legitimate remote and it reaches every message
	here. git redacts the password in its own fatal; we were printing it verbatim to the footer and to
	CLI scrollback, and splicing it into the ssh form we suggested. A credential is not an error detail.
	"""
	scheme, sep, rest = (url or "").strip().partition("://")
	if not sep or scheme not in ("http", "https"):
		return ""
	authority, _, path = rest.partition("/")
	return f"{scheme}://{authority.rpartition('@')[2]}/{path}" if path else ""


def ssh_form(url):
	"""The ssh form of an http(s) URL — "https://host/a/b(.git)" -> "git@host:a/b.git". "" if not one.

	ponytail: host-agnostic on purpose. This is not a GitHub fact; every git host offers both forms,
	and ssh is the one that authenticates from an agent with nothing else configured.
	"""
	if not (u := bare_url(url)):
		return ""
	host, _, path = u.partition("://")[2].partition("/")
	return f"git@{host}:{path.rstrip('/').removesuffix('.git')}.git" if host and path else ""


def clone(repo, dest):
	"""Clone `repo` into `dest`. Any git URL, a path, or owner/name on GitHub. "" or an error."""
	local = looks_local(repo) or "://" in repo or "@" in repo  # a path, a URL or an ssh remote: pass it through
	# ponytail: git clone, whatever it is. `gh repo clone` was here so that a bare owner/name would
	# work, which quietly made GitHub the only host a team could live on — and a team is just a repo
	# people can reach. A bare owner/name is now expanded to a GitHub URL as a CONVENIENCE, and any
	# other URL, ssh remote or path goes straight through untouched.
	url = repo if local else f"https://github.com/{repo}.git"
	if _note(_remote(["git", "clone", "-q", url, dest]), "join"):
		return ""
	# ponytail: git cannot ask. GIT_TERMINAL_PROMPT=0 is deliberate — a credential prompt inside curses
	# is invisible and hangs the dashboard — so an https URL to a PRIVATE repo fails outright on a
	# machine with no credential helper. It used to work because `gh repo clone` carried gh's own
	# token; dropping gh took that with it. The answer is not to reach for a host's CLI again: it is
	# ssh, which every host speaks and which authenticates from an agent already loaded.
	# ponytail: "could not read " covers Username AND Password — git says the second when the URL
	# carries a user, which is the same failure with the same remedy.
	if "could not read " in ERROR or "Authentication failed" in ERROR:
		say = _credential_hint(url)
		# ponytail: the GLOBAL too. The friendly string was only the return value, so after the popup
		# was dismissed the T row went on painting the clipped fatal — the very string this replaces —
		# until the next successful sync.
		globals()["ERROR"] = f"join: {say}"
		return say
	return ERROR


def _credential_hint(url):
	"""One line, short enough to survive the footer, that says what to do about a missing credential."""
	alt = ssh_form(url)
	if not alt:
		# ponytail: already ssh, so there is no other form to suggest — and the answer is different.
		# GIT_SSH_COMMAND carries -oBatchMode=yes, so a key with a passphrase and no agent fails here
		# exactly as an https URL with no helper does, and "check your agent" is the actual remedy.
		return f"no credential for {host_of(url) or 'that remote'} — is your ssh agent loaded?"[:FOOTER]
	# ponytail: the action alone when the whole sentence will not fit. A reason that pushes the remedy
	# off the end of the line is worse than no reason — that is the bug this is fixing.
	full = f"no credential — try {alt}"
	return full if len(full) <= FOOTER else f"try {alt}"[:FOOTER]


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


def _undo(dest):
	"""Remove a team directory or link we just created and could not finish. Never raises."""
	try:
		if os.path.islink(dest):
			os.remove(dest)          # ponytail: the LINK, never what it points at — that is the user's
		elif os.path.isdir(dest):
			shutil.rmtree(dest, ignore_errors=True)
	except OSError:
		pass


def start(name, description="", at=""):
	"""Start a team here: a checkout, a name, a description. No remote, no host. "" or an error.

	ponytail: nothing external is involved. A team is a place people can reach that pools what reviews
	learn; git is the only technology it needs, and a remote is something you add when you have one —
	`connect()`. Requiring a repo to exist first meant the first person on a team was stuck.
	ponytail: `at` symlinks rather than copies, so a team kept on a shared drive, or inside a repo you
	already have, stays where it is. The key still names the link, because the key is the identity.
	"""
	if not (key := key_of(name)):
		return "a team needs a name"
	dest = os.path.join(config.TEAMS, dirname(key))
	if os.path.lexists(dest):
		return f"already in {key}" if is_repo(dest) else f"{dest} exists and is not a team"
	try:
		os.makedirs(config.TEAMS, exist_ok=True)
		if at:
			at = os.path.abspath(os.path.expanduser(at))
			# ponytail: an existing checkout is somebody's repo, and write_info would overwrite its
			# team.json with this name. Joining one is `teams --join`; this makes a new team.
			if is_repo(at):
				return f"{at} is already a git checkout — join it with `teams --join` instead"
			os.makedirs(at, exist_ok=True)
			os.symlink(at, dest)
		else:
			os.makedirs(dest)
		os.makedirs(os.path.join(dest, "memory"), exist_ok=True)
	except OSError as e:
		_undo(dest)
		return str(e)
	if not is_repo(dest):
		if _git("init", "-q", cwd=dest).returncode != 0:
			# ponytail: take the link back out. It is not a repo so joined() skips it, but lexists() is
			# true — so retrying the same name answered "exists and is not a team" forever and the user
			# had to clean up by hand, after an error that said nothing about a leftover.
			_undo(dest)
			return f"could not git init {dest}"
		# ponytail: PIN the branch. `git init` uses init.defaultBranch, which is "main" on one machine
		# and "master" on the next — so two people starting or connecting the same team push branches
		# that never meet, and a clone of a repo whose HEAD names the other one comes back EMPTY. Found
		# by CI, which has no global git config where mine says main; symbolic-ref rather than `init -b`
		# because it needs no minimum git version.
		_git("symbolic-ref", "HEAD", "refs/heads/" + BRANCH, cwd=dest)
	union_attrs(dest)
	write_info(key, name, description)
	seed_project(os.path.join(dest, "memory", "project.md"))
	push_dir(dest, "gitdashy: new team " + name, "join")
	return ""


def connect(key, url):
	"""Give a local team a remote and push it, or repoint one it already has. "" or an error.

	ponytail: the other half of starting local. You make a repo wherever you keep repos, paste its URL
	here, and the team you have been using becomes the one everybody pulls — without the key changing,
	so every binding pointing at it still does.
	"""
	if not (d := dir_of(key)):
		return f"not in {key}"
	if not url.strip():
		return "a remote needs a URL"
	had = has_remote(d)
	r = _git("remote", "set-url" if had else "add", "origin", url.strip(), cwd=d)
	if r.returncode != 0:
		return (r.stderr or r.stdout).strip().splitlines()[-1][:120] if (r.stderr or r.stdout).strip() else "could not set the remote"
	if err := push_dir(d, "gitdashy: connect " + key, "join"):
		return err
	# ponytail: push EXPLICITLY. push_dir returns early when there is nothing new to commit, which is
	# exactly the state a team is in when you connect it — everything was committed locally already. So
	# the remote stayed empty, and the next person to clone it got no team.json, no name, and a key
	# derived from the URL instead of the one every binding points at.
	with _lock:
		if not _note(_git("push", "-q", "-u", "origin", "HEAD", cwd=d), "join"):
			return f"connected, but the push failed: {ERROR}" if ERROR else "connected, but the push failed"
	return ""


def setup(repo, name=""):
	"""Join a team that already exists: clone it and name it locally. Returns "" or an error.

	ponytail: no `create` any more. Making a repo is something you do wherever you keep repos, with
	whatever host you use; this clones one that exists. `gh repo create` made GitHub the only place a
	team could be born, which is not what a team is.
	ponytail: the key comes from the CLONED team.json when it has one, so everybody who joins the same
	repo agrees on the key their bindings point at. Only a repo that predates team.json needs `name`.
	"""
	if is_own_memory(repo):
		return "that is your own memory directory, which holds drafts — use a different repo for the team"
	tmp = os.path.join(config.TEAMS, ".joining")
	shutil.rmtree(tmp, ignore_errors=True)
	os.makedirs(config.TEAMS, exist_ok=True)
	if err := clone(repo, tmp):
		shutil.rmtree(tmp, ignore_errors=True)
		return err
	# ponytail: the team's own name first, then what you called it, then the last path segment. A repo
	# that already carries a name must not get a second one because two people typed differently.
	try:
		with open(os.path.join(tmp, INFO)) as f:
			theirs = str((json.load(f) or {}).get("name") or "")
	except (OSError, ValueError):
		theirs = ""
	key = key_of(theirs) or key_of(name) or key_of(slug_of(repo).split("/")[-1])
	if not key:
		shutil.rmtree(tmp, ignore_errors=True)
		return "that repo does not name a team — pass a name to call it by"
	dest = os.path.join(config.TEAMS, dirname(key))
	if os.path.lexists(dest):
		shutil.rmtree(tmp, ignore_errors=True)
		return f"already in {key}"
	os.rename(tmp, dest)
	union_attrs(dest)
	os.makedirs(os.path.join(dest, "memory"), exist_ok=True)
	seed_project(os.path.join(dest, "memory", "project.md"))
	# ponytail: your own log is NOT copied in. It was, back when "a team" was singular and your log
	# became the team's — but a personal log holds reviews of repos bound to OTHER teams and of private
	# work, and copying it in committed and PUSHED all of it. Joining a team would have told them what
	# else you review. log.reviewed() merges every log on read, so you still see your own history;
	# they see only what was reviewed for them.
	activate()
	# ponytail: the JOIN is done — cloned, renamed, activated. A push that fails after this is a sync
	# problem, not a join problem: read-only access to the repo clones fine, and then union_attrs and
	# seed_project give push_dir something to commit. Returning that error made the caller treat a
	# usable checkout as a failure — the TUI skipped state.wake.set() so REVIEWED never reloaded, and
	# retrying answered "already in <key>". push_dir already puts the reason in ERROR, which the Team
	# row shows, so it is reported where a sync failure belongs rather than as a failure to join.
	push_dir(dest, "gitdashy: join " + (os.environ.get("USER") or "team"), "join")
	return ""
