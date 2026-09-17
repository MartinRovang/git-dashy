//! A DB repo's whole schema as a graph: tables, their columns and the foreign keys between them.
//!
//! No model: a shallow clone of the DB repo (~/.prs_dbschema/<owner>__<name>) and regexes over its .sql
//! files. The output has the shape of a review's `db` section, so the pane's DbGraph draws it as is.
//!
//! ponytail: files are read in natural path order (`V2__` before `V10__`) and CREATE/ALTER/DROP applied as
//! met, not by a migration tool's own history. Right for a schema kept as one file per table and for numbered
//! migrations; a repo that orders them some other way (a manifest, timestamps in a table) is read out of order.
//! Postgres-flavoured DDL only: ORM models (SQLAlchemy, Prisma) would need their own patterns. A reference
//! to a table renamed after it was written still names the old table.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

use regex::Regex;
use serde_json::{json, Value};

use crate::{bind, github, team};

#[derive(Default)]
struct Table {
    columns: Vec<Col>,
    refs: Vec<String>,
}

#[derive(Default)]
struct Col {
    name: String,
    ty: String,
    pk: bool,
    /// the table this column's foreign key points at, as written
    fk: String,
}

fn re(p: &str) -> Regex {
    Regex::new(p).expect("static regex")
}
static COMMENT: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)/\*.*?\*/|--[^\n]*"));
static STMT: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r#"(?i)\b(create\s+(?:(?:global|local|unlogged|temp|temporary)\s+)*table|alter\s+table|drop\s+table)\s+(?:if\s+(?:not\s+)?exists\s+)?(?:only\s+)?([\w."]+)"#,
    )
});
// a table-level `PRIMARY KEY (a, b)` or `FOREIGN KEY (a) REFERENCES t`
static KEYS: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?i)\b(primary|foreign)\s+key\s*\(([^)]*)\)(?:\s*references\s+([\w."]+))?"#));
static PK: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bprimary\s+key\b"));
static REFS: LazyLock<Regex> = LazyLock::new(|| re(r#"(?i)\breferences\s+([\w."]+)"#));
// one action of an ALTER TABLE, split at its top-level commas so `numeric(10,2)` stays whole
static ADD: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?is)^\s*add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?("[^"]+"|\w+)\s+(.*)$"#));
static DROP: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?i)^\s*drop\s+(?:column\s+)?(?:if\s+exists\s+)?("[^"]+"|\w+)"#));
static RENAME: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?i)^\s*rename\s+(?:(?:column\s+)?("[^"]+"|\w+)\s+)?to\s+([\w."]+)"#));
// where a column's type ends and its constraints start
static TAIL: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)\s+(?:not\b|null\b|default\b|primary\b|references\b|unique\b|check\b|constraint\b|generated\b|collate\b)",
    )
});
const NOT_COLUMN: &[&str] = &[
    "constraint",
    "primary",
    "foreign",
    "unique",
    "check",
    "exclude",
    "like",
];

/// A table or column name as one key: unquoted, lower case, and `public.users` is `users`, Postgres's default schema
/// (the graph reads an unqualified table as `public` too).
fn name(s: &str) -> String {
    let n = s.replace('"', "").to_lowercase();
    n.strip_prefix("public.").map(str::to_string).unwrap_or(n)
}

/// `text[at..]` up to the paren that closes the one just before `at`, and where that ends.
fn inside(text: &str, at: usize) -> (&str, usize) {
    let mut depth = 1;
    for (i, c) in text[at..].char_indices() {
        match c {
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth == 0 {
                    return (&text[at..at + i], at + i + 1);
                }
            }
            _ => {}
        }
    }
    (&text[at..], text.len())
}

/// Split at commas outside parens: `numeric(10, 2)` stays one piece.
fn pieces(body: &str) -> Vec<&str> {
    let (mut out, mut depth, mut from) = (Vec::new(), 0, 0);
    for (i, c) in body.char_indices() {
        match c {
            '(' => depth += 1,
            ')' => depth -= 1,
            ',' if depth == 0 => {
                out.push(&body[from..i]);
                from = i + 1;
            }
            _ => {}
        }
    }
    out.push(&body[from..]);
    out
}

fn column(t: &mut Table, col: &str, rest: &str) {
    let col = name(col);
    if col.is_empty() || NOT_COLUMN.contains(&col.as_str()) {
        return;
    }
    let ty = TAIL
        .split(rest.trim())
        .next()
        .unwrap_or("")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    t.columns.retain(|c| c.name != col);
    t.columns.push(Col {
        name: col,
        ty: ty.to_lowercase(),
        pk: PK.is_match(rest),
        fk: REFS.captures(rest).map(|c| name(&c[1])).unwrap_or_default(),
    });
}

/// Mark the columns table-level PRIMARY KEY and FOREIGN KEY clauses in `text` name.
fn keys(t: &mut Table, text: &str) {
    for k in KEYS.captures_iter(text) {
        let fk = k.get(3).map(|m| name(m.as_str()));
        for c in k[2].split(',').map(name) {
            if let Some(col) = t.columns.iter_mut().find(|x| x.name == c.trim()) {
                match &fk {
                    Some(f) => col.fk = f.clone(),
                    None if k[1].eq_ignore_ascii_case("primary") => col.pk = true,
                    None => {}
                }
            }
        }
    }
}

fn refs(t: &mut Table, text: &str) {
    t.refs.extend(REFS.captures_iter(text).map(|c| name(&c[1])));
}

fn apply(tables: &mut BTreeMap<String, Table>, sql: &str) {
    let sql = COMMENT.replace_all(sql, " ");
    let mut from = 0;
    while let Some(m) = STMT.captures_at(&sql, from) {
        let (verb, tname) = (m[1].to_lowercase(), name(&m[2]));
        let end = m.get(0).unwrap().end();
        from = end;
        if verb.starts_with("drop") {
            tables.remove(&tname);
        } else if verb.starts_with("create") {
            let t = tables.entry(tname).or_default();
            let Some(open) = sql[end..]
                .find(|c: char| !c.is_whitespace())
                .filter(|&i| sql[end + i..].starts_with('('))
            else {
                continue; // CREATE TABLE x AS SELECT / PARTITION OF: no column list to read
            };
            let (body, close) = inside(&sql, end + open + 1);
            from = close;
            for p in pieces(body) {
                let p = p.trim();
                // the first word ends at a space or a paren: `UNIQUE(x)` is a constraint, not a column
                let cut = p.find(|c: char| c.is_whitespace() || c == '(').unwrap_or(p.len());
                column(t, &p[..cut], &p[cut..]);
                refs(t, p);
            }
            keys(t, body);
        } else {
            // the body ends at its `;`, or where the next statement starts when a file leaves the `;` out
            let semi = sql[end..].find(';').map_or(sql.len(), |i| end + i);
            let next = STMT.find_at(&sql, end).map_or(sql.len(), |n| n.start());
            let stop = semi.min(next);
            let body = &sql[end..stop];
            from = stop;
            let t = tables.entry(tname.clone()).or_default();
            let mut moved = None;
            for p in pieces(body) {
                if let Some(c) = ADD.captures(p) {
                    column(t, &c[1], &c[2]);
                } else if let Some(c) = RENAME.captures(p) {
                    match c.get(1) {
                        Some(old) => {
                            let (old, new) = (name(old.as_str()), name(&c[2]));
                            if let Some(col) = t.columns.iter_mut().find(|x| x.name == old) {
                                col.name = new;
                            }
                        }
                        // RENAME TO keeps the schema: `restricted.users RENAME TO people` is `restricted.people`
                        None => {
                            let new = name(&c[2]);
                            moved = Some(match tname.rsplit_once('.') {
                                Some((schema, _)) if !new.contains('.') => format!("{schema}.{new}"),
                                _ => new,
                            });
                        }
                    }
                } else if let Some(c) = DROP.captures(p) {
                    let col = name(&c[1]);
                    if !NOT_COLUMN.contains(&col.as_str()) {
                        t.columns.retain(|c| c.name != col);
                    }
                }
            }
            refs(t, body);
            keys(t, body);
            if let Some(new) = moved {
                let t = tables.remove(&tname).unwrap_or_default();
                tables.insert(new, t);
            }
        }
    }
}

/// `files` as a review's db section: every table, `change` empty, columns with their types as notes.
pub fn parse(files: &[String]) -> Value {
    let mut tables = BTreeMap::new();
    for f in files {
        apply(&mut tables, f);
    }
    // a reference names `users` where the table is `auth.users`, or the other way round: match the last part
    let last = |s: &str| s.rsplit('.').next().unwrap_or(s).to_string();
    let resolve = |r: &str| {
        if tables.contains_key(r) {
            return Some(r.to_string());
        }
        tables.keys().find(|k| last(k) == last(r)).cloned()
    };
    let out: Vec<Value> = tables
        .iter()
        .map(|(n, t)| {
            let mut rs: Vec<String> = t
                .refs
                .iter()
                .filter_map(|r| resolve(r))
                .filter(|r| r != n)
                .collect();
            rs.sort();
            rs.dedup();
            json!({
                "name": n,
                "change": "",
                "refs": rs,
                // key: "fk" beats "pk", a junction table's key columns are worth more as the links they are
                "columns": t.columns.iter().map(|c| json!({
                    "name": c.name,
                    "change": "",
                    "note": c.ty,
                    "key": if !c.fk.is_empty() { "fk" } else if c.pk { "pk" } else { "" },
                    "ref": if c.fk.is_empty() { None } else { resolve(&c.fk) }.unwrap_or_default(),
                })).collect::<Vec<_>>(),
            })
        })
        .collect();
    json!({"tables": out, "risks": []})
}

/// A .sql file bigger than this is a data dump or a seed, not a schema: skipped.
const MAX_SQL: u64 = 2 * 1024 * 1024;

/// Every .sql file under `dir`, skipping .git, anything too big, and every symlink.
///
/// ponytail: the DB repo is anyone-who-can-push input. A symlink is never followed: `loop -> .` would recurse
/// until the stack overflows and aborts the app, and `x.sql -> ~/.ssh/...` would read outside the clone.
/// DirEntry's file type and metadata describe the link itself, never its target.
fn sql_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let Ok(ft) = e.file_type() else { continue };
        let p = e.path();
        if ft.is_symlink() || p.file_name().is_some_and(|n| n == ".git") {
            continue;
        }
        if ft.is_dir() {
            sql_files(&p, out);
        } else if ft.is_file()
            && p.extension().is_some_and(|x| x.eq_ignore_ascii_case("sql"))
            && e.metadata().is_ok_and(|m| m.len() <= MAX_SQL)
        {
            out.push(p);
        }
    }
}

/// ponytail: one lock for every DB repo. Two clicks, or two tabs, would otherwise clone or reset the same
/// directory at once and one would delete the other's checkout half-way. Per-repo locks if several DB repos
/// ever need reading at the same moment.
static CHECKOUT: Mutex<()> = Mutex::new(());

/// Runs of digits compare as numbers, the rest as text: `V2__a.sql` sorts before `V10__b.sql`.
fn natural(s: &str) -> Vec<(u8, u128, String)> {
    static RUNS: LazyLock<Regex> = LazyLock::new(|| re(r"\d+|\D+"));
    RUNS.find_iter(s)
        .map(|m| match m.as_str().parse::<u128>() {
            Ok(n) => (0, n, String::new()),
            Err(_) => (1, 0, m.as_str().to_string()),
        })
        .collect()
}

/// Every .sql file under `dir`, in natural path order, parsed.
fn read(dir: &Path) -> Value {
    let mut paths = Vec::new();
    sql_files(dir, &mut paths);
    paths.sort_by_cached_key(|p| natural(&p.to_string_lossy()));
    let files: Vec<String> = paths
        .iter()
        .filter_map(|p| std::fs::read_to_string(p).ok())
        .collect();
    parse(&files)
}

/// A shallow checkout of `url` at `dir`, fresh: fetched and reset when it is there, cloned when it is not, and
/// cloned again when a fetch fails on a checkout that is corrupt or was cut off half-way.
fn checkout(url: &str, dir: &Path) -> Result<(), String> {
    let d = dir.to_string_lossy().to_string();
    let auth = github::git_auth();
    let run = |cmd: &[&str]| {
        let o = team::remote(cmd, Some(&auth), Some(team::CLONE_TIMEOUT));
        if o.ok() {
            Ok(())
        } else {
            Err(o.last_line())
        }
    };
    if dir.join(".git").is_dir()
        && run(&["git", "-C", &d, "fetch", "--depth", "1", "origin"]).is_ok()
        && run(&["git", "-C", &d, "reset", "--hard", "FETCH_HEAD"]).is_ok()
    {
        return Ok(());
    }
    let _ = std::fs::remove_dir_all(dir);
    run(&["git", "clone", "--depth", "1", url, &d])
}

/// Clone or refresh `db` (owner/name), then parse it. Err says why not.
pub fn get(db: &str) -> Result<Value, String> {
    let k = bind::key(db);
    if k.is_empty() {
        return Err(format!("{db} is not an owner/name"));
    }
    let dir = crate::config::home()
        .join(".prs_dbschema")
        .join(k.replace('/', "__"));
    let _held = CHECKOUT.lock().unwrap_or_else(|e| e.into_inner());
    checkout(&format!("{}{k}.git", github::GITHUB), &dir).map_err(|e| format!("{k}: {e}"))?;
    Ok(read(&dir))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tables_columns_and_foreign_keys() {
        let v = parse(&[
            r#"/* users, (with a paren) */
CREATE TABLE IF NOT EXISTS restricted.users (
    user_uid TEXT NOT NULL, -- the id, (really)
    "Score" NUMERIC(10, 2) DEFAULT 0,
    FOREIGN KEY (user_uid) REFERENCES restricted.user_meta_table (user_uid),
    UNIQUE(user_uid)
)
CREATE TABLE restricted.user_meta_table (user_uid TEXT PRIMARY KEY, gone INT);
create table analytics (id serial, owner text references users(user_uid));
CREATE TABLE copy AS SELECT * FROM analytics;"#
                .to_string(),
            "ALTER TABLE restricted.user_meta_table ADD COLUMN created timestamp with time zone NOT NULL, DROP COLUMN gone;
             ALTER TABLE analytics ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES missing (id);
             CREATE TABLE tmp (x int); DROP TABLE IF EXISTS tmp;"
                .to_string(),
        ]);
        let t = |n: &str| {
            v["tables"]
                .as_array()
                .unwrap()
                .iter()
                .find(|t| t["name"] == n)
                .cloned()
        };
        let users = t("restricted.users").unwrap();
        assert_eq!(users["refs"], json!(["restricted.user_meta_table"]));
        assert_eq!(
            users["columns"],
            json!([
                {"name": "user_uid", "change": "", "note": "text", "key": "fk", "ref": "restricted.user_meta_table"},
                {"name": "score", "change": "", "note": "numeric(10, 2)", "key": "", "ref": ""},
            ])
        );
        let meta = t("restricted.user_meta_table").unwrap();
        let cols: Vec<_> = meta["columns"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c["name"].as_str().unwrap())
            .collect();
        assert_eq!(cols, ["user_uid", "created"]);
        assert_eq!(meta["columns"][1]["note"], "timestamp with time zone");
        assert_eq!(meta["columns"][0]["key"], "pk");
        assert_eq!(t("analytics").unwrap()["columns"][1]["ref"], "restricted.users");
        // an unqualified reference finds the qualified table; one to a table nowhere in the repo is dropped
        assert_eq!(t("analytics").unwrap()["refs"], json!(["restricted.users"]));
        assert!(t("tmp").is_none());
        assert!(t("copy").is_some_and(|c| c["columns"] == json!([])));
    }

    #[test]
    fn alter_actions_split_at_top_level_commas_and_renames_follow() {
        let v = parse(&[
            // no `;` after the first ALTER: the CREATE after it is still read
            "CREATE TABLE auth.users (id int, nick text);
             ALTER TABLE auth.users ADD COLUMN amount numeric(10,2) NOT NULL, RENAME COLUMN nick TO handle, ALTER COLUMN id DROP DEFAULT
             CREATE TABLE later (x int);
             ALTER TABLE auth.users RENAME TO people;
             ALTER TABLE auth.people ADD COLUMN email text;"
                .to_string(),
        ]);
        let names: Vec<_> = v["tables"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        assert_eq!(names, ["auth.people", "later"]);
        let cols: Vec<_> = v["tables"][0]["columns"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| format!("{} {}", c["name"].as_str().unwrap(), c["note"].as_str().unwrap()))
            .collect();
        assert_eq!(
            cols,
            ["id int", "handle text", "amount numeric(10,2)", "email text"]
        );
    }

    #[cfg(unix)]
    #[test]
    fn sql_files_never_follow_a_symlink() {
        let d = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        std::fs::write(outside.path().join("secret.sql"), "CREATE TABLE secret (x int);").unwrap();
        std::fs::create_dir_all(d.path().join("schema")).unwrap();
        std::fs::create_dir_all(d.path().join(".git")).unwrap();
        std::fs::write(d.path().join("schema/users.sql"), "CREATE TABLE users (x int);").unwrap();
        std::fs::write(d.path().join(".git/hidden.sql"), "").unwrap();
        std::fs::write(d.path().join("dump.sql"), vec![b' '; MAX_SQL as usize + 1]).unwrap();
        std::os::unix::fs::symlink(".", d.path().join("schema/loop")).unwrap();
        std::os::unix::fs::symlink(outside.path().join("secret.sql"), d.path().join("leak.sql")).unwrap();
        std::os::unix::fs::symlink(outside.path(), d.path().join("away")).unwrap();
        let mut found = Vec::new();
        sql_files(d.path(), &mut found);
        assert_eq!(found, [d.path().join("schema/users.sql")]);
    }

    #[test]
    fn public_is_the_default_schema() {
        let v = parse(&["CREATE TABLE users (id int);
             ALTER TABLE public.users ADD COLUMN email text;
             CREATE TABLE auth.users (id int);
             CREATE TABLE posts (author int REFERENCES public.users (id));"
            .to_string()]);
        let t = |n: &str| {
            v["tables"]
                .as_array()
                .unwrap()
                .iter()
                .find(|t| t["name"] == n)
                .cloned()
                .unwrap()
        };
        assert_eq!(v["tables"].as_array().unwrap().len(), 3);
        assert_eq!(t("users")["columns"].as_array().unwrap().len(), 2);
        // exact beats the last-part match, which would have picked auth.users (it sorts first)
        assert_eq!(t("posts")["refs"], json!(["users"]));
    }

    #[test]
    fn migrations_apply_in_natural_order() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(
            d.path().join("V2__create.sql"),
            "CREATE TABLE gone (x int); CREATE TABLE kept (x int);",
        )
        .unwrap();
        std::fs::write(d.path().join("V10__drop.sql"), "DROP TABLE gone;").unwrap();
        assert_eq!(
            read(d.path())["tables"],
            json!([{"name": "kept", "change": "", "refs": [], "columns": [{"name": "x", "change": "", "note": "int", "key": "", "ref": ""}]}])
        );
    }

    /// The whole allowed path against a local repo: a clone, a fetch that sees a new commit, and a broken checkout
    /// cloned again rather than failing every click after it.
    #[test]
    fn checkout_clones_refreshes_and_recovers() {
        let git = |dir: &Path, args: &[&str]| {
            let o = std::process::Command::new("git")
                .args(["-c", "user.name=t", "-c", "user.email=t@t", "-C"])
                .arg(dir)
                .args(args)
                .output()
                .unwrap();
            assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
        };
        let origin = tempfile::tempdir().unwrap();
        git(origin.path(), &["init", "-q"]);
        std::fs::write(origin.path().join("a.sql"), "CREATE TABLE a (x int);").unwrap();
        git(origin.path(), &["add", "."]);
        git(origin.path(), &["commit", "-qm", "a"]);
        let url = format!("file://{}", origin.path().display());
        let home = tempfile::tempdir().unwrap();
        let dir = home.path().join("acme__schema");
        let names = || {
            read(&dir)["tables"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| t["name"].as_str().unwrap().to_string())
                .collect::<Vec<_>>()
        };

        assert_eq!(checkout(&url, &dir), Ok(()));
        assert_eq!(names(), ["a"]);
        std::fs::write(origin.path().join("b.sql"), "CREATE TABLE b (x int);").unwrap();
        git(origin.path(), &["add", "."]);
        git(origin.path(), &["commit", "-qm", "b"]);
        assert_eq!(checkout(&url, &dir), Ok(()));
        assert_eq!(names(), ["a", "b"]);
        std::fs::write(dir.join(".git/HEAD"), "garbage").unwrap();
        assert_eq!(checkout(&url, &dir), Ok(()));
        assert_eq!(names(), ["a", "b"]);
        assert!(checkout("file:///nowhere/at/all", &home.path().join("x")).is_err());
    }
}
