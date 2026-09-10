"""Shared fixtures. ponytail: one fake screen, one temp log, no framework."""
import json
import os
import shutil
import urllib.request

import pytest

from dashy import demo, config
from dashy.core import bind, github, install, log, memory, review, state, team, update
from dashy.ui import screen as ui

# ponytail: captured at import, BEFORE the autouse fixture stubs it. The two tests that are about
# judging need the real thing; every other test must not reach a model at all.
REAL_JUDGED = memory.judged

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
	# ponytail: the agent config too. install.session_notes()/corpus_remembers() read claude_dir(), so a
	# UI test that does not set this reads the DEVELOPER's real ~/.claude — the Knowledge row then says
	# something different on their laptop than in CI, and a header test passes or fails on whose machine
	# it ran. Same class as the ~/.prs_teams pin above.
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
	# ponytail: and the cache keyed off it, exactly as log._CACHE is pinned. A module-global that
	# survives a test carries one test's tmp_path answer into the next one's assertions.
	monkeypatch.setattr(install, "_NOTES", (None, []))
	# ponytail: and the review lens. config.INSTRUCTIONS is read from $PRS_INSTRUCTIONS at import, and
	# review() appends that file to every prompt — so a developer who actually uses --instructions ran a
	# suite that asserted on prompt CONTENT and LENGTH with their own text folded in. Two review tests
	# failed on this machine and passed in CI, which reads as "main is broken" rather than "your
	# environment leaked in". Same class as CLAUDE_CONFIG_DIR above; the env var is cleared too, because
	# anything re-reading config at import time would pick it back up.
	monkeypatch.delenv("PRS_INSTRUCTIONS", raising=False)
	monkeypatch.setattr(config, "INSTRUCTIONS", "")
	monkeypatch.setenv("USER", "tester")  # ponytail: memory.whoami() reads $USER; a test must not depend on it
	monkeypatch.setattr(update, "update_available", lambda: "")
	# ponytail: no test consults a model. judged() shells out to the claude CLI for a bare model name,
	# so a scan test would hang on a subprocess or pass only on a machine where claude is installed.
	# ponytail: NONE, not identity. None means "could not be asked", and the two callers take opposite
	# directions on it — the scan shows every candidate, cross_check promotes nothing. Identity was the
	# unsafe one: a test that forgot to patch this would silently AUTO-PROMOTE every loose candidate and
	# still pass. A default has to fail in the direction that writes nothing.
	monkeypatch.setattr(memory, "judged", lambda pairs, model: None)
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
	# ponytail: `gitdashy` alone — review.shutil IS the shutil module, so a blanket lambda answered for
	# every caller in every module (github.copy's clipboard probe included) and swallowed `path=`.
	orig_which = shutil.which
	monkeypatch.setattr(review.shutil, "which", lambda c, *a, **kw:
	                    os.path.join(review.HERE, "prs.py") if c == "gitdashy" and not (a or kw)
	                    else orig_which(c, *a, **kw))
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


def counts(rows):
	"""(count, fact) for drafts rows, dropping the review ids.

	ponytail: the ids are provenance — which runs observed a thing — and no test here is about them.
	Comparing (count, fact) exactly is the assertion these tests always made; this keeps it that way
	rather than loosening them to a length or a substring because the row grew a third field.
	"""
	return [(n, t) for n, _ids, t in rows]


def gql_nodes(*sections):
	"""{"data": {"s0": {"nodes": [...]}, ...}} — one section per argument."""
	return {"data": {f"s{i}": {"nodes": list(ns)} for i, ns in enumerate(sections)}}
def a_team(monkeypatch, tmp_path, key="org-t"):
	"""A joined team at ~/.prs_teams/<slug>/. Returns its memory dir.

	ponytail: a real directory with a .git in it, because that is what "joined" MEANS now — the
	filesystem is the registry. Faking it by setting one config path stopped working the moment a
	slug had to resolve to one checkout among several, which is the whole point of the change.
	"""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	d = tmp_path / "teams" / key
	(d / ".git").mkdir(parents=True, exist_ok=True)
	(d / "memory").mkdir(parents=True, exist_ok=True)
	# ponytail: a fixture that joins a team is a fixture whose operator said yes to publishing.
	# Granted through the real call, so the consent gate stays in the path every test walks.
	memory.allow_publishing(key)
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
