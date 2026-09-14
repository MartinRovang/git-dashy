//! Which repos auto-review is armed for (~/.prs_autoreview).
//!
//! `a` turns auto on for the whole board, and the board spans whatever the token can see — so
//! arming it for one repo armed it for every repo a teammate happened to open a PR in. This store
//! says where.
//!
//! **The rule, stated once.** Auto covers every repo while nothing is armed. Arm one repo or one
//! owner and it covers only what is armed. A repo row beats an owner row, so a repo can be carved
//! out of an armed owner by arming it `false`.
//!
//! ponytail: its own file, not a field on ~/.prs_bindings. That store's `touched` set means "a line
//! has named this repo for TEAM purposes" and three of its rules read it — an exact row blocks the
//! owner rule in pick(), excluded() lists what a rule no longer covers, and seed() skips what is
//! already named. An auto-review row in that file would silently unbind a repo from the team its
//! owner rule covers, which is a data loss you would find weeks later.
//!
//! ponytail: same line shape as bind.rs and install::REGISTRY — one JSON object per line, last line
//! wins on read, `key()`/`owner_key()` borrowed so a repo folds to the same key both stores use.
//! Not a second format: that one has already survived a review round on appending without a lock and
//! on reading back a file an older version wrote.

use std::collections::HashMap;
use std::io::Write;
use std::path::PathBuf;

use serde_json::Value;

use crate::bind::{key, owner_key};

fn store() -> PathBuf {
    crate::config::get().autorev
}

fn read() -> String {
    // no file is the normal state: auto covers everything until something is armed
    std::fs::read_to_string(store()).unwrap_or_default()
}

/// Every live decision: repo -> armed, owner -> armed.
#[derive(Default, Debug, PartialEq)]
pub struct Scope {
    pub repos: HashMap<String, bool>,
    pub owners: HashMap<String, bool>,
}

impl Scope {
    /// Nothing armed anywhere, so auto covers the whole board.
    pub fn everywhere(&self) -> bool {
        !self.repos.values().any(|v| *v) && !self.owners.values().any(|v| *v)
    }

    /// Whether auto reviews `repo`. A repo row beats an owner row beats the default.
    pub fn armed(&self, repo: &str) -> bool {
        if self.everywhere() {
            return true;
        }
        let r = key(repo);
        if r.is_empty() {
            return false;
        }
        if let Some(v) = self.repos.get(&r) {
            return *v;
        }
        let o = r.split('/').next().unwrap_or("");
        *self.owners.get(o).unwrap_or(&false)
    }

    /// What to show: (target, armed), repos then owners, each sorted. Owners read back as `acme/*`.
    pub fn listed(&self) -> Vec<(String, bool)> {
        let mut repos: Vec<(String, bool)> = self.repos.iter().map(|(k, v)| (k.clone(), *v)).collect();
        let mut owners: Vec<(String, bool)> =
            self.owners.iter().map(|(k, v)| (format!("{k}/*"), *v)).collect();
        repos.sort();
        owners.sort();
        owners.extend(repos);
        owners
    }
}

fn flag(e: &serde_json::Map<String, Value>) -> Option<bool> {
    match e.get("auto") {
        Some(Value::Bool(b)) => Some(*b),
        _ => None,
    }
}

/// One read of the store. A line with no usable key, or no `auto` boolean, is skipped.
pub fn scope() -> Scope {
    let mut out = Scope::default();
    for line in read().lines() {
        if line.trim().is_empty() {
            continue;
        }
        // ponytail: one unreadable line is not a reason to lose the rest of the file
        let e = match serde_json::from_str::<Value>(line) {
            Ok(Value::Object(e)) => e,
            _ => continue,
        };
        let Some(on) = flag(&e) else { continue };
        if let Some(Value::String(o)) = e.get("owner") {
            let o = owner_key(o);
            if !o.is_empty() {
                out.owners.insert(o, on);
            }
            continue;
        }
        if let Some(Value::String(r)) = e.get("repo") {
            let r = key(r);
            if !r.is_empty() {
                out.repos.insert(r, on);
            }
        }
    }
    out
}

/// Whether auto reviews `repo`, against one read. For a single question.
pub fn armed(repo: &str) -> bool {
    scope().armed(repo)
}

/// A repo -> armed function over ONE read, for a caller asking about many.
///
/// ponytail: a tick asks this per row. Calling armed() per row would reopen the file once per PR on
/// every refresh, and the answers could differ within one tick.
pub fn resolver() -> Box<dyn Fn(&str) -> bool + Send + Sync> {
    let s = scope();
    Box::new(move |repo: &str| s.armed(repo))
}

/// Add one line. Returns "" or why it could not: never panics.
///
/// ponytail: the same shape and the same reason as bind::append — this runs from the UI, and an
/// unwritable home must not unwind out of a click and take the dashboard down.
fn append(fields: &[(&str, &str)], on: bool) -> String {
    let mut line: Vec<String> = fields
        .iter()
        .map(|(k, v)| format!("{}: {}", dumps(k), dumps(v)))
        .collect();
    line.push(format!("{}: {}", dumps("auto"), on));
    let p = store();
    let parent = p
        .parent()
        .filter(|d| !d.as_os_str().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| ".".into());
    let go = || -> std::io::Result<()> {
        std::fs::create_dir_all(&parent)?;
        let mut f = std::fs::OpenOptions::new().append(true).create(true).open(&p)?;
        f.write_all(format!("{{{}}}\n", line.join(", ")).as_bytes())
    };
    match go() {
        Ok(()) => String::new(),
        Err(e) => e.to_string(),
    }
}

/// A JSON string the way Python's json.dumps writes it, so a line is byte-identical to bind's.
fn dumps(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut buf = [0u16; 2];
                for u in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{:04x}", u));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Arm or disarm one repo. Returns "" or why it did not.
pub fn set(repo: &str, on: bool) -> String {
    let r = key(repo);
    if r.is_empty() {
        return format!("{repo} is not an owner/name");
    }
    if scope().repos.get(&r) == Some(&on) {
        return String::new(); // already says this; writing it again grows the file for nothing
    }
    append(&[("repo", &r)], on)
}

/// Arm or disarm every repo under one owner. Returns "" or why it did not.
pub fn set_owner(owner: &str, on: bool) -> String {
    let o = owner_key(owner);
    if o.is_empty() {
        return format!("{owner} is not an owner");
    }
    if scope().owners.get(&o) == Some(&on) {
        return String::new();
    }
    append(&[("owner", &o)], on)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, MutexGuard};

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn fresh() -> (MutexGuard<'static, ()>, tempfile::TempDir) {
        let g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.autorev = d.path().join("autorev"));
        (g, d)
    }

    #[test]
    fn an_empty_store_arms_every_repo() {
        let (_g, _d) = fresh();
        assert!(scope().everywhere());
        assert!(armed("acme/api"));
        assert!(armed("other/thing"));
    }

    #[test]
    fn arming_one_repo_disarms_every_other() {
        let (_g, _d) = fresh();
        assert_eq!(set("acme/api", true), "");
        assert!(!scope().everywhere());
        assert!(armed("acme/api"));
        assert!(!armed("acme/web"));
        assert!(!armed("other/thing"));
    }

    #[test]
    fn an_owner_arms_everything_under_it_and_nothing_else() {
        let (_g, _d) = fresh();
        assert_eq!(set_owner("acme", true), "");
        assert!(armed("acme/api"));
        assert!(armed("acme/web"));
        assert!(!armed("other/thing"));
    }

    /// The carve-out: a repo row beats the owner row, whichever was written first.
    #[test]
    fn a_repo_can_be_carved_out_of_an_armed_owner() {
        let (_g, _d) = fresh();
        set_owner("acme", true);
        set("acme/web", false);
        assert!(armed("acme/api"));
        assert!(!armed("acme/web"));
    }

    #[test]
    fn a_repo_can_be_armed_under_a_disarmed_owner() {
        let (_g, _d) = fresh();
        set_owner("acme", false);
        set("acme/api", true);
        assert!(armed("acme/api"));
        assert!(!armed("acme/web"));
    }

    /// Only `true` rows narrow the scope: a store holding nothing but a `false` still means
    /// everywhere, because disarming a repo nobody armed is not a decision to arm the rest.
    #[test]
    fn a_lone_false_row_leaves_auto_covering_everything() {
        let (_g, _d) = fresh();
        set("acme/web", false);
        assert!(scope().everywhere());
        assert!(armed("acme/web"));
    }

    #[test]
    fn the_last_line_wins() {
        let (_g, _d) = fresh();
        set("acme/api", true);
        set("acme/api", false);
        assert!(scope().everywhere(), "nothing is armed any more");
        set("acme/web", true);
        assert!(!armed("acme/api"));
    }

    #[test]
    fn saying_the_same_thing_twice_writes_nothing() {
        let (_g, d) = fresh();
        set("acme/api", true);
        let before = std::fs::read_to_string(d.path().join("autorev")).unwrap();
        assert_eq!(set("acme/api", true), "");
        assert_eq!(std::fs::read_to_string(d.path().join("autorev")).unwrap(), before);
    }

    #[test]
    fn a_key_it_cannot_read_is_refused_rather_than_stored() {
        let (_g, _d) = fresh();
        assert!(set("notes", true).contains("owner/name"));
        assert!(set_owner("acme/api", true).contains("is not an owner"));
        assert!(scope().everywhere());
    }

    /// A URL, an ssh remote and a bare name fold to one key, the way a binding does.
    #[test]
    fn a_url_and_a_bare_name_are_the_same_repo() {
        let (_g, _d) = fresh();
        set("https://github.com/Acme/API", true);
        assert!(armed("acme/api"));
        assert!(armed("ACME/API"));
    }

    #[test]
    fn one_broken_line_does_not_lose_the_rest() {
        let (_g, d) = fresh();
        set("acme/api", true);
        let p = d.path().join("autorev");
        let good = std::fs::read_to_string(&p).unwrap();
        std::fs::write(&p, format!("not json at all\n{{\"repo\": \"x\"}}\n{good}")).unwrap();
        assert!(armed("acme/api"));
    }

    #[test]
    fn a_line_with_no_auto_flag_is_not_a_decision() {
        let (_g, d) = fresh();
        // exactly what a bind row looks like: this store must read it as saying nothing
        std::fs::write(
            d.path().join("autorev"),
            "{\"repo\": \"acme/api\", \"team\": \"acme\"}\n",
        )
        .unwrap();
        assert!(scope().everywhere());
    }

    #[test]
    fn listed_puts_owners_first_and_sorts_each() {
        let (_g, _d) = fresh();
        set("zeta/one", true);
        set("acme/api", true);
        set_owner("beta", true);
        assert_eq!(
            scope().listed(),
            vec![
                ("beta/*".to_string(), true),
                ("acme/api".to_string(), true),
                ("zeta/one".to_string(), true),
            ]
        );
    }

    #[test]
    fn the_resolver_answers_from_one_read() {
        let (_g, _d) = fresh();
        set_owner("acme", true);
        let f = resolver();
        // the file changing under it must not change its answers mid-tick
        set("acme/api", false);
        assert!(f("acme/api"));
        assert!(f("acme/web"));
        assert!(!f("other/thing"));
    }
}
