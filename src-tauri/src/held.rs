//! Reviews that finished but have not been posted (~/.prs_held).
//!
//! A held review is the finished verdict on disk, waiting for a keypress. Running and posting were
//! one call, so a verdict you disagreed with was already on the PR by the time you read it — the
//! same rule the memory pool is built on: automate where being wrong costs only you, require a
//! keypress where it costs other people.
//!
//! ponytail: the whole verdict, not the rendered markdown. Posting needs the structured
//! `approve`/`request_changes`/`comment` and the body, and the log entry needs the findings and the
//! kind; parsing those back out of a file written for a human is how a store starts lying.
//!
//! ponytail: its own directory, not ~/.prs_reviews. That one holds pre-reviews of YOUR PRs, which
//! `p` finds again by deriving the name — the same derivation, so a held review of someone else's PR
//! would be indistinguishable from a pre-review of your own on any row where both could exist.

use std::collections::HashSet;
use std::path::PathBuf;

use anyhow::Result;
use serde::{Deserialize, Serialize};

use crate::types::{Pr, Verdict};

/// Everything the post needs, so posting never re-runs the model.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Held {
    pub pr: Pr,
    pub model: String,
    pub verdict: Verdict,
    /// The comment posted before the review starts. Held with it so the PR still reads in order.
    #[serde(default)]
    pub hello: String,
    /// When the review finished, seconds since the epoch.
    #[serde(default)]
    pub at: f64,
    /// The claude session the review ran in, "" for any other backend or a review held before this
    /// was saved. A discussion resumes it, so the agent still has everything it read.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub session: String,
    /// The team whose repos the review was allowed to read, "" for none. A discussion resumes under this,
    /// not under whatever the repo is bound to by then.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub team: String,
    /// The discussion so far, oldest first.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub thread: Vec<Turn>,
    /// A revised verdict the agent wrote after the discussion, waiting for a yes or a no.
    ///
    /// ponytail: beside the verdict, never over it. `verdict` is what posts, and the verdict you read
    /// has to be the verdict that goes up; a revision replaces it only when accepted, and a release
    /// is refused while one is waiting.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proposed: Option<Verdict>,
}

/// One message in a discussion of a held review.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Turn {
    /// "you", "agent", or "error" when a turn failed.
    pub who: String,
    pub text: String,
    #[serde(default)]
    pub at: f64,
}

fn dir() -> PathBuf {
    crate::config::get().held_dir
}

/// Where one held review lives, or None when `repo` is not a name this can key on.
///
/// ponytail: derived from the repo and number, the way review::self_review_path is, so a held review
/// is found again after a restart without a second store saying where it went.
/// ponytail: folded through bind::key first, and refused when that fails. `repo` arrives from an
/// HTTP body, and discard calls remove_file on what comes back — replacing `/` alone left a `\` or
/// a `..` segment to walk straight out of the directory on a host whose separator is not `/`. The
/// fold accepts exactly one `owner/name` and nothing else, which is the same guard set_post uses.
pub fn path(repo: &str, n: u64) -> Option<PathBuf> {
    let key = crate::bind::key(repo);
    if key.is_empty() {
        return None;
    }
    Some(dir().join(format!("{}__{n}.json", key.replace('/', "__"))))
}

/// Park a finished review. Returns where it went.
pub fn put(h: &Held) -> Result<PathBuf> {
    let p = path(h.pr.repo(), h.pr.number)
        .ok_or_else(|| anyhow::anyhow!("{} is not an owner/name", h.pr.repo()))?;
    std::fs::create_dir_all(dir())?;
    // ponytail: a temp file renamed over. The screen polls this file every 1.5s while a turn runs, and every
    // turn rewrites it; a read that caught a half-written file saw no held review at all.
    write_atomic(&p, serde_json::to_string_pretty(h)?.as_bytes())?;
    Ok(p)
}

/// Write `bytes` to a temp file beside `p` and rename it over, so a reader never sees half of it.
pub fn write_atomic(p: &std::path::Path, bytes: &[u8]) -> std::io::Result<()> {
    let tmp = p.with_extension("tmp");
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, p)
}

/// The held review for one PR, if there is one.
///
/// ponytail: a file it cannot parse is None, not an error. This store is read on every draw to
/// decide a row's status; one unreadable file must not take the dashboard down, and the review it
/// described is gone either way.
pub fn get(repo: &str, n: u64) -> Option<Held> {
    let text = std::fs::read_to_string(path(repo, n)?).ok()?;
    serde_json::from_str(&text).ok()
}

/// Whether this PR has a review waiting. `repo` is folded, so the caller may pass the raw name.
///
/// ponytail: the only lookup. `waiting()` returns folded keys because the filenames are folded, and a
/// caller comparing them against a raw `nameWithOwner` silently never matched — `MartinRovang` is
/// this repo's own owner, and every fixture was lowercase, so nothing caught it.
pub fn is_waiting(set: &HashSet<(String, u64)>, repo: &str, n: u64) -> bool {
    set.contains(&(crate::bind::key(repo), n))
}

/// Which PRs have a review waiting, from the filenames alone. Keys are FOLDED: ask with
/// `is_waiting`, never by building the tuple yourself.
///
/// ponytail: the names, not the contents. The payload asks this on every poll and only wants to know
/// WHICH rows are waiting; deserialising every parked verdict to answer that is work nobody reads.
/// The name is `<owner>__<repo>__<n>.json`, which is where both halves come from.
pub fn waiting() -> HashSet<(String, u64)> {
    std::fs::read_dir(dir())
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().into_string().ok()?;
            let stem = name.strip_suffix(".json")?;
            let (repo, n) = stem.rsplit_once("__")?;
            Some((repo.replacen("__", "/", 1), n.parse().ok()?))
        })
        .collect()
}

/// Forget one, posted or dropped. Missing is not an error: two keys racing is not a failure.
pub fn drop(repo: &str, n: u64) -> Result<()> {
    let Some(p) = path(repo, n) else {
        return Ok(()); // not a name we could have written: there is nothing of ours to remove
    };
    match std::fs::remove_file(p) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e.into()),
        _ => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_review_held_before_discussions_still_reads_and_a_quiet_one_writes_no_new_keys() {
        let old = r#"{"pr":{"number":7,"title":"T","url":"u","updatedAt":"2020-01-01T00:00:00Z","repository":{"nameWithOwner":"a/b","name":"b"}},"model":"opus","verdict":{"verdict":"approve"},"hello":"","at":1.0}"#;
        let h: Held = serde_json::from_str(old).expect("a file from before this change");
        assert!(h.session.is_empty() && h.thread.is_empty() && h.proposed.is_none());
        let back = serde_json::to_string(&h).unwrap();
        for key in ["session", "thread", "proposed", "instructions"] {
            assert!(!back.contains(&format!("\"{key}\"")), "{key} in {back}");
        }
    }

    use crate::types::{Login, Repository};

    fn fresh() -> (std::sync::MutexGuard<'static, ()>, tempfile::TempDir) {
        let g = crate::autorev::test_lock();
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.held_dir = d.path().join("held"));
        (g, d)
    }

    fn held(repo: &str, n: u64, at: f64) -> Held {
        Held {
            pr: Pr {
                number: n,
                url: format!("https://x/{repo}/{n}"),
                author: Some(Login { login: "bob".into() }),
                repository: Repository {
                    name_with_owner: repo.into(),
                    name: repo.split('/').next_back().unwrap_or(repo).into(),
                },
                ..Default::default()
            },
            model: "opus".into(),
            verdict: Verdict {
                verdict: "request_changes".into(),
                summary: "one real bug".into(),
                body: "## Findings\n- a thing".into(),
                ..Default::default()
            },
            hello: "Reviewing with opus".into(),
            at,
            ..Default::default()
        }
    }

    #[test]
    fn a_held_review_comes_back_whole() {
        let (_g, _d) = fresh();
        put(&held("acme/api", 7, 100.0)).unwrap();
        let got = get("acme/api", 7).unwrap();
        assert_eq!(got.verdict.verdict, "request_changes");
        assert_eq!(got.verdict.body, "## Findings\n- a thing");
        assert_eq!(got.hello, "Reviewing with opus");
        assert_eq!(got.pr.number, 7);
        assert_eq!(got.model, "opus");
    }

    /// The name is derived, so a restart finds it without a second store saying where it went.
    #[test]
    fn the_path_is_derived_from_the_repo_and_number() {
        let (_g, d) = fresh();
        put(&held("acme/api", 7, 100.0)).unwrap();
        assert!(d.path().join("held/acme__api__7.json").exists());
    }

    /// `repo` arrives from an HTTP body and discard calls remove_file on what comes back. Every path
    /// this hands back must be one file directly inside the store, whatever was typed: no separator
    /// survives the fold, so nothing walks out of the directory on any host.
    #[test]
    fn no_name_can_reach_outside_the_store() {
        let (_g, d) = fresh();
        let store = d.path().join("held");
        let keyed = |t: &str| path(t, 7).is_some();
        assert!(
            !keyed("..\\..\\windows\\system32"),
            "a backslash name keys nothing at all"
        );
        assert!(keyed("acme/api"), "and a real one still does");
        for typed in [
            "../../etc/passwd",
            "..\\..\\windows\\system32",
            "acme/api/../../../etc",
            "acme/../../../api",
            "https://github.com/acme/api",
            "acme/api",
        ] {
            // either it keys nothing, or it keys one file directly inside the store — never a path
            let Some(p) = path(typed, 7) else { continue };
            assert_eq!(p.parent(), Some(store.as_path()), "{typed:?} left the store");
            let name = p.file_name().unwrap().to_string_lossy().to_string();
            assert!(
                !name.contains("..") && !name.contains('/') && !name.contains('\\'),
                "{name:?}"
            );
            assert!(name.ends_with("__7.json"), "{name:?}");
        }
    }

    /// And a name it cannot key at all writes nothing and removes nothing.
    #[test]
    fn a_name_that_is_not_one_repo_gets_no_path_at_all() {
        let (_g, _d) = fresh();
        for bad in ["notes", "", "/", "   "] {
            assert!(path(bad, 7).is_none(), "{bad:?} should not key a file");
            assert!(get(bad, 7).is_none());
            assert!(drop(bad, 7).is_ok(), "{bad:?}: nothing of ours to remove");
        }
    }

    /// The fold is bind's, so a URL, an ssh remote and a bare name land on one file.
    #[test]
    fn a_url_and_a_bare_name_are_the_same_held_review() {
        let (_g, d) = fresh();
        put(&held("Acme/API", 7, 100.0)).unwrap();
        assert!(d.path().join("held/acme__api__7.json").exists());
        assert!(get("https://github.com/acme/api", 7).is_some());
        assert!(get("ACME/API", 7).is_some());
    }

    #[test]
    fn nothing_held_is_none_not_an_error() {
        let (_g, _d) = fresh();
        assert!(get("acme/api", 7).is_none());
        assert!(waiting().is_empty());
    }

    /// Read on every draw: one unreadable file must not take the dashboard down.
    #[test]
    fn a_file_it_cannot_parse_is_none() {
        let (_g, _d) = fresh();
        std::fs::create_dir_all(dir()).unwrap();
        std::fs::write(path("acme/api", 7).unwrap(), "not json at all").unwrap();
        assert!(get("acme/api", 7).is_none());
        put(&held("acme/web", 1, 50.0)).unwrap();
        assert!(get("acme/web", 1).is_some(), "the readable one still reads");
    }

    /// The fold lowercases, so the set's keys are lowercase and a raw nameWithOwner never matches.
    /// `MartinRovang/git-dashy` is this repo's own owner and every other fixture here is lowercase,
    /// so no test caught it.
    #[test]
    fn a_mixed_case_repo_is_found_by_the_name_the_board_uses() {
        let (_g, _d) = fresh();
        put(&held("MartinRovang/git-dashy", 7, 100.0)).unwrap();
        let w = waiting();
        assert!(
            is_waiting(&w, "MartinRovang/git-dashy", 7),
            "the raw name the board carries"
        );
        assert!(is_waiting(&w, "martinrovang/git-dashy", 7), "and the folded one");
        assert!(!is_waiting(&w, "MartinRovang/git-dashy", 8));
        assert!(!is_waiting(&w, "someone/else", 7));
        assert!(get("MartinRovang/git-dashy", 7).is_some());
    }

    /// The payload asks this per poll and only wants the row keys, so it reads names, not verdicts.
    #[test]
    fn waiting_names_every_held_pr_without_reading_one() {
        let (_g, _d) = fresh();
        put(&held("acme/api", 7, 100.0)).unwrap();
        put(&held("acme/web", 1, 300.0)).unwrap();
        put(&held("other/thing", 2, 200.0)).unwrap();
        let w = waiting();
        assert_eq!(w.len(), 3);
        assert!(w.contains(&("acme/api".to_string(), 7)));
        assert!(w.contains(&("other/thing".to_string(), 2)));
        assert!(!w.contains(&("acme/api".to_string(), 8)));
    }

    /// It reads names, so a file it could not parse still marks the row — which is right: the review
    /// is there, and Y says what is wrong with it rather than the row pretending nothing waits.
    #[test]
    fn a_file_it_cannot_parse_still_marks_the_row() {
        let (_g, _d) = fresh();
        std::fs::create_dir_all(dir()).unwrap();
        std::fs::write(path("acme/api", 7).unwrap(), "not json at all").unwrap();
        assert!(waiting().contains(&("acme/api".to_string(), 7)));
        assert!(get("acme/api", 7).is_none());
    }

    #[test]
    fn a_name_it_cannot_read_is_skipped() {
        let (_g, d) = fresh();
        std::fs::create_dir_all(dir()).unwrap();
        for bad in ["notes.json", "acme__api__notanumber.json", "acme__api__7.txt"] {
            std::fs::write(d.path().join("held").join(bad), "{}").unwrap();
        }
        put(&held("acme/api", 7, 100.0)).unwrap();
        assert_eq!(waiting(), HashSet::from([("acme/api".to_string(), 7)]));
    }

    #[test]
    fn a_second_review_of_one_pr_replaces_the_first() {
        let (_g, _d) = fresh();
        put(&held("acme/api", 7, 100.0)).unwrap();
        let mut newer = held("acme/api", 7, 200.0);
        newer.verdict.verdict = "approve".into();
        put(&newer).unwrap();
        assert_eq!(waiting().len(), 1);
        assert_eq!(get("acme/api", 7).unwrap().verdict.verdict, "approve");
    }

    #[test]
    fn dropping_one_leaves_the_others_and_dropping_nothing_is_fine() {
        let (_g, _d) = fresh();
        put(&held("acme/api", 7, 100.0)).unwrap();
        put(&held("acme/web", 1, 200.0)).unwrap();
        drop("acme/api", 7).unwrap();
        assert!(get("acme/api", 7).is_none());
        assert_eq!(waiting().len(), 1);
        drop("acme/api", 7).unwrap(); // already gone: two keys racing is not a failure
    }
}
