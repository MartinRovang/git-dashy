"""Self-update: track the newest vX.Y.Z tag on origin. ponytail: git is the package manager until v2,
whose releases are prebuilt binaries — those are fetched over the legacy entrypoint, not checked out."""
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from .. import HERE, VERSION

REPO = "MartinRovang/github-dashy"  # the release host install.sh and the v2 binary both name
# ponytail: the same asset names ci.yml uploads and the Rust updater downloads.
ASSETS = {
	("Linux", "x86_64"): "gitdashy-linux-x86_64",
	("Darwin", "arm64"): "gitdashy-macos-arm64",
	("Darwin", "x86_64"): "gitdashy-macos-x86_64",
}


def vkey(v):
	return tuple(int(x) for x in v.split("."))  # ponytail: plain numeric tags, no pre-release parsing


def latest_release():
	"""Highest vX.Y.Z tag on origin, or "". ls-remote, so no gh auth and no API rate limit."""
	try:
		out = subprocess.run(["git", "-C", HERE, "ls-remote", "--tags", "--refs", "origin"],
		                     capture_output=True, text=True, check=True, timeout=60).stdout
		return max(re.findall(r"refs/tags/v(\d+(?:\.\d+)*)$", out, re.M), key=vkey, default="")
	except Exception:
		logging.getLogger(__name__).exception("ls-remote failed")
		return ""  # not a clone, no origin, offline


def update_available():
	"""The released version newer than ours, or ""."""
	tag = latest_release()
	return tag if tag and vkey(tag) > vkey(VERSION) else ""


def is_binary_release(version):
	"""True for the v2+ app, whose releases are prebuilt binaries, not git tags."""
	return vkey(version)[0] >= 2


def asset_name():
	"""The prebuilt binary for this machine, or "" where the release has none."""
	return ASSETS.get((platform.system(), platform.machine()), "")


def migrate(version):
	"""Fetch the v2+ binary over the legacy entrypoint and re-exec. Returns an error string, or never returns."""
	asset = asset_name()
	if not asset:
		return f"no binary for {platform.system()}-{platform.machine()}"
	bin_dir = os.path.join(os.path.expanduser("~"), ".local", "bin")
	os.makedirs(bin_dir, exist_ok=True)
	target = os.path.join(bin_dir, "gitdashy")
	url = f"https://github.com/{REPO}/releases/download/v{version}/{asset}"
	try:
		with urllib.request.urlopen(url, timeout=300) as resp:
			# ponytail: written beside the target, so the replace is on one filesystem and atomic. It
			# swaps the legacy symlink itself, leaving the prs.py it pointed at alone.
			with tempfile.NamedTemporaryFile(dir=bin_dir, prefix=".gitdashy.", delete=False) as tmp:
				shutil.copyfileobj(resp, tmp)
				tmp_path = tmp.name
		os.chmod(tmp_path, 0o755)
		os.replace(tmp_path, target)
	except Exception as e:
		logging.getLogger(__name__).exception("migration to %s failed", version)
		return str(e)[:60]
	os.execv(target, [target, *sys.argv[1:]])


def apply_update(version):
	"""v2+ releases are the new app: migrate to the binary. Tags below are git checkouts, as before."""
	if is_binary_release(version):
		return migrate(version)
	try:
		for a in (["fetch", "--tags", "-q"], ["checkout", "-q", f"v{version}"]):
			subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, check=True, timeout=120)
	except subprocess.CalledProcessError as e:
		return (e.stderr or "checkout failed").strip().splitlines()[-1][:60]
	except Exception as e:
		logging.getLogger(__name__).exception("update to %s failed", version)
		return str(e)[:60]
	os.execv(sys.executable, [sys.executable, os.path.join(HERE, "prs.py"), *sys.argv[1:]])
