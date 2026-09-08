"""The PR diff, parsed, with the review's findings anchored to the lines they name."""
import pytest
import subprocess

from dashy.core import diff

DIFF = """diff --git a/gitdashy/auto.py b/gitdashy/auto.py
index 1111111..2222222 100644
--- a/gitdashy/auto.py
+++ b/gitdashy/auto.py
@@ -136,7 +136,12 @@ class Auto:
     def _sweep(self, prs):
         for pr in prs:
-            self.in_flight.discard(pr.id)
+            self.in_flight.discard(pr.id)
+            verdict = self._verdict_for(pr)
+            if verdict is None:
+                continue
             self.persist_verdict(pr, verdict)
diff --git a/CHANGELOG.md b/CHANGELOG.md
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,4 +1,6 @@
 # Changelog
+## 1.34.0
+- auto: keep the verdict when a tick lands mid-sweep
 ## 1.33.2
"""


def test_a_hunk_numbers_lines_the_way_a_review_cites_them():
	"""A finding says auto.py:141 meaning the file as it WILL be, so `n` is the new-side number."""
	files = diff.parse(DIFF)
	assert [f["path"] for f in files] == ["gitdashy/auto.py", "CHANGELOG.md"]
	auto = files[0]
	assert (auto["add"], auto["dele"]) == (4, 1)
	lines = auto["hunks"][0]["lines"]
	assert [(l["n"], l["sign"]) for l in lines] == [
		(136, " "), (137, " "), (138, "-"), (138, "+"), (139, "+"), (140, "+"), (141, "+"), (142, " ")]
	# a removed line carries the position it sits at and does not consume a new-side number
	assert lines[2]["del"] and not lines[3]["del"]
	assert lines[2]["text"] == "            self.in_flight.discard(pr.id)"


def test_a_finding_lands_on_the_line_it_names():
	files = diff.parse(DIFF)
	marks = diff.anchor(files, [
		{"kind": "blocking", "loc": "auto.py:139", "text": "verdict dropped mid-sweep"},
		{"kind": "nit", "loc": "CHANGELOG.md", "text": "entry missing the version bump"},
	])
	assert [m["kind"] for m in marks] == ["blocking", "nit"]
	assert marks[0]["file"] == 0
	line = next(l for l in files[0]["hunks"][0]["lines"] if l["n"] == 139 and not l["del"])
	assert line["marks"] == [marks[0]]
	assert diff.worst(line) == "blocking"
	# a basename matches a full path, because a review cites either
	assert marks[0]["path"] == "auto.py"


def test_a_finding_about_a_file_the_diff_does_not_touch_is_kept():
	"""A review's most important line is sometimes about something the change should have touched."""
	marks = diff.anchor(diff.parse(DIFF), [{"kind": "note", "loc": "nowhere/at/all.py:9", "text": "missing"}])
	assert len(marks) == 1 and marks[0]["file"] is None


def test_the_worst_mark_on_a_line_is_the_one_it_paints():
	files = diff.parse(DIFF)
	diff.anchor(files, [{"kind": "nit", "loc": "auto.py:139", "text": "a"},
	                    {"kind": "blocking", "loc": "auto.py:139", "text": "b"}])
	line = next(l for l in files[0]["hunks"][0]["lines"] if l["n"] == 139 and not l["del"])
	assert len(line["marks"]) == 2 and diff.worst(line) == "blocking"
	assert diff.worst({"n": 1}) == ""


def test_marks_only_keeps_the_lines_near_a_finding():
	"""A review of a 1,400-line diff has four findings in it; scrolling to them is the work removed."""
	files = diff.parse(DIFF)
	diff.anchor(files, [{"kind": "blocking", "loc": "auto.py:139", "text": "x"}])
	got = diff.narrow(files, context=1)
	assert [f["path"] for f in got] == ["gitdashy/auto.py"]     # CHANGELOG has no mark, so it goes
	assert [l["n"] for l in got[0]["hunks"][0]["lines"]] == [138, 139, 140]
	assert diff.narrow(diff.parse(DIFF)) == []                  # no marks at all: nothing to narrow to


def test_fetch_never_raises_and_caches_on_the_head(monkeypatch):
	"""It is reached from a keypress, and a PR whose diff gh will not print must leave an empty pane."""
	calls = []
	def fake(cmd, **kw):
		calls.append(cmd)
		return subprocess.CompletedProcess(cmd, 0, "diff --git a/x b/x\n", "")
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(subprocess, "run", fake)
	assert diff.fetch("a/b", 7, "sha1").startswith("diff --git")
	assert diff.fetch("a/b", 7, "sha1") and len(calls) == 1      # cached
	assert diff.fetch("a/b", 7, "sha2") and len(calls) == 2      # a push invalidates it

	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no gh")))
	assert diff.fetch("a/b", 7, "sha1") == ""
	monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom"))
	monkeypatch.setattr(diff, "_CACHE", {})
	assert diff.fetch("a/b", 7, "sha1") == ""


def test_a_diff_it_cannot_read_yields_what_it_could():
	assert diff.parse("") == [] and diff.parse("not a diff at all\n") == []
	half = "diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+b\n"
	assert [f["path"] for f in diff.parse(half)] == ["x.py"]


def test_a_diff_gh_cannot_produce_is_not_re_run_on_every_look(monkeypatch):
	"""The timing-out PR was the ONE input the cache did not cover — the pane re-ran gh on every draw."""
	calls = []
	def boom(*a, **k):
		calls.append(1)
		raise subprocess.TimeoutExpired("gh", diff.TIMEOUT)
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(subprocess, "run", boom)
	assert diff.fetch("a/b", 7, "s") == "" and len(calls) == 1
	assert diff.fetch("a/b", 7, "s") == "" and len(calls) == 1   # the failure is cached too

	diff.retry()                                                 # f means "go and look again"
	assert diff.fetch("a/b", 7, "s") == "" and len(calls) == 2


def test_retry_forgets_only_the_failures(monkeypatch):
	"""A diff that really is empty is not worth re-fetching every time someone presses refresh."""
	calls = []
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(subprocess, "run",
	                    lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
	assert diff.fetch("a/b", 7, "s") == "" and len(calls) == 1   # gh succeeded and printed nothing
	diff.retry()
	assert diff.fetch("a/b", 7, "s") == "" and len(calls) == 1   # still cached: it was not a failure


def test_an_unknown_kind_sorts_last_instead_of_raising():
	"""ORDER.index is reached from inside draw(). log.KINDS is a dict someone will add a row to."""
	assert diff.rank("blocking") == 0 and diff.rank("nit") == 2
	assert diff.rank("wildcard") == len(diff.ORDER)
	marks = diff.anchor(diff.parse(DIFF), [
		{"kind": "wildcard", "loc": "auto.py:139", "text": "a kind this table does not name"},
		{"kind": "blocking", "loc": "auto.py:139", "text": "known"}])
	assert [m["kind"] for m in marks] == ["blocking", "wildcard"]   # sorted, not crashed


def test_the_context_ring_has_one_home():
	"""Two copies of one number is how the c key became a KeyError waiting to happen."""
	assert len(set(diff.CONTEXTS)) == len(diff.CONTEXTS)


def test_an_added_line_that_looks_like_a_header_does_not_rewrite_the_path():
	"""A doc or PR body quoting a diff does exactly this, and this repo writes them constantly."""
	text = ("diff --git a/docs/memory.md b/docs/memory.md\n"
	        "--- a/docs/memory.md\n"
	        "+++ b/docs/memory.md\n"
	        "@@ -1,1 +1,3 @@\n"
	        " intro\n"
	        "+++ b/not-a-file.py\n"
	        "+really part of the doc\n")
	files = diff.parse(text)
	assert [f["path"] for f in files] == ["docs/memory.md"]
	body = [l["text"] for h in files[0]["hunks"] for l in h["lines"]]
	assert "++ b/not-a-file.py" in body            # kept as the added line it is


@pytest.mark.parametrize("context", diff.CONTEXTS)
def test_narrow_holds_up_at_every_context_the_ring_offers(context):
	"""narrow was proven at context=1, which the shipped ring never uses: it is 3/8/0.

	0 is the edge that matters — the marked line alone, with nothing either side — because a context
	that trims to nothing would leave a hunk with no lines in it, and the pane draws a header over
	whatever it is handed.
	"""
	files = diff.parse(DIFF)
	diff.anchor(files, [{"kind": "blocking", "loc": "auto.py:139", "text": "x"}])
	got = diff.narrow(files, context=context)
	assert [f["path"] for f in got] == ["gitdashy/auto.py"]
	lines = got[0]["hunks"][0]["lines"]
	assert lines, f"context={context} produced a hunk with no lines"
	assert 139 in [l["n"] for l in lines]                 # the marked line always survives
	assert all(h["lines"] for f in got for h in f["hunks"])


def test_a_finding_that_names_a_column_still_lands_on_its_line():
	"""A reviewer writes file:line:col, and one rpartition read that as ("file:line", col).

	The path then matched nothing and the "line" was really a column, so the finding silently became an
	orphan — reachable only in the orphan list, never on the code it is about.
	"""
	assert diff._where("gitdashy/auto.py:139:5") == ("gitdashy/auto.py", 139)
	assert diff._where("gitdashy/auto.py:139") == ("gitdashy/auto.py", 139)
	assert diff._where("gitdashy/auto.py") == ("gitdashy/auto.py", 0)
	assert diff._where("") == ("", 0)
	files = diff.parse(DIFF)
	marks = diff.anchor(files, [{"kind": "blocking", "loc": "auto.py:139:5", "text": "x"}])
	line = next(l for f in files for h in f["hunks"] for l in h["lines"] if l["n"] == 139)
	assert line.get("marks") == marks                     # on the line, not in the orphan pile


def test_same_file_matches_when_the_finding_is_the_longer_path():
	"""Only one direction was proven. A reviewer cites the full path and the diff carries a basename
	(or the reverse), and both have to meet."""
	assert diff._same_file("auto.py", "gitdashy/core/auto.py")
	assert diff._same_file("gitdashy/core/auto.py", "auto.py")
	assert diff._same_file("gitdashy/core/auto.py", "gitdashy/core/auto.py")
	assert not diff._same_file("auto.py", "other.py")
	assert not diff._same_file("core/auto.py", "core/other.py")


def test_the_diff_cache_does_not_grow_without_end(monkeypatch):
	"""Only FAILURES were ever dropped, by retry(). Every diff gh did produce stayed for the life of
	the process, one entry per (repo, number, head), each holding the whole text — and a push adds an
	entry rather than replacing one. The layer above evicts per PR; this one held the megabytes."""
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(diff, "_KEEP", 4)
	monkeypatch.setattr(subprocess, "run",
	                    lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, f"diff --git a/{cmd[-1]} b/x\n", ""))
	for i in range(10):
		diff.fetch("a/b", i, "sha")
	assert len(diff._CACHE) == 4, diff._CACHE
	assert [k[1] for k in diff._CACHE] == [6, 7, 8, 9]        # the oldest go first


def test_a_look_counts_as_a_use_so_the_pane_you_are_reading_does_not_age_out(monkeypatch):
	"""LRU, not FIFO: the diff you keep coming back to is the one that must survive."""
	monkeypatch.setattr(diff, "_CACHE", {})
	monkeypatch.setattr(diff, "_KEEP", 3)
	monkeypatch.setattr(subprocess, "run",
	                    lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "diff --git a/x b/x\n", ""))
	for i in (1, 2, 3):
		diff.fetch("a/b", i, "sha")
	diff.fetch("a/b", 1, "sha")                              # a hit on the oldest, which renews it
	diff.fetch("a/b", 4, "sha")                              # pushes one out
	assert sorted(k[1] for k in diff._CACHE) == [1, 3, 4]    # 2 went, not 1
