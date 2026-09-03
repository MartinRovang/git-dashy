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
	mem = tmp_path / "mem"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	(mem / "a__b.md").write_text("- tabs\n")
	open(log.LOG, "w").write('{"x":1}\n')
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	assert team.setup(str(remote)) == ""
	assert team.on() and log.LOG == str(tmp_path / "me" / "reviewed.jsonl")  # the log is shared history
	assert config.MEMORY_DIR == str(mem) and open(memory.path("a/b")).read() == "- tabs\n"  # memory stays yours
	# joining must not publish every private fact you have ever collected, unreviewed, in one action
	assert not os.path.exists(memory.path("a/b", str(tmp_path / "me" / "memory")))

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


def test_every_git_call_is_bounded_and_never_prompts(monkeypatch, tmp_path):
	"""pull/push run on every refresh tick from a daemon thread — a credential prompt there hangs the TUI."""
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "t"))
	(tmp_path / "t" / ".git").mkdir(parents=True)
	seen = []
	def fake_run(cmd, **kw):
		seen.append((cmd[:2], kw.get("env", {}).get("GIT_TERMINAL_PROMPT"), kw.get("timeout")))
		raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
	monkeypatch.setattr(subprocess, "run", fake_run)
	monkeypatch.setattr(team, "ERROR", "")
	team.pull()  # must not raise out of the refresh thread
	assert seen and seen[0][1] == "0" and seen[0][2] == 120
	assert team.ERROR.startswith("sync: timed out")


def test_same_remote_does_not_ignore_the_host():
	assert team.same_remote("git@github.com:org/mem.git", "https://github.com/org/mem")
	assert team.same_remote("ssh://git@github.com/org/mem", "git@github.com:org/mem.git")
	assert not team.same_remote("git@gitlab.com:org/mem.git", "https://github.com/org/mem")
	assert team.same_remote("org/mem", "https://github.com/org/mem")  # a bare name names no host
	assert not team.same_remote("other/mem", "https://github.com/org/mem")
	assert not team.same_remote("", "https://github.com/org/mem")


def test_joining_a_team_refuses_your_own_memory_from_either_side(monkeypatch, tmp_path):
	"""adopt() guards one direction; this is the other. Whichever you set up second must refuse."""
	mem = tmp_path / "mem"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "team"))
	assert team.is_own_memory(str(mem))  # the same directory, by path
	assert "your own memory directory" in team.setup(str(mem))
	subprocess.run(["git", "init", "-q", str(mem)], check=True)
	subprocess.run(["git", "-C", str(mem), "remote", "add", "origin",
	                "git@github.com:org/mine.git"], check=True)
	assert team.is_own_memory("https://github.com/org/mine")  # or by the remote it pushes to
	assert not team.is_own_memory("https://gitlab.com/org/mine")  # a different host is a different repo
	assert not (tmp_path / "team").exists()

def test_a_new_team_repo_gets_a_brief_to_fill_in(monkeypatch, tmp_path):
	p = tmp_path / "project.md"
	team.seed_project(str(p))
	assert "What we are building" in p.read_text() and "Constraints that change decisions" in p.read_text()
	p.write_text("ours, written\n")
	team.seed_project(str(p))
	assert p.read_text() == "ours, written\n"  # never overwritten
