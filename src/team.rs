//! Team checkouts under ~/.prs_teams/<slug>: git as the sync server. Port of dashy/core/team.py.
//!
//! ponytail: git is the sync server. Appends merge with the union driver, so parallel reviews never conflict.

use std::collections::HashMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::{bind, config, github};

/// Seconds a clone or repo-create may take before we give up on it.
pub const CLONE_TIMEOUT: u64 = 300;
/// The branch a team gitdashy STARTS uses; a team it clones keeps its own.
pub const BRANCH: &str = "main";
/// Inside the checkout: the team's own name and description, shared with everyone.
pub const INFO: &str = "team.json";
/// Under a checkout's .git: why its last pull did not land, "" when it did.
pub const FAILED: &str = "dashy-pull-failed";
/// ponytail: confirm() wraps a message as " {err}  [any key]" and draw() hard-clips the footer at
/// w - 1, so 66 is what survives an 80-column terminal. Not a guess: the fix this replaces was 181
/// characters and its advice fell off the end of the line.
pub const FOOTER: usize = 66;

/// Last git failure, shown in the header until the next success.
pub static ERROR: Mutex<String> = Mutex::new(String::new());
/// The joined team keys, comma-joined, for the header strip only: resolution goes by key.
pub static NAME: Mutex<String> = Mutex::new(String::new());
/// Review threads push concurrently; git wants one writer.
static LOCK: Mutex<()> = Mutex::new(());
/// d -> why. ponytail: cached, or every write pays a rev-parse forever: is_repo can never become true
/// for a directory we have decided not to initialise.
static NO_HISTORY: Mutex<Option<HashMap<PathBuf, String>>> = Mutex::new(None);

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Info {
    pub name: String,
    pub description: String,
}

/// What a finished git command said.
#[derive(Clone, Debug, Default)]
pub struct Out {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

impl Out {
    fn ok(&self) -> bool {
        self.code == 0
    }
    fn text(&self) -> String {
        if self.stderr.trim().is_empty() {
            self.stdout.trim().to_string()
        } else {
            self.stderr.trim().to_string()
        }
    }
    fn last_line(&self) -> String {
        self.text().lines().last().unwrap_or("").to_string()
    }
}

fn error() -> String {
    ERROR.lock().unwrap_or_else(|e| e.into_inner()).clone()
}

fn set_error(s: String) {
    *ERROR.lock().unwrap_or_else(|e| e.into_inner()) = s;
}

fn lock() -> std::sync::MutexGuard<'static, ()> {
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

/// Run a command that talks to a remote, and never let it wait on a human.
///
/// ponytail: a URL to a private repo makes git ask for a password. Inside curses that prompt is invisible
/// and blocks the whole dashboard forever, so prompts are off and the call is bounded: fail, don't hang.
fn remote(cmd: &[&str], extra_env: Option<&HashMap<String, String>>, timeout: Option<u64>) -> Out {
    let timeout = timeout.unwrap_or(CLONE_TIMEOUT);
    let mut c = Command::new(cmd[0]);
    c.args(&cmd[1..]);
    // ponytail: LC_ALL=C because we MATCH on git's stderr: "could not read ", "Authentication failed".
    // On a localized machine those strings never appear, the auth branch never fires, and the user gets
    // back the clipped fatal this whole path exists to replace. Parsing output means pinning its locale.
    c.env("GIT_TERMINAL_PROMPT", "0")
        .env("LC_ALL", "C")
        .env("LANGUAGE", "");
    if std::env::var_os("GIT_SSH_COMMAND").is_none() {
        c.env("GIT_SSH_COMMAND", "ssh -oBatchMode=yes"); // keeps a user's own setting if they have one
    }
    if let Some(env) = extra_env {
        c.envs(env);
    }
    c.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = match c.spawn() {
        Ok(ch) => ch,
        Err(e) => {
            let why = e.to_string();
            let why = why.split(" (os error").next().unwrap_or(&why).to_string();
            return Out {
                code: 1,
                stdout: String::new(),
                stderr: format!("{why}: {}", cmd[0]),
            };
        }
    };
    let out = drain(child.stdout.take());
    let err = drain(child.stderr.take());
    let started = Instant::now();
    let code = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.code().unwrap_or(1),
            Ok(None) if started.elapsed() >= Duration::from_secs(timeout) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = out.join();
                let _ = err.join();
                // ponytail: reason first: note() keeps the first 60 chars for the header.
                return Out {
                    code: 1,
                    stdout: String::new(),
                    stderr: format!("timed out after {timeout}s waiting on the remote"),
                };
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(20)),
            Err(e) => {
                let _ = child.kill();
                return Out {
                    code: 1,
                    stdout: String::new(),
                    stderr: format!("{e}: {}", cmd[0]),
                };
            }
        }
    };
    Out {
        code,
        stdout: out.join().unwrap_or_default(),
        stderr: err.join().unwrap_or_default(),
    }
}

/// Read a child's pipe to the end on its own thread, so a chatty git never fills it and stalls.
fn drain<R: Read + Send + 'static>(mut r: Option<R>) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(r) = r.as_mut() {
            let _ = r.read_to_end(&mut buf);
        }
        String::from_utf8_lossy(&buf).into_owned()
    })
}

pub fn is_repo(d: &Path) -> bool {
    !d.as_os_str().is_empty() && d.join(".git").is_dir() // "" (demo) is never a repo
}

/// True when this machine has joined any team. ponytail: plural now: `joined()` is the real answer,
/// and this stays because a dozen call sites only ever asked the yes/no.
pub fn on() -> bool {
    !joined().is_empty()
}

/// ponytail: same protection as a clone. These are the calls that run on every refresh tick, from the
/// daemon thread: a pull that stops to ask for a credential would hang the dashboard with nothing on
/// screen to say why, which is the whole reason remote() exists.
/// ponytail: cwd is REQUIRED. With several checkouts there is no default; a silent wrong-directory git
/// call is worse than an error at the call site.
fn git(cwd: &Path, args: &[&str]) -> Out {
    let cwd = cwd.to_string_lossy();
    let mut cmd = vec!["git", "-C", &cwd];
    cmd.extend_from_slice(args);
    remote(&cmd, None, Some(120))
}

fn note(r: &Out, label: &str) -> bool {
    if r.ok() {
        set_error(String::new());
    } else {
        let last = r.last_line();
        let last = if last.is_empty() {
            "git failed".to_string()
        } else {
            last
        };
        set_error(format!("{label}: {}", last.chars().take(60).collect::<String>()));
    }
    r.ok()
}

/// Record on the CHECKOUT itself why its last pull did not land, or clear it when one does.
///
/// ponytail: on the checkout, not in a module global. `ERROR` is last-writer-wins across every git
/// call in one process, so one team's failure was pinned on every team's line and a later team's
/// success wiped it, and it is a process global, so a session hook rewriting the mirror in a fresh
/// process had ERROR == "" and reported "last pulled just now" over a pull that had failed elsewhere.
/// This is a file beside FETCH_HEAD, which every reader can stat and no other process can contradict.
/// ponytail: never raises. A checkout we cannot write a marker into is not a reason to fail the pull
/// that already happened; the age alone is what the header falls back to.
fn mark_pull(d: &Path, why: &str) {
    let p = d.join(".git").join(FAILED);
    if !why.is_empty() {
        let _ = std::fs::write(&p, why.chars().take(200).collect::<String>());
    } else if p.exists() {
        let _ = std::fs::remove_file(&p);
    }
}

/// Why `d`'s last pull did not land, "" when it did or when nothing has tried.
pub fn pull_failed(d: &Path) -> String {
    std::fs::read_to_string(d.join(".git").join(FAILED))
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

/// When `d` last reached its remote, unix time. None when never or no remote.
///
/// ponytail: FETCH_HEAD, not a commit date. It measures when the team was last reached; a pull that
/// found nothing new still rewrites it. A commit date measures when the team last said something, and
/// reads as weeks stale on a team that is simply quiet.
/// ponytail: a stat, not a subprocess, and the .git DIRECTORY is checked here rather than left to
/// has_remote, which falls back to spawning git when .git is a file. This is read on the mirror path,
/// which a SessionStart hook calls inside a ten-second budget, so "not a subprocess" has to be true of
/// every shape of checkout and not only the common one. A worktree reports no age, which is the same
/// answer a checkout that has never been pulled gives.
pub fn fetched_at(d: &Path) -> Option<f64> {
    if !d.join(".git").is_dir() || !has_remote(d) {
        return None;
    }
    let m = std::fs::metadata(d.join(".git").join("FETCH_HEAD"))
        .ok()?
        .modified()
        .ok()?;
    let t = m.duration_since(std::time::UNIX_EPOCH).ok()?;
    Some(t.as_secs_f64())
}

/// True when the checkout at `d` has an origin.
///
/// ponytail: reads .git/config rather than spawning git. This is on the refresh tick now, and a
/// subprocess per tick to learn something that changes about once in a checkout's life is waste,
/// and `git remote get-url` does not go through remote(), so putting it on the tick path would have
/// quietly broken the invariant that every git call there is bounded and cannot prompt.
/// ponytail: a .git that is a FILE is a worktree or a submodule; fall back to asking git rather than
/// guessing from a path that does not exist.
pub fn has_remote(d: &Path) -> bool {
    let g = d.join(".git");
    if g.is_dir() {
        return std::fs::read_to_string(g.join("config"))
            .map(|t| t.contains("[remote \"origin\"]"))
            .unwrap_or(false);
    }
    if g.exists() {
        !url(d).is_empty()
    } else {
        false
    }
}

/// The origin URL at `d` from .git/config, "" when none. Spawns nothing.
///
/// ponytail: for the DRAW PATH. has_remote reads this same file for exactly this reason, and `url`
/// does not go through remote(), so its 60s timeout is unbounded from a panel loop that redraws several
/// times a second, once per joined team.
/// ponytail: a .git that is a FILE is a worktree or a submodule, and its config lives elsewhere; ask git
/// there rather than guess from a path that does not exist. Same fallback has_remote makes.
pub fn origin_url(d: &Path) -> String {
    let g = d.join(".git");
    if !g.is_dir() {
        return if !d.as_os_str().is_empty() && g.exists() {
            url(d)
        } else {
            String::new()
        };
    }
    let Ok(text) = std::fs::read_to_string(g.join("config")) else {
        return String::new();
    };
    let mut section = "";
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            section = line;
        } else if section == "[remote \"origin\"]" && line.replace(' ', "").starts_with("url=") {
            return line
                .split_once('=')
                .map(|(_, v)| v.trim().to_string())
                .unwrap_or_default();
        }
    }
    String::new()
}

/// `url` with any user:password taken out of the authority, whatever the scheme. For DISPLAY.
///
/// ponytail: bare_url answers only for http(s) and returns "" for anything else, because its callers
/// want the ssh FORM back and there is no form to suggest otherwise. This one is the other job: it
/// always returns something to show, and it covers every scheme: `ssh://u:pw@host/o/r` printed its
/// password verbatim through every caller that fell back to the raw URL.
/// ponytail: the scp-like form (git@host:o/r) has a user and no password slot, so it is returned as is.
pub fn redacted(url: &str) -> String {
    let Some((scheme, rest)) = url.trim().split_once("://") else {
        return url.to_string();
    };
    let (authority, tail) = match rest.split_once('/') {
        Some((a, p)) => (a, format!("/{p}")),
        None => (rest, String::new()),
    };
    let authority = authority.rsplit_once('@').map(|(_, h)| h).unwrap_or(authority);
    format!("{scheme}://{authority}{tail}")
}

/// The `-c` identity pair, and ONLY when the machine has none of its own.
///
/// ponytail: command-line -c has the highest precedence in git, so passing it unconditionally did not
/// fall back to the user's identity, it REPLACED it. push_dir is how the shared team repo commits, so
/// every shared fact and every reviewed.jsonl append landed as "gitdashy" for every member: pushed,
/// and not rewritable afterwards. Attribution there is the whole point: who wrote a fact is who you go
/// and ask about it.
/// ponytail: each half is asked for SEPARATELY and only the missing one is supplied. Checking email
/// alone meant a config with an email and no name (user.useConfigOnly, or an empty gecos field)
/// got nothing, and the commit failed where the unconditional pair had worked. A fallback that only
/// fires all-or-nothing is not a fallback for a half-configured machine.
/// ponytail: `git config` reads config files. Local, bounded, and it cannot prompt.
fn ident(d: &Path) -> Vec<String> {
    let mut out = Vec::new();
    for (key, val) in [("user.name", "gitdashy"), ("user.email", "gitdashy@localhost")] {
        let r = git(d, &["config", key]);
        if !r.ok() || r.stdout.trim().is_empty() {
            out.push("-c".to_string());
            out.push(format!("{key}={val}"));
        }
    }
    out
}

/// `git <ident> <args>` at `d`.
fn git_as(d: &Path, args: &[&str]) -> Out {
    let id = ident(d);
    let mut all: Vec<&str> = id.iter().map(String::as_str).collect();
    all.extend_from_slice(args);
    git(d, &all)
}

fn realpath(p: &Path) -> PathBuf {
    p.canonicalize().unwrap_or_else(|_| p.to_path_buf())
}

/// True when `d` sits inside a git repo that is not `d` itself.
///
/// ponytail: `git init` there would nest a repo inside someone's notes or dotfiles checkout, which
/// surprises their tooling and is not ours to do. is_repo only looks for .git in the directory itself,
/// so it cannot see this.
pub fn inside_other_repo(d: &Path) -> bool {
    let r = git(d, &["rev-parse", "--show-toplevel"]);
    let top = r.stdout.trim();
    r.ok() && !top.is_empty() && realpath(Path::new(top)) != realpath(d)
}

fn no_history_get(d: &Path) -> Option<String> {
    NO_HISTORY
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .as_ref()
        .and_then(|m| m.get(d).cloned())
}

fn no_history_set(d: &Path, why: &str) {
    NO_HISTORY
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get_or_insert_with(HashMap::new)
        .insert(d.to_path_buf(), why.into());
}

/// Why `d` has no history and will not get any, "" otherwise.
pub fn no_history(d: &Path) -> String {
    if is_repo(d) {
        String::new()
    } else {
        no_history_get(d).unwrap_or_default()
    }
}

/// Give `d` local git history, no remote needed. True when it has one. Never raises.
///
/// ponytail: memory is the one thing here that cannot be recreated: a dream rewrites every file and
/// deletes any the model returned empty, and on a default install ~/.prs_memory was a plain directory
/// with no history, no remote and no snapshot. `git init` costs nothing and makes every write in the
/// system undoable with commands the user already knows.
/// ponytail: identity comes from -c, not from their global config. A machine that has never set
/// user.email would otherwise fail to commit, which is exactly the machine with no other backup.
pub fn init_history(d: &Path) -> bool {
    if is_repo(d) {
        return true;
    }
    if d.as_os_str().is_empty() || !d.is_dir() {
        return false;
    }
    if no_history_get(d).is_some() {
        return false;
    }
    if inside_other_repo(d) {
        // ponytail: `git init ~` is a normal dotfiles setup, which makes the DEFAULT ~/.prs_memory
        // nested, so this is not the exotic case the docs framed it as. Recorded rather than
        // discarded, so the Memory row can say the net is off instead of it being silently absent.
        no_history_set(d, "inside another git repo");
        return false;
    }
    {
        let _g = lock();
        if !git(d, &["init", "-q"]).ok() {
            no_history_set(d, "git init failed");
            return false;
        }
        git(d, &["add", "-A"]);
        git_as(
            d,
            &[
                "commit",
                "-qm",
                "gitdashy: memory as it was before this was tracked",
            ],
        );
    }
    is_repo(d)
}

/// `pull --rebase`, and never leave a rebase behind. True when it merged. Caller holds LOCK.
///
/// ponytail: `refspec` is an explicit refspec for connect(), which runs before any upstream is set:
/// `remote add` does not set one, so a bare `pull --rebase` there fails on "no tracking information"
/// rather than on anything about the history. Same function either way: the abort is the point.
///
/// ponytail: team.json is JSON, not append-only, so it is not union-merged: two people describing the
/// team at once conflict. A pull that stopped mid-rebase STAYED that way: every later pull and push
/// failed on it, leave refused the dirty tree, and the checkout was wedged until someone ran
/// `git rebase --abort` by hand. Aborting puts HEAD back on the local commit with a clean tree, so
/// nothing is lost and the next tick starts from a state git can work with.
fn pull_locked(d: &Path, label: &str, refspec: &[&str]) -> bool {
    let mut args = vec!["pull", "--rebase", "-q"];
    args.extend_from_slice(refspec);
    if note(&git(d, &args), label) {
        mark_pull(d, "");
        return true;
    }
    git(d, &["rebase", "--abort"]); // ponytail: a no-op when none is in progress; ERROR keeps the pull's reason
    mark_pull(d, &error());
    false
}

/// pull --rebase at `d`. True when it merged.
/// ponytail: git is the sync server for any checkout, not just the team's: the private one uses it too.
pub fn pull_dir(d: &Path, label: &str) -> bool {
    if is_repo(d) && has_remote(d) {
        // ponytail: local-only history has nothing to pull and no error to show
        let _g = lock();
        return pull_locked(d, label, &[]);
    }
    false
}

/// Commit, and push when there is somewhere to push to. "" or why not.
///
/// ponytail: the commit is the point, not the push. A memory dir with local-only history must record
/// every change (that is what makes a bad dream recoverable) and a missing origin is not an error to
/// put on the header, it is the normal state of a machine that has not joined anything.
pub fn push_dir(d: &Path, msg: &str, label: &str) -> String {
    // ponytail: returns "" or WHY it did nothing. It used to return None on every path, so a commit that
    // failed was indistinguishable from one that was not needed. A dream emptied general.md, its commit
    // did not happen, and the deletion sat uncommitted until an unrelated review's push swept it in under
    // that review's message, so `git log` blamed a review for a dream's damage. A caller that is about
    // to destroy something has to be able to ask whether the record of it was actually written.
    if !is_repo(d) {
        return "not a git checkout".into();
    }
    {
        let _g = lock();
        git(d, &["add", "-A"]);
        if git(d, &["diff", "--cached", "--quiet"]).ok() {
            return String::new(); // nothing new
        }
        if !note(&git_as(d, &["commit", "-qm", msg]), label) {
            let e = error();
            return if e.is_empty() { "commit failed".into() } else { e };
        }
    }
    if !has_remote(d) {
        return String::new(); // ponytail: committed, which is the half that protects you. Nothing to push to.
    }
    let _g = lock();
    // rejected: someone pushed first, merge and retry
    let pushed = note(&git(d, &["push", "-q", "-u", "origin", "HEAD"]), label)
        || (pull_locked(d, label, &[]) && note(&git(d, &["push", "-q"]), label));
    if !pushed {
        // ponytail: names the PUSH, with the last git message after it. ERROR alone was whichever
        // of the two failed, so a pull that failed reported itself as the push's reason.
        let e = error();
        return if e.is_empty() {
            "push failed".into()
        } else {
            format!("push failed: {e}")
        }; // committed locally, nothing lost
    }
    String::new()
}

/// Pull every joined team. ponytail: one failing does not stop the rest: note() keeps the last reason.
pub fn pull() {
    for d in dirs() {
        pull_dir(&d, "sync");
    }
}

/// Commit and push every joined team. "" or the first reason one did not.
///
/// ponytail: passes the reason up so a caller that deletes can check it, but NOT being in a team is
/// the normal state, not a failure. Returning "not a git checkout" from here made the dream warn that
/// memory was uncommitted every single time, on a machine with no team, which is a warning nobody would
/// read twice.
pub fn push(msg: &str) -> String {
    dirs()
        .into_iter()
        .map(|d| push_dir(&d, msg, "sync"))
        .find(|e| !e.is_empty())
        .unwrap_or_default()
}

/// owner/name from a remote URL, path or owner/name. "" when there is nothing to read.
pub fn slug_of(url: &str) -> String {
    let u = url.trim().trim_end_matches('/');
    let u = u.strip_suffix(".git").unwrap_or(u).replace(':', "/");
    if u.is_empty() {
        return String::new();
    }
    let parts: Vec<&str> = u.split('/').collect();
    let n = parts.len();
    parts[n.saturating_sub(2)..].join("/")
}

/// The host a remote URL names, "" for a bare owner/name or a local path.
pub fn host_of(url: &str) -> String {
    let mut u = url.trim();
    for p in ["ssh://", "https://", "http://"] {
        u = u.strip_prefix(p).unwrap_or(u);
    }
    let u = u.rsplit('@').next().unwrap_or("");
    let u = u.replace(':', "/");
    let head = u.split('/').next().unwrap_or("");
    if head.contains('.') {
        head.to_lowercase()
    } else {
        String::new()
    }
}

/// Whether two remotes name the same repository.
///
/// ponytail: owner/name alone is not enough: gitlab.com/org/mem and github.com/org/mem share it. Hosts
/// are compared when both carry one, so an ssh URL still matches its own https form.
pub fn same_remote(a: &str, b: &str) -> bool {
    let sa = slug_of(a);
    if sa.is_empty() || sa != slug_of(b) {
        return false;
    }
    let (ha, hb) = (host_of(a), host_of(b));
    ha.is_empty() || hb.is_empty() || ha == hb
}

/// The origin URL at `path`, "" when there is none. ponytail: asked in passing, so it never raises.
fn url(path: &Path) -> String {
    let p = path.to_string_lossy();
    let r = remote(&["git", "-C", &p, "remote", "get-url", "origin"], None, Some(60));
    if r.ok() {
        r.stdout.trim().to_string()
    } else {
        String::new()
    }
}

/// owner/name from the git remote at `path`.
pub fn origin_slug(path: &Path) -> String {
    slug_of(&url(path))
}

/// The directory a team key lives in. ponytail: keys are already filesystem-safe; this is identity.
pub fn dirname(key: &str) -> PathBuf {
    PathBuf::from(key)
}

/// A stable key from a team's name: lowercase, one dash between words. "" when there is no name.
///
/// ponytail: the key is fixed when the team is created and never derived from a remote again. A team
/// is local today and gets a git URL tomorrow: that was the exact moment an origin-derived slug broke,
/// because bindings point at the team and the team had no identity until it was hosted somewhere. The
/// NAME is the identity; the location is a separate, changeable fact.
/// ponytail: the display name lives in team.json and can be edited freely, because it is not this.
pub fn key_of(name: &str) -> String {
    let mut out = String::new();
    let mut dash = false;
    for c in name.trim().to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
            dash = false;
        } else if !dash {
            out.push('-');
            dash = true;
        }
    }
    out.trim_matches('-').chars().take(64).collect()
}

/// team.json as a JSON object, empty when it is missing, unreadable, or not an object.
///
/// ponytail: no checkout, no file. Joining "" and "team.json" gave a RELATIVE path, so a key this
/// machine has not joined read whatever team.json happened to be in the working directory. Harmless
/// while info() was the only caller; covers() and write_info() come through here now, and one of them
/// decides what gets bound on this machine.
fn info_raw(key: &str) -> serde_json::Map<String, serde_json::Value> {
    let Some(d) = dir_of(key) else {
        return Default::default();
    };
    std::fs::read_to_string(d.join(INFO))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| match v {
            serde_json::Value::Object(m) => Some(m),
            _ => None,
        })
        .unwrap_or_default()
}

fn as_text(v: Option<&serde_json::Value>) -> String {
    match v {
        None | Some(serde_json::Value::Null) => String::new(),
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

/// What the team says about itself, from its own checkout. Falls back to the key.
///
/// ponytail: read from the TEAM, not from local config, so everyone who clones it sees the same name
/// and the same description. A checkout written before this file existed still reads: the key is the
/// fallback, and the key is what everything resolves by anyway.
pub fn info(key: &str) -> Info {
    let got = info_raw(key);
    Info {
        name: clean(&as_text(got.get("name")), key),
        description: clean(&as_text(got.get("description")), ""),
    }
}

/// Python's str.isprintable: no control, format or separator characters other than a plain space.
fn printable(c: char) -> bool {
    !c.is_control() && (c == ' ' || !c.is_whitespace()) && c != '\u{200b}' && c != '\u{feff}'
}

/// What the team declares it covers, sorted: ["acme/api", "neomedsys/*"]. [] when nothing.
///
/// ponytail: DECLARED IN THE TEAM, so it clones with it. Bindings are per machine, and a colleague who
/// joined got per-repo seeds from the review log and nothing else: the owner rule that made the team
/// cover an org never left the laptop it was typed on, and two people on one team read different
/// briefs for the same repo with nothing on screen to say so. activate() seeds these into the local
/// store once, exactly as it seeds from the log: visible in `bind --list`, and a --forget still sticks.
/// ponytail: read through clean and bind.target, because this file arrives in a clone. An entry that
/// is not an owner or an owner/name is dropped rather than stored: a claim the resolver could never
/// match is a row that reads as bound to nothing, and a control byte here would reach the listing.
pub fn covers(key: &str) -> Vec<String> {
    let raw = info_raw(key);
    let mut out: Vec<String> = Vec::new();
    if let Some(serde_json::Value::Array(items)) = raw.get("covers") {
        for item in items {
            // ponytail: DROPPED, not repaired. clean strips a control byte and keeps the rest, which here
            // would bind an owner nobody typed; a claim is refused whole or taken whole.
            let t = item.as_str().map(str::trim).unwrap_or("");
            if !t.is_empty() && t.chars().all(printable) && t.chars().count() <= 120 {
                let c = bind::cover_key(t);
                if !c.is_empty() && !out.contains(&c) {
                    out.push(c);
                }
            }
        }
    }
    out.sort();
    out
}

/// One line of printable text, clipped. For anything read out of a team's own files.
///
/// ponytail: team.json comes from a CLONED repo, so anyone with push access to the team writes it, and
/// it lands in the curses header and the CLI listing. A newline or a control byte there is theirs to
/// choose and mine to refuse: this is the chokepoint every reader goes through.
fn clean(v: &str, fallback: &str) -> String {
    let t: String = v.chars().filter(|c| printable(*c)).collect();
    let t: String = t.trim().chars().take(120).collect();
    if t.is_empty() {
        fallback.to_string()
    } else {
        t
    }
}

/// team.json on disk: the key order Python wrote.
#[derive(Serialize)]
struct InfoFile<'a> {
    name: &'a str,
    description: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    covers: Option<&'a [String]>,
}

/// Record what the team calls itself, and what it covers when given. Returns "" or why it could not.
///
/// ponytail: `covers` None KEEPS what is there. Describing a team must not silently drop what it
/// covers, which is what a whole-file rewrite from three arguments would do.
pub fn write_info(key: &str, name: &str, description: &str, covers_: Option<&[String]>) -> String {
    let Some(d) = dir_of(key) else {
        return format!("not in {key}");
    };
    // ponytail: through covers(), not the raw list. team.json arrives in a clone, and copying the raw
    // value back out meant a junk entry someone else pushed survived a rename untouched: the one write
    // that reads the file and rewrites it whole is the write that must not launder it.
    let kept: Vec<String> = match covers_ {
        None => covers(key),
        Some(c) => c.to_vec(),
    };
    let body = InfoFile {
        name: if name.is_empty() { key } else { name },
        description,
        covers: if kept.is_empty() { None } else { Some(&kept) },
    };
    let mut buf = Vec::new();
    let mut ser =
        serde_json::Serializer::with_formatter(&mut buf, serde_json::ser::PrettyFormatter::with_indent(b" "));
    if let Err(e) = body.serialize(&mut ser) {
        return e.to_string();
    }
    buf.push(b'\n');
    match std::fs::write(d.join(INFO), buf) {
        Ok(()) => String::new(),
        Err(e) => e.to_string(),
    }
}

/// Add or remove one coverage claim in team.json and push it. "" or why not.
fn declare(key: &str, target: &str, add: bool) -> String {
    let t = bind::cover_key(target);
    if t.is_empty() {
        return format!("{target:?} is not an owner or an owner/name");
    }
    let Some(d) = dir_of(key) else {
        return format!("not in {key}");
    };
    let now = covers(key);
    // ponytail: `now` is the VALIDATED list, so a claim someone else pushed that is not an owner or an
    // owner/name is dropped by any --cover from here. That is deliberate (an entry the resolver could
    // never match is not a claim, it is noise) but it does mean the list can shrink from a machine that
    // only asked to add one thing, which is worth knowing before you go looking for what removed it.
    if now.contains(&t) == add {
        return String::new(); // already what was asked for
    }
    let it = info(key);
    let next: Vec<String> = if add {
        let mut v = now.clone();
        v.push(t.clone());
        v.sort();
        v.dedup();
        v
    } else {
        now.iter().filter(|c| **c != t).cloned().collect()
    };
    let err = write_info(key, &it.name, &it.description, Some(&next));
    if !err.is_empty() {
        return err;
    }
    // ponytail: shared, so it is pushed. A push that fails is a sync problem on the T row, like any other.
    let verb = if add { "covers" } else { "no longer covers" };
    push_dir(&d, &format!("team: {key} {verb} {t}"), "sync");
    String::new()
}

/// Declare that team `key` covers `target`: an owner ("acme", "acme/*") or an owner/name. "" or why not.
///
/// ponytail: a claim is a disclosure decision for everyone who joins: every review of every repo it
/// names reads this team's brief and facts, and facts about them may be shared here. That is why it is
/// a separate, explicit verb with a keypress of its own, and not a side effect of binding locally.
pub fn cover(key: &str, target: &str) -> String {
    declare(key, target, true)
}

/// Withdraw a claim. The rows it already seeded on each machine stay theirs to change.
pub fn uncover(key: &str, target: &str) -> String {
    declare(key, target, false)
}

/// Every team checkout on this machine, sorted. [] when none.
///
/// ponytail: the FILESYSTEM is the registry, as it is for memory and the store: a directory that
/// happens to hold a .git is a team, exactly as ~/.prs_team being a checkout was "team mode is on".
/// No config file to fall out of step with what is actually on disk.
pub fn joined() -> Vec<String> {
    let teams = config::get().teams;
    let Ok(rd) = std::fs::read_dir(&teams) else {
        return Vec::new();
    };
    let mut names: Vec<String> = rd
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    // ponytail: a dotted name is never a team. setup() clones into TEAMS/.joining, and the moment git
    // creates its .git the refresh thread would walk into a half-cloned checkout and pull inside it.
    names.retain(|n| !n.starts_with('.') && is_repo(&teams.join(n)));
    names
}

/// The checkout for `slug`, None when this machine has not joined it.
///
/// ponytail: folded, as bind.team_dir already is. Everything above this (info, write_info, connect,
/// knowledge.leave, the CLI's --team) answered "not in Org-Mem" about a team you were in, because
/// only the resolver had been taught that a key is typed. Folding here covers all of them at once.
pub fn dir_of(slug: &str) -> Option<PathBuf> {
    if slug.is_empty() {
        return None;
    }
    let want = slug.to_lowercase();
    let teams = config::get().teams;
    joined()
        .into_iter()
        .find(|n| n.to_lowercase() == want)
        .map(|n| teams.join(n))
}

/// Every joined team's checkout directory.
pub fn dirs() -> Vec<PathBuf> {
    let teams = config::get().teams;
    joined().iter().map(|s| teams.join(dirname(s))).collect()
}

/// Where reviews of `slug`'s repos are logged; your own log for "".
///
/// ponytail: config.log for yours, not local_log. They are the same path in a real run, and NOT
/// the same under --demo or a test, both of which point the log somewhere throwaway. Reading the
/// constant sent test writes to the real ~/.prs_reviewed.jsonl, which is the one file in this system
/// nobody would think to check.
pub fn log_of(slug: &str) -> PathBuf {
    match dir_of(slug) {
        Some(d) => d.join("reviewed.jsonl"),
        None => config::get().log,
    }
}

/// Move a pre-plural ~/.prs_team into ~/.prs_teams/<slug>/. One-line report or "".
///
/// ponytail: this is an automatic move of a directory holding somebody's unpushed work, at startup,
/// inside curses: the class of operation that has destroyed data twice in this repo. So it refuses on
/// everything it cannot prove safe rather than trying to cope: no slug to key it by, a destination that
/// already exists, uncommitted or unpushed work, or a home it cannot create.
/// ponytail: rename, never a copy-then-delete. A failure leaves the source exactly where it was,
/// and there is no window in which the only copy is half-written.
/// ponytail: never raises. It runs before the first draw; an exception here is a dashboard that never
/// appears, over a directory the user could have moved by hand.
pub fn migrate() -> String {
    let c = config::get();
    let src = c.team;
    let teams = c.teams;
    if !is_repo(&src) || teams.as_os_str().is_empty() {
        return String::new(); // nothing to migrate, which is every machine that installed after this
    }
    // ponytail: the old layout had no name of its own, so the origin is the only thing that can name it,
    // through key_of, because a key is a directory name and owner/name has a slash in it.
    let was = origin_slug(&src); // ponytail: EXACTLY what the shipped version wrote into every binding
    let key = key_of(&was.replace('/', "-"));
    if key.is_empty() {
        return format!(
            "gitdashy: {} has no origin to name it by: move it into {} by hand",
            src.display(),
            teams.display()
        );
    }
    let dest = teams.join(dirname(&key));
    if dest.symlink_metadata().is_ok() {
        return format!(
            "gitdashy: {} already exists: {} was left alone",
            dest.display(),
            src.display()
        );
    }
    // ponytail: the same test knowledge.leave() uses before it deletes anything. A migration that moves
    // a checkout with unpushed reviews in it is a migration that can lose them if the move half-fails.
    let ahead = crate::knowledge::unpushed(&src);
    if ahead != 0 {
        let n = if ahead > 0 {
            ahead.to_string()
        } else {
            "possibly".to_string()
        };
        return format!(
            "gitdashy: {} has {n} unpushed reviews: push them, then restart",
            src.display()
        );
    }
    crate::memory::backup("migrate"); // ponytail: before, not after. Never raises; see memory.backup.
    if let Err(e) = std::fs::create_dir_all(&teams).and_then(|_| std::fs::rename(&src, &dest)) {
        return format!(
            "gitdashy: could not move {} to {}: {e}",
            src.display(),
            dest.display()
        );
    }
    // ponytail: and carry the BINDINGS. The shipped version keyed them on origin_slug ("owner/name",
    // with a slash) and the directory is now "owner-name", so every one of them resolved to nothing:
    // the team's facts stopped being read, brief() said "not in team owner/name" about the team you
    // were in, and pooling and sharing went quiet, with nothing on screen to say so. seed() cannot
    // repair it either, because every bound repo is already in `touched`.
    // ponytail: a rewrite rather than teaching team_dir to also match the old shape: one migration
    // that ends, instead of a fallback that lives in the resolver forever.
    let mut moved = 0;
    for (repo, t) in bind::bindings() {
        if t.to_lowercase() == was.to_lowercase() {
            bind::bind(&repo, &key);
            moved += 1;
        }
    }
    for (owner, t) in bind::owners() {
        if t.to_lowercase() == was.to_lowercase() {
            bind::bind_owner(&owner, &key);
            moved += 1;
        }
    }
    let note = if moved > 0 {
        format!(
            ", and repointed {moved} binding{}",
            if moved == 1 { "" } else { "s" }
        )
    } else {
        String::new()
    };
    format!("gitdashy: moved your team checkout to {}{note}", dest.display())
}

/// Point log + memory at the team checkout. At startup and after setup().
// PORT-NOTE: the Python this ports no longer repoints the log ("the log is PER TEAM now: log.logs()
// reads yours plus every joined one and merges"), so `config.log` is left alone here too; log_of(slug)
// is where a team's log lives.
pub fn activate() {
    if !on() {
        return;
    }
    // ponytail: the log is PER TEAM now: log.logs() reads yours plus every joined one and merges. There
    // is no single config.LOG to point somewhere, which is why that line went rather than moved.
    *NAME.lock().unwrap_or_else(|e| e.into_inner()) = joined().join(", "); // ponytail: for the header only. Resolution goes through the slug.
                                                                           // ponytail: seeding lives HERE, on the one function that says "a team is now known", rather than at
                                                                           // each of the six entry points that call it: a bootstrap only some callers perform is the bootstrap
                                                                           // that is missing on the path nobody tested.
                                                                           // ponytail: the review log AND the mirror registry. Seeding from the log alone missed the one route
                                                                           // that is not reviewing: a repo wired with `gitdashy init` and never reviewed stayed unbound, so
                                                                           // mirror._write resolved sources(repo) to yours alone and, because a mirror never outlives its
                                                                           // source, DELETED the general.md and repo.md already sitting in that repo on the next refresh tick.
                                                                           // ponytail: but only registry repos the team ALREADY HOLDS FACTS FOR. logged_repos() is disclosure-
                                                                           // neutral by construction: the team can see those names already. The registry is not: it is every
                                                                           // repo `gitdashy init` ever wired, personal side projects included, and binding one makes its facts
                                                                           // poolable and shareable. Seeding the whole registry fixed a deletion by GRANTING DISCLOSURE that
                                                                           // neither of the old rules gave, which is a fix carried past its reason. This set is exactly the old
                                                                           // disclosure test, and exactly the set whose repo.md the mirror would otherwise strip: a repo the
                                                                           // team has no facts about only ever had general.md mirrored, and dropping that IS the new rule.
                                                                           // ponytail: per team, and only for teams that can be seeded unambiguously. With several joined, a
                                                                           // repo in one team's log is that team's; a repo in two is left alone rather than guessed at.
                                                                           // ponytail: what a team DECLARES it covers is NOT seeded here. activate() runs on every command and
                                                                           // every launch, so a `covers` line added to the team repo after you joined would bind owner-wide
                                                                           // rules on your machine with no keypress and nothing that said it happened, and a binding decides
                                                                           // which brief a review reads AND whether facts about those repos may be pooled and shared into that
                                                                           // team. Anyone who can push to the team could then reach repos the team has never held a fact about.
                                                                           // The log seeding beside it is disclosure-neutral by construction; a claim is not. So adoption
                                                                           // happens once, in setup(), where a person chose to trust this team, and a claim that appears
                                                                           // later is shown by `gitdashy teams` and taken with `bind --owner`, which is a keypress.
                                                                           // "Require a keypress where it costs other people" is the rule this is the case for.
    let registered = crate::install::registered();
    for slug in joined() {
        let Some(d) = dir_of(&slug) else { continue };
        let theirs = d.join("memory");
        let known: Vec<String> = registered
            .iter()
            .map(|(_, r, _, _)| r.clone())
            .filter(|r| !r.is_empty() && crate::memory::path(Some(r), Some(&theirs)).exists())
            .collect();
        let mut repos: Vec<String> = crate::memory::logged_repos(Some(&log_of(&slug)))
            .into_iter()
            .collect();
        repos.sort();
        repos.extend(known);
        bind::seed(&slug, &repos);
    }
}

/// Bind what joined teams declare they cover. [(key, target)] for what it wrote.
///
/// ponytail: called from setup(): joining IS the act of trusting a team, so what it already declares
/// comes with it, once, into a store you can read and take back. Not called from activate(): see the
/// note there for why a claim that appears later must not land on its own.
/// ponytail: repo claims are seeded for EVERY team before any owner rule, because the store resolves an
/// exact binding ahead of an owner rule and seeding has to deliver the same order. Doing it per team
/// meant one team's `acme/*` was written first and then `bind.seed` skipped another team's `acme/api`
/// (already resolved through the rule) so which team won a contested repo came down to the
/// alphabetical order of team names.
/// ponytail: a target two joined teams both claim is left alone rather than guessed at, exactly as a
/// repo in two teams' logs is. A claim is a disclosure decision; guessing publishes to the wrong team.
pub fn adopt_covers(only: Option<&str>) -> Vec<(String, String)> {
    let all = joined();
    let mut claims: HashMap<String, Vec<String>> = HashMap::new();
    for slug in &all {
        for t in covers(slug) {
            let who = claims.entry(t).or_default();
            if !who.contains(slug) {
                who.push(slug.clone());
            }
        }
    }
    let mine = |slug: &str| -> Vec<String> {
        let mut v: Vec<String> = claims
            .iter()
            .filter(|(_, who)| who.len() == 1 && who[0] == slug)
            .map(|(t, _)| t.clone())
            .collect();
        v.sort();
        v
    };
    let todo: Vec<&String> = all
        .iter()
        .filter(|s| only.is_none_or(|o| o == s.as_str()))
        .collect();
    let mut wrote: Vec<(String, String)> = Vec::new();
    for s in &todo {
        let repos: Vec<String> = mine(s).into_iter().filter(|t| !t.ends_with("/*")).collect();
        for r in bind::seed(s, &repos) {
            wrote.push(((*s).clone(), r));
        }
    }
    for s in &todo {
        let owners: Vec<String> = mine(s)
            .into_iter()
            .filter(|t| t.ends_with("/*"))
            .map(|t| t[..t.len() - 2].to_string())
            .collect();
        for o in bind::seed_owners(s, &owners) {
            wrote.push(((*s).clone(), format!("{o}/*")));
        }
    }
    wrote
}

fn expanduser(p: &str) -> PathBuf {
    if p == "~" {
        config::home()
    } else if let Some(rest) = p.strip_prefix("~/") {
        config::home().join(rest)
    } else {
        PathBuf::from(p)
    }
}

fn abspath(p: &Path) -> PathBuf {
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|d| d.join(p))
            .unwrap_or_else(|_| p.to_path_buf())
    }
}

/// True when `repo` names a place on this machine rather than a repo on GitHub.
///
/// ponytail: an existing directory always wins; beyond that a leading /, ./, ../ or ~ makes it a path
/// whether or not it exists yet. `os.path.isdir` alone meant a path you were about to CREATE was read
/// as owner/name and handed to gh, which answered "Could not resolve to a Repository": the same
/// defect knowledge.is_remote was fixed for, in the function beside it, left unswept.
pub fn looks_local(repo: &str) -> bool {
    let repo = repo.trim();
    !repo.is_empty()
        && (expanduser(repo).is_dir()
            || repo.starts_with('/')
            || repo.starts_with("./")
            || repo.starts_with("../")
            || repo.starts_with('~'))
}

/// A URL with any user:password stripped out of the authority. "" for anything that is not one.
///
/// ponytail: `https://x-token:ghp_…@host/o/r.git` is a legitimate remote and it reaches every message
/// here. git redacts the password in its own fatal; we were printing it verbatim to the footer and to
/// CLI scrollback, and splicing it into the ssh form we suggested. A credential is not an error detail.
pub fn bare_url(url: &str) -> String {
    let Some((scheme, rest)) = url.trim().split_once("://") else {
        return String::new();
    };
    if scheme != "http" && scheme != "https" {
        return String::new();
    }
    let Some((authority, path)) = rest.split_once('/') else {
        return String::new();
    };
    if path.is_empty() {
        return String::new();
    }
    let authority = authority.rsplit_once('@').map(|(_, h)| h).unwrap_or(authority);
    format!("{scheme}://{authority}/{path}")
}

/// The ssh form of an http(s) URL: "https://host/a/b(.git)" -> "git@host:a/b.git". "" if not one.
///
/// ponytail: host-agnostic on purpose. This is not a GitHub fact; every git host offers both forms,
/// and ssh is the one that authenticates from an agent with nothing else configured.
pub fn ssh_form(url: &str) -> String {
    let u = bare_url(url);
    if u.is_empty() {
        return String::new();
    }
    let rest = u.split_once("://").map(|(_, r)| r).unwrap_or("");
    let (host, path) = rest.split_once('/').unwrap_or((rest, ""));
    if host.is_empty() || path.is_empty() {
        return String::new();
    }
    let path = path.trim_end_matches('/');
    let path = path.strip_suffix(".git").unwrap_or(path);
    format!("git@{host}:{path}.git")
}

/// Clone `repo` into `dest`. Any git URL, a path, or owner/name on GitHub. "" or an error.
pub fn clone(repo: &str, dest: &Path) -> String {
    // a path, a URL or an ssh remote: pass it through
    let local = looks_local(repo) || repo.contains("://") || repo.contains('@');
    // ponytail: git clone, whatever it is. `gh repo clone` was here so that a bare owner/name would
    // work, which quietly made GitHub the only host a team could live on, and a team is just a repo
    // people can reach. A bare owner/name is now expanded to a GitHub URL as a CONVENIENCE, and any
    // other URL, ssh remote or path goes straight through untouched.
    let url = if local {
        repo.to_string()
    } else {
        format!("https://github.com/{repo}.git")
    };
    // ponytail: the token rides along on THAT path only: it is github.com by construction there. A URL
    // is the user's own host and gets nothing, and falls through to the ssh hint below.
    let auth = if local { HashMap::new() } else { github::git_auth() };
    let dest_s = dest.to_string_lossy();
    if note(
        &remote(&["git", "clone", "-q", &url, &dest_s], Some(&auth), None),
        "join",
    ) {
        github::persist_auth(dest); // the env config does not survive the clone; the checkout needs its own
        return String::new();
    }
    // ponytail: git cannot ask. GIT_TERMINAL_PROMPT=0 is deliberate (a credential prompt inside curses
    // is invisible and hangs the dashboard) so an https URL to a PRIVATE repo fails outright on a
    // machine with no credential helper. It used to work because `gh repo clone` carried gh's own
    // token; dropping gh took that with it. The answer is not to reach for a host's CLI again: it is
    // ssh, which every host speaks and which authenticates from an agent already loaded.
    // ponytail: "could not read " covers Username AND Password: git says the second when the URL
    // carries a user, which is the same failure with the same remedy.
    let say = auth_hint(&error(), &url);
    if !say.is_empty() {
        // ponytail: the GLOBAL too. The friendly string was only the return value, so after the popup was
        // dismissed the T row went on painting the clipped fatal until the next successful sync. The row
        // renders ERROR[:40], so a long hint is still cut there: a truncated hint beats a truncated
        // fatal, but FOOTER is not that row's constraint and this does not make it fit.
        set_error(format!("join: {say}"));
        return say;
    }
    error()
}

/// The credential hint when `msg` is git saying it could not authenticate, else "".
fn auth_hint(msg: &str, url: &str) -> String {
    if msg.contains("could not read ") || msg.contains("Authentication failed") {
        credential_hint(url)
    } else {
        String::new()
    }
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// One line, short enough to survive the footer, that says what to do about a missing credential.
fn credential_hint(url: &str) -> String {
    let alt = ssh_form(url);
    if alt.is_empty() {
        // ponytail: already ssh, so there is no other form to suggest, and the answer is different.
        // GIT_SSH_COMMAND carries -oBatchMode=yes, so a key with a passphrase and no agent fails here
        // exactly as an https URL with no helper does, and "check your agent" is the actual remedy.
        let host = host_of(url);
        let host = if host.is_empty() {
            "that remote".to_string()
        } else {
            host
        };
        return clip(
            &format!("no credential for {host}: is your ssh agent loaded?"),
            FOOTER,
        );
    }
    // ponytail: the action alone when the whole sentence will not fit. A reason that pushes the remedy
    // off the end of the line is worse than no reason: that is the bug this is fixing.
    // ponytail: the URL is never clipped. Dropping words to fit is fine (a truncated REMOTE is not a
    // remote, and handing someone an uncopyable one is the same failure as the 181-char message, just
    // rarer). The sentence goes first, then the verb, and the address always survives whole.
    for line in [format!("no credential: try {alt}"), format!("try {alt}")] {
        if line.chars().count() <= FOOTER {
            return line;
        }
    }
    alt
}

/// Make append-only files merge without conflicts, so two people writing at once never collide.
pub fn union_attrs(dest: &Path) {
    let p = dest.join(".gitattributes");
    let have = std::fs::read_to_string(&p).unwrap_or_default();
    if !have.contains("merge=union") {
        use std::io::Write;
        if let Ok(mut f) = std::fs::OpenOptions::new().append(true).create(true).open(&p) {
            let _ = f.write_all(b"*.jsonl merge=union\n*.md merge=union\n");
        }
    }
}

/// Whether `repo` names the directory your memory lives in, or the remote it pushes to.
///
/// ponytail: the mirror of knowledge.adopt's guard: the two must never be the same place, whichever
/// you happen to set up second. Your memory holds drafts and is pushed; the team must never receive them.
pub fn is_own_memory(repo: &str) -> bool {
    let mem = config::get().memory_dir;
    let p = Path::new(repo);
    if p.is_dir() && realpath(p) == realpath(&mem) {
        return true;
    }
    is_repo(&mem) && same_remote(repo, &url(&mem))
}

pub const AGENTS_TEMPLATE: &str = "# For agent sessions working in this team's repos

This file is the team's, not one machine's. It reaches every session in every repo bound to this
team, through that repo's `.agent/team/repo.md` mirror. Reviews never see it: it says how to work
here, which is not something a reviewer should be told about the code it is judging.

## File what you work out

A session that establishes something about the code files it, and it costs one line:

```sh
gitdashy remember \"the viewer owns mask state; the store only mirrors it\"
gitdashy remember --general \"logic that can live in the API does\"
```

It becomes a draft, never a fact. A draft is confirmed only when a review, or a teammate, arrives
at the same thing independently — so file freely. What does not belong: what this task did, one
bug, anything git already records.

Without these, most drafts stay at one observation.
";

pub const PROJECT_TEMPLATE: &str = "# What we are building

Fill this in once, together. Everyone who joins this team reads it, and so does every review —
so a reviewer knows what the code is for before it judges whether a change serves it.

Keep it short. This is intent, not documentation: the things that would change a verdict.

## The project

What it is, and who uses it.

## Why it matters

The outcome that makes the work worth doing.

## Constraints that change decisions

Regulatory, contractual, performance, compatibility — anything with real consequences for
what is acceptable, not just what is tidy.

## How this codebase is shaped

The handful of structural facts a newcomer would otherwise learn the hard way.
";

/// Give a new team repo a brief to fill in. ponytail: never overwrite: theirs is the real one.
pub fn seed_project(path: &Path) {
    if !path.exists() {
        let _ = std::fs::write(path, PROJECT_TEMPLATE);
    }
}

/// Give a new team repo the instruction its members' agent sessions will read.
///
/// ponytail: shipped with the TEAM, not with the machine. The rule that makes a session file drafts
/// lived in one operator's own corpus, so a colleague's sessions never filed any and half the second
/// observers the recurrence gate needs did not exist. A team is the right scope for it: it is the team
/// that wants the drafts, and a team is already a git repo that everyone pulls.
/// ponytail: never overwrites, exactly like the brief. A team that has edited this owns it.
pub fn seed_agents(path: &Path) {
    if !path.exists() {
        let _ = std::fs::write(path, AGENTS_TEMPLATE);
    }
}

/// Remove a team directory or link we just created and could not finish. Never raises.
fn undo(dest: &Path) {
    if let Ok(m) = dest.symlink_metadata() {
        if m.file_type().is_symlink() {
            let _ = std::fs::remove_file(dest); // ponytail: the LINK, never what it points at: that is the user's
        } else if m.is_dir() {
            let _ = std::fs::remove_dir_all(dest);
        }
    }
}

#[cfg(unix)]
fn symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

#[cfg(not(unix))]
fn symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_dir(target, link)
}

/// Start a team here: a checkout, a name, a description. No remote, no host. "" or an error.
///
/// ponytail: nothing external is involved. A team is a place people can reach that pools what reviews
/// learn; git is the only technology it needs, and a remote is something you add when you have one:
/// `connect()`. Requiring a repo to exist first meant the first person on a team was stuck.
/// ponytail: `at` symlinks rather than copies, so a team kept on a shared drive, or inside a repo you
/// already have, stays where it is. The key still names the link, because the key is the identity.
pub fn start(name: &str, description: &str, at: &str) -> String {
    let key = key_of(name);
    if key.is_empty() {
        return "a team needs a name".into();
    }
    let teams = config::get().teams;
    let dest = teams.join(dirname(&key));
    if dest.symlink_metadata().is_ok() {
        return if is_repo(&dest) {
            format!("already in {key}")
        } else {
            format!("{} exists and is not a team", dest.display())
        };
    }
    let made: Result<(), String> = (|| {
        std::fs::create_dir_all(&teams).map_err(|e| e.to_string())?;
        if !at.is_empty() {
            let at = abspath(&expanduser(at));
            // ponytail: an existing checkout is somebody's repo, and write_info would overwrite its
            // team.json with this name. Joining one is `teams --join`; this makes a new team.
            if is_repo(&at) {
                return Err(format!(
                    "{} is already a git checkout: join it with `teams --join` instead",
                    at.display()
                ));
            }
            // ponytail: EMPTY, or not there yet. Only a repo was refused, so `--at ~/dev/neomedsys` (the
            // parent of every checkout on the machine) was accepted, git-inited and committed: thirty
            // repos recorded as gitlinks in one commit, every later team commit re-recording their HEADs,
            // and a leave that refuses forever because the nested repos keep the tree dirty. A directory
            // that already holds something is somebody's; adopting it is not what "keep it somewhere
            // else" meant, and the prompt now says "empty" rather than "a path".
            if at.is_dir()
                && std::fs::read_dir(&at)
                    .map(|mut r| r.next().is_some())
                    .unwrap_or(false)
            {
                return Err(format!(
                    "not empty: a team needs an empty directory, or one that does not exist yet: {}",
                    at.display()
                ));
            }
            std::fs::create_dir_all(&at).map_err(|e| e.to_string())?;
            symlink(&at, &dest).map_err(|e| e.to_string())?;
        } else {
            std::fs::create_dir(&dest).map_err(|e| e.to_string())?;
        }
        std::fs::create_dir_all(dest.join("memory")).map_err(|e| e.to_string())
    })();
    if let Err(e) = made {
        undo(&dest);
        return e;
    }
    if !is_repo(&dest) {
        if !git(&dest, &["init", "-q"]).ok() {
            // ponytail: take the link back out. It is not a repo so joined() skips it, but lexists() is
            // true, so retrying the same name answered "exists and is not a team" forever and the user
            // had to clean up by hand, after an error that said nothing about a leftover.
            undo(&dest);
            return format!("could not git init {}", dest.display());
        }
        // ponytail: PIN the branch. `git init` uses init.defaultBranch, which is "main" on one machine
        // and "master" on the next, so two people starting or connecting the same team push branches
        // that never meet, and a clone of a repo whose HEAD names the other one comes back EMPTY. Found
        // by CI, which has no global git config where mine says main; symbolic-ref rather than `init -b`
        // because it needs no minimum git version.
        git(&dest, &["symbolic-ref", "HEAD", &format!("refs/heads/{BRANCH}")]);
    }
    union_attrs(&dest);
    write_info(&key, name, description, None);
    seed_project(&dest.join("memory").join("project.md"));
    seed_agents(&dest.join("memory").join("agents.md"));
    push_dir(&dest, &format!("gitdashy: new team {name}"), "join");
    String::new()
}

/// Give a local team a remote and push it, or repoint one it already has. "" or an error.
///
/// ponytail: the other half of starting local. You make a repo wherever you keep repos, paste its URL
/// here, and the team you have been using becomes the one everybody pulls, without the key changing,
/// so every binding pointing at it still does.
pub fn connect(key: &str, url_: &str) -> String {
    let Some(d) = dir_of(key) else {
        return format!("not in {key}");
    };
    let url_ = url_.trim();
    if url_.is_empty() {
        return "a remote needs a URL".into();
    }
    let err = cannot_push_into(url_);
    if !err.is_empty() {
        return err;
    }
    let had = if has_remote(&d) { url(&d) } else { String::new() };
    let r = git(
        &d,
        &[
            "remote",
            if had.is_empty() { "add" } else { "set-url" },
            "origin",
            url_,
        ],
    );
    if !r.ok() {
        let t = r.text();
        return if t.is_empty() {
            "could not set the remote".into()
        } else {
            clip(&r.last_line(), 120)
        };
    }
    // ponytail: LOOK before pushing. A remote already holding history that is not this team's is a team
    // to JOIN: the push was rejected as non-fast-forward, the rebase could not even start because
    // `remote add` sets no upstream, and every tick after that painted git's --set-upstream-to hint on
    // the T row. History that IS ours (a mirror pushed by hand, a host moved) fast-forwards, and
    // refusing that would make repointing impossible: ancestry is the test, not emptiness. The fetch
    // goes through git() like every other call here: bounded, and it cannot prompt.
    let (f, foreign_) = {
        let _g = lock();
        let f = git(&d, &["fetch", "-q", "--prune", "origin"]);
        let foreign_ = f.ok() && foreign(&d);
        (f, foreign_)
    };
    if !f.ok() || foreign_ {
        unset(&d, &had);
        if foreign_ {
            // ponytail: through bare_url, like every other message that names a remote. A token in the
            // URL is a legitimate remote and this was a NEW message that skipped the chokepoint.
            let shown = bare_url(url_);
            let shown = if shown.is_empty() { url_.to_string() } else { shown };
            return format!("already has history that is not this team's: `teams --join` it, or connect an empty repo: {shown}");
        }
        let msg = f.last_line();
        let msg = if msg.is_empty() {
            "could not reach it".to_string()
        } else {
            msg
        };
        let hint = auth_hint(&msg, url_);
        return if hint.is_empty() { clip(&msg, FOOTER) } else { hint };
    }
    // ponytail: take what is already there before pushing. Accepting a remote that holds this team's
    // history and is AHEAD of us (a host a colleague has pushed to since) is only half a fix if the
    // push that follows is then rejected as non-fast-forward: the caller gets "connected, but the push
    // failed" and the team is left half-connected, which is the state this whole path exists to avoid.
    let branch = git(&d, &["rev-parse", "--abbrev-ref", "HEAD"])
        .stdout
        .trim()
        .to_string();
    let branch = if branch.is_empty() {
        BRANCH.to_string()
    } else {
        branch
    };
    if git(
        &d,
        &[
            "rev-parse",
            "--verify",
            "-q",
            &format!("refs/remotes/origin/{branch}"),
        ],
    )
    .ok()
    {
        let _g = lock();
        if !pull_locked(&d, "join", &["origin", &branch]) {
            drop(_g);
            unset(&d, &had);
            let e = error();
            return if e.is_empty() {
                "could not merge what is already there".into()
            } else {
                format!("connected, but could not merge what is already there: {e}")
            };
        }
    }
    let err = push_dir(&d, &format!("gitdashy: connect {key}"), "join");
    if !err.is_empty() {
        return err;
    }
    // ponytail: push EXPLICITLY. push_dir returns early when there is nothing new to commit, which is
    // exactly the state a team is in when you connect it: everything was committed locally already. So
    // the remote stayed empty, and the next person to clone it got no team.json, no name, and a key
    // derived from the URL instead of the one every binding points at.
    let _g = lock();
    if !note(&git(&d, &["push", "-q", "-u", "origin", "HEAD"]), "join") {
        let e = error();
        return if e.is_empty() {
            "connected, but the push failed".into()
        } else {
            format!("connected, but the push failed: {e}")
        };
    }
    String::new()
}

/// Put origin back to `had`, or remove it. ponytail: taken back out on every refusal in connect():
/// half-connected, origin set and nothing pushed, is the state that showed a git hint on the row
/// forever, and it is not what the team looked like before the question was asked.
fn unset(d: &Path, had: &str) {
    if had.is_empty() {
        git(d, &["remote", "remove", "origin"]);
    } else {
        git(d, &["remote", "set-url", "origin", had]);
    }
}

/// True when origin holds history that is not this team's: unrelated, not merely different.
///
/// ponytail: ancestry in EITHER direction is ours. A remote AHEAD of us is a host a colleague has
/// pushed to since, which is the normal state of a live team. Testing only "is it an ancestor of
/// HEAD" called that foreign, refused the connect with a message that was false, and took the remote
/// back out. What makes a remote somebody else's is that its commits and ours share no line at all.
fn foreign(d: &Path) -> bool {
    let r = git(
        d,
        &["for-each-ref", "--format=%(objectname)", "refs/remotes/origin"],
    );
    r.stdout.split_whitespace().any(|sha| {
        !git(d, &["merge-base", "--is-ancestor", sha, "HEAD"]).ok()
            && !git(d, &["merge-base", "--is-ancestor", "HEAD", sha]).ok()
    })
}

/// Why a LOCAL path cannot be a team's remote, or "". Only a non-bare checkout on this machine is.
///
/// ponytail: `--new --at /srv/shared/x` and then `--join /srv/shared/x` was the documented shared-drive
/// pairing. The clone works, and every push into it is refused (git will not move the branch a
/// checkout has checked out) so the joiner's first fact failed with "failed to push some refs", and
/// every one after it. A bare copy is what a shared drive needs, and the message says how to make one.
/// `receive.denyCurrentBranch=updateInstead` is the one setting under which a checkout accepts them.
fn cannot_push_into(repo: &str) -> String {
    if !looks_local(repo) {
        return String::new();
    }
    let p = abspath(&expanduser(repo.trim()));
    if !is_repo(&p) {
        return String::new(); // bare, or not a repo at all: git clone says which
    }
    let r = git(&p, &["config", "receive.denyCurrentBranch"]);
    if r.ok() && r.stdout.trim() == "updateInstead" {
        return String::new();
    }
    // ponytail: the remedy first. confirm() clips at the footer, and a long path pushed it off the line.
    format!(
        "a checkout: git refuses pushes into one; share a bare copy: git clone --bare {p} {p}.git",
        p = p.display()
    )
}

/// The key of the joined team whose origin is `repo`, "" when none is.
///
/// ponytail: knowledge.adopt has had this guard since the day memory could be a checkout; setup did not,
/// so `--join` of a repo you were in (under a --name, or after its team.json was renamed) made a
/// second checkout with a second key, and every binding pointed at one of the two. A path is compared
/// as a path: two shares that happen to end in the same two segments are not the same repo.
pub fn already_joined(repo: &str) -> String {
    let local = looks_local(repo);
    let want = if local {
        realpath(&expanduser(repo.trim()))
    } else {
        PathBuf::from(repo)
    };
    for slug in joined() {
        let Some(d) = dir_of(&slug) else { continue };
        let have = url(&d);
        if have.is_empty() {
            continue;
        }
        if local {
            // ponytail: looks_local, which is what the rest of this file trusts to answer "is that a
            // path". Testing for the absence of "@" said no to /srv/team@shared.git, so the duplicate
            // join this exists to stop went through for any path with an @ in it.
            if looks_local(&have) && realpath(&d.join(expanduser(&have))) == want {
                return slug;
            }
        } else if same_remote(repo, &have) {
            return slug;
        }
    }
    String::new()
}

/// Join a team that already exists: clone it and name it locally. Returns "" or an error.
///
/// ponytail: no `create` any more. Making a repo is something you do wherever you keep repos, with
/// whatever host you use; this clones one that exists. `gh repo create` made GitHub the only place a
/// team could be born, which is not what a team is.
/// ponytail: the key comes from the CLONED team.json when it has one, so everybody who joins the same
/// repo agrees on the key their bindings point at. Only a repo that predates team.json needs `name`.
pub fn setup(repo: &str, name: &str) -> String {
    if is_own_memory(repo) {
        return "that is your own memory directory, which holds drafts: use a different repo for the team"
            .into();
    }
    let err = cannot_push_into(repo);
    if !err.is_empty() {
        return err;
    }
    let have = already_joined(repo);
    if !have.is_empty() {
        return format!("already joined that repo, as {have}");
    }
    let teams = config::get().teams;
    let tmp = teams.join(".joining");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(&teams);
    let err = clone(repo, &tmp);
    if !err.is_empty() {
        let _ = std::fs::remove_dir_all(&tmp);
        return err;
    }
    // ponytail: the team's own name first, then what you called it, then the last path segment. A repo
    // that already carries a name must not get a second one because two people typed differently.
    let theirs = std::fs::read_to_string(tmp.join(INFO))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .map(|v| as_text(v.get("name")))
        .unwrap_or_default();
    let key = [
        key_of(&theirs),
        key_of(name),
        key_of(slug_of(repo).rsplit('/').next().unwrap_or("")),
    ]
    .into_iter()
    .find(|k| !k.is_empty())
    .unwrap_or_default();
    if key.is_empty() {
        let _ = std::fs::remove_dir_all(&tmp);
        return "that repo does not name a team: pass a name to call it by".into();
    }
    let dest = teams.join(dirname(&key));
    if dest.symlink_metadata().is_ok() {
        let _ = std::fs::remove_dir_all(&tmp);
        return format!("already in {key}");
    }
    if let Err(e) = std::fs::rename(&tmp, &dest) {
        let _ = std::fs::remove_dir_all(&tmp);
        return e.to_string();
    }
    union_attrs(&dest);
    let _ = std::fs::create_dir_all(dest.join("memory"));
    seed_project(&dest.join("memory").join("project.md"));
    seed_agents(&dest.join("memory").join("agents.md"));
    // ponytail: a repo that predates team.json gets one NOW (the name you gave, or the key that was
    // derived) and the push below carries it. Without it every joiner keyed the same repo by whatever
    // they typed, so two people on one team held two keys, and a binding one of them made meant nothing
    // on the other's machine. start() writes this file; a join is the other way a checkout is born.
    if !dest.join(INFO).exists() {
        write_info(&key, if name.is_empty() { &key } else { name }, "", None);
    }
    // ponytail: your own log is NOT copied in. It was, back when "a team" was singular and your log
    // became the team's, but a personal log holds reviews of repos bound to OTHER teams and of private
    // work, and copying it in committed and PUSHED all of it. Joining a team would have told them what
    // else you review. log.reviewed() merges every log on read, so you still see your own history;
    // they see only what was reviewed for them.
    activate();
    adopt_covers(Some(&key)); // ponytail: what this team says it covers, once, because you chose to join it
                              // ponytail: the JOIN is done: cloned, renamed, activated. A push that fails after this is a sync
                              // problem, not a join problem: read-only access to the repo clones fine, and then union_attrs and
                              // seed_project give push_dir something to commit. Returning that error made the caller treat a
                              // usable checkout as a failure: the TUI skipped state.wake.set() so REVIEWED never reloaded, and
                              // retrying answered "already in <key>". push_dir already puts the reason in ERROR, which the Team
                              // row shows, so it is reported where a sync failure belongs rather than as a failure to join.
    let user = std::env::var("USER")
        .ok()
        .filter(|u| !u.is_empty())
        .unwrap_or_else(|| "team".into());
    push_dir(&dest, &format!("gitdashy: join {user}"), "join");
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Tests that touch the global config take this; the pure ones do not need it.
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn sh(cwd: &Path, args: &[&str]) -> String {
        let r = git(cwd, args);
        assert!(r.ok(), "git {args:?} at {}: {}", cwd.display(), r.stderr);
        r.stdout
    }

    /// Point every path at `root`, so nothing a test writes lands in the real home.
    fn point(root: &Path) {
        config::update(|c| {
            c.teams = root.join("teams");
            c.team = root.join("old");
            c.memory_dir = root.join("mem");
            c.local_memory = root.join("mem");
            c.log = root.join("reviewed.jsonl");
            c.local_log = root.join("reviewed.jsonl");
            c.bindings = root.join("bindings");
            c.settings = None;
        });
        std::fs::create_dir_all(root.join("mem")).unwrap();
    }

    #[test]
    fn key_of_is_lowercase_dashed_and_clipped() {
        assert_eq!(key_of("  Org Mem  "), "org-mem");
        assert_eq!(key_of("A__b!!C"), "a-b-c");
        assert_eq!(key_of("---"), "");
        assert_eq!(key_of(""), "");
        assert_eq!(key_of(&"x".repeat(80)).len(), 64);
    }

    #[test]
    fn slug_of_reads_every_shape() {
        assert_eq!(slug_of("git@github.com:org/mem.git"), "org/mem");
        assert_eq!(slug_of("https://github.com/org/mem/"), "org/mem");
        assert_eq!(slug_of("org/mem"), "org/mem");
        assert_eq!(slug_of("/srv/teams/x"), "teams/x");
        assert_eq!(slug_of("mem"), "mem");
        assert_eq!(slug_of(""), "");
        assert_eq!(slug_of("  "), "");
    }

    #[test]
    fn host_of_names_only_a_host() {
        assert_eq!(host_of("git@github.com:org/mem.git"), "github.com");
        assert_eq!(host_of("ssh://git@GitLab.example.com/o/r"), "gitlab.example.com");
        assert_eq!(host_of("https://u:pw@github.com/o/r"), "github.com");
        assert_eq!(host_of("org/mem"), "");
        assert_eq!(host_of("/a/local/path"), "");
    }

    #[test]
    fn same_remote_does_not_ignore_the_host() {
        assert!(same_remote(
            "git@github.com:org/mem.git",
            "https://github.com/org/mem"
        ));
        assert!(same_remote(
            "ssh://git@github.com/org/mem",
            "git@github.com:org/mem.git"
        ));
        assert!(!same_remote(
            "git@gitlab.com:org-mem.git",
            "https://github.com/org/mem"
        ));
        assert!(same_remote("org/mem", "https://github.com/org/mem"));
        assert!(!same_remote("other/mem", "https://github.com/org/mem"));
        assert!(!same_remote("", "https://github.com/org/mem"));
        assert!(!same_remote(
            "git@gitlab.com:org/mem.git",
            "https://github.com/org/mem"
        ));
    }

    #[test]
    fn bare_url_strips_a_credential_and_refuses_non_http() {
        assert_eq!(
            bare_url("https://x-token:ghp_secret@github.com/a/b.git"),
            "https://github.com/a/b.git"
        );
        assert_eq!(bare_url("http://h/a/b"), "http://h/a/b");
        assert_eq!(bare_url("git@github.com:a/b.git"), "");
        assert_eq!(bare_url("ssh://u:pw@h/a/b"), "");
        assert_eq!(bare_url("https://host/"), "");
        assert_eq!(bare_url(""), "");
    }

    #[test]
    fn the_ssh_form_of_a_url_is_not_a_github_fact() {
        assert_eq!(ssh_form("https://github.com/a/b.git"), "git@github.com:a/b.git");
        assert_eq!(
            ssh_form("https://gitlab.example.com/g/sub/c"),
            "git@gitlab.example.com:g/sub/c.git"
        );
        assert_eq!(ssh_form("http://h/a/b"), "git@h:a/b.git");
        assert_eq!(ssh_form("https://u:pw@h/a/b/"), "git@h:a/b.git");
        assert_eq!(ssh_form("git@github.com:a/b.git"), "");
        assert_eq!(ssh_form("/a/local/path"), "");
        assert_eq!(ssh_form(""), "");
    }

    #[test]
    fn redacted_covers_every_scheme_and_leaves_scp_alone() {
        assert_eq!(redacted("ssh://u:pw@host/o/r"), "ssh://host/o/r");
        assert_eq!(
            redacted("https://x:y@github.com/a/b.git"),
            "https://github.com/a/b.git"
        );
        assert_eq!(redacted("https://github.com"), "https://github.com");
        assert_eq!(redacted("git@github.com:a/b.git"), "git@github.com:a/b.git");
        assert_eq!(redacted("/a/path"), "/a/path");
        assert_eq!(redacted(""), "");
    }

    #[test]
    fn looks_local_reads_paths_that_do_not_exist_yet() {
        assert!(looks_local("/srv/nope/yet"));
        assert!(looks_local("./x"));
        assert!(looks_local("../x"));
        assert!(looks_local("~/x"));
        assert!(looks_local(" /x "));
        assert!(!looks_local("org/mem"));
        assert!(!looks_local("https://github.com/o/r"));
        assert!(!looks_local(""));
        let t = tempfile::tempdir().unwrap();
        let rel = t.path().to_string_lossy().to_string();
        assert!(looks_local(&rel));
    }

    #[test]
    fn dirname_is_identity() {
        assert_eq!(dirname("org-mem"), PathBuf::from("org-mem"));
        assert_eq!(dirname(""), PathBuf::from(""));
    }

    #[test]
    fn clean_refuses_control_bytes_and_clips() {
        assert_eq!(clean("  a\nb\x07c  ", "k"), "abc");
        assert_eq!(clean("\n\t", "k"), "k");
        assert_eq!(clean(&"x".repeat(200), "k").len(), 120);
    }

    #[test]
    fn credential_hint_never_clips_the_remote() {
        let long = format!("https://{}/a/b.git", "h".repeat(90));
        let hint = credential_hint(&long);
        assert_eq!(hint, ssh_form(&long));
        assert_eq!(
            credential_hint("https://github.com/a/b.git"),
            "no credential: try git@github.com:a/b.git"
        );
        assert!(credential_hint("git@github.com:a/b.git").contains("ssh agent"));
        assert!(auth_hint("fatal: nope", "https://h/a/b").is_empty());
        assert!(!auth_hint("could not read Username", "https://h/a/b").is_empty());
    }

    #[test]
    fn starting_a_team_needs_no_remote_and_pins_its_branch() {
        let _l = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let t = tempfile::tempdir().unwrap();
        point(t.path());
        assert_eq!(start("Org Mem", "the org", ""), "");
        assert_eq!(joined(), vec!["org-mem".to_string()]);
        assert!(on());
        let d = dir_of("org-mem").unwrap();
        assert_eq!(dir_of("ORG-MEM"), Some(d.clone()));
        assert!(dir_of("nope").is_none());
        assert_eq!(dirs(), vec![d.clone()]);
        assert!(is_repo(&d));
        assert!(!has_remote(&d));
        assert_eq!(sh(&d, &["rev-parse", "--abbrev-ref", "HEAD"]).trim(), "main");
        assert!(sh(&d, &["log", "--oneline"]).contains("new team Org Mem"));
        assert!(sh(&d, &["status", "--porcelain"]).trim().is_empty());
        assert!(std::fs::read_to_string(d.join(".gitattributes"))
            .unwrap()
            .contains("merge=union"));
        assert_eq!(
            std::fs::read_to_string(d.join("memory/project.md")).unwrap(),
            PROJECT_TEMPLATE
        );
        assert_eq!(
            std::fs::read_to_string(d.join("memory/agents.md")).unwrap(),
            AGENTS_TEMPLATE
        );
        assert_eq!(
            info("org-mem"),
            Info {
                name: "Org Mem".into(),
                description: "the org".into()
            }
        );
        assert_eq!(log_of("org-mem"), d.join("reviewed.jsonl"));
        assert_eq!(log_of(""), t.path().join("reviewed.jsonl"));
        assert_eq!(log_of("nope"), t.path().join("reviewed.jsonl"));
        assert_eq!(start("org mem", "", ""), "already in org-mem");
        assert_eq!(start("", "", ""), "a team needs a name");
        activate();
        assert_eq!(*NAME.lock().unwrap(), "org-mem");
        // a second one sorts in, and push() runs over every team
        assert_eq!(start("Alpha", "", ""), "");
        assert_eq!(joined(), vec!["alpha".to_string(), "org-mem".to_string()]);
        std::fs::write(d.join("reviewed.jsonl"), "{}\n").unwrap();
        assert_eq!(push("mine"), "");
        assert!(sh(&d, &["log", "--oneline"]).contains("mine"));
    }

    #[test]
    fn a_team_can_live_somewhere_else_and_be_linked() {
        let _l = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let t = tempfile::tempdir().unwrap();
        point(t.path());
        let at = t.path().join("elsewhere");
        assert_eq!(start("Linked", "", &at.to_string_lossy()), "");
        let d = dir_of("linked").unwrap();
        assert!(d.symlink_metadata().unwrap().file_type().is_symlink());
        assert!(at.join(".git").is_dir());
        // a directory that holds something is somebody's
        let full = t.path().join("full");
        std::fs::create_dir_all(&full).unwrap();
        std::fs::write(full.join("x"), "").unwrap();
        assert!(start("Other", "", &full.to_string_lossy()).starts_with("not empty"));
        assert!(dir_of("other").is_none());
        assert!(t.path().join("teams/other").symlink_metadata().is_err());
        // an existing checkout is joined, not started
        assert!(start("Third", "", &at.to_string_lossy()).contains("teams --join"));
    }

    #[test]
    fn write_info_round_trips_and_launders() {
        let _l = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let t = tempfile::tempdir().unwrap();
        point(t.path());
        assert_eq!(write_info("nope", "x", "", None), "not in nope");
        assert_eq!(start("Team", "", ""), "");
        let d = dir_of("team").unwrap();
        assert_eq!(
            write_info(
                "team",
                "Renamed",
                "a description",
                Some(&["acme/api".to_string()])
            ),
            ""
        );
        let text = std::fs::read_to_string(d.join(INFO)).unwrap();
        // Python's json.dump(indent=1): one-space indent, key order name, description, covers
        assert_eq!(
            text,
            "{\n \"name\": \"Renamed\",\n \"description\": \"a description\",\n \"covers\": [\n  \"acme/api\"\n ]\n}\n"
        );
        assert_eq!(
            info("team"),
            Info {
                name: "Renamed".into(),
                description: "a description".into()
            }
        );
        let raw: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(raw["covers"][0], "acme/api");
        // a name someone pushed with a newline in it never reaches the header
        std::fs::write(
            d.join(INFO),
            "{\"name\": \"bad\\nname\\u0007\", \"description\": 7}",
        )
        .unwrap();
        assert_eq!(
            info("team"),
            Info {
                name: "badname".into(),
                description: "7".into()
            }
        );
        std::fs::write(d.join(INFO), "[1, 2]").unwrap();
        assert_eq!(
            info("team"),
            Info {
                name: "team".into(),
                description: "".into()
            }
        );
        assert!(covers("team").is_empty());
        // an empty name falls back to the key, and no covers means no key
        assert_eq!(write_info("team", "", "", Some(&[])), "");
        assert_eq!(
            std::fs::read_to_string(d.join(INFO)).unwrap(),
            "{\n \"name\": \"team\",\n \"description\": \"\"\n}\n"
        );
    }

    #[test]
    fn init_history_tracks_a_plain_directory_and_refuses_a_nested_one() {
        let t = tempfile::tempdir().unwrap();
        let mem = t.path().join("mem");
        std::fs::create_dir_all(&mem).unwrap();
        std::fs::write(mem.join("general.md"), "- a fact\n").unwrap();
        assert!(!is_repo(&mem));
        assert!(init_history(&mem));
        assert!(is_repo(&mem));
        assert!(init_history(&mem));
        assert_eq!(no_history(&mem), "");
        assert!(sh(&mem, &["log", "--oneline"]).contains("before this was tracked"));
        assert!(sh(&mem, &["status", "--porcelain"]).trim().is_empty());
        // nested in someone else's repo: recorded, not initialised
        let inner = mem.join("inner");
        std::fs::create_dir_all(&inner).unwrap();
        assert!(inside_other_repo(&inner));
        assert!(!init_history(&inner));
        assert_eq!(no_history(&inner), "inside another git repo");
        assert!(!init_history(&inner));
        assert!(!is_repo(&inner));
        assert!(!init_history(&t.path().join("missing")));
        assert!(!init_history(Path::new("")));
    }

    #[test]
    fn push_dir_commits_without_a_remote_and_says_why_not() {
        let t = tempfile::tempdir().unwrap();
        let d = t.path().join("d");
        std::fs::create_dir_all(&d).unwrap();
        assert_eq!(push_dir(&d, "x", "sync"), "not a git checkout");
        assert!(init_history(&d));
        assert_eq!(push_dir(&d, "nothing new", "sync"), "");
        std::fs::write(d.join("a.md"), "- one\n").unwrap();
        assert_eq!(push_dir(&d, "one fact", "sync"), "");
        assert_eq!(push_dir(&d, "nothing new", "sync"), "");
        let log = sh(&d, &["log", "--oneline"]);
        assert!(log.contains("one fact") && !log.contains("nothing new"));
        assert!(sh(&d, &["status", "--porcelain"]).trim().is_empty());
        assert!(!pull_dir(&d, "sync")); // nothing to pull from, and no error either
        assert_eq!(error(), "");
        assert_eq!(origin_url(&d), "");
        assert_eq!(fetched_at(&d), None);
    }

    #[test]
    fn a_pull_records_on_the_checkout_whether_it_landed() {
        let t = tempfile::tempdir().unwrap();
        let remote_ = t.path().join("remote.git");
        sh(
            t.path(),
            &["init", "-q", "--bare", "-b", "main", &remote_.to_string_lossy()],
        );
        let d = t.path().join("d");
        std::fs::create_dir_all(&d).unwrap();
        assert!(init_history(&d));
        sh(&d, &["remote", "add", "origin", &remote_.to_string_lossy()]);
        assert!(has_remote(&d));
        assert_eq!(origin_url(&d), remote_.to_string_lossy());
        assert_eq!(origin_slug(&d), slug_of(&remote_.to_string_lossy()));
        assert_eq!(fetched_at(&d), None); // a remote, but never fetched
        std::fs::write(d.join("reviewed.jsonl"), "{}\n").unwrap();
        assert_eq!(push_dir(&d, "first", "sync"), "");
        assert!(pull_dir(&d, "sync"));
        assert_eq!(pull_failed(&d), "");
        assert!(fetched_at(&d).is_some());
        // a remote that vanished: the marker says so, and stays until a pull lands
        std::fs::remove_dir_all(&remote_).unwrap();
        assert!(!pull_dir(&d, "sync"));
        assert!(!pull_failed(&d).is_empty());
        assert!(error().starts_with("sync: "));
        assert!(!sh(&d, &["status", "--porcelain"]).contains("rebase"));
        mark_pull(&d, "");
        assert_eq!(pull_failed(&d), "");
    }

    #[test]
    fn connect_pushes_and_setup_joins_what_was_pushed() {
        let _l = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let t = tempfile::tempdir().unwrap();
        point(t.path());
        let remote_ = t.path().join("remote.git");
        sh(
            t.path(),
            &["init", "-q", "--bare", "-b", "main", &remote_.to_string_lossy()],
        );
        assert_eq!(connect("nope", "x"), "not in nope");
        assert_eq!(start("Shared", "d", ""), "");
        assert_eq!(connect("shared", "  "), "a remote needs a URL");
        assert_eq!(connect("shared", &remote_.to_string_lossy()), "");
        let d = dir_of("shared").unwrap();
        assert!(has_remote(&d));
        assert!(sh(&d, &["ls-remote", "--heads", "origin"]).contains("refs/heads/main"));
        assert_eq!(already_joined(&remote_.to_string_lossy()), "shared");
        assert_eq!(
            setup(&remote_.to_string_lossy(), ""),
            "already joined that repo, as shared"
        );
        // a checkout cannot be pushed into
        assert!(setup(&d.to_string_lossy(), "").starts_with("a checkout"));
        // a second machine joins the same repo and agrees on the key from team.json
        let other = t.path().join("other");
        config::update(|c| c.teams = other.join("teams"));
        assert!(joined().is_empty());
        assert_eq!(setup(&remote_.to_string_lossy(), "Whatever I Typed"), "");
        assert_eq!(joined(), vec!["shared".to_string()]);
        assert_eq!(
            info("shared"),
            Info {
                name: "Shared".into(),
                description: "d".into()
            }
        );
        assert!(!other.join("teams/.joining").exists());
        assert_eq!(
            setup(&remote_.to_string_lossy(), ""),
            "already joined that repo, as shared"
        );
        assert_eq!(*NAME.lock().unwrap(), "shared");
        assert_eq!(error(), "");
        // a clone that cannot happen says so and leaves nothing behind
        assert!(!setup(&t.path().join("missing.git").to_string_lossy(), "").is_empty());
        assert!(!other.join("teams/.joining").exists());
    }

    #[test]
    fn migration_refuses_what_it_cannot_prove_safe() {
        let _l = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let t = tempfile::tempdir().unwrap();
        point(t.path());
        assert_eq!(migrate(), ""); // nothing to migrate
        let old = t.path().join("old");
        std::fs::create_dir_all(&old).unwrap();
        assert!(init_history(&old));
        let msg = migrate();
        assert!(msg.contains("has no origin to name it by"), "{msg}");
        assert!(old.is_dir());
        sh(&old, &["remote", "add", "origin", "git@github.com:org/mem.git"]);
        // knowledge::unpushed cannot tell once the tree is dirty, so the move is refused rather than
        // risked. A remote with no upstream is 0, not -1: with nothing to compare against there is
        // nothing unpushed (see unpushed() and test_knowledge.py). Uncommitted work is the real -1.
        std::fs::write(old.join("uncommitted.md"), "unpushed work\n").unwrap();
        let msg = migrate();
        assert!(msg.contains("possibly unpushed reviews"), "{msg}");
        assert!(old.is_dir());
        std::fs::create_dir_all(t.path().join("teams/org-mem")).unwrap();
        let msg = migrate();
        assert!(msg.contains("already exists"), "{msg}");
        assert!(old.is_dir());
    }

    #[test]
    fn union_attrs_appends_once() {
        let t = tempfile::tempdir().unwrap();
        union_attrs(t.path());
        union_attrs(t.path());
        let text = std::fs::read_to_string(t.path().join(".gitattributes")).unwrap();
        assert_eq!(text, "*.jsonl merge=union\n*.md merge=union\n");
        seed_project(&t.path().join("p.md"));
        std::fs::write(t.path().join("a.md"), "mine").unwrap();
        seed_agents(&t.path().join("a.md"));
        assert_eq!(std::fs::read_to_string(t.path().join("a.md")).unwrap(), "mine");
        assert_eq!(
            std::fs::read_to_string(t.path().join("p.md")).unwrap(),
            PROJECT_TEMPLATE
        );
    }

    #[test]
    fn a_remote_call_is_bounded() {
        let r = remote(&["sleep", "5"], None, Some(0));
        assert_eq!(r.code, 1);
        assert!(r.stderr.contains("timed out after 0s"));
        let r = remote(&["definitely-not-a-binary-xyz"], None, None);
        assert_eq!(r.code, 1);
        assert!(r.stderr.ends_with(": definitely-not-a-binary-xyz"));
        let mut out = Out {
            code: 1,
            stdout: String::new(),
            stderr: "fatal: a\nfatal: b\n".into(),
        };
        assert!(!note(&out, "sync"));
        assert_eq!(error(), "sync: fatal: b");
        out.code = 0;
        assert!(note(&out, "sync"));
        assert_eq!(error(), "");
    }
}
