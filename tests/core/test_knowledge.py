import os
import subprocess

from dashy import config
from dashy.core import knowledge, team


def test_repoint_moves_what_is_there_and_leaves_a_symlink(monkeypatch, tmp_path):
	old, new = tmp_path / "old", tmp_path / "new"
	old.mkdir()
	(old / "general.md").write_text("- a fact\n")
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(old))
	monkeypatch.setattr(config, "MEMORY_DIR", str(old))
	assert knowledge.set_local(str(new)) == ""
	assert os.path.islink(old) and os.path.realpath(old) == str(new)
	assert (new / "general.md").read_text() == "- a fact\n"  # nothing was lost in the move
	assert open(os.path.join(str(old), "general.md")).read() == "- a fact\n"  # still reachable by the old path


def test_repoint_refuses_when_the_env_var_owns_it(monkeypatch, tmp_path):
	monkeypatch.setenv("PRS_MEMORY", str(tmp_path / "env"))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "old"))
	err = knowledge.set_local(str(tmp_path / "new"))
	assert "PRS_MEMORY" in err and not (tmp_path / "new").exists()


def test_repoint_refuses_rather_than_picking_a_winner(monkeypatch, tmp_path):
	old, new = tmp_path / "old", tmp_path / "new"
	old.mkdir(); new.mkdir()
	(old / "general.md").write_text("mine\n")
	(new / "general.md").write_text("theirs\n")
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(old))
	err = knowledge.set_local(str(new))
	assert "merge them by hand" in err
	assert (new / "general.md").read_text() == "theirs\n" and (old / "general.md").read_text() == "mine\n"


def test_set_store_refuses_while_the_refresh_thread_is_in_there(monkeypatch, tmp_path):
	monkeypatch.setattr(team, "on", lambda: True)
	assert "leave the team first" in knowledge.set_store(str(tmp_path / "elsewhere"))


def test_leave_refuses_to_delete_unpushed_reviews(monkeypatch, tmp_path):
	monkeypatch.setattr(team, "on", lambda: True)
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "team"))
	(tmp_path / "team").mkdir()
	monkeypatch.setattr(knowledge, "unpushed", lambda: 2)
	assert "2 unpushed reviews" in knowledge.leave()
	assert (tmp_path / "team").exists()
	monkeypatch.setattr(knowledge, "unpushed", lambda: -1)  # no upstream: also refuse, the log may exist only here
	assert "possibly unpushed" in knowledge.leave()
	assert (tmp_path / "team").exists()


def test_leave_goes_back_to_the_solo_locations(monkeypatch, tmp_path):
	from dashy.core import log
	store = tmp_path / "team"
	store.mkdir()
	monkeypatch.setattr(team, "on", lambda: True)
	monkeypatch.setattr(team, "NAME", "org/t")
	monkeypatch.setattr(config, "TEAM", str(store))
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mine"))
	monkeypatch.setattr(config, "LOCAL_LOG", str(tmp_path / "solo.jsonl"))
	monkeypatch.setattr(knowledge, "unpushed", lambda: 0)
	assert knowledge.leave() == ""
	assert not store.exists()
	assert log.LOG == str(tmp_path / "solo.jsonl")  # the log lived in the checkout, so it comes back
	assert config.MEMORY_DIR == str(tmp_path / "mine")  # memory never moved there, so nothing to move back
	assert team.NAME == ""


def test_store_moved_only_off_the_default(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "TEAM", knowledge.DEFAULT_STORE)
	assert not knowledge.store_moved()
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "elsewhere"))
	assert knowledge.store_moved()


def test_show_names_the_target_of_a_symlink(tmp_path):
	real, link = tmp_path / "real", tmp_path / "link"
	real.mkdir()
	os.symlink(real, link)
	assert knowledge.show(str(link)).endswith(f"link → {real}")
	assert "→" not in knowledge.show(str(real))


def test_inside_git_sees_an_unignored_path_under_a_repo(tmp_path):
	repo = tmp_path / "repo"
	repo.mkdir()
	subprocess.run(["git", "init", "-q", str(repo)], check=True)
	assert knowledge.inside_git(str(repo / "notes" / "mem"))  # nearest existing ancestor is the repo itself
	(repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
	(repo / ".git" / "info" / "exclude").write_text("notes/\n")
	assert not knowledge.inside_git(str(repo / "notes" / "mem"))
	assert not knowledge.inside_git(str(tmp_path / "plain"))  # outside any repo


def test_is_remote_tells_a_repo_from_a_directory(tmp_path):
	real = tmp_path / "here"
	real.mkdir()
	assert knowledge.is_remote("git@github.com:NilsPontus/Np_Claude_Agentic.git")
	assert knowledge.is_remote("https://github.com/org/mem.git")
	assert knowledge.is_remote("ssh://git@host/org/mem")
	assert knowledge.is_remote("org/mem")  # owner/name, as T accepts
	assert not knowledge.is_remote(str(real))  # an existing directory always wins
	assert not knowledge.is_remote("./org/mem")  # the dot makes it a path
	assert not knowledge.is_remote("/abs/path/mem")
	assert not knowledge.is_remote("~/work/mem")
	assert not knowledge.is_remote("")


def test_abspath_survives_a_deleted_working_directory(monkeypatch, tmp_path):
	def boom():
		raise FileNotFoundError(2, "No such file or directory")
	monkeypatch.setattr(os, "getcwd", boom)
	assert knowledge.abspath("some/relative/thing") == "some/relative/thing"  # no crash
	assert knowledge.inside_git("some/relative/thing") is False


def test_adopt_clones_and_keeps_the_facts_already_there(monkeypatch, tmp_path):
	remote, mine = tmp_path / "remote.git", tmp_path / "mine"
	subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
	work = tmp_path / "seed"
	subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
	(work / "general.md").write_text("- from the repo\n")
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
	             ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
	subprocess.run(["git", "-C", str(work), "commit", "-qm", "seed"], check=True)
	subprocess.run(["git", "-C", str(work), "push", "-q"], check=True)

	mine.mkdir()
	(mine / "a__b.md").write_text("- only on this machine\n")
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mine))
	assert knowledge.adopt(str(remote)) == ""
	assert (mine / ".git").is_dir()  # your memory dir IS the checkout now
	assert (mine / "general.md").read_text() == "- from the repo\n"
	assert (mine / "a__b.md").read_text() == "- only on this machine\n"  # nothing local was lost
	assert "merge=union" in (mine / ".gitattributes").read_text()
	assert not (tmp_path / "mine.incoming").exists()


def test_adopt_refuses_rather_than_overwriting_a_clash(monkeypatch, tmp_path):
	remote, mine = tmp_path / "remote.git", tmp_path / "mine"
	subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
	work = tmp_path / "seed"
	subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
	(work / "general.md").write_text("- theirs\n")
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
	             ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
	subprocess.run(["git", "-C", str(work), "commit", "-qm", "seed"], check=True)
	subprocess.run(["git", "-C", str(work), "push", "-q"], check=True)

	mine.mkdir()
	(mine / "general.md").write_text("- mine\n")
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mine))
	assert "merge it by hand" in knowledge.adopt(str(remote))
	assert (mine / "general.md").read_text() == "- mine\n"  # untouched
	assert not (mine / ".git").exists() and not (tmp_path / "mine.incoming").exists()


def test_adopt_refuses_an_existing_checkout_or_a_symlink(monkeypatch, tmp_path):
	mine = tmp_path / "mine"
	mine.mkdir()
	subprocess.run(["git", "init", "-q", str(mine)], check=True)
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mine))
	assert "already a git checkout" in knowledge.adopt("git@github.com:org/mem.git")
	link = tmp_path / "link"
	os.symlink(tmp_path / "elsewhere", link)
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(link))
	assert "point it back" in knowledge.adopt("git@github.com:org/mem.git")


def test_adopt_defers_to_the_env_var(monkeypatch, tmp_path):
	monkeypatch.setenv("PRS_MEMORY", str(tmp_path / "env"))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mine"))
	assert "PRS_MEMORY" in knowledge.adopt("git@github.com:org/mem.git")
