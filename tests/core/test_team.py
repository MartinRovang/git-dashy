import os
import pathlib
import subprocess

from dashy import config
from dashy.core import bind, knowledge, log, memory, team


def git(*a, cwd):
	return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_setup_seeds_and_pushes_then_pull_sees_teammate(monkeypatch, tmp_path):
	remote = tmp_path / "remote.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	mem = tmp_path / "mem"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	(mem / "a__b.md").write_text("- tabs\n")
	open(log.LOG, "w").write('{"x":1}\n')
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	assert team.setup(str(remote)) == ""
	# ponytail: the log lives IN the team now and is merged on read, so there is no single log.LOG to
	# point at it. team.log_of(slug) is the answer, and yours stays yours for unbound repos.
	assert team.on() and team.log_of(team.joined()[0]) == os.path.join(team.dirs()[0], "reviewed.jsonl")
	assert config.MEMORY_DIR == str(mem) and open(memory.path("a/b")).read() == "- tabs\n"  # memory stays yours
	# joining must not publish every private fact you have ever collected, unreviewed, in one action
	assert not os.path.exists(memory.path("a/b", os.path.join(team.dirs()[0], "memory")))

	# a teammate appends to the same files; our pull picks it up, our push merges without conflict
	git("clone", "-q", str(remote), str(tmp_path / "mate"), cwd=tmp_path)
	assert "merge=union" in open(tmp_path / "mate" / ".gitattributes").read()
	open(tmp_path / "mate" / "reviewed.jsonl", "a").write('{"x":2}\n')
	git("add", "-A", cwd=tmp_path / "mate")
	git("commit", "-qm", "mate", cwd=tmp_path / "mate")
	git("push", "-q", cwd=tmp_path / "mate")
	# ponytail: the TEAM's log, not yours. Reviews of a bound repo are logged inside the team checkout
	# and merged on read; yours holds only the unbound ones, so appending here proved nothing about the
	# union driver it is testing.
	theirs = team.log_of(team.joined()[0])
	open(theirs, "a").write('{"x":3}\n')
	team.push("mine")
	team.pull()
	# ponytail: {"x":1} is gone — joining no longer copies your personal log into the team's repo, so
	# the only entries here are the ones written FOR this team. The union driver is still what is
	# under test: two people appending to the same file, merged without a conflict.
	assert team.ERROR == "" and open(theirs).read() == '{"x":2}\n{"x":3}\n'


def test_joining_binds_the_repos_the_log_already_names(monkeypatch, tmp_path):
	"""Joining binds the repos the TEAM's shared log already names, so nothing you had stops working.

	ponytail: the team's log, not yours. It used to be yours, copied in on join — which also published
	every private repo you had reviewed. Seeding hangs off activate(), which runs after the clone, so
	the entries have to be in the repo being cloned.
	"""
	remote = tmp_path / "remote.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(mem := tmp_path / "mem").mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	_seed_remote_log(tmp_path, remote, '{"pr":{"repository":{"nameWithOwner":"acme/api"}}}\n')
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	assert team.setup(str(remote)) == ""
	assert bind.of("acme/api") == team.joined()[0]  # bound to the team we just joined
	# and the team's brief is what a review of it now reads — the whole point of the binding
	(pathlib.Path(team.dirs()[0], "memory", "project.md")).write_text("What we build together.\n")
	assert memory.brief("acme/api") == ("What we build together.", f"team {team.joined()[0]}")


def test_off_is_a_noop(tmp_path, monkeypatch):
	monkeypatch.setattr(team, "ERROR", "")
	team.pull(); team.push("x")
	assert not team.on() and team.ERROR == ""


def test_setup_accepts_a_url_and_never_waits_on_a_prompt(monkeypatch, tmp_path):
	"""A https/ssh URL clones with git, not gh — and a remote that asks for a password must fail, not hang."""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	seen = {}
	def fake_run(cmd, **kw):
		seen["cmd"], seen["env"], seen["timeout"] = cmd, kw.get("env", {}), kw.get("timeout")
		raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
	monkeypatch.setattr(subprocess, "run", fake_run)
	err = team.setup("https://github.com/org/review-team.git")
	assert seen["cmd"][:2] == ["git", "clone"]  # a URL is not an owner/name, so gh is not involved
	assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and "BatchMode=yes" in seen["env"]["GIT_SSH_COMMAND"]
	assert seen["timeout"] == team.CLONE and err.startswith("join: timed out after")  # reason first, and labelled as a join, not a sync
	assert not team.on()


def test_owner_name_clones_over_https_with_the_api_token(monkeypatch, tmp_path):
	"""No gh to clone with: owner/name becomes a URL, and a private team repo needs the token on it."""
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "me"))
	monkeypatch.setenv("GH_TOKEN", "gho_x")
	seen = {}
	monkeypatch.setattr(subprocess, "run",
	                    lambda cmd, **kw: seen.update(cmd=cmd, env=kw.get("env")) or subprocess.CompletedProcess(cmd, 1, "", "nope"))
	team.setup("org/review-team")
	assert seen["cmd"][0] == "git" and "clone" in seen["cmd"]
	assert seen["cmd"][-2] == "https://github.com/org/review-team.git"
	# the token rides in the ENV, never in argv — argv is world-readable in `ps` for the whole clone
	assert "gho_x" not in " ".join(seen["cmd"])
	assert seen["env"]["GIT_CONFIG_VALUE_0"] == "Authorization: Basic eC1hY2Nlc3MtdG9rZW46Z2hvX3g="
	assert seen["env"]["GIT_CONFIG_KEY_0"] == "http.extraHeader" and seen["env"]["GIT_CONFIG_COUNT"] == "1"


def test_setup_keeps_a_users_own_ssh_command(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
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
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(tmp_path / "teams" / "o__r" / ".git").mkdir(parents=True)
	# ponytail: an origin, because pull now skips a checkout that has none — local-only history has
	# nothing to pull and no error worth showing. The invariant under test is unchanged.
	(tmp_path / "teams" / "o__r" / ".git" / "config").write_text('[remote "origin"]\n\turl = git@github.com:o/r.git\n')
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
	assert not team.same_remote("git@gitlab.com:org-mem.git", "https://github.com/org/mem")
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


def test_joining_also_binds_the_repos_only_the_mirror_registry_knows(monkeypatch, tmp_path):
	"""The route that is not reviewing. A repo wired with `gitdashy init` and never reviewed was left
	unbound, so its mirror — which never outlives its source — deleted the team files already in it."""
	from dashy.core import install
	remote = tmp_path / "remote.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(mem := tmp_path / "mem").mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(install, "REGISTRY", str(tmp_path / "mirrors"))
	install.register(str(tmp_path / "wired"), "acme/only-wired")   # init'd, never reviewed
	install.register(str(tmp_path / "mine"), "me/weekend-thing")   # init'd, and none of the team's business
	_seed_remote_log(tmp_path, remote, '{"pr":{"repository":{"nameWithOwner":"acme/reviewed"}}}\n')
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	assert team.setup(str(remote)) == ""
	# the team holds facts for one of the wired repos; the mirror would strip them if it stayed unbound
	shared = pathlib.Path(team.dirs()[0], "memory")
	shared.mkdir(parents=True, exist_ok=True)
	(shared / "acme__only-wired.md").write_text("- the team knows this repo\n")
	team.activate()
	assert bind.of("acme/reviewed") == team.joined()[0]    # the log route
	assert bind.of("acme/only-wired") == team.joined()[0]  # and the mirror route, for a repo they can see

	# ponytail: and NOT the other way. The registry is every repo `gitdashy init` ever wired, personal
	# ones included; binding one makes its facts poolable and shareable, which neither old rule did.
	assert bind.of("me/weekend-thing") == ""
	assert not memory.team_visible("me/weekend-thing")
	memory.append("me/weekend-thing", "my side project uses bun")
	memory.append("me/weekend-thing", "my side project uses bun")   # promoted for me
	assert ("me/weekend-thing", "my side project uses bun") not in memory.shareable()
	assert not os.path.exists(memory.pool_path(memory.whoami(), "me/weekend-thing"))


def test_joining_a_team_that_already_has_a_log_seeds_from_theirs(monkeypatch, tmp_path):
	"""Only the copy-my-log branch was covered. When the team HAS a log, yours is never copied — so the
	seed has to come off the first activate(), before that branch is even considered."""
	remote = tmp_path / "remote.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	# a teammate has already reviewed things and pushed the shared log
	git("clone", "-q", str(remote), str(tmp_path / "mate"), cwd=tmp_path)
	(tmp_path / "mate" / "reviewed.jsonl").write_text('{"pr":{"repository":{"nameWithOwner":"acme/theirs"}}}\n')
	git("add", "-A", cwd=tmp_path / "mate")
	git("commit", "-qm", "mate", cwd=tmp_path / "mate")
	git("push", "-q", cwd=tmp_path / "mate")

	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(mem := tmp_path / "mem").mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	open(log.LOG, "w").write('{"pr":{"repository":{"nameWithOwner":"me/mine"}}}\n')
	assert team.setup(str(remote)) == ""
	assert bind.of("acme/theirs") == team.joined()[0]       # seeded from the log that was already there
	# ponytail: and YOUR log is untouched and unpublished. It holds reviews of repos bound to other
	# teams and of private work; copying it in would have committed and pushed all of it, so joining a
	# team told them what else you review.
	assert open(config.LOG).read() == '{"pr":{"repository":{"nameWithOwner":"me/mine"}}}\n'
	assert "me/mine" not in open(team.log_of(team.joined()[0])).read()
	assert bind.of("me/mine") == ""                         # and nothing of yours got bound to them


def _old_layout(monkeypatch, tmp_path, remote=True):
	"""A pre-plural ~/.prs_team with an origin, and an empty plural home beside it."""
	src = tmp_path / "prs_team"
	(src / "memory").mkdir(parents=True)
	git("init", "-q", str(src), cwd=tmp_path)
	if remote:
		git("remote", "add", "origin", "git@github.com:org/mem.git", cwd=src)
	(src / "memory" / "general.md").write_text("- the team knows this\n")
	monkeypatch.setattr(config, "TEAM", str(src))
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "prs_teams"))
	return src


def test_an_old_single_checkout_moves_into_the_plural_home(monkeypatch, tmp_path):
	"""Everything keeps working with nothing for the user to do — the point of migrating at all."""
	src = _old_layout(monkeypatch, tmp_path)
	monkeypatch.setattr(knowledge, "unpushed", lambda d=None: 0)
	report = team.migrate()
	assert "moved your team checkout" in report
	assert not src.exists()
	assert team.joined() == ["org-mem"]
	assert open(os.path.join(team.dirs()[0], "memory", "general.md")).read() == "- the team knows this\n"
	assert team.migrate() == ""            # idempotent: nothing left at the old path


def test_migration_refuses_rather_than_risking_unpushed_work(monkeypatch, tmp_path):
	"""An automatic move of someone's unpushed reviews, at startup, inside curses. It refuses instead."""
	src = _old_layout(monkeypatch, tmp_path)
	monkeypatch.setattr(knowledge, "unpushed", lambda d=None: 2)
	assert "2 unpushed reviews" in team.migrate()
	assert src.exists() and (src / "memory" / "general.md").exists()   # untouched
	monkeypatch.setattr(knowledge, "unpushed", lambda d=None: -1)      # cannot tell: also refuse
	assert "possibly unpushed" in team.migrate()
	assert src.exists()


def test_migration_refuses_a_checkout_it_cannot_key_or_a_taken_destination(monkeypatch, tmp_path):
	src = _old_layout(monkeypatch, tmp_path, remote=False)
	monkeypatch.setattr(knowledge, "unpushed", lambda d=None: 0)
	assert "no origin" in team.migrate() and src.exists()   # nothing to name the directory by

	git("remote", "add", "origin", "git@github.com:org/mem.git", cwd=src)
	(tmp_path / "prs_teams" / "org-mem").mkdir(parents=True)
	assert "already exists" in team.migrate()
	assert src.exists() and (src / "memory" / "general.md").exists()   # and it is not merged over


def test_a_path_that_does_not_exist_yet_is_still_a_path(monkeypatch, tmp_path):
	"""os.path.isdir alone sent a path you were about to CREATE to gh, which answered
	"Could not resolve to a Repository" — the sibling of the knowledge.is_remote fix, left unswept."""
	assert team.looks_local(str(tmp_path / "nope-not-yet"))     # absolute, does not exist
	assert team.looks_local("./notes") and team.looks_local("../x") and team.looks_local("~/mem")
	assert team.looks_local(str(tmp_path))                       # and one that does
	assert not team.looks_local("NeoMedSys/review-memory")       # still owner/name
	assert not team.looks_local("NilsPontus")


def test_starting_a_team_needs_no_remote_and_no_github(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	assert team.start("NeoMedSys review memory", "what we build") == ""
	assert team.joined() == ["neomedsys-review-memory"]
	d = team.dirs()[0]
	assert os.path.isdir(os.path.join(d, ".git"))                 # a real checkout, just with no origin
	assert not team.has_remote(d)
	assert os.path.exists(os.path.join(d, "memory", "project.md"))  # seeded, ready to fill in
	assert "merge=union" in open(os.path.join(d, ".gitattributes")).read()
	assert "already in" in team.start("NeoMedSys review memory")    # idempotent, and says so
	assert "needs a name" in team.start("   ")                  # the slug is the identity


def test_a_team_can_live_somewhere_else_and_be_linked(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)
	share = tmp_path / "on-a-share"
	assert team.start("acme mem", "", str(share)) == ""
	link = tmp_path / "teams" / "acme-mem"
	assert os.path.islink(link) and os.path.realpath(link) == str(share)
	assert team.joined() == ["acme-mem"]        # and the slug still names it, not the directory it points at


def _seed_remote_log(tmp_path, remote, text):
	"""Put a shared review log in the team's own repo, which is where seeding reads it from.

	ponytail: it used to go in YOUR log, because joining copied that into the team. It no longer does —
	a personal log holds other teams' repos and private work — so a test about what the TEAM's log
	seeds has to put the entries where the team's log actually is.
	"""
	mate = tmp_path / ("mate-" + os.path.basename(str(remote)))
	git("clone", "-q", str(remote), str(mate), cwd=tmp_path)
	(mate / "reviewed.jsonl").write_text(text)
	git("add", "-A", cwd=mate)
	# ponytail: its own identity, via -c. A caller that sets GIT_AUTHOR_* later in the test, or a
	# machine with no global git config (CI), otherwise makes this commit fail with 128.
	git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed", cwd=mate)
	git("push", "-q", "origin", "HEAD", cwd=mate)


def _ident(monkeypatch):
	for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
		monkeypatch.setenv(k, v)


def test_a_team_is_named_by_its_own_files_not_by_where_it_is_hosted(monkeypatch, tmp_path):
	"""Start local, get a repo later, and everyone who clones it agrees on the key bindings point at.

	The key is fixed at creation and never derived from a remote — that is what makes the local-then-
	hosted move safe. An origin-derived key does not exist until the team is hosted, so every binding
	would have gone dead at exactly the moment the team got a URL.
	"""
	_ident(monkeypatch)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	remote = tmp_path / "eventual.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)

	assert team.start("NeoMedSys Platform", "Precision-medicine platform.") == ""
	assert team.joined() == ["neomedsys-platform"]
	assert team.info("neomedsys-platform") == {"name": "NeoMedSys Platform",
	                                           "description": "Precision-medicine platform."}
	assert not team.has_remote(team.dirs()[0])         # nothing hosted anywhere yet

	assert team.connect("neomedsys-platform", str(remote)) == ""
	assert team.joined() == ["neomedsys-platform"]     # the key did not move, so bindings still hold

	# a colleague clones the same repo and lands on the same key, from the team's own team.json
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "other"))
	assert team.setup(str(remote)) == ""
	assert team.joined() == ["neomedsys-platform"]
	assert team.info("neomedsys-platform")["name"] == "NeoMedSys Platform"


def test_connect_pushes_even_when_there_is_nothing_new_to_commit(monkeypatch, tmp_path):
	"""push_dir returns early with nothing staged — which is exactly a team's state when you connect it.
	The remote stayed empty, so the next person to clone got no team.json and a key from the URL."""
	_ident(monkeypatch)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	remote = tmp_path / "r.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
	assert team.start("Acme Tools", "internal") == ""
	assert team.connect("acme-tools", str(remote)) == ""
	out = subprocess.run(["git", "-C", str(remote), "ls-tree", "-r", "--name-only", "HEAD"],
	                     capture_output=True, text=True).stdout
	assert "team.json" in out and "memory/project.md" in out   # it actually reached the remote


def test_nothing_in_the_team_path_shells_out_to_github():
	"""A team is a git repo people can reach. `gh repo create` made GitHub the only place one could be
	born, and `gh repo clone` made it the only place one could live."""
	# ponytail: the code, not the prose. The ponytails explaining why gh is gone naturally say "gh".
	import ast as _ast, pathlib as _p
	src = _p.Path(team.__file__).read_text()
	strings = [n.value for n in _ast.walk(_ast.parse(src))
	           if isinstance(n, _ast.Constant) and isinstance(n.value, str)]
	cmds = [t for t in strings if t == "gh" or t.startswith("gh ")]
	assert cmds == [], f"team.py still invokes gh: {cmds}"


def test_a_started_team_pins_its_branch(monkeypatch, tmp_path):
	"""git init uses init.defaultBranch — "main" on one machine, "master" on the next. Two people
	starting or connecting the same team then push branches that never meet, and a clone of a repo
	whose HEAD names the other one comes back EMPTY. CI found it; my own config hid it."""
	_ident(monkeypatch)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)   # a machine with no init.defaultBranch
	monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
	assert team.start("Acme Tools", "internal") == ""
	head = subprocess.run(["git", "-C", team.dirs()[0], "symbolic-ref", "--short", "HEAD"],
	                      capture_output=True, text=True).stdout.strip()
	assert head == team.BRANCH


def test_migration_carries_the_bindings_across_the_rename(monkeypatch, tmp_path):
	"""The shipped version (v1.30.0) keyed bindings on origin_slug — "owner/name", with a slash. The
	directory is now "owner-name", so every one of them resolved to nothing: the team's facts stopped
	being read, brief() said "not in team owner/name" about the team you were in, and pooling and
	sharing went quiet with nothing on screen to say so."""
	_ident(monkeypatch)
	src = tmp_path / "prs_team"
	(src / "memory").mkdir(parents=True)
	git("init", "-q", str(src), cwd=tmp_path)
	git("remote", "add", "origin", "git@github.com:org/mem.git", cwd=src)
	(src / "memory" / "general.md").write_text("- the team knows this\n")
	monkeypatch.setattr(config, "TEAM", str(src))
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "prs_teams"))
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mine"))
	monkeypatch.setattr(knowledge, "unpushed", lambda d: 0)
	bind.bind("acme/api", "org/mem")          # exactly as the shipped version wrote them
	bind.bind_owner("acme", "org/mem")
	bind.bind("other/repo", "someone/else")   # a team we are not migrating: left alone

	report = team.migrate()
	assert "repointed 2 bindings" in report
	assert team.joined() == ["org-mem"]
	assert bind.of("acme/api") == "org-mem" and bind.of("acme/anything") == "org-mem"
	assert bind.of("other/repo") == "someone/else"
	# and the whole point: the team's memory is readable again
	assert [l for l, _ in memory.sources("acme/api")] == ["mine", "team org-mem"]
	assert memory.team_visible("acme/api")


def test_joining_never_publishes_your_own_review_log(monkeypatch, tmp_path):
	"""A personal log holds reviews of repos bound to OTHER teams and of private work. Copying it into
	the team committed and PUSHED all of it, so joining told them what else you review."""
	_ident(monkeypatch)
	remote = tmp_path / "teamB.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(tmp_path / "mine").mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mine"))
	open(log.LOG, "w").write(
		'{"at":"2026-09-01T10:00:00+00:00","verdict":"approve","model":"o","summary":"","body":"b",'
		'"pr":{"repository":{"nameWithOwner":"acme/secret-side-project"},"number":1,"url":"u","title":"t"}}\n')

	assert team.setup(str(remote)) == ""
	files = subprocess.run(["git", "-C", str(remote), "ls-tree", "-r", "--name-only", "HEAD"],
	                       capture_output=True, text=True).stdout
	assert "team.json" not in files or "reviewed.jsonl" not in files.split()
	theirs = team.log_of(team.joined()[0])
	assert not os.path.exists(theirs) or "secret-side-project" not in open(theirs).read()
	assert "secret-side-project" in open(log.LOG).read()   # still yours, still readable by you
	assert bind.of("acme/secret-side-project") == ""       # and not bound to them either


def test_a_join_reports_on_its_own_push_not_another_teams(monkeypatch, tmp_path):
	"""push() walks EVERY joined team now, so a team you were already in whose push fails set the
	ERROR global — and the freshly cloned, renamed, fully joined team was reported as a failure."""
	_ident(monkeypatch)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	good = tmp_path / "good.git"
	git("init", "-q", "--bare", "-b", "main", str(good), cwd=tmp_path)
	assert team.start("Broken Team", "") == ""
	team.connect("broken-team", str(tmp_path / "nonexistent" / "never.git"))
	open(os.path.join(team.dir_of("broken-team"), "memory", "general.md"), "w").write("- a fact\n")

	err = team.setup(str(good))
	assert err == "", f"a successful join reported {err!r}"
	assert "good" in team.joined()


def test_a_failed_start_leaves_no_link_behind(monkeypatch, tmp_path):
	"""It is not a repo so joined() skips it, but lexists() is true — so retrying the same name
	answered "exists and is not a team" forever and the user had to clean up by hand."""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	share = tmp_path / "share"
	monkeypatch.setattr(team, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no"))
	assert "could not git init" in team.start("Acme", "", str(share))
	assert not os.path.lexists(tmp_path / "teams" / "acme")   # and the name is free to retry
	assert share.exists()                                      # what it pointed at is the user's


def test_unpushed_cannot_raise(monkeypatch, tmp_path):
	"""team.migrate() documents that it never raises — it runs before the first draw — and calls this."""
	def boom(*a, **k):
		raise subprocess.TimeoutExpired(a, 60)
	monkeypatch.setattr(knowledge.subprocess, "run", boom)
	assert knowledge.unpushed(str(tmp_path)) == -1
	monkeypatch.setattr(knowledge.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
	assert knowledge.unpushed(str(tmp_path)) == -1


def test_a_team_you_started_can_be_left(monkeypatch, tmp_path):
	"""`git log @{u}..HEAD` exits non-zero without an upstream, so unpushed() said -1 and leave()
	refused forever — on exactly the teams this PR exists to let you create. start() then answered
	"already in <key>", so there was no way out inside the app."""
	_ident(monkeypatch)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	assert team.start("Acme Tools", "internal") == ""
	d = team.dir_of("acme-tools")
	assert not team.has_remote(d)
	assert knowledge.unpushed(d) == 0          # nowhere to push, so nothing is unpushed
	assert knowledge.leave("acme-tools") == ""
	assert team.joined() == []
	assert team.start("Acme Tools") == ""      # and the name is free again

	# uncommitted work still refuses, remote or not — that is the check that matters here
	open(os.path.join(team.dir_of("acme-tools"), "memory", "general.md"), "w").write("- unsaved\n")
	assert knowledge.unpushed(team.dir_of("acme-tools")) == -1
	assert "uncommitted work" in knowledge.leave("acme-tools")  # nowhere to push, so that is the risk


def test_a_join_that_cannot_publish_still_joined(monkeypatch, tmp_path):
	"""Read-only access clones fine, then union_attrs and seed_project give push_dir something to
	commit. Returning that error made the caller treat a usable checkout as a failure."""
	_ident(monkeypatch)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	remote = tmp_path / "r.git"
	git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
	git("clone", "-q", str(remote), str(tmp_path / "seed"), cwd=tmp_path)
	(tmp_path / "seed" / "team.json").write_text('{"name": "Read Only", "description": ""}')
	git("add", "-A", cwd=tmp_path / "seed")
	git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "s", cwd=tmp_path / "seed")
	git("push", "-q", "origin", "HEAD", cwd=tmp_path / "seed")
	# ponytail: a pre-receive hook, not chmod. `os.chmod(remote, 0o500)` gates the TOP directory only —
	# objects/ and refs/ stay writable — and mode bits are ignored for root, which CI may well be. A
	# test that cannot fail proves nothing, and this one asserts the absence of an error.
	hook = remote / "hooks" / "pre-receive"
	hook.parent.mkdir(exist_ok=True)
	hook.write_text("#!/bin/sh\nexit 1\n")
	hook.chmod(0o755)
	assert team.setup(str(remote)) == "", "a usable checkout was reported as a failed join"
	assert team.joined() == ["read-only"]
	assert team.ERROR                            # and the sync failure IS reported, on the Team row


def test_a_teams_own_name_cannot_paint_the_header(monkeypatch, tmp_path):
	"""team.json comes from a CLONED repo, so anyone with push access to the team writes it — and it
	lands in the curses header and the CLI listing."""
	shared = tmp_path / "teams" / "org-t"
	(shared / ".git").mkdir(parents=True)
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	(shared / "team.json").write_text(
		'{"name": "Evil\\nTeam\\u0007\\u001b[31m", "description": "line one\\nline two\\u0000"}')
	it = team.info("org-t")
	assert "\n" not in it["name"] and "\n" not in it["description"]
	assert all(c.isprintable() for c in it["name"] + it["description"])
	assert it["name"] == "EvilTeam[31m" and it["description"] == "line oneline two"
	# and a name that is nothing but control bytes falls back to the key rather than rendering empty
	(shared / "team.json").write_text('{"name": "\\u0007\\u0007", "description": ""}')
	assert team.info("org-t")["name"] == "org-t"


def test_a_private_https_clone_says_what_to_do_about_it(monkeypatch, tmp_path):
	"""git cannot ask — GIT_TERMINAL_PROMPT=0 is deliberate — so an https URL to a private repo fails
	outright on a machine with no credential helper, and the raw fatal is clipped mid-sentence.
	It used to work because `gh repo clone` carried gh's token; dropping gh took that with it."""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	fatal = "fatal: could not read Username for 'https://github.com': terminal prompts disabled"
	monkeypatch.setattr(team, "_remote",
						lambda cmd, timeout=None: subprocess.CompletedProcess(cmd, 128, "", fatal))
	err = team.clone("https://github.com/NilsPontus/TeamDashy.git", str(tmp_path / "x"))
	assert "git@github.com:NilsPontus/TeamDashy.git" in err   # the form that works from an agent
	# ponytail: and it FITS. The version this replaces was 181 characters; confirm() wraps it and the
	# footer hard-clips at w - 1, so the advice fell off the end of an 80-column line — a truncation
	# bug fixed with a message that truncates. This is the assertion that would have caught that.
	assert len(err) <= team.FOOTER, f"{len(err)} chars will be clipped in the footer"
	assert team.ERROR.endswith(err)                            # and the T row stops painting the fatal

	# ponytail: only for an auth failure. A repo that does not exist must still say THAT.
	monkeypatch.setattr(team, "_remote", lambda cmd, timeout=None:
						subprocess.CompletedProcess(cmd, 128, "", "fatal: repository not found"))
	assert "not found" in team.clone("https://github.com/a/b.git", str(tmp_path / "y"))


def test_the_ssh_form_of_a_url_is_not_a_github_fact():
	"""Every git host offers both forms; ssh is the one that authenticates from an agent."""
	assert team.ssh_form("https://github.com/a/b.git") == "git@github.com:a/b.git"
	assert team.ssh_form("https://gitlab.example.com/g/sub/c") == "git@gitlab.example.com:g/sub/c.git"
	assert team.ssh_form("http://h/a/b") == "git@h:a/b.git"   # not only https
	assert team.ssh_form("git@github.com:a/b.git") == ""      # already ssh
	assert team.ssh_form("/a/local/path") == "" and team.ssh_form("") == ""


def test_a_credential_in_the_url_is_never_echoed(monkeypatch, tmp_path):
	"""`https://user:token@host/a/b.git` is a legitimate remote. git redacts the password in its own
	fatal; we were printing it to the footer and to CLI scrollback, and splicing it into the ssh form."""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	monkeypatch.setattr(team, "_remote", lambda cmd, timeout=None: subprocess.CompletedProcess(
		cmd, 128, "", "fatal: Authentication failed for 'https://github.com/o/r.git/'"))
	err = team.clone("https://x-token:ghp_SECRET123@github.com/o/r.git", str(tmp_path / "x"))
	assert "ghp_SECRET123" not in err and "x-token" not in err
	assert "ghp_SECRET123" not in team.ERROR
	assert err.endswith("git@github.com:o/r.git")              # and the suggestion is still usable
	assert team.ssh_form("https://u:tok@h/a/b.git") == "git@h:a/b.git"


def test_the_password_variant_is_the_same_failure(monkeypatch, tmp_path):
	"""git says Password, not Username, when the URL carries a user. Same class, same remedy."""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	monkeypatch.setattr(team, "_remote", lambda cmd, timeout=None: subprocess.CompletedProcess(
		cmd, 128, "", "fatal: could not read Password for 'https://me@github.com': terminal prompts disabled"))
	err = team.clone("https://me@github.com/a/b.git", str(tmp_path / "x"))
	assert "git@github.com:a/b.git" in err and "could not read Password" not in err


def test_an_ssh_url_that_cannot_authenticate_still_says_something_useful(monkeypatch, tmp_path):
	"""There is no ssh form to suggest, so it names the host and says gitdashy cannot ask."""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	monkeypatch.setattr(team, "_remote", lambda cmd, timeout=None: subprocess.CompletedProcess(
		cmd, 128, "", "fatal: Authentication failed for 'git@github.com:o/r.git'"))
	err = team.clone("git@github.com:o/r.git", str(tmp_path / "x"))
	assert "ssh agent" in err and "github.com" in err and len(err) <= team.FOOTER


def test_a_long_remote_is_never_clipped(monkeypatch, tmp_path):
	"""Dropping words to fit is fine; a truncated REMOTE is not a remote. Handing someone an
	uncopyable git URL is the same failure as the 181-char message, just rarer."""
	monkeypatch.setattr(config, "TEAMS", str(tmp_path / "teams"))
	monkeypatch.setattr(team, "_remote", lambda cmd, timeout=None: subprocess.CompletedProcess(
		cmd, 128, "", "fatal: could not read Username for 'https://h': terminal prompts disabled"))
	long_url = "https://git.a-very-long-internal-host.example.com/platform/group/subgroup/memory.git"
	err = team.clone(long_url, str(tmp_path / "x"))
	assert team.ssh_form(long_url) in err          # whole, and copyable
	assert len(err) > team.FOOTER                   # the words went instead, and it says nothing else
	assert err == team.ssh_form(long_url)


def test_git_is_asked_in_a_locale_we_can_read(monkeypatch):
	"""Detection matches on git's stderr. On a localized machine those strings never appear, the auth
	branch never fires, and the user gets the clipped fatal this whole path exists to replace."""
	seen = {}
	def fake(cmd, **kw):
		seen.update(kw.get("env", {}))
		return subprocess.CompletedProcess(cmd, 0, "", "")
	monkeypatch.setattr(subprocess, "run", fake)
	team._remote(["git", "status"])
	assert seen.get("LC_ALL") == "C" and seen.get("LANGUAGE") == ""
	assert seen.get("GIT_TERMINAL_PROMPT") == "0"   # and the reason it cannot prompt is still there
