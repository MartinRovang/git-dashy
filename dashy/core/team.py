"""Team sync: log + memory live in a git checkout (~/.prs_team) that everyone pushes to.
ponytail: git is the sync server. Appends merge with the union driver, so parallel reviews never conflict."""
import json
import os
import re
import shutil
import subprocess
import threading

from .. import config
from . import github, log

ERROR = ""  # last git failure, shown in the header until the next success
NAME = ""  # the joined team keys, comma-joined, for the header strip only — resolution goes by key
_lock = threading.Lock()  # review threads push concurrently; git wants one writer
CLONE = 300  # seconds a clone or repo-create may take before we give up on it
BRANCH = "main"  # the branch a team gitdashy STARTS uses; a team it clones keeps its own


def _remote(cmd, extra_env=None, timeout=None):
	"""Run a command that talks to a remote, and never let it wait on a human.

	ponytail: a URL to a private repo makes git ask for a password. Inside curses that prompt is invisible
	and blocks the whole dashboard forever, so prompts are off and the call is bounded — fail, don't hang.
	"""
	timeout = CLONE if timeout is None else timeout
	# ponytail: LC_ALL=C because we MATCH on git's stderr — "could not read ", "Authentication failed".
	# On a localized machine those strings never appear, the auth branch never fires, and the user gets
	# back the clipped fatal this whole path exists to replace. Parsing output means pinning its locale.
	env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C", LANGUAGE="", **(extra_env or {}))
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


def origin_url(d):
	"""The origin URL at `d`, read from .git/config. "" when there is none. Spawns nothing.

	ponytail: for the DRAW PATH. has_remote reads this same file for exactly this reason — "a subprocess
	per tick to learn something that changes about once in a checkout's life is waste" — and `_url` does
	not go through _remote, so its 60s timeout is unbounded from a panel loop that redraws several times
	a second, once per joined team.
	ponytail: a .git that is a FILE is a worktree or a submodule, and its config lives elsewhere; ask git
	there rather than guess from a path that does not exist. Same fallback has_remote makes.
	"""
	g = os.path.join(d or "", ".git")
	if not os.path.isdir(g):
		return _url(d) if d and os.path.exists(g) else ""
	try:
		with open(os.path.join(g, "config")) as f:
			text = f.read()
	except OSError:
		return ""
	section = ""
	for line in text.splitlines():
		line = line.strip()
		if line.startswith("["):
			section = line
		elif section == '[remote "origin"]' and line.replace(" ", "").startswith("url="):
			return line.partition("=")[2].strip()
	return ""


def redacted(url):
	"""`url` with any user:password taken out of the authority, whatever the scheme. For DISPLAY.

	ponytail: bare_url answers only for http(s) and returns "" for anything else, because its callers
	want the ssh FORM back and there is no form to suggest otherwise. This one is the other job: it
	always returns something to show, and it covers every scheme — `ssh://u:pw@host/o/r` printed its
	password verbatim through every caller that fell back to the raw URL.
	ponytail: the scp-like form (git@host:o/r) has a user and no password slot, so it is returned as is.
	"""
	scheme, sep, rest = (url or "").strip().partition("://")
	if not sep:
		return url or ""
	authority, slash, path = rest.partition("/")
	return f"{scheme}://{authority.rpartition('@')[2]}{slash}{path}"


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


def _pull(d, label, *ref):
	"""`pull --rebase`, and never leave a rebase behind. True when it merged. Caller holds _lock.

	ponytail: `ref` is an explicit refspec for connect(), which runs before any upstream is set —
	`remote add` does not set one, so a bare `pull --rebase` there fails on "no tracking information"
	rather than on anything about the history. Same function either way: the abort is the point.

	ponytail: team.json is JSON, not append-only, so it is not union-merged — two people describing the
	team at once conflict. A pull that stopped mid-rebase STAYED that way: every later pull and push
	failed on it, leave refused the dirty tree, and the checkout was wedged until someone ran
	`git rebase --abort` by hand. Aborting puts HEAD back on the local commit with a clean tree, so
	nothing is lost and the next tick starts from a state git can work with.
	"""
	if _note(_git("pull", "--rebase", "-q", *ref, cwd=d), label):
		return True
	_git("rebase", "--abort", cwd=d)  # ponytail: a no-op when none is in progress; ERROR keeps the pull's reason
	return False


def pull_dir(d, label="sync"):
	"""ponytail: git is the sync server for any checkout, not just the team's — the private one uses it too."""
	if is_repo(d) and has_remote(d):  # ponytail: local-only history has nothing to pull and no error to show
		with _lock:
			_pull(d, label)


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
			if not (_pull(d, label) and _note(_git("push", "-q", cwd=d), label)):
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


def _info_raw(key):
	"""team.json as a dict, {} when it is missing, unreadable, or not an object.

	ponytail: no checkout, no file. os.path.join("", "team.json") is "team.json" — a RELATIVE path — so
	a key this machine has not joined read whatever team.json happened to be in the working directory.
	Harmless while info() was the only caller; covers() and write_info() come through here now, and one
	of them decides what gets bound on this machine.
	"""
	if not (d := dir_of(key)):
		return {}
	try:
		with open(os.path.join(d, INFO)) as f:
			got = json.load(f)
		return got if isinstance(got, dict) else {}
	except (OSError, ValueError):
		return {}


def info(key):
	"""{"name", "description"} the team says about itself, from its own checkout. Falls back to the key.

	ponytail: read from the TEAM, not from local config, so everyone who clones it sees the same name
	and the same description. A checkout written before this file existed still reads — the key is the
	fallback, and the key is what everything resolves by anyway.
	"""
	got = _info_raw(key)
	return {"name": _clean(got.get("name"), key), "description": _clean(got.get("description"), "")}


def covers(key):
	"""What the team declares it covers — ["acme/api", "neomedsys/*"], sorted. [] when nothing.

	ponytail: DECLARED IN THE TEAM, so it clones with it. Bindings are per machine, and a colleague who
	joined got per-repo seeds from the review log and nothing else: the owner rule that made the team
	cover an org never left the laptop it was typed on, and two people on one team read different
	briefs for the same repo with nothing on screen to say so. activate() seeds these into the local
	store once, exactly as it seeds from the log — visible in `bind --list`, and a --forget still sticks.
	ponytail: read through _clean and bind.target, because this file arrives in a clone. An entry that
	is not an owner or an owner/name is dropped rather than stored: a claim the resolver could never
	match is a row that reads as bound to nothing, and a control byte here would reach the listing.
	"""
	from . import bind  # ponytail: bind imports this module; a top-level import is a cycle
	raw = _info_raw(key).get("covers")
	out = set()
	for item in (raw if isinstance(raw, list) else []):
		# ponytail: DROPPED, not repaired. _clean strips a control byte and keeps the rest, which here
		# would bind an owner nobody typed; a claim is refused whole or taken whole.
		t = item.strip() if isinstance(item, str) else ""
		if t and t.isprintable() and len(t) <= 120 and (c := bind.cover_key(t)):
			out.add(c)
	return sorted(out)


def _clean(v, fallback):
	"""One line of printable text, clipped. For anything read out of a team's own files.

	ponytail: team.json comes from a CLONED repo, so anyone with push access to the team writes it, and
	it lands in the curses header and the CLI listing. A newline or a control byte there is theirs to
	choose and mine to refuse — this is the chokepoint every reader goes through.
	"""
	t = "".join(c for c in str(v or "") if c.isprintable()).strip()
	return t[:120] or fallback


def write_info(key, name, description, covers_=None):
	"""Record what the team calls itself, and what it covers when given. Returns "" or why it could not.

	ponytail: `covers_` None KEEPS what is there. Describing a team must not silently drop what it
	covers, which is what a whole-file rewrite from three arguments would do.
	"""
	if not (d := dir_of(key)):
		return f"not in {key}"
	body = {"name": name or key, "description": description or ""}
	# ponytail: through covers(), not the raw list. team.json arrives in a clone, and copying the raw
	# value back out meant a junk entry someone else pushed survived a rename untouched — the one write
	# that reads the file and rewrites it whole is the write that must not launder it.
	kept = covers(key) if covers_ is None else covers_
	if kept:
		body["covers"] = kept
	try:
		with open(os.path.join(d, INFO), "w") as f:
			json.dump(body, f, indent=1)
			f.write("\n")
	except OSError as e:
		return str(e)
	return ""


def _declare(key, target, add):
	"""Add or remove one coverage claim in team.json and push it. "" or why not."""
	from . import bind
	if not (t := bind.cover_key(target)):
		return f"{target!r} is not an owner or an owner/name"
	if not (d := dir_of(key)):
		return f"not in {key}"
	now = covers(key)
	# ponytail: `now` is the VALIDATED list, so a claim someone else pushed that is not an owner or an
	# owner/name is dropped by any --cover from here. That is deliberate — an entry the resolver could
	# never match is not a claim, it is noise — but it does mean the list can shrink from a machine that
	# only asked to add one thing, which is worth knowing before you go looking for what removed it.
	if (t in now) == add:
		return ""  # already what was asked for
	it = info(key)
	if err := write_info(key, it["name"], it["description"], sorted(set(now) | {t}) if add else [c for c in now if c != t]):
		return err
	# ponytail: shared, so it is pushed. A push that fails is a sync problem on the T row, like any other.
	push_dir(d, f"team: {key} {'covers' if add else 'no longer covers'} {t}", "sync")
	return ""


def cover(key, target):
	"""Declare that team `key` covers `target` — an owner ("acme", "acme/*") or an owner/name. "" or why not.

	ponytail: a claim is a disclosure decision for everyone who joins — every review of every repo it
	names reads this team's brief and facts, and facts about them may be shared here. That is why it is
	a separate, explicit verb with a keypress of its own, and not a side effect of binding locally.
	"""
	return _declare(key, target, True)


def uncover(key, target):
	"""Withdraw a claim. The rows it already seeded on each machine stay theirs to change."""
	return _declare(key, target, False)


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
	# ponytail: what a team DECLARES it covers is NOT seeded here. activate() runs on every command and
	# every launch, so a `covers` line added to the team repo after you joined would bind owner-wide
	# rules on your machine with no keypress and nothing that said it happened — and a binding decides
	# which brief a review reads AND whether facts about those repos may be pooled and shared into that
	# team. Anyone who can push to the team could then reach repos the team has never held a fact about.
	# The log seeding beside it is disclosure-neutral by construction; a claim is not. So adoption
	# happens once, in setup(), where a person chose to trust this team — and a claim that appears
	# later is shown by `gitdashy teams` and taken with `bind --owner`, which is a keypress.
	# "Require a keypress where it costs other people" is the rule this is the case for.
	for slug in joined():
		theirs = os.path.join(dir_of(slug), "memory")
		known = [r for _, r, *_ in install.registered() if r and os.path.exists(memory.path(r, theirs))]
		bind.seed(slug, sorted(memory.logged_repos(log_of(slug))) + known)


def adopt_covers(only=None):
	"""Bind what joined teams declare they cover. Returns [(key, target)] for what it wrote.

	ponytail: called from setup() — joining IS the act of trusting a team, so what it already declares
	comes with it, once, into a store you can read and take back. Not called from activate(): see the
	note there for why a claim that appears later must not land on its own.
	ponytail: repo claims are seeded for EVERY team before any owner rule, because the store resolves an
	exact binding ahead of an owner rule and seeding has to deliver the same order. Doing it per team
	meant one team's `acme/*` was written first and then `bind.seed` skipped another team's `acme/api`
	— already resolved through the rule — so which team won a contested repo came down to the
	alphabetical order of team names.
	ponytail: a target two joined teams both claim is left alone rather than guessed at, exactly as a
	repo in two teams' logs is. A claim is a disclosure decision; guessing publishes to the wrong team.
	"""
	from . import bind  # ponytail: bind imports this module; a top-level import is a cycle
	claims = {}
	for slug in joined():
		for t in covers(slug):
			claims.setdefault(t, set()).add(slug)
	mine = lambda slug: [t for t, who in claims.items() if who == {slug}]
	todo = [s for s in joined() if only is None or s == only]
	wrote = [(s, r) for s in todo for r in bind.seed(s, [t for t in mine(s) if not t.endswith("/*")])]
	return wrote + [(s, o + "/*") for s in todo
	                for o in bind.seed_owners(s, [t[:-2] for t in mine(s) if t.endswith("/*")])]


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
	global ERROR  # ponytail: declared here, not beside the assignment — ERROR is READ below first, and
	              # Python rejects a global declared after a read. Same ownership _note already has.
	local = looks_local(repo) or "://" in repo or "@" in repo  # a path, a URL or an ssh remote: pass it through
	# ponytail: git clone, whatever it is. `gh repo clone` was here so that a bare owner/name would
	# work, which quietly made GitHub the only host a team could live on — and a team is just a repo
	# people can reach. A bare owner/name is now expanded to a GitHub URL as a CONVENIENCE, and any
	# other URL, ssh remote or path goes straight through untouched.
	url = repo if local else f"https://github.com/{repo}.git"
	# ponytail: the token rides along on THAT path only — it is github.com by construction there. A URL
	# is the user's own host and gets nothing, and falls through to the ssh hint below.
	auth = github.git_auth() if not local else {}
	if _note(_remote(["git", "clone", "-q", url, dest], auth), "join"):
		github.persist_auth(dest)  # the env config does not survive the clone; the checkout needs its own
		return ""
	# ponytail: git cannot ask. GIT_TERMINAL_PROMPT=0 is deliberate — a credential prompt inside curses
	# is invisible and hangs the dashboard — so an https URL to a PRIVATE repo fails outright on a
	# machine with no credential helper. It used to work because `gh repo clone` carried gh's own
	# token; dropping gh took that with it. The answer is not to reach for a host's CLI again: it is
	# ssh, which every host speaks and which authenticates from an agent already loaded.
	# ponytail: "could not read " covers Username AND Password — git says the second when the URL
	# carries a user, which is the same failure with the same remedy.
	if say := _auth_hint(ERROR, url):
		# ponytail: the GLOBAL too. The friendly string was only the return value, so after the popup was
		# dismissed the T row went on painting the clipped fatal until the next successful sync. The row
		# renders ERROR[:40], so a long hint is still cut there — a truncated hint beats a truncated
		# fatal, but FOOTER is not that row's constraint and this does not make it fit.
		ERROR = f"join: {say}"
		return say
	return ERROR


def _auth_hint(msg, url):
	"""The credential hint when `msg` is git saying it could not authenticate, else ""."""
	return _credential_hint(url) if ("could not read " in msg or "Authentication failed" in msg) else ""


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
	# ponytail: the URL is never clipped. Dropping words to fit is fine — a truncated REMOTE is not a
	# remote, and handing someone an uncopyable one is the same failure as the 181-char message, just
	# rarer. The sentence goes first, then the verb, and the address always survives whole.
	for line in (f"no credential — try {alt}", f"try {alt}"):
		if len(line) <= FOOTER:
			return line
	return alt


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

AGENTS_TEMPLATE = """# For agent sessions working in this team's repos

This file is the team's, not one machine's. It reaches every session in every repo bound to this
team, through that repo's `.agent/team/repo.md` mirror. Reviews never see it: it says how to work
here, which is not something a reviewer should be told about the code it is judging.

## File what you work out

A session that establishes something about the code files it, and it costs one line:

```sh
gitdashy remember "the viewer owns mask state; the store only mirrors it"
gitdashy remember --general "logic that can live in the API does"
```

It becomes a draft, never a fact. A draft is confirmed only when a review, or a teammate, arrives
at the same thing independently — so file freely. What does not belong: what this task did, one
bug, anything git already records.

Without these, most drafts stay at one observation. Reviews propose; something else has to agree.
"""

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


def seed_agents(path):
	"""Give a new team repo the instruction its members' agent sessions will read.

	ponytail: shipped with the TEAM, not with the machine. The rule that makes a session file drafts
	lived in one operator's own corpus, so a colleague's sessions never filed any and half the second
	observers the recurrence gate needs did not exist. A team is the right scope for it: it is the team
	that wants the drafts, and a team is already a git repo that everyone pulls.
	ponytail: never overwrites, exactly like the brief. A team that has edited this owns it.
	"""
	if not os.path.exists(path):
		with open(path, "w") as f:
			f.write(AGENTS_TEMPLATE)


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
			# ponytail: EMPTY, or not there yet. Only a repo was refused, so `--at ~/dev/neomedsys` — the
			# parent of every checkout on the machine — was accepted, git-inited and committed: thirty
			# repos recorded as gitlinks in one commit, every later team commit re-recording their HEADs,
			# and a leave that refuses forever because the nested repos keep the tree dirty. A directory
			# that already holds something is somebody's; adopting it is not what "keep it somewhere
			# else" meant, and the prompt now says "empty" rather than "a path".
			if os.path.isdir(at) and os.listdir(at):
				return f"not empty — a team needs an empty directory, or one that does not exist yet: {at}"
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
	seed_agents(os.path.join(dest, "memory", "agents.md"))
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
	if not (url := url.strip()):
		return "a remote needs a URL"
	if err := _cannot_push_into(url):
		return err
	had = _url(d) if has_remote(d) else ""
	r = _git("remote", "set-url" if had else "add", "origin", url, cwd=d)
	if r.returncode != 0:
		return (r.stderr or r.stdout).strip().splitlines()[-1][:120] if (r.stderr or r.stdout).strip() else "could not set the remote"
	# ponytail: LOOK before pushing. A remote already holding history that is not this team's is a team
	# to JOIN — the push was rejected as non-fast-forward, the rebase could not even start because
	# `remote add` sets no upstream, and every tick after that painted git's --set-upstream-to hint on
	# the T row. History that IS ours — a mirror pushed by hand, a host moved — fast-forwards, and
	# refusing that would make repointing impossible: ancestry is the test, not emptiness. The fetch
	# goes through _git like every other call here: bounded, and it cannot prompt.
	with _lock:
		f = _git("fetch", "-q", "--prune", "origin", cwd=d)
		foreign = f.returncode == 0 and _foreign(d)
	if f.returncode != 0 or foreign:
		_unset(d, had)
		if foreign:
			# ponytail: through bare_url, like every other message that names a remote. A token in the
			# URL is a legitimate remote and this was a NEW message that skipped the chokepoint.
			return f"already has history that is not this team's — `teams --join` it, or connect an empty repo: {bare_url(url) or url}"
		msg = ((f.stderr or f.stdout).strip().splitlines() or ["could not reach it"])[-1]
		return _auth_hint(msg, url) or msg[:FOOTER]
	# ponytail: take what is already there before pushing. Accepting a remote that holds this team's
	# history and is AHEAD of us — a host a colleague has pushed to since — is only half a fix if the
	# push that follows is then rejected as non-fast-forward: the caller gets "connected, but the push
	# failed" and the team is left half-connected, which is the state this whole path exists to avoid.
	branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=d).stdout.strip() or BRANCH
	if _git("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}", cwd=d).returncode == 0:
		with _lock:
			if not _pull(d, "join", "origin", branch):
				_unset(d, had)
				return f"connected, but could not merge what is already there: {ERROR}" if ERROR else "could not merge what is already there"
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


def _unset(d, had):
	"""Put origin back to `had`, or remove it. ponytail: taken back out on every refusal in connect() —
	half-connected, origin set and nothing pushed, is the state that showed a git hint on the row
	forever, and it is not what the team looked like before the question was asked."""
	_git("remote", "set-url", "origin", had, cwd=d) if had else _git("remote", "remove", "origin", cwd=d)


def _foreign(d):
	"""True when origin holds history that is not this team's — unrelated, not merely different.

	ponytail: ancestry in EITHER direction is ours. A remote AHEAD of us is a host a colleague has
	pushed to since, which is the normal state of a live team — testing only "is it an ancestor of
	HEAD" called that foreign, refused the connect with a message that was false, and took the remote
	back out. What makes a remote somebody else's is that its commits and ours share no line at all.
	"""
	r = _git("for-each-ref", "--format=%(objectname)", "refs/remotes/origin", cwd=d)
	return any(_git("merge-base", "--is-ancestor", sha, "HEAD", cwd=d).returncode != 0
	           and _git("merge-base", "--is-ancestor", "HEAD", sha, cwd=d).returncode != 0
	           for sha in r.stdout.split())


def _cannot_push_into(repo):
	"""Why a LOCAL path cannot be a team's remote, or "". Only a non-bare checkout on this machine is.

	ponytail: `--new --at /srv/shared/x` and then `--join /srv/shared/x` was the documented shared-drive
	pairing. The clone works, and every push into it is refused — git will not move the branch a
	checkout has checked out — so the joiner's first fact failed with "failed to push some refs", and
	every one after it. A bare copy is what a shared drive needs, and the message says how to make one.
	`receive.denyCurrentBranch=updateInstead` is the one setting under which a checkout accepts them.
	"""
	if not looks_local(repo):
		return ""
	p = os.path.abspath(os.path.expanduser(repo.strip()))
	if not is_repo(p):
		return ""  # bare, or not a repo at all — git clone says which
	r = _git("config", "receive.denyCurrentBranch", cwd=p)
	if r.returncode == 0 and r.stdout.strip() == "updateInstead":
		return ""
	# ponytail: the remedy first. confirm() clips at the footer, and a long path pushed it off the line.
	return f"a checkout — git refuses pushes into one; share a bare copy: git clone --bare {p} {p}.git"


def already_joined(repo):
	"""The key of the joined team whose origin is `repo`, "" when none is.

	ponytail: knowledge.adopt has had this guard since the day memory could be a checkout; setup did not,
	so `--join` of a repo you were in — under a --name, or after its team.json was renamed — made a
	second checkout with a second key, and every binding pointed at one of the two. A path is compared
	as a path: two shares that happen to end in the same two segments are not the same repo.
	"""
	local = looks_local(repo)
	want = os.path.realpath(os.path.expanduser(repo.strip())) if local else repo
	for slug in joined():
		d = dir_of(slug)
		if not (have := _url(d)):
			continue
		if local:
			# ponytail: looks_local, which is what the rest of this file trusts to answer "is that a
			# path". Testing for the absence of "@" said no to /srv/team@shared.git, so the duplicate
			# join this exists to stop went through for any path with an @ in it.
			if looks_local(have) and os.path.realpath(os.path.join(d, os.path.expanduser(have))) == want:
				return slug
		elif same_remote(want, have):
			return slug
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
	if err := _cannot_push_into(repo):
		return err
	if have := already_joined(repo):
		return f"already joined that repo, as {have}"
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
	seed_agents(os.path.join(dest, "memory", "agents.md"))
	# ponytail: a repo that predates team.json gets one NOW — the name you gave, or the key that was
	# derived — and the push below carries it. Without it every joiner keyed the same repo by whatever
	# they typed, so two people on one team held two keys, and a binding one of them made meant nothing
	# on the other's machine. start() writes this file; a join is the other way a checkout is born.
	if not os.path.exists(os.path.join(dest, INFO)):
		write_info(key, name or key, "")
	# ponytail: your own log is NOT copied in. It was, back when "a team" was singular and your log
	# became the team's — but a personal log holds reviews of repos bound to OTHER teams and of private
	# work, and copying it in committed and PUSHED all of it. Joining a team would have told them what
	# else you review. log.reviewed() merges every log on read, so you still see your own history;
	# they see only what was reviewed for them.
	activate()
	adopt_covers(key)  # ponytail: what this team says it covers, once, because you chose to join it
	# ponytail: the JOIN is done — cloned, renamed, activated. A push that fails after this is a sync
	# problem, not a join problem: read-only access to the repo clones fine, and then union_attrs and
	# seed_project give push_dir something to commit. Returning that error made the caller treat a
	# usable checkout as a failure — the TUI skipped state.wake.set() so REVIEWED never reloaded, and
	# retrying answered "already in <key>". push_dir already puts the reason in ERROR, which the Team
	# row shows, so it is reported where a sync failure belongs rather than as a failure to join.
	push_dir(dest, "gitdashy: join " + (os.environ.get("USER") or "team"), "join")
	return ""
