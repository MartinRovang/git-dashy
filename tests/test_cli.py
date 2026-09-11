import json
import logging
import os
import sys
import threading

import pytest

from dashy import cli, config
from dashy.core import install as install_mod, memory, state, team

from conftest import counts


def facts(p):
	return [l.strip() for l in open(p).read().splitlines() if l.strip()]


def test_remember_drafts_then_confirms_on_a_second_observation(monkeypatch, tmp_path, capsys):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	cli.run(["gitdashy", "remember", "the", "viewer", "owns", "mask", "state"])
	assert "drafted" in capsys.readouterr().out
	assert counts(memory.drafts("acme/web")) == [(1, "the viewer owns mask state")]
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
	assert counts(memory.drafts(None)) == [(1, "PHI reaches the frontend")]
	cli.run(["gitdashy", "remember", "--repo", "other/thing", "migrations run first"])
	assert counts(memory.drafts("other/thing")) == [(1, "migrations run first")]
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


def test_version_and_help_do_not_start_the_dashboard(capsys):
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
	monkeypatch.setattr(cli, "setup", lambda argv, project=True: called.append((argv, project)))
	return called


def test_declining_the_briefs_is_not_a_failed_install(monkeypatch, tmp_path, capsys):
	"""cli.setup's own `ask` raises SystemExit on Ctrl-C, and SystemExit is a BaseException.

	It walked past the handler, so a COMPLETED install that printed its report still exited
	non-zero — a wrapper checking $? read a finished install as a failed one.
	"""
	_offer_env(monkeypatch, tmp_path)
	monkeypatch.setattr(cli, "setup",
	                    lambda argv, project=True: (_ for _ in ()).throw(SystemExit("\nnothing written")))
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
	assert called == [] and "whenever you want to" in capsys.readouterr().out
	monkeypatch.setattr("builtins.input", lambda _: "")   # blank is yes
	cli.offer_setup(["gitdashy", "install", "--full"])
	assert len(called) == 1
	# ponytail: project=False is the whole point of the offer now — who you are is machine-level, what
	# the work is for is not, and asking it here wrote one brief that every unbound repo then read.
	assert called[0][1] is False


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
	assert "--hunter must be from ponytail, security, tests, humanizer" in capsys.readouterr().out


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


def test_api_prints_a_file_decoded_and_json_as_json(monkeypatch, capsys):
	"""The reviewer's one command. A file arrives base64 in an envelope; a model must not have to unwrap it."""
	import base64
	from dashy import cli
	from dashy.core import github
	monkeypatch.setattr(github, "call", lambda path, **kw: json.dumps(
		{"encoding": "base64", "content": base64.b64encode(b"def f():\n\tpass\n").decode()}
		if "contents" in path else {"number": 7}))
	cli.api(["gitdashy", "api", "/repos/a/b/contents/x.py?ref=feat"])
	assert capsys.readouterr().out == "def f():\n\tpass\n\n"
	cli.api(["gitdashy", "api", "repos/a/b/pulls/7"])  # a path with no leading slash still works
	assert json.loads(capsys.readouterr().out) == {"number": 7}


def test_api_says_what_went_wrong_instead_of_a_traceback(monkeypatch, capsys):
	from dashy import cli
	from dashy.core import github
	monkeypatch.setattr(github, "call", lambda path, **kw: (_ for _ in ()).throw(github.Error("404 x: Not Found")))
	with pytest.raises(SystemExit, match="Not Found"):
		cli.api(["gitdashy", "api", "/repos/a/b/pulls/9"])
	with pytest.raises(SystemExit, match="needs a path"):
		cli.api(["gitdashy", "api"])


def test_no_token_says_so_instead_of_opening_the_dashboard(monkeypatch, capsys):
	"""Every call needs one, so without it the dashboard is three rows of 401 under curses."""
	monkeypatch.setattr(cli.web, "main", lambda *a, **kw: pytest.fail("opened the dashboard"))
	monkeypatch.setattr(cli.web, "desktop_binary", lambda: "")
	monkeypatch.setattr(cli.web, "download_desktop", lambda: "")
	monkeypatch.setattr(config, "SETTINGS", "")
	cli.run(["gitdashy"])
	out = capsys.readouterr().out
	assert "no GitHub token" in out and "export GH_TOKEN" in out and "--demo" in out
	monkeypatch.setenv("GH_TOKEN", "gho_x")
	opened = []
	monkeypatch.setattr(cli.web, "main", lambda *a, **kw: opened.append(a))
	monkeypatch.setattr(cli.web, "desktop_binary", lambda: "")
	monkeypatch.setattr(cli.web, "download_desktop", lambda: "")
	cli.run(["gitdashy"])
	assert opened  # with one, it starts


def test_an_unknown_command_is_an_error_not_the_dashboard(monkeypatch):
	"""`gitdashy api …` against a build with no api command fell through into curses.wrapper, so a review
	whose first tool call hit an older install crashed instead of being told the command was not there."""
	from dashy import cli
	monkeypatch.setattr(cli.web, "main", lambda *a, **kw: pytest.fail("opened the dashboard"))
	monkeypatch.setattr(cli.web, "desktop_binary", lambda: "")
	monkeypatch.setattr(cli.web, "download_desktop", lambda: "")
	with pytest.raises(SystemExit, match="no command 'bogus'"):
		cli.run(["gitdashy", "bogus"])
	with pytest.raises(SystemExit, match="no command 'pr'"):  # a command from some other build, or a typo
		cli.run(["gitdashy", "pr", "view", "7"])


def test_the_reviewers_command_points_at_the_running_code(monkeypatch, tmp_path):
	"""A stale `gitdashy` on PATH is a different program: the prompt said `api` while the binary was a
	build that had none. The bare name is used only when PATH resolves to this very checkout."""
	from dashy.core import review
	monkeypatch.setattr(review.shutil, "which", lambda c: str(tmp_path / "old-install" / "prs.py"))
	assert review.api_cmd().endswith("prs.py") and "gitdashy" not in review.api_cmd().split("/")[-1]
	monkeypatch.setattr(review.shutil, "which", lambda c: os.path.join(review.HERE, "prs.py"))
	assert review.api_cmd() == "gitdashy"


def test_api_asks_for_a_diff_when_told_to(monkeypatch, capsys):
	"""A patch inside json is readable but escaped; --diff is the same GET with one header changed."""
	from dashy import cli
	from dashy.core import github
	seen = []
	monkeypatch.setattr(github, "call", lambda path, **kw: seen.append(kw["accept"]) or "diff --git a b")
	cli.api(["gitdashy", "api", "/repos/a/b/compare/x...y", "--diff"])
	assert seen == ["application/vnd.github.v3.diff"] and capsys.readouterr().out == "diff --git a b\n"
	cli.api(["gitdashy", "api", "/repos/a/b/pulls/7"])
	assert seen[-1] == "application/vnd.github+json"


def test_api_refuses_a_url(monkeypatch):
	"""The caller is a model that has just read an untrusted diff. A diff that talks it into pointing
	this at another host must not get a request out of it, token or no token."""
	from dashy import cli
	from dashy.core import github
	monkeypatch.setattr(github, "call", lambda path, **kw: pytest.fail(f"called out to {path}"))
	for url in ("https://evil.example.com/collect?t=", "http://169.254.169.254/latest/meta-data/", "//evil.example.com/x"):
		with pytest.raises(SystemExit, match="not a URL"):
			cli.api(["gitdashy", "api", url])
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


def test_bind_refuses_a_team_this_machine_has_not_joined(monkeypatch, tmp_path):
	"""A typo in --team bound the org to nothing and said it had worked."""
	from dashy.core import bind
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(tmp_path / "teams" / "neomedsys-team" / ".git").mkdir(parents=True)
	with pytest.raises(SystemExit) as e:
		cli.bind(["gitdashy", "bind", "--owner", "neomedsys", "--team", "neomedsys-tean"])
	assert "not in team" in str(e.value) and "neomedsys-team" in str(e.value)
	assert bind.owners() == {}
	cli.bind(["gitdashy", "bind", "--owner", "neomedsys", "--team", "NeoMedSys_Team"])  # a spelling of one we ARE in
	assert bind.owners() == {"neomedsys": "neomedsys-team"}


def bind_owners_now():
	from dashy.core import bind
	return dict(bind.owners())


def test_teams_cover_declares_in_the_team_and_the_listing_says_so(monkeypatch, capsys, tmp_path):
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	assert team.start("Platform") == ""
	cli.teams(["gitdashy", "teams", "--team", "platform", "--cover", "neomedsys"])
	out = capsys.readouterr().out
	assert "platform now covers neomedsys/*" in out
	assert team.covers("platform") == ["neomedsys/*"]
	assert "declares: neomedsys/*" in out          # the listing after it
	before = bind_owners_now()
	with pytest.raises(SystemExit) as e:
		cli.teams(["gitdashy", "teams", "--team", "nope", "--cover", "acme"])
	assert "not in team" in str(e.value) and "nope" in str(e.value)
	# ponytail: and the STORE is untouched. The check used to run after bind_owner had already appended
	# {"owner": "acme", "team": "nope"}, so a typo left a live rule pointing at a team nobody holds —
	# the silently-wrong-team bug the bind resolver exists to kill, one verb over, plus a dirty store.
	from dashy.core import bind
	assert before == bind.owners() and "acme" not in bind.owners()
	cli.teams(["gitdashy", "teams", "--team", "platform", "--uncover", "neomedsys/*"])
	assert "no longer covers neomedsys/*" in capsys.readouterr().out
	assert team.covers("platform") == []


def test_debug_writes_log_file(monkeypatch, tmp_path, capsys):
	"""--debug: a swallowed exception lands in the file with its traceback; a crash lands there AND on stderr."""
	path = tmp_path / "dbg.log"
	monkeypatch.setattr(config, "DEBUG_LOG", str(path))
	monkeypatch.setattr(logging.root, "handlers", [])  # basicConfig is a no-op once a handler exists
	monkeypatch.setattr(logging.root, "level", logging.root.level)
	monkeypatch.setattr(sys, "excepthook", sys.excepthook)
	monkeypatch.setattr(threading, "excepthook", threading.excepthook)
	cli.debug(["gitdashy"])
	assert oct(path.stat().st_mode)[-3:] == "600"
	try:
		raise ValueError("boom")
	except ValueError:
		state.LOG.exception("tick failed")
		sys.excepthook(*sys.exc_info())
	text = path.read_text()
	assert "starting" in text and "tick failed" in text and "uncaught" in text and text.count("ValueError: boom") == 2
	assert "ValueError: boom" in capsys.readouterr().err
	for h in logging.root.handlers:
		h.close()


def test_logger_silent_without_debug(monkeypatch, capsys):
	"""The NullHandler on `dashy` keeps logging.lastResort off the curses screen."""
	monkeypatch.setattr(logging.root, "handlers", [])
	state.LOG.error("tick failed")
	assert capsys.readouterr().err == ""


def test_drafts_prints_the_fact_without_its_provenance(monkeypatch, capsys, tmp_path):
	"""The ids are how the store reasons about a count; they are not part of the sentence a person reads."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(team, "activate", lambda: None)
	memory.append("acme/web", "- the viewer owns mask state")
	cli.drafts(["gitdashy", "drafts"])
	out = capsys.readouterr().out
	assert "the viewer owns mask state" in out
	assert "[r:" not in out and "r:" not in out.split("waiting")[0]   # no provenance in the sentence


def test_api_is_confined_to_the_repo_under_review(monkeypatch):
	"""The scope arrives in the ENVIRONMENT, so there is nothing in the argv a prompt can rewrite."""
	from dashy import cli
	from dashy.core import github
	monkeypatch.setenv(github.SCOPE, "acme/api")
	monkeypatch.setattr(github, "call", lambda path, **kw: pytest.fail(f"called out to {path}"))
	for path in ("/repos/some-other-org/private-repo/contents/.env",
	             "/search/code?q=AWS_SECRET+user:victim",
	             "/user/repos?per_page=100"):
		with pytest.raises(SystemExit, match="acme/api"):
			cli.api(["gitdashy", "api", path])


def test_api_unscoped_still_reads_anything(monkeypatch, capsys):
	"""ponytail: no scope means a person typed it. Refusing there protects nobody and teaches a workaround."""
	from dashy import cli
	from dashy.core import github
	monkeypatch.delenv(github.SCOPE, raising=False)
	monkeypatch.setattr(github, "call", lambda path, **kw: json.dumps({"path": path}))
	cli.api(["gitdashy", "api", "/user/repos"])
	assert json.loads(capsys.readouterr().out) == {"path": "/user/repos"}


def test_api_reaches_a_sibling_when_the_team_env_is_set(monkeypatch, tmp_path):
	"""ponytail: that SCOPE_TEAM travels from the environment into github.scoped was unproven — dropping
	the third argument in cli.api failed no test, while the PR claimed every layer was mutation-checked.
	The parent setting it and the policy honouring it were both covered; the wire between them was not."""
	from dashy import cli
	from dashy.core import bind, github
	b = tmp_path / "bindings"
	b.write_text(json.dumps({"repo": "acme/shared-lib", "team": "acme-platform"}) + "\n")
	monkeypatch.setattr(bind, "BINDINGS", str(b))
	monkeypatch.setenv(github.SCOPE, "acme/api")
	monkeypatch.setenv(github.SCOPE_TEAM, "acme-platform")
	got = []
	monkeypatch.setattr(github, "call", lambda path, **kw: got.append(path) or json.dumps({"ok": 1}))
	cli.api(["gitdashy", "api", "/repos/acme/shared-lib/contents/x.py"])
	assert got == ["/repos/acme/shared-lib/contents/x.py"]
	monkeypatch.delenv(github.SCOPE_TEAM)          # the same read, without the team
	with pytest.raises(SystemExit, match="acme/api"):
		cli.api(["gitdashy", "api", "/repos/acme/shared-lib/contents/x.py"])


def test_remember_general_keeps_the_project_it_was_observed_in(monkeypatch, capsys, tmp_path):
	"""--general threw away the one thing that says which project the fact is about: the repo you are
	standing in. So with two teams joined it had nowhere to go and could never be shared."""
	from dashy.core import bind
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	monkeypatch.setattr(team, "activate", lambda: None)
	for key in ("nms", "dashy"):
		(tmp_path / "teams" / key / ".git").mkdir(parents=True)
		(tmp_path / "teams" / key / "memory").mkdir()
	bind.bind_owner("neomedsys", "nms")
	bind.bind("martin/git-dashy", "dashy")
	# ponytail: a fixture that joins a team is one whose operator said yes to publishing
	memory.allow_publishing("nms")
	memory.allow_publishing("dashy")
	monkeypatch.setattr(team, "origin_slug", lambda p: "neomedsys/neo-api")
	monkeypatch.setattr(team, "push_dir", lambda d, m, l="sync": "")
	monkeypatch.setattr(team, "push", lambda m: "")
	cli.remember(["gitdashy", "remember", "--general", "releases go out through neogate"])
	pooled = tmp_path / "teams" / "nms" / "memory" / memory.DRAFT_POOL / "tester" / "general.md"
	assert pooled.exists(), "a general observation pooled to no project"
	assert not (tmp_path / "teams" / "dashy" / "memory" / memory.DRAFT_POOL).exists()
