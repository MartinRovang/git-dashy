"""Self-update: track the newest vX.Y.Z tag on origin. ponytail: git is the package manager."""
import os
import re
import subprocess
import sys

from .. import HERE, VERSION


def vkey(v):
	return tuple(int(x) for x in v.split("."))  # ponytail: plain numeric tags, no pre-release parsing


def latest_release():
	"""Highest vX.Y.Z tag on origin, or "". ls-remote, so no gh auth and no API rate limit."""
	try:
		out = subprocess.run(["git", "-C", HERE, "ls-remote", "--tags", "--refs", "origin"],
		                     capture_output=True, text=True, check=True, timeout=60).stdout
		return max(re.findall(r"refs/tags/v(\d+(?:\.\d+)*)$", out, re.M), key=vkey, default="")
	except Exception:
		return ""  # not a clone, no origin, offline


def update_available():
	"""The released version newer than ours, or ""."""
	tag = latest_release()
	return tag if tag and vkey(tag) > vkey(VERSION) else ""


def apply_update(version):
	"""Check out the release tag, then re-exec so the new code keeps running. Returns an error string, or never returns."""
	try:
		for a in (["fetch", "--tags", "-q"], ["checkout", "-q", f"v{version}"]):
			subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, check=True, timeout=120)
	except subprocess.CalledProcessError as e:
		return (e.stderr or "checkout failed").strip().splitlines()[-1][:60]
	except Exception as e:
		return str(e)[:60]
	os.execv(sys.executable, [sys.executable, os.path.join(HERE, "prs.py"), *sys.argv[1:]])
