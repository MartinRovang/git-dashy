//! Background refresh loop and everything the UI reads. Port of dashy/core/state.py.

use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use log::{debug, error, info};

use crate::types::{Detail, DiffFile, Finding, Mark, Pr, Section};
use crate::{
    config, diff, github, heartbeat, install, log as review_log, memory, mirror, review, team, update,
};

/// The desktop popup's icon. Python pointed notify-send at dashy/notify.png on disk; the binary
/// carries it and writes it out once, beside the other temp files.
pub const NOTIFY_ICON: &[u8] = include_bytes!("../ui/notify.png");

/// Seconds since the epoch, as Python's time.time().
pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// The detail cache key: (url, updatedAt).
pub type DetailKey = (String, String);
/// The diff cache key: (repo, number, head, findings signature, diff generation).
pub type DiffKey = (String, u64, String, Vec<(String, String, String)>, u64);
/// One PR's parsed diff and the marks anchored onto it.
pub type DiffCache = (Vec<DiffFile>, Vec<Mark>);

/// An event the refresh loop sleeps on: `set` wakes it, `wait` returns true when it was set.
#[derive(Default)]
pub struct Wake {
    flag: Mutex<bool>,
    cv: Condvar,
}

impl Wake {
    pub fn set(&self) {
        *self.flag.lock().unwrap_or_else(|e| e.into_inner()) = true;
        self.cv.notify_all();
    }
    pub fn clear(&self) {
        *self.flag.lock().unwrap_or_else(|e| e.into_inner()) = false;
    }
    pub fn is_set(&self) -> bool {
        *self.flag.lock().unwrap_or_else(|e| e.into_inner())
    }
    /// True when set (now or within `d`), false when the time ran out.
    pub fn wait(&self, d: Duration) -> bool {
        let guard = self.flag.lock().unwrap_or_else(|e| e.into_inner());
        if *guard {
            return true;
        }
        let (guard, _) = self.cv.wait_timeout(guard, d).unwrap_or_else(|e| e.into_inner());
        *guard
    }
}

/// Shared between the refresh thread, the review threads and the HTTP handlers.
#[derive(Default)]
pub struct Inner {
    pub sections: Vec<Section>,
    pub fetched_at: Option<f64>,
    pub fetching: bool,
    /// Why the last refresh failed, "" while ticks are landing. See run_loop.
    pub error: String,
    pub auto: bool,
    /// The REVIEW REQUESTED urls with no verdict or review in flight, as of the last tick.
    pub pending: Vec<String>,
    /// url -> status string of a running or finished review this session started.
    pub reviews: HashMap<String, String>,
    /// urls with a review or pre-review in flight. See in_flight().
    pub running: HashSet<String>,
    /// url -> when it started, so a row in flight can say how long it has been.
    pub since: HashMap<String, f64>,
    /// Newer released version, refreshed with each fetch.
    pub update: String,
    /// ponytail: since this dashboard STARTED, and not persisted. The question a badge answers is
    /// "has anything landed that I have not looked at", and a dashboard the operator leaves running
    /// for days is exactly where that goes unnoticed. Persisting it would mean a file that changes
    /// whenever the team does, inside a directory push_dir commits, for a number that is only ever
    /// read by the header of the process that wrote it.
    pub arrived: HashMap<String, usize>,
    pub details: HashMap<DetailKey, Option<Detail>>,
    pub detailing: HashSet<DetailKey>,
    pub diffs: HashMap<DiffKey, Option<DiffCache>>,
    pub diffing: HashSet<DiffKey>,
    /// RR urls present when auto was switched on; None while auto is off.
    pub auto_baseline: Option<HashSet<String>>,
    /// ponytail: the PR's updatedAt (or head) when a finished status was last seen. A verdict
    /// describes one revision; once the PR moves, it is stale and must stop masking what GitHub now says.
    pub seen_at: HashMap<String, String>,
    /// url -> when a status landed, so an older in-flight fetch cannot baseline it.
    pub done_at: HashMap<String, f64>,
    /// urls wanted from me at the last fetch; None until the first fetch lands.
    pub known: Option<HashSet<String>>,
    /// ponytail: one draft sweep at a time; see start_sweep.
    pub sweeping: bool,
    /// The launch-time consent questions the page still shows (web::launch_asks).
    pub asks: Vec<serde_json::Value>,
    /// Lines the page shows once and acknowledges.
    pub notices: Vec<String>,
    /// The session token the server answers on; a re-exec after an update keeps it.
    pub token: String,
    pub wake: Arc<Wake>,
}

impl Inner {
    fn rr_urls(&self) -> Vec<String> {
        self.sections
            .iter()
            .filter(|s| s.name == "REVIEW REQUESTED")
            .flat_map(|s| s.prs.iter().flatten())
            .map(|p| p.url.clone())
            .collect()
    }
    /// Review-requested urls with no verdict or review in flight.
    pub fn pending_rr(&self) -> Vec<String> {
        self.rr_urls()
            .into_iter()
            .filter(|u| !self.reviews.contains_key(u))
            .collect()
    }
    fn rr_prs(&self) -> Vec<Pr> {
        self.sections
            .iter()
            .filter(|s| s.name == "REVIEW REQUESTED")
            .flat_map(|s| s.prs.iter().flatten())
            .cloned()
            .collect()
    }
}

/// Drop every entry the predicate names, so one PR keeps one entry, not one per push or review.
pub fn evict<K: Eq + std::hash::Hash, V>(cache: &mut HashMap<K, V>, drop: impl Fn(&K) -> bool) {
    cache.retain(|k, _| !drop(k));
}

/// True while a review or pre-review of this PR is running.
///
/// ponytail: membership, not a suffix. The status channel also carries a truncated stderr line, so
/// sniffing for "..." meant an error whose last line happened to end in one would pin the UI at a 50ms
/// timeout, count as a running agent, block the key from ever retrying that PR, and survive the stale
/// sweep, permanently, on wording nobody controls. The set is written by the two functions that start
/// work and cleared by the two that finish it; nothing has to be parsed.
pub fn in_flight(inner: &Inner, url: &str) -> bool {
    inner.running.contains(url)
}

/// The last non-empty line of an error, clipped to `max` chars; `fallback` when there is none.
pub fn last_line(text: &str, max: usize, fallback: &str) -> String {
    let line = text.trim().lines().last().unwrap_or(fallback);
    line.chars().take(max).collect()
}

/// What a panic said, for the header.
fn panic_text(e: &(dyn std::any::Any + Send)) -> String {
    if let Some(s) = e.downcast_ref::<String>() {
        s.clone()
    } else if let Some(s) = e.downcast_ref::<&str>() {
        s.to_string()
    } else {
        "panic".into()
    }
}

/// Re-mirror every repo `gitdashy init` registered. Never raises: a bad entry must not stop a refresh.
pub fn refresh_mirrors() {
    for (into, repo, root, _loader) in install::registered() {
        // ponytail: refresh what is THERE. init creates the mirror, so a refresh never has cause to make
        // one, and makedirs would otherwise rebuild the tree of a repo you deleted and write memory
        // back into it. Asking about `into` itself needs no recorded root and holds for any --into shape.
        if !into.is_dir() || (!root.as_os_str().is_empty() && !root.is_dir()) {
            install::unregister(&into);
            continue;
        }
        // already pulled above; and this must not touch the network
        let report = catch_unwind(AssertUnwindSafe(|| mirror::sync(&into, &repo, false, false)));
        match report {
            Ok(line) => debug!("mirror sync {}: {}", into.display(), line),
            Err(e) => error!("mirror sync {} failed: {}", into.display(), panic_text(&*e)),
        }
    }
}

#[derive(Clone, Default)]
pub struct State(pub Arc<Mutex<Inner>>);

impl State {
    pub fn new() -> State {
        Default::default()
    }

    /// The Inner, poisoned or not: a review thread that panicked must not take the UI with it.
    pub fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.0.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn waker(&self) -> Arc<Wake> {
        self.lock().wake.clone()
    }

    /// The selected PR's detail, or None while it is being fetched.
    ///
    /// ponytail: keyed by (url, updatedAt), not url. A PR that moved has a different branch head, diff
    /// size and CI result, and the pane kept showing the old ones until a restart: the same staleness
    /// the pre-review path had, for the same reason: caching what changes as though it does not.
    /// ponytail: fetched once per PR, off the draw thread. The page polls many times a second; anything
    /// that talks to the network from there would stutter the whole dashboard on every request.
    pub fn want_detail(&self, pr: &Pr) -> Option<Detail> {
        let key: DetailKey = (pr.url.clone(), pr.updated_at.clone());
        {
            let mut inner = self.lock();
            if let Some(got) = inner.details.get(&key) {
                return got.clone();
            }
            if inner.detailing.contains(&key) {
                return None;
            }
            inner.detailing.insert(key.clone());
        }
        let (me, repo, number) = (self.clone(), pr.repo().to_string(), pr.number);
        std::thread::spawn(move || {
            let got = github::detail(&repo, number);
            let mut inner = me.lock();
            // ponytail: drop what we knew about this PR at any other revision, so the cache cannot
            // grow one entry per push for a branch someone is iterating on.
            evict(&mut inner.details, |k| k.0 == key.0);
            inner.details.insert(key.clone(), got);
            inner.detailing.remove(&key);
        });
        None
    }

    /// (files, marks) for one PR's diff, or None while it is being read.
    ///
    /// ponytail: off the draw thread for the same reason want_detail is: the diff is a network read,
    /// so a slow one stuttered the whole dashboard and a timing-out one froze it. It ANCHORS here too,
    /// not in the pane: anchor() tags the lines it marks, so calling it twice over one parse appends
    /// every note twice, and the pane redraws constantly.
    pub fn want_diff(
        &self,
        repo: &str,
        number: u64,
        head: &str,
        findings: &[Finding],
    ) -> Option<(Vec<DiffFile>, Vec<Mark>)> {
        if repo.is_empty() {
            return None;
        }
        // ponytail: the findings are part of the key. A re-review changes what is marked without moving
        // the head, and the pane would have gone on showing the previous round's marks.
        let sig: Vec<(String, String, String)> = findings
            .iter()
            .map(|f| (f.kind.clone(), f.loc.clone(), f.text.clone()))
            .collect();
        // ponytail: the GENERATION is in the key. Without it f cleared diff's cache while this cache went
        // on answering from the failed read above it, so one blip pinned "no diff to show" for the
        // rest of the session: the retry reached the layer nobody was asking.
        let key: DiffKey = (
            repo.to_string(),
            number,
            head.to_string(),
            sig,
            diff::generation(),
        );
        {
            let mut inner = self.lock();
            if let Some(got) = inner.diffs.get(&key) {
                return got.clone();
            }
            if inner.diffing.contains(&key) {
                return None;
            }
            inner.diffing.insert(key.clone());
        }
        let (me, repo, head, findings) = (
            self.clone(),
            repo.to_string(),
            head.to_string(),
            findings.to_vec(),
        );
        std::thread::spawn(move || {
            let got = diff::load(&repo, number, &head, &findings);
            let mut inner = me.lock();
            evict(&mut inner.diffs, |k| k.0 == repo && k.1 == number);
            inner.diffs.insert(key.clone(), Some(got));
            inner.diffing.remove(&key);
        });
        None
    }

    /// include_existing: review what is already listed too, not just what shows up later.
    pub fn set_auto(&self, on: bool, include_existing: bool) {
        let wake = {
            let mut inner = self.lock();
            inner.auto = on;
            inner.auto_baseline = if !on {
                None
            } else if include_existing {
                Some(HashSet::new())
            } else {
                Some(inner.rr_urls().into_iter().collect())
            };
            inner.wake.clone()
        };
        if on && include_existing {
            wake.set(); // refetch now so the listed PRs start without waiting for the next tick
        }
    }

    /// Review-requested PRs with no verdict or review in flight.
    pub fn pending_rr(&self) -> Vec<Pr> {
        let inner = self.lock();
        inner
            .rr_prs()
            .into_iter()
            .filter(|p| !inner.reviews.contains_key(&p.url))
            .collect()
    }

    /// The thread's landing: the row's status, nothing spinning, nothing kept.
    fn finish(&self, url: &str, status: String) {
        {
            let mut inner = self.lock();
            inner.reviews.insert(url.to_string(), status);
            inner.running.remove(url);
            inner.seen_at.remove(url);
            inner.since.remove(url);
            inner.done_at.insert(url.to_string(), now());
        }
        self.waker().set(); // refetch so an approved PR drops off the list
    }

    /// Claim `url` for a review. False when one is already running: the caller must not spawn.
    ///
    /// ponytail: the claim IS the check. Testing in_flight() and then calling this took the lock twice,
    /// and every request has its own thread, so two POSTs for one URL both passed and both spawned:
    /// two "on its way" comments, two paid model runs, two posted verdicts.
    fn begin(&self, url: &str, status: &str) -> bool {
        let mut inner = self.lock();
        if !inner.running.insert(url.to_string()) {
            return false;
        }
        inner.reviews.insert(url.to_string(), status.to_string());
        inner.since.insert(url.to_string(), now());
        true
    }

    /// False when a review of this PR is already running, and nothing was started.
    pub fn start_review(&self, pr: &Pr) -> bool {
        let model = config::get().model;
        let (me, pr) = (self.clone(), pr.clone());
        if !self.begin(&pr.url, "reviewing...") {
            return false;
        }
        std::thread::spawn(move || {
            // ponytail: the row spins until this thread writes a status, so a failure review() does not
            // catch would leave it spinning for the rest of the session with nothing to press. Catch here
            // too, and the row says what happened.
            info!("review {} with {}", pr.url, model);
            let status = match catch_unwind(AssertUnwindSafe(|| review::review(&pr, &model))) {
                Ok(Ok(status)) => status,
                Ok(Err(e)) => {
                    error!("review {} failed: {e:#}", pr.url);
                    format!("error: {e}").chars().take(88).collect()
                }
                Err(e) => {
                    error!("review {} failed: {}", pr.url, panic_text(&*e));
                    format!("error: {}", panic_text(&*e)).chars().take(88).collect()
                }
            };
            info!("review {} -> {}", pr.url, status);
            me.finish(&pr.url, status);
        });
        true
    }

    /// Pre-review one of MY PRs. Posts nothing; the file it writes is found again by its name.
    /// False when a review of this PR is already running, and nothing was started.
    pub fn start_self_review(&self, pr: &Pr) -> bool {
        let model = config::get().model;
        let (me, pr) = (self.clone(), pr.clone());
        if !self.begin(&pr.url, "pre-reviewing...") {
            return false;
        }
        std::thread::spawn(move || {
            // ponytail: same reason as start_review: a dead thread must not wedge the row
            let status = match catch_unwind(AssertUnwindSafe(|| review::self_review(&pr, &model))) {
                Ok(Ok((status, _dest))) => status, // ponytail: the path is not kept: it is derivable
                Ok(Err(e)) => {
                    error!("self-review {} failed: {e:#}", pr.url);
                    format!("error: {e}").chars().take(88).collect()
                }
                Err(e) => {
                    error!("self-review {} failed: {}", pr.url, panic_text(&*e));
                    format!("error: {}", panic_text(&*e)).chars().take(88).collect()
                }
            };
            info!("self-review {} -> {}", pr.url, status);
            me.finish(&pr.url, status);
        });
        true
    }

    /// Refresh forever on this thread. A tick that fails is reported and retried; it never ends the thread.
    ///
    /// ponytail: the guard is here rather than on each call this makes, because the ways a tick can
    /// fail are not enumerable: it reads a log other machines append to and merge, a registry, a
    /// settings file and several subprocesses. One unreadable line in the review log used to unwind out
    /// of this thread, and nothing restarts it: fetching stayed true, no key reaches a thread that
    /// is gone, and f only sets an event nobody waits on any more. The dashboard was over, silently.
    pub fn run_loop(&self) {
        let wake = self.waker();
        loop {
            let t0 = now();
            // ponytail: from THIS attempt, not from fetched_at, when the tick failed. That holds the last
            // SUCCESSFUL fetch, so the moment one tick failed the deadline was already in the past: the
            // loop fell straight out of the wait and retried every second, hammering the API for as long
            // as the failure lasted. It also covers the FIRST tick, where fetched_at is still None.
            // ponytail: the fetch's own time otherwise, so the header's countdown agrees.
            let base = if self.refresh(t0) {
                self.lock().fetched_at.unwrap_or(t0)
            } else {
                t0
            };
            // ponytail: `base + interval` is evaluated per slice, and `interval` is the reason.
            // Hoisting it into a variable above the loop is the natural way to write this and silently
            // breaks `i`: the settings screen changes the interval and does NOT wake the loop, so
            // dropping 30m to 1m waited out the remaining 29 instead of refetching now.
            while !wake.wait(Duration::from_secs(1)) && now() < base + config::get().interval as f64 {
                // 1s slices, so a change to either side takes effect within the second
            }
            wake.clear();
        }
    }

    /// One tick, and the failure path: true when it landed.
    fn refresh(&self, t0: f64) -> bool {
        let failed = match catch_unwind(AssertUnwindSafe(|| self.tick_inner(t0))) {
            Ok(Ok(())) => return true,
            Ok(Err(e)) => format!("{e:#}"),
            Err(e) => panic_text(&*e),
        };
        error!("tick failed: {failed}"); // ponytail: the whole point: a failed tick is a row, not the end
        let mut inner = self.lock();
        inner.fetching = false;
        inner.error = last_line(&failed, 60, "error");
        false
    }

    pub fn wake(&self) {
        self.waker().set();
    }

    /// Pool and cross-check drafts on a thread of their own. Returns at once; never raises.
    ///
    /// ponytail: NOT on the refresh thread. It was, immediately after the pull and before
    /// `github::fetch()`, with `fetching` already true and a 300s model timeout, so the first sweep
    /// after an upgrade, with a whole backlog newly poolable, blocked the PR list for as long as the
    /// model took. Catching the exception was never the risk; the wait was. The comment claimed the
    /// list must not depend on a model and the code put one in front of it.
    /// ponytail: one at a time. A slow sweep must not have a second one started on top of it by the
    /// next tick, both writing the same pool files and both pushing the same checkout.
    pub fn start_sweep(&self) {
        {
            let mut inner = self.lock();
            if inner.sweeping {
                return;
            }
            inner.sweeping = true;
        }
        let me = self.clone();
        std::thread::spawn(move || {
            let model = config::get().model;
            if let Err(e) = catch_unwind(AssertUnwindSafe(|| memory::sweep(&model))) {
                error!("draft sweep failed: {}", panic_text(&*e)); // surfaced in the debug log, never on the header
            }
            me.lock().sweeping = false;
        });
    }

    /// What has arrived since anyone last looked, and forget it. Under the lock: the refresh thread
    /// adds to this while the UI reads it, and an arrival landing between a copy and a clear was
    /// silently dropped.
    pub fn take_arrivals(&self) -> HashMap<String, usize> {
        std::mem::take(&mut self.lock().arrived)
    }

    /// One refresh: pull, mirror, fetch, sweep stale verdicts, start auto reviews, notify.
    pub fn tick(&self, t0: f64) {
        self.refresh(t0);
    }

    fn tick_inner(&self, t0: f64) -> anyhow::Result<()> {
        debug!("tick");
        let interval = config::get().interval;
        self.lock().fetching = true; // ponytail: the failure path clears this under the lock; both sides now agree
                                     // ponytail: FIRST, and before the pull it is a claim about. Anything reading this while the tick
                                     // runs should be told a dashboard is here, or it does the pull itself, which is the one thing
                                     // the beat exists to prevent. Written every tick rather than once at startup, so a dashboard
                                     // that stopped refreshing (suspended, wedged) stops counting as one.
        heartbeat::beat(interval);
        // ponytail: the lines around the pull, with your own facts excluded; see memory::arrivals.
        // ponytail: the snapshot must not be able to cost the pull. One unreadable team file (a
        // permission, a half-written merge, a non-UTF-8 byte a teammate pushed) must not skip that
        // tick's git entirely, for the sake of a badge. The badge is the optional half.
        let was: HashMap<String, HashSet<(Option<String>, String)>> = match catch_unwind(|| {
            team::joined()
                .into_iter()
                .map(|k| (k.clone(), memory::team_lines(&k)))
                .collect()
        }) {
            Ok(was) => was,
            Err(e) => {
                error!("could not count team facts before the pull: {}", panic_text(&*e));
                HashMap::new()
            }
        };
        // ponytail: the tick takes the LOCK too. It used to beat and then pull unguarded, on the
        // argument that a beat is enough, but a hook sync that claimed the lock while no dashboard was
        // up keeps pulling after one starts, and the first tick then rebased the same checkout beside
        // it. "Only one process ever pulls a given checkout" was in the README and was not true.
        // ponytail: the lock is not waited for. A tick that cannot have it skips the pull and takes the
        // next one; the alternative is blocking the refresh thread, and everything after this line
        // (the PR list, the mirrors) has nothing to do with the team's git.
        if heartbeat::claim() {
            let pulled = catch_unwind(team::pull); // newest team log + memory before we read them
            heartbeat::unclaim();
            if let Err(e) = pulled {
                std::panic::resume_unwind(e);
            }
        }
        // ponytail: guarded on THIS side too. The pull is what brings in the unreadable file, so the
        // read after it is the likelier of the two to meet one, and everything below here, the sweep,
        // the mirrors and the PR list, was riding on a counter.
        let grew: HashMap<String, usize> = match catch_unwind(AssertUnwindSafe(|| {
            was.iter()
                .map(|(k, before)| (k.clone(), memory::arrivals(k, before)))
                .filter(|(_, n)| *n > 0)
                .collect()
        })) {
            Ok(grew) => grew,
            Err(e) => {
                error!("could not count what the pull brought: {}", panic_text(&*e));
                HashMap::new()
            }
        };
        if !grew.is_empty() {
            let mut inner = self.lock(); // ponytail: read on the UI thread, like every other cross-thread field
            for (key, n) in grew {
                *inner.arrived.entry(key).or_insert(0) += n;
            }
        }
        self.start_sweep();
        memory::history(); // ponytail: before the backup, so the first commit is memory as it arrived,
        memory::backup("tick"); // and so the Memory row can say "no history" before a write, not after
        refresh_mirrors(); // ponytail: here, not in a session hook: no global config, no timeout budget
        let mut data = github::fetch();
        let stale = review_log::mark_rereviews(&mut data);
        let newer = update::update_available();
        if self.lock().fetched_at.is_none() {
            // let the splash breathe on the first load
            let left = config::SPLASH_MIN - (now() - t0);
            if left > 0.0 {
                std::thread::sleep(Duration::from_secs_f64(left));
            }
        }
        // ponytail: beaten AGAIN, at the end. The first beat is stamped at tick start and alive() allows
        // one interval plus GRACE, but the next tick starts at fetched_at + interval, so any tick
        // longer than GRACE made a healthy dashboard read as dead until it came round again, and one
        // slow team pull is enough at a 120s git timeout. So the tick beats again at the end.
        heartbeat::beat(interval);
        let new: Vec<Pr> = {
            let mut inner = self.lock();
            inner.sections = data.clone();
            inner.fetched_at = Some(now());
            inner.update = newer;
            inner.fetching = false;
            inner.error = String::new(); // this tick landed, so whatever the last one said is over
            for u in &stale {
                // forget the old verdict so r / auto can review the new push
                // ponytail: same guard as the sweep below. `stale` is read off the REVIEWED section of
                // THIS fetch, so a fetch that started before our verdict landed still holds the previous
                // entry, calls the head we just reviewed a new push, drops the verdict, and auto starts
                // the identical review a second later.
                if !in_flight(&inner, u) && inner.done_at.get(u).copied().unwrap_or(0.0) <= t0 {
                    inner.reviews.remove(u);
                    inner.seen_at.remove(u);
                }
            }
            // ponytail: and forget it for ANY row whose PR has moved since. `stale` comes from
            // log::mark_rereviews, which only ever names REVIEW REQUESTED urls, so a finished
            // pre-review masked GitHub's decision on a MINE row until restart, and a colleague
            // approving your PR never showed. One rule for both: a verdict describes one revision.
            // ponytail: baselined on the first fetch that STARTED after the work finished. Posting a
            // review bumps updatedAt itself, so the value we held is already stale, and a fetch that
            // was in flight when the verdict landed carries the pre-post value, which is why the
            // start time is compared rather than merely "the next fetch".
            for s in &data {
                if s.name == "REVIEWED" {
                    // ponytail: not a live row. Its updatedAt is the log timestamp, which never equals
                    // the live row's value, so sweeping it dropped every verdict on the next tick.
                    continue;
                }
                for p in s.prs.iter().flatten() {
                    let u = &p.url;
                    if !inner.reviews.contains_key(u) || in_flight(&inner, u) {
                        continue;
                    }
                    // ponytail: by head on a REVIEW REQUESTED row, like mark_rereviews. updatedAt there
                    // comes from the lagging search index (the tick after a verdict can still read the
                    // pre-post value and the next the post-post one) and a reply on the thread bumps
                    // it too; both dropped the verdict and auto reviewed the same head again. On MINE
                    // rows updatedAt stays: a colleague's approval must be allowed to unmask GitHub.
                    // A PR without the field simply never goes stale.
                    let at = if s.name == "REVIEW REQUESTED" {
                        // no head means the graphql call failed; not a reason to call it moved
                        if p.head.is_empty() {
                            continue;
                        }
                        p.head.clone()
                    } else {
                        if p.updated_at.is_empty() {
                            continue;
                        }
                        p.updated_at.clone()
                    };
                    if inner.done_at.get(u).copied().unwrap_or(0.0) > t0 {
                        // ponytail: this fetch STARTED before the work finished, so it carries the
                        // pre-post updatedAt. Baselining on it would sweep our own verdict on the
                        // next tick, which the comment below claimed to avoid and did not.
                        continue;
                    }
                    match inner.seen_at.get(u) {
                        None => {
                            inner.seen_at.insert(u.clone(), at);
                        }
                        Some(seen) if *seen != at => {
                            inner.reviews.remove(u);
                            inner.seen_at.remove(u);
                        }
                        Some(_) => {}
                    }
                }
            }
            inner.pending = inner.pending_rr();
            match (&inner.auto, &inner.auto_baseline) {
                (true, Some(baseline)) => inner
                    .rr_prs()
                    .into_iter()
                    .filter(|p| !baseline.contains(&p.url) && !inner.reviews.contains_key(&p.url))
                    .collect(),
                _ => Vec::new(),
            }
        };
        for p in &new {
            self.start_review(p); // already running is not an error here: auto only skips it
        }
        let asks: Vec<&Section> = data
            .iter()
            .filter(|s| s.name == "REVIEW REQUESTED" || s.name == "ASSIGNED")
            .collect();
        // a failed section would look like every PR left, then came back
        if asks.iter().all(|s| s.err.as_deref().unwrap_or("").is_empty()) {
            let wanted: HashMap<String, (&Pr, &str)> = asks
                .iter()
                .flat_map(|s| {
                    s.prs
                        .iter()
                        .flatten()
                        .map(move |p| (p.url.clone(), (p, s.name.as_str())))
                })
                .collect();
            let (known, notify_on) = (self.lock().known.clone(), config::get().notify);
            if let (Some(known), true) = (known, notify_on) {
                let mut fresh: Vec<&String> = wanted.keys().filter(|u| !known.contains(*u)).collect();
                fresh.sort();
                for u in fresh {
                    let (p, section) = wanted[u];
                    notify(p, section);
                }
            }
            self.lock().known = Some(wanted.keys().cloned().collect());
        }
        Ok(())
    }
}

/// Where notify-send finds the icon: written out of the binary once.
fn notify_icon() -> PathBuf {
    let path = std::env::temp_dir().join("gitdashy-notify.png");
    if !path.is_file() {
        let _ = std::fs::write(&path, NOTIFY_ICON);
    }
    path
}

/// The notify-send argv for a PR that just asked for me. None on a payload missing a field (a deleted author).
pub fn notify_cmd(pr: &Pr, section: &str) -> Option<Vec<String>> {
    let what = if section == "REVIEW REQUESTED" {
        "wants a review"
    } else {
        "assigned you"
    };
    let author = pr.author.as_ref()?;
    let icon = notify_icon();
    Some(
        [
            "notify-send",
            "-a",
            "gitdashy",
            "-u",
            "normal",
            "-c",
            "im.received",
            "-A",
            "open=Open PR",
            "-i",
        ]
        .into_iter()
        .map(String::from)
        .chain([
            icon.to_string_lossy().into_owned(),
            format!("#{} {}", pr.number, pr.title),
            format!("<b>{}</b> · {} {}", pr.repository.name, author.login, what),
        ])
        .collect(),
    )
}

/// Desktop popup with an Open button. Silent if notify-send is missing or the payload is odd (deleted author).
pub fn notify(pr: &Pr, section: &str) {
    let Some(argv) = notify_cmd(pr, section) else {
        return;
    };
    let url = pr.url.clone();
    // ponytail: -A blocks until dismissed, so wait in a thread; notify-send only (Linux)
    std::thread::spawn(move || {
        // a popup is decoration; the refresh loop must outlive it
        let out = std::process::Command::new(&argv[0]).args(&argv[1..]).output();
        if let Ok(out) = out {
            if String::from_utf8_lossy(&out.stdout).trim() == "open" {
                github::open_in_browser(&url);
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Login, Repository};

    fn pr(url: &str) -> Pr {
        Pr {
            number: 7,
            title: "T".into(),
            url: url.into(),
            updated_at: "2020-01-01T00:00:00Z".into(),
            author: Some(Login { login: "me".into() }),
            repository: Repository {
                name_with_owner: "a/b".into(),
                name: "b".into(),
            },
            ..Default::default()
        }
    }

    fn section(name: &str, prs: Option<Vec<Pr>>, err: Option<&str>) -> Section {
        Section {
            name: name.into(),
            prs,
            err: err.map(String::from),
        }
    }

    #[test]
    fn evict_drops_every_entry_the_predicate_names() {
        let mut cache: HashMap<DetailKey, Option<Detail>> = HashMap::new();
        cache.insert(("u".into(), "1".into()), None);
        cache.insert(("u".into(), "2".into()), None);
        cache.insert(("v".into(), "1".into()), None);
        evict(&mut cache, |k| k.0 == "u");
        assert_eq!(cache.len(), 1);
        assert!(cache.contains_key(&("v".to_string(), "1".to_string())));
    }

    #[test]
    fn in_flight_is_the_set_not_a_suffix() {
        // an error whose wording ends in "..." must not count as a running agent
        let mut inner = Inner::default();
        inner
            .reviews
            .insert("u".into(), "error: timed out waiting...".into());
        assert!(!in_flight(&inner, "u"));
        inner.running.insert("u".into());
        assert!(in_flight(&inner, "u"));
    }

    #[test]
    fn notify_cmd_pins_the_payload_contract() {
        let cmd = notify_cmd(&pr("u"), "ASSIGNED").unwrap();
        assert_eq!(cmd[0], "notify-send");
        assert!(cmd.contains(&"-A".to_string()));
        assert_eq!(cmd[cmd.len() - 2], "#7 T");
        assert_eq!(cmd[cmd.len() - 1], "<b>b</b> · me assigned you");
        assert!(notify_cmd(&pr("u"), "REVIEW REQUESTED")
            .unwrap()
            .last()
            .unwrap()
            .contains("wants a review"));
        let mut gone = pr("u");
        gone.author = None;
        assert!(notify_cmd(&gone, "ASSIGNED").is_none()); // a deleted account; notify() swallows this
    }

    #[test]
    fn set_auto_baselines_the_listed_rr_and_off_clears_it() {
        let st = State::new();
        st.lock().sections = vec![section(
            "REVIEW REQUESTED",
            Some(vec![pr("old"), pr("done")]),
            None,
        )];
        st.lock().reviews.insert("done".into(), "✓ approved".into());
        assert_eq!(
            st.pending_rr().iter().map(|p| p.url.as_str()).collect::<Vec<_>>(),
            ["old"]
        );
        st.set_auto(true, false);
        assert_eq!(st.lock().auto_baseline.clone().unwrap().len(), 2);
        assert!(!st.lock().wake.is_set());
        st.set_auto(true, true);
        assert!(st.lock().auto_baseline.clone().unwrap().is_empty());
        assert!(st.lock().wake.is_set()); // refetch now so the listed PRs start
        st.set_auto(false, false);
        let inner = st.lock();
        assert!(!inner.auto && inner.auto_baseline.is_none());
    }

    #[test]
    fn take_arrivals_empties_the_badge() {
        let st = State::new();
        st.lock().arrived.insert("t1".into(), 3);
        assert_eq!(st.take_arrivals().get("t1"), Some(&3));
        assert!(st.take_arrivals().is_empty());
    }

    #[test]
    fn the_header_gets_the_last_line_clipped() {
        assert_eq!(last_line("boom\nsecond line\n", 60, "?"), "second line");
        assert_eq!(last_line("   ", 60, "Error"), "Error");
        assert_eq!(last_line(&"x".repeat(80), 60, "?").len(), 60);
    }

    #[test]
    fn wake_wait_returns_true_when_set_and_false_on_timeout() {
        let w = Wake::default();
        assert!(!w.wait(Duration::from_millis(5)));
        w.set();
        assert!(w.wait(Duration::from_millis(5)));
        w.clear();
        assert!(!w.is_set());
    }

    #[test]
    fn sweep_runs_one_at_a_time() {
        let st = State::new();
        st.lock().sweeping = true;
        st.start_sweep(); // returns at once: the flag says one is already running
        assert!(st.lock().sweeping);
    }
}
