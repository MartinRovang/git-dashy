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
import fcntl
import json
import os
import socket
import tempfile
import time

from .. import config

# ponytail: the HOST is recorded because a pid is only meaningful on the machine that made it, and
# a memory dir synced between two machines carries this file along. Without it, a stale pid from
# the laptop can match a live unrelated process on the desktop and a dead dashboard reads as alive.
HOST = socket.gethostname()
GRACE = 90  # seconds past one interval before a beat counts as stopped, for a tick that ran long
# ponytail: the declared interval is NOT clamped. A clamp to max(config.INTERVALS) was tried and made
# an honest `--interval 1800` read as dead; the host and live-pid checks below are what bound a lying
# beat, and being wrong here fails towards pulling too often.
LOCK = ".prs_pulling"  # flock'd for one team.pull(), by whichever process got there first


def _beside_settings(name):
	"""Where a small cross-process file of ours lives, or "" in demo mode, which writes nothing at all.
	ponytail: see the module docstring for why it is not under the memory dir."""
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
	# ponytail: whole, then renamed, the same shape mirror._write uses — and for a sharper reason. A
	# plain open(p, "w") truncates first, and a reader landing in that window gets a short file, reads
	# it as "no dashboard", and goes and pulls. That window sits exactly where the beat is supposed to
	# be doing its work, and this is the one writer that can close it.
	tmp = ""
	try:
		fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".", prefix=".prs_dashboard.")
		with os.fdopen(fd, "w") as f:
			json.dump({"pid": os.getpid(), "host": HOST, "at": time.time(), "interval": interval}, f)
		os.replace(tmp, p)
	except OSError:
		try:
			os.remove(tmp)
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
		if got["host"] != HOST or time.time() - got["at"] > float(got["interval"]) + GRACE:
			return False
		os.kill(int(got["pid"]), 0)
	except (OSError, ValueError, KeyError, TypeError):
		return False
	return True


_HELD = {}  # path -> the open fd holding its flock, so unclaim can let go of exactly what claim took


def claim():
	"""Take the pull lock, or False when somebody else holds it. Release it with `unclaim`.

	ponytail: the heartbeat closes the dashboard-versus-hook race and NOT hook-versus-hook — a
	background sync writes no beat, so two sessions opened at once (a terminal and an editor is the
	ordinary case) both saw nothing running and both ran `pull --rebase` in one checkout. `team._lock`
	is a threading.Lock and does not reach across processes.
	ponytail: FLOCK, after a hand-rolled O_EXCL lock was written and found racy twice. That one needed
	a stale-break timeout, a rename-aside, a pid written into the file and a pid-checked release, and
	the break was still two steps — stat the mtime, then rename — so two claimants that both read the
	old mtime could both end up holding it. The kernel owns this one: exactly one holder, released on
	close AND on the process dying however it dies, so a crash cannot leave a lock nobody can clear.
	ponytail: Unix only, which this already is — curses, os.kill and the whole review path assume it.
	"""
	if not (p := _beside_settings(LOCK)):
		return True  # demo mode writes nothing and pulls nothing; there is no one to race
	if p in _HELD:
		return False  # ponytail: not reentrant. Two pulls in one process are the case this guards.
	try:
		fd = os.open(p, os.O_CREAT | os.O_RDWR, 0o600)
	except OSError:
		return True  # nowhere to put a lock is not a reason to stop syncing
	try:
		fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
	except OSError:  # BlockingIOError on a held lock; anything else is not ours to force
		os.close(fd)
		return False
	_HELD[p] = fd
	return True


def unclaim():
	"""Give back the pull lock if this process holds it. Never raises: it runs in a finally."""
	if fd := _HELD.pop(_beside_settings(LOCK), None):
		try:
			fcntl.flock(fd, fcntl.LOCK_UN)
		except OSError:
			pass
		os.close(fd)
