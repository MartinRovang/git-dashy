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

	ponytail: STRUCTURED fields only — never a text match on message bodies. Calibrating this, a grep for
	"tool use was rejected" scored the session that was READING about rejected tool calls as though it had
	had them; the transcript that discusses friction detection is indistinguishable from the one that hit
	it. `interruptedMessageId` is a key and `is_error` is a boolean, and neither can be quoted into
	existence by talking about the subject.
	"""
	interrupts = denials = 0
	try:
		with open(path, encoding="utf-8", errors="replace") as f:
			for line in f:
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
	except OSError:
		return 0, 0
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
	"""
	from . import memory  # ponytail: local — memory imports config, which reads the environment on import

	try:
		return os.path.getmtime(memory.queue_path(repo)) > when
	except OSError:
		return False  # no drafts file at all: nothing has ever been filed, so nothing was filed just now


def started_at(path):
	"""Epoch seconds of a transcript's first record, or 0.0 when it cannot be read.

	ponytail: the FIRST timestamp, not the file's mtime — mtime is when the session last spoke, which is
	after any draft it filed, and would make filed_since() answer False for every session that behaved.
	"""
	try:
		with open(path, encoding="utf-8", errors="replace") as f:
			for line in f:
				try:
					stamp = json.loads(line).get("timestamp")
				except (ValueError, AttributeError):
					continue
				if stamp:
					return _epoch(stamp)
	except OSError:
		pass
	return 0.0


def _epoch(stamp):
	"""ISO 8601 -> epoch seconds, 0.0 when it is not a shape we know.

	ponytail: fromisoformat does not take a trailing Z before 3.11 and this ships for 3.9, so the Z is
	stripped and the result read as UTC rather than reaching for a dependency to parse one field.
	"""
	import calendar
	import datetime

	try:
		text = str(stamp)
		naive = datetime.datetime.fromisoformat(text[:-1] if text.endswith("Z") else text)
		if naive.tzinfo is not None:
			return naive.timestamp()
		return calendar.timegm(naive.timetuple()) + naive.microsecond / 1e6
	except (ValueError, TypeError):
		return 0.0
