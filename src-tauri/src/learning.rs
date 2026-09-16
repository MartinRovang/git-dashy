//! How fast the memory learns: dated events for the Knowledge chart.
//!
//! A fact or a draft is a markdown line with no date on it, so "when was this learned" is answered from two
//! places. From now on, the moment itself: every draft proposed, every draft seen again, every fact gained
//! is appended to ~/.prs_learning.jsonl where it happens. Before the first line of that file, git: your
//! memory dir and every team checkout commit each write, and a commit's diff says what was added.
//!
//! ponytail: team arrivals come from git always, not from the log. A teammate's fact reaches this machine by
//! a pull, and the team checkout's own history already says who wrote it and when, exactly -- a log written
//! here would only know when the pull happened.

use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};

use crate::{config, memory, team};

/// One thing learned.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Event {
    /// Seconds since the epoch.
    pub at: f64,
    /// fact | draft | arrival
    pub kind: String,
    /// "owner/name", or "" for a general fact.
    pub repo: String,
    /// The team it happened in, "" for your own memory.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub team: String,
    /// Who: you for your own memory, the person whose pool or drafts it landed in for a team.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub who: String,
    /// How: a draft's review | pre-review | session; a fact's "seen twice" | hand | teammate, "" from git (earlier);
    /// an arrival's fact | draft.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub source: String,
    /// The fact's text, to tell a first appearance from a file rewritten with it still in. Never sent.
    #[serde(skip)]
    pub fact: String,
}

fn log_path() -> PathBuf {
    config::get().learning
}

/// Record one event now, in your own memory. Never fails a caller: a lost chart point is not worth a lost review.
pub fn record(kind: &str, repo: &str, source: &str) {
    let c = config::get();
    if c.demo || c.learning.as_os_str().is_empty() {
        return;
    }
    let e = Event {
        at: crate::state::now(),
        kind: kind.into(),
        repo: repo.into(),
        who: memory::whoami(),
        source: source.into(),
        ..Default::default()
    };
    let line = match serde_json::to_string(&e) {
        Ok(l) => l,
        Err(_) => return,
    };
    use std::io::Write;
    let wrote = std::fs::OpenOptions::new()
        .append(true)
        .create(true)
        .open(log_path())
        .and_then(|mut f| writeln!(f, "{line}"));
    if let Err(e) = wrote {
        log::debug!("learning event not recorded: {e}");
    }
}

/// The recorded events, oldest first. A line that does not parse is skipped, not the file.
pub fn recorded() -> Vec<Event> {
    std::fs::read_to_string(log_path())
        .unwrap_or_default()
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect()
}

/// "MartinRovang__git-dashy.md" -> "MartinRovang/git-dashy", "general.md" -> "".
///
/// None for anything else, which is how the brief (project.md), the agents file and every dotfile stay out of
/// the chart: none of them is `general` or `owner__name`.
fn repo_of(file: &str) -> Option<String> {
    let stem = file.strip_suffix(".md")?;
    if stem == "general" {
        return Some(String::new());
    }
    let (owner, name) = stem.split_once("__")?;
    Some(format!("{owner}/{name}"))
}

/// A commit's subject that is housekeeping, not learning: its diff moves lines that were already known.
///
/// ponytail: skipped whole. A dream rewrites a file and a restore puts one back, which as a diff is dozens of
/// facts "added" in one minute; a fold turns two drafts into one; a forget and a withdraw only remove.
fn housekeeping(subject: &str) -> bool {
    let s = subject.to_lowercase();
    ["restore", "dream", "folded", "forget", "withdraw", "brief"]
        .iter()
        .any(|w| s.contains(w))
}

/// (fact, count) for each draft line of a diff side; a fact line without a count counts 1.
fn drafts(lines: &[String]) -> Vec<(String, u32)> {
    lines
        .iter()
        .filter(|l| l.trim_start().starts_with('-'))
        .map(|l| {
            let d = memory::parse(l);
            (d.fact, d.count)
        })
        .filter(|(f, _)| !f.is_empty())
        .collect()
}

/// The facts a diff side adds: bullet lines that the other side does not also carry (a moved line is not news).
fn added_facts(removed: &[String], added: &[String]) -> Vec<String> {
    let gone: Vec<String> = removed.iter().map(|l| memory::parse(l).fact).collect();
    added
        .iter()
        .filter(|l| l.trim_start().starts_with("- "))
        .map(|l| memory::parse(l).fact)
        .filter(|f| !f.is_empty() && !gone.contains(f))
        .collect()
}

/// Everything one file's diff in one commit says was learned.
#[allow(clippy::too_many_arguments)]
fn classify(
    path: &str,
    team_key: &str,
    at: f64,
    author: &str,
    subject: &str,
    removed: &[String],
    added: &[String],
) -> Vec<Event> {
    let mut out = Vec::new();
    let ev = |kind: &str, repo: &str, who: &str, source: &str, fact: &str| Event {
        at,
        kind: kind.into(),
        repo: repo.into(),
        team: team_key.into(),
        who: who.to_lowercase(),
        source: source.into(),
        fact: fact.into(),
    };
    // a team checkout keeps its memory under memory/; your own memory dir is the memory itself
    let rel = if team_key.is_empty() {
        path
    } else {
        match path.strip_prefix("memory/") {
            Some(r) => r,
            None => return out,
        }
    };
    let parts: Vec<&str> = rel.split('/').collect();
    let file = *parts.last().unwrap_or(&"");
    let Some(repo) = repo_of(file) else {
        return out;
    };
    // drafts diff: a line that was not there before is a new draft. A count going up is not an event: at the
    // gate's threshold of two it is the moment the draft becomes a fact, which the facts file records.
    let draft_events = |source: &str, who: &str, new_kind: &str| -> Vec<Event> {
        let before = drafts(removed);
        drafts(added)
            .into_iter()
            .filter(|(fact, _)| !before.iter().any(|(f, _)| f == fact))
            .map(|(fact, _)| ev(new_kind, &repo, who, source, &fact))
            .collect()
    };
    match (team_key.is_empty(), parts.as_slice()) {
        // your own memory
        (true, ["drafts", "self", _]) => out.extend(draft_events("pre-review", "", "draft")),
        (true, ["drafts", _]) => {
            let source = if subject.contains("remembered for") {
                "session"
            } else {
                "review"
            };
            out.extend(draft_events(source, "", "draft"))
        }
        (true, [_]) => {
            for f in added_facts(removed, added) {
                out.push(ev("fact", &repo, "", "", &f));
            }
        }
        // a team: what each person sent, and the team's own facts
        (false, ["pool", person, _]) => {
            for f in added_facts(removed, added) {
                out.push(ev("arrival", &repo, person, "fact", &f));
            }
        }
        (false, ["drafts", person, _]) => out.extend(draft_events("draft", person, "arrival")),
        (false, [_]) => {
            for f in added_facts(removed, added) {
                out.push(ev("fact", &repo, author, "", &f));
            }
        }
        _ => {}
    }
    out
}

/// The marker a commit header starts with in the log we ask git for.
const COMMIT: char = '\u{1}';

/// Parse `git log --reverse -p --unified=0 --format=%x01%at%x02%an%x02%s` into events.
pub fn parse_log(text: &str, team_key: &str) -> Vec<Event> {
    let mut out = Vec::new();
    let (mut at, mut author, mut subject) = (0.0, String::new(), String::new());
    let (mut path, mut removed, mut added) = (String::new(), Vec::new(), Vec::new());
    let mut skip = false;
    let flush = |out: &mut Vec<Event>,
                 path: &mut String,
                 removed: &mut Vec<String>,
                 added: &mut Vec<String>,
                 skip: bool,
                 at: f64,
                 author: &str,
                 subject: &str| {
        if !path.is_empty() && !skip {
            out.extend(classify(path, team_key, at, author, subject, removed, added));
        }
        path.clear();
        removed.clear();
        added.clear();
    };
    for line in text.lines() {
        if let Some(head) = line.strip_prefix(COMMIT) {
            flush(
                &mut out,
                &mut path,
                &mut removed,
                &mut added,
                skip,
                at,
                &author,
                &subject,
            );
            let mut f = head.splitn(3, '\u{2}');
            at = f.next().and_then(|t| t.trim().parse().ok()).unwrap_or(0.0);
            author = f.next().unwrap_or("").to_string();
            subject = f.next().unwrap_or("").to_string();
            skip = housekeeping(&subject);
        } else if let Some(rest) = line.strip_prefix("diff --git a/") {
            flush(
                &mut out,
                &mut path,
                &mut removed,
                &mut added,
                skip,
                at,
                &author,
                &subject,
            );
            path = rest.split(" b/").next().unwrap_or("").to_string();
        } else if line.starts_with("+++") || line.starts_with("---") {
        } else if let Some(l) = line.strip_prefix('+') {
            added.push(l.to_string());
        } else if let Some(l) = line.strip_prefix('-') {
            removed.push(l.to_string());
        }
    }
    flush(
        &mut out,
        &mut path,
        &mut removed,
        &mut added,
        skip,
        at,
        &author,
        &subject,
    );
    first_seen(out)
}

/// Keep a draft, fact or arrival only the first time it appears for that person, team and repo.
///
/// ponytail: a file rewritten is not a thing learned again. A teammate's draft pool is written out whole on each
/// sync, and a line one commit removes and a later one puts back read as a new arrival every time: 974 arrivals
/// from one person on this machine were 181 drafts.
pub fn first_seen(events: Vec<Event>) -> Vec<Event> {
    let mut seen = std::collections::HashSet::new();
    events
        .into_iter()
        .filter(|e| {
            e.kind == "confirm"
                || seen.insert((
                    e.kind.clone(),
                    e.team.clone(),
                    e.who.clone(),
                    e.repo.clone(),
                    e.fact.clone(),
                ))
        })
        .collect()
}

/// Everything git remembers of one repo, as events. Nothing when it is not a repo or git fails.
fn from_git(dir: &Path, team_key: &str) -> Vec<Event> {
    if !team::is_repo(dir) {
        return Vec::new();
    }
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args([
            "log",
            "--reverse",
            "--no-renames",
            "-p",
            "--unified=0",
            "--no-color",
            "--format=%x01%at%x02%an%x02%s",
        ])
        .output();
    match out {
        Ok(o) if o.status.success() => parse_log(&String::from_utf8_lossy(&o.stdout), team_key),
        _ => Vec::new(),
    }
}

/// Your own memory's events: git before the log began, the log after it.
///
/// ponytail: the cut is the first recorded event, not "now". Git keeps committing every write after the log
/// starts, so taking both for the same stretch counted every draft twice.
pub fn mine(from_git: Vec<Event>, recorded: Vec<Event>) -> Vec<Event> {
    let cut = recorded.iter().map(|e| e.at).fold(f64::INFINITY, f64::min);
    let mut all: Vec<Event> = from_git.into_iter().filter(|e| e.at < cut).collect();
    all.extend(recorded);
    all
}

/// Every event on this machine: your memory, then each team joined, oldest first.
pub fn events() -> Vec<Event> {
    let c = config::get();
    if c.demo {
        return Vec::new();
    }
    let mut all = mine(from_git(&c.memory_dir, ""), recorded());
    for key in team::joined() {
        all.extend(from_git(&c.teams.join(&key), &key));
    }
    all.sort_by(|a, b| a.at.total_cmp(&b.at));
    all
}

#[cfg(test)]
mod tests {
    use super::*;

    fn log(commits: &[(&str, &str, &str)]) -> String {
        // (subject, path, diff body lines)
        commits
            .iter()
            .enumerate()
            .map(|(i, (subject, path, body))| {
                format!(
                    "\u{1}{}\u{2}nilspontus\u{2}{subject}\n\ndiff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n{body}\n",
                    1000 + i
                )
            })
            .collect()
    }
    fn kinds(e: &[Event]) -> Vec<String> {
        e.iter().map(|e| e.kind.clone()).collect()
    }

    #[test]
    fn a_new_draft_and_the_fact_it_becomes_are_told_apart() {
        let text = log(&[
            (
                "memory: acme/api#7",
                "drafts/acme__api.md",
                "+- (1) [r:ab12] the pager guards total",
            ),
            (
                "memory: acme/api#9",
                "drafts/acme__api.md",
                "-- (1) [r:ab12] the pager guards total\n+- (2) [r:ab12,r:cd34] the pager guards total",
            ),
            ("memory: acme/api#9", "acme__api.md", "+- the pager guards total"),
        ]);
        let e = parse_log(&text, "");
        // the second sighting is the fact it became, not an event of its own
        assert_eq!(kinds(&e), ["draft", "fact"]);
        assert!(e.iter().all(|e| e.repo == "acme/api" && e.team.is_empty()));
        assert_eq!(e[0].source, "review");
        assert_eq!((e[0].at, e[1].at), (1000.0, 1002.0));
    }

    #[test]
    fn where_a_draft_came_from_is_kept() {
        let text = log(&[
            (
                "memory: pre-review acme/api#7",
                "drafts/self/acme__api.md",
                "+- (1) a pre-review saw this",
            ),
            (
                "memory: remembered for general",
                "drafts/general.md",
                "+- (1) a session filed this",
            ),
        ]);
        let e = parse_log(&text, "");
        assert_eq!(
            (e[0].source.as_str(), e[1].source.as_str()),
            ("pre-review", "session")
        );
        assert_eq!(e[1].repo, "", "general");
    }

    #[test]
    fn housekeeping_is_not_learning() {
        // a restore puts back a whole file, a dream rewrites one, a fold merges two drafts
        let text = log(&[
            (
                "restore general.md, emptied by a dream",
                "general.md",
                "+- one\n+- two\n+- three",
            ),
            (
                "memory: folded two drafts for acme/api",
                "drafts/acme__api.md",
                "+- (2) merged",
            ),
            ("memory: forget general", "general.md", "-- one"),
        ]);
        assert!(parse_log(&text, "").is_empty());
    }

    #[test]
    fn a_moved_line_is_not_a_new_fact_and_the_brief_is_not_a_fact_at_all() {
        let text = log(&[
            (
                "memory: acme/api#1",
                "acme__api.md",
                "-- already known\n+- already known",
            ),
            ("memory: acme/api#2", "project.md", "+- we build a dashboard"),
        ]);
        assert!(parse_log(&text, "").is_empty());
    }

    #[test]
    fn a_team_checkout_says_who_sent_what() {
        let text = log(&[
            (
                "memory: what my reviews have proposed",
                "memory/drafts/martin/acme__api.md",
                "+- (1) martin saw this",
            ),
            (
                "memory: evidence for acme/api",
                "memory/pool/pontus/acme__api.md",
                "+- pontus confirmed this",
            ),
            (
                "review acme/api#3: approve",
                "memory/acme__api.md",
                "+- the team knows this",
            ),
            ("review acme/api#3: approve", "reviewed.jsonl", "+{\"at\": 1}"),
            // a checkout keeps its memory under memory/: a notes file named like a repo at its root is not memory
            ("review acme/api#3: approve", "acme__api.md", "+- not memory"),
        ]);
        let e = parse_log(&text, "teamdashy");
        assert_eq!(kinds(&e), ["arrival", "arrival", "fact"]);
        assert_eq!((e[0].who.as_str(), e[0].source.as_str()), ("martin", "draft"));
        assert_eq!((e[1].who.as_str(), e[1].source.as_str()), ("pontus", "fact"));
        assert_eq!(e[2].who, "nilspontus", "a team fact is the committer's");
        assert!(e.iter().all(|e| e.team == "teamdashy"));
    }

    #[test]
    fn a_file_rewritten_with_a_line_still_in_it_is_not_learning_it_again() {
        let text = log(&[
            (
                "memory: what my reviews have proposed",
                "memory/drafts/martin/acme__api.md",
                "+- (1) martin saw this",
            ),
            (
                "memory: what my reviews have proposed",
                "memory/drafts/martin/acme__api.md",
                "-- (1) martin saw this",
            ),
            (
                "memory: what my reviews have proposed",
                "memory/drafts/martin/acme__api.md",
                "+- (1) martin saw this\n+- (1) and something new",
            ),
        ]);
        let e = parse_log(&text, "teamdashy");
        assert_eq!(e.len(), 2, "the re-add is the same arrival: {e:?}");
        assert_eq!(e[1].at, 1002.0);
        // the same text from someone else is their own arrival
        let other = log(&[
            ("x", "memory/drafts/martin/acme__api.md", "+- (1) seen twice"),
            ("x", "memory/drafts/pontus/acme__api.md", "+- (1) seen twice"),
        ]);
        assert_eq!(parse_log(&other, "teamdashy").len(), 2);
    }

    #[test]
    fn an_event_is_recorded_only_where_a_log_is_named() {
        let _g = crate::autorev::test_lock();
        let d = tempfile::tempdir().unwrap();
        // a test build names no log: nothing is written anywhere
        config::update(|c| c.learning = PathBuf::new());
        record("draft", "acme/api", "review");
        config::update(|c| c.learning = d.path().join("learning.jsonl"));
        record("draft", "acme/api", "review");
        record("fact", "", "hand");
        let got = recorded();
        assert_eq!(got.len(), 2);
        assert_eq!(
            (got[0].kind.as_str(), got[0].repo.as_str(), got[0].source.as_str()),
            ("draft", "acme/api", "review")
        );
        assert_eq!(
            (got[1].kind.as_str(), got[1].repo.as_str()),
            ("fact", ""),
            "general"
        );
        // a line that does not parse is skipped, not the file
        std::fs::write(
            d.path().join("learning.jsonl"),
            format!("not json\n{}\n", serde_json::to_string(&got[0]).unwrap()),
        )
        .unwrap();
        assert_eq!(recorded().len(), 1);
        config::update(|c| c.learning = PathBuf::new());
    }

    #[test]
    fn git_counts_only_until_the_log_begins() {
        let ev = |at: f64| Event {
            at,
            kind: "draft".into(),
            ..Default::default()
        };
        let all = mine(vec![ev(1.0), ev(5.0), ev(9.0)], vec![ev(5.0), ev(7.0)]);
        let ats: Vec<f64> = all.iter().map(|e| e.at).collect();
        assert_eq!(ats, [1.0, 5.0, 7.0], "git's 5 and 9 are the log's era");
        assert_eq!(mine(vec![ev(1.0)], vec![]).len(), 1, "no log yet: all of git");
    }
}
