import os
import shutil
import subprocess
import threading
import time

from dashy import config
from dashy.core import bind, heartbeat, memory, mirror, team

from conftest import a_team


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
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	shared = a_team(monkeypatch, tmp_path, "org-t")
	bind.bind("a/b", "org-t")  # the mirror shows what a review of THIS repo sees, which the binding decides
	seed("a/b", "mine about a/b")
	seed("a/b", "team about a/b", str(shared))
	into = tmp_path / "out"
	mirror.sync(str(into), "a/b")
	repo = (into / "repo.md").read_text()
	assert "mine about a/b" in repo and "team about a/b" in repo
	assert "### mine" in repo and "### team org-t" in repo


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


def test_an_unbound_repos_mirror_loses_the_teams_files(monkeypatch, tmp_path):
	"""A mirror never outlives its source — so unbinding DELETES what is already in the repo.

	That is correct behaviour and it is why seeding has to cover every route into the mirror, not just
	the review log. Pinned here so the deletion is a decision someone made rather than a surprise.
	"""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	shared = a_team(monkeypatch, tmp_path, "org-t")
	seed("a/b", "team about a/b", str(shared))
	seed(None, "team general", str(shared))
	into = tmp_path / "out"
	bind.bind("a/b", "org-t")
	mirror.sync(str(into), "a/b", pull=False, general=True)
	assert sorted(p.name for p in into.iterdir()) == ["general.md", "repo.md"]

	bind.forget("a/b")
	mirror.sync(str(into), "a/b", pull=False, general=True)
	assert sorted(p.name for p in into.iterdir()) == []  # both go: nothing of yours, nothing of theirs


def test_the_report_names_the_team_it_actually_read(monkeypatch, tmp_path):
	"""team.NAME is a comma-joined list now, so a sync for a repo bound to ONE team reported
	"from team org-a, org-b" — the report naming a source the write did not come from."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	shared = a_team(monkeypatch, tmp_path, "org-a")
	(tmp_path / "teams" / "org-b" / ".git").mkdir(parents=True)
	monkeypatch.setattr(team, "NAME", "org-a, org-b")
	seed("a/b", "team about a/b", str(shared))
	bind.bind("a/b", "org-a")
	out = mirror.sync(str(tmp_path / "out"), "a/b", pull=False)
	assert "from team org-a" in out and "org-b" not in out


def test_the_report_falls_back_when_the_team_has_been_left(monkeypatch, tmp_path):
	"""The case the label fix was made for and did not cover: a repo still bound to a team whose
	checkout is gone. sources() drops to yours alone, so the report must say so too."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	shared = a_team(monkeypatch, tmp_path, "org-a")
	seed("a/b", "team about a/b", str(shared))
	seed("a/b", "mine about a/b")
	bind.bind("a/b", "org-a")
	assert "from team org-a" in mirror.sync(str(tmp_path / "out"), "a/b", pull=False)

	shutil.rmtree(tmp_path / "teams" / "org-a")     # left, while the binding remains
	out = mirror.sync(str(tmp_path / "out"), "a/b", pull=False)
	assert "from team org-a" not in out and str(tmp_path / "mem") in out


def test_a_bound_repos_mirror_carries_the_teams_brief_and_general_facts(monkeypatch, tmp_path):
	"""A review of a bound repo reads the team's brief and general facts; a session in it read neither.
	They ride repo.md now — per repo, through the binding — and yours stay out, since they load live."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	shared = a_team(monkeypatch, tmp_path, "org-t")
	seed(None, "mine general — loads live, not here")
	seed(None, "team general: verify against the pushed head", str(shared))
	seed("a/b", "team about a/b", str(shared))
	(shared / "project.md").write_text("We build the thing.\n")
	bind.bind("a/b", "org-t")
	into = tmp_path / "out"
	mirror.sync(str(into), "a/b", pull=False)
	repo = (into / "repo.md").read_text()
	assert "### brief — team org-t" in repo and "We build the thing." in repo
	assert "team org-t — true of every repo it covers" in repo and "pushed head" in repo
	assert "## a/b" in repo and "team about a/b" in repo
	assert "mine general" not in repo                       # yours are global already
	assert repo.index("We build the thing.") < repo.index("## a/b")   # context, then the repo

	bind.forget("a/b")
	mirror.sync(str(into), "a/b", pull=False)
	assert not (into / "repo.md").exists()                  # unbound: nothing of theirs, nothing of yours here


def test_an_unbound_repos_mirror_has_no_team_context(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	shared = a_team(monkeypatch, tmp_path, "org-t")
	seed(None, "team general", str(shared))
	(shared / "project.md").write_text("theirs\n")
	seed("a/b", "mine about a/b")
	into = tmp_path / "out"
	mirror.sync(str(into), "a/b", pull=False)
	repo = (into / "repo.md").read_text()
	assert "mine about a/b" in repo and "team general" not in repo and "theirs" not in repo


def test_the_mirror_carries_the_one_brief_a_review_would_get(monkeypatch, tmp_path):
	"""Never both. The block used to import yours globally while the mirror carried the team's."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	shared = a_team(monkeypatch, tmp_path, "org-t")
	os.makedirs(tmp_path / "mem", exist_ok=True)
	(tmp_path / "mem" / "project.md").write_text("mine: a side project\n")
	(shared / "project.md").write_text("theirs: the product\n")
	seed("a/b", "a fact")
	into = tmp_path / "out"

	mirror.sync(str(into), "a/b", pull=False)                          # unbound: yours, and it says so
	repo = (into / "repo.md").read_text()
	assert "### brief — yours" in repo and "a side project" in repo and "the product" not in repo

	bind.bind("a/b", "org-t")
	mirror.sync(str(into), "a/b", pull=False)                          # bound: theirs, and only theirs
	repo = (into / "repo.md").read_text()
	assert "### brief — team org-t" in repo and "the product" in repo and "a side project" not in repo


def test_general_on_a_bound_repo_says_the_teams_general_facts_once(monkeypatch, tmp_path):
	"""general.md already carries them through scope_text; repo.md must not carry them again."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	shared = a_team(monkeypatch, tmp_path, "org-t")
	seed(None, "team general: verify the pushed head", str(shared))
	seed("a/b", "team about a/b", str(shared))
	bind.bind("a/b", "org-t")
	into = tmp_path / "out"
	mirror.sync(str(into), "a/b", pull=False, general=True)
	both = (into / "general.md").read_text() + (into / "repo.md").read_text()
	assert both.count("verify the pushed head") == 1
	mirror.sync(str(into), "a/b", pull=False)                          # without --general it moves to repo.md
	assert "verify the pushed head" in (into / "repo.md").read_text()


def with_remote(d, fetched_ago=None):
	"""Make a team checkout look like one that was cloned, and optionally pulled `fetched_ago` seconds ago."""
	(d / ".git" / "config").write_text('[remote "origin"]\n\turl = git@example.com:org/t.git\n')
	if fetched_ago is not None:
		f = d / ".git" / "FETCH_HEAD"
		f.write_text("abc123\t\tbranch 'main' of git@example.com:org/t.git\n")
		at = time.time() - fetched_ago
		os.utime(f, (at, at))


def test_the_header_says_when_the_team_was_last_reached(monkeypatch, tmp_path):
	"""The one place a session actually reads. An age nobody can see is an age nobody acts on."""
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	bind.bind("a/b", "org-t")
	with_remote(mem.parent, fetched_ago=600)
	seed("a/b", "uses tabs")
	heartbeat.beat(300)
	mirror.sync(str(tmp_path / "out"), "a/b", pull=False)
	got = (tmp_path / "out" / "repo.md").read_text()
	assert "> team org-t: last pulled 10 minutes ago" in got
	assert "may be behind" not in got  # inside STALE, so the age is reported and not warned about
	# ponytail: the blank line the header used to end with. It was lost when {age} took its place, and
	# the body renders by luck today because it always starts with a heading, which closes the
	# blockquote — a body starting with a bullet would be folded into the quote.
	assert got.split("## a/b")[0].endswith("ago\n\n")


def test_the_header_warns_when_nothing_is_refreshing_it(monkeypatch, tmp_path):
	"""A mirror is only as fresh as the dashboard that pulls for it, and a session started in a repo
	nobody has the dashboard open for reads days-old team memory with no way to tell."""
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	bind.bind("a/b", "org-t")
	with_remote(mem.parent, fetched_ago=4 * 86400)
	seed("a/b", "uses tabs")
	mirror.sync(str(tmp_path / "out"), "a/b", pull=False)
	got = (tmp_path / "out" / "repo.md").read_text()
	assert "> team org-t: last pulled 4 days ago — **this may be behind what the team has.**" in got
	assert "Nothing is refreshing it: start `gitdashy`" in got


def test_the_header_warns_when_the_dashboard_is_running_but_not_reaching_the_team(monkeypatch, tmp_path):
	"""The case that most needs telling, and the one that read as fine. An expired credential or a VPN
	that is off leaves a dashboard ticking happily while every pull fails, and `stale = not running and
	...` printed the age with no call to action."""
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	bind.bind("a/b", "org-t")
	with_remote(mem.parent, fetched_ago=4 * 86400)
	seed("a/b", "uses tabs")
	heartbeat.beat(300)  # ticking, and four days behind
	mirror.sync(str(tmp_path / "out"), "a/b", pull=False)
	got = (tmp_path / "out" / "repo.md").read_text()
	assert "last pulled 4 days ago — **this may be behind what the team has.**" in got
	assert "running but is not reaching the team" in got
	assert "Nothing is refreshing it" not in got  # the remedy differs; something IS trying


def test_a_team_with_no_remote_has_no_age_to_report(monkeypatch, tmp_path):
	"""There is nothing to be behind. A local-only team saying "last pulled never" would read as a
	fault, and the operator has one of these.

	ponytail: the checkout is given a FETCH_HEAD and NO origin, which is a remote that was removed
	after a fetch. Without it the test passed whatever the remote check did, because a team that never
	had a remote has no FETCH_HEAD to stat either and the age came back empty for the other reason.
	"""
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	bind.bind("a/b", "org-t")
	with_remote(mem.parent, fetched_ago=4 * 86400)
	(mem.parent / ".git" / "config").write_text("[core]\n\tbare = false\n")  # origin removed since
	seed("a/b", "uses tabs")
	mirror.sync(str(tmp_path / "out"), "a/b", pull=False)
	got = (tmp_path / "out" / "repo.md").read_text()
	assert "last pulled" not in got


def test_sync_does_not_pull_while_a_dashboard_is_running(monkeypatch, tmp_path):
	"""Two processes running pull --rebase in one checkout race for index.lock, and the loser can
	leave a rebase behind. The session hook fires sync off in the background without knowing whether
	a dashboard is up, so the skip has to live here."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	seed("a/b", "uses tabs")
	pulls = []
	monkeypatch.setattr(team, "pull", lambda: pulls.append(1))
	heartbeat.beat(300)
	mirror.sync(str(tmp_path / "out"), "a/b")
	assert pulls == []
	# ponytail: and it SAYS so. Someone typing `gitdashy sync-memory` asked for the team's newest, and
	# a silent skip is indistinguishable from a fetch that found nothing — one of those is a reason to
	# go looking and the other is not.
	assert "not pulled: a dashboard is refreshing this" in mirror.sync(str(tmp_path / "out"), "a/b")
	os.remove(tmp_path / ".prs_dashboard")
	report = mirror.sync(str(tmp_path / "out"), "a/b")
	assert pulls == [1]  # nothing else is going to, so this caller does it
	assert "not pulled" not in report

def test_a_write_that_fails_leaves_the_previous_mirror_whole(monkeypatch, tmp_path):
	"""Two writers now, and a plain open() truncates before it writes: a session reading at the wrong
	moment imported an empty mirror and was told the team knows nothing."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	seed("a/b", "uses tabs")
	into = tmp_path / "out"
	mirror.sync(str(into), "a/b", pull=False)
	before = (into / "repo.md").read_text()
	seed("a/b", "and spaces nowhere")

	def no(*a):
		raise OSError("no space left on device")

	monkeypatch.setattr(mirror.os, "replace", no)  # the write lands, the move into place does not
	assert "refused" in mirror.sync(str(into), "a/b", pull=False)
	assert (into / "repo.md").read_text() == before  # the old mirror, not an empty file
	assert not (into / "repo.md.part").exists()  # and nothing left in a directory the mirror owns


def test_two_writers_never_leave_a_mirror_a_reader_can_half_see(monkeypatch, tmp_path):
	"""The seam the rename exists for, with nothing mocked.

	A fixed `dst + ".part"` passes every test that mocks os.replace and fails here: both writers open
	the same temp file, the second truncates the first mid-write, and whoever renames first moves an
	interleaved file into place. That is worse than a short file, because a whole-looking corrupt
	mirror invites no second look — and the failure path removed the OTHER writer's temp file.
	"""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	seed("a/b", "uses tabs " + "x" * 4000)  # big enough that a write is not one atomic syscall
	into = tmp_path / "out"
	mirror.sync(str(into), "a/b", pull=False)
	dst, seen, stop = into / "repo.md", [], threading.Event()

	def read():
		while not stop.is_set():
			try:
				seen.append(dst.read_text())
			except OSError:
				seen.append("")  # a missing file is a failure too; assert on it below

	def write():
		for _ in range(40):
			mirror.sync(str(into), "a/b", pull=False)

	reader = threading.Thread(target=read)
	reader.start()
	writers = [threading.Thread(target=write) for _ in range(3)]
	for w in writers:
		w.start()
	for w in writers:
		w.join()
	stop.set()
	reader.join()
	assert seen, "the reader never got a look in"
	for got in seen:
		assert got.startswith("> **Shared team memory"), repr(got[:80])
		assert got.endswith("x" * 100 + "\n"), repr(got[-80:])
	assert not [p for p in os.listdir(into) if p not in mirror.NAMES], os.listdir(into)


def test_two_background_syncs_at_once_pull_once(monkeypatch, tmp_path):
	"""Two sessions opened at once — a terminal and an editor is the ordinary case — both run the
	hook's background sync. Neither writes a beat, so alive() is false for both, and before the lock
	both ran `pull --rebase` in the same checkout: the loser's `rebase --abort` fires into a checkout
	the winner may still be rebasing."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	seed("a/b", "uses tabs")
	inside, reports = threading.Semaphore(0), []
	release = threading.Event()

	def slow_pull():
		inside.release()
		release.wait(5)

	monkeypatch.setattr(team, "pull", slow_pull)
	first = threading.Thread(target=lambda: reports.append(mirror.sync(str(tmp_path / "out"), "a/b")))
	first.start()
	assert inside.acquire(timeout=5)  # the first is inside team.pull, holding the lock
	reports.append(mirror.sync(str(tmp_path / "out2"), "a/b"))
	release.set()
	first.join(5)
	assert not first.is_alive()
	assert sum("not pulled: another sync is already pulling" in r for r in reports) == 1
	assert not os.path.exists(tmp_path / heartbeat.LOCK)  # and it is given back


def test_a_team_reached_moments_ago_is_not_fetched_again(monkeypatch, tmp_path):
	"""The session hook fires one of these at every session start, on every machine. Open six repos in
	an editor and that is six fetches in a second, all asking the question the first one answered —
	and nothing throttled it, because alive() is false exactly when the hook runs."""
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	bind.bind("a/b", "org-t")
	seed("a/b", "uses tabs")
	pulls = []
	monkeypatch.setattr(team, "pull", lambda: pulls.append(1))
	with_remote(mem.parent, fetched_ago=5)
	assert "not pulled: every team was reached in the last few minutes" in mirror.sync(str(tmp_path / "o"), "a/b")
	assert pulls == []
	with_remote(mem.parent, fetched_ago=mirror.FRESH + 1)
	mirror.sync(str(tmp_path / "o"), "a/b")
	assert pulls == [1]  # past the floor, it goes


def test_a_team_with_no_remote_is_not_a_reason_to_skip_the_others(monkeypatch, tmp_path):
	"""A local-only team is never "reached", so treating it as stale would make every sync pull for a
	team that has nowhere to pull from — and treating it as fresh would hold back the ones that do."""
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	seed("a/b", "uses tabs")
	pulls = []
	monkeypatch.setattr(team, "pull", lambda: pulls.append(1))
	mirror.sync(str(tmp_path / "o"), "a/b")  # the only team has no remote at all
	assert pulls == [1]


def test_the_header_says_when_the_last_sync_did_not_land(monkeypatch, tmp_path):
	"""`git pull --rebase` rewrites FETCH_HEAD during the FETCH, before the rebase. A pull that
	fetched and then failed to rebase leaves a fresh timestamp over a checkout that did not move, so
	the header read "last pulled just now" for exactly the case it exists to report — and _all_fresh
	then held retries off on the strength of it. A live error outranks any age."""
	mem = a_team(monkeypatch, tmp_path)
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	bind.bind("a/b", "org-t")
	with_remote(mem.parent, fetched_ago=2)
	seed("a/b", "uses tabs")
	monkeypatch.setattr(team, "ERROR", "could not apply 3f2a1b… CONFLICT in memory/general.md")
	mirror.sync(str(tmp_path / "out"), "a/b", pull=False)
	got = (tmp_path / "out" / "repo.md").read_text()
	assert "last pulled just now — **the last sync did not land:**" in got
	assert "CONFLICT in memory/general.md" in got
