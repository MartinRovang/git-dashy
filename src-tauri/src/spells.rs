//! Spells (~/.prs_spells/<name>.md): one-time, in-depth investigations cast on one PR.
//!
//! A cast is a normal review whose private instructions are the spell, framed by `cast`. The instructions
//! path already keeps them trusted, private and leak-checked, so a spell adds nothing to the prompt itself.
//! The file name is the spell's name; `name_ok` keeps every name inside the folder.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Result};

/// Longest spell text, the same ceiling as instructions typed for one review.
pub const TEXT_MAX: usize = 8000;

/// Written once, when the folder does not exist yet. Deleting them is a choice that sticks.
pub const STARTERS: &[(&str, &str)] = &[
    (
        "auth-check",
        "Trace every request path this PR adds or changes back to where the caller is authenticated and \
         authorised. Name any path that reaches data or a side effect without a check, any check done after \
         the work, and any role or tenant assumption the code does not enforce.",
    ),
    (
        "migration-audit",
        "Read every schema or data migration this PR adds, and the code that reads the tables it touches. \
         Say whether each one can run on a live database without locking a busy table, whether it can be \
         rolled back, and what existing rows or old app versions break while it runs.",
    ),
    (
        "test-gaps",
        "List the behaviours this PR changes, then find the test that would fail if each one broke. For every \
         behaviour with no such test, write the smallest test that would catch it: its name, its setup, and its \
         assertion.",
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

fn file(dir: &Path, name: &str) -> Result<PathBuf> {
    if !name_ok(name) {
        return Err(anyhow!("a spell name is 1-40 of a-z, 0-9 and -"));
    }
    Ok(dir.join(format!("{name}.md")))
}

/// (name, text), sorted by name. A folder that does not exist yet is seeded with STARTERS first.
pub fn list_in(dir: &Path) -> Vec<(String, String)> {
    if !dir.exists() {
        for (n, t) in STARTERS {
            if let Err(e) = save_in(dir, n, t) {
                log::warn!("could not write starter spell {n}: {e}");
            }
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
    std::fs::read_to_string(file(dir, name).ok()?).ok()
}

pub fn save_in(dir: &Path, name: &str, text: &str) -> Result<()> {
    let path = file(dir, name)?;
    if text.chars().count() > TEXT_MAX {
        return Err(anyhow!("a spell is at most {TEXT_MAX} characters"));
    }
    std::fs::create_dir_all(dir)?;
    std::fs::write(path, text)?;
    Ok(())
}

pub fn delete_in(dir: &Path, name: &str) -> Result<()> {
    std::fs::remove_file(file(dir, name)?).map_err(|e| anyhow!("no spell {name}: {e}"))
}

pub fn list() -> Vec<(String, String)> {
    list_in(&dir())
}
pub fn get(name: &str) -> Option<String> {
    get_in(&dir(), name)
}
pub fn save(name: &str, text: &str) -> Result<()> {
    save_in(&dir(), name, text)
}
pub fn delete(name: &str) -> Result<()> {
    delete_in(&dir(), name)
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
    fn a_missing_folder_gets_starters_and_an_empty_one_does_not() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("spells");
        let names: Vec<String> = list_in(&dir).into_iter().map(|(n, _)| n).collect();
        assert_eq!(
            names,
            STARTERS.iter().map(|(n, _)| n.to_string()).collect::<Vec<_>>()
        );
        for (n, _) in STARTERS {
            delete_in(&dir, n).unwrap();
        }
        assert!(list_in(&dir).is_empty(), "deleted starters stay deleted");
    }

    #[test]
    fn save_get_delete() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().to_path_buf(); // exists: no starters
        save_in(&dir, "zeta", "look at z").unwrap();
        save_in(&dir, "alpha", "look at a").unwrap();
        assert_eq!(
            list_in(&dir),
            vec![
                ("alpha".into(), "look at a".into()),
                ("zeta".into(), "look at z".into())
            ]
        );
        assert_eq!(get_in(&dir, "zeta").as_deref(), Some("look at z"));
        assert!(save_in(&dir, "../evil", "x").is_err());
        assert!(save_in(&dir, "long", &"x".repeat(TEXT_MAX + 1)).is_err());
        delete_in(&dir, "zeta").unwrap();
        assert_eq!(get_in(&dir, "zeta"), None);
        assert!(delete_in(&dir, "zeta").is_err(), "deleting nothing says so");
    }

    #[test]
    fn cast_frames_the_spell() {
        let s = cast("migration-audit", "check every migration for a rollback");
        assert!(s.contains("**Migration audit**"), "{s}");
        assert!(s.ends_with("check every migration for a rollback"), "{s}");
    }
}
