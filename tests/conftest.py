"""Shared fixtures. ponytail: one fake screen, one temp log, no framework."""
import json
import os
import urllib.request

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
	monkeypatch.setattr(config, "SPLASH_MIN", 0)
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "no-team"))
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
	# ponytail: github.py talks HTTP now, so a test that forgets to fake it would hit the real API with
	# the developer's own token — which is exactly what happened once. No test gets a socket for free.
	POSTED.clear()
	monkeypatch.setattr(urllib.request, "urlopen", recorder)
	for var in ("GH_TOKEN", "GITHUB_TOKEN"):
		monkeypatch.delenv(var, raising=False)  # a token on the machine must not change what a test sends
	# ponytail: $GITHUB_API is read at import, so a developer pointed at an Enterprise host would fail
	# every test that names a url. The environment does not get to decide what the suite asserts.
	monkeypatch.setattr(github, "API", "https://api.github.com")
	monkeypatch.setattr(github, "GRAPHQL", "https://api.github.com/graphql")
	# ponytail: pin what api_cmd READS, not api_cmd itself — a stub here would have hidden the very bug
	# it exists to keep out of the suite (a `gitdashy` on PATH that is a different, older build).
	monkeypatch.setattr(review.shutil, "which", lambda c: os.path.join(review.HERE, "prs.py"))
	monkeypatch.setattr(github, "_me", "")  # the login is cached for the process; not across tests
	# ponytail: --demo's install() must not leak into the next test. This used to name three attrs
	# while install() swapped eight, so github.copy, collaborators, request_review, self_review and
	# update_available stayed faked for every module collected afterwards. demo.restore() puts back
	# whatever was actually swapped, so a swap added later is covered without touching this file.
	yield
	demo.restore()


POSTED = []  # (url, body) of every github API call the test under way made


def recorder(req, timeout=None):
	"""The default urlopen: records github calls and answers {}, refuses to reach anything else.

	ponytail: no test gets a socket. github.py talks HTTP now, so without this a test that forgets to
	fake it hits the real API with the developer's own token — which is exactly what happened once.
	"""
	if not req.full_url.startswith((github.API, github.GRAPHQL)):  # on Enterprise the two are siblings
		raise AssertionError(f"test tried to reach {req.full_url}")
	POSTED.append((req.full_url, json.loads(req.data) if req.data else None))
	return Body(b"{}")


class Body:
	"""A response body handed over in one chunk. ponytail: urlopen's contract is read() and a context."""
	def __init__(self, raw): self.raw = raw
	def read(self): return self.raw
	def __enter__(self): return self
	def __exit__(self, *a): return False


class Writes:
	"""The github calls that WROTE, indexable after the fact. A review reads the PR and its diff too, and
	those are not what a test asserting "it posted the verdict" means."""
	def rows(self): return [c for c in POSTED if c[1] is not None]
	def __getitem__(self, i): return self.rows()[i]
	def __len__(self): return len(self.rows())
	def __iter__(self): return iter(self.rows())
	def __eq__(self, other): return self.rows() == other


@pytest.fixture
def posted():
	return Writes()


def fake_http(handler):
	"""urlopen replacement: handler(url, body-dict-or-None) -> the response text (or a dict, as json)."""
	def go(req, timeout=None):
		out = handler(req.full_url, json.loads(req.data) if req.data else None)
		return Body((out if isinstance(out, str) else json.dumps(out)).encode())
	return go


def gql_nodes(*sections):
	"""{"data": {"s0": {"nodes": [...]}, ...}} — one section per argument."""
	return {"data": {f"s{i}": {"nodes": list(ns)} for i, ns in enumerate(sections)}}


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
