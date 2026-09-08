import json
import os
import pathlib
import shlex
import shutil
import subprocess

from dashy import config
from dashy.core import install, mirror, state, team


def fresh(monkeypatch, tmp_path):
	"""A machine with an agent config dir and nothing wired."""
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "team"))
	monkeypatch.setattr(install, "REGISTRY", str(tmp_path / "mirrors"))  # set at import, so patch it directly
	return cfg


def test_install_links_and_imports_then_is_a_no_op(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	assert all("would" in l for l in install.apply(dry=True))
	assert not (cfg / "prs-memory").exists()  # a dry run writes nothing
	out = install.apply()
	assert os.path.realpath(cfg / "prs-memory") == str(tmp_path / "mem")
	assert "@prs-memory/general.md" in (cfg / "CLAUDE.md").read_text()
	assert "@prs-team/" not in (cfg / "CLAUDE.md").read_text()   # a team's facts are per repo, not global
	assert not (cfg / "prs-team").exists()
	assert all(l.startswith(("link", "add")) for l in out)
	again = install.apply()
	assert all(l.startswith("ok") for l in again)  # nothing done twice
	assert (cfg / "CLAUDE.md").read_text().count("@prs-memory/general.md") == 1


def test_install_never_replaces_something_it_did_not_make(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	(cfg / "prs-memory").mkdir()  # a real directory where our link would go
	out = install.apply()
	assert any("SKIP" in l and "prs-memory" in l for l in out)
	assert (cfg / "prs-memory").is_dir() and not (cfg / "prs-memory").is_symlink()


def test_install_says_so_when_there_is_no_agent_config(monkeypatch, tmp_path):
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
	out = install.apply()
	assert len(out) == 1 and out[0].startswith("FAIL")


def test_uninstall_removes_its_own_block_and_leaves_a_hand_written_one(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	(cfg / "CLAUDE.md").write_text("# mine\n\nkeep this\n")
	install.apply()
	out = install.remove()
	assert not (cfg / "prs-memory").exists() and not (cfg / "prs-team").exists()
	text = (cfg / "CLAUDE.md").read_text()
	assert "keep this" in text and "@prs-memory" not in text and install.BEGIN not in text
	assert all(not l.startswith("SKIP") for l in out)
	# a block someone wrote by hand is not ours to remove
	(cfg / "CLAUDE.md").write_text("# mine\n\n@prs-memory/general.md\n")
	out = install.remove()
	assert any("not in a block we wrote" in l for l in out)
	assert "@prs-memory/general.md" in (cfg / "CLAUDE.md").read_text()


def test_registry_round_trip(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	assert install.registered() == []
	assert install.register(str(tmp_path / "a"), "o/n") is True
	assert install.register(str(tmp_path / "a"), "o/n") is False  # already known
	assert [(i, r) for i, r, *_ in install.registered()] == [(str(tmp_path / "a"), "o/n")]
	assert install.unregister(str(tmp_path / "a")) is True
	assert install.unregister(str(tmp_path / "a")) is False
	assert install.registered() == []


def test_wire_repo_excludes_imports_registers_and_mirrors(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	os.makedirs(tmp_path / "mem")
	(tmp_path / "mem" / "o__n.md").write_text("- a fact about o/n\n")
	repo = tmp_path / "proj"
	repo.mkdir()
	subprocess.run(["git", "init", "-q", str(repo)], check=True)
	loader = repo / "NOTES.md"
	loader.write_text("# notes\n")
	install.wire_repo(str(repo / ".agent" / "team"), str(loader), "o/n")
	assert ".agent/team/" in (repo / ".git" / "info" / "exclude").read_text()
	assert "@.agent/team/repo.md" in loader.read_text()
	entry = install.registered()[0]
	assert entry[:2] == (str(repo / ".agent" / "team"), "o/n")
	assert entry[2] == str(repo) and entry[3] == str(loader)  # root and loader, so uninstall can undo
	assert "a fact about o/n" in (repo / ".agent" / "team" / "repo.md").read_text()
	assert not subprocess.run(["git", "-C", str(repo), "status", "--porcelain", ".agent"],
	                          capture_output=True, text=True).stdout.strip()  # git cannot see it
	again = install.wire_repo(str(repo / ".agent" / "team"), str(loader), "o/n")
	assert sum(l.startswith("ok") for l in again) == 3  # exclude, import and registry all already done
	assert loader.read_text().count("@.agent/team/repo.md") == 1


def test_refresh_mirrors_resyncs_and_survives_a_bad_entry(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	calls = []
	monkeypatch.setattr(mirror, "sync", lambda into, repo, pull=True: calls.append((into, repo, pull)))
	(tmp_path / "a").mkdir()   # a mirror that is still there
	(tmp_path / "b").mkdir()
	install.register(str(tmp_path / "a"), "o/n")
	install.register(str(tmp_path / "b"), "o/m")
	state.refresh_mirrors()
	assert calls == [(str(tmp_path / "a"), "o/n", False), (str(tmp_path / "b"), "o/m", False)]
	def boom(into, repo, pull=True):
		raise OSError("gone")
	monkeypatch.setattr(mirror, "sync", boom)
	state.refresh_mirrors()  # a stale entry must not stop the refresh loop


def test_the_refresh_loop_is_still_a_method_on_state():
	"""Regression: refresh_mirrors was once defined inside the class body, which swallowed loop()."""
	assert callable(getattr(state.State, "loop", None))
	assert callable(getattr(state, "refresh_mirrors", None))


def test_explain_names_real_paths_and_the_state_of_each(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	out = "\n".join(install.explain())
	assert "[new]" in out and str(cfg / "prs-memory") in out
	assert "will NOT" in out and "hooks" in out and "settings.json" in out
	assert "--uninstall" in out  # you are told how to reverse it before you agree to it
	install.apply()
	out = "\n".join(install.explain())
	assert "already correct" in out and "already there" in out and "[new]" not in out
	(cfg / "prs-memory").unlink()
	(cfg / "prs-memory").mkdir()
	assert "EXISTS, will be left alone" in "\n".join(install.explain())


def full_env(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(install, "CORPUS_HOME", str(tmp_path / "corpus-home"))
	return cfg, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "corpus")


def test_full_install_is_surgical_about_settings_json(monkeypatch, tmp_path):
	cfg, corpus = full_env(monkeypatch, tmp_path)
	(cfg / "settings.json").write_text(
		'{"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}')
	install.full_apply(corpus)
	got = __import__("json").loads((cfg / "settings.json").read_text())
	assert got["model"] == "opus"  # someone else's settings are not ours to rewrite
	assert got["hooks"]["Stop"][0]["hooks"][0]["command"] == "mine"
	assert len(got["hooks"]["SessionStart"]) == 1
	install.full_apply(corpus)  # again
	got = __import__("json").loads((cfg / "settings.json").read_text())
	assert len(got["hooks"]["SessionStart"]) == 1  # never doubled
	install.full_remove()
	got = __import__("json").loads((cfg / "settings.json").read_text())
	assert got["hooks"]["Stop"][0]["hooks"][0]["command"] == "mine"  # still theirs
	assert "SessionStart" not in got["hooks"]


def test_full_install_seeds_user_md_and_never_ships_a_real_one(monkeypatch, tmp_path):
	cfg, corpus = full_env(monkeypatch, tmp_path)
	assert not os.path.exists(os.path.join(corpus, "identity", "USER.md")), \
		"a filled-in USER.md must never be committed to the repo"
	install.full_apply(corpus)
	seeded = os.path.join(str(tmp_path / "corpus-home"), "identity", "USER.md")
	seeded_text = open(seeded).read()
	assert os.path.exists(seeded) and "Who you are" in seeded_text and "**Name:**" in seeded_text
	assert "project.md" in seeded_text  # and it says where the SHARED context lives instead
	open(seeded, "w").write("# me\n")
	install.full_apply(corpus)
	assert open(seeded).read() == "# me\n"  # what you wrote is never overwritten


def test_full_uninstall_leaves_the_corpus_and_removes_only_its_own_block(monkeypatch, tmp_path):
	cfg, corpus = full_env(monkeypatch, tmp_path)
	(cfg / "CLAUDE.md").write_text("# my own rules\n\nkeep me\n")
	install.full_apply(corpus)
	text = (cfg / "CLAUDE.md").read_text()
	assert install.CBEGIN in text and install.BEGIN in text  # two independent blocks
	install.full_remove()
	text = (cfg / "CLAUDE.md").read_text()
	assert "keep me" in text and install.CBEGIN not in text and install.BEGIN not in text
	assert os.path.isdir(tmp_path / "corpus-home")  # you may have edited it, so it stays


def test_full_install_refuses_a_broken_settings_file(monkeypatch, tmp_path):
	cfg, corpus = full_env(monkeypatch, tmp_path)
	(cfg / "settings.json").write_text("{not json")
	out = install.full_apply(corpus)
	assert any(l.startswith("FAIL") and "valid JSON" in l for l in out)


def test_the_shipped_corpus_is_generic(monkeypatch, tmp_path):
	"""It goes to strangers, so nothing personal or project-specific may be in it."""
	_, corpus = full_env(monkeypatch, tmp_path)
	blob = ""
	for root, _, fs in os.walk(corpus):
		for f in fs:
			blob += open(os.path.join(root, f)).read().lower()
	import re
	# ponytail: word boundaries. "phi" is inside "philosophy" and "ous" inside "obvious" — substring
	# matching here would fail on innocent prose and pass on a real leak that happened to be hyphenated.
	for word in ("neomedsys", "nils", "pontus", "phi", "medquery", "neo-api", "nms-platform",
	             "neoservo", "neocoms", "ous", "ce-marked"):
		assert not re.search(rf"\b{re.escape(word)}\b", blob), f"the shipped corpus mentions {word!r}"
	assert install.corpus_files(corpus)  # and it actually has identity files to import


def worktree(tmp_path):
	"""A linked worktree, where .git is a FILE rather than a directory."""
	main = tmp_path / "main"
	main.mkdir()
	subprocess.run(["git", "init", "-q", str(main)], check=True)
	subprocess.run(["git", "-C", str(main), "-c", "user.email=t@t", "-c", "user.name=t",
	                "commit", "-q", "--allow-empty", "-m", "init"], check=True)
	wt = tmp_path / "wt"
	subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt), "-b", "side"], check=True)
	assert wt.joinpath(".git").is_file()  # the whole point of this fixture
	return wt


def test_wiring_a_worktree_still_hides_the_mirror_from_git(monkeypatch, tmp_path):
	"""In a worktree .git is a file; building <root>/.git/info/exclude raised, and the seed went ahead."""
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	os.makedirs(tmp_path / "mem")
	(tmp_path / "mem" / "o__n.md").write_text("- a fact\n")
	wt = worktree(tmp_path)
	loader = wt / "NOTES.md"
	loader.write_text("# notes\n")
	out = install.wire_repo(str(wt / ".agent" / "team"), str(loader), "o/n")
	assert not any(l.startswith("FAIL") for l in out), out
	assert (wt / ".agent" / "team" / "repo.md").exists()
	seen = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout
	assert ".agent" not in seen, seen  # the guarantee the docs make


def test_the_session_hook_seeds_nothing_it_cannot_hide(tmp_path):
	"""If the ignore cannot be written, the hook must stop rather than create visible files."""
	wt = worktree(tmp_path)
	hook = install.HOOK
	subprocess.run(["bash", hook], cwd=str(wt), capture_output=True)
	seen = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout
	assert (wt / ".agent").is_dir() and (wt / "CLAUDE.local.md").exists()
	assert seen.strip() == "", seen  # seeded, and invisible to git


def test_the_hook_is_registered_once_even_sharing_a_group(monkeypatch, tmp_path):
	"""Counting groups instead of hooks installed a second copy, and then failed to remove either."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	install.full_apply(corpus)
	got = __import__("json").loads((cfg / "settings.json").read_text())
	got["hooks"]["SessionStart"][0]["hooks"].append({"type": "command", "command": "someone-else"})
	got["hooks"]["SessionStart"].append({"hooks": []})  # an empty group somebody left behind
	(cfg / "settings.json").write_text(__import__("json").dumps(got))
	out = install.full_apply(corpus)
	assert any("already registered" in l for l in out)
	got = __import__("json").loads((cfg / "settings.json").read_text())
	assert sum(len(g["hooks"]) for g in got["hooks"]["SessionStart"]) == 2  # not three
	install.full_remove()
	got = __import__("json").loads((cfg / "settings.json").read_text())
	left = [h for g in got.get("hooks", {}).get("SessionStart", []) for h in g["hooks"]]
	assert [h["command"] for h in left] == ["someone-else"]  # ours gone, theirs untouched


def test_any_corpus_gets_the_hook_because_gitdashy_ships_it(monkeypatch, tmp_path):
	"""A corpus shipping no bin/ used to register a hook to a missing command, in every session."""
	cfg, _ = full_env(monkeypatch, tmp_path)
	bare = tmp_path / "bare"
	(bare / "identity").mkdir(parents=True)
	(bare / "identity" / "AGENT.md").write_text("# a\n")
	out = install.full_apply(str(bare))
	assert not any("SKIP" in l for l in out), out
	got = __import__("json").loads((cfg / "settings.json").read_text())
	cmd = got["hooks"]["SessionStart"][0]["hooks"][0]["command"]
	assert install.HOOK in cmd and os.path.isfile(install.HOOK)  # ours, and it is really there
	assert str(tmp_path / "corpus-home") in cmd  # told where the corpus is, for its templates


def test_the_hook_seeds_nothing_when_a_corpus_has_no_templates(tmp_path):
	"""A corpus is free to ship none; the hook must still wire the repo rather than fail."""
	wt = tmp_path / "repo"
	wt.mkdir()
	subprocess.run(["git", "init", "-q", str(wt)], check=True)
	subprocess.run(["bash", install.HOOK, str(tmp_path / "no-such-corpus")], cwd=str(wt), capture_output=True)
	assert (wt / "CLAUDE.local.md").exists() and (wt / ".agent").is_dir()
	assert not (wt / ".agent" / "STATE.md").exists()  # nothing to copy, and that is fine
	seen = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout
	assert seen.strip() == ""


def test_a_deleted_repo_is_forgotten_not_resurrected(monkeypatch, tmp_path):
	"""makedirs would rebuild the tree of a repo you deleted and write memory back into it."""
	fresh(monkeypatch, tmp_path)
	gone = tmp_path / "gone" / "sub" / "team"
	install.register(str(gone), "o/n")
	state.refresh_mirrors()
	assert not gone.exists() and install.registered() == []  # dropped, not recreated


def test_forgetting_a_mirror_leaves_its_files_alone(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	live = tmp_path / "repo" / ".agent" / "team"
	live.mkdir(parents=True)
	(live / "repo.md").write_text("- stays\n")
	install.register(str(live), "o/n")
	assert install.unregister(str(live)) is True
	assert install.registered() == [] and (live / "repo.md").exists()


def test_setup_writes_only_what_was_answered(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	home = tmp_path / "corpus"
	(home / "identity").mkdir(parents=True)
	answers = iter(["Nils", "", "ask first", "", "a platform", "", "CE marking", ""])
	out = install.setup(lambda p: next(answers), str(home))
	user = (home / "identity" / "USER.md").read_text()
	assert "## Name\n\nNils" in user and "## How you work\n\nask first" in user
	assert "## Role" not in user and "## What you own" not in user  # blanks are skipped, not left empty
	brief = (tmp_path / "mem" / "project.md").read_text()
	assert "## The project\n\na platform" in brief and "## Constraints\n\nCE marking" in brief
	assert any("wrote" in l and "USER.md" in l for l in out)


def test_setup_never_touches_a_user_md_you_wrote(monkeypatch, tmp_path):
	"""Re-running setup to change one line must not destroy the rest of the file."""
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	home = tmp_path / "corpus"
	(home / "identity").mkdir(parents=True)
	(home / "identity" / "USER.md").write_text("# mine\n\n## Name\n\nwritten by hand\n")
	out = install.setup(lambda p: "an answer", str(home))
	assert "written by hand" in (home / "identity" / "USER.md").read_text()
	assert any("is yours already" in l for l in out)


def test_setup_answering_nothing_writes_nothing(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	home = tmp_path / "corpus"
	(home / "identity").mkdir(parents=True)
	out = install.setup(lambda p: "", str(home))
	assert not (home / "identity" / "USER.md").exists()
	assert not (tmp_path / "mem" / "project.md").exists()
	assert any("nothing answered" in l for l in out) and any("no brief written" in l for l in out)


def test_setup_never_overwrites_a_brief_that_exists(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	os.makedirs(tmp_path / "mem")
	(tmp_path / "mem" / "project.md").write_text("ours, already written\n")
	home = tmp_path / "corpus"
	(home / "identity").mkdir(parents=True)
	out = install.setup(lambda p: "new answer", str(home))
	assert (tmp_path / "mem" / "project.md").read_text() == "ours, already written\n"
	assert any("already written" in l for l in out)


def test_setup_says_so_with_no_corpus_installed(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	out = install.setup(lambda p: "x", str(tmp_path / "nope"))
	assert any("SKIP" in l and "install --full" in l for l in out)


def test_uninstall_makes_a_mirror_inert_without_deleting_the_facts(monkeypatch, tmp_path):
	"""Deleting them reaches outside the agent config; leaving them live means sessions read frozen memory."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	os.makedirs(tmp_path / "mem")
	(tmp_path / "mem" / "o__n.md").write_text("- a fact\n")
	repo = tmp_path / "proj"
	repo.mkdir()
	subprocess.run(["git", "init", "-q", str(repo)], check=True)
	loader = repo / "NOTES.md"
	loader.write_text("# my notes\n")
	install.wire_repo(str(repo / ".agent" / "team"), str(loader), "o/n")
	assert "@.agent/team/repo.md" in loader.read_text()
	install.full_apply(corpus)
	install.full_remove()
	assert loader.read_text() == "# my notes\n"        # the import it added, and nothing else
	assert (repo / ".agent" / "team" / "repo.md").exists()  # the facts stay, as a snapshot
	assert install.registered() == []


def test_a_failed_corpus_copy_stops_before_wiring_anything(monkeypatch, tmp_path):
	"""It used to report success, then symlink and import a directory that was never created."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	def boom(*a, **k):
		raise OSError(28, "No space left on device")
	monkeypatch.setattr(install.shutil, "copytree", boom)
	out = install.full_apply(corpus)
	assert any(l.startswith("FAIL") for l in out)
	assert not (cfg / "identity").exists() and not (cfg / "CLAUDE.md").exists()


def test_refresh_skips_a_mirror_whose_directory_is_gone(monkeypatch, tmp_path):
	"""No recorded root needed: init creates the mirror, so a refresh never has cause to make one."""
	fresh(monkeypatch, tmp_path)
	gone = tmp_path / "deleted" / "deep" / "mirror"
	install.register(str(gone), "o/n")  # an old entry, with no root recorded
	state.refresh_mirrors()
	assert not gone.exists() and install.registered() == []


def test_setup_will_not_eat_a_template_you_filled_in(monkeypatch, tmp_path):
	"""install --full seeds USER.md FROM the template, and the template says the words 'gitdashy setup'."""
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	home = tmp_path / "corpus"
	(home / "identity").mkdir(parents=True)
	tmpl = pathlib.Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "corpus" / "identity" / "USER.md.template"
	seeded = home / "identity" / "USER.md"
	seeded.write_text(tmpl.read_text() + "\n**Name:** filled in by hand\n")
	out = install.setup(lambda p: "an answer", str(home))
	assert "filled in by hand" in seeded.read_text()
	assert any("is yours already" in l for l in out)
	# and a file setup itself wrote IS rewritable
	seeded.write_text(install.compose("t", "l", [("Name", "old")]))
	install.setup(lambda p: "new", str(home))
	assert "new" in seeded.read_text() and "old" not in seeded.read_text()


def test_the_brief_is_never_imported_globally(monkeypatch, tmp_path):
	"""A session used to get your brief through the block AND the team's through the mirror — two
	statements of what the work is for. The mirror carries the ONE brief(repo) picks; the block none."""
	cfg = fresh(monkeypatch, tmp_path)
	install.apply()
	block = (cfg / "CLAUDE.md").read_text()
	assert "project.md" not in block and "@prs-team/" not in block


def test_forgetting_a_mirror_does_not_rewrite_the_registry(monkeypatch, tmp_path):
	"""register appends without a lock; a truncating rewrite here would drop a concurrent append."""
	fresh(monkeypatch, tmp_path)
	for n in ("a", "b"):
		(tmp_path / n).mkdir()
		install.register(str(tmp_path / n), f"o/{n}")
	before = open(install.REGISTRY).read()
	assert install.unregister(str(tmp_path / "a")) is True
	assert open(install.REGISTRY).read().startswith(before)  # appended to, never truncated
	assert [i for i, *_ in install.registered()] == [str(tmp_path / "b")]
	assert install.unregister(str(tmp_path / "a")) is False  # already gone
	install.register(str(tmp_path / "a"), "o/a")  # and it can come back
	assert sorted(i for i, *_ in install.registered()) == [str(tmp_path / "a"), str(tmp_path / "b")]


def bare_remote(tmp_path, monkeypatch):
	remote = tmp_path / "remote.git"
	subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
	             ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	return remote


def test_an_empty_repo_fails_cleanly_rather_than_half_installing(monkeypatch, tmp_path):
	"""It used to report success at every step while leaving a dangling symlink and importing nothing."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	remote = bare_remote(tmp_path, monkeypatch)
	out = install.full_apply(corpus, str(remote))
	assert any(l.startswith("FAIL") and "no identity" in l for l in out)
	assert not os.path.lexists(cfg / "identity")
	# and it leaves nothing behind, so the next run is not poisoned by this one
	assert not os.path.isdir(tmp_path / "corpus-home")
	again = install.full_apply(corpus)
	assert not any(l.startswith("FAIL") for l in again), again
	assert "@identity/AGENT.md" in (cfg / "CLAUDE.md").read_text()


def test_a_corpus_with_no_identity_stops_rather_than_dangling(monkeypatch, tmp_path):
	cfg, _ = full_env(monkeypatch, tmp_path)
	bare = tmp_path / "not-a-corpus"
	(bare / "docs").mkdir(parents=True)
	(bare / "docs" / "x.md").write_text("# unrelated\n")
	out = install.full_apply(str(bare))
	assert any(l.startswith("FAIL") and "no identity" in l for l in out)
	assert not os.path.lexists(cfg / "identity")  # nothing linked at all
	dry = install.full_apply(str(bare), dry=True)
	assert any(l.startswith("FAIL") for l in dry)  # --dry-run must not report a clean plan


def test_setup_blank_keeps_what_is_there(monkeypatch, tmp_path):
	"""A rewrite that only knew its own answers deleted every section it did not ask about."""
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	home = tmp_path / "corpus"
	(home / "identity").mkdir(parents=True)
	user = home / "identity" / "USER.md"
	full = iter(["Martin R", "Engineer", "ask first", "the viewer", "", "", "", ""])
	install.setup(lambda p: next(full), str(home))
	user.write_text(user.read_text() + "\n## Hand added\n\nsomething I wrote\n")
	partial = iter(["Martin Rovang", "", "", "", "", "", "", ""])
	install.setup(lambda p: next(partial), str(home))
	got = install.sections(user.read_text())
	assert got["Name"] == "Martin Rovang"          # answered, so replaced
	assert got["Role"] == "Engineer"               # blank, so kept
	assert got["How you work"] == "ask first" and got["What you own"] == "the viewer"
	assert got["Hand added"] == "something I wrote"  # never asked about, still there


def test_setup_shows_what_is_there_before_asking(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	home = tmp_path / "corpus"
	(home / "identity").mkdir(parents=True)
	(home / "identity" / "USER.md").write_text(install.compose("t", "l", [("Name", "Martin R")]))
	seen = []
	install.setup(lambda p: seen.append(p) or "", str(home))
	assert any("[now: Martin R]" in p for p in seen)  # you can see what blank would keep


def test_sections_reads_a_brief_back():
	text = "<!-- m -->\n# T\n\nlead\n\n## A\n\none\n\n## B\n\ntwo\nlines\n"
	assert install.sections(text) == {"A": "one", "B": "two\nlines"}
	assert install.sections("no headings at all") == {}


def test_the_setup_marker_never_reaches_a_review(monkeypatch, tmp_path):
	"""It exists so setup can tell its output from yours; a reviewer has no use for it."""
	from dashy.core import memory
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "project.md").write_text(install.compose("What is being built", "lead", [("The project", "a thing")]))
	got, whose = memory.brief()
	assert "a thing" in got and install.SETUP_MARK not in got and "<!--" not in got
	assert whose == "yours"


# ---- siblings of guards that were fixed one case at a time ----


def test_the_registry_survives_a_tab_in_a_path(monkeypatch, tmp_path):
	"""The fields are paths, and a tab in a directory name is legal. Delimiting on one lost the rest."""
	fresh(monkeypatch, tmp_path)
	odd = str(tmp_path / "we\tird")
	install.register(odd, "o/n", root=str(tmp_path), loader=str(tmp_path / "L.md"))
	assert [i for i, *_ in install.registered()] == [odd]
	assert install.registered()[0][1] == "o/n"
	assert install.unregister(odd) is True and install.registered() == []


def test_the_registry_survives_a_path_that_looks_like_a_tombstone(monkeypatch, tmp_path):
	fresh(monkeypatch, tmp_path)
	odd = str(tmp_path / "-forget-me")
	install.register(odd, "o/n")
	assert [i for i, *_ in install.registered()] == [odd]


def test_a_heading_inside_a_code_block_is_not_a_section():
	got = install.sections("## A\n\n```\n## not a heading\n```\n\n## B\n\ntwo\n")
	assert list(got) == ["A", "B"] and "## not a heading" in got["A"]


def test_an_answer_cannot_forge_a_section():
	"""setup rewrites the file from what sections() returns, so a forged heading rearranges your text."""
	text = install.compose("W", "l", [("Name", "Nils\n## Role\n\nCTO")])
	back = install.sections(text)
	assert list(back) == ["Name"]
	assert back["Name"] == "Nils\n## Role\n\nCTO"  # and it round-trips unchanged


def test_uninstall_removes_every_block_not_just_the_first(monkeypatch, tmp_path):
	"""A config copied between machines carries the block twice; removing one of two reads as clean."""
	cfg = fresh(monkeypatch, tmp_path)
	(cfg / "CLAUDE.md").write_text(install.BLOCK + "\nkeep me\n" + install.BLOCK)
	install.remove()
	left = (cfg / "CLAUDE.md").read_text()
	assert install.BEGIN not in left and "keep me" in left


def test_full_uninstall_removes_every_corpus_block_too(monkeypatch, tmp_path):
	cfg, corpus = full_env(monkeypatch, tmp_path)
	blk = install.CBEGIN + "\nx\n" + install.CEND + "\n"
	(cfg / "CLAUDE.md").write_text(blk + "\nkeep\n" + blk)
	install.full_remove()
	left = (cfg / "CLAUDE.md").read_text()
	assert install.CBEGIN not in left and "keep" in left


def test_uninstall_leaves_somebody_elses_hook_of_the_same_name(monkeypatch, tmp_path):
	"""Matching a bare filename would delete a hook that merely shares it."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	settings = {"hooks": {"SessionStart": [{"hooks": [
		{"type": "command", "command": "/opt/theirs/claude-session-start.sh"}]}]}}
	(cfg / "settings.json").write_text(__import__("json").dumps(settings))
	install.full_apply(corpus)
	install.full_remove()
	got = __import__("json").loads((cfg / "settings.json").read_text())
	left = [h["command"] for g in got.get("hooks", {}).get("SessionStart", []) for h in g["hooks"]]
	assert left == ["/opt/theirs/claude-session-start.sh"]


def test_a_broken_settings_file_undoes_the_symlink_and_imports(monkeypatch, tmp_path):
	"""Every failure path used to keep whatever it had already done — this is the one nobody named."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	# ponytail: seeded, because an empty CLAUDE.md satisfies "@identity not in text" just as well as
	# a preserved one — and the undo was in fact emptying it. The assertion has to be what SURVIVES.
	(cfg / "CLAUDE.md").write_text("# mine\n\nkeep this\n")
	(cfg / "settings.json").write_text("{not json")
	out = install.full_apply(corpus)
	assert any(l.startswith("FAIL") and "valid JSON" in l for l in out)
	assert any("undone" in l for l in out)
	assert not any("COULD NOT UNDO" in l for l in out)
	assert not os.path.lexists(cfg / "identity")
	assert (cfg / "CLAUDE.md").read_text() == "# mine\n\nkeep this\n"  # byte for byte, not merely un-imported
	(cfg / "settings.json").write_text("{}")
	assert not any(l.startswith("FAIL") for l in install.full_apply(corpus))  # and it recovers


def test_an_undone_install_leaves_no_claude_md_it_created(monkeypatch, tmp_path):
	"""Undo means the state before. A file we made and then emptied is not that state."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	(cfg / "settings.json").write_text("{not json")
	assert not (cfg / "CLAUDE.md").exists()
	install.full_apply(corpus)
	assert not (cfg / "CLAUDE.md").exists()


def test_the_corpus_undo_cannot_be_retargeted_after_it_is_queued(monkeypatch, tmp_path):
	"""A late-bound global in an rmtree lambda is a recursive delete pointed at whatever it says later."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	(cfg / "settings.json").write_text("{not json")
	elsewhere = tmp_path / "someone-elses-work"
	elsewhere.mkdir()
	(elsewhere / "thesis.txt").write_text("years of it")
	real_rm, calls, real_read = install.knowledge.rmtree_owned, [], install._read

	def spy(path):
		calls.append(path)
		return real_rm(path)

	def moved(path):  # the global moves AFTER the undo is queued and BEFORE it runs
		monkeypatch.setattr(install, "CORPUS_HOME", str(elsewhere), raising=False)
		return real_read(path)

	monkeypatch.setattr(install.knowledge, "rmtree_owned", spy)
	monkeypatch.setattr(install, "_read", moved)
	install.full_apply(corpus)
	assert calls and str(elsewhere) not in calls  # it deleted what it queued, not what the name says now
	assert (elsewhere / "thesis.txt").exists()


def test_rmtree_owned_refuses_a_link_a_root_and_a_home(tmp_path, monkeypatch):
	"""rm -rf with no confirmation gets a gate, not a comment."""
	from dashy.core import knowledge
	real = tmp_path / "real"
	real.mkdir()
	(real / "keep").write_text("x")
	link = tmp_path / "link"
	os.symlink(real, link)
	assert "refusing" in knowledge.rmtree_owned(str(link)) and "link to" in knowledge.rmtree_owned(str(link))
	assert (real / "keep").exists()  # the target is untouched
	monkeypatch.setenv("HOME", str(tmp_path))
	assert "refusing" in knowledge.rmtree_owned(str(tmp_path))
	assert "refusing" in knowledge.rmtree_owned(os.sep)
	assert knowledge.rmtree_owned(str(tmp_path / "never-existed")) == ""  # gone is gone
	assert knowledge.rmtree_owned(str(real)) == "" and not real.exists()


def test_leaving_a_team_does_not_delete_through_a_symlink(monkeypatch, tmp_path):
	"""realpath-then-rmtree deleted the checkout a link pointed at — someone's actual repo."""
	from dashy.core import knowledge
	work = tmp_path / "my-real-checkout"
	work.mkdir()
	(work / "important.md").write_text("mine")
	(work / ".git").mkdir()   # ponytail: a checkout, since "joined" is now "a directory with a .git"
	teams = tmp_path / "teams"
	teams.mkdir()
	link = teams / "org-t"
	os.symlink(work, link)
	monkeypatch.setattr(config, "TEAMS", str(teams))
	monkeypatch.setattr(knowledge, "unpushed", lambda d=None: 0)
	assert knowledge.leave("org-t") == ""
	assert not os.path.lexists(link)          # the link is ours
	assert (work / "important.md").exists()   # what it pointed at is not


def test_a_code_block_survives_being_written_back(monkeypatch, tmp_path):
	"""The reader learned about fences and the writer did not, so it escaped comments inside them."""
	body = "```python\n# a comment\nx = 1\n```"
	text = install.compose("W", "l", [("Name", body)])
	assert "\\# a comment" not in text and "# a comment" in text  # on disk, not just after a round trip
	assert install.sections(text)["Name"] == body
	# and structure outside a fence is still neutralised
	forged = install.compose("W", "l", [("Name", "x\n## Role\n\nCTO")])
	assert list(install.sections(forged)) == ["Name"]


def test_a_repeated_heading_is_kept_not_replaced():
	"""Overwriting meant a rewrite silently deleted the first one."""
	assert install.sections("## A\n\none\n\n## A\n\ntwo\n") == {"A": "one\n\ntwo"}


def test_setup_holds_the_file_still(monkeypatch, tmp_path):
	"""Hand-added sections used to move to the bottom on every run, so the file never settled."""
	fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	home = tmp_path / "corpus"
	(home / "identity").mkdir(parents=True)
	user = home / "identity" / "USER.md"
	user.write_text(install.compose("Who you are", "lead",
	                                [("Context", "written above"), ("Name", "Nils"), ("Role", "eng")]))
	before = [l for l in user.read_text().splitlines() if l.startswith("## ")]
	install.setup(lambda p: "", str(home))
	after = [l for l in user.read_text().splitlines() if l.startswith("## ")]
	assert after[:3] == before  # same order, nothing moved
	assert after[0] == "## Context"


def test_tilde_fences_and_unclosed_ones_do_not_leak(monkeypatch, tmp_path):
	"""Three loops each knew about ``` and none about ~~~, so a fence meant three different things."""
	assert install.sections("## A\n\n~~~\n## B\n~~~\n") == {"A": "~~~\n## B\n~~~"}
	# an info string can only open, so a fenced sample quoting one does not close the block
	assert install.sections("## A\n\n```\n```python\n```\n") == {"A": "```\n```python\n```"}
	# a longer fence is not closed by a shorter one
	assert install.sections("## A\n\n````\n```\n## B\n````\n") == {"A": "````\n```\n## B\n````"}
	# and an unclosed fence in an answer is closed by the writer, not left to swallow what follows
	text = install.compose("W", "l", [("Name", "```"), ("Role", "CTO")])
	assert install.sections(text)["Role"] == "CTO"


def test_a_fenced_quote_of_our_own_markers_is_not_eaten(monkeypatch, tmp_path):
	"""CLAUDE.md is the user's file; quoting the block we add is a normal thing to write in it."""
	quoted = f"# mine\n\n```\n{install.CBEGIN}\nsample\n{install.CEND}\n```\n"
	assert install._strip_blocks(quoted, install.CBEGIN, install.CEND) == quoted
	# a real block beside the quoted one still goes, and only it
	live = quoted + f"\n{install.CBEGIN}\nreal\n{install.CEND}\n"
	out = install._strip_blocks(live, install.CBEGIN, install.CEND)
	assert "sample" in out and "real" not in out


def test_a_registry_from_before_the_format_changed_is_not_dropped(monkeypatch, tmp_path):
	"""json.loads failing on every line read the whole registry back as empty, silently."""
	reg = tmp_path / "prs_mirrors"
	reg.write_text(f"{tmp_path}/a\t{tmp_path}/repo-a\n")
	monkeypatch.setattr(install, "REGISTRY", str(reg))
	assert install.registered() == [(f"{tmp_path}/a", f"{tmp_path}/repo-a", "", "")]
	install.register(str(tmp_path / "b"), str(tmp_path / "repo-b"))
	assert len(install.registered()) == 2  # the old line and the new one coexist
	assert "not a path at all" not in str(install.registered())
	reg.write_text(reg.read_text() + "not a path at all\n")
	assert len(install.registered()) == 2  # and a line that is neither is still just skipped


def test_writing_a_config_keeps_the_symlink_and_the_mode(monkeypatch, tmp_path):
	"""~/.claude/CLAUDE.md in a dotfiles checkout is the normal setup, not the exotic one."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	dotfiles = tmp_path / "dotfiles"
	dotfiles.mkdir()
	(dotfiles / "CLAUDE.md").write_text("# mine\n\nkeep this\n")
	(cfg / "CLAUDE.md").symlink_to(dotfiles / "CLAUDE.md")
	(cfg / "settings.json").write_text("{}")
	os.chmod(cfg / "settings.json", 0o600)

	install.full_apply(corpus)
	assert os.path.islink(cfg / "CLAUDE.md")                       # still a link
	assert "@identity" in (dotfiles / "CLAUDE.md").read_text()     # written through it
	assert oct(os.stat(cfg / "settings.json").st_mode)[-3:] == "600"  # env blocks hold API keys

	install.full_remove()
	assert os.path.islink(cfg / "CLAUDE.md")
	assert (dotfiles / "CLAUDE.md").read_text() == "# mine\n\nkeep this\n"
	assert oct(os.stat(cfg / "settings.json").st_mode)[-3:] == "600"


def test_stripping_a_block_keeps_the_files_last_newline():
	"""A user-owned file, rewritten — leave it shaped the way they had it."""
	text = f"# mine\n\n{install.CBEGIN}\nx\n{install.CEND}\n\nmore\n"
	assert install._strip_blocks(text, install.CBEGIN, install.CEND) == "# mine\nmore\n"
	assert install._strip_blocks("# mine\nmore", install.CBEGIN, install.CEND) == "# mine\nmore"


def test_the_temp_file_is_never_wider_than_its_target(monkeypatch, tmp_path):
	"""settings.json holds env blocks with API keys; 0644 for the length of a write is still a leak."""
	target = tmp_path / "settings.json"
	target.write_text("{}")
	os.chmod(target, 0o600)
	seen = []
	real_open = os.open

	def spy(path, flags, mode=0o777, **kw):
		if str(path).endswith(".gitdashy.tmp"):
			seen.append(mode)
		return real_open(path, flags, mode, **kw)

	monkeypatch.setattr(os, "open", spy)
	install._write_text(str(target), '{"a": 1}')
	assert seen and all(m <= 0o600 for m in seen)          # narrow while being written
	assert oct(os.stat(target).st_mode)[-3:] == "600"      # and the target's own mode afterwards


def test_setup_can_still_fill_the_file_install_seeded(monkeypatch, tmp_path):
	"""install --full seeds the shipped template; setup read that as a hand-written file and refused.

	That left the guided path dead in exactly the case it exists for — the offer fires, you say yes,
	and it declines to ask.
	"""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	mem = tmp_path / "mem"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", "")
	install.full_apply(corpus)
	assert not install.setup_done()  # a seeded template is not a finished brief

	answers = iter(["Ada", "backend", "ask early", "the API", "a viewer", "clinicians", "CE", "layered"])
	install.setup(lambda p: next(answers, ""), corpus_home=install.CORPUS_HOME)

	user = pathlib.Path(install.CORPUS_HOME) / "identity" / "USER.md"
	assert "Ada" in user.read_text() and install.SETUP_MARK in user.read_text()
	assert install.setup_done()


def test_setup_still_refuses_a_file_you_wrote_yourself(monkeypatch, tmp_path):
	"""The guard that made the above fail is the one protecting hand-written files. Keep it."""
	cfg, corpus = full_env(monkeypatch, tmp_path)
	mem = tmp_path / "mem"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", "")
	install.full_apply(corpus)
	user = pathlib.Path(install.CORPUS_HOME) / "identity" / "USER.md"
	user.write_text("# mine\n\nhand written\n")   # one edit of their own and it is theirs

	out = install.setup(lambda p: "should not be asked", corpus_home=install.CORPUS_HOME)

	assert user.read_text() == "# mine\n\nhand written\n"
	assert any("is yours already" in l for l in out)


def test_setup_tells_several_teams_apart_from_no_team(monkeypatch, tmp_path):
	"""bind.team_key() is "" for NONE and for SEVERAL, and those are different problems. Two joined
	teams is the normal state this whole change exists to create."""
	from dashy.core import memory, team
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for key in ("org-one", "org-two"):
		(tmp_path / "teams" / key / ".git").mkdir(parents=True)
	out = install.setup(lambda q: "a thing", corpus_home=str(tmp_path / "nocorpus"))
	note = next(l for l in out if l.startswith("note"))
	assert "several teams joined" in note and "org-one, org-two" in note
	assert "--team" not in note  # setup parses no arguments; it must not advise a flag it does not have
	assert "no origin" not in note and "no name" not in note
	assert os.path.exists(memory.brief_path())   # and it still wrote YOUR brief, as it says


def test_install_retires_the_old_team_link_and_rewrites_its_block(monkeypatch, tmp_path):
	"""The pre-2026-09-08 install pointed `prs-team` at ONE team's memory and imported it into every
	session on the machine. With several teams it dangled; with one it told every repo how that team
	works. A dangling @import is skipped silently, so the block has to be rewritten, not left."""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	old_block = install.BLOCK.replace(install.IMPORT, install.IMPORT + "\n@prs-team/general.md")
	(cfg / "CLAUDE.md").write_text("# mine\n\n" + old_block)
	os.symlink(str(tmp_path / "teams" / "org-t" / "memory"), str(cfg / "prs-team"))   # dangling, ours
	(cfg / "prs-memory").symlink_to(str(tmp_path / "mem"))

	dry = install.apply(dry=True)
	assert any("would retire" in l and "prs-team" in l for l in dry)
	assert (cfg / "prs-team").is_symlink()                     # a dry run touches nothing

	out = install.apply()
	assert any(l.startswith("retire") for l in out) and any("update the import block" in l for l in out)
	assert not (cfg / "prs-team").exists()
	text = (cfg / "CLAUDE.md").read_text()
	assert "@prs-team/" not in text and text.count(install.IMPORT) == 1
	assert text.startswith("# mine\n")                          # the user's own lines survive the rewrite
	assert all(l.startswith("ok") for l in install.apply())     # and it is done once


def test_install_leaves_a_prs_team_link_that_is_not_ours(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(tmp_path / "theirs").mkdir()
	os.symlink(str(tmp_path / "theirs"), str(cfg / "prs-team"))  # someone's own link, same name
	install.apply()
	assert (cfg / "prs-team").is_symlink()
	install.remove()
	assert (cfg / "prs-team").is_symlink()


def test_uninstall_removes_a_retired_team_link_too(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	install.apply()
	os.symlink(str(tmp_path / "team" / "memory"), str(cfg / "prs-team"))   # the pre-plural target
	out = install.remove()
	assert any("retired team link" in l for l in out)
	assert not (cfg / "prs-team").exists() and not (cfg / "prs-memory").exists()


def test_install_says_so_when_the_stale_team_import_is_not_in_our_block(monkeypatch, tmp_path):
	"""A hand-wired CLAUDE.md is not ours to rewrite. But the link it imports through is retired, so the
	import now dangles and the loader skips it silently — the retirement has to be said out loud."""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(cfg / "CLAUDE.md").write_text("# mine\n@prs-memory/general.md\n@prs-team/general.md\n")
	os.symlink(str(tmp_path / "teams" / "x" / "memory"), str(cfg / "prs-team"))
	out = install.apply()
	assert not (cfg / "prs-team").exists()
	assert any(l.startswith("NOTE") and "remove them by hand" in l for l in out)
	assert (cfg / "CLAUDE.md").read_text().count("\n") == 3   # untouched


def test_explain_counts_the_imports_it_will_write_and_names_the_migration(monkeypatch, tmp_path):
	"""The consent screen said "four imports" after the block had two, and nothing about removing a
	symlink and rewriting a block in the user's own config — the thing it was asking consent for."""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	n = install.BLOCK.count("\n@")
	assert any(f"append {n} import" in l for l in install.explain())
	assert not any("retire" in l for l in install.explain())       # nothing to migrate on a fresh machine

	os.symlink(str(tmp_path / "teams" / "x" / "memory"), str(cfg / "prs-team"))
	(cfg / "CLAUDE.md").write_text(install.BLOCK.replace(install.IMPORT, install.IMPORT + "\n@prs-team/general.md"))
	out = install.explain()
	assert any("retire" in l and "prs-team" in l for l in out)
	assert any("update the import block" in l for l in out)
	assert (cfg / "prs-team").is_symlink()                           # explain() explains; it does nothing


def test_a_claude_md_that_only_quotes_the_old_block_is_left_alone(monkeypatch, tmp_path):
	"""A raw substring test took the rewrite branch for a CLAUDE.md that QUOTED the old block in a code
	sample, stripped nothing, and appended BLOCK again on every run — never reaching "ok"."""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	quoted = "# notes\n\n```\n" + install.BLOCK.replace(install.IMPORT, install.IMPORT + "\n@prs-team/general.md") + "```\n"
	(cfg / "CLAUDE.md").write_text(quoted)
	for _ in range(2):
		out = install.apply()
		assert not any("update" in l or "NOTE" in l for l in out), out
	assert (cfg / "CLAUDE.md").read_text() == quoted
	assert install.retire() == []


def test_a_relative_link_is_judged_from_its_own_directory(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(cfg / "teams"))          # so a relative "teams/x" is inside it
	os.symlink("teams/x/memory", str(cfg / "prs-team"))
	monkeypatch.chdir(tmp_path)                                        # an unlucky cwd must not change the answer
	assert install.stale_team_link() == str(cfg / "prs-team")


def test_session_notes_say_what_a_session_is_not_being_told(monkeypatch, tmp_path):
	"""The tool this was built next to lacked the `remember` instruction for months while every draft
	sat at (1). A check that reports beats a README line the reader is assumed to have followed."""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	assert install.corpus_remembers() is None and install.session_notes() == []   # no corpus: nothing to say
	ident = cfg / "identity"
	ident.mkdir()
	(ident / "AGENT.md").write_text("# me\nbe good\n")
	assert install.corpus_remembers() is False
	assert install.session_notes() == ["corpus never says `gitdashy remember`"]
	(ident / "AGENT.md").write_text("# me\n`gitdashy remember` what will still be true next month\n")
	assert install.session_notes() == []
	(cfg / "CLAUDE.md").write_text("@prs-memory/general.md\n@prs-team/general.md\n")
	assert install.session_notes() == ["CLAUDE.md imports @prs-team by hand"]
	(cfg / "CLAUDE.md").write_text("```\n@prs-team/general.md\n```\n")   # quoted, not wired
	assert install.session_notes() == []


def test_full_install_says_when_the_corpus_never_tells_a_session_to_remember(monkeypatch, tmp_path):
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	monkeypatch.setattr(install, "CORPUS_HOME", str(tmp_path / "corpus"))
	monkeypatch.setattr(install, "HOOK", str(tmp_path / "hook.sh"))
	(tmp_path / "hook.sh").write_text("#!/bin/sh\n")
	os.chmod(tmp_path / "hook.sh", 0o755)
	(tmp_path / "corpus" / "identity").mkdir(parents=True)
	(tmp_path / "corpus" / "identity" / "AGENT.md").write_text("# quiet\n")
	out = install.full_apply(str(tmp_path / "corpus"))
	assert any(l.startswith("NOTE") and "gitdashy remember" in l for l in out), out
	install.full_remove()
	(tmp_path / "corpus" / "identity" / "AGENT.md").write_text("# loud\nrun `gitdashy remember` for durable facts\n")
	out = install.full_apply(str(tmp_path / "corpus"))
	assert not any("gitdashy remember" in l for l in out), out


def test_a_blank_store_root_does_not_make_every_link_ours(monkeypatch, tmp_path):
	"""abspath("") is the CURRENT WORKING DIRECTORY, not "nowhere".

	`gitdashy --demo` blanks config.TEAM and config.TEAMS, and PRS_TEAMS= does the same. With the
	prefix test taken against cwd, any symlink under the directory you happened to launch from counted
	as gitdashy's own — and retire() deletes what it matches. This is the case
	test_install_leaves_a_prs_team_link_that_is_not_ours exists to forbid, reached by a different door.
	"""
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	mine = tmp_path / "work" / "notes"          # the user's own, under what will be cwd
	mine.mkdir(parents=True)
	os.symlink(str(mine), str(cfg / "prs-team"))
	monkeypatch.setattr(config, "TEAMS", "")
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.chdir(tmp_path)
	assert install.stale_team_link() == ""       # not ours: no store is configured at all
	assert install.retire() == []                # so nothing is retired, and the link survives
	assert os.path.islink(str(cfg / "prs-team"))


def test_a_relative_store_root_is_refused_too(monkeypatch, tmp_path):
	"""Same door, one step along: a relative root is only meaningful against a cwd this cannot trust."""
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	mine = tmp_path / "teams" / "acme"
	mine.mkdir(parents=True)
	os.symlink(str(mine), str(cfg / "prs-team"))
	monkeypatch.setattr(config, "TEAMS", "teams")   # relative: resolves against whatever cwd happens to be
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.chdir(tmp_path)
	assert install.stale_team_link() == ""


def test_the_real_store_root_still_matches(monkeypatch, tmp_path):
	"""The guard must not have turned the whole check off — the link it IS for is still retired."""
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	teams = tmp_path / "teams"
	(teams / "acme" / "memory").mkdir(parents=True)
	os.symlink(str(teams / "acme" / "memory"), str(cfg / "prs-team"))
	monkeypatch.setattr(config, "TEAMS", str(teams))
	monkeypatch.setattr(config, "TEAM", "")
	assert install.stale_team_link() == str(cfg / "prs-team")


def test_the_retirement_says_the_brief_goes_with_the_facts(monkeypatch, tmp_path):
	"""A repo with no mirror loses the BRIEF as well, and that is the half the message was silent on.

	The global @prs-memory/project.md import is gone after migration, so someone who installed without
	--full, or never ran `gitdashy init` in a repo, gets no brief at all — and the one place they are
	told about the change only mentioned the facts.
	"""
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	teams = tmp_path / "teams"
	(teams / "acme" / "memory").mkdir(parents=True)
	os.symlink(str(teams / "acme" / "memory"), str(cfg / "prs-team"))
	monkeypatch.setattr(config, "TEAMS", str(teams))
	monkeypatch.setattr(config, "TEAM", "")
	said = " ".join(install.retire(dry=True))
	assert "brief" in said, said
	assert "gitdashy init" in said, said          # and what to do about it


def test_session_notes_is_not_full_file_io_on_every_draw(monkeypatch, tmp_path):
	"""header_groups() calls this and draw() calls that on every tick — 50ms while anything spins.

	Uncached it read every identity/*.md, the whole CLAUDE.md, and ran _strip_blocks() over it, twice a
	second forever. Cached on a stat-level key it still has to notice an edit, so both halves are here.
	"""
	cfg = tmp_path / "claude"
	(cfg / "identity").mkdir(parents=True)
	(cfg / "identity" / "AGENT.md").write_text("no instruction here\n")
	(cfg / "CLAUDE.md").write_text("nothing\n")
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	monkeypatch.setattr(install, "_NOTES", (None, []))
	reads = []
	real = install._read
	monkeypatch.setattr(install, "_read", lambda p: reads.append(p) or real(p))

	assert "corpus never says `gitdashy remember`" in install.session_notes()
	first = len(reads)
	assert first > 0                                   # it really did read, the first time
	for _ in range(20):
		install.session_notes()
	assert len(reads) == first, reads                  # and not once more, twenty draws later

	(cfg / "identity" / "AGENT.md").write_text("file it with `gitdashy remember` when you learn one\n")
	assert install.session_notes() == []                # but an edit is still seen
	assert len(reads) > first


def test_the_hook_actually_runs_the_drafts_count_line(tmp_path):
	"""No test in the suite executed this script's step 4, so the line was only proven by reading it.

	A stub `gitdashy` on PATH stands in for the real one: what is under test is that the hook calls it
	and lets its stdout through, not what `drafts --count` itself decides.
	"""
	wt = tmp_path / "repo"
	wt.mkdir()
	subprocess.run(["git", "init", "-q", str(wt)], check=True)
	binp = tmp_path / "bin"
	binp.mkdir()
	stub = binp / "gitdashy"
	stub.write_text('#!/usr/bin/env bash\n'
	                'if [ "$1" = "drafts" ]; then echo "gitdashy: 2 drafts waiting for acme/web"; fi\n'
	                'exit 0\n')
	stub.chmod(0o755)
	env = {**os.environ, "PATH": f"{binp}:{os.environ['PATH']}", "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}
	out = subprocess.run(["bash", install.HOOK, str(tmp_path / "no-such-corpus")], cwd=str(wt),
	                     capture_output=True, text=True, env=env).stdout
	assert "2 drafts waiting for acme/web" in out, out


def test_the_hook_survives_a_gitdashy_that_is_not_there(tmp_path):
	"""command -v guards it; without that the hook fails at the end of every session on a machine
	mid-uninstall, which is a hook the user removes.

	ponytail: the real PATH with only the entries holding a `gitdashy` removed. A hand-built PATH of
	just git and bash exited 0 at step 1 — `grep` was gone too, so the ignore could not be verified and
	the hook bailed before reaching the guard under test. Removing one tool is the smaller change, and
	it is the one the case is actually about.
	"""
	wt = tmp_path / "repo"
	wt.mkdir()
	subprocess.run(["git", "init", "-q", str(wt)], check=True)
	kept = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
	                       if d and not os.path.exists(os.path.join(d, "gitdashy")))
	assert shutil.which("gitdashy", path=kept) is None           # the guard really has nothing to find
	env = {**os.environ, "PATH": kept, "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}
	done = subprocess.run(["bash", install.HOOK, str(tmp_path / "no-such-corpus")], cwd=str(wt),
	                      capture_output=True, text=True, env=env)
	assert done.returncode == 0, done.stderr
	assert os.path.isdir(str(wt / ".agent"))                     # and every step before it still ran
	assert os.path.exists(str(wt / "CLAUDE.local.md"))


def test_the_session_hook_says_what_it_is_loading(tmp_path):
	"""'Know what you are loading' was a sentence in a README. One line at session start is a guard
	that does not depend on being remembered; the corpus that ships this hook grew to twice its stated
	ceiling before anyone measured."""
	wt = tmp_path / "repo"
	wt.mkdir()
	subprocess.run(["git", "init", "-q", str(wt)], check=True)
	cfg = tmp_path / "cfg"
	(cfg / "identity").mkdir(parents=True)
	(cfg / "identity" / "AGENT.md").write_text("one two three four five six seven eight nine ten\n")
	env = {**os.environ, "CLAUDE_CONFIG_DIR": str(cfg)}
	out = subprocess.run(["bash", install.HOOK, str(tmp_path / "no-such-corpus")], cwd=str(wt),
	                     capture_output=True, text=True, env=env).stdout
	assert "[budget] identity ~13 tok" in out, out  # 10 words * 1.35, the estimate the corpus uses
	assert "STATE.md" not in out  # none seeded from a corpus with no templates, so none reported
	seen = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout
	assert seen.strip() == ""  # still writes nothing git can see


def test_a_corpus_that_ships_its_own_budget_check_runs_it_instead(tmp_path):
	"""A corpus knows its own budgets better than a generic total does; when it ships the check, the
	hook defers to it and says nothing of its own. Guarded on -x, so a corpus without one gets the
	generic line rather than a hook pointing at a missing command."""
	wt = tmp_path / "repo"
	wt.mkdir()
	subprocess.run(["git", "init", "-q", str(wt)], check=True)
	corpus = tmp_path / "corpus"
	(corpus / "bin").mkdir(parents=True)
	check = corpus / "bin" / "budget-check.sh"
	check.write_text("#!/usr/bin/env bash\necho 'mine 1 / 2 tok'\n")
	check.chmod(0o755)
	out = subprocess.run(["bash", install.HOOK, str(corpus)], cwd=str(wt), capture_output=True, text=True).stdout
	assert "[budget] mine 1 / 2 tok" in out, out
	assert "identity ~" not in out  # the generic line yields to the corpus's own


def test_explain_describes_the_corpus_that_will_actually_be_imported(monkeypatch, tmp_path):
	"""Explain read the shipped corpus while apply imported CORPUS_HOME. With CORPUS_HOME pointed at
	your own corpus, the report named the wrong files and the wrong cost right before asking you
	to agree to it."""
	_, corpus = full_env(monkeypatch, tmp_path)
	install.full_apply(corpus)                       # CORPUS_HOME now exists, seeded from the shipped one
	extra = os.path.join(install.CORPUS_HOME, "identity", "EXTRA.md")
	open(extra, "w").write("a corpus the user has since made their own\n")
	out = "\n".join(install.full_explain(corpus))
	assert "EXTRA.md" in out, out                    # from CORPUS_HOME, not from the shipped corpus
	# ponytail: the count and the cost, not only the name. `assert "AGENTS.md" not in out` used to stand
	# here and could not fail — neither corpus has that file any more, so it passed with the fix reverted.
	# These two move when `src` moves, which is the thing the fix changed.
	home, shipped = install.corpus_files(install.CORPUS_HOME), install.corpus_files(corpus)
	assert len(home) != len(shipped)                            # or neither assertion below can fail
	assert f"import {len(home)} files" in out, out              # EXTRA.md is COUNTED, not only listed
	words = sum(len(open(os.path.join(install.CORPUS_HOME, "identity", n)).read().split()) for n in home)
	assert f"{int(words * 1.35):,} tokens" in out, out          # and the cost is measured there too


def test_explain_does_not_pass_off_the_shipped_corpus_as_the_one_being_cloned(monkeypatch, tmp_path):
	"""--corpus URL with no CORPUS_HOME yet cannot know the remote's files, and said the shipped ones.

	The report named 3 files and a token cost for a corpus that was about to be replaced by a different
	one — the number a reader consents to was measured from something they will never load.
	"""
	_, corpus = full_env(monkeypatch, tmp_path)
	out = "\n".join(install.full_explain(corpus, url="https://example.invalid/theirs.git"))
	assert "https://example.invalid/theirs.git" in out, out
	assert "cannot be" in out and "until it is cloned" in out, out
	shipped = install.corpus_files(corpus)
	assert f"import {len(shipped)} files" not in out, out       # no file list stated as fact
	assert "tokens of instructions" not in out, out             # and no cost stated as fact


def test_full_explain_names_the_shell_it_will_run(monkeypatch, tmp_path):
	"""The consent screen has to name the exec, because the exec is the new kind of thing.

	Until this corpus shipped a bin/, a corpus was DATA: markdown imported into context, templates
	copied. The hook now runs a script out of it at every session start, in every repo — so
	`--corpus URL` is code you execute, not only text you read, and consent that does not say so is
	not consent to it.
	"""
	_, corpus = full_env(monkeypatch, tmp_path)
	out = "\n".join(install.full_explain(corpus))
	assert "budget-check.sh" in out, out
	assert "RUNS" in out or "runs" in out, out
	assert "every session start" in out, out


def _hook_repo(tmp_path):
	"""A git repo and an agent-config dir, the two things the hook reads before it says anything."""
	wt = tmp_path / "repo"
	wt.mkdir()
	subprocess.run(["git", "init", "-q", str(wt)], check=True)
	cfg = tmp_path / "cfg"
	(cfg / "identity").mkdir(parents=True)
	return wt, cfg


def _run_hook(wt, cfg, corpus):
	env = {**os.environ, "CLAUDE_CONFIG_DIR": str(cfg)}
	return subprocess.run(["bash", install.HOOK, str(corpus)], cwd=str(wt),
	                      capture_output=True, text=True, env=env).stdout


def test_the_hook_reports_a_seeded_STATE_md_beside_the_identity_total(tmp_path):
	"""The branch that fires on every real --full install, and had no test.

	Step 2 seeds .agent/STATE.md from the corpus template, so by the time step 5 runs there is one to
	report; the only hook test asserted it was ABSENT, which is the case a corpus with no templates
	produces and not the one a user gets.
	"""
	wt, cfg = _hook_repo(tmp_path)
	(cfg / "identity" / "AGENT.md").write_text("one two three four five six seven eight nine ten\n")
	corpus = tmp_path / "corpus"
	(corpus / "repo-template").mkdir(parents=True)
	(corpus / "repo-template" / "STATE.md").write_text("a b c d\n")
	out = _run_hook(wt, cfg, corpus)
	assert "identity ~13 tok" in out, out
	assert ".agent/STATE.md ~5 tok" in out, out   # 4 words * 1.35
	assert out.count("[budget]") == 1, out        # one line, one prefix, not one per part


def test_a_budget_check_without_the_executable_bit_falls_back(tmp_path):
	"""The case the -x guard exists for, and the only one that was never run.

	A corpus cloned without the bit set (or shipped with it lost) must get the generic line, not a hook
	that points at a command it cannot run.
	"""
	wt, cfg = _hook_repo(tmp_path)
	(cfg / "identity" / "AGENT.md").write_text("one two three four five six seven eight nine ten\n")
	corpus = tmp_path / "corpus"
	(corpus / "bin").mkdir(parents=True)
	check = corpus / "bin" / "budget-check.sh"
	check.write_text("#!/usr/bin/env bash\necho 'mine 1 / 2 tok'\n")
	check.chmod(0o644)                            # present, not executable
	out = _run_hook(wt, cfg, corpus)
	assert "identity ~13 tok" in out, out
	assert "mine 1 / 2 tok" not in out, out


def test_no_identity_directory_says_nothing_rather_than_zero(tmp_path):
	"""An absence reported as a measurement is worse than an absence reported as silence.

	"identity ~0 tok" reads as "the corpus is loaded and empty", which a reader acts on; the corpus is
	simply not installed.
	"""
	wt, cfg = _hook_repo(tmp_path)
	out = _run_hook(wt, cfg, tmp_path / "no-such-corpus")
	assert "identity" not in out, out
	assert "~0 tok" not in out, out


def test_a_corpus_check_that_will_not_stop_talking_is_capped(tmp_path):
	"""Third-party stdout lands in the session context at every start, in every repo.

	Unbounded, a check that prints 200 lines puts 200 of them there — the cost is paid by the session
	the budget line exists to protect.
	"""
	wt, cfg = _hook_repo(tmp_path)
	corpus = tmp_path / "corpus"
	(corpus / "bin").mkdir(parents=True)
	check = corpus / "bin" / "budget-check.sh"
	check.write_text("#!/usr/bin/env bash\nfor i in $(seq 200); do echo \"line $i\"; done\n")
	check.chmod(0o755)
	out = _run_hook(wt, cfg, corpus)
	assert out.count("[budget]") == 5, out


def test_retire_never_raises_on_a_config_it_cannot_write(monkeypatch, tmp_path):
	"""team.migrate() states the contract one line above this in the launch path: never raises, because
	it runs before the first draw and an exception there is a dashboard that never appears.

	retire() did the opposite — os.remove() and _write_text() propagated OSError straight out of main().
	A read-only ~/.claude, a CLAUDE.md whose realpath is in a checkout the user cannot write, a
	root-owned file: every launch dead, with no way out but removing the link by hand.
	"""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(tmp_path / "teams" / "org-t" / "memory").mkdir(parents=True)
	os.symlink(str(tmp_path / "teams" / "org-t" / "memory"), str(cfg / "prs-team"))
	(cfg / "CLAUDE.md").write_text("# mine\n\n" + install.BLOCK.replace(
		install.IMPORT, install.IMPORT + "\n@prs-team/general.md"))

	def refuse(*a, **kw):
		raise OSError(13, "Permission denied")
	monkeypatch.setattr(install.os, "remove", refuse)
	monkeypatch.setattr(install, "_write_text", refuse)

	out = install.retire()                       # the whole point: this returns rather than raising
	assert any("could not retire" in l for l in out), out
	assert any("could not update the import block" in l for l in out), out
	assert all("Permission denied" in l for l in out if l.startswith("gitdashy:")), out
	assert (cfg / "prs-team").is_symlink()       # and it says so instead of pretending it happened


def test_a_failed_retirement_is_said_not_swallowed(monkeypatch, tmp_path):
	"""Reported, not silently skipped. A migration that did not happen and said nothing is one the user
	discovers when their sessions quietly stop loading the team's facts."""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(tmp_path / "teams" / "org-t" / "memory").mkdir(parents=True)
	os.symlink(str(tmp_path / "teams" / "org-t" / "memory"), str(cfg / "prs-team"))
	monkeypatch.setattr(install.os, "remove", lambda *a, **kw: (_ for _ in ()).throw(OSError(30, "Read-only file system")))
	out = install.retire()
	assert out and "Read-only file system" in out[0] and "by hand" in out[0], out
	# ponytail: a LAUNCH shows the first non-NOTE line, so the failure has to survive that filter or the
	# one place the user would see it drops it.
	assert not out[0].startswith("NOTE")


def test_an_unclosed_marker_is_not_a_block_of_ours(monkeypatch, tmp_path):
	"""The two fence walkers disagreed about an unclosed `begin`, and retire() asked one and acted on
	the other.

	_inside_blocks treated everything after a lone marker as ours; _strip_blocks treated it as not ours
	and did nothing. So a CLAUDE.md holding an unclosed marker over an `@prs-team/` line reported
	"ours", stripped nothing, and appended BLOCK — and on the NEXT launch the strip ran from the user's
	own unclosed marker all the way to the appended END and deleted everything in between. In the file
	this whole path exists to protect.
	"""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	mine = f"# mine\n\n{install.BEGIN}\n# Review memory\n\n@prs-team/general.md\nkeep this line\n"
	(cfg / "CLAUDE.md").write_text(mine)

	assert install.STALE not in install._inside_blocks(mine)     # not ours: there is no closing marker
	install.retire()
	after = (cfg / "CLAUDE.md").read_text()
	assert after.count(install.BEGIN) == 1, after                # no second block appended
	assert "keep this line" in after                             # and nothing of theirs eaten
	install.retire()                                             # idempotent, and still no data loss
	assert (cfg / "CLAUDE.md").read_text() == after


def test_the_two_answers_come_from_one_walk_and_cannot_disagree():
	"""_split_blocks returns (inside, outside) together, so 'is this ours' and 'remove ours' are the
	same decision rather than two functions that have to be kept in step by hand."""
	text = f"# mine\n{install.BEGIN}\nheld\n{install.END}\ntail\n"
	inside, outside = install._split_blocks(text, install.BEGIN, install.END)
	assert inside == "held"
	assert install.BEGIN not in outside and "tail" in outside and "# mine" in outside
	assert install._inside_blocks(text) == inside
	assert install._strip_blocks(text, install.BEGIN, install.END) == outside
	# an unclosed one: nothing held, nothing stripped, and the two still agree
	lone = f"# mine\n{install.BEGIN}\nheld\n"
	assert install._split_blocks(lone, install.BEGIN, install.END) == ("", lone)


def test_a_launch_survives_a_claude_md_it_cannot_read(monkeypatch, tmp_path):
	"""The last round wrapped the two WRITES and left the read, which happens first.

	`_read` caught FileNotFoundError, not OSError, so an existing-but-unreadable file raised — and
	`text = _read(md)` sits above both try blocks. One `sudo claude` leaves a root-owned
	~/.claude/CLAUDE.md and retire() then tracebacks out of main() before the first draw, which is the
	exact case the docstring added last round claims is "a report line, not a traceback".
	"""
	cfg = fresh(monkeypatch, tmp_path)
	md = cfg / "CLAUDE.md"
	md.write_text("# mine\n")
	md.chmod(0o000)
	try:
		assert install.retire() == []          # returns, says nothing it cannot support, does not raise
		assert install._read(str(md)) == ""    # unreadable and absent are one answer to every caller
	finally:
		md.chmod(0o644)


def test_a_draw_survives_an_unreadable_agent_config(monkeypatch, tmp_path):
	"""session_notes() is called from row() on EVERY draw, so this is not a launch-only crash.

	corpus_remembers() reached both an unguarded os.listdir and a _read that only caught a missing
	file; either one raising takes the dashboard down every tick until someone chowns the file back.
	_notes_key() guarded its own OSError correctly, so the cache key computed and then the body raised.
	"""
	cfg = fresh(monkeypatch, tmp_path)
	monkeypatch.setattr(install, "_NOTES", (None, []))
	ident = cfg / "identity"
	ident.mkdir()
	(ident / "AGENT.md").write_text("no instruction here\n")
	ident.chmod(0o000)
	try:
		assert install.corpus_remembers() is None   # unreadable is not "a corpus without the instruction"
		assert install.session_notes() == []        # and nothing is claimed about it
	finally:
		ident.chmod(0o755)

	ident.chmod(0o755)
	(cfg / "CLAUDE.md").write_text("# mine\n")
	(cfg / "CLAUDE.md").chmod(0o000)
	monkeypatch.setattr(install, "_NOTES", (None, []))
	try:
		install.session_notes()                     # the other half: hand_wired_team_import's read
	finally:
		(cfg / "CLAUDE.md").chmod(0o644)


def test_full_install_registers_both_hooks_and_uninstall_removes_both(monkeypatch, tmp_path):
	"""The Stop hook is what makes the friction ask a mechanism rather than an instruction.

	Registered but never removed, it would fail at the end of every session forever once the checkout
	it points into is gone, with nothing naming gitdashy as the cause — so both halves are one test.
	"""
	d, corpus = full_env(monkeypatch, tmp_path)
	install.full_apply(corpus)
	settings = json.loads(open(os.path.join(d, "settings.json")).read())
	cmds = {event: [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]
	        for event in ("SessionStart", "Stop")}
	assert any(install.HOOK_MATCH in c for c in cmds["SessionStart"]), cmds
	assert any(install.STOP_MATCH in c for c in cmds["Stop"]), cmds
	# ponytail: the Stop hook takes NO argument — everything it judges arrives on stdin. The
	# SessionStart one is passed the corpus home, and passing it to both would be a silent no-op today
	# and a wrong path the day the stop hook reads argv.
	stop = next(c for c in cmds["Stop"] if install.STOP_MATCH in c)
	assert stop.strip() == shlex.quote(install.STOP_HOOK)

	install.full_apply(corpus)                                   # idempotent: no second copy of either
	settings = json.loads(open(os.path.join(d, "settings.json")).read())
	assert install._count(settings, "Stop") == 1
	assert install._count(settings, "SessionStart") == 1

	install.full_remove()
	settings = json.loads(open(os.path.join(d, "settings.json")).read())
	assert not settings.get("hooks", {}).get("Stop"), settings
	assert not settings.get("hooks", {}).get("SessionStart"), settings


def test_uninstall_leaves_somebody_elses_stop_hook_alone(monkeypatch, tmp_path):
	"""Same rule the SessionStart match already follows: enough path to be ours, so a hook of another
	tool's that happens to run on Stop is not swept away with ours."""
	d, corpus = full_env(monkeypatch, tmp_path)
	sp = os.path.join(d, "settings.json")
	theirs = {"type": "command", "command": "/opt/other/claude-stop.sh"}
	open(sp, "w").write(json.dumps({"hooks": {"Stop": [{"hooks": [theirs]}]}}))
	install.full_apply(corpus)
	install.full_remove()
	settings = json.loads(open(sp).read())
	left = [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]]
	assert left == ["/opt/other/claude-stop.sh"], settings


def test_the_consent_screen_names_the_stop_hook_and_what_it_can_do(monkeypatch, tmp_path):
	"""#31's blocking finding, one door along: a hook that gains a new kind of power and a consent
	screen that still describes the old one. A Stop hook can HOLD A SESSION OPEN, which is done to the
	user rather than for them, so the screen has to say that before they agree to it."""
	_, corpus = full_env(monkeypatch, tmp_path)
	out = "\n".join(install.full_explain(corpus))
	assert "Stop hook" in out, out
	assert "hold a session open" in out, out
	assert "never twice" in out, out
	assert "sends nothing anywhere" in out, out


def test_one_missing_hook_script_does_not_cost_you_the_other(monkeypatch, tmp_path):
	"""The per-hook SKIP claims exactly this, and nothing proved it.

	Registration walks HOOK_TABLE; a script that is present but not executable must skip its own row
	and leave the other registered, rather than aborting the loop or writing a hook that cannot run.
	"""
	d, corpus = full_env(monkeypatch, tmp_path)
	dead = tmp_path / "not-executable.sh"
	dead.write_text("#!/usr/bin/env bash\nexit 0\n")
	dead.chmod(0o644)
	monkeypatch.setattr(install, "HOOK_TABLE",
	                    (install.HOOK_TABLE[0],
	                     ("Stop", str(dead), install.STOP_MATCH, "x", False)))
	out = install.full_apply(corpus)
	assert any("SKIP" in l and "no Stop hook" in l for l in out), out
	settings = json.loads(open(os.path.join(d, "settings.json")).read())
	assert install._count(settings, "SessionStart") == 1        # the other one still landed
	assert not settings.get("hooks", {}).get("Stop")


def test_the_install_doc_names_every_hook_that_is_actually_registered(monkeypatch, tmp_path):
	"""docs/install.md is what README calls "the full account — every file it writes".

	It said install registers "one SessionStart hook" while HOOK_TABLE had grown to two, and it
	understated it precisely for the hook that can hold a session open. Prose nobody checks is the
	thing that goes stale, so the check is here rather than in someone's memory: the doc has to name
	every event the code actually registers.
	"""
	doc = open(os.path.join(install.HERE, "docs", "install.md")).read()
	for event, *_ in install.HOOK_TABLE:
		assert f"`{event}`" in doc, f"docs/install.md never names the {event} hook"
	# and the one consequence a reader must not have to discover at runtime
	assert "hold a session open" in doc, doc[:0] or "docs/install.md does not say the Stop hook can block"
