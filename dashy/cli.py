"""Argument parsing and the curses entry point. ponytail: sys.argv scan, argparse would be more code than this."""
import curses
import sys

from . import VERSION, config, demo
from .core import mirror, team
from .ui import screen

USAGE = f"""gitdashy {VERSION} — terminal dashboard of open PRs: mine, review-requested, assigned.

Usage: gitdashy [--interval SECONDS] [--auto] [--model NAME] [--effort LEVEL] [--depth LEVEL] [--instructions FILE] [--demo] [--version] [--help]
       gitdashy sync-memory --into PATH [--repo owner/name] [--no-pull]

  --interval N   seconds between refreshes (default {config.INTERVAL}); i picks 1/2/5/10/15m
  --auto         Claude reviews every review-requested PR that appears from now on
  --model NAME   review model (default {config.DEFAULT_MODEL}, or $PRS_MODEL); m picks at runtime
  --effort LEVEL claude effort: low, medium, high, xhigh, max (default {config.EFFORT}, or $PRS_EFFORT); e picks
  --depth LEVEL  review depth: low, medium, high, adaptive (default {config.DEPTH}, or $PRS_DEPTH); d picks
  --instructions FILE  text file appended to every review prompt (or $PRS_INSTRUCTIONS)
  --demo         canned PRs and a fake reviewer — nothing touches gh, claude or your real log

sync-memory copies the shared review memory into PATH as read-only mirrors (general.md, repo.md), so an
  agent session in that repo reads what the reviews learned. --repo defaults to this directory's origin.
  Refuses to write anywhere git would commit it. --no-pull skips the team fetch, for callers on a
  timeout: it mirrors whatever the last refresh pulled.

Keys: j/k move, o open, ⏎ review (REVIEW REQUESTED) or read the review (REVIEWED),
␣ unfold/fold older reviews of the same PR, a auto, m model, d depth, e effort, t REVIEWED history window, i interval, s summaries
(each opens a dropdown under the setting: j/k or the same key moves, ⏎ picks, esc keeps), D show/hide drafts (hidden by default),
S/R/V settings menus (all / Reviewer / View), ? show each setting's key in the header, n repo memory, g general memory ($EDITOR),
Z dream (Claude tidies all memory, you approve), T team repo setup, u install the newest release, r refresh, q quit."""


def arg(flag, default=None, cast=str, argv=None):
	argv = sys.argv if argv is None else argv
	return cast(argv[argv.index(flag) + 1]) if flag in argv else default


def sync_memory(argv):
	"""Mirror the shared memory into --into, for an agent session in that repo to read."""
	into = arg("--into", "", str, argv)
	if not into:
		raise SystemExit("gitdashy: sync-memory needs --into PATH")
	team.activate()  # ponytail: points MEMORY_DIR at the team checkout before we read it
	return print(mirror.sync(into, arg("--repo", "", str, argv) or team.origin_slug("."), "--no-pull" not in argv))


def run(argv=None):
	argv = sys.argv if argv is None else argv
	if "--help" in argv or "-h" in argv:
		return print(USAGE)
	if "--version" in argv:
		return print(f"gitdashy {VERSION}")
	if len(argv) > 1 and argv[1] == "sync-memory":
		return sync_memory(argv)
	if "--demo" in argv:
		demo.install()
	config.EFFORT = arg("--effort", config.EFFORT, str, argv)
	config.DEPTH = arg("--depth", config.DEPTH, str, argv)
	if config.DEPTH not in config.DEPTHS:
		return print(f"gitdashy: --depth must be low, medium, high or adaptive, not {config.DEPTH!r}")
	config.INSTRUCTIONS = arg("--instructions", config.INSTRUCTIONS, str, argv)
	curses.wrapper(screen.main, arg("--interval", config.INTERVAL, int, argv), "--auto" in argv,
	               arg("--model", config.DEFAULT_MODEL, str, argv))
