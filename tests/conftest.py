"""Shared fixtures. ponytail: one fake screen, one temp log, no framework."""
import json
import pytest

from dashy import config
from dashy.core import github, log, memory, review, state, update
from dashy.ui import screen as ui

PR = {"repository": {"nameWithOwner": "a/b", "name": "b"}, "number": 7, "url": "u", "title": "T",
      "isDraft": False, "author": {"login": "me"}, "updatedAt": "2020-01-01T00:00:00Z"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
	"""Never touch the real log, the real git remote, or wait on the splash."""
	monkeypatch.setattr(log, "LOG", str(tmp_path / "log.jsonl"))
	monkeypatch.setattr(config, "SPLASH_MIN", 0)
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "no-team"))
	monkeypatch.setattr(update, "update_available", lambda: "")
	# ponytail: re-set the swappable attrs so --demo's install() can't leak into the next test
	monkeypatch.setattr(github, "fetch", github.fetch)
	monkeypatch.setattr(review, "review", review.review)
	monkeypatch.setattr(memory, "dream", memory.dream)


class Result:
	def __init__(self, stdout=""):
		self.stdout = stdout


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
