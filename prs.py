#!/usr/bin/env python3
"""Entry point — the app lives in the dashy/ package. Run ./prs.py --help."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # runs fine as a symlink on PATH

from dashy.cli import run

if __name__ == "__main__":
	run()
