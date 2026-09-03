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
	summary, new = memory.dream("sonnet")
	assert calls[0][:2] == ["claude", "-p"] and "--model" in calls[0]
	assert "--safe-mode" in calls[0]  # dream has a JSON contract too, no ambient CLAUDE.md
	assert "### mine/general.md" in calls[0][2] and "### team/general.md" in calls[0][2]
	assert "never move a line from mine/ into team/" in calls[0][2].lower() or "never move" in calls[0][2]
	assert summary == "tidied"
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
