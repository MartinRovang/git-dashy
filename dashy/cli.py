"""Argument parsing and the curses entry point. ponytail: sys.argv scan, argparse would be more code than this."""
import curses
import itertools
import os
import sys

from . import HERE, VERSION, config, demo
from .core import bind as bind_mod, install as install_mod, knowledge, memory, mirror, review as review_mod, team
from .ui import screen

USAGE = f"""gitdashy {VERSION} — terminal dashboard of open PRs: mine, review-requested, assigned.

Usage: gitdashy [--interval SECONDS] [--auto] [--model NAME] [--effort LEVEL] [--depth LEVEL] [--voice A,B] [--hunter A,B] [--instructions FILE] [--demo] [--version] [--help]
       gitdashy sync-memory --into PATH [--repo owner/name] [--no-pull] [--general]
       gitdashy remember [--repo owner/name | --general] FACT
       gitdashy self-review N [--repo owner/name] [--model NAME]
       gitdashy setup
       gitdashy self-check [--model NAME]
       gitdashy install [--full [--corpus URL]] [--dry-run] [--yes] [--no-setup] [--uninstall]
       gitdashy init --into DIR --loader FILE [--repo owner/name] | --into DIR --forget
       gitdashy bind [owner/name] [--team SLUG] [--forget] | --owner OWNER [--forget] | --list
       gitdashy drafts [--repo owner/name]
       gitdashy teams [--new NAME [--desc TEXT] [--at DIR]] [--join URL|PATH [--name NAME]]
                      [--team KEY --connect URL] [--leave KEY]

  --interval N   seconds between refreshes (default {config.INTERVAL}); i picks 1/2/5/10/15m
  --auto         Claude reviews every review-requested PR that appears from now on
  --model NAME   review model (default {config.DEFAULT_MODEL}, or $PRS_MODEL); m picks at runtime
  --effort LEVEL claude effort: low, medium, high, xhigh, max (default {config.EFFORT}, or $PRS_EFFORT); e picks
  --depth LEVEL  review depth: low, medium, high, adaptive (default {config.DEPTH}, or $PRS_DEPTH); d picks
  --voice A,B    how the posted body is phrased: review, caveman, bot, any mix (default review, or $PRS_VOICE); x toggles
  --hunter A,B   extra lenses, each a section of its own findings: ponytail, security, tests (or $PRS_HUNTER); h toggles
  --instructions FILE  text file appended to every review prompt (or $PRS_INSTRUCTIONS)
  --demo         canned PRs and a fake reviewer — nothing touches gh, claude or your real log

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

install wires this machine so every session reads the cross-repo facts: two symlinks in the agent config
  directory and two imports. It explains itself and asks before writing anything (--yes to skip the ask,
  --dry-run to see it and stop). Idempotent, and --uninstall reverses exactly what it wrote. --full ends
  by offering the two briefs; --yes, --no-setup or a non-terminal stdin all skip that. Reviews need
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

teams lists the teams this machine has joined, what each calls itself, and what it covers.
  A team is a git repo — or just a directory — that pools what reviews learn. Whoever can reach it is
  on the team; there is no service and no account. Its name and description live in team.json inside
  it, so everyone who clones it sees the same ones.
  --new starts one right here with no remote at all: a name, a description, and files. --at DIR keeps
  it somewhere else and links to it. When you have a repo for it, --team KEY --connect URL points it
  there and pushes; the key does not change, so every binding still holds.
  --join clones one that exists, from any git URL or a path (a bare owner/name is expanded to GitHub
  as a convenience, nothing more). It takes its key from the team's own name.
  --leave drops one checkout, refusing while it holds unpushed work.
  Several teams at once; which one applies to a repo is `gitdashy bind`.

setup asks for the two things a corpus cannot work out for itself: who you are, and what the work is
  for. It writes USER.md and a project brief — yours when you are on your own, the team's when you are in
  one. Which repos read it is decided by `gitdashy bind`. Re-runnable: a blank answer KEEPS what is already there rather
  than clearing it, the prompt shows you what that is, and sections you added by hand are left alone.

self-check makes one real claude call and proves the three things every review depends on: that the
  appended review lens arrives, that --safe-mode hides the machine's CLAUDE.md, and that tools still run
  under it. A unit test can assert the flags are passed; only this can tell you they are honoured.

Keys: j/k move, ⏎ detail pane, r review (REVIEW REQUESTED), p pre-review your own PR posting nothing,
v read the full review of the selected PR (any row that has one), Y open the pre-review, o open,
␣ unfold/fold older reviews of the same PR, a auto, m model, d depth, e effort, x voices, h hunters, t REVIEWED history window, i interval, s summaries
(each opens a dropdown under the setting: j/k or the same key moves, ⏎ picks, esc keeps), D show/hide drafts (hidden by default),
S/R/V/K settings menus (all / Reviewer / View / Knowledge), ? show each setting's key in the header,
L local memory dir, C where all team checkouts live, n repo memory, g general memory ($EDITOR),
b bind the selected repo to a team (1-8 pick, o whole owner, x unbind),
P share your facts with the team (t share, x forget), W what is waiting to become a fact (t accept, x drop), Z dream (Claude tidies all memory, you approve),
T teams (n start, a join, e edit its brief, d describe, c connect a remote, x leave), u install the newest release, f refresh, q quit."""


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
	if install_mod.setup_done():
		return  # ponytail: nothing to offer when both briefs are already written
	later = "`gitdashy setup` whenever you want them — nothing else is waiting on it."
	try:
		if input("\nAnswer the two briefs now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
			return print(later)
	except (EOFError, KeyboardInterrupt):
		return print("\n" + later)
	print("")
	try:
		setup(argv)
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


def setup(argv):
	"""Ask for the two things a corpus cannot work out on its own: who you are, and what this is for."""
	print("Two short briefs. Blank keeps what is already there — the prompt shows you what. "
	      "Edit the files later; nothing here is final.\n")
	def ask(prompt):
		try:
			return input(f"  {prompt}\n  > ").strip()
		except (EOFError, KeyboardInterrupt):
			raise SystemExit("\nnothing written")
	team.activate()
	print("\n" + "\n".join(install_mod.setup(ask)))


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
	print("\n".join(install_mod.wire_repo(into, loader, repo)))


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
	if memory.already_known(scope, fact):
		return print(f"gitdashy: {where} already knows that")
	promoted = memory.append(scope, fact)
	team.push_dir(config.MEMORY_DIR, f"memory: remembered for {where}", "mine")
	team.push(f"memory: evidence for {where}")  # ponytail: a promotion writes the pool, which lives over there
	if promoted:  # ponytail: the counter counts observations; it does not know which surface each came from
		return print(f"gitdashy: {where} — confirmed by a second independent observation: {promoted[0]}")
	print(f"gitdashy: {where} — drafted; one more independent observation confirms it")


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
		to = arg("--team", "", str, argv) or bind_mod.team_key()
		if not to:
			raise SystemExit("gitdashy: not in a team — join one with T in the dashboard, or pass --team SLUG")
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
		to = arg("--team", "", str, argv) or bind_mod.team_key()
		if not to:
			raise SystemExit("gitdashy: not in a team — join one with T in the dashboard, or pass --team SLUG")
		if err := bind_mod.bind(repo, to):
			raise SystemExit("gitdashy: " + err)
		print(f"gitdashy: {bind_mod.key(repo)} → {to}")
	# ponytail: says what the repo GETS, not that a row was written. A binding is only ever a means to
	# selecting a brief, and the one thing worth confirming is which brief a review will now be given.
	text, whose = memory.brief(repo)
	print(f"  reviews of {bind_mod.key(repo) or repo} read: {whose}" + ("" if text else " (nothing to read)"))


def drafts(argv):
	"""Show what gitdashy has heard once and not confirmed. Read-only; W in the dashboard acts on it."""
	team.activate()
	only = arg("--repo", "", str, argv)
	rows = memory.waiting()
	if only:
		rows = [r for r in rows if (r[0] or "general") == only]
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
		key = arg("--team", "", str, argv) or (team.joined()[0] if len(team.joined()) == 1 else "")
		if not key:
			raise SystemExit(f"gitdashy: say which team: --team {' | --team '.join(team.joined()) or 'NAME'}")
		if err := team.connect(key, url):
			raise SystemExit("gitdashy: " + err)
		print(f"gitdashy: {key} now pushes to {url}")
	elif join := arg("--join", "", str, argv):
		# ponytail: what CHANGED, not joined()[-1] — that is the last alphabetically, so already being
		# in "zulu" and joining "acme" printed "joined zulu".
		before = set(team.joined())
		if err := team.setup(join, arg("--name", "", str, argv)):
			raise SystemExit("gitdashy: " + err)
		fresh = sorted(set(team.joined()) - before)
		print(f"gitdashy: joined {fresh[0] if fresh else join}"
		      + (f"  ({team.ERROR})" if team.ERROR else ""))
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
		print(f"      {', '.join(owners + bound) or 'no repos bound to it yet'}")


def run(argv=None):
	argv = sys.argv if argv is None else argv
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
	if len(argv) > 1 and argv[1] == "drafts":
		return drafts(argv)
	if len(argv) > 1 and argv[1] == "teams":
		return teams(argv)
	if len(argv) > 1 and argv[1] == "self-check":
		rows = review_mod.self_check(arg("--model", config.DEFAULT_MODEL, str, argv))
		for name, ok, detail in rows:
			print(f"{'ok  ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
		raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
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
	curses.wrapper(screen.main, arg("--interval", config.INTERVAL, int, argv), "--auto" in argv,
	               arg("--model", config.DEFAULT_MODEL, str, argv))
