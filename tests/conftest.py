"""Shared fixtures. ponytail: one fake screen, one temp log, no framework."""
import json
import pytest

from dashy import demo, config
from dashy.core import bind, github, log, memory, review, state, team, update
from dashy.ui import screen as ui

PR = {"repository": {"nameWithOwner": "a/b", "name": "b"}, "number": 7, "url": "u", "title": "T",
      "isDraft": False, "author": {"login": "me"}, "updatedAt": "2020-01-01T00:00:00Z"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
	"""Never touch the real log, the real git remote, or wait on the splash."""
	monkeypatch.setattr(log, "LOG", str(tmp_path / "log.jsonl"))
	# ponytail: the CONSTANTS too, not just the module attr. Anything reading config.LOG or LOCAL_LOG
	# wrote to the real ~/.prs_reviewed.jsonl while everything else read the patched attr — 39 fixture
	# entries were found in it, 8 of them from earlier sessions. Pinning one of a pair is how that hides.
	monkeypatch.setattr(config, "LOG", str(tmp_path / "log.jsonl"))
	monkeypatch.setattr(config, "LOCAL_LOG", str(tmp_path / "log.jsonl"))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "memory"))
	monkeypatch.setattr(config, "SPLASH_MIN", 0)
	# ponytail: the parse cache is keyed on (mtime_ns, size) and module-global — two tests writing the
	# same tmp path within one stat tick would otherwise see each other's entries.
	monkeypatch.setattr(log, "_CACHE", {})
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "no-team"))
	# ponytail: the plural home too. A real ~/.prs_teams on the machine running the suite would make
	# team_dir() resolve slugs from whoever's laptop this is.
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "no-teams"))
	# ponytail: a real ~/.prs_bindings on the machine running the suite would SELECT briefs during it,
	# so a test asserting "unbound falls back to yours" would pass or fail on whose laptop it ran.
	monkeypatch.setattr(bind, "BINDINGS", str(tmp_path / "bindings"))
	# ponytail: demo.install() and team.activate() write these globals directly, so without pinning them here
	# one test's temp paths leak into the next — the header reads both on every draw
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "memory"))
	monkeypatch.setattr(team, "NAME", "")
	monkeypatch.setattr(team, "ERROR", "")
	monkeypatch.setenv("USER", "tester")  # ponytail: memory.whoami() reads $USER; a test must not depend on it
	monkeypatch.setattr(update, "update_available", lambda: "")
	# ponytail: --demo's install() must not leak into the next test. This used to name three attrs
	# while install() swapped eight, so github.copy, collaborators, request_review, self_review and
	# update_available stayed faked for every module collected afterwards. demo.restore() puts back
	# whatever was actually swapped, so a swap added later is covered without touching this file.
	yield
	demo.restore()


def a_team(monkeypatch, tmp_path, slug="org/t"):
	"""A joined team at ~/.prs_teams/<slug>/. Returns its memory dir.

	ponytail: a real directory with a .git in it, because that is what "joined" MEANS now — the
	filesystem is the registry. Faking it by setting one config path stopped working the moment a
	slug had to resolve to one checkout among several, which is the whole point of the change.
	"""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	d = tmp_path / "teams" / slug.replace("/", "__")
	(d / ".git").mkdir(parents=True, exist_ok=True)
	(d / "memory").mkdir(parents=True, exist_ok=True)
	return d / "memory"


class Result:
	def __init__(self, stdout="", returncode=0, stderr=""):
		self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def claude_out(**fields):
	return Result(json.dumps({"result": "Sure:\n" + json.dumps(fields)}))


class FakeScr:
	def __init__(self, h=30, w=100):
		self.h, self.w, self.cells = h, w, {}
	def getmaxyx(self):
		return self.h, self.w
	def erase(self):
		self.cells = {}
	def refresh(self):
		pass
	noutrefresh = refresh
	def addnstr(self, y, x, s, n, attr=0):
		assert 0 <= y < self.h and 0 <= x < self.w and n >= 1, (y, x, n)
		for i, ch in enumerate(s[:n]):
			if x + i < self.w:
				self.cells[(y, x + i)] = ch
	def line(self, y):
		return "".join(self.cells.get((y, x), " ") for x in range(self.w)).rstrip()
	def text(self):
		return "\n".join(self.line(y) for y in range(self.h))


@pytest.fixture
def screen(monkeypatch):
	monkeypatch.setattr(ui, "C", lambda n: 0)
	monkeypatch.setattr(ui.curses, "A_REVERSE", 1 << 18, raising=False)
	monkeypatch.setattr(ui.curses, "A_ITALIC", 1 << 23, raising=False)
	return FakeScr()


@pytest.fixture
def st():
	return state.State(60)
