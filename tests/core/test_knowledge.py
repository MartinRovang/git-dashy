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
	# ponytail: with an ORIGIN. Local-only history is no longer "already a checkout" — every memory dir
	# has it now, because that is what makes a bad dream recoverable.
	subprocess.run(["git", "-C", str(mine), "remote", "add", "origin", "git@github.com:org/other.git"], check=True)
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mine))
	assert "already a checkout of" in knowledge.adopt("git@github.com:org/mem.git")
	link = tmp_path / "link"
	os.symlink(tmp_path / "elsewhere", link)
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(link))
	assert "point it back" in knowledge.adopt("git@github.com:org/mem.git")


def test_adopt_defers_to_the_env_var(monkeypatch, tmp_path):
	monkeypatch.setenv("PRS_MEMORY", str(tmp_path / "env"))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mine"))
	assert "PRS_MEMORY" in knowledge.adopt("git@github.com:org/mem.git")


def test_leave_refuses_a_dirty_tree_even_with_nothing_unpushed(monkeypatch, tmp_path):
	"""A push that failed earlier leaves work staged but uncommitted: zero commits ahead, still someone's."""
	store = tmp_path / "team"
	store.mkdir()
	subprocess.run(["git", "init", "-q", str(store)], check=True)
	(store / "reviewed.jsonl").write_text('{"x":1}\n')
	monkeypatch.setattr(team, "on", lambda: True)
	monkeypatch.setattr(config, "TEAM", str(store))
	assert knowledge.unpushed() == -1  # no upstream AND dirty
	assert "unpushed" in knowledge.leave()
	assert store.exists() and (store / "reviewed.jsonl").exists()


def test_adopt_refuses_to_make_your_memory_the_team_repo(monkeypatch, tmp_path):
	"""Your memory dir is pushed and holds drafts/. Pointing it at the team repo would publish them."""
	store = tmp_path / "team"
	store.mkdir()
	subprocess.run(["git", "init", "-q", str(store)], check=True)
	subprocess.run(["git", "-C", str(store), "remote", "add", "origin",
	                "git@github.com:org/review-team.git"], check=True)
	monkeypatch.setattr(team, "on", lambda: True)
	monkeypatch.setattr(config, "TEAM", str(store))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mine"))
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	for url in ("git@github.com:org/review-team.git", "https://github.com/org/review-team",
	            "org/review-team"):
		assert "that is the team repo" in knowledge.adopt(url), url
	assert not (tmp_path / "mine").exists()


def _memory_repo(tmp_path, *names):
	"""A pushable repo holding `names`, to adopt from."""
	import subprocess
	remote, work = tmp_path / "remote.git", tmp_path / "work"
	work.mkdir()
	for n in names:
		(work / n).write_text("- theirs\n")
	env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
	       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
	subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
	for c in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "x"],
	          ["git", "remote", "add", "origin", str(remote)],
	          ["git", "push", "-q", "origin", "HEAD:refs/heads/main"]):
		subprocess.run(c, cwd=work, env=env, check=True)
	# ponytail: point the bare repo's HEAD at the branch we actually pushed. A runner whose
	# init.defaultBranch is "master" leaves HEAD dangling, and `git clone` of that succeeds with an
	# EMPTY working tree — so every assertion here passed locally and failed on CI for a reason that
	# had nothing to do with the code under test.
	subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
	return str(remote)


def test_adopt_refusing_a_collision_keeps_every_file_it_had_not_reached(monkeypatch, tmp_path):
	"""The refusal used to rmtree a tmp that already held files MOVED out of the memory dir.

	Sorted order made it the likely path: a memory repo has general.md, and it sorts after
	acme__api.md and drafts/ — so the two that had already moved were the ones destroyed, under
	a message saying "merge it by hand".
	"""
	mem = tmp_path / "prs_memory"
	(mem / "drafts").mkdir(parents=True)
	(mem / "acme__api.md").write_text("- the api repo owns no DDL\n")
	(mem / "drafts" / "acme__api.md").write_text("- (1) unconfirmed\n")
	(mem / "general.md").write_text("- two years of facts\n")
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mem))
	monkeypatch.delenv("PRS_MEMORY", raising=False)

	err = knowledge.adopt(_memory_repo(tmp_path, "general.md"))

	assert "general.md exists in both" in err
	assert (mem / "acme__api.md").read_text() == "- the api repo owns no DDL\n"
	assert (mem / "drafts" / "acme__api.md").exists()
	assert (mem / "general.md").read_text() == "- two years of facts\n"


def test_adopt_names_every_collision_not_just_the_first(monkeypatch, tmp_path):
	"""Refusing one at a time means fixing them one round trip at a time."""
	mem = tmp_path / "prs_memory"
	mem.mkdir()
	(mem / "general.md").write_text("- mine\n")
	(mem / "acme__api.md").write_text("- mine\n")
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mem))
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	err = knowledge.adopt(_memory_repo(tmp_path, "general.md", "acme__api.md"))
	assert "acme__api.md" in err and "general.md" in err


def test_adopt_that_succeeds_still_keeps_everything(monkeypatch, tmp_path):
	"""The happy path, so the guard above cannot pass by refusing everything."""
	mem = tmp_path / "prs_memory"
	(mem / "drafts").mkdir(parents=True)
	(mem / "acme__api.md").write_text("- mine\n")
	(mem / "drafts" / "x.md").write_text("- (1) draft\n")
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mem))
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	assert knowledge.adopt(_memory_repo(tmp_path, "general.md")) == ""
	assert (mem / "acme__api.md").read_text() == "- mine\n"   # kept
	assert (mem / "drafts" / "x.md").exists()                  # drafts came across too
	assert (mem / "general.md").read_text() == "- theirs\n"    # and theirs arrived
	assert team.is_repo(str(mem))                              # it is a checkout now


def test_adopting_over_local_history_keeps_that_history_beside_it(monkeypatch, tmp_path):
	"""Every memory dir has local history now, so adopt must work through it — and not throw it away."""
	import subprocess as sp
	mem = tmp_path / "prs_memory"
	mem.mkdir()
	(mem / "acme__api.md").write_text("- mine\n")
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mem))
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.delenv("PRS_MEMORY", raising=False)
	assert team.init_history(str(mem)) and not team.has_remote(str(mem))

	assert knowledge.adopt(_memory_repo(tmp_path, "general.md")) == ""

	assert (mem / "acme__api.md").read_text() == "- mine\n"      # facts came across
	assert (mem / "general.md").read_text() == "- theirs\n"      # and theirs arrived
	assert team.has_remote(str(mem))                              # it is a real checkout now
	kept = [d for d in os.listdir(tmp_path) if ".local-history-" in d]
	assert len(kept) == 1                                         # the old history is beside it
	r = sp.run(["git", "--git-dir", str(tmp_path / kept[0]), "log", "--oneline"], capture_output=True, text=True)
	assert r.returncode == 0 and r.stdout.strip()                 # and it still reads


def test_a_memory_dir_gets_history_on_the_first_write(monkeypatch, tmp_path):
	"""The net under every rewrite: `git checkout HEAD~1 -- general.md` has to be a thing you can do."""
	from dashy.core import memory
	mem = tmp_path / "prs_memory"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", "")
	(mem / "general.md").write_text("- two years of facts\n")
	memory._append_line(str(mem / "general.md"), "one more")
	assert team.is_repo(str(mem)) and not team.has_remote(str(mem))
	team.push_dir(str(mem), "memory: test", "mine")   # commits; no remote, and that is not an error
	assert team.ERROR == ""

	memory.write({"mine/general.md": ""})             # a dream that deletes the file
	assert not (mem / "general.md").exists()
	import subprocess as sp
	got = sp.run(["git", "-C", str(mem), "show", "HEAD:general.md"], capture_output=True, text=True)
	assert "two years of facts" in got.stdout        # recoverable


def test_backups_are_compressed_deduplicated_and_capped(monkeypatch, tmp_path):
	"""Markdown is tiny; losing one file once is not."""
	from dashy.core import memory
	mem, backups = tmp_path / "prs_memory", tmp_path / "backups"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.setattr(memory, "BACKUPS", str(backups))
	monkeypatch.setattr(memory, "KEEP_BACKUPS", 3)
	(mem / "general.md").write_text("- a fact\n")

	first = memory.backup("test")
	assert first.endswith(".tar.gz") and os.path.getsize(first) > 0
	assert memory.backup("test") == ""               # unchanged content is not stored twice
	import tarfile
	with tarfile.open(first) as t:
		assert t.getnames() == ["mine/general.md"]

	for i in range(5):
		(mem / "general.md").write_text(f"- fact {i}\n")
		memory.backup("test")
	assert len(os.listdir(backups)) == 3             # capped, oldest pruned
	assert not any(n.endswith(".part") for n in os.listdir(backups))


def test_backup_never_raises_and_never_blocks(monkeypatch, tmp_path):
	"""It runs on the refresh tick and before a dream; failing must not stop either."""
	from dashy.core import memory
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "gone"))
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.setattr(memory, "BACKUPS", "/proc/nope/backups")  # unwritable
	assert memory.backup("test") == ""


def test_a_relative_path_is_a_path_not_an_owner_name(tmp_path):
	"""The docstring had said the dot made it a path. Nothing implemented that."""
	assert not knowledge.is_remote("./notes")
	assert not knowledge.is_remote("../shared/memory")
	assert not knowledge.is_remote("~/somewhere")
	assert not knowledge.is_remote(str(tmp_path))          # an existing dir always wins
	assert knowledge.is_remote("owner/name")               # still owner/name, as T reads it
	assert knowledge.is_remote("git@github.com:o/r.git")
	assert knowledge.is_remote("https://github.com/o/r.git")


def _configured(d, name="Real Person", mail="real@example.com"):
	import subprocess as sp
	sp.run(["git", "init", "-q", str(d)], check=True)
	sp.run(["git", "-C", str(d), "config", "user.name", name], check=True)
	sp.run(["git", "-C", str(d), "config", "user.email", mail], check=True)


def test_a_commit_keeps_the_authors_identity(tmp_path):
	"""-c has the highest precedence in git, so passing it always REPLACED the author, never fell back.

	push_dir is how the shared team repo commits. Every shared fact and every reviewed.jsonl append was
	landing as "gitdashy" for every member — pushed, and not rewritable afterwards.
	"""
	import subprocess as sp
	d = tmp_path / "repo"
	d.mkdir()
	_configured(d)
	(d / "general.md").write_text("- a fact\n")
	team.push_dir(str(d), "memory: test", "mine")
	got = sp.run(["git", "-C", str(d), "log", "-1", "--format=%an <%ae>"], capture_output=True, text=True)
	assert got.stdout.strip() == "Real Person <real@example.com>"


def test_a_machine_with_no_identity_can_still_commit(tmp_path, monkeypatch):
	"""The fallback is the point — it is exactly the machine with no other backup."""
	import subprocess as sp
	monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
	monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
	for v in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
	          "EMAIL", "GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
		monkeypatch.delenv(v, raising=False)
	d = tmp_path / "mem"
	d.mkdir()
	(d / "general.md").write_text("- a fact\n")
	assert team.init_history(str(d))
	got = sp.run(["git", "-C", str(d), "log", "-1", "--format=%an"], capture_output=True, text=True)
	assert got.returncode == 0 and got.stdout.strip() == "gitdashy"


def test_history_is_not_started_inside_someone_elses_repo(tmp_path):
	"""PRS_MEMORY pointing into a notes repo must not get an embedded repo nobody asked for."""
	import subprocess as sp
	outer = tmp_path / "notes"
	(outer / "memory").mkdir(parents=True)
	sp.run(["git", "init", "-q", str(outer)], check=True)
	assert not team.init_history(str(outer / "memory"))
	assert not (outer / "memory" / ".git").exists()
	assert team.inside_other_repo(str(outer / "memory"))


def test_every_rewrite_of_memory_has_history_behind_it(monkeypatch, tmp_path):
	"""forget and the pool rewrite went through no helper that started history, and the docs said they did."""
	from dashy.core import memory
	import subprocess as sp
	mem = tmp_path / "prs_memory"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", "")
	(mem / "general.md").write_text("- keep me\n- drop me\n")

	memory.forget(None, "drop me")

	assert team.is_repo(str(mem))
	assert (mem / "general.md").read_text() == "- keep me\n"
	got = sp.run(["git", "-C", str(mem), "show", "HEAD:general.md"], capture_output=True, text=True)
	assert "drop me" in got.stdout  # the state before the forget is recoverable
