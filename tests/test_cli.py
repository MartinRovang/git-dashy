import os
import subprocess

import pytest

from dashy import cli, config
from dashy.core import memory, team


def facts(p):
	return [l.strip() for l in open(p).read().splitlines() if l.strip()]


def test_remember_drafts_then_confirms_on_a_second_observation(monkeypatch, tmp_path, capsys):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	cli.run(["gitdashy", "remember", "the", "viewer", "owns", "mask", "state"])
	assert "drafted" in capsys.readouterr().out
	assert memory.drafts("acme/web") == [(1, "the viewer owns mask state")]
	cli.run(["gitdashy", "remember", "The viewer owns mask state."])  # reworded, same fact
	assert "confirmed" in capsys.readouterr().out
	assert facts(memory.path("acme/web")) == ["- the viewer owns mask state"]
	cli.run(["gitdashy", "remember", "the viewer owns mask state"])
	assert "already knows that" in capsys.readouterr().out


def test_remember_general_and_explicit_repo(monkeypatch, tmp_path, capsys):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	cli.run(["gitdashy", "remember", "--general", "PHI reaches the frontend"])
	assert "general" in capsys.readouterr().out
	assert memory.drafts(None) == [(1, "PHI reaches the frontend")]
	cli.run(["gitdashy", "remember", "--repo", "other/thing", "migrations run first"])
	assert memory.drafts("other/thing") == [(1, "migrations run first")]
	assert memory.drafts("acme/web") == []  # the flag won, not the cwd


def test_remember_goes_to_drafts_never_straight_to_memory(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	cli.run(["gitdashy", "remember", "one session said so"])
	assert not os.path.exists(memory.path("acme/web"))  # same gate as a review's claim
	assert memory.read("acme/web") == ""  # and not readable, so it cannot confirm itself


def test_remember_needs_a_fact_and_a_scope(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	with pytest.raises(SystemExit, match="needs a fact"):
		cli.run(["gitdashy", "remember"])
	monkeypatch.setattr(team, "origin_slug", lambda p: "")
	with pytest.raises(SystemExit, match="no git origin"):
		cli.run(["gitdashy", "remember", "a fact with nowhere to go"])


def test_sync_memory_needs_a_destination():
	with pytest.raises(SystemExit, match="needs --into"):
		cli.run(["gitdashy", "sync-memory"])


def test_version_and_help_do_not_start_curses(capsys):
	cli.run(["gitdashy", "--version"])
	assert "gitdashy" in capsys.readouterr().out
	cli.run(["gitdashy", "--help"])
	out = capsys.readouterr().out
	assert "sync-memory" in out and "remember" in out


def test_sync_memory_expands_a_tilde_in_into(monkeypatch, tmp_path):
	home = tmp_path / "home"
	home.mkdir()
	monkeypatch.setenv("HOME", str(home))
	got = []
	monkeypatch.setattr("dashy.core.mirror.sync", lambda into, *a: got.append(into) or "ok")
	monkeypatch.setattr(team, "origin_slug", lambda p: "a/b")
	cli.run(["gitdashy", "sync-memory", "--into", "~/mem"])
	assert got == [str(home / "mem")]  # not a directory literally named ~


def test_self_check_reports_and_exits_nonzero_on_failure(monkeypatch, capsys):
	monkeypatch.setattr("dashy.core.review.self_check",
	                    lambda m: [("flag arrives", True, ""), ("safe-mode hides CLAUDE.md", False, "leaked")])
	with pytest.raises(SystemExit) as e:
		cli.run(["gitdashy", "self-check"])
	assert e.value.code == 1
	out = capsys.readouterr().out
	assert "ok    flag arrives" in out and "FAIL  safe-mode hides CLAUDE.md  (leaked)" in out
