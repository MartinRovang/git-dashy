import pytest

from dashy.ui.rows import age, rows

from conftest import PR


def test_age():
	assert age("2020-01-01T00:00:00Z").endswith("d")
	from datetime import datetime, timezone, timedelta
	now = datetime.now(timezone.utc)
	assert age((now - timedelta(hours=3)).isoformat()) == "3h"
	assert age((now - timedelta(minutes=5)).isoformat()) == "5m"
	assert age(now.isoformat()) == "now"


def test_rows_layout_and_section_tagging():
	p = dict(PR)
	rs = rows([("MINE", [p], None), ("REVIEW REQUESTED", [], None), ("ASSIGNED", None, "boom\nmore")])
	# your own PRs get their own section; the rest are queues, under one heading
	# queues keep their section order: an empty one collapses in place, a live one opens where it sits
	assert [k for k, _ in rs] == ["cols", "head", "pr", "blank", "head", "queue", "head", "err", "blank"]
	assert rs[1][1] == ("MINE", "1 open", "")
	assert rs[4][1] == ("QUEUES", "", "")  # something is live, so no "nothing waiting on you"
	assert rs[5][1] == ("review requested", "0", "none")
	assert rs[6][1] == ("assigned", "!", "") and rs[7][1] == "boom"
	assert p["section"] == "MINE"


# ---- fetch ----
def test_rows_reviewed_window_filters_but_keeps_summaries():
	from datetime import datetime, timezone, timedelta
	now = datetime.now(timezone.utc)
	def ent(url, hours):
		e = {"at": (now - timedelta(hours=hours)).isoformat(), "model": "opus", "verdict": "approve",
		     "summary": "sum " + url, "body": "", "pr": dict(PR, url=url)}
		return {**e["pr"], "review": e, "status": "✓ approved", "updatedAt": e["at"]}
	secs = [("REVIEW REQUESTED", [dict(PR, url="old")], None), ("REVIEWED", [ent("new", 0.5), ent("old", 5)], None)]
	rs = rows(secs, window=4)
	heads = [pl for k, pl in rs if k == "head"]
	assert ("review requested", "1", "") in heads and ("sub", "sum old") in rs  # summary survives the window
	assert ("reviewed", "1", "") in heads and [p["url"] for k, p in rs if k == "pr"] == ["old", "new"]
	assert ("reviewed", "2", "") in [pl for k, pl in rows(secs, window=None) if k == "head"]
	tight = rows(secs, window=1)
	assert ("reviewed", "1", "") in [pl for k, pl in tight if k == "head"]
	assert ("queue", ("reviewed", "0", "none in the last 1h")) not in tight


def test_rows_subs_modes():
	from datetime import datetime, timezone
	e = {"at": datetime.now(timezone.utc).isoformat(), "model": "opus", "verdict": "approve", "summary": "s", "body": "", "pr": dict(PR)}
	rv = {**e["pr"], "review": e, "status": "✓ approved", "updatedAt": e["at"]}
	secs = [("REVIEW REQUESTED", [dict(PR)], None), ("REVIEWED", [rv], None)]
	count = lambda mode: [k for k, _ in rows(secs, subs=mode)].count("sub")
	assert count("all") == 2 and count("open") == 1 and count("off") == 0


LS_REMOTE = ("abc\trefs/tags/v0.9.0\n" "def\trefs/tags/v1.10.0\n" "fed\trefs/tags/v1.2.0\n")


def test_rows_drafts_filter():
	p, d = dict(PR), {**PR, "url": "d", "isDraft": True}
	secs = [("MINE", [p, d], None)]
	assert [x["url"] for k, x in rows(secs) if k == "pr"] == ["u", "d"]
	assert [x["url"] for k, x in rows(secs, drafts=False) if k == "pr"] == ["u"]
	assert ("head", ("MINE", "1 open", "")) in rows(secs, drafts=False)


def test_rows_reviewed_stacks_rereviews_and_unfolds():
	from datetime import datetime, timezone, timedelta
	now = datetime.now(timezone.utc)
	def ent(url, hours):
		e = {"at": (now - timedelta(hours=hours)).isoformat(), "model": "opus", "verdict": "approve",
		     "summary": "sum " + str(hours), "body": "", "pr": dict(PR, url=url)}
		return {**e["pr"], "review": e, "status": "✓ approved", "updatedAt": e["at"]}
	secs = [("REVIEWED", [ent("a", 1), ent("b", 2), ent("a", 3)], None)]
	rs = rows(secs)
	prs = [p for k, p in rs if k == "pr"]
	assert [p["url"] for p in prs] == ["a", "b"] and prs[0]["more"] == 1 and prs[1]["more"] == 0
	assert ("head", ("reviewed", "2", "")) in rs and ("sub", "sum 1") in rs and ("sub", "sum 3") not in rs
	rs = rows(secs, expanded={"a"})
	prs = [p for k, p in rs if k == "pr"]
	assert [(p["url"], p.get("child", False)) for p in prs] == [("a", False), ("a", True), ("b", False)]
	assert prs[0]["more"] == 0 and prs[0]["open"] and ("sub", "sum 3") in rs


def test_note_says_what_your_own_prs_are_asking_of_you():
	from dashy.ui.rows import note
	assert note([]) == "" and note(None) == ""
	assert note([{"status": "✓ approved"}]) == ""  # nothing is asking anything
	assert note([{"status": "✗ changes requested"}, {"status": "✗ changes requested"},
	             {"status": "· awaiting review"}]) == "2 need work · 1 waiting"
	assert note([{"status": "↻ re-review requested"}]) == "1 waiting"
	assert note([{}]) == ""  # a PR with no status at all


def test_an_empty_queue_collapses_but_a_live_one_opens():
	from dashy.ui.rows import rows as R
	empty = R([("MINE", [], None), ("REVIEW REQUESTED", [], None), ("ASSIGNED", [], None), ("REVIEWED", [], None)])
	assert [k for k, _ in empty] == ["cols", "head", "empty", "blank", "head", "queue", "queue", "queue"]
	assert ("head", ("QUEUES", "", "nothing waiting on you")) in empty
