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


def path():
	"""Where the beat is written. "" in demo mode, which writes nothing anywhere."""
	return config.SETTINGS and os.path.join(os.path.dirname(config.SETTINGS) or ".", ".prs_dashboard")


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
		if got["host"] != HOST or time.time() - got["at"] > got["interval"] + GRACE:
			return False
		os.kill(int(got["pid"]), 0)
	except (OSError, ValueError, KeyError, TypeError):
		return False
	return True
