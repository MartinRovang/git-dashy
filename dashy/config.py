"""Tunables and env overrides. ponytail: module-level constants, no config file."""
import os

MODELS = ["opus", "sonnet", "fable"]  # cycle list, pass any name via --model
DEFAULT_MODEL = os.environ.get("PRS_MODEL", "opus")
EFFORTS = ["", "low", "medium", "high", "xhigh", "max"]  # e cycles; "" = claude's default
DEPTHS = ["adaptive", "low", "medium", "high"]  # d cycles
EFFORT = os.environ.get("PRS_EFFORT", "")  # claude --effort: low, medium, high, xhigh, max; "" = claude's default
DEPTH = os.environ.get("PRS_DEPTH", "adaptive")  # review depth: low, medium, high, adaptive
INSTRUCTIONS = os.environ.get("PRS_INSTRUCTIONS", "")  # text file appended to the review prompt, --instructions overrides
LOG = os.environ.get("PRS_LOG", os.path.expanduser("~/.prs_reviewed.jsonl"))  # jsonl, one review per line
INTERVAL = 300  # seconds between refreshes
SPLASH_MIN = 1.0  # seconds the startup splash stays up even if gh is fast
SUBS = ["all", "open", "off"]  # which rows get a summary line under them
WINDOWS = [1, 4, 6, None]  # hours of REVIEWED history to show, None = all
STATUS = {"approve": "✓ approved", "request_changes": "✗ changes requested", "comment": "~ commented"}
