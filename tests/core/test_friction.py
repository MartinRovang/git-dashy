import json
import os
import time

from dashy import cli, config
from dashy.core import friction, memory, team


def transcript(tmp_path, *records, name="t.jsonl"):
	"""A Claude Code transcript, one JSON record per line, as the real ones are written."""
	p = tmp_path / name
	p.write_text("".join(json.dumps(r) + "\n" for r in records))
	return str(p)


def result(text, is_error=True):
	"""One tool_result block, the shape a transcript actually carries."""
	return {"type": "user", "message": {"content": [
		{"type": "tool_result", "tool_use_id": "x", "content": text, "is_error": is_error}]}}


DENIAL = result("The user doesn't want to proceed with this tool use. The tool use was rejected "
                "(eg. if it was a file edit, the new_string was NOT written to the file)")


# ---- the policy, which knows about no agent at all -------------------------------------------------

def test_a_routine_session_is_asked_nothing():
	"""Nothing in reason() takes a turn count, so length alone can never ask — the invariant the whole
	design rests on, and what a first calibration got wrong by firing on every session."""
	assert friction.reason(0, 0) == ""
	assert friction.reason(2, 1) == ""  # steering and one refusal: under both thresholds, on purpose


def test_repeated_interruptions_ask_and_say_how_many():
	said = friction.reason(3, 0)
	assert "interrupted 3 times" in said
	assert "gitdashy remember" in said


def test_refusals_ask_on_their_own():
	assert "2 tool calls were refused" in friction.reason(0, 2)


def test_both_signals_are_named_when_both_fired():
	said = friction.reason(5, 4)
	assert "interrupted 5 times" in said and "4 tool calls were refused" in said


# ---- the Claude adapter, the only part that knows a transcript format ------------------------------

def test_counts_interruptions_and_refusals_from_a_transcript(tmp_path):
	p = transcript(tmp_path,
	               {"type": "user", "interruptedMessageId": "a"},
	               {"type": "user", "interruptedMessageId": "b"},
	               DENIAL, DENIAL,
	               result("Exit code 2\nls: cannot access 'ROADMAP.md'"))  # a real, benign failure
	assert friction.claude_signals(p) == (2, 2)


def test_a_session_that_only_READS_about_refusals_is_not_a_session_that_had_them(tmp_path):
	"""The false positive this design was built around.

	Calibrating against real transcripts, a text match for the refusal wording scored the session that
	was reading teamai's docs ABOUT friction detection as though it had been refused. Structured fields
	cannot be quoted into existence; message bodies can.

	Both markers below are the VERBATIM strings a real transcript carries — "[Request interrupted by
	user]" appears in message bodies more often than the structured key does (25 against 21, measured),
	so a text match counts the session that quoted them as one that lived them.
	"""
	quoted = ("The Stop hook scores whether the user interrupted the AI. A stop writes "
	          "[Request interrupted by user] into the body, and a refusal reads: The user doesn't "
	          "want to proceed with this tool use. The tool use was rejected.")
	p = transcript(tmp_path,
	               result(quoted, is_error=False),
	               {"type": "assistant", "message": {"content": [{"type": "text", "text": quoted}]}},
	               {"type": "user", "message": {"content": quoted}})
	assert friction.claude_signals(p) == (0, 0)
	assert friction.reason(*friction.claude_signals(p)) == ""


def test_a_benign_error_is_not_a_refusal(tmp_path):
	p = transcript(tmp_path, result("Exit code 1\nno matches found"), result("fatal: not a git repository"))
	assert friction.claude_signals(p) == (0, 0)


def test_a_half_written_line_is_skipped_not_fatal(tmp_path):
	p = tmp_path / "t.jsonl"
	p.write_text(json.dumps({"type": "user", "interruptedMessageId": "a"}) + "\n{\"half\": ")
	assert friction.claude_signals(str(p)) == (1, 0)


def test_a_transcript_that_is_not_there_says_nothing(tmp_path):
	assert friction.claude_signals(str(tmp_path / "gone.jsonl")) == (0, 0)
	assert friction.started_at(str(tmp_path / "gone.jsonl")) is None


def test_started_at_reads_the_first_record_not_the_last(tmp_path):
	"""The session BEGAN when its first record was written.

	Taking the last one (or the file's mtime, which is the same thing) would put the moment after any
	draft the session filed, and filed_since() would then answer False for every session that behaved.
	"""
	p = transcript(tmp_path,
	               {"type": "user", "timestamp": "2026-09-08T09:00:00.000Z"},
	               {"type": "user", "timestamp": "2026-09-08T11:00:00.000Z"})
	assert friction.started_at(p) == friction._epoch("2026-09-08T09:00:00.000Z")
	assert friction.started_at(p) != friction._epoch("2026-09-08T11:00:00.000Z")


def test_a_leading_record_with_no_timestamp_does_not_stop_the_search(tmp_path):
	p = transcript(tmp_path, {"type": "mode", "mode": "default"},
	               {"type": "user", "timestamp": "2026-09-08T09:00:00.000Z"})
	assert friction.started_at(p) == friction._epoch("2026-09-08T09:00:00.000Z")


# ---- do not ask a session that already answered ----------------------------------------------------

def test_filed_since_sees_a_draft_written_after_the_session_began(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	assert friction.filed_since("acme/web", time.time() - 60) is False  # nothing filed ever
	memory.append("acme/web", "the viewer owns mask state")
	assert friction.filed_since("acme/web", time.time() - 60) is True
	assert friction.filed_since("acme/web", time.time() + 60) is False  # filed, but before this session


# ---- the Claude Stop hook, end to end --------------------------------------------------------------

def hook(monkeypatch, capsys, tmp_path, body):
	monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(lambda: json.dumps(body))})())
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	cli.run(["gitdashy", "friction", "--claude-hook"])
	return capsys.readouterr().out


def test_the_hook_blocks_the_stop_and_asks(monkeypatch, tmp_path, capsys):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	p = transcript(tmp_path, DENIAL, DENIAL, {"type": "user", "timestamp": "2026-09-08T09:00:00.000Z"})
	said = hook(monkeypatch, capsys, tmp_path, {"transcript_path": p, "stop_hook_active": False})
	assert json.loads(said)["decision"] == "block"
	assert "gitdashy remember" in json.loads(said)["reason"]


def test_the_hook_never_asks_twice(monkeypatch, tmp_path, capsys):
	"""stop_hook_active means we already blocked this stop once. Blocking again is a loop with no exit."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	p = transcript(tmp_path, DENIAL, DENIAL)
	assert hook(monkeypatch, capsys, tmp_path, {"transcript_path": p, "stop_hook_active": True}) == ""


def test_the_hook_says_nothing_to_a_session_that_already_filed(monkeypatch, tmp_path, capsys):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	p = transcript(tmp_path, DENIAL, DENIAL, {"type": "user", "timestamp": "2000-01-01T00:00:00.000Z"})
	memory.append("acme/web", "a fact this session filed")
	assert hook(monkeypatch, capsys, tmp_path, {"transcript_path": p, "stop_hook_active": False}) == ""


def test_a_routine_session_ends_in_silence(monkeypatch, tmp_path, capsys):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	p = transcript(tmp_path, {"type": "user", "interruptedMessageId": "a"}, result("Exit code 1"))
	assert hook(monkeypatch, capsys, tmp_path, {"transcript_path": p, "stop_hook_active": False}) == ""


def test_junk_on_stdin_never_blocks_a_stop(monkeypatch, capsys):
	monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(lambda: "not json")})())
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	cli.run(["gitdashy", "friction", "--claude-hook"])
	assert capsys.readouterr().out == ""


# ---- the contract another agent is wired against ---------------------------------------------------

def test_any_agent_can_pass_its_own_counts_and_get_the_same_policy(monkeypatch, capsys):
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	cli.run(["gitdashy", "friction", "--interrupts", "4", "--denials", "0"])
	said = capsys.readouterr().out
	assert "interrupted 4 times" in said
	assert said.strip() == friction.reason(4, 0)  # the same words Claude Code is given, not a variant


def test_counts_under_the_threshold_print_nothing_at_all(monkeypatch, capsys):
	monkeypatch.setattr(team, "origin_slug", lambda p: "acme/web")
	cli.run(["gitdashy", "friction", "--interrupts", "1", "--denials", "1"])
	assert capsys.readouterr().out == ""


def test_a_session_that_cannot_be_dated_is_still_asked(monkeypatch, tmp_path, capsys):
	"""started_at() returning 0.0 was not an absence — it is a real instant every drafts file postdates.

	So a transcript with no parseable timestamp made filed_since() True for any repo that had ever
	filed one draft, and the ask was vetoed for good, by arithmetic rather than by decision. A guard
	that cannot answer abstains: the friction counts are sound without a timestamp.
	"""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	memory.append("acme/web", "a draft filed long before this session")
	p = transcript(tmp_path, DENIAL, DENIAL, {"type": "user", "no": "timestamp here"})
	assert friction.started_at(p) is None
	assert friction.filed_since("acme/web", None) is False        # abstains, does not veto
	said = hook(monkeypatch, capsys, tmp_path, {"transcript_path": p, "stop_hook_active": False})
	assert json.loads(said)["decision"] == "block", said


def test_a_dateable_session_is_still_vetoed_by_its_own_draft(monkeypatch, tmp_path, capsys):
	"""The abstain must not have turned the guard off for sessions that CAN be dated."""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	p = transcript(tmp_path, DENIAL, DENIAL, {"type": "user", "timestamp": "2000-01-01T00:00:00.000Z"})
	memory.append("acme/web", "filed during this session")
	assert friction.started_at(p) is not None
	assert hook(monkeypatch, capsys, tmp_path, {"transcript_path": p, "stop_hook_active": False}) == ""


def test_friction_outside_a_git_repo_still_answers(monkeypatch, tmp_path, capsys):
	"""Every other test monkeypatches origin_slug, which mocks away the seam under test.

	With no origin there is no repo name, and memory.queue_path("") is the general drafts file — the
	ask must still be made rather than raising or silently resolving to somebody else's repo.
	"""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(team, "origin_slug", lambda p: "")      # the real answer outside a repo
	p = transcript(tmp_path, DENIAL, DENIAL, {"type": "user", "timestamp": "2026-09-08T09:00:00.000Z"})
	monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(
		lambda: json.dumps({"transcript_path": p, "stop_hook_active": False}))})())
	cli.run(["gitdashy", "friction", "--claude-hook"])
	assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_the_repo_is_not_resolved_for_a_session_that_will_not_be_asked(monkeypatch, tmp_path, capsys):
	"""origin_slug() forks `git`, and this runs at the end of EVERY session in EVERY repo.

	Resolved at the top of the function it paid for that fork on every routine session and then threw
	the answer away, which is nearly all of them.
	"""
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
	monkeypatch.setattr(team, "origin_slug",
	                    lambda p: (_ for _ in ()).throw(AssertionError("forked git for a routine session")))
	p = transcript(tmp_path, {"type": "user", "interruptedMessageId": "a"})   # one interrupt: routine
	monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(
		lambda: json.dumps({"transcript_path": p, "stop_hook_active": False}))})())
	cli.run(["gitdashy", "friction", "--claude-hook"])
	assert capsys.readouterr().out == ""


def test_a_transcript_path_that_is_not_a_plain_file_is_not_opened(tmp_path):
	"""transcript_path arrives on hook stdin and was opened as given.

	A FIFO blocks on open until a writer appears, and the stop hook has a 10s budget — so a path that
	is not a transcript hangs the end of the session on something that is not a transcript.

	ponytail: run on a THREAD with a join deadline, so removing the guard FAILS rather than hangs. The
	bug it pins is an unbounded block, and a test that reproduces it by blocking forever turns a clean
	CI failure into a job that sits there until the runner's own timeout kills it with no useful name.
	"""
	import threading

	fifo = tmp_path / "pipe"
	os.mkfifo(str(fifo))
	out = []
	worker = threading.Thread(target=lambda: out.append(friction.claude_signals(str(fifo))), daemon=True)
	worker.start()
	worker.join(timeout=5)
	assert not worker.is_alive(), "claude_signals blocked on a fifo instead of declining to open it"
	assert out == [(0, 0)]
	assert friction.started_at(str(fifo)) is None
	assert friction.claude_signals(str(tmp_path)) == (0, 0)   # a directory, too


def test_an_endless_transcript_is_read_only_so_far(monkeypatch, tmp_path):
	"""The other half of the same budget: a huge file is read until the hook timeout kills it."""
	monkeypatch.setattr(friction, "MAX_BYTES", 400)
	rec = json.dumps({"type": "user", "interruptedMessageId": "x"}) + "\n"
	p = tmp_path / "big.jsonl"
	p.write_text(rec * 500)
	interrupts, _ = friction.claude_signals(str(p))
	assert 0 < interrupts < 500, interrupts               # some read, and stopped well before the end
	assert interrupts <= 400 // len(rec) + 1
