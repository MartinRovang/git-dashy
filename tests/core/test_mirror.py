import os
import subprocess

from dashy import config
from dashy.core import memory, mirror, team


def seed(repo, text, base=None):
	"""A confirmed fact, written the way two agreeing reviews would have left it."""
	p = memory.path(repo, base)
	os.makedirs(os.path.dirname(p), exist_ok=True)
	with open(p, "a") as f:
		f.write(f"- {text}\n")


def test_sync_mirrors_the_repo_only_and_leaves_general_to_the_global_route(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed(None, "run make lint")
	seed("a/b", "uses tabs")
	into = tmp_path / "out"
	report = mirror.sync(str(into), "a/b")
	repo = (into / "repo.md").read_text()
	assert repo.startswith("> **Shared team memory — read-only mirror.**")
	assert "### mine" in repo and "- uses tabs" in repo  # the mirror says whose fact each one is
	assert not (into / "general.md").exists()  # a user-level import loads those, live; twice is waste
	assert "repo.md" in report and "a/b" in report


def test_sync_general_mirrors_the_cross_repo_facts_too(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed(None, "run make lint")
	seed("a/b", "uses tabs")
	into = tmp_path / "out"
	report = mirror.sync(str(into), "a/b", general=True)
	assert "- run make lint" in (into / "general.md").read_text()
	assert "general.md, repo.md" in report
	mirror.sync(str(into), "a/b")  # switching back cleans up rather than leaving a stale copy
	assert not (into / "general.md").exists()


def test_sync_mirrors_what_a_review_sees_from_both_sources(monkeypatch, tmp_path):
	shared = tmp_path / "team" / "memory"
	shared.mkdir(parents=True)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "team"))
	monkeypatch.setattr(team, "on", lambda: True)
	monkeypatch.setattr(team, "NAME", "org/t")
	seed("a/b", "mine about a/b")
	seed("a/b", "team about a/b", str(shared))
	into = tmp_path / "out"
	mirror.sync(str(into), "a/b")
	repo = (into / "repo.md").read_text()
	assert "mine about a/b" in repo and "team about a/b" in repo
	assert "### mine" in repo and "### team org/t" in repo


def test_sync_never_mirrors_a_draft(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	memory.append("a/b", "one review said so")  # a draft, not a fact
	into = tmp_path / "out"
	report = mirror.sync(str(into), "a/b")
	assert not (into / "repo.md").exists() and "nothing" in report


def test_sync_without_a_repo_writes_nothing_unless_general_is_asked_for(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed(None, "run make lint")
	into = tmp_path / "out"
	assert "nothing" in mirror.sync(str(into), "")
	assert not (into / "general.md").exists() and not (into / "repo.md").exists()
	mirror.sync(str(into), "", general=True)
	assert (into / "general.md").exists() and not (into / "repo.md").exists()


def test_sync_removes_a_mirror_whose_source_is_gone(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed("a/b", "uses tabs")
	into = tmp_path / "out"
	mirror.sync(str(into), "a/b")
	assert (into / "repo.md").exists()
	(tmp_path / "mem" / "a__b.md").unlink()
	report = mirror.sync(str(into), "a/b")
	assert not (into / "repo.md").exists() and "nothing" in report


def test_sync_refuses_a_path_git_would_commit(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed(None, "team only")
	repo = tmp_path / "repo"
	repo.mkdir()
	subprocess.run(["git", "init", "-q", str(repo)], check=True)
	into = repo / ".agent" / "team"
	assert "refused" in mirror.sync(str(into), "", general=True)
	assert not (into / "general.md").exists()
	(repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
	(repo / ".git" / "info" / "exclude").write_text(".agent/\n")
	assert "refused" not in mirror.sync(str(into), "", general=True)
	assert (into / "general.md").exists()


def test_origin_slug_reads_the_remote(tmp_path):
	repo = tmp_path / "r"
	repo.mkdir()
	subprocess.run(["git", "init", "-q", str(repo)], check=True)
	assert team.origin_slug(str(repo)) == ""
	subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
	                "git@github.com:acme/web.git"], check=True)
	assert team.origin_slug(str(repo)) == "acme/web"
	assert team.origin_slug(str(tmp_path / "nope")) == ""


def test_sync_no_pull_skips_the_team_fetch(monkeypatch, tmp_path):
	pulls = []
	monkeypatch.setattr(team, "pull", lambda: pulls.append(1))
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed(None, "run make lint")
	mirror.sync(str(tmp_path / "a"), "", pull=False)
	assert pulls == []
	mirror.sync(str(tmp_path / "b"), "")
	assert pulls == [1]


def test_a_refused_mirror_leaves_no_directory_behind(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed("a/b", "uses tabs")
	repo = tmp_path / "repo"
	repo.mkdir()
	subprocess.run(["git", "init", "-q", str(repo)], check=True)
	into = repo / "deep" / "dir"
	assert "refused" in mirror.sync(str(into), "a/b")
	assert not into.exists() and not (repo / "deep").exists()  # it refused, so it built nothing


def test_sync_reports_a_path_it_cannot_create_instead_of_raising(monkeypatch, tmp_path):
	"""A SessionStart hook calls this; a traceback there is a broken hook, not a message."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed("a/b", "uses tabs")
	blocker = tmp_path / "afile"
	blocker.write_text("not a directory\n")
	report = mirror.sync(str(blocker / "under" / "a" / "file"), "a/b")
	assert report.startswith("gitdashy: refused")


def test_sync_survives_git_being_missing(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed("a/b", "uses tabs")
	def no_git(*a, **kw):
		raise FileNotFoundError(2, "No such file or directory: 'git'")
	monkeypatch.setattr(subprocess, "run", no_git)
	assert "refused" in mirror.sync(str(tmp_path / "out"), "a/b")  # fail safe, not fail loud


def test_sync_refuses_when_only_one_of_the_names_is_ignored(monkeypatch, tmp_path):
	"""An ignore rule covering general.md but not repo.md would let the mirror commit the other one."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	seed("a/b", "uses tabs")
	repo = tmp_path / "repo"
	repo.mkdir()
	subprocess.run(["git", "init", "-q", str(repo)], check=True)
	(repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
	(repo / ".git" / "info" / "exclude").write_text("mirror/general.md\n")  # only one of the two
	assert "refused" in mirror.sync(str(repo / "mirror"), "a/b")
	(repo / ".git" / "info" / "exclude").write_text("mirror/\n")  # now both
	assert "refused" not in mirror.sync(str(repo / "mirror"), "a/b")
