"""Argument parsing and the curses entry point. ponytail: sys.argv scan, argparse would be more code than this."""
import curses
import os
import sys

from . import HERE, VERSION, config, demo
from .core import install as install_mod, memory, mirror, review as review_mod, team
from .ui import screen

USAGE = f"""gitdashy {VERSION} — terminal dashboard of open PRs: mine, review-requested, assigned.

Usage: gitdashy [--interval SECONDS] [--auto] [--model NAME] [--effort LEVEL] [--depth LEVEL] [--instructions FILE] [--demo] [--version] [--help]
       gitdashy sync-memory --into PATH [--repo owner/name] [--no-pull] [--general]
       gitdashy remember [--repo owner/name | --general] FACT
       gitdashy setup
       gitdashy self-check [--model NAME]
       gitdashy install [--full [--corpus URL]] [--dry-run] [--yes] [--no-setup] [--uninstall]
       gitdashy init --into DIR --loader FILE [--repo owner/name] | --into DIR --forget

  --interval N   seconds between refreshes (default {config.INTERVAL}); i picks 1/2/5/10/15m
  --auto         Claude reviews every review-requested PR that appears from now on
  --model NAME   review model (default {config.DEFAULT_MODEL}, or $PRS_MODEL); m picks at runtime
  --effort LEVEL claude effort: low, medium, high, xhigh, max (default {config.EFFORT}, or $PRS_EFFORT); e picks
  --depth LEVEL  review depth: low, medium, high, adaptive (default {config.DEPTH}, or $PRS_DEPTH); d picks
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

install wires this machine so every session reads the cross-repo facts: two symlinks in the agent config
  directory and two imports. It explains itself and asks before writing anything (--yes to skip the ask,
  --dry-run to see it and stop). Idempotent, and --uninstall reverses exactly what it wrote. --full ends
  by offering the two briefs; --no-setup skips that, as does a non-terminal stdin. Reviews need
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

setup asks for the two things a corpus cannot work out for itself: who you are, and what the work is
  for. It writes USER.md and a project brief — yours when you are on your own, the team's when you are in
  one, and every review reads the brief. Re-runnable: a blank answer KEEPS what is already there rather
  than clearing it, the prompt shows you what that is, and sections you added by hand are left alone.

self-check makes one real claude call and proves the three things every review depends on: that the
  appended review lens arrives, that --safe-mode hides the machine's CLAUDE.md, and that tools still run
  under it. A unit test can assert the flags are passed; only this can tell you they are honoured.

Keys: j/k move, o open, ⏎ review (REVIEW REQUESTED) or read the review (REVIEWED),
␣ unfold/fold older reviews of the same PR, a auto, m model, d depth, e effort, t REVIEWED history window, i interval, s summaries
(each opens a dropdown under the setting: j/k or the same key moves, ⏎ picks, esc keeps), D show/hide drafts (hidden by default),
S/R/V/K settings menus (all / Reviewer / View / Knowledge), ? show each setting's key in the header,
L local memory dir, C team checkout dir, n repo memory, g general memory ($EDITOR),
P share your facts with the team (t share, x forget), Z dream (Claude tidies all memory, you approve),
T team repo setup or leave, u install the newest release, r refresh, q quit."""


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
	if "--no-setup" in argv or not sys.stdin.isatty():
		return  # ponytail: never prompt a script; --no-setup is the explicit opt-out for one that is a tty
	if install_mod.setup_done():
		return  # ponytail: nothing to offer when both briefs are already written
	try:
		if input("\nAnswer the two briefs now? [Y/n] ").strip().lower() in ("", "y", "yes"):
			print("")
			setup(argv)
		else:
			print("`gitdashy setup` whenever you want them — nothing else is waiting on it.")
	except (EOFError, KeyboardInterrupt):
		print("\n`gitdashy setup` whenever you want them.")


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
	if len(argv) > 1 and argv[1] == "setup":
		return setup(argv)
	if len(argv) > 1 and argv[1] == "init":
		return init(argv)
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
	config.INSTRUCTIONS = arg("--instructions", config.INSTRUCTIONS, str, argv)
	curses.wrapper(screen.main, arg("--interval", config.INTERVAL, int, argv), "--auto" in argv,
	               arg("--model", config.DEFAULT_MODEL, str, argv))
