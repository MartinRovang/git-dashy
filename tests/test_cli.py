import os

import pytest

from dashy import cli, config
from dashy.core import install as install_mod, memory, team


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


def test_a_flag_with_no_value_is_a_message_not_a_traceback():
	with pytest.raises(SystemExit, match="--repo needs a value"):
		cli.run(["gitdashy", "remember", "a fact", "--repo"])

def test_install_asks_before_writing_and_a_no_changes_nothing(monkeypatch, tmp_path, capsys):
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mem"))
	monkeypatch.setattr("sys.stdin.isatty", lambda: True)
	monkeypatch.setattr("builtins.input", lambda _: "n")
	cli.run(["gitdashy", "install"])
	assert "nothing changed" in capsys.readouterr().out
	assert not (cfg / "prs-memory").exists() and not (cfg / "CLAUDE.md").exists()
	monkeypatch.setattr("builtins.input", lambda _: "y")
	cli.run(["gitdashy", "install"])
	capsys.readouterr()
	assert (cfg / "prs-memory").is_symlink() and "@prs-memory" in (cfg / "CLAUDE.md").read_text()


def test_install_refuses_unattended_without_yes(monkeypatch, tmp_path):
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mem"))
	monkeypatch.setattr("sys.stdin.isatty", lambda: False)
	with pytest.raises(SystemExit, match="not a terminal"):
		cli.run(["gitdashy", "install"])
	assert not (cfg / "prs-memory").exists()
	cli.run(["gitdashy", "install", "--yes"])  # explicit, so it proceeds
	assert (cfg / "prs-memory").is_symlink()


def test_install_dry_run_explains_and_writes_nothing(monkeypatch, tmp_path, capsys):
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(tmp_path / "mem"))
	monkeypatch.setattr("builtins.input", lambda _: pytest.fail("--dry-run must not ask"))
	cli.run(["gitdashy", "install", "--dry-run"])
	assert "nothing was changed" in capsys.readouterr().out
	assert not (cfg / "prs-memory").exists()


def _offer_env(monkeypatch, tmp_path):
	"""A machine where install --full would land, with the offer reachable."""
	cfg = tmp_path / "claude"
	cfg.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	mem = tmp_path / "mem"
	mem.mkdir()
	monkeypatch.setattr(config, "LOCAL_MEMORY", str(mem))
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.setattr(install_mod, "CORPUS_HOME", str(tmp_path / "corpus-home"))
	monkeypatch.setattr("sys.stdin.isatty", lambda: True)
	called = []
	monkeypatch.setattr(cli, "setup", lambda argv: called.append(argv))
	return called


def test_declining_the_briefs_is_not_a_failed_install(monkeypatch, tmp_path, capsys):
	"""cli.setup's own `ask` raises SystemExit on Ctrl-C, and SystemExit is a BaseException.

	It walked past the handler, so a COMPLETED install that printed its report still exited
	non-zero — a wrapper checking $? read a finished install as a failed one.
	"""
	_offer_env(monkeypatch, tmp_path)
	monkeypatch.setattr(cli, "setup", lambda argv: (_ for _ in ()).throw(SystemExit("\nnothing written")))
	monkeypatch.setattr("builtins.input", lambda _: "y")
	cli.offer_setup(["gitdashy", "install", "--full"])       # must not raise
	assert "nothing written" in capsys.readouterr().out


def test_yes_skips_the_offer_because_it_means_do_not_ask_me(monkeypatch, tmp_path):
	"""This command tells you to pass --yes for unattended installs, then used to block anyway.

	isatty alone does not cover it: a bootstrap script run from an interactive shell inherits the tty.
	"""
	called = _offer_env(monkeypatch, tmp_path)
	monkeypatch.setattr("builtins.input", lambda _: pytest.fail("--yes must not prompt"))
	cli.offer_setup(["gitdashy", "install", "--full", "--yes"])
	assert called == []


def test_no_setup_and_a_non_tty_both_skip_the_offer(monkeypatch, tmp_path):
	called = _offer_env(monkeypatch, tmp_path)
	monkeypatch.setattr("builtins.input", lambda _: pytest.fail("must not prompt"))
	cli.offer_setup(["gitdashy", "install", "--full", "--no-setup"])
	monkeypatch.setattr("sys.stdin.isatty", lambda: False)
	cli.offer_setup(["gitdashy", "install", "--full"])
	assert called == []


def test_saying_yes_reaches_setup_and_no_does_not(monkeypatch, tmp_path, capsys):
	called = _offer_env(monkeypatch, tmp_path)
	monkeypatch.setattr("builtins.input", lambda _: "n")
	cli.offer_setup(["gitdashy", "install", "--full"])
	assert called == [] and "whenever you want them" in capsys.readouterr().out
	monkeypatch.setattr("builtins.input", lambda _: "")   # blank is yes
	cli.offer_setup(["gitdashy", "install", "--full"])
	assert len(called) == 1


def test_a_failed_full_install_never_offers_the_briefs(monkeypatch, tmp_path):
	"""The guard depends on install.fail() emitting a line prefixed FAIL — nothing else holds that."""
	_offer_env(monkeypatch, tmp_path)
	offered = []
	monkeypatch.setattr(cli, "offer_setup", lambda argv: offered.append(argv))
	monkeypatch.setattr(install_mod, "full_apply", lambda *a, **k: ["ok    something", "FAIL  broken"])
	monkeypatch.setattr(install_mod, "full_explain", lambda *a, **k: ["…"])
	cli.install(["gitdashy", "install", "--full", "--yes"])
	assert offered == []
	monkeypatch.setattr(install_mod, "full_apply", lambda *a, **k: ["ok    something"])
	cli.install(["gitdashy", "install", "--full", "--yes"])
	assert len(offered) == 1


def test_voice_and_hunter_flags_are_checked(monkeypatch, capsys):
	monkeypatch.setattr(config, "SETTINGS", "")
	monkeypatch.setattr(config, "VOICE", ["review"])
	cli.run(["gitdashy", "--voice", "ponytail"])
	assert "--voice must be from review, caveman, bot" in capsys.readouterr().out
	monkeypatch.setattr(config, "VOICE", ["review"])  # the refused value is not undone; a real run exits here
	cli.run(["gitdashy", "--hunter", "tests,nope"])
	assert "--hunter must be from ponytail, security, tests" in capsys.readouterr().out


def test_bind_refuses_a_positional_that_is_not_a_slug(monkeypatch, tmp_path):
	"""It used to fall through to this directory's origin and bind the wrong repo, reporting success."""
	from dashy import cli
	from dashy.core import bind, team
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/api")
	monkeypatch.setattr(bind, "team_key", lambda: "org-mem")
	with pytest.raises(SystemExit) as e:
		cli.bind(["gitdashy", "bind", "neo-api", "--team", "org-mem"])
	assert "not owner/name" in str(e.value)
	assert bind.bindings() == {}  # and nothing was bound in its place


def test_bind_list_answers_even_when_the_positional_is_a_typo(monkeypatch, capsys):
	"""--list is a read-only question; gating it behind a check on the thing you asked about turned it
	into a SystemExit."""
	from dashy import cli
	from dashy.core import bind, team
	monkeypatch.setattr(team, "activate", lambda: None)
	bind.bind("acme/api", "org-mem")
	cli.bind(["gitdashy", "bind", "not-a-slug", "--list"])
	assert "acme/api" in capsys.readouterr().out


def test_drafts_prints_a_heading_for_every_group_including_general(monkeypatch, capsys, tmp_path):
	"""`where = None` collided with the repo of the general file, which is also None — and general
	sorts first, so the one group that could hit it always did: its rows printed under no heading."""
	from dashy import cli
	from dashy.core import memory, team
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	memory.append(None, "a general guess")
	memory.append("a/b", "a repo guess")
	cli.drafts(["gitdashy", "drafts"])
	out = capsys.readouterr().out
	assert "general" in out and "a/b" in out
	assert out.index("general") < out.index("a general guess")   # the heading is ABOVE its rows
	assert out.index("a/b") < out.index("a repo guess")


def test_teams_lists_what_each_one_covers(monkeypatch, capsys, tmp_path):
	from dashy import cli
	from dashy.core import bind, team
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for slug in ("org-one", "org-two"):
		(tmp_path / "teams" / slug / ".git").mkdir(parents=True)
	bind.bind_owner("neomedsys", "org-one")
	bind.bind("acme/tool", "org-two")
	cli.teams(["gitdashy", "teams"])
	out = capsys.readouterr().out
	assert "org-one" in out and "neomedsys/*" in out
	assert "org-two" in out and "acme/tool" in out
	assert out.index("org-one") < out.index("org-two")      # each team's coverage under its own row
	assert out.index("neomedsys/*") < out.index("org-two")


def test_teams_join_names_the_team_it_just_joined(monkeypatch, capsys, tmp_path):
	"""joined()[-1] is the last ALPHABETICALLY, so already being in "zulu" and joining "acme" printed
	"joined zulu"."""
	from dashy import cli
	from dashy.core import team
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(team, "ERROR", "")
	for key in ("zulu",):
		(tmp_path / "teams" / key / ".git").mkdir(parents=True)
	def fake_setup(repo, name=""):
		(tmp_path / "teams" / "acme" / ".git").mkdir(parents=True)
		return ""
	monkeypatch.setattr(team, "setup", fake_setup)
	cli.teams(["gitdashy", "teams", "--join", "somewhere/acme.git"])
	assert "joined acme" in capsys.readouterr().out


def test_drafts_count_is_one_line_for_the_repo_you_stand_in_and_silent_when_empty(monkeypatch, capsys, tmp_path):
	"""For a session hook: the pull toward W that was missing. Reads the local store only."""
	from dashy import cli
	from dashy.core import memory, team
	# ponytail: team.activate() runs for real here. Stubbing it out was stubbing out the very seam the
	# "local store only" claim rests on — the test then proved nothing about the network, only that a
	# no-op does nothing. What must hold is that team.pull() is never reached, so that is asserted.
	monkeypatch.setattr(team, "pull", lambda: (_ for _ in ()).throw(AssertionError("drafts --count must not pull")))
	monkeypatch.setattr(team, "pull_dir", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("drafts --count must not pull")))
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	cli.drafts(["gitdashy", "drafts", "--count"])
	assert capsys.readouterr().out == ""                       # nothing to say, nothing said
	memory.append("acme/web", "one guess")
	memory.append("acme/web", "another guess")
	memory.append("acme/other", "not this repo")
	cli.drafts(["gitdashy", "drafts", "--count"])
	out = capsys.readouterr().out
	assert out.startswith("gitdashy: 2 drafts waiting for acme/web") and "W" in out
	cli.drafts(["gitdashy", "drafts", "--count", "--repo", "acme/other"])
	assert "1 draft waiting for acme/other" in capsys.readouterr().out


def test_drafts_count_says_nothing_for_a_repo_it_cannot_name(monkeypatch, capsys, tmp_path):
	"""The hook only requires a git repo, not an origin. A local-only repo was told every draft on the
	machine was waiting for it, under the label "general"."""
	from dashy import cli
	from dashy.core import memory, team
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(team, "origin_slug", lambda p: "")
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	memory.append("acme/web", "a guess")
	memory.append(None, "a general guess")
	cli.drafts(["gitdashy", "drafts", "--count"])
	assert capsys.readouterr().out == ""
