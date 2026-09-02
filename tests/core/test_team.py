import os
import subprocess

from dashy import config
from dashy.core import log, memory, team


def git(*a, cwd):
	return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_setup_seeds_and_pushes_then_pull_sees_teammate(monkeypatch, tmp_path):
	remote = tmp_path / "remote.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "me"))
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	memory.append("a/b", "tabs")
	open(log.LOG, "w").write('{"x":1}\n')
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	assert team.setup(str(remote)) == ""
	assert team.on() and log.LOG == str(tmp_path / "me" / "reviewed.jsonl")
	assert open(memory.path("a/b")).read() == "- tabs\n"

	# a teammate appends to the same files; our pull picks it up, our push merges without conflict
	git("clone", "-q", str(remote), str(tmp_path / "mate"), cwd=tmp_path)
	assert "merge=union" in open(tmp_path / "mate" / ".gitattributes").read()
	open(tmp_path / "mate" / "reviewed.jsonl", "a").write('{"x":2}\n')
	git("commit", "-qam", "mate", cwd=tmp_path / "mate")
	git("push", "-q", cwd=tmp_path / "mate")
	open(log.LOG, "a").write('{"x":3}\n')
	team.push("mine")
	team.pull()
	assert team.ERROR == "" and open(log.LOG).read() == '{"x":1}\n{"x":2}\n{"x":3}\n'


def test_off_is_a_noop(tmp_path, monkeypatch):
	monkeypatch.setattr(team, "ERROR", "")
	team.pull(); team.push("x")
	assert not team.on() and team.ERROR == ""
