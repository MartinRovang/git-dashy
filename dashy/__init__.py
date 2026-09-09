"""github-dashy — a terminal dashboard for the PRs you care about, with a one-key Claude review."""
import logging
import os

VERSION = "1.41.0"
logging.getLogger(__name__).addHandler(logging.NullHandler())  # silent unless cli.debug() turns the file on
HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # repo root; realpath: installed as a symlink
