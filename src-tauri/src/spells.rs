//! Spells (~/.prs_spells/<name>.md): one-time, in-depth investigations cast on one PR.
//!
//! Each spell is a whole markdown file you write outside the app; the book only lists and casts them. A cast
//! looks at that one topic and nothing else (review::cast_spell), on any PR, and its result stays on this
//! machine until you post it as a comment.
//! The file name is the spell's name; `name_ok` keeps every name inside the folder.

use std::path::{Path, PathBuf};

/// Written once, when the folder does not exist yet; deleted starters are not written again.
/// ponytail: prose, not bullets. A review that repeats a 24+ char instruction line is held (quotes_instructions),
/// and a model mirrors a bullet list word for word.
pub const STARTERS: &[(&str, &str)] = &[
    (
        "auth-check",
        "# Auth check

Trace every request path this PR touches back to where the caller is authenticated and authorised.

## Look for
Paths that reach data or a side effect with no check, checks that run after the work they guard, and role or
tenant assumptions the code does not enforce.

## Report
One line per path: `file:line`, what it reaches, and which check is missing.
",
    ),
    (
        "fog-audit",
        "# Fog audit

Audit the prose this PR adds or changes against the checks below.

Docs, README, UI strings, error messages and comments; never code or the PR description. Name the intended
reader in one line first.

## Look for
Fails: the point is not in the first paragraph, it is unclear who must do what, a claim with no source, one thing
under two names, a sentence that reads two ways, a hedge where a fact or requirement belongs, jargon the reader
does not know, steps out of order or a condition after its action. Warnings: actions buried in nouns, a passive
that drops a known actor, over 10 words before the subject, sentences over 25 words, a paragraph with two points,
headings that only name a topic, filler, inflated words, redundancy, and instructions not addressed to you.

## Report
One line per finding: `file:line`, fail or warn, a short quote, and the rewrite.
",
    ),
    (
        "migration-audit",
        "# Migration audit

Read every schema or data migration this PR adds, and the code that reads the tables it touches.

## For each migration
Whether it runs on a live database without locking a busy table, how it rolls back, and which existing rows or
older app versions break while it runs.

## Report
One line per migration: `file:line`, the risk, and the safer order of steps.
",
    ),
    (
        "spaghetti-audit",
        "# Spaghetti audit

Audit the whole repository at this PR's head commit against the Spaghetti rulebook, not just the diff.

## Look for
Fails: silenced checks, swallowed errors, hidden global state, a frontend that decides. Warnings: files over
600 lines, dead code, pass-through wrappers, I/O mixed into computation.

## Report
One line per finding: `file:line`, fail or warn, and the fix.
",
    ),
    (
        "test-gaps",
        "# Test gaps

List the behaviours this PR changes, then find the test that would fail if each one broke.

## For each behaviour with no such test
Write the smallest test that would catch it: its name, its setup and its assertion.
",
    ),
];

/// The prompt a cast runs with; the spell itself goes in the system prompt, where instructions are trusted.
pub const CAST: &str = "Investigate only the topic in your instructions on pull request {repo}#{number}. \
Do not review anything else or give a verdict. Go past the diff wherever it needs: callers, migrations, \
config, tests.

Answer in markdown only: one line per finding, each with file:line, and say plainly when you found nothing.";

fn dir() -> PathBuf {
    crate::config::get().spells_dir
}

pub fn name_ok(name: &str) -> bool {
    (1..=40).contains(&name.len())
        && name
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// (name, text), sorted by name. A folder that does not exist yet is seeded with STARTERS first.
pub fn list_in(dir: &Path) -> Vec<(String, String)> {
    if !dir.exists() {
        let seeded = std::fs::create_dir_all(dir).and_then(|_| {
            STARTERS
                .iter()
                .try_for_each(|(n, t)| std::fs::write(dir.join(format!("{n}.md")), t))
        });
        if let Err(e) = seeded {
            log::warn!("could not write the starter spells: {e}");
        }
    }
    let mut out: Vec<(String, String)> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| {
            let p = e.path();
            let name = p.file_stem()?.to_str()?.to_string();
            (p.extension()? == "md" && name_ok(&name)).then_some(())?;
            Some((name, std::fs::read_to_string(&p).ok()?))
        })
        .collect();
    out.sort();
    out
}

pub fn get_in(dir: &Path, name: &str) -> Option<String> {
    if !name_ok(name) {
        return None;
    }
    std::fs::read_to_string(dir.join(format!("{name}.md"))).ok()
}

pub fn list() -> Vec<(String, String)> {
    list_in(&dir())
}
pub fn get(name: &str) -> Option<String> {
    get_in(&dir(), name)
}

/// The card's one line: the first line of prose, past any headings and blank lines.
pub fn about(text: &str) -> String {
    text.lines()
        .map(str::trim)
        .find(|l| !l.is_empty() && !l.starts_with('#'))
        .unwrap_or("")
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_stay_in_the_folder() {
        assert!(name_ok("migration-audit"));
        assert!(name_ok("a1"));
        for bad in ["", "../x", "A", "a b", "a/b", "a.md", &"a".repeat(41)] {
            assert!(!name_ok(bad), "{bad:?}");
        }
    }

    #[test]
    fn a_missing_folder_gets_starters_and_an_emptied_one_does_not() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("spells");
        let names: Vec<String> = list_in(&dir).into_iter().map(|(n, _)| n).collect();
        assert_eq!(
            names,
            STARTERS.iter().map(|(n, _)| n.to_string()).collect::<Vec<_>>()
        );
        for (n, _) in STARTERS {
            std::fs::remove_file(dir.join(format!("{n}.md"))).unwrap();
        }
        assert!(list_in(&dir).is_empty(), "deleted starters stay deleted");
    }

    #[test]
    fn lists_and_reads_md_files_only() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().to_path_buf(); // exists: no starters
        std::fs::write(dir.join("zeta.md"), "look at z").unwrap();
        std::fs::write(dir.join("alpha.md"), "look at a").unwrap();
        std::fs::write(dir.join("notes.txt"), "not a spell").unwrap();
        std::fs::write(dir.join("Bad Name.md"), "not a name").unwrap();
        assert_eq!(
            list_in(&dir),
            vec![
                ("alpha".into(), "look at a".into()),
                ("zeta".into(), "look at z".into())
            ]
        );
        assert_eq!(get_in(&dir, "zeta").as_deref(), Some("look at z"));
        assert_eq!(get_in(&dir, "../zeta"), None);
        assert_eq!(get_in(&dir, "nope"), None);
    }

    #[test]
    fn about_is_the_first_line_of_prose() {
        assert_eq!(
            about("# Auth check\n\nTrace every path.\n\n## Look for\n- x"),
            "Trace every path."
        );
        assert_eq!(about("just a line"), "just a line");
        assert_eq!(about("# only a heading"), "");
    }

    #[test]
    fn every_starter_card_is_a_whole_sentence() {
        for (n, t) in STARTERS {
            assert!(about(t).ends_with('.'), "{n}");
        }
    }
}
