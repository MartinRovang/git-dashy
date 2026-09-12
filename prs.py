#!/usr/bin/env python3
"""Legacy entrypoint, kept in the v2 tree so pre-1.47.1 installs survive the upgrade.

Those versions update by `git checkout v2.0.0` and then re-exec HERE/prs.py — a file the rewrite
otherwise deleted, leaving a dangling ~/.local/bin/gitdashy symlink. ponytail: this hands the
machine to install.sh sitting next to it (same asset table, same symlink repoint) and execs the
binary it drops. 1.47.1+ never gets here; its updater downloads the binary itself.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))  # runs fine as a symlink on PATH
TARGET = os.path.join(os.path.expanduser("~"), ".local", "bin", "gitdashy")

if __name__ == "__main__":
	# ponytail: quiet, because the caller may still be inside curses; the binary redraws anyway.
	r = subprocess.run(["sh", os.path.join(HERE, "install.sh")], capture_output=True, text=True)
	if r.returncode != 0 or not os.path.exists(TARGET):
		sys.exit(f"gitdashy v2 is a prebuilt binary and installing it failed:\n"
		         f"{(r.stderr or r.stdout).strip()[-300:]}\n"
		         f"Install it by hand from https://github.com/MartinRovang/github-dashy/releases/latest")
	os.execv(TARGET, [TARGET, *sys.argv[1:]])
