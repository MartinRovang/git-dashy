"""Argument parsing and the curses entry point. ponytail: sys.argv scan, argparse would be more code than this."""
import curses
import os
import sys

from . import VERSION, config, demo
from .core import memory, mirror, review as review_mod, team
from .ui import screen

USAGE = f"""gitdashy {VERSION} — terminal dashboard of open PRs: mine, review-requested, assigned.

Usage: gitdashy [--interval SECONDS] [--auto] [--model NAME] [--effort LEVEL] [--depth LEVEL] [--instructions FILE] [--demo] [--version] [--help]
       gitdashy sync-memory --into PATH [--repo owner/name] [--no-pull] [--general]
       gitdashy remember [--repo owner/name | --general] FACT
       gitdashy self-check [--model NAME]

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
	if len(argv) > 1 and argv[1] == "self-check":
		rows = review_mod.self_check(arg("--model", config.DEFAULT_MODEL, str, argv))
		for name, ok, detail in rows:
			print(f"{'ok  ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
		raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
	if "--demo" in argv:
		demo.install()
	config.EFFORT = arg("--effort", config.EFFORT, str, argv)
	config.DEPTH = arg("--depth", config.DEPTH, str, argv)
	if config.DEPTH not in config.DEPTHS:
		return print(f"gitdashy: --depth must be low, medium, high or adaptive, not {config.DEPTH!r}")
	config.INSTRUCTIONS = arg("--instructions", config.INSTRUCTIONS, str, argv)
	curses.wrapper(screen.main, arg("--interval", config.INTERVAL, int, argv), "--auto" in argv,
	               arg("--model", config.DEFAULT_MODEL, str, argv))
