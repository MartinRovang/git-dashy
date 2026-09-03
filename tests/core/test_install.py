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
	assert install.registered() == [(str(tmp_path / "a"), "o/n")]
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
	assert install.registered() == [(str(repo / ".agent" / "team"), "o/n")]
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
	assert os.path.exists(seeded) and "Fill this in" in open(seeded).read()
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
	for word in ("neomedsys", "nils", "pontus", "phi", "medquery", "neo-api", "nms-platform"):
		assert word not in blob, f"the shipped corpus mentions {word!r}"
	assert install.corpus_files(corpus)  # and it actually has identity files to import
