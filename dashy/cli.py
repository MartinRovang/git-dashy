"""Argument parsing and the curses entry point. ponytail: sys.argv scan, argparse would be more code than this."""
import base64
import curses
import itertools
import json
import logging
import os
import signal
import sys
import threading

from . import HERE, VERSION, config, demo
from .core import (bind as bind_mod, friction as friction_mod, github, install as install_mod, knowledge,
                   memory, mirror, review as review_mod, team)
from .ui import screen, web

USAGE = f"""gitdashy {VERSION} — terminal dashboard of open PRs: mine, review-requested, assigned.

Usage: gitdashy [--gui [--browser]] [--interval SECONDS] [--auto] [--model NAME] [--effort LEVEL] [--depth LEVEL] [--voice A,B] [--hunter A,B] [--instructions FILE] [--demo] [--debug] [--version] [--help]
       gitdashy sync-memory --into PATH [--repo owner/name] [--no-pull] [--general]
       gitdashy remember [--repo owner/name | --general] FACT
       gitdashy self-review N [--repo owner/name] [--model NAME]
       gitdashy setup
       gitdashy self-check [--model NAME]
       gitdashy api PATH [--diff]
       gitdashy install [--full [--corpus URL]] [--dry-run] [--yes] [--no-setup] [--uninstall]
       gitdashy init --into DIR --loader FILE [--repo owner/name] | --into DIR --forget
       gitdashy bind [owner/name] [--team SLUG] [--forget] | --owner OWNER [--forget] | --list
       gitdashy drafts [--repo owner/name] [--count]
       gitdashy friction --claude-hook [--repo owner/name] | --interrupts N --denials N
       gitdashy teams [--new NAME [--desc TEXT] [--at DIR]] [--join URL|PATH [--name NAME]]
                      [--team KEY --connect URL] [--team KEY --cover TARGET | --uncover TARGET] [--leave KEY]
                      [--team KEY --agents-again]
                      [--team KEY --agents-again]

  --interval N   seconds between refreshes (default {config.INTERVAL}); i picks 1/2/5/10/15m
  --gui          open the dashboard in the desktop app (downloaded on first use)
  --browser      with --gui: use the browser even when the desktop app is available
  --no-open      with --gui: serve only, no browser — what the desktop app runs behind its splash
  --port N       with --gui --no-open: bind this port instead of a free one (the app picks it)
  --auto         Claude reviews every review-requested PR that appears from now on
  --model NAME   review model (default {config.DEFAULT_MODEL}, or $PRS_MODEL); m picks at runtime
  --effort LEVEL claude effort: low, medium, high, xhigh, max (default {config.EFFORT}, or $PRS_EFFORT); e picks
  --depth LEVEL  review depth: low, medium, high, adaptive (default {config.DEPTH}, or $PRS_DEPTH); d picks
  --voice A,B    how the posted body is phrased: review, caveman, bot, any mix (default review, or $PRS_VOICE); x toggles
  --hunter A,B   extra lenses, each a section of its own findings: ponytail, security, tests, humanizer (or $PRS_HUNTER); h toggles
  --instructions FILE  text file appended to every review prompt (or $PRS_INSTRUCTIONS)
  --demo         canned PRs and a fake reviewer — nothing touches github, claude or your real log
  --debug        append every API call, review, tick and swallowed exception to $PRS_DEBUG_LOG (or $PRS_DEBUG=1)

sync-memory copies this repo's review memory into PATH as a read-only mirror, so an agent session there
  reads what the reviews learned. --repo defaults to this directory's origin. Cross-repo facts are left out:
  put `@prs-memory/general.md` in ~/.claude/CLAUDE.md (via a symlink) and they load everywhere, live, with
  nothing to sync. --general mirrors them in here as well, for anyone not doing that.
  Refuses to write anywhere git would commit it. --no-pull skips the team fetch, for callers on a
  timeout: it mirrors whatever the last refresh pulled.

remember files a fact you learned while working, into the same drafts a review writes to — so a fact a
  review and a coding session found independently is confirmed by their agreement. --repo defaults to this
  directory's origin; --general is for something true of every repo.

self-review runs the reviewer over one of your OWN PRs and posts nothing — a pass before you ask a
  person, not a substitute for one. The review is written to ~/.prs_reviews/ and its findings wait in a
  separate pool that never confirms a fact by itself: the pre-review and the real one are the same model
  on the same diff, so only a later real review landing on the same fact independently promotes it.

install wires this machine so every session reads the cross-repo facts: one symlink in the agent config
  directory and one import. It explains itself and asks before writing anything (--yes to skip the ask,
  --dry-run to see it and stop). Idempotent, and --uninstall reverses exactly what it wrote. --full ends
  by offering to say who you are; --yes, --no-setup or a non-terminal stdin all skip that. Reviews need
  none of this — they read memory through the prompt and always have.

install --full also puts an agent corpus on this machine, so coding sessions work to a stated discipline:
  it installs the small one gitdashy ships (or --corpus URL for your own), imports it, seeds a USER.md for
  you to fill in, and registers one SessionStart hook that seeds a repo's local notes. It says what that
  costs in tokens and asks separately, because it is a much bigger commitment than the line above. See
  docs/install.md.

init wires one repo, so a session there also reads that repo's own facts: it excludes the mirror from git
  (via .git/info/exclude, never the tracked .gitignore), adds the import to --loader, and registers the
  path so the running dashboard re-mirrors it on every refresh. No hooks. --into DIR --forget stops
  refreshing one; the files stay, they just go still.

bind says which team a repo belongs to, and that decides everything the team knows about it: which
  project brief its reviews are told (one, never two), which facts they read, and whether a fact about it
  may be shared. A repo bound to nothing is private — your own memory and nothing else. --owner OWNER
  binds a whole org in one line, and a repo binding or --forget still overrides it, so the one repo that
  is not the project can be left out. Joining a team binds the repos already named in its shared review
  log, once, so nothing you had yesterday disappears; after that it is yours to change and --forget
  sticks. Defaults to this directory\'s origin; --list shows every rule. A bare `bind` reports and changes
  no binding of its own — though joining a team seeds from its log on any command, including this one.

drafts shows what a review proposed and no second review has confirmed — the store nothing else can
  show you. Counts say how close each is to becoming a fact; `pre-review` findings carry no count,
  because a pre-review and the real review are one model on one diff. Read-only here: W in the dashboard
  promotes one by hand or drops it.

friction answers one question — did this session hit something worth remembering? — and is how a
  coding session gets ASKED to file a fact instead of being told to remember. Two human signals only:
  how often you interrupted, and how often you refused a tool call. Tool errors are ignored on purpose
  (most are a benign non-zero exit), and neither signal grows with session length, so a long routine
  session stays silent. It prints the ask, or nothing at all, which is the usual answer. Claude Code is
  wired by the Stop hook gitdashy ships; any other agent counts its own signals and passes them in.

teams lists the teams this machine has joined, what each calls itself, and what it covers.
  A team is a git repo — or just a directory — that pools what reviews learn. Whoever can reach it is
  on the team; there is no service and no account. Its name and description live in team.json inside
  it, so everyone who clones it sees the same ones.
  --new starts one right here with no remote at all: a name, a description, and files. --at DIR keeps
  it somewhere else and links to it — an EMPTY directory, or one that does not exist yet; one that
  already holds something is refused rather than adopted. When you have a repo for it, --team KEY
  --connect URL points it there and pushes; the key does not change, so every binding still holds. A
  remote that already has history that is not this team's is refused: that is a team to join.
  --join clones one that exists, from any git URL or a path (a bare owner/name is expanded to GitHub
  as a convenience, nothing more). It takes its key from the team's own name, and writes one in when
  the repo has none, so the next person lands on the same key. A path must be a BARE repo — git
  refuses pushes into a checkout — so a team on a shared drive is `git init --bare` there, then --connect.
  --cover TARGET declares, in the team, that it covers an owner ("acme", "acme/*") or an owner/name,
  and binds it here. Someone JOINING the team gets what it declares seeded into their own bindings
  once, the way the review log is — visible in `bind --list`, and a --forget still sticks. A claim
  added after they joined is NOT bound on their machine on its own: it is listed here, and taken with
  `bind --owner`, because what a repo's reviews read and where its facts may be pooled is not something
  a push to the team repo gets to decide for someone else. --uncover withdraws the declaration; rows it
  already seeded stay each person's to change. --leave drops one checkout, refusing while it holds
  unpushed work. Several teams at once; which one applies to a repo is `gitdashy bind`.

setup asks for the two things a corpus cannot work out for itself: who you are, and what the work is
  for. It writes USER.md and a project brief — yours when you are on your own, the team's when you are in
  one. Which repos read it is decided by `gitdashy bind`. Re-runnable: a blank answer KEEPS what is already there rather
  than clearing it, the prompt shows you what that is, and sections you added by hand are left alone.
  `install --full` asks only the first of the two: who you are is a property of this machine, and what
  the work is for is a property of a repo. YOUR brief covers every repo bound to no team — one file for
  all of them — so give each project its own team (`gitdashy teams --new`, then `gitdashy bind`) rather
  than letting one product's brief reach the reviews of another.

self-check makes one real claude call and proves the three things every review depends on: that the
  appended review lens arrives, that --safe-mode hides the machine's CLAUDE.md, and that tools still run
  under it. A unit test can assert the flags are passed; only this can tell you they are honoured.

Keys: j/k move, ⏎ detail pane, r review (REVIEW REQUESTED), p pre-review your own PR posting nothing,
v read the full review of the selected PR (any row that has one), Y open the pre-review, o open,
␣ unfold/fold older reviews of the same PR, a auto, m model, d depth, e effort, x voices, h hunters, t REVIEWED history window, i interval, s summaries
(each opens a dropdown under the setting: j/k or the same key moves, ⏎ picks, esc keeps), D show/hide drafts (hidden by default),
S/R/V/K settings menus (all / Reviewer / View / Knowledge), ? show each setting's key in the header,
L local memory dir, C where all team checkouts live, n repo memory, g general memory ($EDITOR),
1/2 or Tab switch the pane between the review summary and the code it is about,
  in code: n/N next mark (or file), D marks-only vs the full diff, c context ±3/±8/none,
b bind the selected repo to a team (1-8 pick, o whole owner, x unbind),
P what the team knows from you (x forget it everywhere, t send one that never went), W what is waiting to become a fact (t accept, x drop, s scan for repeats), Z dream (Claude tidies all memory, you approve),
T teams (1-8 open one, n start one, a join one; inside a team: e brief, d describe, c connect, o cover, x leave), u install the newest release, f refresh, q quit."""


def arg(flag, default=None, cast=str, argv=None):
	argv = sys.argv if argv is None else argv
	if flag not in argv:
		return default
	i = argv.index(flag) + 1
	if i >= len(argv):  # ponytail: a flag with nothing after it is a typo, not a traceback
		raise SystemExit(f"gitdashy: {flag} needs a value")
	return cast(argv[i])


def sync_memory(argv):
	"""Mirror the shared memory into --into, for an agent session in that repo to read."""
	into = os.path.expanduser(arg("--into", "", str, argv))  # a quoted "~/x" would mirror into a dir named ~
	if not into:
		raise SystemExit("gitdashy: sync-memory needs --into PATH")
	team.activate()  # ponytail: names the team and points LOG at its checkout; memory.sources() needs it
	return print(mirror.sync(into, arg("--repo", "", str, argv) or team.origin_slug("."),
	                         "--no-pull" not in argv, "--general" in argv))


def install(argv):
	"""Wire this machine, after saying what that means and being told to go ahead."""
	full, dry = "--full" in argv, "--dry-run" in argv
	corpus, url = os.path.join(HERE, "corpus"), arg("--corpus", "", str, argv)
	if "--uninstall" in argv:
		return print("\n".join((install_mod.full_remove if full else install_mod.remove)(dry)))
	print("\n".join(install_mod.full_explain(corpus, url) if full else install_mod.explain()))
	if dry:
		print("")
		print("\n".join(install_mod.full_apply(corpus, url, dry=True) if full else install_mod.apply(dry=True)))
		return print("\n--dry-run, so nothing was changed. Without it you are asked first.")
	if "--yes" not in argv:
		if not sys.stdin.isatty():  # ponytail: never write global config from a script that cannot be asked
			raise SystemExit("\ngitdashy: not a terminal — pass --yes if you meant to install unattended")
		try:
			if input("\nGo ahead? [y/N] ").strip().lower() not in ("y", "yes"):
				return print("nothing changed")
		except (EOFError, KeyboardInterrupt):
			return print("\nnothing changed")
	print("")
	out = install_mod.full_apply(corpus, url) if full else install_mod.apply()
	print("\n".join(out))
	# ponytail: --full ONLY. Plain install puts no corpus on the machine, so there is no USER.md to
	# fill in, and its whole promise is that it stays out of the way — no corpus, no hooks, no
	# settings.json, and nothing to answer. The project brief still matters at that tier and setup
	# writes it, but offering a two-part flow whose first half SKIPs is worse than saying nothing.
	if full and not any(l.startswith("FAIL") for l in out):
		offer_setup(argv)


def offer_setup(argv):
	"""After a full install, offer the questions rather than only naming the file to hand-edit.

	ponytail: the installer used to say "fill it in, it is the highest-value file here" and never
	mention `gitdashy setup` — every occurrence of that string was inside setup() itself or a marker.
	The guided path existed and was unreachable from the one moment you are deciding how to fill it.
	"""
	# ponytail: --yes too. This command already tells you "pass --yes if you meant to install unattended"
	# when stdin is not a tty, so --yes means "do not ask me" for BOTH gates or the promise is false. A
	# bootstrap script run from an interactive shell inherits that tty, so isatty alone does not cover it.
	if "--no-setup" in argv or "--yes" in argv or not sys.stdin.isatty():
		return
	if install_mod.setup_done(project=False):
		return  # ponytail: nothing to offer once USER.md is written; the brief is not asked here
	later = "`gitdashy setup` whenever you want to — nothing else is waiting on it."
	try:
		if input("\nSay who you are now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
			return print(later)
	except (EOFError, KeyboardInterrupt):
		return print("\n" + later)
	print("")
	try:
		setup(argv, project=False)
	except SystemExit as e:
		# ponytail: cli.setup's own `ask` raises SystemExit on Ctrl-C, and SystemExit is a BaseException,
		# so it walked past the handler above — the install had COMPLETED and printed its report, and the
		# process still exited non-zero. A wrapper checking $? read a finished install as a failed one.
		# Declining halfway through the briefs is a decline, not a failure.
		print((str(e).strip() or later))


def self_review(argv):
	"""Pre-review one of your own PRs. Posts nothing; writes a file and prints where it is."""
	nums = [a for a in argv[2:] if a.isdigit()]
	repo = arg("--repo", "", str, argv) or team.origin_slug(".")
	if not nums or not repo:
		raise SystemExit("gitdashy: self-review needs a PR number, and --repo owner/name "
		                 "unless this directory has a github origin")
	config.load()
	team.activate()
	pr = {"repository": {"nameWithOwner": repo}, "number": int(nums[0]),
	      "url": f"https://github.com/{repo}/pull/{nums[0]}"}
	print(f"pre-reviewing {repo}#{nums[0]} — nothing will be posted…")
	status, dest = review_mod.self_review(pr, arg("--model", config.DEFAULT_MODEL, str, argv))
	print(f"{status}\n{dest}" if dest else status)
	raise SystemExit(0 if dest else 1)


def setup(argv, project=True):
	"""Ask for the two things a corpus cannot work out on its own: who you are, and what this is for.

	ponytail: `project` False is the install-time path — who you are is machine-level, what the work is
	for is not. See install.setup.
	"""
	print(("Two short briefs. " if project else "One short brief. ")
	      + "Blank keeps what is already there — the prompt shows you what. "
	        "Edit the files later; nothing here is final.\n")
	def ask(prompt):
		try:
			return input(f"  {prompt}\n  > ").strip()
		except (EOFError, KeyboardInterrupt):
			raise SystemExit("\nnothing written")
	team.activate()
	print("\n" + "\n".join(install_mod.setup(ask, project=project)))


def init(argv):
	"""Wire one repo so a session there reads its review memory."""
	into, loader = arg("--into", "", str, argv), arg("--loader", "", str, argv)
	if into and "--forget" in argv:  # ponytail: the registry grows on its own, so it needs a way out
		return print(f"gitdashy: {'no longer refreshing' if install_mod.unregister(into) else 'was not refreshing'} {into}")
	if not into or not loader:
		raise SystemExit("gitdashy: init needs --into DIR (where the mirror goes) and --loader FILE "
		                 "(the instruction file that should import it)")
	team.activate()
	repo = arg("--repo", "", str, argv) or team.origin_slug(".")
	if not repo:
		raise SystemExit("gitdashy: no git origin here — pass --repo owner/name")
	# ponytail: a REFUSAL exits non-zero. wire_repo reports in prose, so `init` printed "refused — git
	# would commit …" and exited 0; the session hook reads that status to decide whether to start its
	# background sync, and took the refusal for a success.
	print("\n".join(lines := install_mod.wire_repo(into, loader, repo)))
	if any("refused" in l for l in lines):
		raise SystemExit(1)


def remember(argv):
	"""File a fact a coding session learned, into the same drafts a review writes to."""
	rest, skip = [], False
	for a in argv[2:]:  # ponytail: the fact is everything that is not a flag or a flag's value
		if skip:
			skip = False
		elif a == "--repo":
			skip = True
		elif a != "--general":
			rest.append(a)
	fact = " ".join(rest).strip()
	if not fact:
		raise SystemExit("gitdashy: remember needs a fact to remember")
	team.activate()  # so memory.sources() sees the team as a second source
	general = "--general" in argv
	repo = "" if general else (arg("--repo", "", str, argv) or team.origin_slug("."))
	if not general and not repo:
		raise SystemExit("gitdashy: no git origin here — pass --repo owner/name, or --general")
	scope, where = repo or None, repo or "general"
	# ponytail: --general threw away the repo you are standing in, which is the only thing that says
	# WHICH PROJECT a general fact is about. With two teams joined it then had no destination at all —
	# neither poolable nor shareable, with nothing on screen saying why. The context is kept now; a
	# general fact means "true across this project", and the project is that repo's team.
	about = "" if not general else (arg("--repo", "", str, argv) or team.origin_slug("."))
	if memory.already_known(scope, fact):
		return print(f"gitdashy: {where} already knows that")
	promoted = memory.append(scope, fact, about)
	team.push_dir(config.MEMORY_DIR, f"memory: remembered for {where}", "mine")
	team.push(f"memory: evidence for {where}")  # ponytail: a promotion writes the pool, which lives over there
	if promoted:  # ponytail: the counter counts observations; it does not know which surface each came from
		return print(f"gitdashy: {where} — confirmed by a second independent observation: {promoted[0]}")
	print(f"gitdashy: {where} — drafted; one more independent observation confirms it")


def _team_of(argv):
	"""The team a command acts on: --team as typed, folded to its key, or the one joined team.

	ponytail: refused when it is not a team this machine has joined. `--team NeoMedSys_team` was accepted
	verbatim, reported success and resolved to nothing — a typo bound an org to a team that did not
	exist, and the only sign was the pane saying "not in team" on every row of it.
	ponytail: ONE resolver for every verb that takes --team. There were two, and the second skipped the
	membership check — so `teams --team nope --cover acme` appended {"owner": "acme", "team": "nope"} to
	the bindings store and only then failed with "not in nope". That is the same silently-wrong-team bug
	this function exists to kill, reintroduced one verb over, and a write before a check is worse than
	the original because it leaves the store dirty.
	ponytail: none joined and several joined are different problems, so they get different sentences —
	folding them into "not in a team" told someone with two teams to go and join one.
	"""
	if typed := arg("--team", "", str, argv):
		key = team.key_of(typed)
		if not team.dir_of(key):
			raise SystemExit(f"gitdashy: not in team {typed!r} — joined: {', '.join(team.joined()) or 'none'}")
		return key
	if len(joined := team.joined()) == 1:
		return joined[0]
	raise SystemExit("gitdashy: " + (f"say which team: --team {' | --team '.join(joined)}" if joined else
	                                 "not in a team — join one with T in the dashboard, or pass --team SLUG"))


def bind(argv):
	"""Bind a repo to a team, so reviews of it are told that team\'s brief and no other."""
	team.activate()  # ponytail: names the team, and seeds bindings from the shared log the first time
	# ponytail: a flag's VALUE is not a positional. Scanning argv for the first thing containing "/"
	# matched `--team org/mem` and bound the team to itself, from inside the repo you meant to bind —
	# reported as success, with the right words and the wrong repo. `remember` already skips this way.
	rest, skip = [], False
	for a in argv[2:]:
		if skip:
			skip = False
		elif a in ("--repo", "--team", "--owner"):
			skip = True
		elif not a.startswith("-"):
			rest.append(a)
	# ponytail: BEFORE the positional guard. --list is a read-only question, and gating it behind a check
	# on the thing you were asking about turned `bind <typo> --list` into an exit instead of an answer.
	if "--list" in argv:
		rows = ([(o + "/*", t) for o, t in sorted(bind_mod.owners().items())] + sorted(bind_mod.bindings().items())
		        + [(r, "excluded — kept out of the rule above") for r in bind_mod.excluded()])
		print("\n".join(f"  {r:36}  →  {t}" for r, t in rows) if rows else "  no repo is bound to a team")
		return
	named = arg("--repo", "", str, argv) or next((a for a in rest if "/" in a), "")
	# ponytail: a positional we cannot read is a TYPO, not an absence. `bind neo-api --team org/mem`
	# used to fall through to this directory's origin and bind whatever repo you were standing in,
	# reporting success with the wrong name — the same silent-wrong-repo shape as the flag-value bug
	# above it, reached by a different route.
	if not named and rest:
		raise SystemExit(f"gitdashy: {rest[0]!r} is not owner/name — bind takes a full slug, or --owner OWNER")
	repo = named or team.origin_slug(".")
	# ponytail: an owner rule is one line for a whole org, and a repo binding still overrides it — so the
	# one repo under that owner which is NOT the project can be excluded with `--forget`, which a pattern
	# on its own cannot express. Handled before the repo path, since --owner names no repo.
	if owner := arg("--owner", "", str, argv):
		if "--forget" in argv:
			if err := bind_mod.forget_owner(owner):
				raise SystemExit("gitdashy: " + err)
			return print(f"gitdashy: {bind_mod.owner_key(owner)}/* is no longer bound")
		to = _team_of(argv)
		if err := bind_mod.bind_owner(owner, to):
			raise SystemExit("gitdashy: " + err)
		return print(f"gitdashy: {bind_mod.owner_key(owner)}/* → {to}  (a repo binding still overrides it)")
	if not repo:
		raise SystemExit("gitdashy: no git origin here — pass owner/name, or --list")
	# ponytail: a bare `gitdashy bind` REPORTS — it changes no binding of its own. Naming no repo and
	# asking for no change is a question, and answering it by binding this directory to whatever team
	# you are in is a write nobody asked for. It is not a read-only command, though, and saying so would
	# be false: team.activate() above seeds bindings from the shared log, on this and every other
	# command. That is the bootstrap, and a bootstrap only some entry points perform is the one missing
	# on the path nobody tested.
	if not named and not arg("--team", "", str, argv) and "--forget" not in argv:
		text, whose = memory.brief(repo)
		print(f"gitdashy: {bind_mod.key(repo)} → {bind_mod.of(repo) or 'no team'}")
		return print(f"  reviews of it read: {whose}" + ("" if text else " (nothing to read)"))
	if "--forget" in argv:
		was = bind_mod.of(repo)
		if err := bind_mod.forget(repo):
			raise SystemExit("gitdashy: " + err)
		print(f"gitdashy: {bind_mod.key(repo)} " + (f'unbound from {was}' if was else 'was not bound to anything'))
	else:
		to = _team_of(argv)
		if err := bind_mod.bind(repo, to):
			raise SystemExit("gitdashy: " + err)
		print(f"gitdashy: {bind_mod.key(repo)} → {to}")
	# ponytail: says what the repo GETS, not that a row was written. A binding is only ever a means to
	# selecting a brief, and the one thing worth confirming is which brief a review will now be given.
	text, whose = memory.brief(repo)
	print(f"  reviews of {bind_mod.key(repo) or repo} read: {whose}" + ("" if text else " (nothing to read)"))


NO_TOKEN = """  gitdashy: no GitHub token.

  Set one and run again — a classic token with the `repo` scope, or a fine-grained
  token with read access to the repos you review and write access to pull requests:

      export GH_TOKEN=…          (or $GITHUB_TOKEN)
      https://github.com/settings/tokens

  Put it in your shell rc to keep it. Nothing else is needed: gitdashy talks to the
  GitHub API itself and does not use the gh CLI.

  To look around without one:  gitdashy --demo
"""


def api(argv):
	"""GET one GitHub API path and print it, `--diff` for a unified diff. This is how a review reads the
	repo now that gh is gone.

	ponytail: GET only, github only, and a file arrives decoded rather than as base64 in an envelope.
	It is the one command a review is allowed to run, so what it can do is what a reviewer may do: read.
	ponytail: a PATH, never a URL. The caller is a model that has just read an untrusted diff, and a diff
	that talks it into `gitdashy api https://elsewhere/…` must not be able to send anything anywhere.
	github.call() withholds the token off-host as well — two locks, because this one is worth two.
	"""
	signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # `| head` is a closed pipe, not a BrokenPipeError to print
	path = next((a for a in argv[2:] if not a.startswith("-")), "")
	if not path:
		raise SystemExit("gitdashy: api needs a path, e.g. /repos/owner/name/contents/src/app.py")
	if path.startswith(("http://", "https://", "//")):
		raise SystemExit("gitdashy: api takes an API path, not a URL")
	# ponytail: and the repo, when a review set one. GET-only and host-pinned kept the token in and the
	# writes out; nothing kept the READS to the PR being reviewed, and a review body is posted publicly.
	try:
		path = github.scoped(path, os.environ.get(github.SCOPE, ""), os.environ.get(github.SCOPE_TEAM, ""))
	except ValueError as e:
		raise SystemExit(f"gitdashy: {e}")
	try:
		accept = "application/vnd.github.v3.diff" if "--diff" in argv else "application/vnd.github+json"
		raw = github.call(path if path.startswith("/") else "/" + path, accept=accept, timeout=60)
	except OSError as e:
		raise SystemExit(f"gitdashy: {e}")
	try:
		d = json.loads(raw)
	except ValueError:
		return print(raw)  # a diff, a raw file: already text
	if isinstance(d, dict) and d.get("encoding") == "base64":
		return print(base64.b64decode(d["content"]).decode(errors="replace"))
	print(json.dumps(d, indent=1))


def drafts(argv):
	"""Show what gitdashy has heard once and not confirmed. Read-only; W in the dashboard acts on it."""
	team.activate()
	count = "--count" in argv
	# ponytail: --count is for a session hook, so it defaults to the repo you are standing in and reads
	# only the local store — no network, and nothing printed when there is nothing to say.
	only = arg("--repo", "", str, argv) or (team.origin_slug(".") if count else "")
	if count and not only:
		# ponytail: a repo we cannot name has nothing waiting FOR IT. Without this a local-only repo —
		# a git repo, which is all the hook requires — was told every draft on the machine was its own.
		return
	rows = memory.waiting()
	if only:
		rows = [r for r in rows if (r[0] or "general") == only]
	if count:
		if rows:
			print(f"gitdashy: {len(rows)} draft{'s' if len(rows) != 1 else ''} waiting for {only}"
			      " — `gitdashy drafts` lists them, W in the dashboard promotes or drops them")
		return
	if not rows:
		return print("  nothing waiting — every observation so far is either a fact or gone")
	rows.sort(key=lambda r: ((r[0] or ""), r[3] == "self", -r[1]))
	# ponytail: groupby, not a `where` sentinel. `where = None` collided with the repo of the GENERAL
	# file, which is also None — and general sorts first, so the one group that could hit it always did:
	# its rows printed under no heading at all. A sentinel that can equal a real value is not a sentinel.
	for repo, group in itertools.groupby(rows, key=lambda r: r[0]):
		team_of = bind_mod.of(repo) if repo else ""
		print(f"\n  {repo or 'general'}" + (f"  ({team_of})" if team_of else ""))
		for _repo, n, fact, kind in group:
			# ponytail: the count is the whole point of the line — it says how close this is to being a
			# fact, and a pre-review finding has no count because one opinion twice is still one opinion.
			print(f"    [{('pre-review' if kind == 'self' else f'seen {n}×'):>10}]  {fact}")
	print(f"\n  {len(rows)} waiting · {memory.PROMOTE_AT} independent observations make a fact · "
	      f"W in the dashboard promotes or drops one")


def teams(argv):
	"""List the teams this machine has joined, or join/leave one.

	ponytail: a CLI as well as `T`, for the same reason `bind` has one — it is scriptable, it is
	testable without curses, and the join path is the one that clones a repo, which is worth being able
	to run somewhere errors are visible rather than on a footer.
	"""
	team.activate()
	if new := arg("--new", "", str, argv):
		# ponytail: START one, with nothing hosted anywhere. Every other path clones a repo that already
		# exists, so the first person on a team was stuck waiting for somebody to make one.
		if err := team.start(new, arg("--desc", "", str, argv), arg("--at", "", str, argv)):
			raise SystemExit("gitdashy: " + err)
		key = team.key_of(new)
		print(f"gitdashy: started {new} ({key}) at {team.dir_of(key)}")
		print(f"  bind repos to it: gitdashy bind --owner OWNER --team {key}")
		print(f"  give it a remote when you have one: gitdashy teams --team {key} --connect URL")
	elif url := arg("--connect", "", str, argv):
		key = _team_of(argv)
		if err := team.connect(key, url):
			raise SystemExit("gitdashy: " + err)
		print(f"gitdashy: {key} now pushes to {url}")
	elif target := arg("--cover", "", str, argv):
		key = _team_of(argv)
		# ponytail: declared in the team AND bound here. The local rule is the cheap, reversible half and
		# goes first: a declaration that published while the binding failed is a rule that works only on
		# other people's machines, which is the one outcome this pair must not produce.
		kind, t = bind_mod.target(target)
		if err := (bind_mod.bind_owner(t, key) if kind == "owner" else bind_mod.bind(t, key) if kind else
		           f"{target!r} is not an owner or an owner/name") or team.cover(key, target):
			raise SystemExit("gitdashy: " + err)
		print(f"gitdashy: {key} now covers {bind_mod.cover_key(target)}  (bound here, and everyone who joins gets it once)"
		      + (f"  ({team.ERROR})" if team.ERROR else ""))
	elif target := arg("--uncover", "", str, argv):
		key = _team_of(argv)
		if err := team.uncover(key, target):
			raise SystemExit("gitdashy: " + err)
		print(f"gitdashy: {key} no longer covers {bind_mod.cover_key(target)}  (rows it seeded stay until `gitdashy bind ... --forget`)"
		      + (f"  ({team.ERROR})" if team.ERROR else ""))
	elif join := arg("--join", "", str, argv):
		# ponytail: what CHANGED, not joined()[-1] — that is the last alphabetically, so already being
		# in "zulu" and joining "acme" printed "joined zulu".
		before = set(team.joined())
		if err := team.setup(join, arg("--name", "", str, argv)):
			raise SystemExit("gitdashy: " + err)
		fresh = sorted(set(team.joined()) - before)
		print(f"gitdashy: joined {fresh[0] if fresh else join}"
		      + (f"  ({team.ERROR})" if team.ERROR else ""))
	elif "--agents-again" in argv:
		key = _team_of(argv)
		print(f"gitdashy: {key} will be asked about again at the next launch" if memory.ask_agents_again(key)
		      else f"gitdashy: nothing recorded for {key} — it is already asked about at launch")
	elif leave := arg("--leave", "", str, argv):
		if err := knowledge.leave(leave):
			raise SystemExit("gitdashy: " + err)
		print(f"gitdashy: left {leave}")
	got = team.joined()
	if not got:
		return print("  no teams joined — `gitdashy teams --join owner/name` or T in the dashboard")
	for key in got:
		it = team.info(key)
		bound = sorted(r for r, t in bind_mod.bindings().items() if t == key)
		owners = sorted(o + "/*" for o, t in bind_mod.owners().items() if t == key)
		d = team.dir_of(key)
		print(f"  {it['name']}  ({key})")
		if it["description"]:
			print(f"      {it['description']}")
		print(f"      {d}{'' if team.has_remote(d) else '   · no remote yet'}")
		if declared := team.covers(key):
			print(f"      declares: {', '.join(declared)}")
		print(f"      {', '.join(owners + bound) or 'no repos bound to it yet'}")


def friction(argv):
	"""Ask, when a session hit something worth remembering. The contract every agent is wired against.

	Two ways in, one policy behind both:

	    gitdashy friction --interrupts N --denials N     any agent that can count its own signals
	    gitdashy friction --claude-hook                  Claude's Stop hook JSON on stdin, its JSON out

	Prints the reason and exits 0 when there is one; prints nothing when there is not. Silence is the
	normal answer — most sessions are routine, and one that fires every time is a prompt nobody reads.

	ponytail: there was a third door, `--transcript PATH`, and nothing wired it: --claude-hook covers
	Claude and --interrupts/--denials covers everyone else. It also resolved filed_since BEFORE counting
	while the hook path does it after, so the two entry points disagreed about a session that had both
	friction and a draft. One door fewer is one disagreement fewer. `echo '{"transcript_path":"..."}' |
	gitdashy friction --claude-hook` does the same job for a person debugging one.
	"""
	if "--claude-hook" not in argv:
		if said := friction_mod.reason(arg("--interrupts", 0, int, argv), arg("--denials", 0, int, argv)):
			print(said)
		return
	try:
		hook = json.loads(sys.stdin.read() or "{}")
	except ValueError:
		return  # ponytail: a hook that cannot parse its own input says nothing, never blocks a stop
	# ponytail: stop_hook_active means WE already blocked this stop once. Blocking again is a loop the
	# user cannot leave except by killing the session, so the second ask is never made.
	if hook.get("stop_hook_active") or not (path := hook.get("transcript_path")):
		return
	if not (said := friction_mod.reason(*friction_mod.claude_signals(path))):
		return
	# ponytail: the repo is resolved HERE, not at the top. origin_slug() forks `git`, and this runs at
	# the end of every session in every repo — above the check it paid for that fork on every routine
	# session and then discarded the answer, which is ~99% of them.
	repo = arg("--repo", "", str, argv) or team.origin_slug(".")
	if not friction_mod.filed_since(repo, friction_mod.started_at(path)):
		print(json.dumps({"decision": "block", "reason": said}))


def debug(argv):
	"""Dump the log to config.DEBUG_LOG, tracebacks included. The screen shows one line per failure; this keeps the rest."""
	logging.basicConfig(filename=config.DEBUG_LOG, level=logging.DEBUG, format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
	os.chmod(config.DEBUG_LOG, 0o600)  # every PR url and traceback lands here
	# Log, then hand over to the default hooks: a crash still prints to the terminal.
	sys.excepthook = lambda *a: (logging.critical("uncaught", exc_info=a), sys.__excepthook__(*a))
	threading.excepthook = lambda a: (logging.critical("uncaught in thread %s", a.thread.name, exc_info=(a.exc_type, a.exc_value, a.exc_traceback)), threading.__excepthook__(a))
	logging.info("gitdashy %s starting: %s", VERSION, argv)


def run(argv=None):
	argv = sys.argv if argv is None else argv
	if "--debug" in argv or os.environ.get("PRS_DEBUG"):
		debug(argv)
	if "--help" in argv or "-h" in argv:
		return print(USAGE)
	if "--version" in argv:
		return print(f"gitdashy {VERSION}")
	if len(argv) > 1 and argv[1] == "sync-memory":
		return sync_memory(argv)
	if len(argv) > 1 and argv[1] == "remember":
		return remember(argv)
	if len(argv) > 1 and argv[1] == "install":
		return install(argv)
	if len(argv) > 1 and argv[1] == "self-review":
		return self_review(argv)
	if len(argv) > 1 and argv[1] == "setup":
		return setup(argv)
	if len(argv) > 1 and argv[1] == "init":
		return init(argv)
	if len(argv) > 1 and argv[1] == "bind":
		return bind(argv)
	if len(argv) > 1 and argv[1] == "friction":
		return friction(argv)
	if len(argv) > 1 and argv[1] == "api":
		return api(argv)
	if len(argv) > 1 and argv[1] == "drafts":
		return drafts(argv)
	if len(argv) > 1 and argv[1] == "teams":
		return teams(argv)
	if len(argv) > 1 and argv[1] == "self-check":
		rows = review_mod.self_check(arg("--model", config.DEFAULT_MODEL, str, argv))
		for name, ok, detail in rows:
			print(f"{'ok  ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
		raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
	# ponytail: an unknown subcommand is an ERROR, not the dashboard. `gitdashy api …` against a build
	# without that command fell through to here and opened curses, which is how a review crashed rather
	# than being told the command was not there. Last, so every command above still gets its turn.
	if len(argv) > 1 and not argv[1].startswith("-"):
		raise SystemExit(f"gitdashy: no command {argv[1]!r} in {VERSION} — see gitdashy --help")
	if "--demo" in argv:
		demo.install()
	config.load()
	config.EFFORT = arg("--effort", config.EFFORT, str, argv)
	config.DEPTH = arg("--depth", config.DEPTH, str, argv)
	if config.DEPTH not in config.DEPTHS:
		return print(f"gitdashy: --depth must be low, medium, high or adaptive, not {config.DEPTH!r}")
	config.VOICE = arg("--voice", config.VOICE, lambda v: [x for x in v.split(",") if x], argv)
	if set(config.VOICE) - set(config.VOICES):
		return print(f"gitdashy: --voice must be from {', '.join(config.VOICES)}, not {config.VOICE!r}")
	config.HUNTER = arg("--hunter", config.HUNTER, lambda v: [x for x in v.split(",") if x], argv)
	if set(config.HUNTER) - set(config.HUNTERS):
		return print(f"gitdashy: --hunter must be from {', '.join(config.HUNTERS)}, not {config.HUNTER!r}")
	config.INSTRUCTIONS = arg("--instructions", config.INSTRUCTIONS, str, argv)
	# ponytail: last check before the screen goes up, and after the flags — a typo in --voice is still a
	# typo without a token. Nothing in the dashboard works without one, and three rows of "401 Bad
	# credentials" under curses is a worse way to learn that than a message with the fix in it.
	if "--demo" not in argv and not github.token():
		return print(NO_TOKEN)
	interval, auto = arg("--interval", config.INTERVAL, int, argv), "--auto" in argv
	model = arg("--model", config.DEFAULT_MODEL, str, argv)
	# ponytail: same State, same flags, one branch. The GUI is a second front end over the core, not a
	# second app — everything above this line is shared, so a flag added there works in both.
	if "--gui" in argv:
		# ponytail: three ways in, one branch. --no-open means the desktop shell spawned us and wants the
		# server only; otherwise --gui IS the app: built locally, else downloaded from the latest release,
		# else the browser when the download fails.
		# No recursion: the shell always adds --no-open to the command it runs.
		if "--no-open" in argv:
			# ponytail: the token arrives in the ENVIRONMENT, not argv — argv is world-readable in ps,
			# and this token starts paid review runs. Popped so it does not ride along into the Claude
			# subprocesses a review spawns.
			return web.main(interval, auto, model, open_browser=False, orphan_exit=True,
			                port=arg("--port", 0, int, argv),
			                token=os.environ.pop("GITDASHY_GUI_TOKEN", ""))
		if "--browser" not in argv:
			if exe := web.desktop_binary() or web.download_desktop():
				return web.launch_desktop(exe, argv)
		return web.main(interval, auto, model, open_browser=True)
	curses.wrapper(screen.main, interval, auto, model)
