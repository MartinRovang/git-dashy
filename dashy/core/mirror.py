"""Mirror the shared review memory into a repo, so an agent session there reads what the reviews learned.

ponytail: a copy, not a symlink. Claude Code confines CLAUDE.md imports to the project tree — `~`,
absolute and symlinked paths are all refused — so a real file inside the repo is the only way in.
"""
import datetime
import os
import subprocess
import time

from .. import config
from . import heartbeat, memory, team

NAMES = ("general.md", "repo.md")  # the only names sync() ever writes or removes
HEADER = """> **Shared team memory — read-only mirror.** PR reviews write these facts; `gitdashy sync-memory`
> copies them here. Edits to this file are lost at the next sync — change the source, not the mirror.
>
> source: `{src}` · synced: {at}
{age}"""

STALE = 3600  # seconds since a team was last reached, past which the header warns rather than reports



def _ago(secs):
	"""A rough age, in the largest unit that is not a fraction. Exactness is not the point here — the
	reader is deciding whether to trust a file, and "2 days" and "51 hours" lead to the same decision."""
	for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
		if secs >= size:
			n = int(secs // size)
			return f"{n} {unit}{'s' if n > 1 else ''} ago"
	return "just now"


def _freshness(got, now):
	"""The header's age lines: one per team source that has a remote, saying when it was last reached.

	ponytail: in the FILE, not on the hook's stdout. This is what a session reads, it is imported on
	every turn, and it costs one line. A hook message scrolls past once, at the moment nobody is
	looking for it, and says nothing at all to a session started any other way.
	ponytail: the warning names the dashboard rather than the age alone, because that is the thing the
	reader can act on. "4 days ago" invites a shrug; "nothing is refreshing this" is an instruction.
	"""
	out, running = [], heartbeat.alive()
	for label, base in got:
		if label == "mine":
			continue
		at = team.fetched_at(os.path.dirname(base))
		if at is None:
			continue  # local-only team: there is no remote to be behind
		stale = not running and now - at > STALE
		out.append(f"> {label}: last pulled {_ago(now - at)}"
		           + (" — **the dashboard is not running, so this may be behind what the team has.**"
		              " Start `gitdashy`, or run `gitdashy sync-memory --into` this directory." if stale else ""))
	return "".join(l + "\n" for l in out)


def tracked(path, names=NAMES):
	"""True when git would commit a file written at `path`: inside a repo and not ignored.

	ponytail: `path` need not exist — check-ignore is pure path matching, so we ask about the real target
	but run git from the nearest directory that does exist. Fail-safe: anything but a clean "ignored"
	answer counts as tracked, so a broken git call refuses rather than leaking memory into someone's history.
	"""
	base = os.path.abspath(path)
	while not os.path.isdir(base) and os.path.dirname(base) != base:
		base = os.path.dirname(base)
	if subprocess.run(["git", "-C", base, "rev-parse", "--show-toplevel"],
	                  capture_output=True, timeout=60).returncode != 0:
		return False  # not a git repo: nothing to leak into
	# ponytail: EVERY name we write, not just the first. An ignore rule matching general.md but not
	# repo.md would answer "ignored" and we would then commit the other one — the exact leak this prevents.
	return any(subprocess.run(["git", "-C", base, "check-ignore", "-q", os.path.join(os.path.abspath(path), n)],
	                          capture_output=True, timeout=60).returncode != 0 for n in names)


def sync(into, repo="", pull=True, general=False):
	"""Mirror `repo`'s memory into `into`, and the general file too when asked. One-line report.

	ponytail: per-repo only by default. Cross-repo facts belong in a user-level instruction file, which
	loads them live everywhere; mirroring them per repo as well would put every general fact in context
	twice. general=True is for anyone who has not wired that route and wants it all in the repo.

	ponytail: pull=False for callers on a clock (a SessionStart hook) — mirrors whatever the last
	gitdashy refresh pulled, instead of risking a network round trip inside their timeout.
	"""
	# ponytail: a RUNNING dashboard already pulled, less than one interval ago, and will again. Two
	# processes running `pull --rebase` in one checkout race for git's index.lock, and the loser leaves
	# a rebase behind for the winner to trip over. Skipping here is what lets the session hook fire this
	# off in the background without having to know whether anything else is doing the same job.
	if pull and not heartbeat.alive():
		team.pull()  # newest shared memory first; a no-op when team mode is off
	# ponytail: ask BEFORE creating anything, or a refusal leaves the tree it refused to write in. And a
	# SessionStart hook calls this: an exception there is a broken hook, so failures come back as the report.
	try:
		if tracked(into, NAMES if general else NAMES[1:]):
			return f"gitdashy: refused — git would commit {into}; ignore that path before mirroring team memory there"
		os.makedirs(into, exist_ok=True)
	except OSError as e:
		return f"gitdashy: refused — {e}"
	at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
	try:
		return _write(into, repo, general, at)
	except OSError as e:
		return f"gitdashy: refused — {e}"


def _write(into, repo, general, at):
	# ponytail: ONE call, and the report is derived from it. `where` used to ask bind.of(repo)
	# independently, so a repo bound to a team you have since LEFT reported "from team X" while
	# sources() had already returned yours alone — the label and the content disagreeing about the
	# same write, which is the defect this line was changed to fix in the first place.
	got = memory.sources(repo)
	src = " + ".join(label for label, _ in got)
	age = _freshness(got, time.time())
	wrote = []
	for name, scope in zip(NAMES, (None if general else "", repo if repo else "")):
		dst = os.path.join(into, name)
		text = memory.scope_text(scope, repo) if scope is None or scope else ""
		if scope:
			# ponytail: repo.md carries the brief and the bound team's general facts ABOVE the repo's own,
			# because they reach the session no other way now. Your general facts stay out: they load
			# live through the prs-memory link, and a second copy per repo is context spent twice.
			text = "\n\n".join(t for t in (memory.session_context(repo, general_mirrored=general),
			                                f"## {repo}\n{text}" if text else "") if t)
		if text:
			# ponytail: written whole, then moved into place. There are two writers of this file now —
			# the dashboard's refresh and the background one a session hook starts — and a plain open()
			# truncates first, so a session reading at the wrong moment imported an empty or half-written
			# mirror and was told the team knows nothing. rename within one directory is atomic, so a
			# reader sees the old file or the new one.
			tmp = dst + ".part"
			try:
				with open(tmp, "w") as f:
					f.write(HEADER.format(src=src, at=at, age=age) + text + "\n")
				os.replace(tmp, dst)
			except OSError:
				# ponytail: the half-written file goes, and the exception carries on to sync()'s handler.
				# Leaving it behind puts an unexplained repo.md.part in someone's repo — inside a
				# directory the mirror promises to own the contents of — for every failed write.
				if os.path.exists(tmp):
					os.remove(tmp)
				raise
			wrote.append(name)
		elif os.path.exists(dst):
			os.remove(dst)  # ponytail: a mirror never outlives its source, or it becomes a rumour
	where = next((label for label, _ in got if label != "mine"), config.MEMORY_DIR)
	return (f"gitdashy: mirrored {', '.join(wrote) or 'nothing'} into {into}"
	        f" from {where}{' for ' + repo if repo else ''}{' · ' + team.ERROR if team.ERROR else ''}")
