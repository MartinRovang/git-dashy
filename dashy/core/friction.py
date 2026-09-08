"""Whether a session hit something worth remembering, and the words to ask for it.

Two layers, deliberately. `reason()` is the POLICY and knows nothing about any agent: it takes counted
signals and answers. `claude_signals()` is ONE ADAPTER, and the only part here that knows a transcript
format. Another agent is wired with `gitdashy friction --interrupts N --denials N` and gets the same
thresholds and the same wording that Claude Code gets — the counting is format-specific, the judgement
is not. Declaring the policy once is the whole point; a second adapter that also re-decided when to
speak would be a second product.

ponytail: this exists because the instruction did not work. The corpus tells a session to run
`gitdashy remember`, and after 47 drafts every single one still sat at (1) — no fact has ever been
confirmed by a coding session, because a guard that must be remembered is not a guard. This asks.
"""
import json
import os

INTERRUPTS = 3  # a human stopping the agent mid-answer. One or two is steering; three is a session going wrong.
DENIALS = 2  # a human refusing a tool call outright. Rare enough in real transcripts that two is a pattern.

# ponytail: tool ERRORS are counted by nobody here, on purpose. Across the three real transcripts this was
# calibrated on, 30 of 36 error blocks were benign — a non-zero `ls` inside a compound command, a grep that
# matched nothing. Folding them in fired on every session, including the routine ones, and a prompt that
# fires every time is the warning nobody reads twice. Both signals kept are HUMAN actions, which is also
# why neither scales with session length: a 2029-turn session earns no friction by being long.

DENIED = "the tool use was rejected"  # the canonical refusal body Claude Code puts in an is_error result
MAX_BYTES = 32 << 20  # a real transcript is a few MB; past this it is not one, and this runs on a 10s hook


def _lines(path):
	"""The transcript's lines, bounded. Yields nothing for anything that is not a plain file.

	ponytail: `transcript_path` arrives on hook stdin and is opened as given. A FIFO blocks on open
	until a writer appears, and a huge or endless file is read until the hook's 10s timeout kills it —
	either way the session's stop hangs on something that is not a transcript. isfile() is False for a
	fifo, a socket and a directory, and the byte cap bounds the rest.
	"""
	if not os.path.isfile(path):
		return
	read = 0
	try:
		with open(path, encoding="utf-8", errors="replace") as f:
			for line in f:
				read += len(line)
				if read > MAX_BYTES:
					return
				yield line
	except OSError:
		return


def reason(interrupts, denials):
	"""Why this session is worth a draft, or "" when it is not. The policy, and all of it.

	The string is addressed to the agent, not the user: it is handed back as the reason a stop was
	blocked, so it has to say what happened AND what to do about it.
	"""
	hit = []
	if interrupts >= INTERRUPTS:
		hit.append(f"you were interrupted {interrupts} times")
	if denials >= DENIALS:
		hit.append(f"{denials} tool calls were refused")
	if not hit:
		return ""
	return (
		"This session hit friction worth recording: " + ", and ".join(hit) + ". "
		"If it taught you something durable about this codebase — a constraint, a convention, why "
		"something is shaped as it is — file it with `gitdashy remember \"...\"`. Not what this task "
		"did, not one bug, not what git already records. If it taught you nothing, say so and stop."
	)


def claude_signals(path):
	"""(interrupts, denials) from a Claude Code transcript. Unreadable, absent or partial -> what was read.

	ponytail: STRUCTURED fields first — never a text match on a message body alone. Calibrating this, a
	grep for "tool use was rejected" scored the session that was READING about rejected tool calls as
	though it had had them; the transcript that discusses friction detection is indistinguishable from
	the one that hit it. `interruptedMessageId` is a key and `is_error` is a boolean, and neither can be
	quoted into existence by talking about the subject.
	ponytail: the DENIAL half is honestly a hybrid, and worth saying so rather than overclaiming. Claude
	Code marks a refusal only as an is_error tool_result carrying that sentence — there is no field that
	says "refused" — so the structured flag narrows it and the substring identifies it. The residual: an
	error result whose body QUOTES the sentence counts. The reachable case is this repo's own suite,
	where a failing test prints DENIAL's verbatim text; two of those and the hook asks. Self-inflicted,
	bounded, and the ask is a question rather than a change. If Claude Code ever labels the refusal, that
	label replaces the substring and this note goes with it.
	"""
	interrupts = denials = 0
	for line in _lines(path):
		line = line.strip()
		if not line:
			continue
		try:
			rec = json.loads(line)
		except ValueError:
			continue  # a half-written last line is normal: the session is still being appended to
		if not isinstance(rec, dict):
			continue
		if "interruptedMessageId" in rec:
			interrupts += 1
		content = (rec.get("message") or {}).get("content")
		for block in content if isinstance(content, list) else []:
			if isinstance(block, dict) and block.get("is_error") is True:
				if DENIED in str(block.get("content") or "").lower():
					denials += 1
	return interrupts, denials


def filed_since(repo, when):
	"""True when a draft for `repo` was written after `when` (epoch seconds).

	ponytail: a session that already ran `gitdashy remember` must not then be asked to. Asking anyway
	teaches the agent that the prompt is noise, which costs more than the one draft it might have won.
	Compared by mtime rather than by looking for the call in the transcript — the file moving is the
	thing that actually happened, and it is true however the draft was filed.
	ponytail: this is deliberately LOOSE — a draft filed by a DIFFERENT session while this one was open
	also silences the ask, because the mtime carries no session id. That errs toward asking too rarely,
	which is the direction to err in: a prompt that fires when it should not is one the agent learns to
	answer with nothing, and then it is worth less than no prompt. Tighten it only if a real session is
	seen going unasked, and tighten it with a session id rather than a shorter window.
	ponytail: `when` of None means the session could not be dated, and then this DOES NOT VETO. It was
	0.0 before, which is not an absence — it is a real instant that every drafts file postdates, so any
	repo with one draft on it silenced every undateable session, for good, by arithmetic rather than by
	decision. A guard that cannot answer must abstain: the friction counts are still sound without a
	timestamp, so the ask stands and at worst is one the agent says "nothing to file" to.
	"""
	from . import memory  # ponytail: local — memory imports config, which reads the environment on import

	if when is None:
		return False
	try:
		return os.path.getmtime(memory.queue_path(repo)) > when
	except OSError:
		return False  # no drafts file at all: nothing has ever been filed, so nothing was filed just now


def started_at(path):
	"""Epoch seconds of a transcript's first record, or None when it cannot be dated.

	ponytail: None, never 0.0. See filed_since — 0.0 is a real instant, and one that every drafts file
	is newer than, so returning it silently vetoed the ask instead of declining to answer.

	ponytail: the FIRST timestamp, not the file's mtime — mtime is when the session last spoke, which is
	after any draft it filed, and would make filed_since() answer False for every session that behaved.
	"""
	for line in _lines(path):
		try:
			stamp = json.loads(line).get("timestamp")
		except (ValueError, AttributeError):
			continue
		if stamp and (at := _epoch(stamp)) is not None:
			return at
	return None


def _epoch(stamp):
	"""ISO 8601 -> epoch seconds, None when it is not a shape we know.

	ponytail: fromisoformat does not take a trailing Z before 3.11 and this ships for 3.9, so the Z
	becomes the offset it means. A stamp carrying no zone at all is read as UTC rather than as the
	machine's local time — a transcript is not written where it is read.
	"""
	import datetime

	try:
		at = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
		return (at if at.tzinfo else at.replace(tzinfo=datetime.timezone.utc)).timestamp()
	except (ValueError, TypeError):
		return None
