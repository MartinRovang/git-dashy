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
#[derive(Clone, Debug, Serialize, Deserialize)]
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
}

fn dir() -> PathBuf {
    crate::config::get().held_dir
}

/// ponytail: derived from the repo and number, the way review::self_review_path is, so a held review
/// is found again after a restart without a second store saying where it went.
pub fn path(repo: &str, n: u64) -> PathBuf {
    dir().join(format!("{}__{n}.json", repo.replace('/', "__")))
}

/// Park a finished review. Returns where it went.
pub fn put(h: &Held) -> Result<PathBuf> {
    let p = path(h.pr.repo(), h.pr.number);
    std::fs::create_dir_all(dir())?;
    std::fs::write(&p, serde_json::to_string_pretty(h)?)?;
    Ok(p)
}

/// The held review for one PR, if there is one.
///
/// ponytail: a file it cannot parse is None, not an error. This store is read on every draw to
/// decide a row's status; one unreadable file must not take the dashboard down, and the review it
/// described is gone either way.
pub fn get(repo: &str, n: u64) -> Option<Held> {
    let text = std::fs::read_to_string(path(repo, n)).ok()?;
    serde_json::from_str(&text).ok()
}

/// Which PRs have a review waiting, from the filenames alone.
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
    match std::fs::remove_file(path(repo, n)) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e.into()),
        _ => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
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
        std::fs::write(path("acme/api", 7), "not json at all").unwrap();
        assert!(get("acme/api", 7).is_none());
        put(&held("acme/web", 1, 50.0)).unwrap();
        assert!(get("acme/web", 1).is_some(), "the readable one still reads");
    }

    #[test]
    /// The payload asks this per poll and only wants the row keys, so it reads names, not verdicts.
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
        std::fs::write(path("acme/api", 7), "not json at all").unwrap();
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
