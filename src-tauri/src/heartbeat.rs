//! "Is a dashboard running on this machine?" and the pull lock. Port of dashy/core/heartbeat.py.
//! Files live beside the settings file; demo mode (settings None) writes nothing.
//!
//! One question, asked by two callers that must not disagree: `mirror::sync` decides whether to pull
//! (a running dashboard pulled less than one interval ago, so a second pull in the same checkout is
//! a git lock race bought for nothing), and the mirror header tells a reading session whether what it
//! is looking at is being kept current at all.
//!
//! ponytail: NOT under memory_dir, though that is where gitdashy's other small state files live. A
//! memory dir is often itself a git repo that `push_dir` commits with `git add -A`, and a file whose
//! contents change every refresh would put one commit per tick in that history for ever. This belongs
//! beside the settings file, which nothing commits.

use std::fs::File;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use fs4::fs_std::FileExt;
use serde_json::Value;

use crate::config;

/// Seconds past one interval before a beat counts as stopped, for a tick that ran long.
pub const GRACE: u64 = 90;
// ponytail: the declared interval is NOT clamped. A clamp to max(config::INTERVALS) was tried and made
// an honest `--interval 1800` read as dead; the host and live-pid checks below are what bound a lying
// beat, and being wrong here fails towards pulling too often.
/// flock'd for one team::pull(), by whichever process got there first.
pub const LOCK: &str = ".prs_pulling";
const BEAT: &str = ".prs_dashboard";

// ponytail: the HOST is recorded because a pid is only meaningful on the machine that made it, and
// a memory dir synced between two machines carries this file along. Without it, a stale pid from
// the laptop can match a live unrelated process on the desktop and a dead dashboard reads as alive.
/// This machine's name, as Python's socket.gethostname() gave it.
pub fn host() -> &'static str {
    static HOST: OnceLock<String> = OnceLock::new();
    HOST.get_or_init(|| {
        std::fs::read_to_string("/proc/sys/kernel/hostname")
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .or_else(|| std::env::var("HOSTNAME").ok().filter(|s| !s.is_empty()))
            .or_else(|| {
                let out = std::process::Command::new("hostname").output().ok()?;
                Some(String::from_utf8_lossy(&out.stdout).trim().to_string()).filter(|s| !s.is_empty())
            })
            .unwrap_or_default()
    })
}

/// Where a small cross-process file of ours lives, or None in demo mode, which writes nothing at all.
/// ponytail: see the module docs for why it is not under the memory dir.
fn beside_settings(name: &str) -> Option<PathBuf> {
    let settings = config::get().settings?;
    let dir = settings
        .parent()
        .filter(|d| !d.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    Some(dir.join(name))
}

/// Where the beat is written.
pub fn path() -> Option<PathBuf> {
    beside_settings(BEAT)
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Record that a dashboard on this machine just refreshed. Never fails: a read-only home is not
/// a reason to fail a tick.
pub fn beat(interval: u64) {
    let Some(p) = path() else { return };
    write_beat(&p, interval);
}

fn write_beat(p: &Path, interval: u64) {
    // ponytail: whole, then renamed, the same shape mirror::write uses, and for a sharper reason. A
    // plain create(p) truncates first, and a reader landing in that window gets a short file, reads
    // it as "no dashboard", and goes and pulls. That window sits exactly where the beat is supposed to
    // be doing its work, and this is the one writer that can close it.
    let dir = p
        .parent()
        .filter(|d| !d.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut salt = [0u8; 4];
    let _ = getrandom::fill(&mut salt);
    let tmp = dir.join(format!(
        ".prs_dashboard.{}{}",
        std::process::id(),
        u32::from_le_bytes(salt)
    ));
    let body =
        serde_json::json!({"pid": std::process::id(), "host": host(), "at": now(), "interval": interval});
    if std::fs::write(&tmp, body.to_string())
        .and_then(|_| std::fs::rename(&tmp, p))
        .is_err()
    {
        let _ = std::fs::remove_file(&tmp);
    }
}

/// True when a dashboard on THIS machine refreshed recently enough to be trusted to keep pulling.
///
/// Three things must hold, and each answers a different way of being wrong: the beat is from this host
/// (a pid means nothing across machines), the process still exists (a dashboard killed mid-tick leaves
/// its last beat behind), and the beat is younger than the interval it declared (a suspended laptop
/// leaves a live pid and a beat from yesterday).
pub fn alive() -> bool {
    path().is_some_and(|p| read_alive(&p))
}

fn read_alive(p: &Path) -> bool {
    let Ok(got) = std::fs::read_to_string(p) else {
        return false;
    };
    let Ok(got) = serde_json::from_str::<Value>(&got) else {
        return false;
    };
    let num = |v: Option<&Value>| match v {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => s.trim().parse::<f64>().ok(),
        _ => None,
    };
    let (Some(at), Some(interval)) = (num(got.get("at")), num(got.get("interval"))) else {
        return false;
    };
    if got.get("host").and_then(Value::as_str) != Some(host()) || now() - at > interval + GRACE as f64 {
        return false;
    }
    let Some(pid) = num(got.get("pid")).filter(|p| p.fract() == 0.0 && *p >= 0.0) else {
        return false;
    };
    pid_alive(pid as u64)
}

/// Whether a process with this pid exists. ponytail: no libc, so no kill(pid, 0). On Linux /proc
/// answers the same question; anywhere else the pid check is skipped and the host and age checks
/// bound a stale beat on their own, which fails towards pulling too often, the safe side.
fn pid_alive(pid: u64) -> bool {
    if cfg!(target_os = "linux") {
        Path::new("/proc").join(pid.to_string()).exists()
    } else {
        true
    }
}

/// The open file holding the flock, so unclaim can let go of exactly what claim took.
fn held() -> &'static Mutex<Option<File>> {
    static HELD: OnceLock<Mutex<Option<File>>> = OnceLock::new();
    HELD.get_or_init(Default::default)
}

/// Take the pull lock (flock on .prs_pulling), or false when somebody else holds it. Release it with
/// `unclaim`.
///
/// ponytail: the heartbeat closes the dashboard-versus-hook race and NOT hook-versus-hook: a
/// background sync writes no beat, so two sessions opened at once (a terminal and an editor is the
/// ordinary case) both saw nothing running and both ran `pull --rebase` in one checkout. team's lock
/// is an in-process mutex and does not reach across processes.
/// ponytail: FLOCK, after a hand-rolled O_EXCL lock was written and found racy twice. That one needed
/// a stale-break timeout, a rename-aside, a pid written into the file and a pid-checked release, and
/// the break was still two steps (stat the mtime, then rename) so two claimants that both read the
/// old mtime could both end up holding it. The kernel owns this one: exactly one holder, released on
/// close AND on the process dying however it dies, so a crash cannot leave a lock nobody can clear.
pub fn claim() -> bool {
    let Some(p) = beside_settings(LOCK) else {
        return true; // demo mode writes nothing and pulls nothing; there is no one to race
    };
    let mut held = held().lock().unwrap_or_else(|e| e.into_inner());
    if held.is_some() {
        return false; // ponytail: not reentrant. Two pulls in one process are the case this guards.
    }
    let Some(file) = open_lock(&p) else {
        return true; // nowhere to put a lock is not a reason to stop syncing
    };
    if !matches!(file.try_lock_exclusive(), Ok(true)) {
        return false; // held elsewhere; anything else is not ours to force
    }
    *held = Some(file);
    true
}

fn open_lock(p: &Path) -> Option<File> {
    let mut opts = std::fs::OpenOptions::new();
    opts.create(true).truncate(false).read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    opts.open(p).ok()
}

/// Give back the pull lock if this process holds it. Never fails: it runs on the way out.
pub fn unclaim() {
    if let Some(file) = held().lock().unwrap_or_else(|e| e.into_inner()).take() {
        let _ = file.unlock();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn beat_file(dir: &Path) -> PathBuf {
        config::update(|c| c.settings = Some(dir.join("settings.json")));
        dir.join(BEAT)
    }

    fn write(p: &Path, pid: u64, host: &str, at: f64, interval: u64) {
        std::fs::write(
            p,
            serde_json::json!({"pid": pid, "host": host, "at": at, "interval": interval}).to_string(),
        )
        .unwrap();
    }

    fn me() -> u64 {
        u64::from(std::process::id())
    }

    #[test]
    fn a_beat_this_process_just_wrote_reads_as_alive() {
        let _g = TEST_LOCK.lock().unwrap();
        let d = tempfile::tempdir().unwrap();
        let p = beat_file(d.path());
        beat(300);
        let got: Value = serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(got["pid"], me());
        assert_eq!(got["host"], host());
        assert_eq!(got["interval"], 300);
        assert!(alive());
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 1); // no temp file left behind
    }

    #[test]
    fn no_beat_at_all_is_not_alive() {
        let _g = TEST_LOCK.lock().unwrap();
        let d = tempfile::tempdir().unwrap();
        beat_file(d.path());
        assert!(!alive());
    }

    #[test]
    fn a_beat_older_than_the_interval_it_declared_is_not_alive() {
        // A suspended laptop leaves a live pid and yesterday's beat. The pid is not the whole answer.
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join(BEAT);
        write(&p, me(), host(), now() - 300.0 - GRACE as f64 - 1.0, 300);
        assert!(!read_alive(&p));
        write(&p, me(), host(), now() - 300.0, 300);
        assert!(read_alive(&p)); // inside the interval plus the grace a long tick is allowed
    }

    #[test]
    fn a_beat_from_another_machine_is_not_alive() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join(BEAT);
        write(&p, me(), &format!("{}-other", host()), now(), 300);
        assert!(!read_alive(&p));
    }

    #[test]
    fn a_dead_pid_is_not_alive() {
        // ponytail: a pid that cannot exist. 2**22 is above every Linux pid_max default, so this is not a
        // race against the machine happening to have that process: it is never a live one.
        if !cfg!(target_os = "linux") {
            return;
        }
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join(BEAT);
        write(&p, 1 << 22, host(), now(), 300);
        assert!(!read_alive(&p));
    }

    #[test]
    fn a_corrupt_beat_is_not_alive_rather_than_a_panic() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join(BEAT);
        std::fs::write(&p, "{\"pid\": 1, \"ho").unwrap();
        assert!(!read_alive(&p));
        std::fs::write(
            &p,
            format!(
                "{{\"pid\": \"not a number\", \"host\": \"{}\", \"at\": 0, \"interval\": 300}}",
                host()
            ),
        )
        .unwrap();
        assert!(!read_alive(&p));
        std::fs::write(
            &p,
            format!(
                "{{\"pid\": {}, \"host\": \"{}\", \"interval\": 300}}",
                me(),
                host()
            ),
        )
        .unwrap();
        assert!(!read_alive(&p)); // a missing key
        std::fs::write(&p, "[]").unwrap();
        assert!(!read_alive(&p));
    }

    #[test]
    fn demo_mode_writes_no_beat_anywhere_and_takes_no_lock() {
        let _g = TEST_LOCK.lock().unwrap();
        let d = tempfile::tempdir().unwrap();
        config::update(|c| c.settings = None);
        beat(300);
        assert!(!alive());
        assert!(claim());
        unclaim();
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0);
    }

    #[test]
    fn an_interval_longer_than_the_dropdown_offers_is_honoured() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join(BEAT);
        write(&p, me(), host(), now() - 1000.0, 1800);
        assert!(read_alive(&p));
        write(&p, me(), host(), now() - 1800.0 - GRACE as f64 - 1.0, 1800);
        assert!(!read_alive(&p)); // and it still lapses, at the interval it actually declared
    }

    #[test]
    fn a_beat_is_never_read_half_written() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join(BEAT);
        write_beat(&p, 300);
        let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let (s, watched) = (stop.clone(), p.clone());
        let t = std::thread::spawn(move || {
            let mut seen = (0usize, 0usize);
            while !s.load(std::sync::atomic::Ordering::Relaxed) {
                if read_alive(&watched) {
                    seen.0 += 1
                } else {
                    seen.1 += 1
                }
            }
            seen
        });
        for _ in 0..400 {
            write_beat(&p, 300);
        }
        stop.store(true, std::sync::atomic::Ordering::Relaxed);
        let (ok, bad) = t.join().unwrap();
        assert!(ok > 0 && bad == 0, "{bad} of {} reads saw no dashboard", ok + bad);
    }

    #[test]
    fn the_pull_lock_has_one_holder_and_unclaiming_nothing_is_fine() {
        let _g = TEST_LOCK.lock().unwrap();
        let d = tempfile::tempdir().unwrap();
        beat_file(d.path());
        unclaim(); // nothing held: not an error
        assert!(claim());
        assert!(!claim()); // the second caller is told, rather than joining in
                           // another open file description on the same lock file is what a second process holds
        let other = open_lock(&d.path().join(LOCK)).unwrap();
        assert!(matches!(other.try_lock_exclusive(), Ok(false)));
        unclaim();
        assert!(matches!(other.try_lock_exclusive(), Ok(true))); // released for real, not just forgotten
        assert!(!claim()); // and now the other holder blocks us
        other.unlock().unwrap();
        assert!(claim()); // it is a lock, not a one-shot
        unclaim();
    }
}
