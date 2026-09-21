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
#[derive(Clone, Default, Debug, PartialEq)]
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
    ///
    /// ponytail: a repo this cannot name is never auto-reviewed, armed or not. It used to follow
    /// the default, so a row whose nameWithOwner failed to come back was reviewed unattended while
    /// nothing was armed and skipped once something was — behaviour that flipped with the store.
    /// Reviewing something we cannot name is the half that costs other people.
    pub fn armed(&self, repo: &str) -> bool {
        let r = key(repo);
        if r.is_empty() {
            return false;
        }
        if self.everywhere() {
            return true;
        }
        if let Some(v) = self.repos.get(&r) {
            return *v;
        }
        let o = r.split('/').next().unwrap_or("");
        *self.owners.get(o).unwrap_or(&false)
    }

    /// What to show: (target, armed), owners then repos, each sorted. Owners read back as `acme/*`.
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

/// What happens to a finished review.
///
/// ponytail: stored as a word, not a bool. The next settling is a threshold ("post unless the change
/// is big"), and a bool would need the file rewritten to take one.
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub enum Post {
    /// Posted the moment it finishes, as it always has been. The default, so a store nobody has
    /// touched behaves exactly as before.
    #[default]
    Now,
    /// Written to ~/.prs_held and left for a keypress.
    Hold,
}

/// The word that REMOVES a rule instead of setting one.
///
/// ponytail: a third word, not a missing field. The store is append-only, so "there is no rule here" has
/// to be something you can write down -- and a line with no `post_*` field at all is how an /api/auto row
/// is told apart from a posting one, which this must not disturb. Without it an owner rule, once set,
/// could never be taken off: no repo under it could get its own rule back.
pub const CLEAR: &str = "none";

impl Post {
    pub fn parse(s: &str) -> Option<Post> {
        match s {
            "post" => Some(Post::Now),
            "hold" => Some(Post::Hold),
            _ => None,
        }
    }
    pub fn word(&self) -> &'static str {
        match self {
            Post::Now => "post",
            Post::Hold => "hold",
        }
    }
}

/// Whether a review's findings are posted on the lines they name, for one repo or one owner.
///
/// ponytail: a word, like `Post`, and for the same reason: the next settling is "inline, but only
/// blocking findings", and a bool would need the file rewritten to take one.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Inline {
    On,
    Off,
}

/// The field an inline rule is written under. A line without it is not an inline rule, which is how
/// these rows sit in the same store as `scope()`'s and `posting()`'s without either reading them.
pub const INLINE: &str = "inline";

impl Inline {
    pub fn parse(s: &str) -> Option<Inline> {
        match s {
            "on" => Some(Inline::On),
            "off" => Some(Inline::Off),
            _ => None,
        }
    }
    pub fn word(&self) -> &'static str {
        match self {
            Inline::On => "on",
            Inline::Off => "off",
        }
    }
    pub fn on(&self) -> bool {
        *self == Inline::On
    }
}

/// Which repos and owners have an inline rule of their own.
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Inlines {
    pub repos: HashMap<String, Inline>,
    pub owners: HashMap<String, Inline>,
}

impl Inlines {
    /// A repo row beats an owner row beats `fallback`, which is the global switch.
    ///
    /// ponytail: a name this cannot fold is OFF, whatever the global says — the same asymmetry
    /// `Rules::of` holds for. Falling back here would put comments on a PR we cannot name, and the
    /// direction that costs other people is the one that posts.
    pub fn of(&self, repo: &str, fallback: bool) -> bool {
        let r = key(repo);
        if r.is_empty() {
            return false;
        }
        if let Some(v) = self.repos.get(&r) {
            return v.on();
        }
        let o = r.split('/').next().unwrap_or("");
        self.owners.get(o).map(|v| v.on()).unwrap_or(fallback)
    }

    /// (target, rule), owners then repos, each sorted. Owners read back as `acme/*`.
    pub fn listed(&self) -> Vec<(String, Inline)> {
        let mut repos: Vec<(String, Inline)> = self.repos.iter().map(|(k, v)| (k.clone(), *v)).collect();
        let mut owners: Vec<(String, Inline)> =
            self.owners.iter().map(|(k, v)| (format!("{k}/*"), *v)).collect();
        repos.sort_by(|a, b| a.0.cmp(&b.0));
        owners.sort_by(|a, b| a.0.cmp(&b.0));
        owners.extend(repos);
        owners
    }
}

/// One read of the store for the inline rules.
///
/// ponytail: skips any line without an `inline` word, the way `posting()` skips one without a
/// `post_*` and `scope()` one without an `auto`. Three sets of rules, one append-only file, and none
/// of them can see another's rows.
pub fn inlines() -> Inlines {
    let mut out = Inlines::default();
    for line in read().lines() {
        let Ok(Value::Object(e)) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let Some(Value::String(word)) = e.get(INLINE) else {
            continue;
        };
        // ponytail: CLEAR removes the rule instead of setting one, so an owner rule can be taken off
        // and the repos under it fall back to the global switch again. Same word `Post` uses.
        let rule = Inline::parse(word);
        if rule.is_none() && word != CLEAR {
            continue;
        }
        if let Some(Value::String(r)) = e.get("repo") {
            let r = key(r);
            if !r.is_empty() {
                match rule {
                    Some(v) => out.repos.insert(r, v),
                    None => out.repos.remove(&r),
                };
            }
        } else if let Some(Value::String(o)) = e.get("owner") {
            let o = owner_key(o);
            if !o.is_empty() {
                match rule {
                    Some(v) => out.owners.insert(o, v),
                    None => out.owners.remove(&o),
                };
            }
        }
    }
    out
}

/// Give one repo its own inline rule, or `None` to drop it and follow the owner or the switch again.
pub fn set_inline(repo: &str, rule: Option<Inline>) -> String {
    let r = key(repo);
    if r.is_empty() {
        return format!("{repo} is not an owner/name");
    }
    let now = inlines();
    if now.repos.get(&r).copied() == rule {
        return String::new(); // already says this; writing it again grows the file for nothing
    }
    append_inline(&[("repo", &r)], rule)
}

/// The same for a whole owner.
pub fn set_inline_owner(owner: &str, rule: Option<Inline>) -> String {
    let o = owner_key(owner);
    if o.is_empty() {
        return format!("{owner} is not an owner");
    }
    if inlines().owners.get(&o).copied() == rule {
        return String::new();
    }
    append_inline(&[("owner", &o)], rule)
}

fn append_inline(fields: &[(&str, &str)], rule: Option<Inline>) -> String {
    let mut all: Vec<(&str, &str)> = fields.to_vec();
    all.push((INLINE, rule.map(|r| r.word()).unwrap_or(CLEAR)));
    bind::append_to(store(), &all, None)
}

/// Which review a rule is about. Two independent settings: pressing `r` yourself is a different
/// decision from letting auto run unattended, and the operator asked for both.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ran {
    /// You pressed `r`.
    Manual,
    /// Auto started it.
    Auto,
}

impl Ran {
    fn field(&self) -> &'static str {
        match self {
            Ran::Manual => "post_manual",
            Ran::Auto => "post_auto",
        }
    }
}

/// The rules for one kind of review.
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Rules {
    pub repos: HashMap<String, Post>,
    pub owners: HashMap<String, Post>,
}

impl Rules {
    /// A repo row beats an owner row beats the default — the chain `armed` already uses.
    ///
    /// ponytail: a name this cannot fold HOLDS, the same call `Scope::armed` makes for the same
    /// failure. The first draft argued the fold is only a lookup key, so failing it just means no
    /// rule was found — but the consequence is not symmetric. Falling to the default here puts a
    /// verdict on a PR, and posting to something we cannot name is the half that costs other people.
    pub fn of(&self, repo: &str) -> Post {
        let r = key(repo);
        if r.is_empty() {
            return Post::Hold;
        }
        if let Some(v) = self.repos.get(&r) {
            return *v;
        }
        let o = r.split('/').next().unwrap_or("");
        *self.owners.get(o).unwrap_or(&Post::default())
    }

    /// (target, policy), owners then repos, each sorted. Owners read back as `acme/*`.
    pub fn listed(&self) -> Vec<(String, Post)> {
        let mut repos: Vec<(String, Post)> = self.repos.iter().map(|(k, v)| (k.clone(), *v)).collect();
        let mut owners: Vec<(String, Post)> =
            self.owners.iter().map(|(k, v)| (format!("{k}/*"), *v)).collect();
        repos.sort_by(|a, b| a.0.cmp(&b.0));
        owners.sort_by(|a, b| a.0.cmp(&b.0));
        owners.extend(repos);
        owners
    }
}

/// Both sets of rules, from one read.
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Posting {
    pub manual: Rules,
    pub auto: Rules,
    /// Owners switched to "each repo on its own". Their rule, if any, stays in the store as the fallback for
    /// a repo that has none of its own -- so a repo nobody has listed keeps holding what the owner held.
    pub per_repo: std::collections::HashSet<String>,
}

impl Posting {
    pub fn of(&self, repo: &str, ran: Ran) -> Post {
        self.rules(ran).of(repo)
    }
    pub fn rules(&self, ran: Ran) -> &Rules {
        match ran {
            Ran::Manual => &self.manual,
            Ran::Auto => &self.auto,
        }
    }
}

/// One read of the store for the posting rules.
///
/// ponytail: the same file as `scope()`, and the two cannot see each other's rows — `scope()` skips
/// any line without an `auto` boolean, and this skips any without a `post_*` word. The reason bind.rs
/// could not take a second field was its `touched` set, which three rules read; this store has no
/// equivalent.
pub fn posting() -> Posting {
    let mut out = Posting::default();
    for line in read().lines() {
        let Ok(Value::Object(e)) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        // the owner's mode: a line of its own, which neither the post_* loop below nor scope() reads
        if let (Some(Value::String(o)), Some(Value::Bool(b))) = (e.get("owner"), e.get(PER_REPO)) {
            let o = owner_key(o);
            if !o.is_empty() {
                if *b {
                    out.per_repo.insert(o);
                } else {
                    out.per_repo.remove(&o);
                }
            }
        }
        for ran in [Ran::Manual, Ran::Auto] {
            let Some(word) = e.get(ran.field()).and_then(|v| v.as_str()) else {
                continue;
            };
            // an unknown word is still ignored; CLEAR is the one that takes a rule away again
            let set = Post::parse(word);
            if set.is_none() && word != CLEAR {
                continue;
            }
            let rules = match ran {
                Ran::Manual => &mut out.manual,
                Ran::Auto => &mut out.auto,
            };
            let (map, k) = if let Some(Value::String(o)) = e.get("owner") {
                (&mut rules.owners, owner_key(o))
            } else if let Some(Value::String(r)) = e.get("repo") {
                (&mut rules.repos, key(r))
            } else {
                continue;
            };
            if k.is_empty() {
                continue;
            }
            match set {
                Some(p) => drop(map.insert(k, p)),
                None => drop(map.remove(&k)),
            }
        }
    }
    out
}

/// Say what happens to `repo`'s reviews of one kind. Returns "" or why it did not.
pub fn set_post(repo: &str, ran: Ran, p: Post) -> String {
    let r = key(repo);
    if r.is_empty() {
        return format!("{repo} is not an owner/name");
    }
    if posting().rules(ran).repos.get(&r) == Some(&p) {
        return String::new();
    }
    append_word(&[("repo", &r)], ran, p)
}

/// The same for a whole owner.
pub fn set_post_owner(owner: &str, ran: Ran, p: Post) -> String {
    let o = owner_key(owner);
    if o.is_empty() {
        return format!("{owner} is not an owner");
    }
    if posting().rules(ran).owners.get(&o) == Some(&p) {
        return String::new();
    }
    append_word(&[("owner", &o)], ran, p)
}

fn append_word(fields: &[(&str, &str)], ran: Ran, p: Post) -> String {
    append_raw(fields, ran, p.word())
}

fn append_raw(fields: &[(&str, &str)], ran: Ran, word: &str) -> String {
    let mut all: Vec<(&str, &str)> = fields.to_vec();
    all.push((ran.field(), word));
    bind::append_to(store(), &all, None)
}

/// The field that switches an owner to "each repo on its own" (true) or back (false).
pub const PER_REPO: &str = "per_repo";

fn set_per_repo(owner: &str, on: bool, was: bool) -> String {
    if was == on {
        return String::new();
    }
    bind::append_to(store(), &[("owner", owner)], Some((PER_REPO, on)))
}

/// Turn owner control on or off for `owner`: one setting for every repo under it, or one per repo.
///
/// ponytail: one operation, not a sequence of set and clear calls from the page. The switch means "the
/// owner's settings apply to all of its repos", which is only true if no repo rule is left beating it, and
/// it must not change what happens to any review on the board as you flip it:
///
/// - ON: the owner takes one word per kind of review -- `hold` if any repo under it holds that kind, on the
///   board or ruled in the store, else `post` -- and every repo rule under the owner is taken off, on the
///   board or not. ponytail: "any repo in the store", not only the board.
///   The rules taken off include repos with no PR on the board today; judging the word from the board
///   alone cleared a repo's `hold` and its next PR posted.
/// - OFF: each repo on the board under the owner is given the word it had from the owner as a rule of its
///   own, and the owner is marked per repo. ponytail: the owner's rule is KEPT, as the fallback. OFF cannot
///   pin a repo it has never seen, so taking the rule away sent every repo with no PR on the board today
///   back to the default -- a held owner's quiet repo started posting its next review. A kind of review
///   the owner never had a rule for is left alone: its repos already follow the default.
pub fn govern(owner: &str, on: bool, board: &[String]) -> String {
    let o = owner_key(owner);
    if o.is_empty() {
        return format!("{owner} is not an owner");
    }
    let before = posting();
    let mut under: Vec<String> = board
        .iter()
        .map(|r| key(r))
        .filter(|k| k.split('/').next() == Some(o.as_str()))
        .collect();
    under.sort();
    under.dedup();
    for ran in [Ran::Manual, Ran::Auto] {
        let rules = before.rules(ran);
        let failed = if on {
            let held_in_store = rules
                .repos
                .iter()
                .any(|(k, v)| k.split('/').next() == Some(o.as_str()) && *v == Post::Hold);
            // the owner's own word counts too: switched per repo, it is still the fallback that holds a repo
            // nobody listed, and switching back on must not quietly let those post
            let owner_holds = rules.owners.get(&o) == Some(&Post::Hold);
            let word = if owner_holds || held_in_store || under.iter().any(|r| rules.of(r) == Post::Hold) {
                Post::Hold
            } else {
                Post::Now
            };
            let mut e = set_post_owner(&o, ran, word);
            let carved: Vec<String> = rules
                .repos
                .keys()
                .filter(|k| k.split('/').next() == Some(o.as_str()))
                .cloned()
                .collect();
            for r in carved {
                if e.is_empty() {
                    e = clear_post(&r, ran);
                }
            }
            e
        } else if !rules.owners.contains_key(&o) {
            String::new()
        } else {
            let mut e = String::new();
            for r in &under {
                if e.is_empty() {
                    e = set_post(r, ran, rules.of(r));
                }
            }
            e
        };
        if !failed.is_empty() {
            return failed;
        }
    }
    // last: the mode flips only once every rule it relies on is written
    set_per_repo(&o, !on, before.per_repo.contains(&o))
}

/// Take the rule off one repo, so it goes back to following its owner. A repo with no rule is a no-op.
pub fn clear_post(repo: &str, ran: Ran) -> String {
    let r = key(repo);
    if r.is_empty() {
        return format!("{repo} is not an owner/name");
    }
    if !posting().rules(ran).repos.contains_key(&r) {
        return String::new();
    }
    append_raw(&[("repo", &r)], ran, CLEAR)
}

/// Take the rule off one owner, so every repo under it is set on its own again.
pub fn clear_post_owner(owner: &str, ran: Ran) -> String {
    let o = owner_key(owner);
    if o.is_empty() {
        return format!("{owner} is not an owner");
    }
    if !posting().rules(ran).owners.contains_key(&o) {
        return String::new();
    }
    append_raw(&[("owner", &o)], ran, CLEAR)
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
        out.push("  auto-review covers every repo on the board; name one to narrow it".to_string());
    }
    out.extend(rows.iter().map(|(t, v)| {
        format!(
            "  {t:<36}  →  {}",
            if *v { "auto-review" } else { "not auto-reviewed" }
        )
    }));
    if s.everywhere() && !rows.is_empty() {
        out.push("  nothing is armed, so the rows above do not apply yet".to_string());
    } else if !s.everywhere() {
        out.push("  every other repo is left alone".to_string());
    }
    out
}

/// Add one line: the fields, then the `auto` flag as a bare JSON bool.
fn append(fields: &[(&str, &str)], on: bool) -> String {
    bind::append_to(store(), fields, Some(("auto", on)))
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
    use std::sync::MutexGuard;

    fn fresh() -> (MutexGuard<'static, ()>, tempfile::TempDir) {
        let g = crate::config::test_lock();
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.autorev = d.path().join("autorev"));
        (g, d)
    }

    /// #161: a repo row beats an owner row beats the global switch, so `--inline` on for your own
    /// repos and off for one you are a guest in is one rule rather than a machine-wide decision.
    #[test]
    fn an_inline_rule_goes_repo_then_owner_then_the_switch() {
        let (_g, _d) = fresh();
        // nothing written: every repo follows the switch, whichever way it is set
        assert!(!inlines().of("acme/api", false));
        assert!(inlines().of("acme/api", true));

        assert_eq!(set_inline_owner("acme", Some(Inline::On)), "");
        assert!(inlines().of("acme/api", false), "the owner rule beats the switch");
        assert!(
            !inlines().of("other/thing", false),
            "and covers only what it names"
        );

        assert_eq!(set_inline("acme/legacy", Some(Inline::Off)), "");
        assert!(
            !inlines().of("acme/legacy", false),
            "the repo rule beats its owner"
        );
        assert!(inlines().of("acme/api", false), "and carves out only itself");

        // dropping the repo rule puts it back under the owner
        assert_eq!(set_inline("acme/legacy", None), "");
        assert!(inlines().of("acme/legacy", false));
        // and dropping the owner's puts everything back on the switch
        assert_eq!(set_inline_owner("acme", None), "");
        assert!(!inlines().of("acme/api", false));
        assert!(inlines().of("acme/api", true));
    }

    /// ponytail: the direction that costs other people is the one that posts, so a name that cannot
    /// be folded is off whatever the switch says — the asymmetry `Rules::of` holds for.
    #[test]
    fn a_name_that_cannot_be_folded_is_never_inlined() {
        let (_g, _d) = fresh();
        assert!(!inlines().of("", true));
        assert!(!inlines().of("not-an-owner-name", true));
        assert_eq!(set_inline("", Some(Inline::On)), " is not an owner/name");
        assert_eq!(set_inline_owner("", Some(Inline::On)), " is not an owner");
    }

    /// The three rule sets share one append-only file and none may read another's rows.
    #[test]
    fn inline_rows_do_not_disturb_the_arming_or_posting_rows() {
        let (_g, _d) = fresh();
        assert_eq!(set("acme/api", true), "");
        assert_eq!(set_post("acme/api", Ran::Manual, Post::Hold), "");
        assert_eq!(set_inline("acme/api", Some(Inline::On)), "");

        assert!(scope().armed("acme/api"), "the inline row did not disarm it");
        assert_eq!(
            posting().of("acme/api", Ran::Manual),
            Post::Hold,
            "nor change what happens when a review finishes"
        );
        assert!(inlines().of("acme/api", false));
        // and the rows that are not inline rules are invisible here
        assert!(inlines().repos.len() == 1 && inlines().owners.is_empty());
    }

    /// Writing the rule it already has must not grow the file: it is append-only.
    #[test]
    fn setting_an_inline_rule_twice_writes_once() {
        let (_g, d) = fresh();
        assert_eq!(set_inline("acme/api", Some(Inline::On)), "");
        let once = std::fs::read_to_string(d.path().join("autorev")).unwrap();
        assert_eq!(set_inline("acme/api", Some(Inline::On)), "");
        assert_eq!(std::fs::read_to_string(d.path().join("autorev")).unwrap(), once);
        // and clearing a rule that is not there is likewise nothing to write
        assert_eq!(set_inline("acme/other", None), "");
        assert_eq!(std::fs::read_to_string(d.path().join("autorev")).unwrap(), once);
    }

    #[test]
    fn listed_reads_owners_back_with_a_star_and_sorts() {
        let (_g, _d) = fresh();
        assert_eq!(set_inline("z/last", Some(Inline::Off)), "");
        assert_eq!(set_inline("a/first", Some(Inline::On)), "");
        assert_eq!(set_inline_owner("acme", Some(Inline::On)), "");
        assert_eq!(
            inlines().listed(),
            vec![
                ("acme/*".to_string(), Inline::On),
                ("a/first".to_string(), Inline::On),
                ("z/last".to_string(), Inline::Off),
            ]
        );
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
            vec!["  auto-review covers every repo on the board; name one to narrow it"]
        );

        set("acme/web", false);
        let lines = report(&scope());
        assert_eq!(
            lines[0].trim(),
            "auto-review covers every repo on the board; name one to narrow it"
        );
        assert!(lines[1].contains("acme/web"));
        assert_eq!(
            lines[2].trim(),
            "nothing is armed, so the rows above do not apply yet"
        );
        assert!(!lines.iter().any(|l| l.contains("left alone")), "{lines:?}");
    }

    #[test]
    fn the_report_says_what_is_left_alone_once_something_is_armed() {
        let (_g, _d) = fresh();
        set("acme/api", true);
        let lines = report(&scope());
        assert!(!lines[0].contains("covers every repo"), "{lines:?}");
        assert!(lines[0].contains("acme/api"));
        assert_eq!(lines.last().unwrap().trim(), "every other repo is left alone");
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

    /// A repo this cannot name is never auto-reviewed. It used to follow the default, so the same
    /// unnamed row was reviewed while nothing was armed and skipped once something was.
    #[test]
    fn a_repo_it_cannot_name_is_never_auto_reviewed() {
        let (_g, _d) = fresh();
        assert!(scope().everywhere());
        assert!(!scope().armed(""));
        assert!(!scope().armed("notes"));
        set("acme/api", true);
        assert!(!scope().armed(""));
    }

    #[test]
    fn nothing_set_posts_everything_as_it_always_has() {
        let (_g, _d) = fresh();
        let p = posting();
        assert_eq!(p.of("acme/api", Ran::Manual), Post::Now);
        assert_eq!(p.of("acme/api", Ran::Auto), Post::Now);
        assert!(p.manual.listed().is_empty() && p.auto.listed().is_empty());
    }

    /// Two independent settings: pressing `r` is a different decision from letting auto run.
    #[test]
    fn manual_and_auto_are_set_apart() {
        let (_g, _d) = fresh();
        assert_eq!(set_post("acme/api", Ran::Auto, Post::Hold), "");
        let p = posting();
        assert_eq!(p.of("acme/api", Ran::Auto), Post::Hold);
        assert_eq!(
            p.of("acme/api", Ran::Manual),
            Post::Now,
            "the other kind is untouched"
        );

        set_post("acme/api", Ran::Manual, Post::Hold);
        assert_eq!(posting().of("acme/api", Ran::Manual), Post::Hold);
    }

    #[test]
    fn a_repo_rule_beats_an_owner_rule_beats_the_default() {
        let (_g, _d) = fresh();
        set_post_owner("acme", Ran::Auto, Post::Hold);
        assert_eq!(posting().of("acme/api", Ran::Auto), Post::Hold);
        assert_eq!(posting().of("other/thing", Ran::Auto), Post::Now);

        set_post("acme/api", Ran::Auto, Post::Now);
        assert_eq!(
            posting().of("acme/api", Ran::Auto),
            Post::Now,
            "the repo row wins"
        );
        assert_eq!(posting().of("acme/web", Ran::Auto), Post::Hold);
    }

    /// The asymmetry with `armed`: falling to the default there skips a review, here it puts a
    /// verdict on a PR. Posting to something we cannot name is the half that costs other people.
    #[test]
    fn a_repo_it_cannot_name_is_never_posted_to() {
        let (_g, _d) = fresh();
        assert_eq!(posting().of("", Ran::Auto), Post::Hold);
        assert_eq!(posting().of("notes", Ran::Manual), Post::Hold);
        set_post("acme/api", Ran::Auto, Post::Now);
        assert_eq!(
            posting().of("", Ran::Auto),
            Post::Hold,
            "and still, whatever else is set"
        );
    }

    #[test]
    fn the_last_word_on_a_target_wins() {
        let (_g, _d) = fresh();
        set_post("acme/api", Ran::Auto, Post::Hold);
        set_post("acme/api", Ran::Auto, Post::Now);
        assert_eq!(posting().of("acme/api", Ran::Auto), Post::Now);
    }

    #[test]
    fn saying_the_same_posting_rule_twice_writes_nothing() {
        let (_g, d) = fresh();
        set_post("acme/api", Ran::Auto, Post::Hold);
        let before = std::fs::read_to_string(d.path().join("autorev")).unwrap();
        assert_eq!(set_post("acme/api", Ran::Auto, Post::Hold), "");
        assert_eq!(std::fs::read_to_string(d.path().join("autorev")).unwrap(), before);
    }

    #[test]
    fn a_key_it_cannot_read_is_refused_by_the_posting_rules_too() {
        let (_g, _d) = fresh();
        assert!(set_post("notes", Ran::Auto, Post::Hold).contains("owner/name"));
        assert!(set_post_owner("acme/api", Ran::Auto, Post::Hold).contains("is not an owner"));
        assert_eq!(posting(), Posting::default());
    }

    /// The two readers share one file and must not see each other's rows.
    #[test]
    fn the_arm_flag_and_the_posting_rules_do_not_read_each_other() {
        let (_g, _d) = fresh();
        set("acme/api", true);
        assert_eq!(
            posting(),
            Posting::default(),
            "an arm row says nothing about posting"
        );

        set_post("acme/web", Ran::Auto, Post::Hold);
        assert!(
            scope().armed("acme/api"),
            "a posting row did not disturb the scope"
        );
        assert!(!scope().armed("acme/web"), "and did not arm anything either");
        assert_eq!(
            scope().listed(),
            vec![("acme/api".to_string(), true)],
            "the posting row is invisible to the scope"
        );
    }

    #[test]
    fn a_word_it_does_not_know_is_not_a_rule() {
        let (_g, d) = fresh();
        std::fs::create_dir_all(d.path()).unwrap();
        std::fs::write(
            d.path().join("autorev"),
            "{\"repo\": \"acme/api\", \"post_auto\": \"maybe\"}\n",
        )
        .unwrap();
        assert_eq!(posting().of("acme/api", Ran::Auto), Post::Now);
    }

    #[test]
    fn a_rule_can_be_taken_off_again_and_the_repo_goes_back_to_following() {
        let (_g, _d) = fresh();
        set_post_owner("acme", Ran::Auto, Post::Hold);
        set_post("acme/api", Ran::Auto, Post::Now);
        assert_eq!(posting().of("acme/api", Ran::Auto), Post::Now, "carved out");

        // the repo stops deciding for itself and follows the owner again
        assert_eq!(clear_post("acme/api", Ran::Auto), "");
        let p = posting();
        assert!(!p.auto.repos.contains_key("acme/api"), "no rule of its own");
        assert_eq!(p.of("acme/api", Ran::Auto), Post::Hold, "the owner's word");

        // and the owner rule itself comes off, which is what the panel's toggle does
        assert_eq!(clear_post_owner("acme", Ran::Auto), "");
        let p = posting();
        assert!(!p.auto.owners.contains_key("acme"));
        assert_eq!(p.of("acme/api", Ran::Auto), Post::Now, "back to the default");
    }

    #[test]
    fn clearing_touches_one_axis_and_one_target_only() {
        let (_g, _d) = fresh();
        set_post_owner("acme", Ran::Auto, Post::Hold);
        set_post_owner("acme", Ran::Manual, Post::Hold);
        set_post_owner("zeta", Ran::Auto, Post::Hold);
        clear_post_owner("acme", Ran::Auto);
        let p = posting();
        assert!(!p.auto.owners.contains_key("acme"));
        assert_eq!(p.manual.owners.get("acme"), Some(&Post::Hold), "other axis");
        assert_eq!(p.auto.owners.get("zeta"), Some(&Post::Hold), "other owner");
    }

    #[test]
    fn clearing_what_has_no_rule_writes_nothing() {
        let (_g, d) = fresh();
        assert_eq!(clear_post("acme/api", Ran::Auto), "");
        assert_eq!(clear_post_owner("acme", Ran::Auto), "");
        assert!(!d.path().join("autorev").exists(), "no line appended");
        // and a name it cannot read is refused rather than written
        assert!(!clear_post("nope", Ran::Auto).is_empty());
        assert!(!clear_post_owner("a/b", Ran::Auto).is_empty());
    }

    #[test]
    fn a_cleared_rule_can_be_set_again_after() {
        let (_g, _d) = fresh();
        set_post_owner("acme", Ran::Auto, Post::Hold);
        clear_post_owner("acme", Ran::Auto);
        set_post_owner("acme", Ran::Auto, Post::Hold);
        assert_eq!(
            posting().auto.owners.get("acme"),
            Some(&Post::Hold),
            "the no-op guard must read the CLEAR line, not the hold before it"
        );
    }

    #[test]
    fn clearing_leaves_the_arm_flag_alone() {
        let (_g, _d) = fresh();
        set_owner("acme", true);
        set_post_owner("acme", Ran::Auto, Post::Hold);
        clear_post_owner("acme", Ran::Auto);
        assert!(
            scope().armed("acme/api"),
            "the two readers still cannot see each other"
        );
    }

    #[test]
    fn owner_control_on_makes_one_setting_for_all_without_starting_to_post_a_held_review() {
        let (_g, _d) = fresh();
        let board = ["acme/api".to_string(), "acme/web".to_string()];
        set_post("acme/web", Ran::Auto, Post::Hold);
        set_post("acme/old", Ran::Manual, Post::Hold); // a repo rule for a repo not on the board
        set_post("zeta/x", Ran::Auto, Post::Hold);
        assert_eq!(govern("acme", true, &board), "");
        let p = posting();
        // web held auto, so the owner holds auto; acme/old held manual and has no PR on the board, and
        // it counts too -- its rule is taken off below, so posting would start its next PR posting
        assert_eq!(p.auto.owners.get("acme"), Some(&Post::Hold));
        assert_eq!(p.manual.owners.get("acme"), Some(&Post::Hold));
        // one setting for all means all: no repo rule under acme is left beating the owner
        assert!(p.auto.repos.keys().all(|k| !k.starts_with("acme/")));
        assert!(
            p.manual.repos.keys().all(|k| !k.starts_with("acme/")),
            "off-board too"
        );
        assert_eq!(p.of("acme/web", Ran::Auto), Post::Hold, "still held");
        assert_eq!(
            p.auto.repos.get("zeta/x"),
            Some(&Post::Hold),
            "another owner untouched"
        );
    }

    #[test]
    fn owner_control_off_gives_each_repo_the_word_it_had() {
        let (_g, _d) = fresh();
        let board = ["acme/api".to_string(), "acme/web".to_string()];
        set_post_owner("acme", Ran::Auto, Post::Hold);
        set_post_owner("acme", Ran::Manual, Post::Now);
        let was: Vec<Post> = board
            .iter()
            .flat_map(|r| [posting().of(r, Ran::Manual), posting().of(r, Ran::Auto)])
            .collect();
        assert_eq!(govern("acme", false, &board), "");
        let p = posting();
        assert!(p.per_repo.contains("acme"), "the owner is per repo now");
        assert_eq!(
            p.auto.owners.get("acme"),
            Some(&Post::Hold),
            "its rule stays, as the fallback"
        );
        // each repo now owns the word, and nothing on the board changed as the switch flipped
        assert_eq!(p.auto.repos.get("acme/api"), Some(&Post::Hold));
        let now: Vec<Post> = board
            .iter()
            .flat_map(|r| [p.of(r, Ran::Manual), p.of(r, Ran::Auto)])
            .collect();
        assert_eq!(was, now);
    }

    #[test]
    fn owner_control_on_holds_for_a_repo_that_held_but_has_no_pr_on_the_board() {
        let (_g, _d) = fresh();
        // acme/old holds auto and has nothing on the board today; its rule is about to be taken off
        set_post("acme/old", Ran::Auto, Post::Hold);
        assert_eq!(govern("acme", true, &["acme/api".to_string()]), "");
        let p = posting();
        assert_eq!(
            p.auto.owners.get("acme"),
            Some(&Post::Hold),
            "its next PR must not post"
        );
        assert!(!p.auto.repos.contains_key("acme/old"));
        assert_eq!(
            p.manual.owners.get("acme"),
            Some(&Post::Now),
            "nothing held manual"
        );
    }

    #[test]
    fn owner_control_off_leaves_a_kind_the_owner_never_ruled_alone() {
        let (_g, d) = fresh();
        let board = ["acme/api".to_string(), "acme/web".to_string()];
        set_post_owner("acme", Ran::Auto, Post::Hold);
        let lines = || {
            std::fs::read_to_string(d.path().join("autorev"))
                .unwrap()
                .lines()
                .count()
        };
        let before = lines();
        assert_eq!(govern("acme", false, &board), "");
        let p = posting();
        assert!(
            p.manual.repos.is_empty(),
            "no manual rule was pinned: the owner had none"
        );
        assert_eq!(p.auto.repos.len(), 2, "auto was pinned on both");
        assert_eq!(lines(), before + 3, "two repo pins and the mode, nothing else");
    }

    #[test]
    fn owner_control_off_keeps_a_hold_for_a_repo_with_no_pr_on_the_board() {
        let (_g, _d) = fresh();
        set_post_owner("acme", Ran::Auto, Post::Hold);
        assert_eq!(govern("acme", false, &["acme/api".to_string()]), "");
        let p = posting();
        assert_eq!(
            p.of("acme/old", Ran::Auto),
            Post::Hold,
            "never listed, still held"
        );
        assert_eq!(p.auto.repos.get("acme/api"), Some(&Post::Hold), "listed, pinned");
    }

    #[test]
    fn the_owner_mode_is_its_own_line_and_the_last_one_wins() {
        let (_g, _d) = fresh();
        set_post_owner("acme", Ran::Auto, Post::Hold);
        govern("acme", false, &[]);
        assert!(posting().per_repo.contains("acme"));
        govern("acme", true, &[]);
        let p = posting();
        assert!(!p.per_repo.contains("acme"), "on again");
        assert_eq!(p.auto.owners.get("acme"), Some(&Post::Hold));
        // the auto-arm reader does not see it, and it does not see the arm flag
        set_owner("acme", true);
        govern("acme", false, &[]);
        assert!(scope().armed("acme/api"));
        assert!(posting().per_repo.contains("acme"));
    }

    #[test]
    fn switching_back_on_holds_what_a_repo_was_set_to_post_while_the_owner_held() {
        // on can make a repo hold that posted, never the reverse: the owner's own hold counts
        let (_g, _d) = fresh();
        let board = ["acme/api".to_string(), "acme/web".to_string()];
        set_post_owner("acme", Ran::Auto, Post::Hold);
        govern("acme", false, &board);
        for r in &board {
            set_post(r, Ran::Auto, Post::Now);
        }
        govern("acme", true, &board);
        let p = posting();
        assert_eq!(p.auto.owners.get("acme"), Some(&Post::Hold));
        assert!(
            board.iter().all(|r| p.of(r, Ran::Auto) == Post::Hold),
            "both hold again"
        );
    }

    #[test]
    fn switching_to_the_mode_it_is_already_in_writes_nothing() {
        let (_g, d) = fresh();
        set_post_owner("acme", Ran::Auto, Post::Hold);
        let lines = || {
            std::fs::read_to_string(d.path().join("autorev"))
                .unwrap()
                .lines()
                .count()
        };
        govern("acme", false, &[]);
        let once = lines();
        govern("acme", false, &[]);
        assert_eq!(lines(), once);
    }

    #[test]
    fn owner_control_refuses_what_is_not_an_owner() {
        let (_g, _d) = fresh();
        assert!(!govern("acme/api", true, &[]).is_empty(), "a repo, not an owner");
        assert!(posting().auto.owners.is_empty());
    }

    #[test]
    fn posting_rules_list_owners_first_and_sort_each() {
        let (_g, _d) = fresh();
        set_post("zeta/one", Ran::Auto, Post::Hold);
        set_post("acme/api", Ran::Auto, Post::Now);
        set_post_owner("beta", Ran::Auto, Post::Hold);
        assert_eq!(
            posting().auto.listed(),
            vec![
                ("beta/*".to_string(), Post::Hold),
                ("acme/api".to_string(), Post::Now),
                ("zeta/one".to_string(), Post::Hold),
            ]
        );
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
