import pytest

from dashy.rows import age, rows

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
	assert [k for k, _ in rs] == ["head", "pr", "blank", "head", "empty", "blank", "head", "err", "blank"]
	assert rs[0][1] == "MINE (1)" and rs[6][1] == "ASSIGNED (!)" and rs[7][1] == "boom"
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
	assert rs[0][1] == "REVIEW REQUESTED (1)" and rs[2] == ("sub", "sum old")  # summary survives the window
	assert ("head", "REVIEWED · last 4h (1)") in rs and [p["url"] for k, p in rs if k == "pr"] == ["old", "new"]
	assert ("head", "REVIEWED (2)") in rows(secs, window=None)
	assert ("head", "REVIEWED · last 1h (0)") not in rows(secs, window=1) and ("head", "REVIEWED · last 1h (1)") in rows(secs, window=1)


def test_rows_subs_modes():
	from datetime import datetime, timezone
	e = {"at": datetime.now(timezone.utc).isoformat(), "model": "opus", "verdict": "approve", "summary": "s", "body": "", "pr": dict(PR)}
	rv = {**e["pr"], "review": e, "status": "✓ approved", "updatedAt": e["at"]}
	secs = [("REVIEW REQUESTED", [dict(PR)], None), ("REVIEWED", [rv], None)]
	count = lambda mode: [k for k, _ in rows(secs, subs=mode)].count("sub")
	assert count("all") == 2 and count("open") == 1 and count("off") == 0


LS_REMOTE = ("abc\trefs/tags/v0.9.0\n" "def\trefs/tags/v1.10.0\n" "fed\trefs/tags/v1.2.0\n")
