"""Is a dashboard running on this machine, and when did it last refresh?

One question, asked by two callers that must not disagree: `mirror.sync` decides whether to pull
(a running dashboard pulled less than one interval ago, so a second pull in the same checkout is
a git lock race bought for nothing), and the mirror header tells a reading session whether what it
is looking at is being kept current at all.

ponytail: NOT under MEMORY_DIR, though that is where gitdashy's other small state files live. A
memory dir is often itself a git repo that `push_dir` commits with `git add -A`, and a file whose
contents change every refresh would put one commit per tick in that history for ever. This belongs
beside the settings file, which nothing commits.
"""
import json
import os
import socket
import time

from .. import config

# ponytail: the HOST is recorded because a pid is only meaningful on the machine that made it, and
# a memory dir synced between two machines carries this file along. Without it, a stale pid from
# the laptop can match a live unrelated process on the desktop and a dead dashboard reads as alive.
HOST = socket.gethostname()
GRACE = 90  # seconds past one interval before a beat counts as stopped, for a tick that ran long
# ponytail: the beat's own interval is CLAMPED to the longest the dashboard offers. It is read from a
# file, and a file that says its interval is a year makes a dashboard that stopped a year ago still
# read as running — suppressing every background pull and the staleness warning with it. Anyone who can
# write beside the settings file has already won, so this is hardening rather than a boundary; it costs
# one call and removes the one field an attacker would reach for.
CEILING = max(config.INTERVALS)
LOCK = ".prs_pulling"  # held for the length of one team.pull(), by whichever process got there first
STUCK = 300  # seconds after which a held lock is assumed to belong to a process that died holding it


def _beside_settings(name):
	""""" in demo mode, which writes nothing anywhere. ponytail: beside the SETTINGS file, never under
	the memory dir — push_dir commits that with `git add -A`, and a file that changes every refresh
	there is one commit per refresh in that history for ever."""
	if not config.SETTINGS:
		return ""
	return os.path.join(os.path.dirname(config.SETTINGS) or ".", name)


def path():
	"""Where the beat is written."""
	return _beside_settings(".prs_dashboard")


def beat(interval):
	"""Record that a dashboard on this machine just refreshed. Never raises: a read-only home is not
	a reason to fail a tick."""
	if not (p := path()):
		return
	try:
		with open(p, "w") as f:
			json.dump({"pid": os.getpid(), "host": HOST, "at": time.time(), "interval": interval}, f)
	except OSError:
		pass


def alive():
	"""True when a dashboard on THIS machine refreshed recently enough to be trusted to keep pulling.

	Three things must hold, and each answers a different way of being wrong: the beat is from this host
	(a pid means nothing across machines), the process still exists (a dashboard killed mid-tick leaves
	its last beat behind), and the beat is younger than the interval it declared (a suspended laptop
	leaves a live pid and a beat from yesterday).
	"""
	try:
		with open(path()) as f:
			got = json.load(f)
		if got["host"] != HOST or time.time() - got["at"] > min(float(got["interval"]), CEILING) + GRACE:
			return False
		os.kill(int(got["pid"]), 0)
	except (OSError, ValueError, KeyError, TypeError):
		return False
	return True


def claim():
	"""Take the pull lock, or False when somebody else holds it. Release it with `unclaim`.

	ponytail: the heartbeat closes the dashboard-versus-hook race and NOT hook-versus-hook — a
	background sync writes no beat, so two sessions opened at once (a terminal and an editor is the
	ordinary case) both saw nothing running and both ran `pull --rebase` in one checkout. `team._lock`
	is a threading.Lock and does not reach across processes. _pull aborts a half-finished rebase, so
	nothing wedges; what it does is fire `rebase --abort` into a checkout the winner may still be
	rebasing. O_EXCL is the one primitive that is atomic on every filesystem this runs on.
	ponytail: a stuck lock is BROKEN after STUCK seconds rather than waited on. A process killed while
	holding it would otherwise stop every later sync from ever pulling, silently, which is a worse
	failure than the race this prevents — and the window it reopens is the one that was there before.
	"""
	if not (p := _beside_settings(LOCK)):
		return True  # demo mode writes nothing and pulls nothing; there is no one to race
	for attempt in (1, 2):
		try:
			fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
		except FileExistsError:
			try:
				if attempt == 2 or time.time() - os.stat(p).st_mtime < STUCK:
					return False
				os.remove(p)
			except OSError:
				return False
			continue
		except OSError:
			return True  # nowhere to put a lock is not a reason to stop syncing
		os.write(fd, str(os.getpid()).encode())
		os.close(fd)
		return True
	return False


def unclaim():
	"""Give the pull lock back. Never raises: a lock we cannot remove is broken by the next claim."""
	try:
		os.remove(_beside_settings(LOCK))
	except OSError:
		pass
