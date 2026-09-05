"""Tunables and env overrides. ponytail: module-level constants, no config file."""
import json
import os

MODELS = ["opus", "sonnet", "fable"]  # cycle list, pass any name via --model
DEFAULT_MODEL = os.environ.get("PRS_MODEL", "opus")
EFFORTS = ["", "low", "medium", "high", "xhigh", "max"]  # e cycles; "" = claude's default
DEPTHS = ["adaptive", "low", "medium", "high"]  # d cycles
EFFORT = os.environ.get("PRS_EFFORT", "medium")  # claude --effort: low, medium, high, xhigh, max; "" = claude's default
DEPTH = os.environ.get("PRS_DEPTH", "adaptive")  # review depth: low, medium, high, adaptive
VOICES = ["review", "caveman", "bot"]  # how the posted body is phrased, in this order; x toggles any number, at least one
VOICE = [v for v in os.environ.get("PRS_VOICE", "review").split(",") if v]  # ponytail: a list, json has no set; empty = review
HUNTERS = ["ponytail", "security", "tests"]  # extra lenses, each appends a section of its own findings; h toggles
HUNTER = [v for v in os.environ.get("PRS_HUNTER", "").split(",") if v]
INSTRUCTIONS = os.environ.get("PRS_INSTRUCTIONS", "")  # text file appended to the review prompt, --instructions overrides
TEAM = os.environ.get("PRS_TEAM", os.path.expanduser("~/.prs_team"))  # git checkout shared with the team; T sets it up
MEMORY_DIR = os.environ.get("PRS_MEMORY", os.path.expanduser("~/.prs_memory"))  # general.md + one md per repo
LOG = os.environ.get("PRS_LOG", os.path.expanduser("~/.prs_reviewed.jsonl"))  # jsonl, one review per line
# ponytail: joining a team moves LOG into the checkout; MEMORY_DIR stays yours, since memory.sources()
# reads both. These two keep the solo locations, so K can show where memory lives and leaving has a home.
LOCAL_MEMORY, LOCAL_LOG = MEMORY_DIR, LOG
INTERVALS = [60, 120, 300, 600, 900]  # i cycles
INTERVAL = 300  # seconds between refreshes, --interval overrides
NOTIFY = os.environ.get("PRS_NOTIFY", "1") != "0"  # desktop popup when a PR asks for me; the Esc menu toggles it
THEME = os.environ.get("PRS_THEME", "dashy")  # colour theme, the Esc menu cycles it; names in ui.screen.THEMES
SPLASH_MIN = 1.0  # seconds the startup splash stays up even if gh is fast
SUBS = ["all", "open", "off"]  # which rows get a summary line under them
SUB = "all"
WINDOWS = [1, 4, 6, None]  # hours of REVIEWED history to show, None = all
WINDOW = 4
DRAFTS = False  # show draft PRs; D toggles
SETTINGS = os.environ.get("PRS_SETTINGS", os.path.expanduser("~/.prs_settings.json"))  # runtime picks land here
SAVED = {"model": "DEFAULT_MODEL", "interval": "INTERVAL", "subs": "SUB", "window": "WINDOW", "drafts": "DRAFTS",
         "depth": "DEPTH", "effort": "EFFORT", "notify": "NOTIFY", "theme": "THEME", "voice": "VOICE", "hunter": "HUNTER"}  # json key -> constant
ENV = {"model": "PRS_MODEL", "depth": "PRS_DEPTH", "effort": "PRS_EFFORT", "notify": "PRS_NOTIFY", "theme": "PRS_THEME", "voice": "PRS_VOICE", "hunter": "PRS_HUNTER"}


def load():
	"""Saved settings override the defaults above; an env var or CLI flag still wins over the file."""
	try:
		with open(SETTINGS) as f:
			saved = json.load(f)
	except (OSError, ValueError):
		return
	for key, name in SAVED.items():
		if key in saved and ENV.get(key, "") not in os.environ:
			globals()[name] = saved[key]
	# ponytail: a saved checklist may name a box that no longer exists (ponytail was a voice before it
	# was a hunter). Drop it rather than refuse to start over a file nobody typed.
	VOICE[:] = [v for v in VOICE if v in VOICES] or ["review"]
	HUNTER[:] = [h for h in HUNTER if h in HUNTERS]


def save(values):
	if SETTINGS:  # ponytail: "" (demo) means never write
		with open(SETTINGS, "w") as f:
			json.dump(values, f, indent=1)


STATUS = {"approve": "✓ approved", "request_changes": "✗ changes requested", "comment": "~ commented"}
