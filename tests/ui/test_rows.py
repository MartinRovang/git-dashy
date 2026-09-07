
from dashy.core import bind
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


def test_rows_draft_under_review_is_exempt_from_the_filter():
	p, d = dict(PR), {**PR, "url": "d", "isDraft": True}
	secs = [("MINE", [p, d], None)]
	shown = lambda busy: [x["url"] for k, x in rows(secs, drafts=False, busy=busy) if k == "pr"]
	assert shown(set()) == ["u"]
	assert shown({"d"}) == ["u", "d"]
	assert shown(set()) == ["u"], "the exemption is in-flight only, it does not stick"


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


def _pr(repo, n):
	return {"repository": {"nameWithOwner": repo, "name": repo.split("/")[-1]}, "number": n,
	        "url": f"u{n}", "title": "T", "isDraft": False, "author": {"login": "me"},
	        "updatedAt": "2020-01-01T00:00:00Z"}


def test_rows_separate_a_section_by_team():
	"""Which team a repo's reviews use was invisible until you opened the pane on every single row."""
	bind.bind_owner("neomedsys", "neomedsys/review-memory")
	bind.bind("acme/tool", "acme/mem")
	prs = [_pr("me/weekend", 1), _pr("neomedsys/neo-api", 2), _pr("acme/tool", 3),
	       _pr("neomedsys/nms-platform-v2", 4)]
	out = rows([("MINE", prs, None)])
	groups = [p for k, p in out if k == "group"]
	assert groups == ["acme/mem", "neomedsys/review-memory", "not bound to a team"]
	# each PR sits under its own team's separator, and unbound is the last pile
	order = [p if k == "group" else p["repository"]["nameWithOwner"] for k, p in out if k in ("group", "pr")]
	assert order == ["acme/mem", "acme/tool",
	                 "neomedsys/review-memory", "neomedsys/neo-api", "neomedsys/nms-platform-v2",
	                 "not bound to a team", "me/weekend"]


def test_one_team_gets_no_separator():
	"""A separator above a single group labels what the whole list already is."""
	bind.bind_owner("neomedsys", "neomedsys/review-memory")
	out = rows([("MINE", [_pr("neomedsys/neo-api", 2), _pr("neomedsys/neo-access", 3)], None)])
	assert not [p for k, p in out if k == "group"]
	out = rows([("MINE", [_pr("me/a", 1), _pr("me/b", 2)], None)])
	assert not [p for k, p in out if k == "group"]  # and none when nothing is bound at all


def test_a_group_rule_is_never_written_zero_wide():
	"""ncurses treats n=0 as a no-op, so a real terminal shrugs and it stays invisible. FakeScr asserts
	n >= 1, which is the bound every other write in screen.py honours via max(1, ...)."""
	import sys, time
	sys.path.insert(0, "tests")
	from conftest import FakeScr
	from dashy.core.state import State
	from dashy.ui import screen as ui
	ui.C = lambda n: 0
	bind.bind_owner("neomedsys", "neomedsys/review-memory")
	prs = [_pr("neomedsys/neo-api", 1), _pr("me/weekend", 2)]
	st = State(60)
	st.sections, st.fetched_at = [("MINE", prs, None)], time.time()
	for w in range(8, 130):
		for h in (5, 8, 12, 24, 40):
			scr = FakeScr(h=h, w=w)
			ui.draw(scr, st, 0, now=1000.0)  # must not raise at any width
