//! Which repo holds the database a repo's code runs against (~/.prs_dbrepo).
//!
//! A review of a PR in a repo with a DB repo may also read that repo, and is asked what the change does
//! to the database: the tables it reads, writes or alters, and what could go wrong. Nothing connects to a
//! database; the schema and migrations in the DB repo are the whole picture.
//!
//! **The rule.** An owner row (`acme/*`) covers every repo under it; a repo row beats it, and a repo row
//! with an empty `db` carves that repo out.
//!
//! ponytail: the same line shape as autorev.rs — one JSON object per line, last line wins, written with
//! bind::append_to and keyed with bind::key/owner_key, so a repo folds to the key the other stores use.

use std::collections::HashMap;
use std::path::PathBuf;

use serde_json::Value;

use crate::bind::{self, key, owner_key};

fn store() -> PathBuf {
    crate::config::get().dbrepo
}

/// Every live row: repo -> db repo, owner -> db repo. An empty db repo is a deliberate "none".
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Rules {
    pub repos: HashMap<String, String>,
    pub owners: HashMap<String, String>,
}

impl Rules {
    /// The DB repo for `repo`, "" for none. A repo row beats an owner row.
    pub fn of(&self, repo: &str) -> String {
        let r = key(repo);
        if r.is_empty() {
            return String::new();
        }
        if let Some(db) = self.repos.get(&r) {
            return db.clone();
        }
        let o = r.split('/').next().unwrap_or("");
        self.owners.get(o).cloned().unwrap_or_default()
    }

    /// (target, db repo), owners then repos, each sorted. Owners read back as `acme/*`.
    pub fn listed(&self) -> Vec<(String, String)> {
        let mut owners: Vec<(String, String)> = self
            .owners
            .iter()
            .map(|(k, v)| (format!("{k}/*"), v.clone()))
            .collect();
        let mut repos: Vec<(String, String)> =
            self.repos.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        owners.sort();
        repos.sort();
        owners.extend(repos);
        owners
    }
}

pub fn rules() -> Rules {
    parse(&std::fs::read_to_string(store()).unwrap_or_default())
}

fn parse(text: &str) -> Rules {
    let mut out = Rules::default();
    for line in text.lines() {
        let Ok(Value::Object(e)) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        // `clear` takes a rule away, where an empty db is a rule that says "none"
        let clear = e.get("clear").and_then(Value::as_bool) == Some(true);
        let db = match e.get("db").and_then(Value::as_str) {
            // a db repo that no longer folds is dropped, not stored raw: scoped() compares against the key
            Some(db) => key(db),
            None if clear => String::new(),
            None => continue,
        };
        let (map, k) = if let Some(o) = e.get("owner").and_then(Value::as_str) {
            (&mut out.owners, owner_key(o))
        } else if let Some(r) = e.get("repo").and_then(Value::as_str) {
            (&mut out.repos, key(r))
        } else {
            continue;
        };
        if k.is_empty() {
            continue;
        }
        if clear {
            map.remove(&k);
        } else {
            map.insert(k, db);
        }
    }
    out
}

/// The DB repo for `repo`, "" for none.
pub fn of(repo: &str) -> String {
    rules().of(repo)
}

/// Point `target` (owner/name, or owner/* for a whole owner) at `db`; "" for none. Returns "" or why not.
pub fn set(target: &str, db: &str) -> String {
    let d = key(db);
    if !db.trim().is_empty() && d.is_empty() {
        return format!("{db} is not an owner/name");
    }
    match field(target) {
        Ok((f, k)) => bind::append_to(store(), &[(f, &k), ("db", &d)], None),
        Err(e) => e,
    }
}

/// Take `target`'s rule away, so it follows its owner again (or has no DB repo). Returns "" or why not.
pub fn clear(target: &str) -> String {
    match field(target) {
        Ok((f, k)) => bind::append_to(store(), &[(f, &k)], Some(("clear", true))),
        Err(e) => e,
    }
}

/// ("owner", key) for `acme/*` or `acme`, ("repo", key) for `acme/api`.
fn field(target: &str) -> Result<(&'static str, String), String> {
    let t = target.trim();
    let (f, k) = if t.ends_with("/*") || !t.contains('/') {
        ("owner", owner_key(t))
    } else {
        ("repo", key(t))
    };
    if k.is_empty() {
        return Err(format!("{target} is not owner/name or owner/*"));
    }
    Ok((f, k))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_beats_owner_and_empty_carves_out() {
        let r = parse(
            r#"{"owner": "acme", "db": "acme/schema"}
{"repo": "acme/billing", "db": "acme/billing-db"}
{"repo": "acme/docs", "db": ""}
{"owner": "beta", "db": "beta/one"}
{"owner": "beta", "db": "beta/two"}
not json"#,
        );
        assert_eq!(r.of("acme/api"), "acme/schema");
        assert_eq!(r.of("Acme/Billing"), "acme/billing-db");
        assert_eq!(r.of("acme/docs"), "");
        assert_eq!(r.of("beta/x"), "beta/two", "last line wins");
        assert_eq!(r.of("other/x"), "");
        assert_eq!(r.of("nonsense"), "");
    }

    #[test]
    fn set_writes_what_of_reads() {
        let _g = crate::config::test_lock();
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.dbrepo = d.path().join("dbrepo"));
        assert_eq!(set("acme/*", "acme/schema"), "");
        assert_eq!(set("acme/docs", ""), "");
        assert!(!set("acme/api", "not a repo").is_empty());
        assert!(!set("a/b/c", "acme/schema").is_empty());
        assert_eq!(of("acme/api"), "acme/schema");
        assert_eq!(of("acme/docs"), "");
        // cleared, the carve-out is gone and the repo follows its owner again
        assert_eq!(clear("acme/docs"), "");
        assert_eq!(of("acme/docs"), "acme/schema");
        assert_eq!(clear("acme/*"), "");
        assert_eq!(of("acme/api"), "");
        assert!(rules().listed().is_empty());
    }
}
