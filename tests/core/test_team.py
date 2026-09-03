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


def test_setup_accepts_a_url_and_never_waits_on_a_prompt(monkeypatch, tmp_path):
	"""A https/ssh URL clones with git, not gh — and a remote that asks for a password must fail, not hang."""
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "me"))
	seen = {}
	def fake_run(cmd, **kw):
		seen["cmd"], seen["env"], seen["timeout"] = cmd, kw.get("env", {}), kw.get("timeout")
		raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
	monkeypatch.setattr(subprocess, "run", fake_run)
	err = team.setup("https://github.com/org/review-team.git")
	assert seen["cmd"][:2] == ["git", "clone"]  # a URL is not an owner/name, so gh is not involved
	assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and "BatchMode=yes" in seen["env"]["GIT_SSH_COMMAND"]
	assert seen["timeout"] == team.CLONE and err.startswith("sync: timed out after")  # reason first, not a clipped URL
	assert not team.on()


def test_setup_keeps_a_users_own_ssh_command(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "me"))
	monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /keys/mine")
	seen = {}
	def fake_run(cmd, **kw):
		seen["env"] = kw["env"]
		return subprocess.CompletedProcess(cmd, 1, "", "nope")
	monkeypatch.setattr(subprocess, "run", fake_run)
	team.setup("git@github.com:org/review-team.git")
	assert seen["env"]["GIT_SSH_COMMAND"] == "ssh -i /keys/mine"
