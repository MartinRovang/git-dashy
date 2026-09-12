//! Which team a repo belongs to (~/.prs_bindings). Port of dashy/core/bind.py.
//!
//! Declared by the user, never inferred from what they happened to review.
//!
//! A brief is DECLARED, somebody states what the work is for, so it gets DECLARED selection. The
//! alternative on the table was the covering rule ("the team's shared review log names this repo"),
//! which is how memory visibility is decided today, and the two look interchangeable. They are not:
//!
//!   - a log entry is a SIDE EFFECT of reviewing one PR, so reviewing once silently changes what every
//!     later review of that repo is told the work is for
//!   - nothing ever removes a repo from a log, so the decision has no undo
//!   - two teams can both name a repo and the log gives no way to prefer one
//!   - nothing on screen says which brief a review is about to get
//!
//! ponytail: same file shape as install.REGISTRY: one JSON object per line, deduplicated on read,
//! removal by tombstone. Not a second format invented for a second registry: that one already survived
//! a review round on appending without a lock and on reading back a file an older version wrote.

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::PathBuf;

use serde_json::Value;

use crate::team;

fn store() -> PathBuf {
    crate::config::get().bindings
}

fn read() -> String {
    // ponytail: no bindings is the normal state, and an unreadable file must not select a brief
    std::fs::read_to_string(store()).unwrap_or_default()
}

/// `repo` as the owner/name this keys on, "" when it is not one.
///
/// ponytail: team.slug_of, so a URL, an ssh remote and a bare owner/name all land on the same key:
/// that is what makes a binding survive a re-clone and a move, and two checkouts of one repo agree.
/// ponytail: a single segment is REFUSED rather than stored. "notes" would key a row nothing ever
/// matches, which reads back as unbound forever with no way to tell it from never having bound it.
/// ponytail: lowercased, and ONLY here. This is the one store fed by something a person types; every
/// other key in the system arrives as GitHub's own nameWithOwner, so `bind Acme/API` must find the row
/// a review of `acme/api` looks up. It is a lookup key, not a filename anyone reads back.
pub fn key(repo: &str) -> String {
    // ponytail: a BARE name must be exactly owner/name. team.slug_of keeps the last two segments of
    // anything, so "acme/api/pull/19", a URL someone trimmed by hand, came back as "pull/19", passed
    // the check below, and bound a repo that does not exist while reporting success. A real URL or path
    // is still handed to slug_of, which is what it is for; the guard is only on the bare form.
    let raw = repo.trim().trim_end_matches('/');
    let raw = raw.strip_suffix(".git").unwrap_or(raw);
    let bare = !(raw.starts_with('/')
        || raw.starts_with("./")
        || raw.starts_with("../")
        || raw.starts_with('~')
        || raw.contains("://")
        || raw.contains('@'));
    if bare && raw.matches('/').count() != 1 {
        return String::new();
    }
    let s = team::slug_of(repo).to_lowercase();
    if s.matches('/').count() == 1 && s.split('/').all(|p| !p.is_empty()) {
        s
    } else {
        String::new()
    }
}

/// `s` as an owner name, "" when it is not one. Accepts "acme" and "acme/*".
pub fn owner_key(s: &str) -> String {
    let s = s.trim().trim_end_matches('/').to_lowercase();
    let s = s.strip_suffix("/*").unwrap_or(&s);
    if !s.is_empty() && !s.contains('/') && s != "*" {
        s.to_string()
    } else {
        String::new()
    }
}

/// One read of the store: live repo->team, live owner->team, every repo any line has named, and
/// every owner any line has named.
#[derive(Default)]
struct Entries {
    repos: HashMap<String, String>,
    owners: HashMap<String, String>,
    touched: HashSet<String>,
    named: HashSet<String>,
}

fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn team_of(e: &serde_json::Map<String, Value>) -> Option<String> {
    match e.get("team") {
        Some(Value::String(t)) if !t.is_empty() => Some(t.clone()),
        _ => None,
    }
}

fn entries() -> Entries {
    let mut out = Entries::default();
    for line in read().lines() {
        if line.trim().is_empty() {
            continue;
        }
        // ponytail: one unreadable line is not a reason to lose the rest of the file
        let e = match serde_json::from_str::<Value>(line) {
            Ok(Value::Object(e)) => e,
            _ => continue,
        };
        if e.contains_key("owner") || e.contains_key("forget_owner") {
            let o = if e.contains_key("owner") {
                e.get("owner")
            } else {
                e.get("forget_owner")
            };
            let o = match o {
                Some(Value::String(s)) => owner_key(s),
                _ => continue,
            };
            if o.is_empty() {
                continue;
            }
            out.named.insert(o.clone());
            if e.contains_key("forget_owner") {
                out.owners.remove(&o);
            } else if let Some(t) = team_of(&e) {
                out.owners.insert(o, t);
            }
            continue;
        }
        let repo = if truthy(e.get("repo")) {
            e.get("repo")
        } else {
            e.get("forget")
        };
        let r = match repo {
            Some(Value::String(s)) => key(s),
            _ => continue,
        };
        if r.is_empty() {
            continue;
        }
        out.touched.insert(r.clone());
        if e.contains_key("forget") {
            out.repos.remove(&r);
        } else if let Some(t) = team_of(&e) {
            out.repos.insert(r, t);
        }
    }
    out
}

/// {repo: team} for every repo bound by name.
pub fn bindings() -> HashMap<String, String> {
    entries().repos
}

/// {owner: team} for every owner-wide rule.
pub fn owners() -> HashMap<String, String> {
    entries().owners
}

/// (kind, slug) for `repo` against one read of the store: ("team"|"owner", slug) or ("", "").
///
/// ponytail: THE precedence, in one place. of(), why() and resolver() all come through here, so the
/// rule cannot drift between the value a review uses and the label a screen shows. It reads: an exact
/// binding first, then an explicit unbind (which beats a pattern: the one repo in an org that is not
/// the project has to be excludable), then the owner rule.
fn pick(entry: &Entries, repo: &str) -> (String, String) {
    let r = key(repo);
    if r.is_empty() {
        return (String::new(), String::new());
    }
    if let Some(t) = entry.repos.get(&r) {
        return ("team".into(), t.clone());
    }
    if entry.touched.contains(&r) {
        return (String::new(), String::new());
    }
    let o = r.split('/').next().unwrap_or("");
    match entry.owners.get(o) {
        Some(t) => ("owner".into(), t.clone()),
        None => (String::new(), String::new()),
    }
}

/// A repo -> team function built from ONE read of the store, for a caller resolving many repos.
///
/// ponytail: a draw resolves every visible PR. Calling of() per row would reopen ~/.prs_bindings once
/// per row on every keypress, and the answers could differ within a single frame.
pub fn resolver() -> Box<dyn Fn(&str) -> String + Send + Sync> {
    let entry = entries();
    Box::new(move |repo| pick(&entry, repo).1)
}

/// The team `repo` is bound to, "" when it is unbound or is not an owner/name.
pub fn of(repo: &str) -> String {
    pick(&entries(), repo).1
}

/// How `repo` resolved: ("team", key) / ("owner", key) / ("", "").
///
/// ponytail: deleted once for having no production caller, and correctly: I had written it for a
/// surface that did not exist yet. The bind screen is that surface: it has to show whether this repo
/// is bound in its own right or covered by an owner rule, because `x` unbinds only the first kind.
pub fn why(repo: &str) -> (String, String) {
    pick(&entries(), repo)
}

/// A JSON string the way Python's json.dumps writes it (ASCII only), so the file stays byte-identical
/// whichever version appended the line.
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

/// Python's repr() of a string, for the messages that quote what was typed.
fn repr(s: &str) -> String {
    if s.contains('\'') && !s.contains('"') {
        format!("\"{}\"", s)
    } else {
        format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
    }
}

/// Add one line. Returns "" or why it could not: never panics.
///
/// ponytail: seed() runs from team.activate(), which runs at startup, inside the UI. An unwritable
/// ~/.prs_bindings (a directory of that name, a read-only home, a full disk) would otherwise unwind
/// out of the wrapper and take the whole dashboard down before it drew anything. This program has
/// killed a session that way once already, over a path that could not be resolved.
fn append(fields: &[(&str, &str)]) -> String {
    let line = fields
        .iter()
        .map(|(k, v)| format!("{}: {}", dumps(k), dumps(v)))
        .collect::<Vec<_>>()
        .join(", ");
    let p = store();
    let parent = p
        .parent()
        .filter(|d| !d.as_os_str().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| ".".into());
    let go = || -> std::io::Result<()> {
        std::fs::create_dir_all(&parent)?;
        // one append; duplicates and reversals are resolved on read
        let mut f = std::fs::OpenOptions::new().append(true).create(true).open(&p)?;
        f.write_all(format!("{{{}}}\n", line).as_bytes())
    };
    match go() {
        Ok(()) => String::new(),
        Err(e) => e.to_string(),
    }
}

/// Bind `repo` to team `to`. Returns "" or why it did not.
///
/// ponytail: `to` is folded to its KEY, here and in bind_owner, the same way `repo` is folded to its
/// key above, and for the same reason: this is the store a person types into. `--team NeoMedSys_team`
/// was stored verbatim, reported success and resolved to nothing, because the resolver folds case and
/// "neomedsys_team" is not the key "neomedsys-team".
pub fn bind(repo: &str, to: &str) -> String {
    let r = key(repo);
    if r.is_empty() {
        return format!("{} is not an owner/name", repr(repo));
    }
    let to = team::key_of(to);
    if to.is_empty() {
        return "a binding needs a team".into();
    }
    if bindings().get(&r) == Some(&to) {
        return String::new(); // already what was asked for; appending it again buys nothing
    }
    append(&[("repo", &r), ("team", &to)])
}

/// Unbind `repo`, and record that it was unbound on purpose. Returns "" or why it could not.
///
/// ponytail: "" or a reason, like bind(), not a bool for "was it bound". Whether it WAS bound is a
/// separate question with its own answer in of(), and a bool cannot distinguish "there was nothing to
/// remove" from "the tombstone could not be written", which are opposite outcomes for the caller.
/// ponytail: the tombstone is written even when there was nothing to remove, because seed() reads
/// `touched` and not the live map. Without it, unbinding a repo that was seeded once gets it seeded
/// again at the next startup, and the unbind looks like it did nothing. It is also what excludes one
/// repo from an owner rule.
pub fn forget(repo: &str) -> String {
    let r = key(repo);
    if r.is_empty() {
        return format!("{} is not an owner/name", repr(repo));
    }
    append(&[("forget", &r)])
}

/// Repos deliberately unbound while an owner rule would otherwise cover them.
///
/// ponytail: listed, or the exclusion is invisible, and "why is this repo not getting the team's
/// facts" would have no answer on any surface. A tombstone that nothing displays is the same silent
/// selection this whole store exists to remove.
pub fn excluded() -> Vec<String> {
    let e = entries();
    let mut out: Vec<String> = e
        .touched
        .iter()
        .filter(|r| !e.repos.contains_key(*r) && e.owners.contains_key(r.split('/').next().unwrap_or("")))
        .cloned()
        .collect();
    out.sort();
    out
}

/// Bind every repo under `owner` to team `to`. Returns "" or why it did not.
pub fn bind_owner(owner: &str, to: &str) -> String {
    let o = owner_key(owner);
    if o.is_empty() {
        return format!("{} is not an owner", repr(owner));
    }
    let to = team::key_of(to);
    if to.is_empty() {
        return "a binding needs a team".into();
    }
    if owners().get(&o) == Some(&to) {
        return String::new();
    }
    append(&[("owner", &o), ("team", &to)])
}

/// Drop the owner-wide rule. Returns "" or why it could not.
pub fn forget_owner(owner: &str) -> String {
    let o = owner_key(owner);
    if o.is_empty() {
        return format!("{} is not an owner", repr(owner));
    }
    append(&[("forget_owner", &o)])
}

/// What a coverage target names: ("owner", "acme") for "acme" or "acme/*", ("repo", "acme/api"), or ("", "").
pub fn target(s: &str) -> (String, String) {
    let o = owner_key(s);
    if !o.is_empty() {
        return ("owner".into(), o);
    }
    let r = key(s);
    if !r.is_empty() {
        return ("repo".into(), r);
    }
    (String::new(), String::new())
}

/// `s` as a team writes it in `covers`: "acme/*" for an owner, "acme/api" for a repo, "" for anything else.
pub fn cover_key(s: &str) -> String {
    let (kind, t) = target(s);
    match kind.as_str() {
        "owner" => format!("{}/*", t),
        "" => String::new(),
        _ => t,
    }
}

/// Which of `targets` this machine has neither bound nor unbound. Order preserved.
///
/// ponytail: the ONLY set worth putting to a person. Everything else a team claims has already been
/// answered here once (bound at join, bound by hand, or refused with a tombstone) and re-asking a
/// settled question is how a prompt becomes noise people dismiss without reading.
/// ponytail: bound to ANOTHER team counts as decided. It is not this team's to ask about again, and
/// asking would be offering to reroute a repo away from the team it is on.
pub fn undecided(targets: &[String]) -> Vec<String> {
    let e = entries();
    targets
        .iter()
        .filter(|t| {
            let (kind, v) = target(t);
            (kind == "owner" && !e.named.contains(&v)) || (kind == "repo" && !e.touched.contains(&v))
        })
        .cloned()
        .collect()
}

/// Bind every owner in `owners` that has never been bound or unbound, to `to`. Returns what it wrote.
///
/// ponytail: the owner-rule half of seed(), fed by what a team declares it covers. Same contract: a
/// tombstone, or a rule already there (to ANY team) is never re-pointed, so what a joiner changed by
/// hand stays changed. "acme" and "acme/*" are both accepted, as bind_owner accepts them.
pub fn seed_owners(to: &str, owners: &[String]) -> Vec<String> {
    if to.is_empty() {
        return Vec::new();
    }
    let mut named = entries().named;
    let mut wrote = Vec::new();
    for o in owners {
        let o = owner_key(o);
        if !o.is_empty() && !named.contains(&o) && append(&[("owner", &o), ("team", to)]).is_empty() {
            named.insert(o.clone());
            wrote.push(o);
        }
    }
    wrote
}

/// Bind every repo in `repos` that has never been bound or unbound. Returns what it wrote.
///
/// ponytail: this is the bootstrap, and it is why the covering rule can be dropped rather than kept
/// beside this one. Joining a team, or upgrading into a version that has bindings, would otherwise
/// leave every repo unbound, so the team's brief silently stops appearing in reviews that had it
/// yesterday. Seeding from the shared log reproduces exactly what the covering rule selected, ONCE,
/// into a store you can read and change.
/// ponytail: `touched`, not `bindings()`. A repo you deliberately unbound has a tombstone and no live
/// entry, and seeding off the live map would re-bind it on the next startup forever.
pub fn seed(to: &str, repos: &[String]) -> Vec<String> {
    if to.is_empty() {
        return Vec::new();
    }
    // ponytail: ONE read, reused for every repo. `of(r)` per repo re-opened ~/.prs_bindings once per
    // entry, and with the mirror registry folded in that is len(log) + len(registry) reads at startup,
    // inside the UI. pick against the entry we already hold answers the same question.
    let mut entry = entries();
    let mut wrote = Vec::new();
    for repo in repos {
        // ponytail: the owner rule too, so a repo it already covers is not given a redundant row of its
        // own. Writing one would pin it to whatever team the rule named at seed time, and removing the
        // rule later would leave rows nobody chose repo by repo.
        let r = key(repo);
        if !r.is_empty()
            && !entry.touched.contains(&r)
            && pick(&entry, &r).1.is_empty()
            && append(&[("repo", &r), ("team", to)]).is_empty()
        {
            entry.touched.insert(r.clone()); // ponytail: `repos` may name one repo twice; the file must not
            wrote.push(r);
        }
    }
    wrote
}

/// The memory directory of the team `slug` names, None when this machine has not joined it.
///
/// ponytail: THE seam. Every read and every write asks this one question, "which directory does this
/// slug mean", instead of reaching for a single config.TEAM, which is what turned many-teams from a
/// 36-site rewrite into a change of this function's body.
/// ponytail: folded on both sides. Repo keys are lowercased because they are typed; a team slug is
/// typed too: `--team Org/Mem` while in org/mem used to resolve to nothing and fall back to your own
/// brief, reporting "not in team Org/Mem" about a team you had joined. Compared rather than stored
/// folded, so a listing still shows the slug as GitHub spells it.
pub fn team_dir(slug: &str) -> Option<PathBuf> {
    if slug.is_empty() {
        return None;
    }
    let want = slug.to_lowercase();
    team::joined()
        .into_iter()
        .find(|s| s.to_lowercase() == want)
        .and_then(|s| team::dir_of(&s))
        .map(|d| d.join("memory"))
}

/// The slug a binding names when you do not say which team; "" when that is not a single answer.
///
/// ponytail: "" when you are in NONE and also when you are in SEVERAL: with more than one joined team
/// there is no default, and picking one would be the silent selection this whole store exists to
/// remove. The caller says so: `bind --team SLUG` names it, and the CLI refuses without one.
pub fn team_key() -> String {
    let got = team::joined();
    if got.len() == 1 {
        got[0].clone()
    } else {
        String::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, MutexGuard};

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    /// A fresh store under a tempdir, held for the test's lifetime.
    fn fresh() -> (MutexGuard<'static, ()>, tempfile::TempDir) {
        let g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.bindings = d.path().join("bindings"));
        (g, d)
    }

    fn v(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn one_repo_is_named_the_same_way_however_you_spell_it() {
        for spelling in [
            "acme/api",
            "git@github.com:acme/api.git",
            "https://github.com/acme/api",
            "https://github.com/acme/api.git",
            "/home/me/src/acme/api",
        ] {
            assert_eq!(key(spelling), "acme/api", "{spelling}");
        }
    }

    #[test]
    fn a_key_that_is_not_an_owner_and_a_name_is_refused() {
        let (_g, _d) = fresh();
        for bad in ["", "notes", "/", "a/", "/b"] {
            assert_eq!(key(bad), "", "{bad}");
        }
        assert_eq!(bind("notes", "org-t"), "'notes' is not an owner/name");
        assert!(bindings().is_empty());
    }

    #[test]
    fn a_trimmed_url_is_refused_rather_than_truncated() {
        let (_g, _d) = fresh();
        assert_eq!(key("acme/api/pull/19"), "");
        assert_eq!(key("acme/api/tree/main"), "");
        assert_eq!(
            bind("acme/api/pull/19", "org-t"),
            "'acme/api/pull/19' is not an owner/name"
        );
        assert!(bindings().is_empty());
        assert_eq!(key("https://github.com/acme/api/"), "acme/api");
        assert_eq!(key("git@github.com:acme/api.git"), "acme/api");
        assert_eq!(key("/home/me/src/acme/api"), "acme/api");
    }

    #[test]
    fn binding_and_unbinding_round_trip() {
        let (_g, _d) = fresh();
        assert_eq!(bind("acme/api", "org-t"), "");
        assert_eq!(of("acme/api"), "org-t");
        assert_eq!(of("git@github.com:acme/api.git"), "org-t");
        assert_eq!(of("acme/other"), "");
        assert_eq!(forget("acme/api"), "");
        assert_eq!(of("acme/api"), "");
        assert_eq!(forget("acme/api"), "");
    }

    #[test]
    fn the_last_line_wins_and_the_file_only_ever_grows() {
        let (_g, _d) = fresh();
        bind("acme/api", "org-one");
        bind("acme/api", "org-two");
        assert_eq!(of("acme/api"), "org-two");
        assert_eq!(bind("acme/api", "org-two"), "");
        let text = std::fs::read_to_string(store()).unwrap();
        assert_eq!(text.matches('\n').count(), 2);
        // byte-compatible with what the Python version wrote
        assert_eq!(
            text.lines().next().unwrap(),
            r#"{"repo": "acme/api", "team": "org-one"}"#
        );
    }

    #[test]
    fn one_unreadable_line_does_not_lose_the_rest() {
        let (_g, _d) = fresh();
        bind("acme/api", "org-t");
        let mut f = std::fs::OpenOptions::new().append(true).open(store()).unwrap();
        f.write_all(b"this is not json\n{}\n{\"repo\": 7}\n{\"repo\": \"acme/two\"}\n")
            .unwrap();
        bind("acme/three", "org-t");
        let want: HashMap<String, String> = [("acme/api", "org-t"), ("acme/three", "org-t")]
            .iter()
            .map(|(a, b)| (a.to_string(), b.to_string()))
            .collect();
        assert_eq!(bindings(), want);
    }

    #[test]
    fn seeding_binds_what_the_log_already_named() {
        let (_g, _d) = fresh();
        assert_eq!(
            seed("org-t", &v(&["acme/api", "acme/web", "acme/api"])),
            v(&["acme/api", "acme/web"])
        );
        assert_eq!(bindings().len(), 2);
        assert!(seed("org-t", &v(&["acme/api", "acme/web"])).is_empty());
        assert!(seed("", &v(&["acme/x"])).is_empty());
    }

    #[test]
    fn unbinding_something_never_bound_still_keeps_it_unbound() {
        let (_g, _d) = fresh();
        assert_eq!(forget("acme/api"), "");
        assert_eq!(of("acme/api"), "");
        assert_eq!(seed("org-t", &v(&["acme/api", "acme/web"])), v(&["acme/web"]));
        assert_eq!(of("acme/api"), "");
        seed("org-t", &v(&["acme/web"]));
        forget("acme/web");
        assert!(seed("org-t", &v(&["acme/web"])).is_empty());
    }

    #[test]
    fn a_hand_typed_repo_finds_the_row_a_review_looks_up() {
        let (_g, _d) = fresh();
        assert_eq!(bind("NeoMedSys/Neo-API", "org-t"), "");
        assert_eq!(of("neomedsys/neo-api"), "org-t");
        assert_eq!(forget("NEOMEDSYS/NEO-API"), "");
        assert_eq!(of("neomedsys/neo-api"), "");
    }

    #[test]
    fn an_unwritable_store_reports_instead_of_taking_the_dashboard_down() {
        let (_g, d) = fresh();
        std::fs::create_dir(d.path().join("as-a-dir")).unwrap();
        crate::config::update(|c| c.bindings = d.path().join("as-a-dir"));
        let err = bind("acme/api", "org-t");
        assert!(err.contains("Is a directory"), "{err}");
        assert!(seed("org-t", &v(&["acme/api"])).is_empty());
        assert!(bindings().is_empty() && of("acme/api").is_empty());
        assert!(!forget("acme/api").is_empty());
    }

    #[test]
    fn an_owner_rule_covers_every_repo_under_it_and_explicit_beats_it() {
        let (_g, _d) = fresh();
        assert_eq!(bind_owner("neomedsys", "org-mem"), "");
        for repo in [
            "neomedsys/neo-api",
            "neomedsys/nms-platform-v2",
            "neomedsys/a-repo-created-tomorrow",
        ] {
            assert_eq!(of(repo), "org-mem", "{repo}");
        }
        assert_eq!(owners().get("neomedsys").map(String::as_str), Some("org-mem"));
        assert!(bindings().is_empty());
        assert_eq!(of("someone-else/tool"), "");
        assert_eq!(
            why("neomedsys/neo-api"),
            ("owner".to_string(), "org-mem".to_string())
        );

        bind("neomedsys/joint-venture", "org-other");
        assert_eq!(of("neomedsys/joint-venture"), "org-other");
        assert_eq!(
            why("neomedsys/joint-venture"),
            ("team".to_string(), "org-other".to_string())
        );
        forget("neomedsys/someones-fork");
        assert_eq!(of("neomedsys/someones-fork"), "");
        assert_eq!(of("neomedsys/neo-api"), "org-mem");
        assert_eq!(excluded(), v(&["neomedsys/someones-fork"]));

        assert_eq!(forget_owner("neomedsys/*"), "");
        assert_eq!(of("neomedsys/neo-api"), "");
        assert!(owners().is_empty());
    }

    #[test]
    fn seeding_does_not_pin_repos_an_owner_rule_already_covers() {
        let (_g, _d) = fresh();
        bind_owner("neomedsys", "org-mem");
        assert_eq!(
            seed("org-mem", &v(&["neomedsys/neo-api", "other/thing"])),
            v(&["other/thing"])
        );
        assert_eq!(bindings().len(), 1);
        assert_eq!(bindings().get("other/thing").map(String::as_str), Some("org-mem"));
    }

    #[test]
    fn a_binding_stores_the_key_however_the_team_was_typed() {
        let (_g, _d) = fresh();
        assert_eq!(bind_owner("neomedsys", "NeoMedSys_team"), "");
        assert_eq!(
            owners().get("neomedsys").map(String::as_str),
            Some("neomedsys-team")
        );
        assert_eq!(bind("acme/api", "Acme Mem"), "");
        assert_eq!(of("acme/api"), "acme-mem");
        assert!(bind("acme/api", "///").contains("needs a team"));
    }

    #[test]
    fn a_coverage_target_is_an_owner_or_a_repo_and_nothing_else() {
        let s = |a: &str, b: &str| (a.to_string(), b.to_string());
        assert_eq!(target("acme"), s("owner", "acme"));
        assert_eq!(target("Acme/*"), s("owner", "acme"));
        assert_eq!(target("acme/api"), s("repo", "acme/api"));
        assert_eq!(target("acme/api/pull/1"), s("", ""));
        assert_eq!(target("*"), s("", ""));
        assert_eq!(target(""), s("", ""));
        assert_eq!(cover_key("acme"), "acme/*");
        assert_eq!(cover_key("acme/api"), "acme/api");
        assert_eq!(cover_key("*"), "");
    }

    #[test]
    fn seeding_owners_skips_what_was_ever_named() {
        let (_g, _d) = fresh();
        assert_eq!(seed_owners("org-t", &v(&["acme", "beta"])), v(&["acme", "beta"]));
        assert_eq!(owners().len(), 2);
        assert_eq!(forget_owner("acme"), "");
        assert_eq!(
            seed_owners("org-t", &v(&["acme", "beta", "gamma/*"])),
            v(&["gamma"])
        );
        assert_eq!(owners().get("gamma").map(String::as_str), Some("org-t"));
        assert!(!owners().contains_key("acme"));
        assert!(seed_owners("org-other", &v(&["beta"])).is_empty());
        assert!(seed_owners("", &v(&["zeta"])).is_empty());
    }

    #[test]
    fn undecided_is_what_a_team_claims_and_this_machine_has_not_answered() {
        let (_g, _d) = fresh();
        assert_eq!(undecided(&v(&["acme", "beta/tool"])), v(&["acme", "beta/tool"]));
        assert_eq!(bind_owner("acme", "org-t"), "");
        assert_eq!(undecided(&v(&["acme", "beta/tool"])), v(&["beta/tool"]));
        assert_eq!(forget("beta/tool"), "");
        assert!(undecided(&v(&["acme", "beta/tool"])).is_empty());
        assert_eq!(bind("gamma/x", "org-other"), "");
        assert!(undecided(&v(&["gamma/x"])).is_empty());
        assert!(undecided(&v(&["not-an-owner/a/b", ""])).is_empty());
    }

    #[test]
    fn a_resolver_answers_from_one_read() {
        let (_g, _d) = fresh();
        bind("acme/api", "org-t");
        let r = resolver();
        assert_eq!(r("acme/api"), "org-t");
        forget("acme/api");
        assert_eq!(r("acme/api"), "org-t"); // the frame it was built for, not the store as it is now
        assert_eq!(of("acme/api"), "");
    }

    #[test]
    fn team_dir_and_team_key_answer_nothing_when_no_team_is_joined() {
        let (_g, d) = fresh();
        crate::config::update(|c| c.teams = d.path().join("teams"));
        assert_eq!(team_dir(""), None);
        assert_eq!(team_dir("org-t"), None);
        assert_eq!(team_key(), "");
    }

    #[test]
    fn dumps_matches_python() {
        assert_eq!(dumps("a\"b\\c\n"), r#""a\"b\\c\n""#);
        assert_eq!(dumps("é"), r#""\u00e9""#);
    }
}
