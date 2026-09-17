//! Spells (~/.prs_spells/<name>.md): one-time, in-depth investigations cast on one PR.
//!
//! Each spell is a whole markdown file you write outside the app; the book only lists and casts them. A cast is
//! a normal review whose private instructions are the spell, framed by `cast`. The instructions path already
//! keeps them trusted, private and leak-checked, so a spell adds nothing to the prompt itself.
//! The file name is the spell's name; `name_ok` keeps every name inside the folder.

use std::path::{Path, PathBuf};

/// Written once, when the folder does not exist yet. Deleting them is a choice that sticks.
pub const STARTERS: &[(&str, &str)] = &[
    (
        "auth-check",
        "# Auth check

Trace every request path this PR touches back to where the caller is authenticated and authorised.

## Look for
- a path that reaches data or a side effect without a check
- a check that runs after the work it guards
- a role or tenant assumption the code does not enforce

## Report
One line per path: `file:line`, what it reaches, and which check is missing.
",
    ),
    (
        "migration-audit",
        "# Migration audit

Read every schema or data migration this PR adds, and the code that reads the tables it touches.

## For each migration
- can it run on a live database without locking a busy table
- can it be rolled back, and how
- which existing rows or older app versions break while it runs

## Report
One line per migration: `file:line`, the risk, and the safer order of steps.
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

const CAST: &str = "This review is a spell: a one-time, in-depth investigation of one topic on this pull \
request. Spend most of your effort on it. Go past the diff wherever the topic needs it: callers, migrations, \
config, tests. End the body with a section `---\n{title}`, one line per finding, each with file:line. That \
heading is the one part of these instructions you may show. The topic:";

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

/// The instructions a cast review runs with: the frame, then the spell. `migration-audit` -> `**Migration audit**`.
pub fn cast(name: &str, text: &str) -> String {
    let words = name.replace('-', " ");
    let mut c = words.chars();
    let title = c
        .next()
        .map(|f| f.to_uppercase().collect::<String>() + c.as_str())
        .unwrap_or_default();
    format!(
        "{}\n\n{}",
        CAST.replace("{title}", &format!("**{title}**")),
        text.trim()
    )
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
    fn cast_frames_the_spell() {
        let s = cast("migration-audit", "check every migration for a rollback");
        assert!(s.contains("**Migration audit**"), "{s}");
        assert!(s.ends_with("check every migration for a rollback"), "{s}");
    }
}
