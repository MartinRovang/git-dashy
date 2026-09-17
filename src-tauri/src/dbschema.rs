//! A DB repo's whole schema as a graph: tables, their columns and the foreign keys between them.
//!
//! No model: a shallow clone of the DB repo (~/.prs_dbschema/<owner>__<name>) and regexes over its .sql
//! files. The output has the shape of a review's `db` section, so the pane's DbGraph draws it as is.
//!
//! ponytail: files are read in path order and CREATE/ALTER/DROP applied as met, not in true migration
//! order. Good for a schema kept as one file per table; a repo of numbered migrations sorts right too.
//! Postgres-flavoured DDL only. ORM models (SQLAlchemy, Prisma) would need their own patterns.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

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
static ADD: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?i)\badd\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?("[^"]+"|\w+)\s+([^,;]*)"#));
static DROP: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?i)\bdrop\s+(?:column\s+)?(?:if\s+exists\s+)?("[^"]+"|\w+)"#));
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

fn name(s: &str) -> String {
    s.replace('"', "").to_lowercase()
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
            let stop = sql[end..].find(';').map_or(sql.len(), |i| end + i);
            let body = &sql[end..stop];
            from = stop;
            let t = tables.entry(tname).or_default();
            for c in DROP.captures_iter(body) {
                let col = name(&c[1]);
                if !NOT_COLUMN.contains(&col.as_str()) {
                    t.columns.retain(|c| c.name != col);
                }
            }
            for c in ADD.captures_iter(body) {
                column(t, &c[1], &c[2]);
            }
            refs(t, body);
            keys(t, body);
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

fn sql_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let p = e.path();
        if p.file_name().is_some_and(|n| n == ".git") {
            continue;
        }
        if p.is_dir() {
            sql_files(&p, out);
        } else if p.extension().is_some_and(|x| x.eq_ignore_ascii_case("sql")) {
            out.push(p);
        }
    }
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
    let d = dir.to_string_lossy().to_string();
    let auth = github::git_auth();
    let url = format!("{}{k}.git", github::GITHUB);
    let steps: Vec<Vec<&str>> = if dir.join(".git").is_dir() {
        vec![
            vec!["git", "-C", &d, "fetch", "--depth", "1", "origin"],
            vec!["git", "-C", &d, "reset", "--hard", "FETCH_HEAD"],
        ]
    } else {
        let _ = std::fs::remove_dir_all(&dir);
        vec![vec!["git", "clone", "--depth", "1", &url, &d]]
    };
    for s in steps {
        let o = team::remote(&s, Some(&auth), Some(team::CLONE_TIMEOUT));
        if !o.ok() {
            return Err(format!("{k}: {}", o.last_line()));
        }
    }
    let mut paths = Vec::new();
    sql_files(&dir, &mut paths);
    paths.sort();
    let files: Vec<String> = paths
        .iter()
        .filter_map(|p| std::fs::read_to_string(p).ok())
        .collect();
    Ok(parse(&files))
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
}
