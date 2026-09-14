//! Which repos auto-review is armed for (~/.prs_autoreview).
//!
//! `a` turns auto on for the whole board, and the board spans whatever the token can see — so
//! arming it for one repo armed it for every repo a teammate happened to open a PR in, and this
//! store says which repos it covers.
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
use std::path::PathBuf;

use serde_json::Value;

use crate::bind::{self, key, owner_key};

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

    /// Whether auto reviews every repo under `owner` that has no row of its own.
    pub fn armed_owner(&self, owner: &str) -> bool {
        if self.everywhere() {
            return true;
        }
        *self.owners.get(&owner_key(owner)).unwrap_or(&false)
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
        let Some(Value::Bool(on)) = e.get("auto").cloned() else {
            continue;
        };
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

/// What `gitdashy auto` prints for a scope.
///
/// ponytail: a function, because it is the only place that turns the rule into English and it had
/// the rule backwards — it branched on "are there any rows" rather than asking everywhere(), so a
/// store holding nothing but an --off row claimed every other repo was left alone while in fact
/// nothing was armed and every repo was still reviewed. Now it can be asserted.
pub fn report(s: &Scope) -> Vec<String> {
    let rows = s.listed();
    let mut out = Vec::new();
    if s.everywhere() {
        out.push("auto-review covers every repo on the board; name one to narrow it".to_string());
    }
    out.extend(rows.iter().map(|(t, v)| {
        format!(
            "  {t:<36}  →  {}",
            if *v { "auto-review" } else { "not auto-reviewed" }
        )
    }));
    if s.everywhere() && !rows.is_empty() {
        out.push("nothing is armed, so the rows above do not apply yet".to_string());
    } else if !s.everywhere() {
        out.push("every other repo is left alone".to_string());
    }
    out
}

/// Add one line: the fields, then the `auto` flag as a bare JSON bool.
fn append(fields: &[(&str, &str)], on: bool) -> String {
    bind::append_to(store(), fields, &[("auto", on.to_string())])
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

/// The one lock for tests that repoint `config.autorev`, which is process-global. Two locks would
/// let web.rs and cli.rs point each other's reads at the wrong tempdir, intermittently.
#[cfg(test)]
pub fn test_lock() -> std::sync::MutexGuard<'static, ()> {
    static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::MutexGuard;

    fn fresh() -> (MutexGuard<'static, ()>, tempfile::TempDir) {
        let g = test_lock();
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.autorev = d.path().join("autorev"));
        (g, d)
    }

    #[test]
    fn an_empty_store_arms_every_repo() {
        let (_g, _d) = fresh();
        assert!(scope().everywhere());
        assert!(scope().armed("acme/api"));
        assert!(scope().armed("other/thing"));
    }

    #[test]
    fn arming_one_repo_disarms_every_other() {
        let (_g, _d) = fresh();
        assert_eq!(set("acme/api", true), "");
        assert!(!scope().everywhere());
        assert!(scope().armed("acme/api"));
        assert!(!scope().armed("acme/web"));
        assert!(!scope().armed("other/thing"));
    }

    #[test]
    fn an_owner_arms_everything_under_it_and_nothing_else() {
        let (_g, _d) = fresh();
        assert_eq!(set_owner("acme", true), "");
        assert!(scope().armed("acme/api"));
        assert!(scope().armed("acme/web"));
        assert!(!scope().armed("other/thing"));
    }

    /// The carve-out: a repo row beats the owner row, whichever was written first.
    #[test]
    fn a_repo_can_be_carved_out_of_an_armed_owner() {
        let (_g, _d) = fresh();
        set_owner("acme", true);
        set("acme/web", false);
        assert!(scope().armed("acme/api"));
        assert!(!scope().armed("acme/web"));
    }

    #[test]
    fn a_repo_can_be_armed_under_a_disarmed_owner() {
        let (_g, _d) = fresh();
        set_owner("acme", false);
        set("acme/api", true);
        assert!(scope().armed("acme/api"));
        assert!(!scope().armed("acme/web"));
    }

    /// Only `true` rows narrow the scope: a store holding nothing but a `false` still means
    /// everywhere, because disarming a repo nobody armed is not a decision to arm the rest.
    #[test]
    fn a_lone_false_row_leaves_auto_covering_everything() {
        let (_g, _d) = fresh();
        set("acme/web", false);
        assert!(scope().everywhere());
        assert!(scope().armed("acme/web"));
    }

    #[test]
    fn the_last_line_wins() {
        let (_g, _d) = fresh();
        set("acme/api", true);
        set("acme/api", false);
        assert!(scope().everywhere(), "nothing is armed any more");
        set("acme/web", true);
        assert!(!scope().armed("acme/api"));
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
        assert!(scope().armed("acme/api"));
        assert!(scope().armed("ACME/API"));
    }

    #[test]
    fn one_broken_line_does_not_lose_the_rest() {
        let (_g, d) = fresh();
        set("acme/api", true);
        let p = d.path().join("autorev");
        let good = std::fs::read_to_string(&p).unwrap();
        std::fs::write(&p, format!("not json at all\n{{\"repo\": \"x\"}}\n{good}")).unwrap();
        assert!(scope().armed("acme/api"));
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

    /// The bug this pins: report() branched on "are there any rows" instead of asking everywhere(),
    /// so a store holding nothing but an --off row claimed every other repo was left alone.
    #[test]
    fn the_report_says_auto_covers_everything_until_something_is_armed() {
        let (_g, _d) = fresh();
        assert_eq!(
            report(&scope()),
            vec!["auto-review covers every repo on the board; name one to narrow it"]
        );

        set("acme/web", false);
        let lines = report(&scope());
        assert_eq!(
            lines[0],
            "auto-review covers every repo on the board; name one to narrow it"
        );
        assert!(lines[1].contains("acme/web"));
        assert_eq!(lines[2], "nothing is armed, so the rows above do not apply yet");
        assert!(!lines.iter().any(|l| l.contains("left alone")), "{lines:?}");
    }

    #[test]
    fn the_report_says_what_is_left_alone_once_something_is_armed() {
        let (_g, _d) = fresh();
        set("acme/api", true);
        let lines = report(&scope());
        assert!(!lines[0].contains("covers every repo"), "{lines:?}");
        assert!(lines[0].contains("acme/api"));
        assert_eq!(lines.last().unwrap(), "every other repo is left alone");
    }

    /// The owner question the CLI prints from: an owner row that is off while nothing is armed still
    /// leaves every repo under it reviewed.
    #[test]
    fn armed_owner_follows_the_same_rule() {
        let (_g, _d) = fresh();
        assert!(scope().armed_owner("acme"), "nothing armed, so everything is");
        set_owner("acme", false);
        assert!(
            scope().armed_owner("acme"),
            "a lone --off arms nothing, so it bites nothing"
        );
        set("other/thing", true);
        assert!(
            !scope().armed_owner("acme"),
            "now something is armed, the off row applies"
        );
        set_owner("acme", true);
        assert!(scope().armed_owner("acme"));
        assert!(!scope().armed_owner("nope"));
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

    /// A tick holds one Scope and asks it per row, so the file changing under it cannot make two
    /// rows in the same tick disagree.
    #[test]
    fn one_scope_answers_every_row_from_one_read() {
        let (_g, _d) = fresh();
        set_owner("acme", true);
        let s = scope();
        set("acme/api", false);
        assert!(s.armed("acme/api"));
        assert!(s.armed("acme/web"));
        assert!(!s.armed("other/thing"));
        assert!(!scope().armed("acme/api"), "a fresh read sees the change");
    }

    /// autorev writes through bind's serializer, so a key that needs escaping reads back intact.
    #[test]
    fn a_key_that_needs_escaping_survives_the_round_trip() {
        let (_g, d) = fresh();
        set("ac\u{e8}me/api", true);
        let line = std::fs::read_to_string(d.path().join("autorev")).unwrap();
        assert!(
            line.contains("\\u00e8"),
            "non-ASCII is escaped the way bind writes it: {line}"
        );
        assert!(scope().armed("ac\u{e8}me/api"));
    }

    #[test]
    fn the_flag_is_a_json_bool_not_a_string() {
        let (_g, d) = fresh();
        set("acme/api", true);
        let line = std::fs::read_to_string(d.path().join("autorev")).unwrap();
        assert!(line.contains("\"auto\": true"), "{line}");
    }
}
