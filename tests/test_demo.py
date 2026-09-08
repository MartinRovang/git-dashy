import os
import subprocess
import time


from dashy import demo
from dashy.core import github, log, review as review_mod
from dashy.ui import screen as ui
from dashy.core.state import State

from conftest import FakeScr


def test_demo_is_self_contained(monkeypatch, tmp_path):
	monkeypatch.setenv("TMPDIR", str(tmp_path))
	monkeypatch.setattr(time, "sleep", lambda s: None)
	monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("demo must not shell out")))
	demo.install()
	assert log.LOG.startswith(str(tmp_path))
	secs = github.fetch()
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]
	assert len(secs[3][1]) == 3  # seeded history, #180 twice
	# ponytail: #180 (06:43) then #44 (04:43) — BY TIMESTAMP. reviewed() used to return reversed FILE
	# order and call it newest-first, which is only the same thing when appends arrive in time order.
	# The demo writes at=updatedAt, so it never did here; with logs from several teams it never will.
	assert [p["more"] for k, p in ui.rows(secs) if k == "pr" and p["section"] == "REVIEWED"] == [1, 0]
	assert len(github.fetch()[1][1]) == 4 and len(github.fetch()[1][1]) == 5  # a new PR shows up on the 3rd refresh (+1 re-requested)
	assert log.mark_rereviews(github.fetch()) == ["https://github.com/acme/infra/pull/44"]
	p = secs[1][1][0]
	statuses = [review_mod.review(p, "opus") for _ in range(4)]
	assert statuses[:3] == ["✓ approved", "✗ changes requested", "~ commented"] and statuses[3].startswith("error:")
	assert len(log.reviewed()) == 6
	st = State(0)
	st.sections = github.fetch()
	ui.C = lambda n: 0
	ui.draw(FakeScr(), st, 0)  # renders without raising


def test_demo_swaps_every_call_out_including_the_pre_reviewer(monkeypatch, tmp_path):
	"""`p` on a demo row called review.self_review, which demo.install() did not swap.

	With claude on PATH that spawned a real `claude -p` against acme/api#101, which then shells out to
	gh — against a README that promises no gh and no claude. The other test asserts no subprocess.run;
	this one asserts the attr itself is not the real function, which is what actually went wrong.
	"""
	monkeypatch.setenv("TMPDIR", str(tmp_path))
	real_review, real_self = review_mod.review, review_mod.self_review
	demo.install()
	try:
		assert review_mod.review is not real_review
		assert review_mod.self_review is not real_self, "demo must swap the pre-reviewer too"
		status, dest = review_mod.self_review({"repository": {"nameWithOwner": "acme/api"},
		                                       "number": 101, "url": "u"}, "opus")
		assert "not posted" in status and os.path.isfile(dest)
		assert str(tmp_path) in dest  # and it wrote into the demo's own temp dir, not ~/.prs_reviews
	finally:
		review_mod.review, review_mod.self_review = real_review, real_self


def test_demo_puts_back_everything_it_swapped(monkeypatch, tmp_path):
	"""conftest named three attrs while install() swapped eight, so five stayed faked for every module
	collected after this file — github.copy, collaborators, request_review, self_review and
	update_available. Recording the originals means a swap added later is covered by having been
	written, not by someone remembering to add it in a second place.
	"""
	from dashy.core import update
	monkeypatch.setenv("TMPDIR", str(tmp_path))
	from dashy.core import install as install_mod
	originals = {(m, n): getattr(m, n) for m, n in
	             ((github, "fetch"), (github, "copy"), (github, "collaborators"), (github, "request_review"),
	              (review_mod, "review"), (review_mod, "self_review"), (update, "update_available"),
	              (install_mod, "retire"))}

	demo.install()
	assert all(getattr(m, n) is not o for (m, n), o in originals.items()), "install must swap all of them"
	assert len(demo.SWAPPED) >= len(originals)

	demo.restore()

	for (m, n), o in originals.items():
		assert getattr(m, n) is o, f"{m.__name__}.{n} was not put back"
	assert demo.SWAPPED == {}


def test_a_demo_launch_leaves_the_real_agent_config_alone(screen, monkeypatch, tmp_path):
	"""--demo is documented as "nothing touches gh, claude or your real log", and demo.install()'s own
	docstring says EVERY call-out is swapped here.

	The launch-time link retirement was a new call-out and was not, so a demo run read and rewrote the
	user's ~/.claude/CLAUDE.md and unlinked a symlink under it. Blanking the store roots stops the link
	being matched; it does not stop the CLAUDE.md rewrite.
	"""
	from dashy import config
	from dashy.core import install, team
	cfg = tmp_path / "claude"
	(cfg / "identity").mkdir(parents=True)
	md = cfg / "CLAUDE.md"
	# ponytail: the REAL markers, with STALE inside them. A CLAUDE.md that does not satisfy
	# `STALE in _inside_blocks(text)` is one retire() would leave alone anyway, so the assertion below
	# would hold with or without the swap — it pinned nothing. This is a file retire() really rewrites.
	md.write_text(f"# mine\n\n{install.BEGIN}\n# Review memory\n\n{install.STALE}general.md\n"
	              f"@prs-memory/general.md\n{install.END}\n")
	assert install.STALE in install._inside_blocks(md.read_text())   # or this test proves nothing
	teams = tmp_path / "teams"
	(teams / "acme" / "memory").mkdir(parents=True)
	os.symlink(str(teams / "acme" / "memory"), str(cfg / "prs-team"))
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
	monkeypatch.setenv("TMPDIR", str(tmp_path))
	before = md.read_bytes()

	demo.install()
	monkeypatch.setattr(install, "_NOTES", (None, []))
	said = []
	monkeypatch.setattr(ui, "init_colors", lambda: None)
	monkeypatch.setattr(ui, "confirm", lambda scr, s, sel, prompt: said.append(prompt) or True)
	monkeypatch.setattr(ui.threading.Thread, "start", lambda self: None)
	monkeypatch.setattr(team, "activate", lambda: None)
	monkeypatch.setattr(team, "migrate", lambda: "")
	screen.getch, screen.timeout = iter([ord("q")]).__next__, lambda t: None
	ui.main(screen, 60, False, "opus")

	assert md.read_bytes() == before                    # byte-identical: nothing rewrote it
	assert os.path.islink(str(cfg / "prs-team"))        # and nothing unlinked it
