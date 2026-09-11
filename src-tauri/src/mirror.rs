//! Read-only mirrors of team memory inside a repo (.agent/team). Port of dashy/core/mirror.py.
//!
//! ponytail: a copy, not a symlink. Claude Code confines CLAUDE.md imports to the project tree (`~`,
//! absolute and symlinked paths are all refused) so a real file inside the repo is the only way in.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::{heartbeat, memory, team};

/// The only names sync() ever writes or removes.
pub const NAMES: &[&str] = &["general.md", "repo.md"];
pub const HEADER: &str =
    "> **Shared team memory — read-only mirror.** PR reviews write these facts; `gitdashy sync-memory`\n\
> copies them here. Edits to this file are lost at the next sync — change the source, not the mirror.\n\
>\n\
> source: `{src}` · synced: {at}\n\
{age}\n";

/// Seconds since a team was last reached, past which the header warns rather than reports.
pub const STALE: u64 = 3600;
/// ponytail: a floor between background pulls. The session hook fires one of these at every session
/// start, on every machine: open six repos in an editor and that is six fetches in a second, all of
/// them asking a question the first one answered. Nothing throttled it: alive() is false when no
/// dashboard is up, which is exactly when the hook runs. A team reached within this needs no second ask.
pub const FRESH: u64 = 300;

const GIT_TIMEOUT: u64 = 60;

/// Run git with `args`, killing it after `timeout` seconds. Err when git cannot be started or hangs.
pub(crate) fn run_git(args: &[&str], timeout: u64) -> std::io::Result<Output> {
    let mut child = Command::new("git")
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let mut out = child.stdout.take().expect("piped stdout");
    let mut err = child.stderr.take().expect("piped stderr");
    let reader = std::thread::spawn(move || {
        let mut o = Vec::new();
        let _ = out.read_to_end(&mut o);
        let mut e = Vec::new();
        let _ = err.read_to_end(&mut e);
        (o, e)
    });
    let start = Instant::now();
    let status = loop {
        if let Some(s) = child.try_wait()? {
            break s;
        }
        if start.elapsed() >= Duration::from_secs(timeout) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                format!("git {} timed out", args.join(" ")),
            ));
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let (stdout, stderr) = reader.join().unwrap_or_default();
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

pub(crate) fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// True when every joined team with a remote was reached within FRESH. A team with none is not a
/// reason to go to the network, and no teams at all means there is nothing to pull.
fn all_fresh(now: f64) -> bool {
    // ponytail: a team whose last pull FAILED is never fresh, whatever FETCH_HEAD says. The fetch half
    // of `pull --rebase` rewrites that stamp before the rebase runs, so a failing checkout otherwise
    // held its own retries off for FRESH seconds at a time, for ever.
    let dirs = team::dirs();
    let ages: Vec<f64> = dirs
        .iter()
        .filter(|d| team::pull_failed(d).is_empty())
        .filter_map(|d| team::fetched_at(d))
        .collect();
    !ages.is_empty()
        && ages.len() == dirs.iter().filter(|d| team::has_remote(d)).count()
        && ages.iter().all(|at| now - at < FRESH as f64)
}

/// A rough age, in the largest unit that is not a fraction. Exactness is not the point here: the
/// reader is deciding whether to trust a file, and "2 days" and "51 hours" lead to the same decision.
pub fn ago(secs: f64) -> String {
    for (size, unit) in [(86400.0, "day"), (3600.0, "hour"), (60.0, "minute")] {
        if secs >= size {
            let n = (secs / size).floor() as i64;
            return format!("{} {}{} ago", n, unit, if n > 1 { "s" } else { "" });
        }
    }
    "just now".into()
}

/// The header's age lines: one per team source that has a remote, saying when it was last reached.
///
/// ponytail: in the FILE, not on the hook's stdout. This is what a session reads, it is imported on
/// every turn, and it costs one line. A hook message scrolls past once, at the moment nobody is
/// looking for it, and says nothing at all to a session started any other way.
/// ponytail: the warning names what the reader can fix, rather than the age alone.
pub fn freshness(got: &[(String, PathBuf)], now: f64) -> String {
    let running = heartbeat::alive();
    let mut out = String::new();
    for (label, base) in got {
        if label == "mine" {
            continue;
        }
        let checkout = base.parent().map(Path::to_path_buf).unwrap_or_default();
        let at = match team::fetched_at(&checkout) {
            Some(at) => at,
            None => continue, // local-only team: there is no remote to be behind
        };
        // ponytail: the AGE decides, not whether something is running. `not running and ...` meant a
        // dashboard whose pulls have been failing for four days (expired credential, VPN off, and
        // team.ERROR already knows) printed "last pulled 4 days ago" with no call to action. That is
        // the case where the reader most needs telling and the one that read as fine. Only the remedy
        // differs, because only the remedy depends on whether anything is trying.
        // ponytail: and the AGE IS NOT THE WHOLE ANSWER. `git pull --rebase` rewrites FETCH_HEAD during
        // the fetch, before the rebase, so a pull that fetched and then failed to rebase leaves a fresh
        // timestamp over a checkout that did not move. The header said "last pulled just now" for
        // exactly the case it exists to report, and all_fresh then held off retries on the strength of
        // it. A live failure outranks any age.
        // ponytail: read off the CHECKOUT, not team.ERROR. That global is last-writer-wins across every
        // git call in this process, so it pinned one team's failure on every team's line and a later
        // success wiped it; and it is per-process, so the session hook's own --no-pull rewrite, in a
        // fresh process with ERROR == "", quietly overwrote the warning at every session start.
        let failed = team::pull_failed(&checkout);
        let why = if !failed.is_empty() {
            let shown: String = team::redacted(&failed).chars().take(80).collect();
            format!(" — **the last sync did not land:** {}", shown)
        } else if now - at <= STALE as f64 {
            String::new()
        } else {
            format!(
                " — **this may be behind what the team has.** {}",
                if running {
                    "The dashboard is running but is not reaching the team; `T` in it shows the last error."
                } else {
                    "Nothing is refreshing it: start `gitdashy`, or run `gitdashy sync-memory --into` this directory."
                }
            )
        };
        out.push_str(&format!("> {}: last pulled {}{}\n", label, ago(now - at), why));
    }
    out
}

/// The header for one mirror file.
pub fn header(src: &str, at: &str, age: &str) -> String {
    HEADER
        .replace("{src}", src)
        .replace("{at}", at)
        .replace("{age}", age)
}

/// Where a path that need not exist would be made absolute; the nearest existing ancestor is what
/// git is run from.
fn absolute(path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|d| d.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    }
}

/// True when git would commit a file written at `path`: inside a repo and not ignored. Err when git
/// could not be asked (missing, or hung).
///
/// ponytail: `path` need not exist: check-ignore is pure path matching, so we ask about the real target
/// but run git from the nearest directory that does exist. Fail-safe: anything but a clean "ignored"
/// answer counts as tracked, so a broken git call refuses rather than leaking memory into someone's history.
pub fn tracked_checked(path: &Path, names: &[&str]) -> std::io::Result<bool> {
    let full = absolute(path);
    let mut base = full.clone();
    while !base.is_dir() {
        match base.parent() {
            Some(p) if p != base => base = p.to_path_buf(),
            _ => break,
        }
    }
    let base_s = base.to_string_lossy().to_string();
    if !run_git(&["-C", &base_s, "rev-parse", "--show-toplevel"], GIT_TIMEOUT)?
        .status
        .success()
    {
        return Ok(false); // not a git repo: nothing to leak into
    }
    // ponytail: EVERY name we write, not just the first. An ignore rule matching general.md but not
    // repo.md would answer "ignored" and we would then commit the other one: the exact leak this prevents.
    for n in names {
        let target = full.join(n).to_string_lossy().to_string();
        if !run_git(&["-C", &base_s, "check-ignore", "-q", &target], GIT_TIMEOUT)?
            .status
            .success()
        {
            return Ok(true);
        }
    }
    Ok(false)
}

// PORT-NOTE: the Python `tracked` raised OSError when git itself could not run, and its two callers
// answered differently: sync() reported a refusal, knowledge.inside_git() said False. `tracked_checked`
// carries the error for sync; this wrapper keeps the stub signature with inside_git's answer.
/// True when git would commit a file written at `path`.
pub fn tracked(path: &Path, names: &[&str]) -> bool {
    tracked_checked(path, names).unwrap_or(false)
}

/// Mirror `repo`'s memory into `into`, and the general file too when asked. One-line report.
///
/// ponytail: per-repo only by default. Cross-repo facts belong in a user-level instruction file, which
/// loads them live everywhere; mirroring them per repo as well would put every general fact in context
/// twice. general=True is for anyone who has not wired that route and wants it all in the repo.
///
/// ponytail: pull=False for callers on a clock (a SessionStart hook): mirrors whatever the last
/// gitdashy refresh pulled, instead of risking a network round trip inside their timeout.
pub fn sync(into: &Path, repo: &str, pull: bool, general: bool) -> String {
    // ponytail: the REFUSAL COMES FIRST, before anything touches the network. It used to sit under the
    // pull, so a repo whose mirror path git tracks still fetched every joined team before being told no,
    // and the session hook fires this in the background, so that was a pull per session start in a
    // repo that can never have a mirror. Ask before creating anything, for the same reason: a refusal
    // must not leave the tree it refused to write in.
    // ponytail: a SessionStart hook calls this, so a panic is a broken hook: failures come back
    // as the report rather than raising.
    match tracked_checked(into, if general { NAMES } else { &NAMES[1..] }) {
        Ok(true) => {
            return format!(
            "gitdashy: refused — git would commit {}; ignore that path before mirroring team memory there",
            into.display()
        )
        }
        Ok(false) => {}
        Err(e) => return format!("gitdashy: refused — {}", e),
    }
    if let Err(e) = std::fs::create_dir_all(into) {
        return format!("gitdashy: refused — {}", e);
    }
    // ponytail: a RUNNING dashboard already pulled, less than one interval ago, and will again. Two
    // processes running `pull --rebase` in one checkout race for git's index.lock, and the loser leaves
    // a rebase behind for the winner to trip over. Skipping here is what lets the session hook fire this
    // off in the background without having to know whether anything else is doing the same job.
    let mut skipped = if pull && heartbeat::alive() {
        "a dashboard is refreshing this"
    } else {
        ""
    };
    if pull && skipped.is_empty() && all_fresh(now()) {
        skipped = "every team was reached in the last few minutes";
    }
    if pull && skipped.is_empty() {
        // ponytail: and the lock covers the case the beat cannot: two background syncs, from two
        // sessions opened at once. Neither writes a beat, so without this both see nothing running.
        if heartbeat::claim() {
            team::pull(); // newest shared memory first; a no-op when team mode is off
            heartbeat::unclaim();
        } else {
            skipped = "another sync is already pulling";
        }
    }
    let at = chrono::Local::now().format("%Y-%m-%d %H:%M").to_string();
    // ponytail: the skip is REPORTED. Someone typing `gitdashy sync-memory` has asked for the team's
    // newest, and silently not fetching it is the same answer as fetching nothing: they cannot tell
    // the two apart, and the second is a reason to go looking. The hook's copy discards stdout, so
    // this costs the path it was added for nothing.
    match write(into, repo, general, &at) {
        Ok(report) => {
            if skipped.is_empty() {
                report
            } else {
                format!("{} · not pulled: {}", report, skipped)
            }
        }
        Err(e) => format!("gitdashy: refused — {}", e),
    }
}

/// A temp file beside `dst`, private to this writer, at 0600.
fn part(into: &Path, name: &str) -> std::io::Result<(PathBuf, std::fs::File)> {
    for _ in 0..100 {
        let mut raw = [0u8; 6];
        getrandom::fill(&mut raw).map_err(|e| std::io::Error::other(e.to_string()))?;
        let tag: String = raw.iter().map(|b| format!("{:02x}", b)).collect();
        let p = into.join(format!("{}.{}.part", name, tag));
        let mut o = std::fs::OpenOptions::new();
        o.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            o.mode(0o600);
        }
        match o.open(&p) {
            Ok(f) => return Ok((p, f)),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(std::io::Error::other("could not make a temp file"))
}

fn write(into: &Path, repo: &str, general: bool, at: &str) -> std::io::Result<String> {
    // ponytail: ONE call, and the report is derived from it. `where` used to ask bind.of(repo)
    // independently, so a repo bound to a team you have since LEFT reported "from team X" while
    // sources() had already returned yours alone: the label and the content disagreeing about the
    // same write, which is the defect this line was changed to fix in the first place.
    let got = memory::sources(repo);
    let src = got
        .iter()
        .map(|(l, _)| l.as_str())
        .collect::<Vec<_>>()
        .join(" + ");
    let age = freshness(&got, now());
    let repo_opt = if repo.is_empty() { None } else { Some(repo) };
    let mut wrote = Vec::new();
    // scope: None = the general file, Some("") = skip, Some(repo) = the repo's file.
    let scopes = [if general { None } else { Some("") }, Some(repo)];
    for (name, scope) in NAMES.iter().zip(scopes) {
        let dst = into.join(name);
        let mut text = match scope {
            Some("") => String::new(),
            s => memory::scope_text(s, repo_opt),
        };
        if let Some(r) = scope.filter(|s| !s.is_empty()) {
            // ponytail: repo.md carries the brief and the bound team's general facts ABOVE the repo's own,
            // because they reach the session no other way now. Your general facts stay out: they load
            // live through the prs-memory link, and a second copy per repo is context spent twice.
            let parts = [
                memory::session_context(r, general),
                if text.is_empty() {
                    String::new()
                } else {
                    format!("## {}\n{}", r, text)
                },
            ];
            text = parts
                .iter()
                .filter(|t| !t.is_empty())
                .cloned()
                .collect::<Vec<_>>()
                .join("\n\n");
        }
        if !text.is_empty() {
            // ponytail: written whole, then moved into place. There are two writers of this file now,
            // the dashboard's refresh and the background one a session hook starts, and a plain open()
            // truncates first, so a session reading at the wrong moment imported an empty or half-written
            // mirror and was told the team knows nothing. rename within one directory is atomic, so a
            // reader sees the old file or the new one.
            // ponytail: a shared `dst + ".part"` is worse, because both write at their own offsets and
            // whoever renames first publishes a file that looks whole. A private temp name also keeps
            // the mirror at 0600, which memory being team-private wants.
            let (tmp, mut f) = part(into, name)?;
            let landed = f
                .write_all(format!("{}{}\n", header(&src, at, &age), text).as_bytes())
                .and_then(|_| f.flush())
                .and_then(|_| std::fs::rename(&tmp, &dst));
            if let Err(e) = landed {
                // ponytail: ours, and only ours. The error carries on to sync()'s handler; leaving
                // the file behind puts an unexplained repo.md.XXXX.part in someone's repo, inside a
                // directory the mirror promises to own the contents of, for every failed write.
                let _ = std::fs::remove_file(&tmp);
                return Err(e);
            }
            wrote.push(*name);
        } else if dst.exists() {
            std::fs::remove_file(&dst)?; // ponytail: a mirror never outlives its source, or it becomes a rumour
        }
    }
    let wher = got
        .iter()
        .find(|(l, _)| l != "mine")
        .map(|(l, _)| l.clone())
        .unwrap_or_else(|| crate::config::get().memory_dir.display().to_string());
    let error = team::ERROR.lock().map(|e| e.clone()).unwrap_or_default();
    Ok(format!(
        "gitdashy: mirrored {} into {} from {}{}{}",
        if wrote.is_empty() {
            "nothing".to_string()
        } else {
            wrote.join(", ")
        },
        into.display(),
        wher,
        if repo.is_empty() {
            String::new()
        } else {
            format!(" for {}", repo)
        },
        if error.is_empty() {
            String::new()
        } else {
            format!(" · {}", error)
        },
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;
    use std::sync::Mutex;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn git(args: &[&str]) {
        assert!(Command::new("git").args(args).status().unwrap().success());
    }

    #[test]
    fn ago_picks_the_largest_whole_unit() {
        assert_eq!(ago(5.0), "just now");
        assert_eq!(ago(60.0), "1 minute ago");
        assert_eq!(ago(600.0), "10 minutes ago");
        assert_eq!(ago(3600.0 * 1.5), "1 hour ago");
        assert_eq!(ago(4.0 * 86400.0), "4 days ago");
        assert_eq!(ago(86400.0 * 2.9), "2 days ago");
    }

    #[test]
    fn header_keeps_the_python_shape_and_ends_with_a_blank_line() {
        let h = header(
            "mine + team org-t",
            "2026-09-11 10:00",
            "> team org-t: last pulled just now\n",
        );
        assert!(h.starts_with("> **Shared team memory — read-only mirror.**"));
        assert!(h.contains("> source: `mine + team org-t` · synced: 2026-09-11 10:00\n"));
        // ponytail: the blank line the header used to end with. A body starting with a bullet would
        // otherwise be folded into the quote.
        assert!(h.ends_with("just now\n\n"), "{h:?}");
        assert!(header("mine", "x", "").ends_with("synced: x\n\n"));
    }

    #[test]
    fn freshness_says_nothing_for_your_own_memory_or_a_team_with_no_remote() {
        let d = tempfile::tempdir().unwrap();
        let got = vec![
            ("mine".to_string(), d.path().join("mem")),
            ("team org-t".to_string(), d.path().join("t/memory")),
        ];
        assert_eq!(freshness(&got, now()), "");
    }

    #[test]
    fn tracked_sees_an_unignored_path_under_a_repo() {
        let d = tempfile::tempdir().unwrap();
        let repo = d.path().join("repo");
        std::fs::create_dir(&repo).unwrap();
        git(&["init", "-q", repo.to_str().unwrap()]);
        let into = repo.join("mirror");
        assert!(tracked(&into, NAMES));
        std::fs::create_dir_all(repo.join(".git/info")).unwrap();
        std::fs::write(repo.join(".git/info/exclude"), "mirror/general.md\n").unwrap(); // only one of the two
        assert!(tracked(&into, NAMES));
        std::fs::write(repo.join(".git/info/exclude"), "mirror/\n").unwrap();
        assert!(!tracked(&into, NAMES));
        assert!(!tracked(&d.path().join("plain"), NAMES)); // outside any repo
    }

    #[test]
    fn sync_refuses_a_path_git_would_commit_and_builds_nothing() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.memory_dir = d.path().join("mem"));
        let repo = d.path().join("repo");
        std::fs::create_dir(&repo).unwrap();
        git(&["init", "-q", repo.to_str().unwrap()]);
        let into = repo.join("deep").join("dir");
        assert!(sync(&into, "a/b", false, true).contains("refused"));
        assert!(!into.exists() && !repo.join("deep").exists());
        std::fs::create_dir_all(repo.join(".git/info")).unwrap();
        std::fs::write(repo.join(".git/info/exclude"), "deep/\n").unwrap();
        let report = sync(&into, "a/b", false, true);
        assert!(!report.contains("refused"), "{report}");
        assert!(into.is_dir());
    }

    #[test]
    fn sync_reports_a_path_it_cannot_create_instead_of_panicking() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.memory_dir = d.path().join("mem"));
        let blocker = d.path().join("afile");
        std::fs::write(&blocker, "not a directory\n").unwrap();
        let report = sync(&blocker.join("under").join("a").join("file"), "a/b", false, false);
        assert!(report.starts_with("gitdashy: refused"), "{report}");
    }

    #[test]
    fn sync_removes_a_mirror_whose_source_is_gone_and_reports_where_it_read() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempfile::tempdir().unwrap();
        crate::config::update(|c| c.memory_dir = d.path().join("mem"));
        let into = d.path().join("out");
        std::fs::create_dir(&into).unwrap();
        std::fs::write(into.join("repo.md"), "stale\n").unwrap();
        std::fs::write(into.join("general.md"), "stale\n").unwrap();
        let report = sync(&into, "a/b", false, false);
        assert_eq!(
            report,
            format!(
                "gitdashy: mirrored nothing into {} from {} for a/b",
                into.display(),
                d.path().join("mem").display()
            )
        );
        assert!(!into.join("repo.md").exists() && !into.join("general.md").exists());
        assert!(sync(&into, "", false, false).ends_with(&format!("from {}", d.path().join("mem").display())));
    }

    #[test]
    fn a_temp_file_is_private_and_never_survives() {
        let d = tempfile::tempdir().unwrap();
        let (p, mut f) = part(d.path(), "repo.md").unwrap();
        f.write_all(b"x").unwrap();
        let name = p.file_name().unwrap().to_string_lossy().to_string();
        assert!(name.starts_with("repo.md.") && name.ends_with(".part"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o600);
        }
    }

    #[test]
    fn run_git_times_out_and_reports_a_missing_binary_as_an_error() {
        let out = run_git(&["--version"], 60).unwrap();
        assert!(out.status.success());
        let d = tempfile::tempdir().unwrap();
        let out = run_git(
            &["-C", d.path().to_str().unwrap(), "rev-parse", "--show-toplevel"],
            60,
        )
        .unwrap();
        assert!(!out.status.success());
    }
}
