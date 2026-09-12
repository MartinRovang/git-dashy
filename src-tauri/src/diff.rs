//! A PR's unified diff, parsed, with the review's findings anchored onto it. Port of dashy/core/diff.py.
//! ponytail: the diff comes from github::diff (the API), not a `gh` subprocess.
//!
//! ponytail: the pane used to list findings as text, `auto.py:141  verdict dropped mid-sweep`, and a
//! line reference you have to go and look up somewhere else is a reference nobody follows. The diff is
//! what the reviewer read; putting each finding back on the line it is about is the whole feature.

use std::sync::{Mutex, OnceLock};

use regex::Regex;

use crate::types::{DiffFile, Finding, Hunk, Line, Mark};

/// Severity order; the pane paints them.
pub const ORDER: &[&str] = &["blocking", "note", "nit"];
// ponytail: the CYCLE is the source of the default, not a second copy of it. c used to step a dict
// {3: 8, 8: 0, 0: 3} while State carried its own literal 3: two defaults for one number, and moving
// either one turned the key that cycles context into a KeyError inside draw().
/// Lines kept either side of a marked line; c steps this ring; [0] is the default.
pub const CONTEXTS: &[usize] = &[3, 8, 0];
/// Diffs kept; a dashboard shows a handful of PRs and re-reads the rest in one request.
const KEEP: usize = 24;
// PORT-NOTE: Mark.file is `usize` in types.rs where Python had `None` for a finding that names a file
// the diff does not touch. NO_FILE is that None: it sorts after every real index, and `landed()` is
// the question the pane asks. Likewise Line.marks holds the KINDS anchored on the line (Python put
// the mark dicts themselves there, and the pane matched by identity); `worst()` needs only the kinds.
/// `Mark.file` for a finding that names a file this diff does not touch.
pub const NO_FILE: usize = usize::MAX;

/// Whether a mark landed on a file the diff touches.
pub fn landed(m: &Mark) -> bool {
    m.file != NO_FILE
}

// ponytail: None is "the fetch failed", "" is "it succeeded and the diff was empty". One map says both,
// and retry() is then the difference between them: a parallel FAILED set was a second place to forget.
// The lock is not decoration: fetch() runs on a worker thread while f calls retry() on the UI one.
// ponytail: BOUNDED, and LRU. Only failures were ever dropped (by retry()), so every diff that WAS
// produced stayed for the life of the process: a push adds an entry rather than replacing one, and each
// holds the whole diff text. The layer above this one goes to the trouble of evicting per PR; the
// layer actually holding the megabytes did not. Ordered by last use, oldest first, newest last.
type Key = (String, u64, String);

#[derive(Default)]
struct Cache {
    /// (repo, number, head) -> diff text | None. Keyed by HEAD, so a push invalidates it.
    items: Vec<(Key, Option<String>)>,
    /// Bumped by retry(); part of the key State caches on, so f reaches past ITS cache too.
    gen: u64,
}

fn cache() -> &'static Mutex<Cache> {
    static CACHE: OnceLock<Mutex<Cache>> = OnceLock::new();
    CACHE.get_or_init(Default::default)
}

fn hunk_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@").expect("hunk regex"))
}

/// Severity glyphs the pane paints: blocking ◆, note ◇, nit ·. "" for a kind ORDER does not name.
pub fn mark(kind: &str) -> &'static str {
    match kind {
        "blocking" => "◆",
        "note" => "◇",
        "nit" => "·",
        _ => "",
    }
}

/// Severity order, least severe last for anything ORDER does not name.
///
/// ponytail: ORDER.index raised, and it is reached from inside draw(). log.KINDS is the list of kinds
/// and it is a table someone will add a row to: a fourth kind should sort last and paint plainly, not
/// take the dashboard down on the next redraw.
pub fn rank(kind: &str) -> usize {
    ORDER.iter().position(|k| *k == kind).unwrap_or(ORDER.len())
}

/// The unified diff for one PR, cached on its head sha. "" when it cannot be produced.
///
/// ponytail: never fails. It is reached from a keypress, and a PR whose diff cannot be printed (too
/// large, a fork it cannot see, no network) must leave an empty pane with a reason on it rather than
/// take the dashboard down. "" is that reason; the caller says so.
pub fn fetch(repo: &str, number: u64, head: &str) -> String {
    fetch_in(cache(), KEEP, (repo.into(), number, head.into()), || {
        crate::github::diff(repo, number).ok()
    })
}

fn fetch_in(cache: &Mutex<Cache>, keep: usize, key: Key, get: impl FnOnce() -> Option<String>) -> String {
    {
        let mut c = cache.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(i) = c.items.iter().position(|(k, _)| *k == key) {
            let hit = c.items.remove(i);
            let text = hit.1.clone().unwrap_or_default();
            c.items.push(hit); // ponytail: a look is a use, or the pane you are reading ages out
            return text;
        }
    }
    // ponytail: OUTSIDE the lock. It is a network call with a ceiling, and holding the lock across
    // it would park the UI thread's retry() behind the network for as long as the request takes.
    // ponytail: the failure is CACHED too. Returning "" uncached meant a PR whose diff times out
    // re-ran the request on every look at it and never got past it: the one input for which
    // the cache existed was the one input it did not cover.
    let got = get();
    let mut c = cache.lock().unwrap_or_else(|e| e.into_inner());
    c.items.retain(|(k, _)| *k != key); // ponytail: re-inserted, so a refetch of a known PR moves to the newest end
    c.items.push((key, got.clone()));
    while c.items.len() > keep {
        c.items.remove(0);
    }
    got.unwrap_or_default()
}

/// Bumped every time retry() drops a failure, so caches ABOVE this one can key on it and follow.
///
/// ponytail: clearing the cache was not enough. State caches (files, marks) per PR and answers from that
/// before fetch() is ever reached, so f cleared the layer nothing was reading and the pane went on
/// showing "no diff to show" for the rest of the session. The remedy has to reach the layer that
/// actually answers, and a generation in the key is how it does that without State knowing why.
pub fn generation() -> u64 {
    cache().lock().unwrap_or_else(|e| e.into_inner()).gen
}

/// Forget the diffs that FAILED, so the next look tries again. Keeps the ones it read.
///
/// ponytail: caching a failure stops the retry storm but makes a dropped network permanent for the
/// life of the process. f already means "go and look again", so it clears these and nothing else: a
/// diff that really is empty is not re-fetched twenty times because someone pressed refresh.
pub fn retry() {
    retry_in(cache());
}

fn retry_in(cache: &Mutex<Cache>) -> usize {
    let mut c = cache.lock().unwrap_or_else(|e| e.into_inner());
    let before = c.items.len();
    c.items.retain(|(_, v)| v.is_some());
    let failed = before - c.items.len();
    if failed > 0 {
        c.gen += 1;
    }
    failed
}

/// Files from a unified diff. Malformed input yields what it could read.
///
/// ponytail: `n` is the NEW line number, because that is what a review cites: a finding says
/// auto.py:141 meaning the file as it will be. A removed line has no new number, so it carries the
/// position it sits at and is marked `del` (with its old-side number), which keeps it in order without
/// pretending to be a line.
pub fn parse(text: &str) -> Vec<DiffFile> {
    let mut files: Vec<DiffFile> = Vec::new();
    let mut in_hunk = false;
    let (mut new, mut old) = (0u32, 0u32);
    for raw in text.lines() {
        if let Some(rest) = raw.strip_prefix("diff --git ") {
            let path = raw.split_once(" b/").map(|(_, b)| b).unwrap_or(rest);
            files.push(DiffFile {
                path: path.into(),
                ..Default::default()
            });
            in_hunk = false;
            continue;
        }
        let Some(cur) = files.last_mut() else { continue };
        if let Some(path) = raw.strip_prefix("+++ b/").filter(|_| !in_hunk) {
            // ponytail: `!in_hunk` or an ADDED LINE reading "+++ b/x" rewrites the path of the file it
            // is in. A PR body or doc quoting a diff does exactly that, and this repo writes them constantly.
            cur.path = path.into(); // the authoritative name; the `diff --git` line quotes odd paths
        } else if let Some(m) = hunk_re().captures(raw) {
            old = m[1].parse().unwrap_or(0);
            new = m[2].parse().unwrap_or(0);
            cur.hunks.push(Hunk {
                header: raw.trim_end().into(),
                start: new,
                lines: Vec::new(),
            });
            in_hunk = true;
        } else if in_hunk && (raw.is_empty() || matches!(raw.as_bytes()[0], b'+' | b'-' | b' ')) {
            let sign = raw.chars().next().unwrap_or(' ');
            let body = raw.get(1..).unwrap_or("").to_string();
            let Some(hunk) = cur.hunks.last_mut() else {
                continue;
            };
            let mut line = Line {
                n: Some(new),
                sign: sign.to_string(),
                text: body,
                del: None,
                marks: Vec::new(),
            };
            match sign {
                '-' => {
                    line.del = Some(old);
                    old += 1;
                    cur.dele += 1;
                }
                '+' => {
                    cur.add += 1;
                    new += 1;
                }
                _ => {
                    old += 1;
                    new += 1;
                }
            }
            hunk.lines.push(line);
        }
    }
    files.retain(|f| !f.hunks.is_empty());
    files
}

/// "a/b.py:141" -> ("a/b.py", 141); "a/b.py" -> ("a/b.py", 0). A file-only finding still lands.
///
/// ponytail: "a/b.py:141:5" is a COLUMN, and the reviewer writes it. One rpartition read that as
/// ("a/b.py:141", 5): a path no file matches and a line number that is really a column, so the finding
/// silently became an orphan. The column is dropped: the pane anchors to lines, and a line is what the
/// reader is being sent to.
pub fn where_(loc: &str) -> (String, u32) {
    let loc = loc.trim();
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let Some((path, tail)) = loc.rsplit_once(':').filter(|(p, t)| !p.is_empty() && digits(t)) else {
        return (loc.into(), 0);
    };
    match path.rsplit_once(':') {
        Some((head, mid)) if !head.is_empty() && digits(mid) => (head.into(), mid.parse().unwrap_or(0)),
        _ => (path.into(), tail.parse().unwrap_or(0)),
    }
}

/// Whether a finding's path names this diff file. Suffix match, because a review cites a basename
/// as often as a full path (`auto.py:141` for `gitdashy/core/auto.py`) and the diff has the truth.
pub fn same_file(a: &str, b: &str) -> bool {
    let (a, b) = (a.trim_matches('/'), b.trim_matches('/'));
    !a.is_empty() && (a == b || b.ends_with(&format!("/{a}")) || a.ends_with(&format!("/{b}")))
}

/// Put each finding on the line it names; tags the lines. Marks in file-then-line order.
///
/// `file` is the index of the file it is in, or NO_FILE when the finding names a file this diff
/// does not touch.
///
/// ponytail: a finding that lands nowhere is KEPT. A review's most important line is sometimes about a
/// file the diff does not contain (something missing, something the change should have touched) and
/// silently dropping it would make the pane quietly less honest than the summary. The pane holds up
/// the other half of that: it gives every mark a row, tagged onto its line or listed on its own.
pub fn anchor(files: &mut [DiffFile], findings: &[Finding]) -> Vec<Mark> {
    let mut marks = Vec::with_capacity(findings.len());
    for f in findings {
        let (path, n) = where_(&f.loc);
        let hit = files.iter().position(|d| same_file(&path, &d.path));
        let mark = Mark {
            kind: f.kind.clone(),
            loc: f.loc.clone(),
            text: f.text.clone(),
            path,
            n,
            file: hit.unwrap_or(NO_FILE),
        };
        if let Some(i) = hit.filter(|_| n > 0) {
            if let Some(l) = files[i]
                .hunks
                .iter_mut()
                .flat_map(|h| h.lines.iter_mut())
                .find(|l| l.n == Some(n) && l.del.is_none())
            {
                l.marks.push(mark.kind.clone());
            }
        }
        marks.push(mark);
    }
    marks.sort_by_key(|m| (!landed(m), m.file, m.n, rank(&m.kind)));
    marks
}

/// The most severe mark on a line, "" when none.
pub fn worst(line: &Line) -> String {
    line.marks
        .iter()
        .min_by_key(|k| rank(k))
        .cloned()
        .unwrap_or_default()
}

/// Only hunks that carry a mark and only lines within `context` of one. "marks only".
///
/// ponytail: a review of a 1,400-line diff has four findings in it, and scrolling to them is the work
/// the pane exists to remove. Full diff is one keypress away for when the answer is not on the line.
pub fn narrow(files: &[DiffFile], context: usize) -> Vec<DiffFile> {
    let mut out = Vec::new();
    for f in files {
        let mut hunks = Vec::new();
        for h in &f.hunks {
            let at: Vec<usize> = h
                .lines
                .iter()
                .enumerate()
                .filter(|(_, l)| !l.marks.is_empty())
                .map(|(i, _)| i)
                .collect();
            if at.is_empty() {
                continue;
            }
            let near = |i: usize| at.iter().any(|&a| i + context >= a && i <= a + context);
            let lines = h
                .lines
                .iter()
                .enumerate()
                .filter(|(i, _)| near(*i))
                .map(|(_, l)| l.clone())
                .collect();
            hunks.push(Hunk {
                header: h.header.clone(),
                start: h.start,
                lines,
            });
        }
        if !hunks.is_empty() {
            out.push(DiffFile {
                path: f.path.clone(),
                add: f.add,
                dele: f.dele,
                hunks,
            });
        }
    }
    out
}

/// (files, marks) for a PR: the parsed diff with its review anchored. Empty when there is none.
pub fn load(repo: &str, number: u64, head: &str, findings: &[Finding]) -> (Vec<DiffFile>, Vec<Mark>) {
    let mut files = parse(&fetch(repo, number, head));
    let marks = anchor(&mut files, findings);
    (files, marks)
}

#[cfg(test)]
mod tests {
    use super::*;

    const DIFF: &str = "diff --git a/gitdashy/auto.py b/gitdashy/auto.py
index 1111111..2222222 100644
--- a/gitdashy/auto.py
+++ b/gitdashy/auto.py
@@ -136,7 +136,12 @@ class Auto:
     def _sweep(self, prs):
         for pr in prs:
-            self.in_flight.discard(pr.id)
+            self.in_flight.discard(pr.id)
+            verdict = self._verdict_for(pr)
+            if verdict is None:
+                continue
             self.persist_verdict(pr, verdict)
diff --git a/CHANGELOG.md b/CHANGELOG.md
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,4 +1,6 @@
 # Changelog
+## 1.34.0
+- auto: keep the verdict when a tick lands mid-sweep
 ## 1.33.2
";

    fn finding(kind: &str, loc: &str, text: &str) -> Finding {
        Finding {
            kind: kind.into(),
            loc: loc.into(),
            text: text.into(),
        }
    }

    fn line_139(files: &[DiffFile]) -> &Line {
        files[0].hunks[0]
            .lines
            .iter()
            .find(|l| l.n == Some(139) && l.del.is_none())
            .unwrap()
    }

    fn key(n: u64) -> Key {
        ("a/b".into(), n, "sha".into())
    }

    #[test]
    fn a_hunk_numbers_lines_the_way_a_review_cites_them() {
        let files = parse(DIFF);
        assert_eq!(
            files.iter().map(|f| f.path.as_str()).collect::<Vec<_>>(),
            ["gitdashy/auto.py", "CHANGELOG.md"]
        );
        let auto = &files[0];
        assert_eq!((auto.add, auto.dele), (4, 1));
        let lines = &auto.hunks[0].lines;
        let got: Vec<(u32, &str)> = lines.iter().map(|l| (l.n.unwrap(), l.sign.as_str())).collect();
        assert_eq!(
            got,
            [
                (136, " "),
                (137, " "),
                (138, "-"),
                (138, "+"),
                (139, "+"),
                (140, "+"),
                (141, "+"),
                (142, " ")
            ]
        );
        assert_eq!(lines[2].del, Some(138));
        assert!(lines[3].del.is_none());
        assert_eq!(lines[2].text, "            self.in_flight.discard(pr.id)");
        assert_eq!(auto.hunks[0].start, 136);
    }

    #[test]
    fn a_finding_lands_on_the_line_it_names() {
        let mut files = parse(DIFF);
        let marks = anchor(
            &mut files,
            &[
                finding("blocking", "auto.py:139", "verdict dropped mid-sweep"),
                finding("nit", "CHANGELOG.md", "entry missing"),
            ],
        );
        assert_eq!(
            marks.iter().map(|m| m.kind.as_str()).collect::<Vec<_>>(),
            ["blocking", "nit"]
        );
        assert_eq!(marks[0].file, 0);
        assert_eq!(marks[1].file, 1);
        let line = line_139(&files);
        assert_eq!(line.marks, ["blocking"]);
        assert_eq!(worst(line), "blocking");
        assert_eq!(marks[0].path, "auto.py"); // a basename matches a full path, because a review cites either
    }

    #[test]
    fn a_finding_about_a_file_the_diff_does_not_touch_is_kept() {
        let marks = anchor(
            &mut parse(DIFF),
            &[finding("note", "nowhere/at/all.py:9", "missing")],
        );
        assert_eq!(marks.len(), 1);
        assert!(!landed(&marks[0]));
    }

    #[test]
    fn the_worst_mark_on_a_line_is_the_one_it_paints() {
        let mut files = parse(DIFF);
        anchor(
            &mut files,
            &[
                finding("nit", "auto.py:139", "a"),
                finding("blocking", "auto.py:139", "b"),
            ],
        );
        let line = line_139(&files);
        assert_eq!(line.marks.len(), 2);
        assert_eq!(worst(line), "blocking");
        assert_eq!(worst(&Line::default()), "");
    }

    #[test]
    fn marks_only_keeps_the_lines_near_a_finding() {
        let mut files = parse(DIFF);
        anchor(&mut files, &[finding("blocking", "auto.py:139", "x")]);
        let got = narrow(&files, 1);
        assert_eq!(
            got.iter().map(|f| f.path.as_str()).collect::<Vec<_>>(),
            ["gitdashy/auto.py"]
        );
        assert_eq!(
            got[0].hunks[0]
                .lines
                .iter()
                .map(|l| l.n.unwrap())
                .collect::<Vec<_>>(),
            [138, 139, 140]
        );
        assert!(narrow(&parse(DIFF), 3).is_empty());
    }

    #[test]
    fn narrow_holds_up_at_every_context_the_ring_offers() {
        // 0 is the edge that matters: the marked line alone, with nothing either side.
        for &context in CONTEXTS {
            let mut files = parse(DIFF);
            anchor(&mut files, &[finding("blocking", "auto.py:139", "x")]);
            let got = narrow(&files, context);
            assert_eq!(got.len(), 1);
            let lines = &got[0].hunks[0].lines;
            assert!(
                !lines.is_empty(),
                "context={context} produced a hunk with no lines"
            );
            assert!(lines.iter().any(|l| l.n == Some(139)));
            if context == 0 {
                assert_eq!(lines.len(), 1);
            }
        }
    }

    #[test]
    fn fetch_never_fails_and_caches_on_the_head() {
        let c = Mutex::new(Cache::default());
        let calls = Mutex::new(0);
        let ok = || {
            *calls.lock().unwrap() += 1;
            Some("diff --git a/x b/x\n".to_string())
        };
        assert!(fetch_in(&c, KEEP, ("a/b".into(), 7, "sha1".into()), ok).starts_with("diff --git"));
        assert!(!fetch_in(&c, KEEP, ("a/b".into(), 7, "sha1".into()), ok).is_empty());
        assert_eq!(*calls.lock().unwrap(), 1); // cached
        assert!(!fetch_in(&c, KEEP, ("a/b".into(), 7, "sha2".into()), ok).is_empty());
        assert_eq!(*calls.lock().unwrap(), 2); // a push invalidates it

        let c = Mutex::new(Cache::default());
        assert_eq!(fetch_in(&c, KEEP, key(7), || None), "");
    }

    #[test]
    fn a_diff_it_cannot_read_yields_what_it_could() {
        assert!(parse("").is_empty());
        assert!(parse("not a diff at all\n").is_empty());
        let half = "diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+b\n";
        assert_eq!(
            parse(half).iter().map(|f| f.path.as_str()).collect::<Vec<_>>(),
            ["x.py"]
        );
        assert!(parse("diff --git a/x b/x\n").is_empty()); // a header with no hunks is not a file
    }

    #[test]
    fn a_diff_that_cannot_be_produced_is_not_re_run_on_every_look() {
        let c = Mutex::new(Cache::default());
        let calls = Mutex::new(0);
        let boom = || {
            *calls.lock().unwrap() += 1;
            None
        };
        assert_eq!(fetch_in(&c, KEEP, key(7), boom), "");
        assert_eq!(fetch_in(&c, KEEP, key(7), boom), "");
        assert_eq!(*calls.lock().unwrap(), 1); // the failure is cached too
        let g = c.lock().unwrap().gen;
        assert_eq!(retry_in(&c), 1); // f means "go and look again"
        assert_eq!(c.lock().unwrap().gen, g + 1);
        assert_eq!(fetch_in(&c, KEEP, key(7), boom), "");
        assert_eq!(*calls.lock().unwrap(), 2);
    }

    #[test]
    fn retry_forgets_only_the_failures() {
        let c = Mutex::new(Cache::default());
        let calls = Mutex::new(0);
        let empty = || {
            *calls.lock().unwrap() += 1;
            Some(String::new())
        };
        assert_eq!(fetch_in(&c, KEEP, key(7), empty), ""); // succeeded and printed nothing
        let g = c.lock().unwrap().gen;
        assert_eq!(retry_in(&c), 0);
        assert_eq!(c.lock().unwrap().gen, g); // nothing dropped, nothing bumped
        assert_eq!(fetch_in(&c, KEEP, key(7), empty), "");
        assert_eq!(*calls.lock().unwrap(), 1); // still cached: it was not a failure
    }

    #[test]
    fn an_unknown_kind_sorts_last_instead_of_panicking() {
        assert_eq!((rank("blocking"), rank("nit")), (0, 2));
        assert_eq!(rank("wildcard"), ORDER.len());
        assert_eq!(
            (mark("blocking"), mark("note"), mark("nit"), mark("wildcard")),
            ("◆", "◇", "·", "")
        );
        let marks = anchor(
            &mut parse(DIFF),
            &[
                finding("wildcard", "auto.py:139", "a"),
                finding("blocking", "auto.py:139", "b"),
            ],
        );
        assert_eq!(
            marks.iter().map(|m| m.kind.as_str()).collect::<Vec<_>>(),
            ["blocking", "wildcard"]
        );
    }

    #[test]
    fn marks_come_in_file_then_line_order_with_orphans_last() {
        let marks = anchor(
            &mut parse(DIFF),
            &[
                finding("nit", "nowhere.py:1", "orphan"),
                finding("nit", "CHANGELOG.md:2", "second file"),
                finding("nit", "auto.py:140", "first file, later line"),
                finding("nit", "auto.py:139", "first file"),
            ],
        );
        assert_eq!(
            marks.iter().map(|m| m.text.as_str()).collect::<Vec<_>>(),
            ["first file", "first file, later line", "second file", "orphan"]
        );
    }

    #[test]
    fn the_context_ring_has_one_home() {
        let mut seen = CONTEXTS.to_vec();
        seen.sort();
        seen.dedup();
        assert_eq!(seen.len(), CONTEXTS.len());
    }

    #[test]
    fn an_added_line_that_looks_like_a_header_does_not_rewrite_the_path() {
        let text = "diff --git a/docs/memory.md b/docs/memory.md\n--- a/docs/memory.md\n+++ b/docs/memory.md\n@@ -1,1 +1,3 @@\n intro\n+++ b/not-a-file.py\n+really part of the doc\n";
        let files = parse(text);
        assert_eq!(
            files.iter().map(|f| f.path.as_str()).collect::<Vec<_>>(),
            ["docs/memory.md"]
        );
        let body: Vec<&str> = files[0]
            .hunks
            .iter()
            .flat_map(|h| h.lines.iter())
            .map(|l| l.text.as_str())
            .collect();
        assert!(body.contains(&"++ b/not-a-file.py")); // kept as the added line it is
    }

    #[test]
    fn a_finding_that_names_a_column_still_lands_on_its_line() {
        assert_eq!(where_("gitdashy/auto.py:139:5"), ("gitdashy/auto.py".into(), 139));
        assert_eq!(where_("gitdashy/auto.py:139"), ("gitdashy/auto.py".into(), 139));
        assert_eq!(where_("gitdashy/auto.py"), ("gitdashy/auto.py".into(), 0));
        assert_eq!(where_(""), ("".into(), 0));
        let mut files = parse(DIFF);
        let marks = anchor(&mut files, &[finding("blocking", "auto.py:139:5", "x")]);
        assert_eq!(marks[0].file, 0);
        assert_eq!(line_139(&files).marks, ["blocking"]); // on the line, not in the orphan pile
    }

    #[test]
    fn same_file_matches_in_both_directions() {
        assert!(same_file("auto.py", "gitdashy/core/auto.py"));
        assert!(same_file("gitdashy/core/auto.py", "auto.py"));
        assert!(same_file("gitdashy/core/auto.py", "gitdashy/core/auto.py"));
        assert!(!same_file("auto.py", "other.py"));
        assert!(!same_file("core/auto.py", "core/other.py"));
        assert!(!same_file("", "core/other.py"));
    }

    #[test]
    fn the_diff_cache_does_not_grow_without_end() {
        let c = Mutex::new(Cache::default());
        for i in 0..10 {
            fetch_in(&c, 4, key(i), || Some(format!("diff --git a/{i} b/x\n")));
        }
        let got = c.lock().unwrap();
        assert_eq!(got.items.len(), 4);
        assert_eq!(
            got.items.iter().map(|(k, _)| k.1).collect::<Vec<_>>(),
            [6, 7, 8, 9]
        ); // the oldest go first
    }

    #[test]
    fn a_look_counts_as_a_use_so_the_pane_you_are_reading_does_not_age_out() {
        let c = Mutex::new(Cache::default());
        let text = || Some("diff --git a/x b/x\n".to_string());
        for i in 1..=3 {
            fetch_in(&c, 3, key(i), text);
        }
        fetch_in(&c, 3, key(1), text); // a hit on the oldest, which renews it
        fetch_in(&c, 3, key(4), text); // pushes one out
        let mut left: Vec<u64> = c.lock().unwrap().items.iter().map(|(k, _)| k.1).collect();
        left.sort();
        assert_eq!(left, [1, 3, 4]); // 2 went, not 1
    }
}
