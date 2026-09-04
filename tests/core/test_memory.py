import os
import subprocess

import pytest

from dashy import config
from dashy.core import memory, team

from conftest import claude_out


def facts(p):
	return [l.strip() for l in open(p).read().splitlines() if l.strip()]


def in_a_team(monkeypatch, tmp_path):
	"""mine/ and team/memory/ side by side, with team mode on. Returns (mine, team memory)."""
	mine, shared = tmp_path / "mine", tmp_path / "team" / "memory"
	mine.mkdir(parents=True, exist_ok=True)
	shared.mkdir(parents=True, exist_ok=True)
	monkeypatch.setattr(config, "MEMORY_DIR", str(mine))
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "team"))
	monkeypatch.setattr(team, "on", lambda: True)
	monkeypatch.setattr(team, "NAME", "org/t")
	return mine, shared


def test_a_fact_takes_two_independent_reviews(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	assert memory.append("a/b", "CI skips the DB tests") == []
	assert memory.drafts("a/b") == [(1, "CI skips the DB tests")]
	assert not os.path.exists(memory.path("a/b"))  # one review does not make a fact
	assert memory.read("a/b") == ""  # and a draft is never read back, or it would confirm itself
	assert memory.append("a/b", "ci skips the db tests") == ["CI skips the DB tests"]
	assert memory.drafts("a/b") == []  # it left the queue
	assert facts(memory.path("a/b")) == ["- CI skips the DB tests"]  # the first wording is the one kept
	assert "CI skips the DB tests" in memory.read("a/b")


def test_near_wordings_are_one_fact_and_distinct_ones_are_not(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	memory.append("a/b", "the frontend is a thin display layer")
	memory.append("a/b", "The frontend is a thin display layer.")
	assert facts(memory.path("a/b")) == ["- the frontend is a thin display layer"]
	memory.append("a/b", "migrations run before deploy")
	assert memory.drafts("a/b") == [(1, "migrations run before deploy")]


def test_an_already_settled_fact_is_dropped_on_arrival(monkeypatch, tmp_path):
	mine, shared = in_a_team(monkeypatch, tmp_path)
	(mine / "general.md").write_text("- uses tabs everywhere\n")
	(shared / "a__b.md").write_text("- the API owns all validation\n")
	assert memory.append("a/b", "uses tabs everywhere\nthe API owns all validation") == []
	assert memory.drafts("a/b") == []  # not even a draft: proposing what is settled says nothing


def test_reads_merge_both_sources_and_never_drafts(monkeypatch, tmp_path):
	mine, shared = in_a_team(monkeypatch, tmp_path)
	(mine / "a__b.md").write_text("- mine about a/b\n")
	(shared / "a__b.md").write_text("- team about a/b\n")
	(shared / "general.md").write_text("- team general\n")
	memory.append("a/b", "a draft nobody confirmed")
	out = memory.read("a/b")
	assert "mine about a/b" in out and "team about a/b" in out and "team general" in out
	assert "### mine" in out and "### team org/t" in out  # a review can tell whose fact it is reading
	assert "draft nobody confirmed" not in out


def test_shareable_is_what_the_team_lacks_and_share_closes_the_gap(monkeypatch, tmp_path):
	mine, shared = in_a_team(monkeypatch, tmp_path)
	(mine / "a__b.md").write_text("- only mine\n- both have this\n")
	(shared / "a__b.md").write_text("- both have this\n")
	assert memory.shareable() == [("a/b", "only mine")]
	memory.share("a/b", "only mine")
	assert memory.shareable() == []
	assert facts(shared / "a__b.md") == ["- both have this", "- only mine"]


def test_forget_drops_one_fact_of_yours(monkeypatch, tmp_path):
	mine, _ = in_a_team(monkeypatch, tmp_path)
	(mine / "a__b.md").write_text("- keep me\n- drop me\n")
	memory.forget("a/b", "drop me")
	assert facts(mine / "a__b.md") == ["- keep me"]
	memory.forget("a/b", "keep me")
	assert not (mine / "a__b.md").exists()  # an empty memory file is removed, not left blank


def test_shareable_is_empty_when_you_are_alone(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "a__b.md").write_text("- mine alone\n")
	assert memory.shareable() == []  # nobody to share with


def test_dream_keys_name_their_source_and_write_lands_in_it(monkeypatch, tmp_path):
	mine, shared = in_a_team(monkeypatch, tmp_path)
	(mine / "general.md").write_text("- run make lint\n")
	(mine / "a__b.md").write_text("- uses tabs\n")
	(shared / "general.md").write_text("- stale team line\n")
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return claude_out(summary="tidied", files={"mine/a__b.md": "- uses tabs", "team/general.md": "",
		                                           "bogus.md": "- nope"})
	monkeypatch.setattr(subprocess, "run", fake_run)
	summary, _before, new = memory.dream("sonnet")
	assert calls[0][:2] == ["claude", "-p"] and "--model" in calls[0]
	assert "--safe-mode" in calls[0]  # dream has a JSON contract too, no ambient CLAUDE.md
	assert "### mine/general.md" in calls[0][2] and "### team/general.md" in calls[0][2]
	assert "never move a line from mine/ into team/" in calls[0][2].lower() or "never move" in calls[0][2]
	assert summary.startswith("tidied")
	assert "ignored bogus.md" in summary  # a dropped edit is reported, not silently discarded
	assert set(new) == {"mine/general.md", "mine/a__b.md", "team/general.md"}
	assert new["mine/general.md"] == "- run make lint\n"  # untouched files keep what they had
	memory.write(new)
	assert facts(mine / "a__b.md") == ["- uses tabs"]
	assert not (shared / "general.md").exists()  # empty content deletes
	assert not (mine / "bogus.md").exists() and not (shared / "bogus.md").exists()


def test_dream_never_writes_a_team_file_when_you_are_not_in_one(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "general.md").write_text("- solo\n")
	memory.write({"team/general.md": "- should not appear"})
	assert not (tmp_path / "general.md").read_text().startswith("- should not appear")
	assert not os.path.exists(os.path.join(config.TEAM, "memory", "general.md"))


def test_dream_with_no_memory_raises(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "none"))
	with pytest.raises(ValueError):
		memory.dream("opus")


def logged(tmp_path, *repos):
	"""Put repos into the shared review log — pooling only covers names the team can already see."""
	from dashy.core import log
	with open(log.LOG, "w") as f:
		for r in repos:
			f.write('{"pr": {"repository": {"nameWithOwner": "%s"}}}\n' % r)


def test_a_promoted_fact_is_pooled_only_for_a_repo_the_team_can_already_see(monkeypatch, tmp_path):
	mine, _ = in_a_team(monkeypatch, tmp_path)
	logged(tmp_path, "a/b")
	memory.append("a/b", "CI skips the DB tests")
	memory.append("a/b", "CI skips the DB tests")  # promoted
	memory.append("secret/side", "my weekend project uses bun")
	memory.append("secret/side", "my weekend project uses bun")  # promoted, but never reviewed by the team
	me = memory.whoami()
	assert facts(memory.pool_path(me, "a/b")) == ["- CI skips the DB tests"]
	assert not os.path.exists(memory.pool_path(me, "secret/side"))  # the repo name never leaves
	assert facts(memory.path("secret/side")) == ["- my weekend project uses bun"]  # still yours


def test_a_draft_is_never_pooled(monkeypatch, tmp_path):
	in_a_team(monkeypatch, tmp_path)
	logged(tmp_path, "a/b")
	memory.append("a/b", "only one review said this")
	assert not os.path.exists(memory.pool_path(memory.whoami(), "a/b"))  # evidence means accepted, not proposed


def test_backers_counts_people_not_reviews(monkeypatch, tmp_path):
	mine, _ = in_a_team(monkeypatch, tmp_path)
	logged(tmp_path, "a/b")
	memory.append("a/b", "the API owns all validation")
	memory.append("a/b", "the API owns all validation")
	# a teammate's checkout brings their own pool along
	mate = os.path.join(config.TEAM, "memory", memory.POOL, "martin")
	os.makedirs(mate)
	open(os.path.join(mate, "a__b.md"), "w").write("- The API owns all validation.\n")  # reworded
	index = memory.pools()
	assert memory.backers(index, "a/b", "the API owns all validation") == sorted([memory.whoami(), "martin"])
	assert memory.backers(index, "a/b", "something nobody said") == []


def test_sharing_and_forgetting_withdraw_the_evidence(monkeypatch, tmp_path):
	mine, shared = in_a_team(monkeypatch, tmp_path)
	logged(tmp_path, "a/b")
	memory.append("a/b", "keep this one")
	memory.append("a/b", "keep this one")
	me = memory.whoami()
	assert os.path.exists(memory.pool_path(me, "a/b"))
	memory.share("a/b", "keep this one")
	assert not os.path.exists(memory.pool_path(me, "a/b"))  # it is team memory now, not evidence
	memory.append("a/b", "drop this one")
	memory.append("a/b", "drop this one")
	memory.forget("a/b", "drop this one")
	assert not os.path.exists(memory.pool_path(me, "a/b"))  # withdrawn when you no longer accept it


def test_the_pool_is_never_read_into_a_prompt(monkeypatch, tmp_path):
	mine, _ = in_a_team(monkeypatch, tmp_path)
	mate = os.path.join(config.TEAM, "memory", memory.POOL, "martin")
	os.makedirs(mate)
	open(os.path.join(mate, "a__b.md"), "w").write("- something only martin accepted\n")
	assert "martin accepted" not in memory.read("a/b")
	assert not any("pool" in k for k in memory.files())  # nor into the dream


def test_a_repo_the_team_holds_memory_for_is_visible_even_if_never_reviewed(monkeypatch, tmp_path):
	"""A repo you only ever code in still corroborates, as long as the team already has facts for it."""
	mine, shared = in_a_team(monkeypatch, tmp_path)
	logged(tmp_path)  # nothing in the review log at all
	assert not memory.team_visible("a/b")
	(shared / "a__b.md").write_text("- the team already knows this repo\n")
	assert memory.team_visible("a/b")
	memory.append("a/b", "coding taught me this")
	memory.append("a/b", "coding taught me this")
	assert facts(memory.pool_path(memory.whoami(), "a/b")) == ["- coding taught me this"]


def test_a_general_fact_always_pools_since_it_names_no_repo(monkeypatch, tmp_path):
	in_a_team(monkeypatch, tmp_path)
	logged(tmp_path)
	assert memory.team_visible(None)
	memory.append(None, "PHI reaches the frontend")
	memory.append(None, "PHI reaches the frontend")
	assert facts(memory.pool_path(memory.whoami(), None)) == ["- PHI reaches the frontend"]


def test_nothing_pools_when_you_are_not_in_a_team(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	assert not memory.team_visible(None) and not memory.team_visible("a/b")


def test_one_review_cannot_confirm_its_own_fact(monkeypatch, tmp_path):
	"""Two wordings of one thing in a single call must count once, or the gate gates nothing."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	assert memory.append("a/b", "- the API owns all validation\n- The API owns all validation.") == []
	assert memory.drafts("a/b") == [(1, "the API owns all validation")]
	assert not os.path.exists(memory.path("a/b"))
	assert memory.append("a/b", "the API owns all validation") == ["the API owns all validation"]


def test_one_review_repeating_itself_pools_nothing(monkeypatch, tmp_path):
	in_a_team(monkeypatch, tmp_path)
	logged(tmp_path, "a/b")
	memory.append("a/b", "- CI skips the DB tests\n- ci skips the db tests\n- CI skips the DB tests!")
	assert not os.path.exists(memory.pool_path(memory.whoami(), "a/b"))  # nothing corroborated anything


def test_one_wrong_word_is_a_different_fact(monkeypatch, tmp_path):
	"""Character similarity called these the same at 0.886. On tokens they are not."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	memory.append("a/b", "CI reports skipping for format-check")
	memory.append("a/b", "CI reports skipping for type-check")
	assert sorted(t for _, t in memory.drafts("a/b")) == [
		"CI reports skipping for format-check", "CI reports skipping for type-check"]
	assert not os.path.exists(memory.path("a/b"))  # neither confirmed the other
	memory.append("a/b", "Tests live in tests/core")
	memory.append("a/b", "Tests live in tests/ui")
	assert len(memory.drafts("a/b")) == 4


def test_a_refinement_is_a_new_fact_but_a_rewording_is_not(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	memory.append("a/b", "The auth module owns session state")
	memory.append("a/b", "The auth module owns session state, not the API layer")
	assert len(memory.drafts("a/b")) == 2  # a refinement says more; it is not the same claim
	memory.append("c/d", "neoservo owns training dispatch")
	memory.append("c/d", "Neoservo owns the training dispatch.")
	assert facts(memory.path("c/d")) == ["- neoservo owns training dispatch"]  # a rewording still matches


def test_forget_removes_exactly_one_line_not_its_neighbour(monkeypatch, tmp_path):
	mine, _ = in_a_team(monkeypatch, tmp_path)
	(mine / "a__b.md").write_text("- CI reports skipping for format-check\n- CI reports skipping for type-check\n")
	memory.forget("a/b", "CI reports skipping for format-check")
	assert facts(mine / "a__b.md") == ["- CI reports skipping for type-check"]


def test_an_unreadable_memory_file_is_not_silently_empty(monkeypatch, tmp_path):
	"""A review with no memory at all, because of a permission error, must not look like a review with none."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	p = tmp_path / "general.md"
	p.write_text("- a fact\n")
	os.chmod(p, 0o000)
	try:
		if os.access(p, os.R_OK):
			pytest.skip("running as root; permissions are not enforced")
		with pytest.raises(PermissionError):
			memory.read("a/b")
	finally:
		os.chmod(p, 0o644)


def test_a_dream_that_forgets_the_prefix_changes_nothing_and_says_so(monkeypatch, tmp_path):
	"""The model must echo mine/ or team/ back. Without it we cannot tell which file it meant."""
	mine, _ = in_a_team(monkeypatch, tmp_path)
	(mine / "general.md").write_text("- one\n- two\n")
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: claude_out(
		summary="merged", files={"general.md": "- one"}))  # no prefix: the pre-PR format
	summary, _before, new = memory.dream("sonnet")
	assert new == {"mine/general.md": "- one\n- two\n"}  # unchanged, not applied
	assert "ignored general.md" in summary
	memory.write(new)
	assert facts(mine / "general.md") == ["- one", "- two"]
	assert "mine/<file>" in memory.DREAM or 'mine/general.md' in memory.DREAM  # and the prompt says so


def test_a_dream_cannot_write_outside_the_memory_dir(monkeypatch, tmp_path):
	mine, _ = in_a_team(monkeypatch, tmp_path)
	memory.write({"mine/../../escaped.md": "- nope"})
	assert not (tmp_path / "escaped.md").exists()
	assert not (tmp_path.parent / "escaped.md").exists()


def test_dream_hands_back_what_it_saw(monkeypatch, tmp_path):
	"""A review can promote a fact during a ten-minute dream; the diff must be against what it read."""
	mine, _ = in_a_team(monkeypatch, tmp_path)
	(mine / "general.md").write_text("- one\n")
	def fake_run(cmd, **kw):
		(mine / "general.md").write_text("- one\n- promoted while dreaming\n")  # a review lands mid-dream
		return claude_out(summary="tidied", files={"mine/general.md": "- one"})
	monkeypatch.setattr(subprocess, "run", fake_run)
	summary, before, new = memory.dream("sonnet")
	assert before == {"mine/general.md": "- one\n"}  # what the model actually read, not what is there now
	assert new == {"mine/general.md": "- one"}

def test_the_team_brief_is_declared_not_learned(monkeypatch, tmp_path):
	"""project.md is what the team says the work is for. The pipeline must never touch it."""
	mine, shared = in_a_team(monkeypatch, tmp_path)
	(shared / "project.md").write_text("# What we are building\n\nA thing, for someone.\n")
	(shared / "general.md").write_text("- a learned fact\n")
	assert "A thing, for someone" in memory.project()
	assert "A thing" not in memory.read("a/b")          # not a fact, so not in the memory block
	assert "project.md" not in " ".join(memory.files())  # the dream tidies facts, not a brief
	assert memory.shareable() == []                     # and it is never offered for sharing
	memory.append("a/b", "A thing, for someone")
	memory.append("a/b", "A thing, for someone")
	assert (shared / "project.md").read_text().startswith("# What we are building")  # untouched


def test_no_team_means_no_brief(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	assert memory.project() == ""


def test_a_brief_belongs_to_whoever_wrote_it(monkeypatch, tmp_path):
	"""It used to be the team's alone, which left anyone working solo with nowhere to put it."""
	mine, shared = in_a_team(monkeypatch, tmp_path)
	(mine / "project.md").write_text("A tool for one person.\n")
	assert memory.project() == "### mine\nA tool for one person."
	(shared / "project.md").write_text("What we build together.\n")
	both = memory.project()
	assert "### mine" in both and "### team org/t" in both and both.index("mine") < both.index("team")
	assert memory.shareable() == []          # still never offered for sharing
	assert not any("project" in k for k in memory.files())  # and still not dreamt over


def test_a_solo_brief_reaches_a_review_with_no_team_at_all(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "project.md").write_text("Just me, building a thing.\n")
	assert memory.project() == "### mine\nJust me, building a thing."


def _mem(monkeypatch, tmp_path):
	d = tmp_path / "mem"
	d.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(d))
	monkeypatch.setattr(config, "TEAM", "")
	return d


def test_a_pre_review_alone_never_becomes_a_fact(monkeypatch, tmp_path):
	"""The pre-review and the real review are the same model on the same diff.

	If a pre-review could promote by itself, or by being run twice, PROMOTE_AT would measure how often
	you pre-reviewed rather than whether the fact recurred.
	"""
	_mem(monkeypatch, tmp_path)
	memory.append_self("acme/api", "the api owns no DDL")
	memory.append_self("acme/api", "the api owns no DDL")   # again, and again
	memory.append_self("acme/api", "the api owns no DDL")
	assert memory.known("acme/api") == []                    # never a fact
	assert [t for _n, t in memory.self_drafts("acme/api")] == ["the api owns no DDL"]
	assert memory.drafts("acme/api") == []                   # and not in the real queue either


def test_a_real_review_agreeing_with_a_pre_review_promotes(monkeypatch, tmp_path):
	"""Two runs, one of which did not know the other existed. That is the bar."""
	_mem(monkeypatch, tmp_path)
	memory.append_self("acme/api", "the api owns no DDL")
	promoted = memory.append("acme/api", "the api owns no DDL")
	assert promoted == ["the api owns no DDL"]
	assert "the api owns no DDL" in memory.known("acme/api")
	assert memory.self_drafts("acme/api") == []              # consumed, not left to pay out again


def test_a_spent_pre_review_finding_cannot_pay_out_twice(monkeypatch, tmp_path):
	"""One pre-review must not keep contributing to fact after fact."""
	_mem(monkeypatch, tmp_path)
	memory.append_self("acme/api", "the api owns no DDL")
	memory.append("acme/api", "the api owns no DDL")          # consumes it
	memory.forget("acme/api", "the api owns no DDL")          # start over
	assert memory.append("acme/api", "the api owns no DDL") == []   # one observation again, not two
	assert [n for n, _t in memory.drafts("acme/api")] == [1]


def test_self_drafts_never_reach_a_prompt_or_the_dream(monkeypatch, tmp_path):
	"""Same rule as drafts, same reason — a reviewer must not meet its own guess as evidence."""
	d = _mem(monkeypatch, tmp_path)
	(d / "acme__api.md").write_text("- a real fact\n")
	memory.append_self("acme/api", "a pre-review guess")

	assert "pre-review guess" not in memory.read("acme/api")
	assert "pre-review guess" not in memory.scope_text("acme/api")
	assert "pre-review guess" not in memory.scope_text()
	assert not any("pre-review guess" in t for t in memory.files().values())   # the dream cannot rewrite it
	assert "a real fact" in memory.read("acme/api")                            # and facts still flow


def test_a_pre_review_does_not_repeat_what_is_already_known(monkeypatch, tmp_path):
	"""Proposing a settled fact says nothing new, and would sit in the pool forever."""
	d = _mem(monkeypatch, tmp_path)
	(d / "acme__api.md").write_text("- the api owns no DDL\n")
	assert memory.append_self("acme/api", "the api owns no DDL") == []
	assert memory.self_drafts("acme/api") == []


def test_the_dream_prompt_says_what_a_general_file_is_for():
	"""The ground cause of a real data loss: the keep-list described repo structure, and general.md
	holds reviewer discipline — a category it never named. The model dropped the file whose contents
	the prompt failed to describe, using the delete mechanism the same prompt had just taught it.
	"""
	d = memory.DREAM
	assert "how reviews are conducted here" in d          # the category that was missing
	assert "what blocks and what does not" in d
	assert "EXPECTED to hold lines that name no repo" in d  # said plainly, not implied
	# and deletion is described as destructive rather than as a tidy mechanism
	assert "DELETES it and everything in it" in d
	assert "never merely because the file does not match a category above" in d


def test_push_dir_says_why_it_did_nothing(monkeypatch, tmp_path):
	"""It returned None on every path, so a failed commit and an unneeded one looked identical.

	That is why a dream's deletion sat uncommitted for two hours and then rode into an unrelated
	review's commit, under that review's message.
	"""
	import subprocess as sp
	d = tmp_path / "mem"
	d.mkdir()
	assert team.push_dir(str(d), "x", "mine") == "not a git checkout"

	sp.run(["git", "init", "-q", str(d)], check=True)
	sp.run(["git", "-C", str(d), "config", "user.email", "t@t"], check=True)
	sp.run(["git", "-C", str(d), "config", "user.name", "t"], check=True)
	assert team.push_dir(str(d), "nothing staged", "mine") == ""     # nothing to do is not an error

	(d / "general.md").write_text("- a fact\n")
	assert team.push_dir(str(d), "memory: real", "mine") == ""       # committed, no remote, fine
	got = sp.run(["git", "-C", str(d), "log", "--oneline"], capture_output=True, text=True)
	assert "memory: real" in got.stdout

	# a commit that cannot happen reports, instead of returning the same None as success
	(d / "general.md").write_text("- another\n")
	def fake(*a, **k):
		# ponytail: "diff --cached --quiet" exits 1 when something IS staged — 0 would mean nothing to
		# commit and push_dir would return "" for the right reason, testing nothing.
		if "diff" in a or "commit" in a:
			return sp.CompletedProcess(a, 1, "", "boom")
		return sp.CompletedProcess(a, 0, "", "")
	monkeypatch.setattr(team, "_git", fake)
	err = team.push_dir(str(d), "memory: doomed", "mine")
	assert err and err != "", f"a failed commit must explain itself, got {err!r}"


def test_push_reports_a_team_failure_but_not_the_absence_of_a_team(monkeypatch, tmp_path):
	"""A dream rewrites both sources, so the team push has to be checked too — and NOT being in a team
	is the normal state, not a failure. Returning an error there made the dream warn on every run on a
	machine with no team, which is a warning nobody reads twice.
	"""
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "no-team"))
	assert not team.on()
	assert team.push("x") == "", "no team is not a failure"

	import subprocess as sp
	d = tmp_path / "team"
	(d / "memory").mkdir(parents=True)
	sp.run(["git", "init", "-q", str(d)], check=True)
	sp.run(["git", "-C", str(d), "config", "user.email", "t@t"], check=True)
	sp.run(["git", "-C", str(d), "config", "user.name", "t"], check=True)
	monkeypatch.setattr(config, "TEAM", str(d))
	assert team.on()
	(d / "memory" / "general.md").write_text("- shared\n")
	assert team.push("memory: real") == ""            # a real team commits, and says nothing

	def fake(*a, **k):
		if "diff" in a or "commit" in a:
			return sp.CompletedProcess(a, 1, "", "boom")
		return sp.CompletedProcess(a, 0, "", "")
	(d / "memory" / "general.md").write_text("- changed\n")
	monkeypatch.setattr(team, "_git", fake)
	assert team.push("memory: doomed"), "a team commit that fails must explain itself"
