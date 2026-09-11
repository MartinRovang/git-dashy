//! Was a session worth a draft? Port of dashy/core/friction.py. The policy lives here once; the Claude
//! Stop hook is a thin adapter over `gitdashy friction --claude-hook`.
//!
//! Two layers, deliberately. `reason()` is the POLICY and knows nothing about any agent: it takes counted
//! signals and answers. `claude_signals()` is ONE ADAPTER, and the only part here that knows a transcript
//! format. Another agent is wired with `gitdashy friction --interrupts N --denials N` and gets the same
//! thresholds and the same wording that Claude Code gets: the counting is format-specific, the judgement
//! is not.
//!
//! ponytail: this exists because the instruction did not work. The corpus tells a session to run
//! `gitdashy remember`, and after 47 drafts every single one still sat at (1): no fact has ever been
//! confirmed by a coding session, because a guard that must be remembered is not a guard. This asks.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

use serde_json::Value;

/// A human stopping the agent mid-answer. One or two is steering; three is a session going wrong.
pub const INTERRUPTS: u32 = 3;
/// A human refusing a tool call outright. Rare enough in real transcripts that two is a pattern.
pub const DENIALS: u32 = 2;

// ponytail: tool ERRORS are counted by nobody here, on purpose. Across the three real transcripts this was
// calibrated on, 30 of 36 error blocks were benign: a non-zero `ls` inside a compound command, a grep that
// matched nothing. Folding them in fired on every session, including the routine ones, and a prompt that
// fires every time is the warning nobody reads twice. Both signals kept are HUMAN actions, which is also
// why neither scales with session length: a 2029-turn session earns no friction by being long.

/// The canonical refusal body Claude Code puts in an is_error result.
pub const DENIED: &str = "the tool use was rejected";
/// A real transcript is a few MB; past this it is not one, and this runs on a 10s hook.
pub const MAX_BYTES: usize = 32 << 20;

/// The transcript's lines, bounded. Nothing for anything that is not a plain file.
///
/// ponytail: `transcript_path` arrives on hook stdin and is opened as given. A FIFO blocks on open
/// until a writer appears, and a huge or endless file is read until the hook's 10s timeout kills it;
/// either way the session's stop hangs on something that is not a transcript. is_file() is false for a
/// fifo, a socket and a directory, and the byte cap bounds the rest.
fn lines(path: &Path, max_bytes: usize) -> impl Iterator<Item = String> {
    let file = if path.is_file() {
        File::open(path).ok()
    } else {
        None
    };
    let mut reader = file.map(BufReader::new);
    let mut read = 0usize;
    std::iter::from_fn(move || {
        let r = reader.as_mut()?;
        let mut buf = Vec::new();
        match r.read_until(b'\n', &mut buf) {
            Ok(0) | Err(_) => None,
            Ok(n) => {
                read += n;
                if read > max_bytes {
                    reader = None;
                    return None;
                }
                Some(String::from_utf8_lossy(&buf).into_owned())
            }
        }
    })
}

/// Why this session is worth a draft, "" when it is not. The policy, and all of it.
///
/// The string is addressed to the agent, not the user: it is handed back as the reason a stop was
/// blocked, so it has to say what happened AND what to do about it.
pub fn reason(interrupts: u32, denials: u32) -> String {
    let mut hit = Vec::new();
    if interrupts >= INTERRUPTS {
        hit.push(format!("you were interrupted {interrupts} times"));
    }
    if denials >= DENIALS {
        hit.push(format!("{denials} tool calls were refused"));
    }
    if hit.is_empty() {
        return String::new();
    }
    format!(
        "This session hit friction worth recording: {}. \
         If it taught you something durable about this codebase \u{2014} a constraint, a convention, why \
         something is shaped as it is \u{2014} file it with `gitdashy remember \"...\"`. Not what this task \
         did, not one bug, not what git already records. If it taught you nothing, say so and stop.",
        hit.join(", and ")
    )
}

/// (interrupts, denials) from a Claude Code transcript; what was read on any failure.
///
/// ponytail: STRUCTURED fields first, never a text match on a message body alone. Calibrating this, a
/// grep for "tool use was rejected" scored the session that was READING about rejected tool calls as
/// though it had had them; the transcript that discusses friction detection is indistinguishable from
/// the one that hit it. `interruptedMessageId` is a key and `is_error` is a boolean, and neither can be
/// quoted into existence by talking about the subject.
/// ponytail: the DENIAL half is honestly a hybrid, and worth saying so rather than overclaiming. Claude
/// Code marks a refusal only as an is_error tool_result carrying that sentence (there is no field that
/// says "refused"), so the structured flag narrows it and the substring identifies it. The residual: an
/// error result whose body QUOTES the sentence counts. The reachable case is this repo's own suite,
/// where a failing test prints DENIAL's verbatim text; two of those and the hook asks. Self-inflicted,
/// bounded, and the ask is a question rather than a change. If Claude Code ever labels the refusal, that
/// label replaces the substring and this note goes with it.
pub fn claude_signals(path: &Path) -> (u32, u32) {
    signals_bounded(path, MAX_BYTES)
}

fn signals_bounded(path: &Path, max_bytes: usize) -> (u32, u32) {
    let (mut interrupts, mut denials) = (0, 0);
    for line in lines(path, max_bytes) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        // a half-written last line is normal: the session is still being appended to
        let Ok(Value::Object(rec)) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if rec.contains_key("interruptedMessageId") {
            interrupts += 1;
        }
        let Some(Value::Array(content)) = rec.get("message").and_then(|m| m.get("content")) else {
            continue;
        };
        for block in content {
            if block.get("is_error") != Some(&Value::Bool(true)) {
                continue;
            }
            let body = match block.get("content") {
                None | Some(Value::Null) => String::new(),
                Some(Value::String(s)) => s.clone(),
                Some(v) => v.to_string(),
            };
            if body.to_lowercase().contains(DENIED) {
                denials += 1;
            }
        }
    }
    (interrupts, denials)
}

/// True when a draft for `repo` was written after `when` (epoch seconds).
///
/// ponytail: a session that already ran `gitdashy remember` must not then be asked to. Asking anyway
/// teaches the agent that the prompt is noise, which costs more than the one draft it might have won.
/// Compared by mtime rather than by looking for the call in the transcript: the file moving is the
/// thing that actually happened, and it is true however the draft was filed.
/// ponytail: this is deliberately LOOSE. A draft filed by a DIFFERENT session while this one was open
/// also silences the ask, because the mtime carries no session id. That errs toward asking too rarely,
/// which is the direction to err in: a prompt that fires when it should not is one the agent learns to
/// answer with nothing, and then it is worth less than no prompt. Tighten it only if a real session is
/// seen going unasked, and tighten it with a session id rather than a shorter window.
/// ponytail: a session that could not be dated DOES NOT VETO. It was 0.0 before, which is not an
/// absence: it is a real instant that every drafts file postdates, so any repo with one draft on it
/// silenced every undateable session, for good, by arithmetic rather than by decision. A guard that
/// cannot answer must abstain: the friction counts are still sound without a timestamp, so the ask
/// stands and at worst is one the agent says "nothing to file" to.
// PORT-NOTE: Python took `when=None` for "the session could not be dated" and answered False. The stub
// takes a plain f64, so that case is the caller's: `started_at(p).is_some_and(|w| filed_since(repo, w))`,
// which abstains exactly as the Python did. Never pass 0.0 for an undated session.
pub fn filed_since(repo: &str, when: f64) -> bool {
    filed_since_at(&crate::memory::queue_path(Some(repo)), when)
}

fn filed_since_at(drafts: &Path, when: f64) -> bool {
    // no drafts file at all: nothing has ever been filed, so nothing was filed just now
    let Ok(modified) = std::fs::metadata(drafts).and_then(|m| m.modified()) else {
        return false;
    };
    modified
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
        > when
}

/// Epoch seconds of a transcript's first record, or None when it cannot be dated.
///
/// ponytail: None, never 0.0. See filed_since: 0.0 is a real instant, and one that every drafts file
/// is newer than, so returning it silently vetoed the ask instead of declining to answer.
///
/// ponytail: the FIRST timestamp, not the file's mtime. mtime is when the session last spoke, which is
/// after any draft it filed, and would make filed_since() answer False for every session that behaved.
pub fn started_at(path: &Path) -> Option<f64> {
    lines(path, MAX_BYTES).find_map(|line| {
        let rec: Value = serde_json::from_str(&line).ok()?;
        match rec.get("timestamp")? {
            Value::String(s) if !s.is_empty() => epoch(s),
            Value::Number(n) => epoch(&n.to_string()),
            _ => None,
        }
    })
}

/// ISO 8601 -> epoch seconds, None when it is not a shape we know.
///
/// ponytail: a stamp carrying no zone at all is read as UTC rather than as the machine's local time:
/// a transcript is not written where it is read. `Z` is the offset it means.
pub fn epoch(stamp: &str) -> Option<f64> {
    use chrono::{DateTime, NaiveDate, NaiveDateTime};
    let s = stamp.trim();
    if let Ok(at) = DateTime::parse_from_rfc3339(s) {
        return Some(at.timestamp() as f64 + f64::from(at.timestamp_subsec_micros()) / 1e6);
    }
    let naive = [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
    ]
    .iter()
    .find_map(|f| NaiveDateTime::parse_from_str(s, f).ok())
    .or_else(|| {
        NaiveDate::parse_from_str(s, "%Y-%m-%d")
            .ok()
            .and_then(|d| d.and_hms_opt(0, 0, 0))
    })?;
    let at = naive.and_utc();
    Some(at.timestamp() as f64 + f64::from(at.timestamp_subsec_micros()) / 1e6)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn transcript(dir: &Path, records: &[Value]) -> std::path::PathBuf {
        let p = dir.join("t.jsonl");
        let mut f = File::create(&p).unwrap();
        for r in records {
            writeln!(f, "{r}").unwrap();
        }
        p
    }

    fn result(text: &str, is_error: bool) -> Value {
        serde_json::json!({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "x", "content": text, "is_error": is_error}]}})
    }

    fn denial() -> Value {
        result(
            "The user doesn't want to proceed with this tool use. The tool use was rejected \
             (eg. if it was a file edit, the new_string was NOT written to the file)",
            true,
        )
    }

    fn interrupted(id: &str) -> Value {
        serde_json::json!({"type": "user", "interruptedMessageId": id})
    }

    // ---- the policy, which knows about no agent at all ----

    #[test]
    fn a_routine_session_is_asked_nothing() {
        assert_eq!(reason(0, 0), "");
        assert_eq!(reason(2, 1), ""); // steering and one refusal: under both thresholds, on purpose
    }

    #[test]
    fn repeated_interruptions_ask_and_say_how_many() {
        let said = reason(3, 0);
        assert!(said.contains("interrupted 3 times"));
        assert!(said.contains("gitdashy remember"));
    }

    #[test]
    fn refusals_ask_on_their_own() {
        assert!(reason(0, 2).contains("2 tool calls were refused"));
    }

    #[test]
    fn both_signals_are_named_when_both_fired() {
        let said = reason(5, 4);
        assert!(said.contains("interrupted 5 times") && said.contains("4 tool calls were refused"));
        assert!(said.contains("interrupted 5 times, and 4 tool calls were refused"));
    }

    // ---- the Claude adapter, the only part that knows a transcript format ----

    #[test]
    fn counts_interruptions_and_refusals_from_a_transcript() {
        let d = tempfile::tempdir().unwrap();
        let p = transcript(
            d.path(),
            &[
                interrupted("a"),
                interrupted("b"),
                denial(),
                denial(),
                result("Exit code 2\nls: cannot access 'ROADMAP.md'", true),
            ],
        );
        assert_eq!(claude_signals(&p), (2, 2));
    }

    #[test]
    fn a_session_that_only_reads_about_refusals_is_not_a_session_that_had_them() {
        let quoted = "The Stop hook scores whether the user interrupted the AI. A stop writes \
                      [Request interrupted by user] into the body, and a refusal reads: The user doesn't \
                      want to proceed with this tool use. The tool use was rejected.";
        let d = tempfile::tempdir().unwrap();
        let p = transcript(
            d.path(),
            &[
                result(quoted, false),
                serde_json::json!({"type": "assistant", "message": {"content": [{"type": "text", "text": quoted}]}}),
                serde_json::json!({"type": "user", "message": {"content": quoted}}),
            ],
        );
        assert_eq!(claude_signals(&p), (0, 0));
        let (i, dn) = claude_signals(&p);
        assert_eq!(reason(i, dn), "");
    }

    #[test]
    fn a_benign_error_is_not_a_refusal() {
        let d = tempfile::tempdir().unwrap();
        let p = transcript(
            d.path(),
            &[
                result("Exit code 1\nno matches found", true),
                result("fatal: not a git repository", true),
            ],
        );
        assert_eq!(claude_signals(&p), (0, 0));
    }

    #[test]
    fn a_half_written_line_is_skipped_not_fatal() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("t.jsonl");
        std::fs::write(&p, format!("{}\n{{\"half\": ", interrupted("a"))).unwrap();
        assert_eq!(claude_signals(&p), (1, 0));
        std::fs::write(&p, b"[1, 2]\n\"a string\"\n\xff\xfe not utf8\n").unwrap();
        assert_eq!(claude_signals(&p), (0, 0)); // records that are not objects are skipped, bytes that are not utf-8 too
    }

    #[test]
    fn a_transcript_that_is_not_there_says_nothing() {
        let d = tempfile::tempdir().unwrap();
        let gone = d.path().join("gone.jsonl");
        assert_eq!(claude_signals(&gone), (0, 0));
        assert_eq!(started_at(&gone), None);
        assert_eq!(claude_signals(d.path()), (0, 0)); // a directory, too
        assert_eq!(started_at(d.path()), None);
    }

    #[test]
    fn started_at_reads_the_first_record_not_the_last() {
        let d = tempfile::tempdir().unwrap();
        let p = transcript(
            d.path(),
            &[
                serde_json::json!({"type": "user", "timestamp": "2026-09-08T09:00:00.000Z"}),
                serde_json::json!({"type": "user", "timestamp": "2026-09-08T11:00:00.000Z"}),
            ],
        );
        assert_eq!(started_at(&p), epoch("2026-09-08T09:00:00.000Z"));
        assert_ne!(started_at(&p), epoch("2026-09-08T11:00:00.000Z"));
    }

    #[test]
    fn a_leading_record_with_no_timestamp_does_not_stop_the_search() {
        let d = tempfile::tempdir().unwrap();
        let p = transcript(
            d.path(),
            &[
                serde_json::json!({"type": "mode", "mode": "default"}),
                serde_json::json!({"type": "user", "no": "timestamp here"}),
                serde_json::json!({"type": "user", "timestamp": ""}),
                serde_json::json!({"type": "user", "timestamp": "2026-09-08T09:00:00.000Z"}),
            ],
        );
        assert_eq!(started_at(&p), epoch("2026-09-08T09:00:00.000Z"));
        let p = transcript(
            d.path(),
            &[serde_json::json!({"type": "user", "no": "timestamp here"})],
        );
        assert_eq!(started_at(&p), None); // undateable is None, never 0.0
    }

    #[test]
    fn epoch_reads_the_shapes_a_transcript_carries() {
        assert_eq!(epoch("2026-09-08T09:00:00.000Z"), Some(1788858000.0));
        assert_eq!(epoch("2026-09-08T09:00:00+00:00"), Some(1788858000.0));
        assert_eq!(epoch("2026-09-08T10:00:00+01:00"), Some(1788858000.0));
        assert_eq!(epoch("2026-09-08T09:00:00"), Some(1788858000.0)); // no zone: UTC, not local
        assert_eq!(epoch("2026-09-08 09:00:00.5"), Some(1788858000.5));
        assert_eq!(epoch("2026-09-08"), Some(1788825600.0));
        assert_eq!(epoch("yesterday"), None);
        assert_eq!(epoch(""), None);
    }

    // ---- do not ask a session that already answered ----

    #[test]
    fn filed_since_sees_a_draft_written_after_the_session_began() {
        let d = tempfile::tempdir().unwrap();
        let drafts = d.path().join("drafts.md");
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        assert!(!filed_since_at(&drafts, now - 60.0)); // nothing filed ever
        std::fs::write(&drafts, "(1) the viewer owns mask state\n").unwrap();
        assert!(filed_since_at(&drafts, now - 60.0));
        assert!(!filed_since_at(&drafts, now + 60.0)); // filed, but before this session
    }

    // ---- the transcript is opened as given, so it is opened carefully ----

    #[test]
    fn a_transcript_path_that_is_not_a_plain_file_is_not_opened() {
        let d = tempfile::tempdir().unwrap();
        let fifo = d.path().join("pipe");
        if !std::process::Command::new("mkfifo")
            .arg(&fifo)
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
        {
            return; // no mkfifo here: nothing to prove
        }
        // ponytail: on a THREAD with a join deadline, so removing the guard FAILS rather than hangs.
        let f = fifo.clone();
        let worker = std::thread::spawn(move || claude_signals(&f));
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while !worker.is_finished() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(
            worker.is_finished(),
            "claude_signals blocked on a fifo instead of declining to open it"
        );
        assert_eq!(worker.join().unwrap(), (0, 0));
        assert_eq!(started_at(&fifo), None);
    }

    #[test]
    fn an_endless_transcript_is_read_only_so_far() {
        let rec = format!("{}\n", interrupted("x"));
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("big.jsonl");
        std::fs::write(&p, rec.repeat(500)).unwrap();
        let (interrupts, _) = signals_bounded(&p, 400);
        assert!(0 < interrupts && interrupts < 500, "{interrupts}");
        assert!(interrupts as usize <= 400 / rec.len() + 1);
    }
}
