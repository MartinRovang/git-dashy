"""The binding store: which team a repo belongs to, declared rather than inferred."""
import os

from dashy import config
from dashy.core import bind, memory, team

from conftest import a_team


def test_one_repo_is_named_the_same_way_however_you_spell_it():
	"""A binding must survive a re-clone and a move, so it keys on the slug, not on a path or a URL."""
	for spelling in ("acme/api", "git@github.com:acme/api.git", "https://github.com/acme/api",
	                 "https://github.com/acme/api.git", "/home/me/src/acme/api"):
		assert bind.key(spelling) == "acme/api", spelling


def test_a_key_that_is_not_an_owner_and_a_name_is_refused():
	"""It would key a row nothing ever matches, which reads back as unbound with no way to tell."""
	for bad in ("", "notes", "/", "a/", "/b"):
		assert bind.key(bad) == "", bad
	assert bind.bind("notes", "org/t") == "'notes' is not an owner/name"
	assert bind.bindings() == {}


def test_binding_and_unbinding_round_trip():
	assert bind.bind("acme/api", "org/t") == ""
	assert bind.of("acme/api") == "org/t"
	assert bind.of("git@github.com:acme/api.git") == "org/t"  # same repo, another spelling
	assert bind.of("acme/other") == ""
	assert bind.forget("acme/api") == ""
	assert bind.of("acme/api") == ""
	assert bind.forget("acme/api") == ""  # already gone, and saying so again is not an error


def test_the_last_line_wins_and_the_file_only_ever_grows():
	"""Appending without a lock is what makes two processes safe; the read resolves the order."""
	bind.bind("acme/api", "org/one")
	bind.bind("acme/api", "org/two")
	assert bind.of("acme/api") == "org/two"
	assert bind.bind("acme/api", "org/two") == ""
	assert open(bind.BINDINGS).read().count("\n") == 2  # asked for what it already had, wrote nothing


def test_one_unreadable_line_does_not_lose_the_rest():
	"""install.registered() lost a whole registry to this once; the shape is copied, so is the guard."""
	bind.bind("acme/api", "org/t")
	with open(bind.BINDINGS, "a") as f:
		f.write("this is not json\n{}\n" + '{"repo": 7}\n' + '{"repo": "acme/two"}\n')
	bind.bind("acme/three", "org/t")
	assert bind.bindings() == {"acme/api": "org/t", "acme/three": "org/t"}  # a team-less line binds nothing


def test_seeding_binds_what_the_log_already_named(monkeypatch, tmp_path):
	"""The bootstrap: upgrading into bindings must not silently drop the brief you had yesterday."""
	assert bind.seed("org/t", ["acme/api", "acme/web", "acme/api"]) == ["acme/api", "acme/web"]
	assert bind.bindings() == {"acme/api": "org/t", "acme/web": "org/t"}
	assert bind.seed("org/t", ["acme/api", "acme/web"]) == []  # idempotent
	assert bind.seed("", ["acme/x"]) == []  # a team with no slug is not something to bind to


def test_unbinding_something_never_bound_still_keeps_it_unbound():
	"""The tombstone is the record of a decision, not of a deletion — seed() reads it, not the live map."""
	assert bind.forget("acme/api") == "" and bind.of("acme/api") == ""  # nothing to remove
	assert bind.seed("org/t", ["acme/api", "acme/web"]) == ["acme/web"]
	assert bind.of("acme/api") == ""


def test_an_unbinding_survives_the_next_seed():
	"""Seeding off the live map would re-bind it at every startup, and the unbind would look inert."""
	bind.seed("org/t", ["acme/api"])
	bind.forget("acme/api")
	assert bind.seed("org/t", ["acme/api"]) == []
	assert bind.of("acme/api") == ""


def test_a_binding_names_a_team_this_machine_may_not_have(monkeypatch, tmp_path):
	shared = a_team(monkeypatch, tmp_path, "org/t")
	assert bind.team_dir("org/t") == str(shared)
	assert bind.team_dir("org/other") == ""  # a team we are not in resolves to nothing, never to ours
	assert bind.team_dir("") == ""
	assert bind.team_key() == "org/t"


def test_the_brief_a_repo_gets_is_the_one_its_binding_names(monkeypatch, tmp_path):
	"""End to end, through the function reviews actually call."""
	mine = tmp_path / "mine"
	mine.mkdir()
	(mine / "project.md").write_text("My own work.\n")
	monkeypatch.setattr(config, "MEMORY_DIR", str(mine))
	shared = a_team(monkeypatch, tmp_path, "org/t")
	(shared / "project.md").write_text("What the team builds.\n")
	assert memory.brief("acme/api")[0] == "My own work."
	bind.bind("acme/api", "org/t")
	assert memory.brief("acme/api") == ("What the team builds.", "team org/t")
	bind.forget("acme/api")
	assert memory.brief("acme/api")[0] == "My own work."  # and it is undoable, which the log never was


def test_a_hand_typed_repo_finds_the_row_a_review_looks_up():
	"""bind is the one store fed by typing; every other key arrives as GitHub's own nameWithOwner."""
	assert bind.bind("NeoMedSys/Neo-API", "org/t") == ""
	assert bind.of("neomedsys/neo-api") == "org/t"
	assert bind.forget("NEOMEDSYS/NEO-API") == ""
	assert bind.of("neomedsys/neo-api") == ""


def test_an_unwritable_store_reports_instead_of_taking_the_dashboard_down(monkeypatch, tmp_path):
	"""seed() runs from team.activate(), which runs at startup inside curses. A raise there draws nothing."""
	monkeypatch.setattr(bind, "BINDINGS", str(tmp_path / "as-a-dir"))
	(tmp_path / "as-a-dir").mkdir()
	err = bind.bind("acme/api", "org/t")
	assert err and "Is a directory" in err  # the real reason, not a swallowed one
	assert bind.seed("org/t", ["acme/api"]) == []  # nothing is claimed as written
	assert bind.bindings() == {} and bind.of("acme/api") == ""
	assert bind.forget("acme/api")  # a reason, not an exception out of the curses wrapper


def test_the_cli_never_reads_a_flags_value_as_the_repo(monkeypatch, tmp_path):
	"""`bind --team org/mem` inside a repo bound the TEAM to itself, and reported success doing it."""
	from dashy import cli
	from dashy.core import team
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/api")  # the repo we are standing in
	monkeypatch.setattr(bind, "team_key", lambda: "org/mem")
	cli.bind(["gitdashy", "bind", "--team", "org/mem"])
	assert bind.bindings() == {"acme/api": "org/mem"}  # the cwd repo, not the flag's value
	# and an explicit positional still wins over the directory we happen to be in
	cli.bind(["gitdashy", "bind", "other/thing", "--team", "org/mem"])
	assert bind.of("other/thing") == "org/mem"


def _estate(monkeypatch, tmp_path):
	"""A team with facts and a brief, plus your own memory. Returns (mine, team memory)."""
	mine = tmp_path / "mine"
	mine.mkdir(parents=True)
	monkeypatch.setattr(config, "MEMORY_DIR", str(mine))
	shared = a_team(monkeypatch, tmp_path, "org/mem")
	(shared / "general.md").write_text("- the team reviews python with 4 spaces\n")
	(shared / "neomedsys__neo-api.md").write_text("- neo-api holds no DDL\n")
	return mine, shared


def test_an_unbound_repo_is_private(monkeypatch, tmp_path):
	"""What the whole feature is for: a side project is not told how somebody else's team reviews."""
	_estate(monkeypatch, tmp_path)
	bind.bind("neomedsys/neo-api", "org/mem")
	got = memory.read("neomedsys/neo-api")
	assert "4 spaces" in got and "no DDL" in got          # bound: the team's general AND repo facts
	assert [l for l, _ in memory.sources("neomedsys/neo-api")] == ["mine", "team org/mem"]

	assert memory.read("me/weekend-thing") == ""          # unbound: nothing of the team's, not even general
	assert [l for l, _ in memory.sources("me/weekend-thing")] == ["mine"]


def test_an_owner_rule_covers_every_repo_under_it(monkeypatch, tmp_path):
	"""~15 repos in one org is 15 commands and one more per new repo; a pattern is one line."""
	_estate(monkeypatch, tmp_path)
	assert bind.bind_owner("neomedsys", "org/mem") == ""
	for repo in ("neomedsys/neo-api", "neomedsys/nms-platform-v2", "neomedsys/a-repo-created-tomorrow"):
		assert bind.of(repo) == "org/mem", repo
	assert bind.owners() == {"neomedsys": "org/mem"} and bind.bindings() == {}  # covered by the rule, not a row
	assert bind.of("someone-else/tool") == ""            # the pattern covers one owner, not everything
	assert "4 spaces" in memory.read("neomedsys/nms-platform-v2")
	assert memory.read("someone-else/tool") == ""


def test_an_explicit_binding_beats_the_owner_rule():
	"""Explicit always wins over a pattern, in both directions."""
	bind.bind_owner("neomedsys", "org/mem")
	bind.bind("neomedsys/joint-venture", "org/other")
	assert bind.of("neomedsys/joint-venture") == "org/other"
	# and a repo in the org that is NOT the project can be excluded, which a pattern alone cannot express
	bind.forget("neomedsys/someones-fork")
	assert bind.of("neomedsys/someones-fork") == ""
	assert bind.of("neomedsys/neo-api") == "org/mem"     # the rule still covers the rest


def test_dropping_the_owner_rule_releases_the_repos_it_covered():
	bind.bind_owner("neomedsys", "org/mem")
	assert bind.of("neomedsys/neo-api") == "org/mem"
	assert bind.forget_owner("neomedsys/*") == ""        # the glob spelling is accepted too
	assert bind.of("neomedsys/neo-api") == "" and bind.owners() == {}


def test_seeding_does_not_pin_repos_an_owner_rule_already_covers():
	"""A redundant row would survive the rule's removal and pin repos nobody chose one by one."""
	bind.bind_owner("neomedsys", "org/mem")
	assert bind.seed("org/mem", ["neomedsys/neo-api", "other/thing"]) == ["other/thing"]
	assert bind.bindings() == {"other/thing": "org/mem"}


def test_an_unbound_repo_never_pools_or_offers_a_fact(monkeypatch, tmp_path):
	"""Disclosure is the sharper half: a fact about private work must not reach other people."""
	mine, _ = _estate(monkeypatch, tmp_path)
	bind.bind("neomedsys/neo-api", "org/mem")
	(mine / "me__weekend.md").write_text("- my side project uses bun\n")
	(mine / "neomedsys__neo-api.md").write_text("- worth telling the team\n")
	assert memory.team_visible("neomedsys/neo-api") and not memory.team_visible("me/weekend")
	assert ("me/weekend", "my side project uses bun") not in memory.shareable()
	assert ("neomedsys/neo-api", "worth telling the team") in memory.shareable()


def test_a_repo_bound_to_a_team_we_are_not_in_discloses_nothing(monkeypatch, tmp_path):
	"""The blocking defect: reads said not-ours, disclosure said ours, and the pool is the publishing half.

	bool(bind.of(repo)) was true for a binding to ANY team. Bound to org/other while in org/mem, the
	repo's NAME and its facts were written into org/mem's pool and offered on the share screen — to
	people with no claim on it — while sources() and brief() both reported it was not ours.
	"""
	mine, _ = _estate(monkeypatch, tmp_path)          # we are in org/mem
	bind.bind("acme/api", "org/other")                # bound somewhere else entirely
	assert [l for l, _ in memory.sources("acme/api")] == ["mine"]      # reads: not ours
	assert memory.brief("acme/api")[1] == "not in team org/other"
	assert not memory.team_visible("acme/api")                          # and so is disclosure

	memory.append("acme/api", "a fact about someone else's repo")
	memory.append("acme/api", "a fact about someone else's repo")       # promoted for me
	assert memory._facts(memory.path("acme/api")) == ["a fact about someone else's repo"]  # still mine
	assert not os.path.exists(memory.pool_path(memory.whoami(), "acme/api"))  # the name never left
	assert ("acme/api", "a fact about someone else's repo") not in memory.shareable()


def test_a_team_slug_is_matched_however_it_is_typed(monkeypatch, tmp_path):
	"""Repo keys fold because they are typed. A --team slug is typed too."""
	_estate(monkeypatch, tmp_path)                    # team.NAME == "org/mem"
	bind.bind("acme/api", "Org/Mem")
	assert bind.team_dir("Org/Mem")                   # not "not in team Org/Mem" about the team we are in
	assert [l for l, _ in memory.sources("acme/api")] == ["mine", "team Org/Mem"]
	assert memory.team_visible("acme/api")


def test_a_trimmed_url_is_refused_rather_than_truncated():
	"""team.slug_of keeps the LAST two segments of anything, so a hand-trimmed URL became 'pull/19' —
	a repo that does not exist — and bound it while reporting success."""
	assert bind.key("acme/api/pull/19") == ""
	assert bind.key("acme/api/tree/main") == ""
	assert bind.bind("acme/api/pull/19", "org/t") == "'acme/api/pull/19' is not an owner/name"
	assert bind.bindings() == {}
	# a real URL still resolves — the guard is only on the bare form
	assert bind.key("https://github.com/acme/api/") == "acme/api"
	assert bind.key("git@github.com:acme/api.git") == "acme/api"
	assert bind.key("/home/me/src/acme/api") == "acme/api"


def test_seeding_reads_the_store_once_however_many_repos(monkeypatch):
	"""seed() called of() per repo, and each of() is a full read. With the mirror registry folded in
	that is len(log) + len(registry) opens at startup, inside curses."""
	opens = []
	real = open
	monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opens.append(str(f)), real(f, *a, **k))[1])
	bind.seed("org/t", [f"acme/r{i}" for i in range(50)])
	reads = [f for f in opens if f == bind.BINDINGS]
	# 50 appends are unavoidable; the point is that the store is not RE-READ per repo
	assert len(bind.bindings()) == 50
	assert len(reads) <= 55, f"{len(reads)} opens of the store for 50 repos"


def test_a_caller_that_already_resolved_a_repo_is_believed(monkeypatch, tmp_path):
	"""brief(repo, slug) is the whole contract the draw path relies on: one resolver per frame, its
	answer handed in rather than looked up again."""
	mine, shared = _estate(monkeypatch, tmp_path)
	(mine / "project.md").write_text("Mine.\n")
	(shared / "project.md").write_text("The team's.\n")
	bind.bind("acme/api", "org/mem")
	assert memory.brief("acme/api") == ("The team's.", "team org/mem")     # looked up
	assert memory.brief("acme/api", "") == ("Mine.", "yours · acme/api is bound to no team")
	bind.forget("acme/api")
	assert memory.brief("acme/api", "org/mem") == ("The team's.", "team org/mem")  # the argument wins
