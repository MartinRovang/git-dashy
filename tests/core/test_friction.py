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


def test_length_alone_never_asks():
	"""The invariant the whole design rests on: a long session earns no friction by being long.

	Nothing in reason() takes a turn count, so this is a guard against a future 'and it was a big
	session' term being added — which is exactly what made the first calibration fire on every session.
	"""
	assert friction.reason(0, 0) == ""


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
	assert friction.started_at(str(tmp_path / "gone.jsonl")) == 0.0


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
