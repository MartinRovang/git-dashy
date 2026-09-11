//! The review log: one JSON object per line. Yours, plus one inside each joined team. Port of dashy/core/log.py.

use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use chrono::{DateTime, FixedOffset, Local, NaiveDateTime, SecondsFormat, Utc};
use serde_json::Value;

use crate::types::{Finding, LogEntry, Pr, Section, Verdict};
use crate::{bind, config, team};

/// An `at` as UTC, whatever offset it carried; naive means UTC. Err when it is not a timestamp.
///
/// ponytail: `at` predates the offset, so entries written before it read back NAIVE, and everything
/// that compares one compares it against an aware value. Kept as a function of its own because parse
/// leans on it failing: an `at` that is not a timestamp at all comes out as the Err that gate already
/// catches, rather than as a fourth type check beside the three.
pub fn when(iso: &str) -> Result<DateTime<Utc>, chrono::ParseError> {
    // fromisoformat took any one character between date and time; chrono wants the T.
    let mut iso = iso.replace('Z', "+00:00");
    if iso.len() > 10 && iso.is_char_boundary(11) && iso.as_bytes()[10] == b' ' {
        iso.replace_range(10..11, "T");
    }
    // ponytail: converted to UTC, not merely made aware. reviewed() sorts on the STRING this becomes,
    // so an entry from a machine at +02:00 sorted by literal text rather than by instant: 09:30+02:00
    // is 07:30Z, earlier than 08:00Z, and came back first in a list whose whole job is "newest first".
    // Unreachable while every writer emits UTC, which is exactly how long it stays unreachable.
    match iso.parse::<DateTime<FixedOffset>>() {
        Ok(t) => Ok(t.with_timezone(&Utc)),
        Err(e) => match iso.parse::<NaiveDateTime>() {
            Ok(t) => Ok(t.and_utc()),
            Err(_) => Err(e),
        },
    }
}

/// The shape every `at` is written back in: seconds precision, `+00:00`.
fn iso(t: DateTime<Utc>) -> String {
    t.to_rfc3339_opts(SecondsFormat::Secs, false)
}

/// Every log to read: yours, then each joined team's.
pub fn logs() -> Vec<PathBuf> {
    let mine = config::get().log;
    let mut out = vec![mine];
    for p in team::joined().iter().map(|s| team::log_of(s)) {
        if p.as_os_str().is_empty() || out.contains(&p) {
            continue;
        }
        out.push(p);
    }
    out
}

type StatKey = (Option<std::time::SystemTime>, u64);
type Cache = HashMap<PathBuf, (StatKey, Arc<Vec<LogEntry>>)>;

/// path -> (stat key, parsed entries). ponytail: see reviewed().
fn cache() -> &'static Mutex<Cache> {
    static CACHE: OnceLock<Mutex<Cache>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// One log's parsed entries, oldest first, cached on (mtime, size).
///
/// ponytail: reviewed() is called per FRAME by the detail pane and again per keypress, and it reparsed
/// the whole file every time. That was tolerable while there was one file; the log is per team now, so
/// an N-team machine paid N whole-file parses at ~20fps. The key is the stat, so an append by a review
/// thread, or a pull bringing a teammate's, invalidates it without anything having to remember to.
fn entries(path: &Path) -> Arc<Vec<LogEntry>> {
    let Ok(st) = std::fs::metadata(path) else {
        return Arc::new(Vec::new());
    };
    let key: StatKey = (st.modified().ok(), st.len());
    let mut cache = cache().lock().unwrap_or_else(|e| e.into_inner());
    if let Some((k, got)) = cache.get(path) {
        if *k == key {
            return Arc::clone(got);
        }
    }
    let Ok(text) = std::fs::read_to_string(path) else {
        return Arc::new(Vec::new());
    };
    // ponytail: a malformed line is skipped, not fatal. One team's half-written append must not empty
    // the REVIEWED list, and with several logs it is no longer your own file.
    let out = Arc::new(text.lines().filter_map(parse).collect::<Vec<_>>());
    cache.insert(path.to_path_buf(), (key, Arc::clone(&out)));
    out
}

fn parse(line: &str) -> Option<LogEntry> {
    let mut e: Value = serde_json::from_str(line).ok()?;
    // ponytail: every field reviewed() then reaches for. It uses "at" in the sort key and "pr" as the
    // row, so a line missing "at", or carrying "pr": 7, took the dashboard down on every frame. The
    // guard exists precisely so a half-written append cannot do that; checking two of the three fields
    // is checking none of them.
    let obj = e.as_object()?;
    if !obj.get("pr")?.is_object() || config::status(obj.get("verdict")?.as_str()?).is_none() {
        return None;
    }
    // ponytail: and `at` is PARSED, not merely typed. Being a str is not being a timestamp, and
    // mark_rereviews on the refresh thread and age() on the draw thread both hand it to when(), so
    // "at": "yesterday" failed in the two threads this gate exists to keep safe. Written back AWARE for
    // the same reason: an entry from before `at` carried an offset read back naive, and comparing it
    // against an aware value was an error. Normalising in the gate means no reader has to know the file
    // holds two shapes, and it settles the sort in reviewed(), where a naive string is a prefix of its
    // own aware form and so sorted before it.
    let at = iso(when(obj.get("at")?.as_str()?).ok()?);
    e["at"] = Value::String(at);
    serde_json::from_value(e).ok()
}

/// PR rows from every log, newest first, each carrying its `review`, `tag` and `status`.
pub fn reviewed() -> Vec<Pr> {
    // ponytail: newest first, and a STABLE tiebreak. It used to be reversed(lines) on one file, so two
    // entries written in the same second came back later-line-first. Sorting on "at" alone is stable in
    // the wrong direction, it kept the earlier line first, which silently reordered same-second
    // reviews, and log.last() reads the FIRST match. Position within its file breaks the tie.
    let mut got: Vec<(LogEntry, usize)> = logs()
        .iter()
        .flat_map(|p| {
            entries(p)
                .iter()
                .cloned()
                .enumerate()
                .map(|(i, e)| (e, i))
                .collect::<Vec<_>>()
        })
        .collect();
    got.sort_by(|x, y| (&y.0.at, y.1).cmp(&(&x.0.at, x.1)));
    got.into_iter()
        .map(|(e, _)| Pr {
            review: Some(Box::new(e.clone())),
            tag: tag(&e),
            status: config::status(&e.verdict).unwrap_or("").to_string(),
            updated_at: e.at.clone(),
            ..e.pr
        })
        .collect()
}

/// The newest entry for this PR url.
pub fn last(url: &str) -> Option<LogEntry> {
    reviewed()
        .into_iter()
        .find(|p| p.url == url)
        .and_then(|p| p.review.map(|b| *b))
}

/// "adaptive/medium $0.42 3m": depth[/effort] the review ran with, then what it cost; "" for old entries.
pub fn tag(e: &LogEntry) -> String {
    let mut t = e.depth.clone();
    if !e.effort.is_empty() {
        t = format!("{t}/{}", e.effort);
    }
    if let Some(cost) = e.cost.filter(|c| *c != 0.0) {
        // 0 on a subscription run, not worth a "$0.00"
        t += &format!(" ${cost:.2}");
    }
    if let Some(ms) = e.ms.filter(|m| *m != 0) {
        let s = (ms as f64 / 1000.0).round_ties_even() as u64;
        t += &if s >= 60 {
            format!(" {}m", s / 60)
        } else {
            format!(" {s}s")
        };
    }
    t.trim().to_string()
}

/// Kind -> tone the pane colours each by: blocking err, note warn, nit dim.
pub fn kind_tone(kind: &str) -> Option<&'static str> {
    match kind {
        "blocking" => Some("err"),
        "note" => Some("warn"),
        "nit" => Some("dim"),
        _ => None,
    }
}

/// A JSON value the way Python's str() would show it for a field: strings bare, the rest as JSON.
fn text_of(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

/// Python truthiness of a field: absent, null, "", 0, false, [] and {} are all "no text".
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// The checked findings a verdict listed; [] for anything malformed. At most 12.
///
/// ponytail: model output, so every field is checked. A review whose findings are junk still has a body,
/// and the pane falls back to it: a bad list must not cost you the review itself.
pub fn findings(v: &Verdict) -> Vec<Finding> {
    let mut out = Vec::new();
    for f in &v.findings {
        let Some(f) = f.as_object() else { continue };
        let kind = text_of(f.get("kind")).to_lowercase();
        if kind_tone(&kind).is_none() || !truthy(f.get("text")) {
            continue;
        }
        let text = text_of(f.get("text"))
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ");
        out.push(Finding {
            kind,
            loc: clip(&text_of(f.get("loc")), 60),
            text: clip(&text, 120),
        });
    }
    out.truncate(12); // ponytail: a pane, not a report. The body has the whole thing
    out
}

/// Append the entry to the log of the team the repo is bound to (yours when none). Returns the status string.
pub fn log_review(pr: &Pr, model: &str, v: &Verdict, at: Option<&str>) -> std::io::Result<String> {
    let status = config::status(&v.verdict).ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("unknown verdict {:?}", v.verdict),
        )
    })?;
    let cfg = config::get();
    // ponytail: both sides added fields to this entry: findings from the pane, head/cost/ms and the
    // checks filter from #9. Neither replaces the other.
    let entry = LogEntry {
        at: at.map(str::to_string).unwrap_or_else(|| iso(Utc::now())),
        model: model.to_string(),
        pr: pr.for_log(), // CI state at review time is stale by the time anyone reads it
        depth: cfg.depth.clone(),
        effort: cfg.effort.clone(),
        head: pr.head.clone(),
        cost: v.cost,
        ms: v.ms,
        verdict: v.verdict.clone(),
        summary: v.summary.clone(),
        body: v.body.clone(),
        findings: findings(v),
    };
    // ponytail: into the log of the team this repo is BOUND to, and yours when it is bound to none. The
    // shared review log is how a teammate's review appears in your list; sending it to a team the repo
    // does not belong to would tell them you reviewed something that is none of their business.
    let repo = pr.repo();
    let dest = if repo.is_empty() {
        cfg.log
    } else {
        team::log_of(&bind::of(repo))
    };
    if let Some(dir) = dest.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::create_dir_all(dir)?;
    }
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&dest)?;
    let line = serde_json::to_string(&entry).map_err(std::io::Error::other)?;
    f.write_all(format!("{line}\n").as_bytes())?;
    Ok(status.to_string())
}

/// Tag REVIEW REQUESTED rows already in the log and pushed to since as `prev`. Returns their urls.
/// ponytail: by head commit when both sides know it: updatedAt also moves on a comment, which is not a
/// reason to review again. Timestamps only for entries logged before heads were.
pub fn mark_rereviews(sections: &mut [Section]) -> Vec<String> {
    let mut last: HashMap<String, LogEntry> = HashMap::new();
    for p in sections
        .iter()
        .filter(|s| s.name == "REVIEWED")
        .flat_map(|s| s.prs.iter().flatten())
    {
        if let Some(e) = &p.review {
            last.entry(p.url.clone()).or_insert_with(|| (**e).clone()); // newest first
        }
    }
    let mut out = Vec::new();
    for p in sections
        .iter_mut()
        .filter(|s| s.name == "REVIEW REQUESTED")
        .flat_map(|s| s.prs.iter_mut().flatten())
    {
        let Some(e) = last.get(&p.url) else { continue };
        let changed = if !e.head.is_empty() {
            // ponytail: the entry knows its head but the row does not: the graphql call failed on this
            // fetch, or returned no node for this PR, and nothing here can tell. Falling through to the
            // timestamp would call it changed (posting a review is itself an update) and auto would
            // re-review every tick. Ceiling: a PR permanently missing from graphql is never re-reviewed.
            if p.head.is_empty() {
                continue;
            }
            p.head != e.head
        } else {
            matches!((when(&p.updated_at), when(&e.at)), (Ok(a), Ok(b)) if a > b)
        };
        if changed {
            p.prev = format!("↻ re-review · was {}", config::status(&e.verdict).unwrap_or(""));
            out.push(p.url.clone());
        }
    }
    out
}

/// The full review as plain text.
pub fn detail(e: &LogEntry) -> String {
    let p = &e.pr;
    let at = when(&e.at)
        .map(|t| t.with_timezone(&Local).format("%Y-%m-%d %H:%M").to_string())
        .unwrap_or_else(|_| e.at.clone());
    let r = format!("{}#{}", p.repo(), p.number);
    let bar = "─".repeat((r.chars().count() + p.title.chars().count() + 2).clamp(40, 78));
    let author = p
        .author
        .as_ref()
        .map(|a| a.login.as_str())
        .filter(|l| !l.is_empty())
        .unwrap_or("?");
    let summary = if e.summary.is_empty() {
        "(no summary)"
    } else {
        &e.summary
    };
    format!(
        "{bar}\n{r}  {}\n{bar}\n  author   {author}\n  url      {}\n  reviewed {at} by {} {}  →  {}\n\n\
         WHAT THE PR DOES\n\n{summary}\n\nREVIEW\n\n{}\n\n{bar}\nq close   j/k or ↑/↓ scroll   o open in browser (from the list)\n",
        p.title,
        p.url,
        e.model,
        tag(e),
        config::status(&e.verdict).unwrap_or(""),
        e.body
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Login, Repository};
    use serde_json::json;
    use std::sync::MutexGuard;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    /// Point every path at a fresh temp dir; hold the guard for the test's life.
    fn isolated() -> (MutexGuard<'static, ()>, tempfile::TempDir) {
        let guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        config::update(|c| {
            c.log = root.join("log.jsonl");
            c.teams = root.join("teams");
            c.team = root.join("team");
            c.bindings = root.join("bindings");
            c.memory_dir = root.join("memory");
            c.depth = "adaptive".into();
            c.effort = "medium".into();
        });
        (guard, dir)
    }

    fn pr() -> Pr {
        Pr {
            number: 7,
            title: "T".into(),
            url: "u".into(),
            updated_at: "2020-01-01T00:00:00Z".into(),
            author: Some(Login { login: "me".into() }),
            repository: Repository {
                name_with_owner: "a/b".into(),
                name: "b".into(),
            },
            ..Default::default()
        }
    }

    fn verdict(v: &str, body: &str) -> Verdict {
        Verdict {
            verdict: v.into(),
            body: body.into(),
            ..Default::default()
        }
    }

    fn write_lines(path: &Path, lines: &[String]) {
        std::fs::write(path, lines.iter().map(|l| format!("{l}\n")).collect::<String>()).unwrap();
    }

    fn good() -> Value {
        json!({"at": "2020-01-01T00:00:00+00:00", "model": "opus", "verdict": "approve", "summary": "", "body": "",
               "pr": {"repository": {"nameWithOwner": "a/b", "name": "b"}, "number": 1, "url": "keep"}})
    }

    #[test]
    fn reviewed_empty_when_no_log() {
        let _g = isolated();
        assert!(reviewed().is_empty());
    }

    #[test]
    fn reviewed_newest_first_and_detail() {
        let _g = isolated();
        let mut v = verdict("approve", "lgtm");
        v.summary = "adds x".into();
        log_review(
            &Pr {
                url: "first".into(),
                ..pr()
            },
            "opus",
            &v,
            Some("2026-01-01T00:00:00+00:00"),
        )
        .unwrap();
        log_review(
            &Pr {
                url: "second".into(),
                ..pr()
            },
            "opus",
            &v,
            Some("2026-01-01T00:00:01+00:00"),
        )
        .unwrap();
        let got = reviewed();
        assert_eq!(
            got.iter().map(|p| p.url.as_str()).collect::<Vec<_>>(),
            ["second", "first"]
        );
        assert_eq!(got[0].status, "✓ approved");
        assert_eq!(got[0].updated_at, got[0].review.as_ref().unwrap().at);
        assert_eq!(got[0].tag, "adaptive/medium");
        let d = detail(got[0].review.as_ref().unwrap());
        for s in ["a/b#7  T", "adds x", "lgtm", "opus", "q close", "author   me"] {
            assert!(d.contains(s), "{s} missing from {d}");
        }
        assert!(!d.contains("+00:00"));
        assert_eq!(last("first").unwrap().pr.url, "first");
        assert!(last("nope").is_none());
    }

    #[test]
    fn same_second_entries_keep_later_line_first() {
        let _g = isolated();
        let v = verdict("approve", "ok");
        for u in ["a", "b", "c"] {
            log_review(
                &Pr {
                    url: u.into(),
                    ..pr()
                },
                "opus",
                &v,
                Some("2020-01-01T00:00:00+00:00"),
            )
            .unwrap();
        }
        assert_eq!(
            reviewed().iter().map(|p| p.url.as_str()).collect::<Vec<_>>(),
            ["c", "b", "a"]
        );
    }

    #[test]
    fn reviewed_tolerates_sparse_entries() {
        let _g = isolated();
        let line = json!({"at": "2020-01-01T00:00:00+00:00", "model": "opus", "verdict": "comment", "summary": "",
                          "body": "", "pr": {"repository": {"nameWithOwner": "a/b"}, "number": 1, "url": "u"}});
        write_lines(&config::get().log, &[line.to_string()]);
        let p = &reviewed()[0];
        assert_eq!(p.title, "?");
        assert!(!p.is_draft && p.author.is_none());
        assert_eq!(p.status, "~ commented");
    }

    #[test]
    fn mark_rereviews_flags_updated_logged_prs_only() {
        let _g = isolated();
        let at = Some("2020-01-01T00:00:00+00:00");
        log_review(
            &Pr {
                url: "old".into(),
                ..pr()
            },
            "opus",
            &verdict("approve", "ok"),
            at,
        )
        .unwrap();
        log_review(
            &Pr {
                url: "same".into(),
                ..pr()
            },
            "opus",
            &verdict("comment", "hm"),
            at,
        )
        .unwrap();
        let old = Pr {
            url: "old".into(),
            updated_at: "2021-01-01T00:00:00Z".into(),
            ..pr()
        };
        let same = Pr {
            url: "same".into(),
            ..pr()
        };
        let fresh = Pr {
            url: "fresh".into(),
            ..pr()
        };
        let mut secs = vec![
            Section {
                name: "REVIEW REQUESTED".into(),
                prs: Some(vec![old, same, fresh]),
                err: None,
            },
            Section {
                name: "REVIEWED".into(),
                prs: Some(reviewed()),
                err: None,
            },
        ];
        assert_eq!(mark_rereviews(&mut secs), ["old"]);
        let rows = secs[0].prs.as_ref().unwrap();
        assert_eq!(rows[0].prev, "↻ re-review · was ✓ approved");
        assert!(rows[1].prev.is_empty() && rows[2].prev.is_empty());
    }

    #[test]
    fn findings_are_kept_but_never_trusted() {
        let f = |v: Value| {
            findings(&Verdict {
                findings: v.as_array().cloned().unwrap_or_default(),
                ..Default::default()
            })
        };
        let good = json!([{"kind": "Blocking", "loc": "keymap.ts:88", "text": "duplicate  binding\nnot detected"},
                          {"kind": "nit", "text": "no loc is fine"}]);
        assert_eq!(
            f(good),
            vec![
                Finding {
                    kind: "blocking".into(),
                    loc: "keymap.ts:88".into(),
                    text: "duplicate binding not detected".into()
                },
                Finding {
                    kind: "nit".into(),
                    loc: "".into(),
                    text: "no loc is fine".into()
                },
            ]
        );
        assert!(f(json!(null)).is_empty());
        assert!(f(json!(["a string", {"kind": "bogus", "text": "x"}, {"kind": "nit"}, 7, {"kind": "nit", "text": ""}])).is_empty());
        let many: Vec<Value> = (0..40)
            .map(|i| json!({"kind": "nit", "text": format!("f{i}")}))
            .collect();
        assert_eq!(f(Value::Array(many)).len(), 12);
        assert_eq!(kind_tone("note"), Some("warn"));
        assert_eq!(kind_tone("x"), None);
    }

    #[test]
    fn mark_rereviews_prefers_the_head_commit_over_the_timestamp() {
        let _g = isolated();
        let at = Some("2020-01-01T00:00:00+00:00");
        let v = verdict("approve", "ok");
        log_review(
            &Pr {
                url: "c".into(),
                head: "aaa".into(),
                ..pr()
            },
            "opus",
            &v,
            at,
        )
        .unwrap();
        log_review(
            &Pr {
                url: "p".into(),
                head: "aaa".into(),
                ..pr()
            },
            "opus",
            &v,
            at,
        )
        .unwrap();
        let commented = Pr {
            url: "c".into(),
            head: "aaa".into(),
            updated_at: "2021-01-01T00:00:00Z".into(),
            ..pr()
        };
        let pushed = Pr {
            url: "p".into(),
            head: "bbb".into(),
            updated_at: "2020-01-01T00:00:00Z".into(),
            ..pr()
        };
        let mut secs = vec![
            Section {
                name: "REVIEW REQUESTED".into(),
                prs: Some(vec![commented, pushed]),
                err: None,
            },
            Section {
                name: "REVIEWED".into(),
                prs: Some(reviewed()),
                err: None,
            },
        ];
        assert_eq!(mark_rereviews(&mut secs), ["p"]);
        assert!(secs[0].prs.as_ref().unwrap()[0].prev.is_empty());
    }

    #[test]
    fn a_pr_with_no_head_is_never_called_pushed_to() {
        let _g = isolated();
        let at = Some("2020-01-01T00:00:00+00:00");
        log_review(
            &Pr {
                url: "p".into(),
                head: "aaa".into(),
                ..pr()
            },
            "opus",
            &verdict("approve", "ok"),
            at,
        )
        .unwrap();
        let unknown = Pr {
            url: "p".into(),
            updated_at: "2021-01-01T00:00:00Z".into(),
            ..pr()
        };
        let mut secs = vec![
            Section {
                name: "REVIEW REQUESTED".into(),
                prs: Some(vec![unknown]),
                err: None,
            },
            Section {
                name: "REVIEWED".into(),
                prs: Some(reviewed()),
                err: None,
            },
        ];
        assert!(mark_rereviews(&mut secs).is_empty());
        assert!(secs[0].prs.as_ref().unwrap()[0].prev.is_empty());
    }

    #[test]
    fn tag_carries_cost_and_duration() {
        let e = |depth: &str, effort: &str, cost: Option<f64>, ms: Option<u64>| LogEntry {
            depth: depth.into(),
            effort: effort.into(),
            cost,
            ms,
            ..Default::default()
        };
        assert_eq!(
            tag(&e("high", "max", Some(0.4171), Some(184_000))),
            "high/max $0.42 3m"
        );
        assert_eq!(tag(&e("low", "", None, Some(9_400))), "low 9s");
        assert_eq!(tag(&e("low", "", Some(0.0), Some(0))), "low");
        assert_eq!(tag(&e("", "", None, None)), "");
    }

    #[test]
    fn reviewed_skips_lines_it_cannot_read() {
        let _g = isolated();
        let mut torn = good();
        torn["at"] = json!(5);
        write_lines(
            &config::get().log,
            &[
                good().to_string(),
                "{\"at\":\"2020-01-0".into(),
                "<<<<<<< HEAD".into(),
                torn.to_string(),
                json!({"at": "2020-01-01T00:00:00+00:00"}).to_string(),
                json!({"at": "yesterday", "verdict": "approve", "pr": {}}).to_string(),
                json!({"at": "2020-01-01T00:00:00+00:00", "verdict": "approve", "pr": 7}).to_string(),
            ],
        );
        assert_eq!(
            reviewed().iter().map(|p| p.url.as_str()).collect::<Vec<_>>(),
            ["keep"]
        );
    }

    #[test]
    fn an_unknown_verdict_drops_its_entry() {
        let _g = isolated();
        let mut bad = good();
        bad["verdict"] = json!("needs_work");
        bad["pr"]["url"] = json!("gone");
        write_lines(&config::get().log, &[good().to_string(), bad.to_string()]);
        assert_eq!(
            reviewed().iter().map(|p| p.url.as_str()).collect::<Vec<_>>(),
            ["keep"]
        );
        assert!(log_review(&pr(), "opus", &verdict("needs_work", "x"), None).is_err());
    }

    #[test]
    fn an_entry_written_without_an_offset_is_read_as_utc() {
        let _g = isolated();
        let mut naive = good();
        naive["at"] = json!("2020-01-01T00:00:00");
        naive["pr"]["url"] = json!("u");
        write_lines(&config::get().log, &[naive.to_string()]);
        let got = reviewed();
        assert_eq!(got[0].review.as_ref().unwrap().at, "2020-01-01T00:00:00+00:00");
        let moved = Pr {
            url: "u".into(),
            updated_at: "2021-01-01T00:00:00Z".into(),
            ..pr()
        };
        let mut secs = vec![
            Section {
                name: "REVIEW REQUESTED".into(),
                prs: Some(vec![moved]),
                err: None,
            },
            Section {
                name: "REVIEWED".into(),
                prs: Some(got),
                err: None,
            },
        ];
        assert_eq!(mark_rereviews(&mut secs), ["u"]);
    }

    #[test]
    fn entries_from_other_offsets_sort_by_instant_not_by_text() {
        let _g = isolated();
        let mut a = good();
        a["at"] = json!("2020-01-01T08:00:00+00:00");
        a["pr"]["url"] = json!("at_0800z");
        let mut b = good();
        b["at"] = json!("2020-01-01T09:30:00+02:00"); // 07:30Z, half an hour earlier
        b["pr"]["url"] = json!("at_0730z");
        write_lines(&config::get().log, &[a.to_string(), b.to_string()]);
        let got = reviewed();
        assert_eq!(
            got.iter().map(|p| p.url.as_str()).collect::<Vec<_>>(),
            ["at_0800z", "at_0730z"]
        );
        assert!(got
            .iter()
            .all(|p| p.review.as_ref().unwrap().at.ends_with("+00:00")));
    }

    #[test]
    fn cache_refreshes_when_the_file_changes() {
        let _g = isolated();
        let path = config::get().log;
        write_lines(&path, &[good().to_string()]);
        assert_eq!(reviewed().len(), 1);
        let mut b = good();
        b["at"] = json!("2020-01-02T00:00:00+00:00");
        write_lines(&path, &[good().to_string(), b.to_string()]);
        assert_eq!(reviewed().len(), 2);
    }

    #[test]
    fn when_reads_every_shape() {
        assert_eq!(
            iso(when("2020-01-01T09:30:00+02:00").unwrap()),
            "2020-01-01T07:30:00+00:00"
        );
        assert_eq!(
            iso(when("2020-01-01T00:00:00Z").unwrap()),
            "2020-01-01T00:00:00+00:00"
        );
        assert_eq!(
            iso(when("2020-01-01T00:00:00").unwrap()),
            "2020-01-01T00:00:00+00:00"
        );
        assert_eq!(
            iso(when("2020-01-01 00:00:00.123").unwrap()),
            "2020-01-01T00:00:00+00:00"
        );
        assert!(when("yesterday").is_err());
    }

    #[test]
    fn log_line_keeps_the_python_keys() {
        let _g = isolated();
        let v = Verdict {
            cost: Some(0.5),
            ms: Some(1200),
            ..verdict("approve", "ok")
        };
        log_review(
            &Pr {
                checks: "✓".into(),
                ..pr()
            },
            "opus",
            &v,
            Some("2020-01-01T00:00:00+00:00"),
        )
        .unwrap();
        let line = std::fs::read_to_string(config::get().log).unwrap();
        let e: Value = serde_json::from_str(line.trim()).unwrap();
        for k in [
            "at", "model", "pr", "depth", "effort", "cost", "ms", "verdict", "summary", "body", "findings",
        ] {
            assert!(e.get(k).is_some(), "{k}");
        }
        assert!(e["pr"].get("checks").is_none());
        assert_eq!(e["depth"], "adaptive");
        assert_eq!(e["cost"], 0.5);
    }
}
