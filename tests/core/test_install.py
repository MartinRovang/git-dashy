import os
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
	assert "@prs-team/general.md" in (cfg / "CLAUDE.md").read_text()
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
	out = install.wire_repo(str(repo / ".agent" / "team"), str(loader), "o/n")
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
	assert out[-1].startswith("FAIL")
	assert not (cfg / "identity").exists() and not (cfg / "CLAUDE.md").exists()


def test_refresh_skips_a_mirror_whose_directory_is_gone(monkeypatch, tmp_path):
	"""No recorded root needed: init creates the mirror, so a refresh never has cause to make one."""
	fresh(monkeypatch, tmp_path)
	gone = tmp_path / "deleted" / "deep" / "mirror"
	install.register(str(gone), "o/n")  # an old entry, with no root recorded
	state.refresh_mirrors()
	assert not gone.exists() and install.registered() == []
