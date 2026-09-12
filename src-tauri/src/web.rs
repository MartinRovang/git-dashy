//! The HTTP server the React frontend talks to. Port of dashy/ui/web.py. Serves the embedded
//! Vite bundle, /api/* JSON, and guards the API with the X-Dashy-Token header (or ?token= on the page).
//!
//! ponytail: tiny_http, no framework. One State, one refresh thread, and one JSON route per thing
//! the dashboard can do: the page polls /api/state and posts back. The desktop window is pointed
//! at this same URL, so there is one UI to maintain and it works in a browser too.

use std::collections::HashMap;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use include_dir::{include_dir, Dir};
use log::{debug, error};
use serde_json::{json, Map, Value};
use tiny_http::{Header, Method, Request, Response, Server};

use crate::state::{last_line, now, State};
use crate::types::{DiffFile, Finding, LogEntry, Mark, Pr, Verdict};
use crate::{
    bind, config, diff, github, install, knowledge, log as review_log, memory, review, team, textdiff, update,
};

/// The built Vite app, embedded so the binary stays self-contained. `pnpm build` must run before cargo.
static DIST: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/../dist");
/// The page carries the palettes; this is what the picker offers.
pub const THEMES: &[&str] = &["pencil", "dashy", "dracula", "gruvbox", "nord"];

/// An error the page should read: (status, message).
#[derive(Debug)]
pub struct Fail(pub u16, pub String);

impl Fail {
    fn new(code: u16, msg: impl Into<String>) -> Fail {
        Fail(code, msg.into())
    }
}

/// Anything else is a 500 with its last line, so a broken action is a flash on the page, not a dead poll.
impl From<anyhow::Error> for Fail {
    fn from(e: anyhow::Error) -> Fail {
        Fail(500, last_line(&format!("{e:#}"), 160, "error"))
    }
}

impl From<std::io::Error> for Fail {
    fn from(e: std::io::Error) -> Fail {
        Fail(500, last_line(&e.to_string(), 160, "error"))
    }
}

type Out = Result<Value, Fail>;
/// The query string, first value per key.
type Query = HashMap<String, String>;

/// Everything the settings can change, in the shape config::save writes.
pub fn snapshot() -> Value {
    serde_json::to_value(config::snapshot(&config::get())).unwrap_or_default()
}

fn team_error() -> String {
    team::ERROR.lock().map(|g| g.clone()).unwrap_or_default()
}

/// Everything one frame of the GUI needs, as plain JSON.
pub fn payload(state: &State) -> Value {
    let (
        sections,
        reviews,
        busy,
        since,
        error,
        arrived,
        fetched_at,
        fetching,
        auto,
        pending,
        update,
        asks,
        notices,
    ) = {
        let inner = state.lock();
        (
            inner.sections.clone(),
            inner.reviews.clone(),
            inner.running.clone(),
            inner.since.clone(),
            inner.error.clone(),
            inner.arrived.clone(),
            inner.fetched_at,
            inner.fetching,
            inner.auto,
            inner.pending_rr().len(),
            inner.update.clone(),
            inner.asks.clone(),
            inner.notices.clone(),
        )
    };
    let cfg = config::get();
    let resolve = bind::resolver(); // ponytail: ONE resolver per frame, like the curses screen used to
    let summaries: HashMap<&str, &str> = sections
        .iter()
        .filter(|s| s.name == "REVIEWED")
        .flat_map(|s| s.prs.iter().flatten())
        .filter_map(|p| p.review.as_ref().map(|r| (p.url.as_str(), r.summary.as_str())))
        .collect();
    let mut out = Vec::new();
    for s in &sections {
        let mut rows = Vec::new();
        for p in s.prs.iter().flatten() {
            let url = p.url.as_str();
            let pre = if s.name == "MINE" {
                review::self_review_state(p)
            } else {
                (0.0, false)
            };
            let (summary, review_at) = match (&s.name[..], &p.review) {
                ("REVIEWED", Some(r)) => (r.summary.as_str(), r.at.as_str()),
                ("REVIEWED", None) => ("", ""),
                _ => (summaries.get(url).copied().unwrap_or(""), ""),
            };
            rows.push(json!({
                "url": url,
                "number": p.number,
                "title": p.title,
                "repo": p.repo(),
                "author": p.author(),
                "updatedAt": p.updated_at,
                "isDraft": p.is_draft,
                "status": p.status,
                "prev": p.prev,
                "checks": p.checks,
                "reviewers": p.reviewers,
                "review": reviews.get(url).cloned().unwrap_or_default(),
                "busy": busy.contains(url),
                "since": since.get(url),
                "team": resolve(p.repo()),
                "summary": summary,
                "reviewAt": review_at,
                "pre": pre_json(pre),
            }));
        }
        out.push(json!({"name": s.name, "prs": rows, "error": s.err.clone().unwrap_or_default()}));
    }
    let names = team::joined();
    json!({
        "version": config::VERSION,
        "sections": out,
        "fetchedAt": fetched_at,
        "interval": cfg.interval,
        "fetching": fetching,
        "error": error,
        "auto": auto,
        "pending": pending,
        "model": cfg.model,
        "running": busy.len(),
        "update": update,
        "settings": snapshot(),
        "options": {"model": cfg.models, "depth": config::DEPTHS, "effort": config::EFFORTS, "voice": config::VOICES,
                    "hunter": config::HUNTERS, "subs": config::SUBS, "window": config::WINDOWS,
                    "interval": config::INTERVALS, "theme": THEMES},
        "knowledge": {
            "memory": knowledge::show(&knowledge::effective()) + &knowledge::history_note(),
            "store": if knowledge::store_moved() { knowledge::show(&cfg.teams) } else { String::new() },
            "teams": names.iter().map(|k| json!({"key": k, "name": team::info(k).name, "arrived": arrived.get(k).copied().unwrap_or(0)})).collect::<Vec<_>>(),
            "teamError": team_error(),
            "notes": install::session_notes(),
            // ponytail: a row you can act on, not a note you cannot. Both gates are asked once at
            // launch and never again, so a team joined since you started, an agents.md a teammate
            // pushed an hour ago, and an answer given by accident all left something withheld with the
            // only route back being a restart, or hand-editing a dotfile for a refusal.
            "waiting": memory::pending_answers().into_iter()
                .map(|(kind, key, what)| json!({"kind": kind, "key": key, "what": what}))
                .collect::<Vec<_>>(),
        },
        "asks": asks,
        "notices": notices,
    })
}

fn pre_json((at, moved): (f64, bool)) -> Value {
    if at != 0.0 {
        json!({"at": at, "moved": moved})
    } else {
        Value::Null
    }
}

/// The newest review of this PR: the row's own on a REVIEWED row, else the log's.
fn last_review(pr: &Pr) -> Option<LogEntry> {
    pr.review
        .as_deref()
        .cloned()
        .or_else(|| review_log::last(&pr.url))
}

/// The checked findings of a log entry, through the same filter a verdict gets.
fn entry_findings(rev: &LogEntry) -> Vec<Finding> {
    let v = Verdict {
        verdict: rev.verdict.clone(),
        findings: rev
            .findings
            .iter()
            .map(|f| serde_json::to_value(f).unwrap_or_default())
            .collect(),
        ..Default::default()
    };
    review_log::findings(&v)
}

/// The side pane's frame for one PR: its size, its CI checks, the brief and the last review of it.
///
/// ponytail: the same want_detail the curses pane used, so a row costs one GitHub query per revision.
/// It answers pending while that query is in flight; the page keeps polling and the pane fills in.
pub fn detail(state: &State, pr: &Pr, section: &str) -> Value {
    let d = state.want_detail(pr);
    let rev = last_review(pr);
    let repo = pr.repo();
    let (text, whose) = memory::brief(Some(repo), Some(&bind::of(repo)));
    let pre = if section == "MINE" {
        review::self_review_state(pr)
    } else {
        (0.0, false)
    };
    json!({
        "url": pr.url,
        "pending": d.is_none(),
        "branch": d.as_ref().map(|d| d.branch.clone()).unwrap_or_default(),
        "add": d.as_ref().and_then(|d| d.add),
        "del": d.as_ref().and_then(|d| d.del),
        "files": d.as_ref().and_then(|d| d.files),
        "checks": d.as_ref().map(|d| d.checks.clone()).unwrap_or_default(),
        "brief": {"whose": whose, "empty": text.is_empty()},
        "pre": pre_json(pre),
        "review": rev.as_ref().map(|rev| json!({
            "verdict": config::status(&rev.verdict).unwrap_or(""),
            "summary": rev.summary,
            "model": rev.model,
            "tag": review_log::tag(rev),
            "at": rev.at,
            "findings": entry_findings(rev),
            "text": if rev.pr.url.is_empty() { rev.body.clone() } else { review_log::detail(rev) },
        })),
    })
}

/// A mark landed on this line: the same file, the new-file line it names, and not a removed line.
fn on_line(m: &Mark, file: usize, line: &crate::types::Line) -> bool {
    m.file == file && m.n != 0 && line.n == Some(m.n) && line.del.is_none()
}

/// The code tab as a flat list of rows, so the page can window it and jump between marks.
///
/// ponytail: ported from the curses pane as-is. Every mark gets a row: on its line when the diff has
/// that line, as an orphan when it does not; a finding is kept only if it can be read.
pub fn code_rows(files: &[DiffFile], marks: &[Mark], scoped: bool) -> Vec<Value> {
    let mut rows = Vec::new();
    let mut landed = vec![false; marks.len()];
    for (fi, f) in files.iter().enumerate() {
        rows.push(json!({"kind": "file", "path": f.path, "add": f.add, "dele": f.dele}));
        for hunk in &f.hunks {
            rows.push(json!({"kind": "hunk", "header": hunk.header}));
            for l in &hunk.lines {
                rows.push(
                    json!({"kind": "line", "n": l.n, "sign": l.sign, "text": l.text, "del": l.del,
                                 "mark": diff::worst(l)}),
                );
                if !scoped {
                    continue;
                }
                for (mi, m) in marks.iter().enumerate() {
                    if on_line(m, fi, l) {
                        rows.push(json!({"kind": "note", "mark": m.kind, "text": m.text}));
                        landed[mi] = true;
                    }
                }
            }
        }
        rows.push(json!({"kind": "gap"}));
    }
    if scoped {
        for (mi, m) in marks.iter().enumerate() {
            if landed[mi] {
                continue;
            }
            // ponytail: `file` past the list is how a mark says the diff does not touch that file
            let why = if m.file >= files.len() {
                "not in this diff"
            } else {
                "line not in this diff"
            };
            rows.push(json!({"kind": "orphan", "mark": m.kind, "text": m.text, "loc": m.loc, "why": why}));
        }
    }
    rows
}

/// The review against the code it is about. pending until the diff has been read.
pub fn code(state: &State, pr: &Pr, scope: &str, context: usize) -> Value {
    let Some(rev) = last_review(pr) else {
        return json!({"url": pr.url, "pending": false, "rows": [],
                      "empty": "no review yet — r reviews this PR, p pre-reviews it"});
    };
    let Some((files, marks)) = state.want_diff(pr.repo(), pr.number, &pr.head, &entry_findings(&rev)) else {
        return json!({"url": pr.url, "pending": true, "rows": []});
    };
    if files.is_empty() {
        return json!({"url": pr.url, "pending": false, "rows": [],
                      "empty": "no diff to show — GitHub could not read it, or nothing changed"});
    }
    let scoped = scope == "marks";
    let rows = if scoped {
        code_rows(&diff::narrow(&files, context), &marks, true)
    } else {
        code_rows(&files, &marks, false)
    };
    if scoped && rows.is_empty() {
        return json!({"url": pr.url, "pending": false, "rows": [],
                      "empty": "the review marked nothing — D shows the whole diff"});
    }
    json!({"url": pr.url, "pending": false, "rows": rows})
}

/// The PR on the board with this url, and the section it is in.
pub fn find_pr(state: &State, url: &str) -> Option<(Pr, String)> {
    let inner = state.lock();
    inner.sections.iter().find_map(|s| {
        s.prs
            .iter()
            .flatten()
            .find(|p| p.url == url)
            .map(|p| (p.clone(), s.name.clone()))
    })
}

fn need_pr(state: &State, url: &str) -> Result<(Pr, String), Fail> {
    find_pr(state, url).ok_or_else(|| Fail::new(404, "no such pr"))
}

// ---------------------------------------------------------------- background jobs (dream, scan)

/// One named job: when it started, whether its thread is still going, and what it said.
#[derive(Default)]
struct Job {
    t0: f64,
    running: bool,
    result: Option<Value>,
    error: String,
}

type Jobs = Mutex<HashMap<String, Arc<Mutex<Job>>>>;
static JOBS: std::sync::LazyLock<Jobs> = std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

fn jobs() -> std::sync::MutexGuard<'static, HashMap<String, Arc<Mutex<Job>>>> {
    JOBS.lock().unwrap_or_else(|e| e.into_inner())
}

fn job_of(name: &str) -> Option<Arc<Mutex<Job>>> {
    jobs().get(name).cloned()
}

/// Run `f` on a thread; job() reports on it. One at a time per name.
pub fn start_job(name: &str, f: impl FnOnce() -> anyhow::Result<Value> + Send + 'static) {
    let mut all = jobs();
    if let Some(j) = all.get(name) {
        if j.lock().map(|j| j.running).unwrap_or(false) {
            return;
        }
    }
    let j = Arc::new(Mutex::new(Job {
        t0: now(),
        running: true,
        result: None,
        error: String::new(),
    }));
    all.insert(name.to_string(), j.clone());
    drop(all);
    let name = name.to_string();
    std::thread::spawn(move || {
        let got = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));
        let mut j = j.lock().unwrap_or_else(|e| e.into_inner());
        match got {
            Ok(Ok(v)) => j.result = Some(v),
            Ok(Err(e)) => {
                error!("{name} failed: {e:#}"); // surfaced in the panel
                j.error = last_line(&format!("{e:#}"), 120, "?");
            }
            Err(e) => {
                let text = e
                    .downcast_ref::<String>()
                    .cloned()
                    .or_else(|| e.downcast_ref::<&str>().map(|s| s.to_string()));
                j.error = last_line(text.as_deref().unwrap_or("?"), 120, "?");
            }
        }
        j.running = false;
    });
}

pub fn job(name: &str) -> Value {
    let Some(j) = job_of(name) else {
        return json!({"running": false, "idle": true});
    };
    let j = j.lock().unwrap_or_else(|e| e.into_inner());
    json!({"running": j.running, "elapsed": (now() - j.t0) as i64, "error": j.error,
           "result": if j.running { Value::Null } else { j.result.clone().unwrap_or(Value::Null) }})
}

/// "mine/a__b.md" -> "mine/a/b": the file name as the dream viewer shows it.
fn dream_name(n: &str) -> String {
    n.strip_suffix(".md").unwrap_or(n).replace("__", "/")
}

fn lookup<'a>(pairs: &'a [(String, String)], name: &str) -> &'a str {
    pairs
        .iter()
        .find(|(n, _)| n == name)
        .map(|(_, t)| t.as_str())
        .unwrap_or("")
}

/// Summary plus a unified diff per changed file.
pub fn dream_detail(summary: &str, before: &[(String, String)], new: &[(String, String)]) -> String {
    let mut out = vec![summary.trim().to_string(), String::new()];
    for (n, t) in new {
        let was = lookup(before, n);
        if t.trim() != was.trim() {
            let d = textdiff::unified(&dream_name(n), was, t.trim());
            if !d.is_empty() {
                out.push(d);
            }
            out.push(String::new());
        }
    }
    let text = out.join("\n");
    if text.trim().is_empty() {
        "nothing changed".into()
    } else {
        text
    }
}

pub fn dream_result((summary, before, new): memory::Dream) -> Value {
    // ponytail: everything the page is told comes from the same filter write() applies. The rows, the
    // deletion count and the full diff all used the raw answer, so the panel could name a team file
    // losing nine lines and then not touch it — a promise the apply drops. `theirs` is how many were
    // read and left alone, because a file simply missing from the list reads as one never looked at.
    let mine = memory::writable(&new);
    let theirs = new.len() - mine.len();
    let mut gone: Vec<&str> = mine
        .iter()
        .filter(|(n, t)| t.trim().is_empty() && !lookup(&before, n).trim().is_empty())
        .map(|(n, _)| n.as_str())
        .collect();
    gone.sort();
    let mut names: Vec<&str> = mine.iter().map(|(n, _)| n.as_str()).collect();
    names.sort_by_key(|n| (!gone.contains(n), n.to_string()));
    let files: Vec<Value> = names
        .iter()
        .map(|n| {
            json!({"name": dream_name(n), "before": lookup(&before, n).lines().count(),
                   "after": lookup(&mine, n).lines().count(), "deleted": gone.contains(n)})
        })
        .collect();
    let lost: usize = gone.iter().map(|n| lookup(&before, n).lines().count()).sum();
    let new_obj: Map<String, Value> = mine
        .iter()
        .map(|(n, t)| (n.clone(), Value::String(t.clone())))
        .collect();
    json!({"summary": summary, "files": files, "lost": lost, "theirs": theirs,
           "detail": dream_detail(&summary, &before, &mine), "new": new_obj})
}

// ---------------------------------------------------------------- routes: GET

fn q<'a>(query: &'a Query, key: &str) -> &'a str {
    query.get(key).map(String::as_str).unwrap_or("")
}

fn get_state(state: &State, _q: &Query) -> Out {
    Ok(payload(state))
}

/// The last `n` lines of a file, or the io error when it cannot be read, so a blank tail says why.
fn tail(path: &Path, n: usize) -> String {
    // ponytail: read the last 64 KB, not the whole log — the first line of the window may be a
    // fragment, which is fine for a tail. Seek to a line boundary if that ever matters.
    let read = || -> std::io::Result<String> {
        let mut f = std::fs::File::open(path)?;
        let len = f.seek(SeekFrom::End(0))?;
        f.seek(SeekFrom::Start(len.saturating_sub(64 * 1024)))?;
        let mut buf = Vec::new();
        f.read_to_end(&mut buf)?;
        Ok(String::from_utf8_lossy(&buf).into_owned())
    };
    match read() {
        Ok(text) => {
            let lines: Vec<&str> = text.lines().collect();
            lines[lines.len().saturating_sub(n)..].join("\n")
        }
        Err(e) => format!("({}: {e})", path.display()),
    }
}

/// The diagnostic bundle the debugger screen shows: build, paths, live poll state, and the debug log tail.
fn get_debug(state: &State, _q: &Query) -> Out {
    let cfg = config::get();
    let (
        fetched_at,
        fetching,
        error,
        auto,
        pending,
        running,
        sections,
        details,
        detailing,
        diffs,
        diffing,
        seen,
        known,
        sweeping,
        asks,
        notices,
    ) = {
        let inner = state.lock();
        (
            inner.fetched_at,
            inner.fetching,
            inner.error.clone(),
            inner.auto,
            inner.pending.clone(),
            inner.running.iter().cloned().collect::<Vec<_>>(),
            inner
                .sections
                .iter()
                .map(|s| {
                    json!({"name": s.name, "count": s.prs.as_ref().map(Vec::len).unwrap_or(0), "error": s.err})
                })
                .collect::<Vec<_>>(),
            inner.details.len(),
            inner.detailing.len(),
            inner.diffs.len(),
            inner.diffing.len(),
            inner.seen_at.len(),
            inner.known.as_ref().map(|k| k.len()),
            inner.sweeping,
            inner.asks.len(),
            inner.notices.clone(),
        )
    };
    Ok(json!({
        "at": now(),
        "version": config::VERSION,
        "pid": std::process::id(),
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "debug": cfg.debug,
        "demo": cfg.demo,
        "model": cfg.model,
        "interval": cfg.interval,
        "paths": {
            "settings": cfg.settings.as_deref().map(config::tilde).unwrap_or_default(),
            "memory": config::tilde(&cfg.memory_dir),
            "log": config::tilde(&cfg.log),
            "debugLog": config::tilde(&cfg.debug_log),
            "selfReviews": config::tilde(&cfg.self_dir),
            "backups": config::tilde(&cfg.backups),
            "bindings": config::tilde(&cfg.bindings),
            "teams": config::tilde(&cfg.teams),
            "registry": config::tilde(&cfg.registry),
            "corpus": config::tilde(&cfg.corpus_home),
        },
        "state": {
            "fetchedAt": fetched_at,
            "fetching": fetching,
            "error": error,
            "auto": auto,
            "pending": pending,
            "running": running,
            "sections": sections,
            "caches": {"details": details, "detailing": detailing, "diffs": diffs, "diffing": diffing, "seen": seen, "known": known},
            "sweeping": sweeping,
            "asks": asks,
            "notices": notices,
        },
        "log": tail(&cfg.debug_log, 200),
    }))
}

fn get_pr(state: &State, query: &Query) -> Out {
    let (pr, section) = need_pr(state, q(query, "url"))?;
    Ok(detail(state, &pr, &section))
}

fn get_diff(state: &State, query: &Query) -> Out {
    let context = q(query, "context").parse().unwrap_or(diff::CONTEXTS[0]);
    let (pr, _) = need_pr(state, q(query, "url"))?;
    let scope = if q(query, "scope").is_empty() {
        "marks"
    } else {
        q(query, "scope")
    };
    Ok(code(state, &pr, scope, context))
}

fn no_prereview(pr: &Pr) -> Fail {
    Fail(404, format!("no pre-review of #{} yet — p runs one", pr.number))
}

fn get_prereview(state: &State, query: &Query) -> Out {
    let (pr, _) = need_pr(state, q(query, "url"))?;
    let (at, moved) = review::self_review_state(&pr);
    if at == 0.0 {
        return Err(no_prereview(&pr));
    }
    let path = review::self_review_path(pr.repo(), pr.number);
    let text = std::fs::read_to_string(&path)?;
    Ok(json!({"path": path, "text": text, "moved": moved}))
}

fn get_memory(_state: &State, query: &Query) -> Out {
    let repo = Some(q(query, "repo")).filter(|r| !r.is_empty());
    let path = memory::path(repo, None);
    team::pull_dir(&config::get().memory_dir, "mine");
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    Ok(json!({"repo": repo.unwrap_or("general"), "path": knowledge::tilde(&path), "text": text}))
}

fn get_drafts(_state: &State, _q: &Query) -> Out {
    let mut items = memory::waiting();
    items.sort_by(|a, b| {
        (
            a.0.clone().unwrap_or_default(),
            a.3 == "self",
            std::cmp::Reverse(a.1),
        )
            .cmp(&(
                b.0.clone().unwrap_or_default(),
                b.3 == "self",
                std::cmp::Reverse(b.1),
            ))
    });
    let items: Vec<Value> = items
        .iter()
        .map(|(repo, n, fact, kind)| {
            json!({"repo": repo, "n": n, "fact": fact, "kind": kind,
                   "team": repo.as_deref().map(bind::of).unwrap_or_default()})
        })
        .collect();
    Ok(json!({"promoteAt": memory::PROMOTE_AT, "items": items}))
}

/// One overlap pair, re-read live: None once either side is no longer a draft.
///
/// ponytail: memory::rows, not memory::drafts. drafts() drops the run ids, and would_merge reads the ids
/// to tell two independent reviews from one review worded twice: with them empty it can only ever answer
/// the max and "origin unknown". That is the panel promising `promotes: false` on the very pair that
/// merge() then promotes, because merge reads rows. The panel and the keypress read the same thing now.
fn pair(repo: Option<&str>, a: &str, b: &str) -> Option<Value> {
    let live = memory::rows(repo);
    let find = |fact: &str| live.iter().find(|d| d.fact == fact).cloned();
    let (a, b) = (find(a)?, find(b)?);
    let (would, says) = memory::would_merge(&a, &b);
    Some(
        json!({"repo": repo, "a": a.fact, "b": b.fact, "would": would, "says": says, "promotes": would >= memory::PROMOTE_AT}),
    )
}

/// The scan's state. The model reads the candidates on a thread; pairs are re-read live on each poll.
fn get_overlaps(_state: &State, _q: &Query) -> Out {
    let mut j = job("overlaps");
    let running = j["running"].as_bool().unwrap_or(false);
    let idle = j["idle"].as_bool().unwrap_or(false);
    if running || idle || !j["error"].as_str().unwrap_or("").is_empty() {
        return Ok(j);
    }
    let pairs: Vec<Value> = j["result"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(|r| {
                    let repo = r[0].as_str();
                    pair(repo, r[2].as_str().unwrap_or(""), r[3].as_str().unwrap_or(""))
                })
                .collect()
        })
        .unwrap_or_default();
    j["result"] = Value::Array(pairs);
    Ok(j)
}

/// Who has accepted this fact, from a pools() index. Two names is two people's reviewers agreeing.
fn backers(
    index: &HashMap<String, Vec<(Option<String>, String)>>,
    repo: Option<&str>,
    fact: &str,
) -> Vec<String> {
    let mut out: Vec<String> = index
        .iter()
        .filter(|(_, items)| {
            items
                .iter()
                .any(|(r, f)| r.as_deref() == repo && memory::same(f, fact))
        })
        .map(|(u, _)| u.clone())
        .collect();
    out.sort();
    out
}

fn get_share(_state: &State, query: &Query) -> Out {
    let about = q(query, "about");
    let mut items = memory::in_team(about);
    let index = memory::pools();
    items.sort_by_key(|(repo, fact, sent)| {
        (
            *sent,
            std::cmp::Reverse(backers(&index, repo.as_deref(), fact).len()),
        )
    });
    let items: Vec<Value> = items
        .iter()
        .map(|(repo, fact, sent)| {
            json!({"repo": repo, "fact": fact, "sent": sent, "backers": backers(&index, repo.as_deref(), fact),
                   "team": repo.as_deref().map(bind::of).unwrap_or_else(|| bind::of(about))})
        })
        .collect();
    Ok(json!({"inTeam": team::on(), "items": items}))
}

fn used_for(key: &str) -> String {
    let key = key.to_lowercase();
    let mut owners: Vec<String> = bind::owners()
        .iter()
        .filter(|(_, t)| t.to_lowercase() == key)
        .map(|(o, _)| format!("{o}/*"))
        .collect();
    owners.sort();
    let repos = bind::bindings()
        .values()
        .filter(|t| t.to_lowercase() == key)
        .count();
    let mut head = owners.iter().take(3).cloned().collect::<Vec<_>>().join(", ");
    if owners.len() > 3 {
        head += &format!(" +{}", owners.len() - 3);
    }
    let tail = if repos > 0 {
        format!("{repos} repo{}", if repos == 1 { "" } else { "s" })
    } else {
        String::new()
    };
    [head, tail]
        .into_iter()
        .filter(|x| !x.is_empty())
        .collect::<Vec<_>>()
        .join(" · ")
}

fn brief_path(key: &str) -> Result<std::path::PathBuf, Fail> {
    let d = team::dir_of(key).ok_or_else(|| Fail(404, format!("not in team {key:?}")))?;
    let path = d.join("memory").join(memory::PROJECT);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(path)
}

/// The teams joined, one dict each. Opening the list clears the arrival badge, as T did.
fn get_teams(state: &State, query: &Query) -> Out {
    let arrived = state.take_arrivals();
    let key = q(query, "brief");
    if !key.is_empty() {
        let path = brief_path(key)?;
        team::seed_project(&path);
        let text = std::fs::read_to_string(&path)?;
        return Ok(json!({"key": key, "path": knowledge::tilde(&path), "text": text}));
    }
    let mut out = Vec::new();
    for key in team::joined() {
        let d = team::dir_of(&key).unwrap_or_default();
        let it = team::info(&key);
        let url = team::origin_url(&d);
        let real = std::fs::canonicalize(&d).unwrap_or_else(|_| d.clone());
        let linked = std::fs::symlink_metadata(&d)
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(false);
        out.push(json!({"key": key, "name": it.name, "description": it.description,
                        "checkout": knowledge::tilde(&real), "linked": linked,
                        "remote": if url.is_empty() { String::new() } else { team::redacted(&url) },
                        "used": used_for(&key), "arrived": arrived.get(&key).copied().unwrap_or(0),
                        "undecided": bind::undecided(&team::covers(&key))}));
    }
    Ok(json!({"teams": out, "error": team_error()}))
}

fn owner_of(repo: &str) -> String {
    bind::key(repo).split('/').next().unwrap_or("").to_string()
}

fn get_bind(_state: &State, query: &Query) -> Out {
    let repo = q(query, "repo");
    if repo.is_empty() {
        return Err(Fail::new(400, "no row selected"));
    }
    let (kind, to) = bind::why(repo);
    let teams: Vec<Value> = team::joined()
        .iter()
        .map(|k| json!({"key": k, "name": team::info(k).name}))
        .collect();
    Ok(json!({"repo": repo, "kind": kind, "to": to, "owner": owner_of(repo), "teams": teams}))
}

fn get_dream(_state: &State, _q: &Query) -> Out {
    let mut j = job("dream");
    if let Some(result) = j["result"].as_object_mut() {
        result.remove("new"); // the page never sees the file bodies; apply uses what the server held
    }
    Ok(j)
}

fn get_collaborators(state: &State, query: &Query) -> Out {
    let (pr, _) = need_pr(state, q(query, "url"))?;
    let me = pr.author().to_string();
    let logins: Vec<String> = github::collaborators(pr.repo())
        .into_iter()
        .filter(|c| *c != me)
        .collect();
    Ok(json!({"logins": logins}))
}

// ---------------------------------------------------------------- routes: POST

type Body = Map<String, Value>;

/// `str(body.get(key, ""))`: a string as is, null or missing as "", anything else as its JSON.
fn text(body: &Body, key: &str) -> String {
    match body.get(key) {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(v) => v.to_string(),
    }
}

/// `bool(body.get(key))`.
fn truthy(body: &Body, key: &str) -> bool {
    match body.get(key) {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0) != 0.0,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `body.get("repo") or None`.
fn repo_of(body: &Body) -> Option<String> {
    Some(text(body, "repo")).filter(|r| !r.is_empty())
}

fn post_review(state: &State, body: &Body) -> Out {
    let (pr, _) = need_pr(state, &text(body, "url"))?;
    // pre-review reads the diff and posts nothing; review posts the verdict. Same row, same spinner.
    // ponytail: the start IS the check. It claims the url under the state lock and says whether it got
    // it, so a double-click cannot start two reviews of one PR between the check and the spawn.
    let started = if truthy(body, "self") {
        state.start_self_review(&pr)
    } else {
        state.start_review(&pr)
    };
    if !started {
        return Err(Fail::new(409, "already running"));
    }
    Ok(json!({"ok": true}))
}

fn post_auto(state: &State, body: &Body) -> Out {
    // ponytail: include_existing only when the page says so: it asks first, with the count, as `a` did.
    state.set_auto(truthy(body, "on"), truthy(body, "includeExisting"));
    Ok(json!({"ok": true}))
}

fn post_refresh(state: &State, _body: &Body) -> Out {
    diff::retry(); // f means "look again", so a diff GitHub failed to read is worth retrying
    state.wake();
    Ok(json!({"ok": true}))
}

/// Open a PR, or its pre-review file, with the desktop. Only things on the board, never a free path.
fn post_open(state: &State, body: &Body) -> Out {
    let (pr, _) = need_pr(state, &text(body, "url"))?;
    if truthy(body, "pre") {
        let (at, _moved) = review::self_review_state(&pr);
        if at == 0.0 {
            return Err(no_prereview(&pr));
        }
        let path = review::self_review_path(pr.repo(), pr.number);
        github::open_in_browser(&path.to_string_lossy());
        return Ok(json!({"ok": true, "opened": path}));
    }
    github::open_in_browser(&pr.url);
    Ok(json!({"ok": true, "opened": pr.url}))
}

fn post_copy(state: &State, body: &Body) -> Out {
    let what = match text(body, "text") {
        t if !t.is_empty() => t,
        _ => need_pr(state, &text(body, "url"))?.0.url,
    };
    Ok(json!({"ok": true, "tool": github::copy(&what)}))
}

fn post_memory(_state: &State, body: &Body) -> Out {
    let repo = repo_of(body).filter(|r| r != "general");
    let path = memory::path(repo.as_deref(), None);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let dir = config::get().memory_dir;
    team::pull_dir(&dir, "mine");
    memory::history(); // the state before the edit is the version you want back if you regret it
    std::fs::write(&path, text(body, "text"))?;
    let err = team::push_dir(
        &dir,
        &format!("memory: {} edited", repo.as_deref().unwrap_or("general")),
        "mine",
    );
    Ok(json!({"ok": true, "error": err}))
}

fn post_drafts(_state: &State, body: &Body) -> Out {
    let (repo, fact) = (repo_of(body), text(body, "fact"));
    let label = repo.as_deref().unwrap_or("general");
    let dir = config::get().memory_dir;
    match text(body, "op").as_str() {
        "promote" => {
            memory::promote(repo.as_deref(), &fact);
            team::push_dir(&dir, &format!("memory: accepted for {label}"), "mine");
            team::push(&format!("memory: evidence for {label}"));
        }
        "drop" => {
            memory::drop(repo.as_deref(), &fact);
            team::push_dir(&dir, &format!("memory: dropped a draft for {label}"), "mine");
        }
        _ => return Err(Fail::new(400, "op must be promote or drop")),
    }
    Ok(json!({"ok": true}))
}

fn post_overlaps(_state: &State, body: &Body) -> Out {
    match text(body, "op").as_str() {
        "start" => {
            let model = config::get().model;
            start_job("overlaps", move || {
                let pairs = memory::overlaps(None);
                if pairs.is_empty() {
                    return Ok(json!([]));
                }
                // ponytail: an empty list is the model saying none match, which is an answer; only "not
                // asked" falls back to every candidate, for a person to read (judged does that itself).
                Ok(serde_json::to_value(memory::judged(&pairs, &model))?)
            });
            Ok(json!({"ok": true}))
        }
        "merge" => {
            let repo = repo_of(body);
            let label = repo.as_deref().unwrap_or("general").to_string();
            let n = memory::merge(repo.as_deref(), &text(body, "keep"), &text(body, "drop"));
            team::push_dir(
                &config::get().memory_dir,
                &format!("memory: folded two drafts for {label}"),
                "mine",
            );
            team::push(&format!("memory: evidence for {label}"));
            Ok(json!({"ok": true, "count": n}))
        }
        _ => Err(Fail::new(400, "op must be start or merge")),
    }
}

fn post_share(_state: &State, body: &Body) -> Out {
    let (repo, fact, about) = (repo_of(body), text(body, "fact"), text(body, "about"));
    let label = repo.as_deref().unwrap_or("general");
    match text(body, "op").as_str() {
        "send" => {
            memory::share(repo.as_deref(), &fact, &about);
            team::push(&format!("memory: share {label}"));
        }
        "forget" => {
            memory::forget(repo.as_deref(), &fact, &about);
            team::push_dir(
                &config::get().memory_dir,
                &format!("memory: forget {label}"),
                "mine",
            );
            team::push(&format!("memory: withdraw {label}"));
        }
        _ => return Err(Fail::new(400, "op must be send or forget")),
    }
    Ok(json!({"ok": true}))
}

/// Bind owner/* here, then declare it in the team. Local first: the cheap, reversible half.
fn cover(key: &str, owner: &str) -> Result<(), Fail> {
    let owner = bind::owner_key(owner);
    if owner.is_empty() {
        return Err(Fail::new(400, "an owner is one name, like neomedsys"));
    }
    let mut err = bind::bind_owner(&owner, key);
    if err.is_empty() {
        err = team::cover(key, &owner);
    }
    if err.is_empty() {
        Ok(())
    } else {
        Err(Fail(400, err))
    }
}

fn fail_if(err: String) -> Result<(), Fail> {
    if err.is_empty() {
        Ok(())
    } else {
        Err(Fail(400, err))
    }
}

fn post_teams(state: &State, body: &Body) -> Out {
    let (op, key) = (text(body, "op"), text(body, "key"));
    if op == "new" {
        let name = text(body, "name").trim().to_string();
        if name.is_empty() {
            return Err(Fail::new(400, "a team needs a name"));
        }
        fail_if(team::start(&name, &text(body, "desc"), ""))?;
        let key = team::key_of(&name);
        let owner = text(body, "owner");
        if !owner.is_empty() {
            cover(&key, &owner)?;
        }
        state.wake();
        return Ok(json!({"ok": true, "key": key}));
    }
    if op == "join" {
        let before = team::joined();
        fail_if(team::setup(text(body, "repo").trim(), ""))?;
        state.wake();
        let mut fresh: Vec<String> = team::joined()
            .into_iter()
            .filter(|k| !before.contains(k))
            .collect();
        fresh.sort();
        let err = team_error();
        let warning = if err.is_empty() {
            String::new()
        } else {
            format!(
                "joined, but could not publish: {}",
                err.chars().take(70).collect::<String>()
            )
        };
        return Ok(
            json!({"ok": true, "key": fresh.first().cloned().unwrap_or_default(), "warning": warning}),
        );
    }
    let Some(dir) = team::dir_of(&key) else {
        return Err(Fail(404, format!("not in team {key:?}")));
    };
    match op.as_str() {
        "connect" => {
            let url = text(body, "url");
            fail_if(team::connect(&key, &url))?;
            Ok(json!({"ok": true, "remote": team::redacted(&url)}))
        }
        "describe" => {
            fail_if(team::write_info(
                &key,
                &team::info(&key).name,
                &text(body, "desc"),
                None,
            ))?;
            team::push_dir(&dir, &format!("team: describe {key}"), "sync");
            Ok(json!({"ok": true}))
        }
        "cover" => {
            cover(&key, &text(body, "owner"))?;
            state.wake();
            Ok(json!({"ok": true}))
        }
        "brief" => {
            let path = brief_path(&key)?;
            std::fs::write(&path, text(body, "text"))?;
            let err = team::push_dir(&dir, &format!("memory: the brief for {key}"), "sync");
            Ok(json!({"ok": true, "error": err}))
        }
        "claim" => {
            let (kind, v) = bind::target(&text(body, "target"));
            let err = match (truthy(body, "yes"), kind == "owner") {
                (true, true) => bind::bind_owner(&v, &key),
                (true, false) => bind::bind(&v, &key),
                (false, true) => bind::forget_owner(&v),
                (false, false) => bind::forget(&v),
            };
            fail_if(err)?;
            state.wake();
            Ok(json!({"ok": true}))
        }
        "leave" => {
            fail_if(knowledge::leave(&key))?;
            state.wake();
            Ok(json!({"ok": true}))
        }
        _ => Err(Fail::new(400, "unknown team op")),
    }
}

fn post_bind(state: &State, body: &Body) -> Out {
    let (repo, op, to) = (text(body, "repo"), text(body, "op"), text(body, "team"));
    if repo.is_empty() {
        return Err(Fail::new(400, "no row selected"));
    }
    let err = match op.as_str() {
        "forget" => bind::forget(&repo),
        "owner" => bind::bind_owner(&owner_of(&repo), &to),
        "bind" => bind::bind(&repo, &to),
        _ => return Err(Fail::new(400, "op must be bind, owner or forget")),
    };
    fail_if(err)?;
    state.wake();
    Ok(json!({"ok": true}))
}

fn post_dream(_state: &State, body: &Body) -> Out {
    let op = text(body, "op");
    if op == "start" {
        let model = config::get().model;
        start_job("dream", move || Ok(dream_result(memory::dream(&model)?)));
        return Ok(json!({"ok": true}));
    }
    if op == "discard" {
        jobs().remove("dream");
        return Ok(json!({"ok": true}));
    }
    if op == "apply" {
        let new = job_of("dream").and_then(|j| {
            let j = j.lock().unwrap_or_else(|e| e.into_inner());
            if j.running {
                return None;
            }
            j.result
                .as_ref()
                .and_then(|r| r.get("new"))
                .and_then(Value::as_object)
                .cloned()
        });
        let Some(new) = new else {
            return Err(Fail::new(409, "no dream to apply"));
        };
        let dir = config::get().memory_dir;
        // ponytail: YOUR dir only, on both legs. A dream rewrites nothing under a team checkout now, so
        // pulling one first bought nothing and pushing one afterwards was worse: push_dir runs
        // `git add -A`, so somebody else's pending team changes were committed under "dream cleanup",
        // and its error could report "memory rewritten, but NOT committed" about a source the dream
        // never touched.
        team::pull_dir(&dir, "mine");
        let new: Vec<(String, String)> = new
            .iter()
            .map(|(n, t)| (n.clone(), t.as_str().unwrap_or("").to_string()))
            .collect();
        memory::write(&new)?;
        jobs().remove("dream");
        let err = team::push_dir(&dir, "memory: dream cleanup", "mine");
        let error = if err.is_empty() {
            String::new()
        } else {
            format!("memory rewritten, but NOT committed: {err} — a backup is in ~/.prs_backups")
        };
        return Ok(json!({"ok": true, "error": error}));
    }
    Err(Fail::new(400, "op must be start, apply or discard"))
}

fn post_request_review(state: &State, body: &Body) -> Out {
    let (pr, _) = need_pr(state, &text(body, "url"))?;
    let login = text(body, "login").trim().to_string();
    if login.is_empty() {
        return Err(Fail::new(400, "a login is needed"));
    }
    fail_if(github::request_review(pr.repo(), pr.number, &login))?;
    state.wake(); // refetch so the new reviewer shows on the row
    Ok(json!({"ok": true}))
}

/// Answer one launch-time ask: whether a team may receive facts, or its agents.md may reach sessions.
fn post_consent(state: &State, body: &Body) -> Out {
    let (kind, key, yes) = (text(body, "kind"), text(body, "key"), truthy(body, "yes"));
    // ponytail: "again" forgets the recorded answer so the ask comes back. The prompts list what has
    // NO answer, so re-asking without forgetting first would draw nothing and read as a dead button.
    if text(body, "op") == "again" {
        match kind.as_str() {
            "publishing" => memory::ask_publishing_again(&key),
            "agents" => memory::ask_agents_again(&key),
            _ => return Err(Fail::new(400, "kind must be publishing or agents")),
        };
        state.lock().asks = launch_asks();
        state.wake();
        return Ok(json!({"ok": true, "asks": launch_asks()}));
    }
    match kind.as_str() {
        "publishing" => memory::allow_publishing(&key, yes),
        "agents" => memory::allow_agents(&key, &text(body, "text"), yes), // what was SHOWN is what gets recorded
        _ => return Err(Fail::new(400, "kind must be publishing or agents")),
    }
    state
        .lock()
        .asks
        .retain(|a| !(a["kind"] == kind && a["key"] == key));
    state.wake();
    Ok(json!({"ok": true}))
}

/// Point Memory (L) or Store (C) somewhere else. Answers {confirm} first when the move needs a yes.
fn post_path(_state: &State, body: &Body) -> Out {
    let (which, new, force) = (
        text(body, "which"),
        text(body, "path").trim().to_string(),
        truthy(body, "force"),
    );
    if (which != "L" && which != "C") || new.is_empty() {
        return Err(Fail::new(400, "which must be L or C, with a path"));
    }
    let cfg = config::get();
    let cur = if which == "L" { cfg.local_memory } else { cfg.teams };
    let err = if knowledge::is_remote(&new) {
        if which != "L" {
            return Err(Fail::new(
                400,
                "Store is a local directory — T is what clones a team repo",
            ));
        }
        if !force {
            return Ok(
                json!({"confirm": format!("clone {new} into {}, keeping the facts already there?", knowledge::tilde(&cur))}),
            );
        }
        knowledge::adopt(&new, None)
    } else if knowledge::inside_git(Path::new(&new)) && !force {
        return Ok(
            json!({"confirm": format!("{new} sits in a git repo that does not ignore it — memory could be committed. continue?")}),
        );
    } else if which == "L" {
        knowledge::set_local(Path::new(&new))
    } else {
        knowledge::set_store(Path::new(&new))
    };
    fail_if(err)?;
    Ok(json!({"ok": true}))
}

/// Install the newest release and re-exec. The reply goes out first; the page reconnects.
fn post_update(state: &State, _body: &Body) -> Out {
    let (version, token) = {
        let inner = state.lock();
        (inner.update.clone(), inner.token.clone())
    };
    if version.is_empty() {
        return Err(Fail::new(409, "already on the newest release"));
    }
    let me = state.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(300));
        if !token.is_empty() {
            std::env::set_var("GITDASHY_GUI_TOKEN", &token); // the re-exec'd server must answer on the same token
        }
        let err = update::apply_update(&version); // re-execs on success
        if !err.is_empty() {
            me.lock().notices.push(format!("update failed: {err}"));
        }
    });
    Ok(json!({"ok": true}))
}

fn post_quit(_state: &State, _body: &Body) -> Out {
    // ponytail: the request threads have nothing to flush; the reply goes out, then the process ends
    std::thread::spawn(|| {
        std::thread::sleep(Duration::from_millis(200));
        std::process::exit(0);
    });
    Ok(json!({"ok": true}))
}

fn post_notices(state: &State, _body: &Body) -> Out {
    state.lock().notices.clear();
    Ok(json!({"ok": true}))
}

/// A JSON value that names one of `options`, as Python's `body[key] in options`.
fn pick<'a>(v: &Value, options: &[&'a str]) -> Option<&'a str> {
    let s = v.as_str()?;
    options.iter().copied().find(|o| *o == s)
}

/// Apply the settings the page can change, and persist them.
///
/// ponytail: values off the wire, so every one is checked here. The interval drives a loop that hits
/// the GitHub API: 0 would spin it flat out against your rate limit, and a string would break inside
/// the refresh thread, where nothing is watching. Nothing lands until every key checked out.
fn post_settings(state: &State, body: &Body) -> Out {
    let mut c = config::get();
    let mut wake = false;
    if let Some(v) = body.get("interval") {
        let n = match v {
            Value::Number(n) => n.as_f64().map(|f| f as i64),
            Value::String(s) => s.trim().parse::<i64>().ok(),
            _ => None,
        };
        let Some(n) = n else {
            return Err(Fail::new(400, "interval must be a number"));
        };
        if !(30..=86400).contains(&n) {
            return Err(Fail::new(400, "interval must be 30s to a day"));
        }
        c.interval = n as u64;
        wake = true; // a shorter interval should not wait out the longer one it replaced
    }
    if let Some(v) = body.get("model") {
        let name = text(body, "model").trim().to_string();
        if name.is_empty() || name.chars().count() > 60 || !v.is_string() {
            return Err(Fail::new(400, "bad model"));
        }
        c.model = name;
    }
    for (key, options) in [
        ("depth", config::DEPTHS),
        ("effort", config::EFFORTS),
        ("theme", THEMES),
    ] {
        if let Some(v) = body.get(key) {
            let Some(got) = pick(v, options) else {
                let list = options
                    .iter()
                    .map(|o| if o.is_empty() { "default" } else { o })
                    .collect::<Vec<_>>()
                    .join(", ");
                return Err(Fail(400, format!("{key} must be one of {list}")));
            };
            match key {
                "depth" => c.depth = got.into(),
                "effort" => c.effort = got.into(),
                _ => c.theme = got.into(),
            }
        }
    }
    for (key, options) in [("voice", config::VOICES), ("hunter", config::HUNTERS)] {
        if let Some(v) = body.get(key) {
            let got: Vec<&str> = v
                .as_array()
                .map(|a| a.iter().filter_map(Value::as_str).collect())
                .unwrap_or_default();
            let odd = v
                .as_array()
                .map(|a| a.iter().any(|x| !x.is_string()))
                .unwrap_or(false);
            if odd || got.iter().any(|g| !options.contains(g)) {
                return Err(Fail(400, format!("{key} must be from {}", options.join(", "))));
            }
            let new: Vec<String> = options
                .iter()
                .filter(|o| got.contains(o))
                .map(|o| o.to_string())
                .collect(); // ponytail: rebuilt in option order
            if key == "voice" && new.is_empty() {
                return Err(Fail::new(
                    400,
                    "at least one voice stays on, or nothing gets posted",
                ));
            }
            if key == "voice" {
                c.voice = new;
            } else {
                c.hunter = new;
            }
        }
    }
    if let Some(v) = body.get("subs") {
        let Some(got) = pick(v, config::SUBS) else {
            return Err(Fail(
                400,
                format!("subs must be one of {}", config::SUBS.join(", ")),
            ));
        };
        c.sub = got.into();
    }
    if let Some(v) = body.get("window") {
        let got = match v {
            Value::Null => Some(None),
            Value::Number(n) => n.as_u64().map(Some),
            _ => None,
        };
        match got {
            Some(w) if config::WINDOWS.contains(&w) => c.window = w,
            _ => {
                return Err(Fail::new(
                    400,
                    "window must be one of the offered hours, or null for all",
                ))
            }
        }
    }
    if body.contains_key("drafts") {
        c.drafts = truthy(body, "drafts");
    }
    if body.contains_key("hinted") {
        c.hinted = truthy(body, "hinted");
    }
    if body.contains_key("notify") {
        c.notify = truthy(body, "notify");
    }
    config::normalise(&mut c);
    let saved = config::snapshot(&c);
    config::update(|cfg| *cfg = c);
    config::save(&saved)?;
    if wake {
        state.wake();
    }
    Ok(json!({"ok": true}))
}

// ---------------------------------------------------------------- the server

type Get = fn(&State, &Query) -> Out;
type Post = fn(&State, &Body) -> Out;

fn get_route(path: &str) -> Option<Get> {
    Some(match path {
        "/api/state" => get_state,
        "/api/debug" => get_debug,
        "/api/asks" => get_asks,
        "/api/pr" => get_pr,
        "/api/diff" => get_diff,
        "/api/prereview" => get_prereview,
        "/api/memory" => get_memory,
        "/api/drafts" => get_drafts,
        "/api/overlaps" => get_overlaps,
        "/api/share" => get_share,
        "/api/teams" => get_teams,
        "/api/bind" => get_bind,
        "/api/dream" => get_dream,
        "/api/collaborators" => get_collaborators,
        _ => return None,
    })
}

fn post_route(path: &str) -> Option<Post> {
    Some(match path {
        "/api/review" => post_review,
        "/api/auto" => post_auto,
        "/api/settings" => post_settings,
        "/api/refresh" => post_refresh,
        "/api/open" => post_open,
        "/api/copy" => post_copy,
        "/api/memory" => post_memory,
        "/api/drafts" => post_drafts,
        "/api/overlaps" => post_overlaps,
        "/api/share" => post_share,
        "/api/teams" => post_teams,
        "/api/bind" => post_bind,
        "/api/dream" => post_dream,
        "/api/request-review" => post_request_review,
        "/api/consent" => post_consent,
        "/api/path" => post_path,
        "/api/update" => post_update,
        "/api/quit" => post_quit,
        "/api/notices" => post_notices,
        _ => return None,
    })
}

/// The launch-time consent questions, as the page shows them: publishing per team, then agents.md.
pub fn launch_asks() -> Vec<Value> {
    let mut asks = Vec::new();
    for (key, drafts, facts) in memory::unasked() {
        let plural = |n: usize, what: &str| {
            if n > 0 {
                format!("{n} {what}{}", if n == 1 { "" } else { "s" })
            } else {
                String::new()
            }
        };
        let waiting = [plural(drafts, "draft"), plural(facts, "fact")]
            .into_iter()
            .filter(|x| !x.is_empty())
            .collect::<Vec<_>>()
            .join(" · ");
        asks.push(
            json!({"kind": "publishing", "key": key, "name": team::info(&key).name, "waiting": waiting}),
        );
    }
    for (key, text) in memory::unacked_agents() {
        let path = bind::team_dir(&key).unwrap_or_default().join(memory::AGENTS);
        asks.push(json!({"kind": "agents", "key": key, "name": team::info(&key).name, "text": text, "path": knowledge::tilde(&path)}));
    }
    asks
}

/// The launch-time consent questions the page still shows, recomputed on demand. The inline flows
/// (a team just started or joined) ask again without waiting for a restart.
fn get_asks(_state: &State, _query: &Query) -> Out {
    Ok(json!({"asks": launch_asks()}))
}

/// The built app's index shell. Vite emits one; a binary with no dist is a build mistake, not a crash.
fn index_html() -> Vec<u8> {
    DIST.get_file("index.html")
        .map(|f| f.contents().to_vec())
        .unwrap_or_default()
}

/// One embedded asset by request path ("/assets/x.js"), and its content type.
pub(crate) fn asset(path: &str) -> Option<(Vec<u8>, &'static str)> {
    let file = DIST.get_file(path.trim_start_matches('/'))?;
    let ctype = match path.rsplit('.').next().unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "js" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "woff2" => "font/woff2",
        _ => "application/octet-stream",
    };
    Some((file.contents().to_vec(), ctype))
}

fn parse_query(raw: &str) -> Query {
    let mut out = Query::new();
    for part in raw.split('&').filter(|p| !p.is_empty()) {
        let (k, v) = part.split_once('=').unwrap_or((part, ""));
        let decode = |s: &str| {
            urlencoding::decode(&s.replace('+', " "))
                .map(|c| c.into_owned())
                .unwrap_or_else(|_| s.to_string())
        };
        out.entry(decode(k)).or_insert_with(|| decode(v));
    }
    out
}

fn header(req: &Request, name: &'static str) -> String {
    req.headers()
        .iter()
        .find(|h| h.field.equiv(name))
        .map(|h| h.value.as_str().to_string())
        .unwrap_or_default()
}

/// What the window is allowed to load. The window navigates to this server over http, and a remote URL
/// takes its policy from the RESPONSE, not from tauri.conf.json > app.security.csp, which only covers
/// pages on the asset protocol. So the page that renders PR titles, diffs and model output, none of it
/// written by us, gets its CSP here or nowhere.
///
/// ponytail: 'unsafe-inline' for styles only. React writes style attributes and the app sets its theme
/// through CSS variables on <html>; scripts stay 'self', which is the half that matters. The two font
/// origins are what dist/index.html already asks for.
const CSP: &str = "default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; \
     style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; \
     object-src 'none'; base-uri 'self'; frame-ancestors 'none'";

/// Every reply the server makes. `cache` is None for anything with data in it.
///
/// ponytail: one responder, because send and send_bytes had drifted into the same four headers written
/// twice, and a header added to one of them would have been a header the other quietly lacked.
fn send_with(req: Request, code: u16, body: Vec<u8>, ctype: &str, cache: Option<&str>) {
    let ok = |h: Result<Header, ()>| h.expect("a static header is well formed");
    let mut resp = Response::from_data(body)
        .with_status_code(code)
        .with_header(ok(Header::from_bytes("Content-Type", ctype)))
        // ponytail: the page talks to its own origin only; nothing here is meant to be embedded.
        .with_header(ok(Header::from_bytes("X-Frame-Options", "DENY")))
        .with_header(ok(Header::from_bytes("Content-Security-Policy", CSP)));
    if let Some(c) = cache {
        resp = resp.with_header(ok(Header::from_bytes("Cache-Control", c)));
    }
    if let Err(e) = req.respond(resp) {
        debug!("gui reply failed: {e}");
    }
}

fn send_json(req: Request, code: u16, body: Value) {
    send_with(req, code, body.to_string().into_bytes(), "application/json", None);
}

/// A static asset. Cacheable hashed names, but no-store keeps it simple.
fn send_bytes(req: Request, code: u16, body: Vec<u8>, ctype: &str) {
    send_with(req, code, body, ctype, Some("no-store"));
}

/// Equal without leaking where they differ.
fn same_token(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// The host a Host header names, without its port.
fn host_of(raw: &str) -> String {
    let raw = raw.trim();
    let no_port = match raw.rsplit_once(':') {
        Some((h, p)) if p.chars().all(|c| c.is_ascii_digit()) => h,
        _ => raw,
    };
    no_port.trim_matches(|c| c == '[' || c == ']').to_string()
}

/// A shared secret on every request, and a localhost Host.
///
/// ponytail: this server answers with your PRs and starts Claude runs that cost money, so it is
/// a trust boundary even on loopback. Any page you happen to have open can POST to 127.0.0.1
/// without reading the reply, and can reach it by a hostname that resolves there (DNS
/// rebinding): the token stops the first, the Host check stops the second.
fn guard(req: &Request, query: &Query, token: &str) -> Result<(), (u16, &'static str)> {
    let host = host_of(&header(req, "Host"));
    if !["127.0.0.1", "localhost", "::1"].contains(&host.as_str()) {
        return Err((403, "bad host"));
    }
    // The shell and its assets hold no data, so they load without a token; every /api route keeps one.
    // ponytail: the app bundle is 270 KB of JS, and a token query on every <script>/<link> would mean
    // rewriting Vite's index — a hole in the guard for static files is the smaller price.
    let path = req.url().split('?').next().unwrap_or("");
    if path != "/" && !path.starts_with("/api/") {
        return Ok(());
    }
    let got = header(req, "X-Dashy-Token");
    let got = if got.is_empty() {
        q(query, "token").to_string()
    } else {
        got
    };
    if !same_token(&got, token) {
        return Err((401, "bad token"));
    }
    Ok(())
}

/// Run a route and send what it says. A Fail is a status the page reads.
fn answer(req: Request, out: Out) {
    match out {
        Ok(v) => send_json(req, 200, v),
        Err(Fail(code, msg)) => send_json(req, code, json!({"error": msg})),
    }
}

fn handle(state: &State, token: &str, mut req: Request) {
    let url = req.url().to_string();
    let (path, raw_query) = url.split_once('?').unwrap_or((&url, ""));
    let query = parse_query(raw_query);
    debug!("gui {} {}", req.method(), path);
    if let Err((code, msg)) = guard(&req, &query, token) {
        return send_json(req, code, json!({"error": msg}));
    }
    match req.method() {
        Method::Get => {
            if path == "/" {
                return send_bytes(req, 200, index_html(), "text/html; charset=utf-8");
            }
            if let Some((body, ctype)) = asset(path) {
                return send_bytes(req, 200, body, ctype);
            }
            match get_route(path) {
                Some(f) => answer(req, f(state, &query)),
                None => send_json(req, 404, json!({"error": "not found"})),
            }
        }
        Method::Post => {
            let Some(f) = post_route(path) else {
                return send_json(req, 404, json!({"error": "not found"}));
            };
            let mut raw = Vec::new();
            let body: Option<Body> = req
                .as_reader()
                .read_to_end(&mut raw)
                .ok()
                .and_then(|_| {
                    if raw.is_empty() {
                        Some(Value::Object(Body::new()))
                    } else {
                        serde_json::from_slice(&raw).ok()
                    }
                })
                .and_then(|v: Value| if let Value::Object(o) = v { Some(o) } else { None });
            match body {
                Some(body) => answer(req, f(state, &body)),
                None => send_json(req, 400, json!({"error": "bad body"})),
            }
        }
        _ => send_json(req, 404, json!({"error": "not found"})),
    }
}

/// Serve on 127.0.0.1:`port` (0 = any free) and return the bound port. Runs its accept loop on a thread.
pub fn serve(state: State, port: u16, token: String) -> std::io::Result<u16> {
    let server = Server::http(("127.0.0.1", port)).map_err(|e| std::io::Error::other(e.to_string()))?;
    let bound = server.server_addr().to_ip().map(|a| a.port()).unwrap_or(port);
    state.lock().token = token.clone();
    std::thread::spawn(move || {
        for req in server.incoming_requests() {
            let (state, token) = (state.clone(), token.clone());
            std::thread::spawn(move || handle(&state, &token, req));
        }
    });
    Ok(bound)
}

/// A random session token, hex.
pub fn new_token() -> String {
    let mut raw = [0u8; 24];
    getrandom::fill(&mut raw).expect("randomness");
    raw.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Hunk, Line, Login, Repository, Section};

    /// Config is global and tests run in parallel: the ones that touch it take this.
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn pr() -> Pr {
        Pr {
            number: 7,
            title: "T".into(),
            url: "u".into(),
            updated_at: "2020-01-01T00:00:00Z".into(),
            author: Some(Login { login: "me".into() }),
            repository: Repository {
                name_with_owner: "a/b".into(),
                name: "b".into(),
            },
            status: "· awaiting review".into(),
            ..Default::default()
        }
    }

    /// A real server over a State with one PR in it: (base url, token, state).
    fn served() -> (String, String, State) {
        let state = State::new();
        state.lock().sections = vec![
            Section {
                name: "MINE".into(),
                prs: Some(vec![pr()]),
                err: None,
            },
            Section {
                name: "ASSIGNED".into(),
                prs: None,
                err: Some("boom\nsecond line".into()),
            },
        ];
        let token = "t0ken".to_string();
        let port = serve(state.clone(), 0, token.clone()).unwrap();
        (format!("http://127.0.0.1:{port}"), token, state)
    }

    fn agent() -> ureq::Agent {
        ureq::Agent::config_builder()
            .http_status_as_error(false)
            .build()
            .new_agent()
    }

    fn get(url: &str, token: Option<&str>) -> (u16, Value) {
        let mut req = agent().get(url);
        if let Some(t) = token {
            req = req.header("X-Dashy-Token", t);
        }
        let mut resp = req.call().unwrap();
        let text = resp.body_mut().read_to_string().unwrap();
        (
            resp.status().as_u16(),
            serde_json::from_str(&text).unwrap_or(Value::String(text)),
        )
    }

    fn post(url: &str, body: Value, token: &str) -> (u16, Value) {
        let mut resp = agent()
            .post(url)
            .header("X-Dashy-Token", token)
            .send_json(body)
            .unwrap();
        let text = resp.body_mut().read_to_string().unwrap();
        (
            resp.status().as_u16(),
            serde_json::from_str(&text).unwrap_or(Value::String(text)),
        )
    }

    #[test]
    fn debug_route_needs_the_token_and_carries_the_paths() {
        let (base, token, _state) = served();
        assert_eq!(get(&format!("{base}/api/debug"), None).0, 401);
        let (code, d) = get(&format!("{base}/api/debug"), Some(&token));
        assert_eq!(code, 200);
        assert_eq!(d["version"], config::VERSION);
        assert!(!d["paths"]["debugLog"].as_str().unwrap().is_empty());
        assert_eq!(d["state"]["sections"][0]["name"], "MINE");
    }

    #[test]
    fn tail_handles_missing_short_and_long_files() {
        let dir = std::env::temp_dir().join(format!("dashy-tail-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let missing = dir.join("nope.log");
        assert!(tail(&missing, 3).contains("nope.log"));
        let empty = dir.join("empty.log");
        std::fs::write(&empty, "").unwrap();
        assert_eq!(tail(&empty, 3), "");
        let few = dir.join("few.log");
        std::fs::write(&few, "a\nb\n").unwrap();
        assert_eq!(tail(&few, 3), "a\nb");
        let many = dir.join("many.log");
        std::fs::write(&many, (0..10).map(|i| format!("{i}\n")).collect::<String>()).unwrap();
        assert_eq!(tail(&many, 3), "7\n8\n9");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn no_token_is_refused_and_a_good_one_gets_the_payload() {
        let (base, token, _state) = served();
        assert_eq!(get(&format!("{base}/api/state"), None).0, 401);
        assert_eq!(get(&format!("{base}/api/state?token=wrong"), None).0, 401);
        let (code, d) = get(&format!("{base}/api/state"), Some(&token));
        assert_eq!(code, 200);
        assert_eq!(d["version"], config::VERSION);
        assert_eq!(d["sections"][0]["name"], "MINE");
        let row = &d["sections"][0]["prs"][0];
        assert_eq!(
            (
                row["number"].as_u64(),
                row["repo"].as_str(),
                row["author"].as_str()
            ),
            (Some(7), Some("a/b"), Some("me"))
        );
        assert_eq!(row["status"], "· awaiting review");
        assert_eq!(row["busy"], false);
        assert_eq!(d["sections"][1]["prs"], json!([]));
        assert!(d["sections"][1]["error"].as_str().unwrap().starts_with("boom"));
        // the token in the query works too, as the page load uses it
        assert_eq!(
            get(&format!("{base}/api/state?token={token}"), None).1["running"],
            0
        );
        assert_eq!(get(&format!("{base}/api/nope"), Some(&token)).0, 404);
    }

    #[test]
    fn the_shell_is_guarded_but_its_assets_are_not() {
        let (base, token, _state) = served();
        let (code, body) = get(&format!("{base}/?token={token}"), None);
        assert_eq!(code, 200);
        let html = body.as_str().unwrap();
        assert!(html.contains("<div id=\"root\">"));
        assert_eq!(get(&format!("{base}/"), None).0, 401);
        // static assets load with no token; the app's own <script>/<link> carry none
        let asset = agent().get(format!("{base}/head.png")).call().unwrap();
        assert_eq!(asset.status().as_u16(), 200);

        // the window loads this over http, so the policy has to ride on the response or it is absent.
        // Both fonts origins are in it, or the page renders in a fallback face.
        for r in [
            &asset,
            &agent().get(format!("{base}/?token={token}")).call().unwrap(),
        ] {
            let csp = r.headers()["content-security-policy"].to_str().unwrap();
            assert!(csp.starts_with("default-src 'self'"), "{csp}");
            assert!(csp.contains("script-src 'self';"), "{csp}");
            assert!(csp.contains("https://fonts.gstatic.com"), "{csp}");
            assert!(!csp.contains('\n'), "one header line, not three: {csp:?}");
        }
    }

    #[test]
    fn busy_follows_in_flight_and_the_review_rides_along() {
        let (base, token, state) = served();
        state.lock().running.insert("u".into());
        state.lock().reviews.insert("u".into(), "3 findings".into());
        let d = get(&format!("{base}/api/state"), Some(&token)).1;
        assert_eq!(d["sections"][0]["prs"][0]["busy"], true);
        assert_eq!(d["sections"][0]["prs"][0]["review"], "3 findings");
        assert_eq!(d["running"], 1);
        // and a review of a row in flight is refused; one off the board is not something to pay for
        assert_eq!(
            post(&format!("{base}/api/review"), json!({"url": "u"}), &token).0,
            409
        );
        assert_eq!(
            post(
                &format!("{base}/api/review"),
                json!({"url": "https://elsewhere/1"}),
                &token
            )
            .0,
            404
        );
    }

    #[test]
    fn unknown_pr_is_a_404_json() {
        let (base, token, _state) = served();
        let (code, d) = get(&format!("{base}/api/pr?url=nope"), Some(&token));
        assert_eq!(code, 404);
        assert_eq!(d["error"], "no such pr");
        let (code, d) = post(&format!("{base}/api/open"), json!({"url": "/etc/passwd"}), &token);
        assert_eq!((code, d["error"].as_str()), (404, Some("no such pr")));
        // no pre-review file yet, so nothing to hand to the desktop either
        assert_eq!(
            post(
                &format!("{base}/api/open"),
                json!({"url": "u", "pre": true}),
                &token
            )
            .0,
            404
        );
    }

    #[test]
    fn a_bad_body_is_a_400() {
        let (base, token, _state) = served();
        let mut resp = agent()
            .post(format!("{base}/api/auto"))
            .header("X-Dashy-Token", &token)
            .send("[1,2]")
            .unwrap();
        assert_eq!(resp.status().as_u16(), 400);
        assert!(resp.body_mut().read_to_string().unwrap().contains("bad body"));
        assert_eq!(post(&format!("{base}/api/nope"), json!({}), &token).0, 404);
    }

    /// The fold panel and the keypress must not disagree: `pair` answering "1, origin unknown" while
    /// `merge` folds to 2 and promotes is the one wrong answer here that writes to the team pool.
    #[test]
    fn the_fold_panel_counts_what_merge_would_actually_do() {
        let _guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        config::update(|c| c.memory_dir = dir.path().to_path_buf());
        let queue = memory::queue_path(None);
        std::fs::create_dir_all(queue.parent().unwrap()).unwrap();
        // two drafts from two genuinely independent review runs
        std::fs::write(
            &queue,
            "- (1) [r:aaaa] the retry loop never backs off\n- (1) [r:bbbb] retries fire with no backoff\n",
        )
        .unwrap();

        let panel = pair(
            None,
            "the retry loop never backs off",
            "retries fire with no backoff",
        )
        .expect("both rows are live");
        let would = panel["would"].as_u64().unwrap() as u32;
        assert_eq!(would, 2, "two run ids, so two observations: {panel}");
        assert!(panel["promotes"].as_bool().unwrap());

        let merged = memory::merge(
            None,
            "the retry loop never backs off",
            "retries fire with no backoff",
        );
        assert_eq!(would, merged, "the panel promised {would}, the fold did {merged}");
    }

    /// ponytail: a theme the stylesheet has no rule for renders :root and looks like nothing happened.
    /// "dashy" IS :root, so it is the one name that needs no body[data-theme=...] block.
    #[test]
    fn every_theme_but_the_root_one_has_a_rule_in_the_stylesheet() {
        let css: String = DIST
            .get_dir("assets")
            .expect("built assets")
            .files()
            .filter(|f| f.path().extension().is_some_and(|e| e == "css"))
            .map(|f| String::from_utf8_lossy(f.contents()).into_owned())
            .collect();
        assert!(!css.is_empty(), "no stylesheet in the bundle");
        for t in THEMES.iter().filter(|t| **t != "dashy") {
            // the bundler drops the quotes: [data-theme=pencil], not [data-theme="pencil"]
            assert!(
                css.contains(&format!("data-theme={t}")) || css.contains(&format!(r#"data-theme="{t}""#)),
                "theme {t} has no rule in the stylesheet"
            );
        }
    }

    #[test]
    fn settings_change_the_theme_and_persist() {
        let _guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("settings.json");
        config::update(|c| {
            c.settings = Some(file.clone());
            c.theme = "dashy".into();
            c.interval = 300;
        });
        let (base, token, state) = served();
        let (code, d) = post(
            &format!("{base}/api/settings"),
            json!({"theme": "nord", "interval": 60, "voice": ["bot", "review"]}),
            &token,
        );
        assert_eq!((code, d["ok"].as_bool()), (200, Some(true)));
        let c = config::get();
        assert_eq!((c.theme.as_str(), c.interval), ("nord", 60));
        assert_eq!(c.voice, vec!["review", "bot"]); // ponytail: rebuilt in option order
        assert!(state.lock().wake.is_set()); // a shorter interval must not wait out the longer one
        let saved: Value = serde_json::from_str(&std::fs::read_to_string(&file).unwrap()).unwrap();
        assert_eq!(
            (saved["theme"].as_str(), saved["interval"].as_u64()),
            (Some("nord"), Some(60))
        );
        // junk off the wire never lands
        for body in [
            json!({"interval": 0}),
            json!({"interval": "soon"}),
            json!({"model": ""}),
            json!({"theme": "neon"}),
            json!({"voice": []}),
            json!({"window": 5}),
        ] {
            assert_eq!(post(&format!("{base}/api/settings"), body, &token).0, 400);
        }
        assert_eq!(config::get().theme, "nord");
        let d = get(&format!("{base}/api/state"), Some(&token)).1;
        assert_eq!(d["settings"]["theme"], "nord");
        // The welcome hint is remembered HERE, not in the webview: its origin is a new random port
        // every launch, so a localStorage flag would show the hint again on every open.
        assert_eq!(d["settings"]["hinted"], json!(false));
        post(&format!("{base}/api/settings"), json!({"hinted": true}), &token);
        let d = get(&format!("{base}/api/state"), Some(&token)).1;
        assert_eq!(d["settings"]["hinted"], json!(true));
        assert_eq!(d["options"]["interval"], json!(config::INTERVALS));
    }

    #[test]
    fn consent_answers_one_ask_and_drops_it() {
        let (base, token, state) = served();
        state.lock().asks = vec![
            json!({"kind": "publishing", "key": "t1"}),
            json!({"kind": "agents", "key": "t1", "text": "do x"}),
        ];
        post(
            &format!("{base}/api/consent"),
            json!({"kind": "publishing", "key": "t1", "yes": false}),
            &token,
        );
        let d = get(&format!("{base}/api/state"), Some(&token)).1;
        assert_eq!(
            d["asks"],
            json!([{"kind": "agents", "key": "t1", "text": "do x"}])
        );
        assert_eq!(
            post(
                &format!("{base}/api/consent"),
                json!({"kind": "odd", "key": "t1"}),
                &token
            )
            .0,
            400
        );
    }

    #[test]
    fn code_rows_keep_every_mark_on_a_line_or_as_an_orphan() {
        let line = |n: u32, sign: &str, text: &str| Line {
            n: Some(n),
            sign: sign.into(),
            text: text.into(),
            del: None,
            marks: vec![],
        };
        let files = vec![DiffFile {
            path: "x.py".into(),
            add: 1,
            dele: 0,
            hunks: vec![Hunk {
                header: "@@ -1,2 +1,3 @@".into(),
                start: 1,
                lines: vec![line(1, " ", "a"), line(2, "+", "b"), line(3, " ", "c")],
            }],
        }];
        let marks = vec![
            Mark {
                kind: "note".into(),
                loc: "x.py:2".into(),
                text: "on b".into(),
                path: "x.py".into(),
                n: 2,
                file: 0,
            },
            Mark {
                kind: "nit".into(),
                loc: "y.py:1".into(),
                text: "elsewhere".into(),
                path: "y.py".into(),
                n: 1,
                file: usize::MAX,
            },
        ];
        let kinds: Vec<&str> = code_rows(&files, &marks, true)
            .iter()
            .map(|r| r["kind"].as_str().unwrap().to_string())
            .collect::<Vec<_>>()
            .leak()
            .iter()
            .map(String::as_str)
            .collect();
        assert_eq!(
            kinds,
            ["file", "hunk", "line", "line", "note", "line", "gap", "orphan"]
        );
        let rows = code_rows(&files, &marks, true);
        assert_eq!(rows[7]["why"], "not in this diff");
        let plain: Vec<String> = code_rows(&files, &marks, false)
            .iter()
            .map(|r| r["kind"].as_str().unwrap().to_string())
            .collect();
        assert_eq!(plain, ["file", "hunk", "line", "line", "line", "gap"]);
    }

    #[test]
    fn dream_result_promises_only_what_the_apply_will_do() {
        // The rows, the deletion count and the full diff all came off the raw answer, so the page
        // could show a team file losing nine lines and then not touch it. `v` is the view somebody
        // opens because they want to be careful, which makes it the worst place to say that.
        let before = vec![
            ("mine/general.md".to_string(), "- mine\n".to_string()),
            (
                "team:org-t/general.md".to_string(),
                (0..9).map(|i| format!("- theirs {i}\n")).collect::<String>(),
            ),
        ];
        let new = vec![
            ("mine/general.md".to_string(), "- mine, tidied\n".to_string()),
            ("team:org-t/general.md".to_string(), String::new()),
        ];
        let r = dream_result(("tidy".into(), before, new));
        let names: Vec<&str> = r["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| f["name"].as_str().unwrap())
            .collect();
        assert_eq!(names, ["mine/general"]);
        assert_eq!(r["lost"].as_u64(), Some(0)); // the team's emptied file is not a deletion
        assert_eq!(r["theirs"].as_u64(), Some(1)); // and the page says it was read and left alone
        let detail = r["detail"].as_str().unwrap();
        assert!(detail.contains("mine, tidied"), "{detail}");
        assert!(!detail.contains("theirs"), "{detail}");
        assert!(!r["new"]
            .as_object()
            .unwrap()
            .contains_key("team:org-t/general.md"));
    }

    #[test]
    fn dream_result_counts_what_is_lost_and_hides_the_bodies_from_the_page() {
        let before = vec![
            ("mine/a.md".to_string(), "x\ny\n".to_string()),
            ("mine/general.md".to_string(), "g\n".to_string()),
        ];
        let new = vec![
            ("mine/a.md".to_string(), "x\n".to_string()),
            ("mine/general.md".to_string(), String::new()),
        ];
        let r = dream_result(("tidy".into(), before, new));
        assert_eq!(
            (r["summary"].as_str(), r["lost"].as_u64()),
            (Some("tidy"), Some(1))
        );
        let files: Vec<(String, bool)> = r["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| {
                (
                    f["name"].as_str().unwrap().into(),
                    f["deleted"].as_bool().unwrap(),
                )
            })
            .collect();
        assert_eq!(
            files,
            [("mine/general".to_string(), true), ("mine/a".to_string(), false)]
        );
        assert!(r["detail"].as_str().unwrap().contains("-y"));
        assert!(r["new"].is_object());
        assert_eq!(
            dream_detail(
                "s",
                &[("a.md".into(), "x\n".into())],
                &[("a.md".into(), "x\n".into())]
            )
            .trim(),
            "s"
        );
    }

    #[test]
    fn jobs_run_once_at_a_time_per_name_and_report_errors() {
        start_job("test-fail", || anyhow::bail!("boom\nlast line"));
        for _ in 0..200 {
            if !job("test-fail")["running"].as_bool().unwrap() {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        let j = job("test-fail");
        assert_eq!(
            (j["running"].as_bool(), j["error"].as_str()),
            (Some(false), Some("last line"))
        );
        assert_eq!(job("never")["idle"], true);
        start_job("test-ok", || Ok(json!({"n": 1})));
        for _ in 0..200 {
            if !job("test-ok")["running"].as_bool().unwrap() {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(job("test-ok")["result"]["n"], 1);
    }

    /// The catch_unwind in start_job only does anything while the release profile unwinds: under
    /// `panic = "abort"` this passed in dev and the shipped binary took the whole app down instead.
    #[test]
    fn a_panicking_job_leaves_the_panel_readable_instead_of_killing_the_app() {
        let hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {})); // ponytail: the panic is the point, keep it off the log
        start_job("test-panic", || panic!("the dream went wrong"));
        for _ in 0..200 {
            if !job("test-panic")["running"].as_bool().unwrap() {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        std::panic::set_hook(hook);
        let j = job("test-panic");
        assert_eq!(j["running"].as_bool(), Some(false));
        assert_eq!(j["error"].as_str(), Some("the dream went wrong"));
    }

    #[test]
    fn hosts_and_queries_parse_like_python() {
        assert_eq!(host_of("127.0.0.1:8080"), "127.0.0.1");
        assert_eq!(host_of("localhost"), "localhost");
        assert_eq!(host_of("[::1]:80"), "::1");
        assert_eq!(host_of("evil.example.com:80"), "evil.example.com");
        let qs = parse_query("url=https%3A%2F%2Fx%2F1&scope=marks&a=b+c");
        assert_eq!(
            (q(&qs, "url"), q(&qs, "scope"), q(&qs, "a"), q(&qs, "none")),
            ("https://x/1", "marks", "b c", "")
        );
    }
}
