//! Wiring this machine: CLAUDE.md imports, the hooks, the corpus, the mirror registry, setup briefs.
//! Port of dashy/core/install.py. The hooks and corpus are embedded in the binary (see `HOOKS`, `CORPUS`).
//!
//! ponytail: reviews read and write memory with no setup at all: that half needs no installing. What needs
//! installing is the half that reaches a regular coding session, which until now lived in one person's
//! private agent corpus, and so was unreachable for everyone else.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, UNIX_EPOCH};

use include_dir::{include_dir, Dir};
use serde::Serialize;
use serde_json::{json, Value};

use crate::config;
use crate::knowledge::{self, tilde};
use crate::{bind, memory, mirror, team};

pub static CORPUS: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/corpus");
pub const SESSION_START_HOOK: &str = include_str!("../hooks/claude-session-start.sh");
pub const STOP_HOOK: &str = include_str!("../hooks/claude-stop.sh");
pub const SETUP_MARK: &str = memory::SETUP_MARK;

pub const BEGIN: &str = "<!-- gitdashy:begin -->";
pub const END: &str = "<!-- gitdashy:end -->";
/// A separate block: one can go without the other.
pub const CBEGIN: &str = "<!-- gitdashy:corpus:begin -->";
pub const CEND: &str = "<!-- gitdashy:corpus:end -->";
/// The line that says the wiring is already there, block or not.
pub const IMPORT: &str = "@prs-memory/general.md";
/// The pre-2026-09-08 block imported one team globally; its presence means "rewrite".
pub const STALE: &str = "@prs-team/";
/// What `full_apply` swaps for the absolute path of the running binary inside both hook scripts.
pub const PLACEHOLDER: &str = "__GITDASHY__";
const MIRROR_COMMENT: &str = "# gitdashy: this repo's review memory (read-only mirror)";

pub const BLOCK: &str = "<!-- gitdashy:begin -->
# Review memory

Cross-repo facts gitdashy's PR reviews have earned: yours. Written only once two independent
observations agreed, so trust them, but they are what the code turned out to be, not rules. The brief
(what the work is for) and a team's facts arrive per repo, through that repo's own mirror: which brief
and which team apply is a property of the repo you are in, not of the machine, and a review of that
repo is told exactly the same ones.

@prs-memory/general.md
<!-- gitdashy:end -->
";

pub fn claude_dir() -> PathBuf {
    std::env::var_os("CLAUDE_CONFIG_DIR").map(PathBuf::from).unwrap_or_else(|| crate::config::home().join(".claude"))
}

/// (link, target) for the one path a session reads memory through.
///
/// ponytail: ONE link now. There used to be a second, `prs-team`, pointing at one team's memory so its
/// general facts loaded into every session on the machine. That was the wrong scope the moment there
/// were two teams and a dangling link once there were, and even with one it told a repo bound to
/// another team, or to none, how that team works. Team knowledge rides the per-repo mirror, through
/// the binding, like everything else that is the team's. See stale_team_link() for the retirement.
pub fn links() -> Vec<(PathBuf, PathBuf)> {
    vec![(claude_dir().join("prs-memory"), config::get().local_memory)]
}

/// The retired `prs-team` symlink, if this machine still has one and it is ours. None otherwise.
///
/// ponytail: ours means it points into the team store, the plural one, or the pre-plural checkout,
/// which is the only place install ever pointed it. Anything else there is someone's own and is left alone.
pub fn stale_team_link() -> Option<PathBuf> {
    let link = claude_dir().join("prs-team");
    let raw = fs::read_link(&link).ok()?;
    let target = abspath(&link.parent().unwrap_or(Path::new("")).join(raw));
    // ponytail: an EMPTY store root means "no store configured", NOT "everywhere". abspath("") is the
    // CURRENT WORKING DIRECTORY, so a blank PRS_TEAMS/PRS_TEAM, which is exactly what `--demo` sets,
    // made every link under cwd count as ours, and retire() deletes what it matches. Launch the demo from
    // $HOME with a hand-made ~/.claude/prs-team -> ~/work/notes and the link is gone. A relative root is
    // refused for the same reason: it is only meaningful against a cwd this has no business trusting.
    let c = config::get();
    let roots = [c.teams.clone(), if c.team.as_os_str().is_empty() { PathBuf::new() } else { c.team.join("memory") }];
    let stores: Vec<PathBuf> =
        roots.iter().filter(|r| !r.as_os_str().is_empty() && r.is_absolute()).map(|r| abspath(r)).collect();
    stores.iter().any(|st| target == *st || target.starts_with(st)).then_some(link)
}

/// Retire the pre-2026-09-08 team link and the imports that went through it. [] when nothing to do.
///
/// ponytail: one callable, because it runs from two places: `gitdashy install`, and every LAUNCH,
/// the way team.migrate() moves a pre-plural checkout. Nothing re-runs install after an update, so a
/// migration that waits for it waits forever on most machines; meanwhile a one-team user had the
/// team's general facts in context twice, and leaking into repos bound to no team.
/// ponytail: it rewrites only text between markers it wrote. A hand-wired import is REPORTED, never
/// touched, and reported here rather than skipped, because the link it went through is gone and the
/// loader drops a dangling @import without a word. A retirement that leaves one behind half happened.
/// ponytail: NEVER RAISES, the same contract team.migrate() states one line above it in the launch
/// path: it runs before the first draw, so an exception here is a dashboard that never appears, every
/// launch, over a migration the user never asked for. A read-only ~/.claude, a CLAUDE.md whose realpath
/// is in a dotfiles checkout, a root-owned file: each of those is a report line, not a traceback. The
/// failure is SAID rather than swallowed, because a migration that silently did not happen is one the
/// user finds out about when their session stops loading the team's facts.
pub fn retire(dry: bool) -> Vec<String> {
    let mut out = Vec::new();
    let did = if dry { "would " } else { "" };
    if let Some(old) = stale_team_link() {
        out.push(format!(
            "{did}retire {}: a team's facts AND its project brief reach a session through its repo's mirror \
             now, so a repo with no mirror gets neither: `gitdashy init --into .agent/team --loader \
             CLAUDE.local.md` wires one",
            tilde(&old)
        ));
        if !dry {
            if let Err(e) = fs::remove_file(&old) {
                *out.last_mut().unwrap() =
                    format!("gitdashy: could not retire {}: {e}; remove it by hand", tilde(&old));
            }
        }
    }
    let md = claude_dir().join("CLAUDE.md");
    let text = read(&md);
    // ponytail: fence-aware on BOTH sides, like strip_blocks. A raw `STALE in text` took this branch for
    // a CLAUDE.md that merely QUOTED the old block in a code sample (docs/install.md shows exactly that),
    // stripped nothing, and appended BLOCK again on every run, never reaching "ok".
    if inside_blocks(&text).contains(STALE) {
        out.push(format!("{did}update the import block in {}: the team imports are per repo now", tilde(&md)));
        if !dry {
            let body = strip_blocks(&text, BEGIN, END);
            if let Err(e) = write_text(&md, &format!("{}\n\n{BLOCK}", body.trim_end_matches('\n'))) {
                *out.last_mut().unwrap() = format!(
                    "gitdashy: could not update the import block in {}: {e}; the team imports are per repo now",
                    tilde(&md)
                );
            }
        }
    }
    if hand_wired_team_import(Some(&text)) {
        out.push(format!(
            "NOTE  {} still imports {STALE}... outside a block we wrote; those lines point at nothing now; \
             remove them by hand",
            tilde(&md)
        ));
    }
    out
}

/// The lines inside every begin..end block we wrote, fence-aware: a quoted block is text, not ours.
fn inside_blocks(text: &str) -> String {
    split_blocks(text, BEGIN, END).0
}

/// True while CLAUDE.md imports @prs-team/ outside a block we wrote and outside a fence.
pub fn hand_wired_team_import(text: Option<&str>) -> bool {
    let owned;
    let text = match text {
        Some(t) => t,
        None => {
            owned = read(&claude_dir().join("CLAUDE.md"));
            &owned
        }
    };
    outside(&strip_blocks(text, BEGIN, END)).iter().any(|(l, out)| *out && l.contains(STALE))
}

/// Whether the installed identity ever tells a session to `gitdashy remember`. None when there is none.
///
/// ponytail: a heuristic, and it says what it looked for. gitdashy cannot write a user's corpus, and
/// the one this tool was built next to lacked the instruction for months while every draft sat at (1):
/// the pipeline's second observer was silent and nothing said so. A check that reports beats a
/// sentence in a README the reader is assumed to have followed.
pub fn corpus_remembers(ident: Option<&Path>) -> Option<bool> {
    let ident = ident.map(Path::to_path_buf).unwrap_or_else(|| claude_dir().join("identity"));
    if !ident.is_dir() {
        return None;
    }
    // ponytail: listdir raises on a directory it cannot read, and this is reached from every draw
    // through session_notes(). is_dir() above answers "is it there", never "can it be opened".
    // Unreadable is not "a corpus without the instruction": it is nothing we can say.
    let names = md_names(&ident)?;
    Some(names.iter().any(|n| read(&ident.join(n)).contains("gitdashy remember")))
}

/// The `*.md` names in `dir`, sorted; None when it cannot be listed.
fn md_names(dir: &Path) -> Option<Vec<String>> {
    let mut names: Vec<String> = fs::read_dir(dir)
        .ok()?
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".md"))
        .collect();
    names.sort();
    Some(names)
}

type Stamp = Option<(u128, u64)>;

/// A stat-level fingerprint of every file session_notes() would read.
///
/// ponytail: (mtime_ns, size) per file, the same key log's cache uses: mtime alone re-reads on a
/// same-nanosecond rewrite, size alone misses an edit that keeps the length. The DIRECTORY's own mtime
/// is not enough: it moves when a file is added or removed, not when one is edited in place.
#[derive(PartialEq)]
struct NotesKey {
    d: PathBuf,
    ident: Vec<(String, Stamp)>,
    md: Stamp,
    agents: Vec<(PathBuf, Stamp)>,
    ok: Stamp,
}

/// (mtime_ns, size), or why not. ponytail: the miss is part of the KEY: "gone" and "back again"
/// have to differ, or a file deleted and restored between draws reads as no change at all.
fn stamp(path: &Path) -> Stamp {
    let st = fs::metadata(path).ok()?;
    let ns = st.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos();
    Some((ns, st.len()))
}

fn notes_key() -> NotesKey {
    let d = claude_dir();
    let ident = d.join("identity");
    let names = md_names(&ident).unwrap_or_default();
    // ponytail: the team's agents.md files are in the key too, because a note below depends on them and
    // on the answers file beside them. Without this the row would be right once and then keep saying it
    // after the file was accepted: a standing note that has stopped being true is worse than none.
    let mut dirs = team::dirs();
    dirs.sort();
    NotesKey {
        ident: names.iter().map(|n| (n.clone(), stamp(&ident.join(n)))).collect(),
        md: stamp(&d.join("CLAUDE.md")),
        agents: dirs.into_iter().map(|t| (t.clone(), stamp(&t.join("memory").join("agents.md")))).collect(),
        ok: stamp(&knowledge::effective().join(".agents-ok")),
        d,
    }
}

static NOTES: Mutex<Option<(NotesKey, Vec<String>)>> = Mutex::new(None);

/// Short standing notes for the Knowledge row: what a session here is NOT being told, and why.
///
/// ponytail: CACHED on a stat-level key, because header_groups() calls this and draw() calls that on
/// every tick: 50ms while anything spins. Uncached it read every identity/*.md in full, read the whole
/// CLAUDE.md, and ran strip_blocks() over it, twice a second for the life of the process. The values
/// beside it on that row cost one stat each; this now costs the same order, and still notices an edit.
pub fn session_notes() -> Vec<String> {
    let key = notes_key();
    let mut cache = NOTES.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((k, notes)) = cache.as_ref() {
        if *k == key {
            return notes.clone();
        }
    }
    let mut out = Vec::new();
    if corpus_remembers(None) == Some(false) {
        out.push("corpus never says `gitdashy remember`".to_string());
    }
    if hand_wired_team_import(None) {
        out.push("CLAUDE.md imports @prs-team by hand".to_string());
    }
    // ponytail: the launch prompt asks ONCE, at startup. After a `n`, or on a machine that only ever
    // runs the session hook, a team's instruction to your sessions is withheld for ever with nothing
    // saying so, and this row exists for exactly that: what a session here is NOT being told.
    // ponytail: a refusal is a note too, and unacked_agents forgets a team once it is answered.
    let waiting: Vec<String> = memory::unacked_agents().into_iter().map(|(k, _)| k).collect();
    if !waiting.is_empty() {
        out.push(format!("{}: agents.md not read, restart to be asked", waiting.join(", ")));
    }
    // ponytail: names the command that ACTUALLY re-asks. It said "restart to be asked again", and a
    // restart asked nothing: ask_agents walks unacked_agents, which drops a team whose refusal
    // matches the file it still has. Nothing cleared a `!` entry at all.
    let refused = memory::refused_agents();
    if !refused.is_empty() {
        out.push(format!("{}: agents.md refused, `gitdashy teams --agents-again`", refused.join(", ")));
    }
    *cache = Some((key, out.clone()));
    out
}

/// Track a fenced code block across one line. Returns the new state: None, or (char, width).
///
/// ponytail: three loops each toggled on "```" alone. "~~~" was invisible to all of them, and any
/// divergence between reader and writer corrupts text, which is how the escaping bug got in. One
/// function, so a fence means exactly one thing everywhere it matters.
/// ponytail: a closing fence is the same character, at least as wide, and carries nothing after it.
/// An info string ("```python") can only open, so a fenced block quoting one is not closed by it.
fn fence(line: &str, open_at: Option<(char, usize)>) -> Option<(char, usize)> {
    let bare = line.trim_start();
    for ch in ['`', '~'] {
        if bare.starts_with(&ch.to_string().repeat(3)) {
            let width = bare.chars().take_while(|c| *c == ch).count();
            match open_at {
                None => return Some((ch, width)),
                Some((oc, ow)) if oc == ch && width >= ow && bare[width..].trim().is_empty() => return None,
                _ => {}
            }
        }
    }
    open_at
}

/// (line, is_outside_a_fence) for every line, so markers inside a code block stay text.
fn outside(text: &str) -> Vec<(&str, bool)> {
    let mut at = None;
    text.lines()
        .map(|line| {
            let was = at;
            at = fence(line, at);
            (line, was.is_none() && at.is_none())
        })
        .collect()
}

/// (inside, outside): the lines within every begin..end block we wrote, and the text without them.
///
/// ponytail: ONE walk answers both, and that is not only a saving. They used to be two functions with
/// two different ideas of an UNCLOSED block: inside_blocks treated everything after a lone `begin` as
/// ours, while this loop treats it as not ours and leaves it alone. retire() asked the first and acted
/// with the second, so a CLAUDE.md holding an unclosed marker over an `@prs-team/` line reported
/// "ours", stripped nothing, and appended BLOCK, and on the NEXT launch the strip ran from the user's
/// own unclosed marker to the appended END and deleted everything in between. Answering both questions
/// from one walk is what makes that disagreement unrepresentable.
///
/// ponytail: a config copied between machines, or two installs racing, leaves the block twice,
/// and removing one of two is worse than removing none, because it reads as a clean uninstall.
/// ponytail: markers inside a fence are text. CLAUDE.md is the user's own file, and quoting our
/// install block in a code sample is a normal thing to do: eating it on uninstall is data loss
/// in the same file this whole path exists to protect.
fn split_blocks(text: &str, begin: &str, end: &str) -> (String, String) {
    let mut held: Vec<String> = Vec::new();
    let mut text = text.to_string();
    loop {
        let lines = outside(&text);
        let Some(at) = lines.iter().position(|(l, out)| *out && l.contains(begin)) else { break };
        let Some(close) = lines.iter().enumerate().position(|(i, (l, out))| i >= at && *out && l.contains(end))
        else {
            break; // ponytail: an unclosed marker is NOT a block of ours: nothing held, nothing stripped
        };
        held.extend(lines[at + 1..close].iter().map(|(l, _)| l.to_string()));
        let head = lines[..at].iter().map(|(l, _)| *l).collect::<Vec<_>>().join("\n");
        let tail = lines[close + 1..].iter().map(|(l, _)| *l).collect::<Vec<_>>().join("\n");
        // ponytail: lines() drops the terminator, so rejoining a file that ended in a newline gave
        // it back without one. It is a user-owned file; leave it shaped the way they had it.
        let body = format!(
            "{}{}{}",
            head.trim_end_matches('\n'),
            if head.trim().is_empty() { "" } else { "\n" },
            tail.trim_start_matches('\n').trim_end_matches('\n')
        );
        text = if text.ends_with('\n') && !body.ends_with('\n') { body + "\n" } else { body };
    }
    (held.join("\n"), text)
}

/// Every begin..end block gone, not just the first. See split_blocks for why every one, and why a
/// fenced one is left.
fn strip_blocks(text: &str, begin: &str, end: &str) -> String {
    split_blocks(text, begin, end).1
}

/// A file's text, or "" for any reason it cannot be read.
///
/// ponytail: any error, not only "not found". "Not there" and "there but unreadable" are the same
/// answer to every one of this module's twenty callers, none of them can do anything with the
/// difference, and the narrow catch made an existing-but-unreadable file raise instead. That reached
/// two places that must never raise: retire(), which runs before the first draw, and session_notes(),
/// which row() calls on EVERY draw. One `sudo claude` leaves a root-owned ~/.claude/CLAUDE.md and the
/// dashboard is gone, every tick, until someone chowns it back.
/// ponytail: reading is the only thing widened. A WRITE that fails still reports (see retire(), where
/// each write says what it could not do). Silence is right for a read and wrong for a write.
fn read(p: &Path) -> String {
    fs::read_to_string(p).unwrap_or_default()
}

/// Put a file back the way it was: gone, if it was not there.
fn unwrite(path: &Path, before: &str) -> String {
    if before.is_empty() {
        let _ = fs::remove_file(path);
        String::new()
    } else {
        io_err(fs::write(path, before))
    }
}

/// "" or the error's text: the shape every undo step and every "returns '' or why not" reports in.
fn io_err<T>(r: io::Result<T>) -> String {
    r.err().map(|e| e.to_string()).unwrap_or_default()
}

/// Replace a file atomically, through a symlink and keeping its mode.
///
/// ponytail: writing to a temp file and renaming truncates nothing, so an error partway leaves the old
/// file rather than half the new one, which is what a dump into an opened "w" did to settings.json,
/// leaving it unparseable and every later run refusing on it.
/// ponytail: renaming onto the PATH swaps a symlink out for a plain file. ~/.claude/CLAUDE.md living in
/// a dotfiles checkout is exactly the setup people have, and the old open(path, "w") wrote through the
/// link. Resolve first, so the target is rewritten and the link stays a link.
/// ponytail: and copy the mode across. settings.json is where Claude Code keeps env blocks with API keys
/// in them; a 0600 file coming back 0664 because of the umask is a real leak, quietly.
pub fn write_text(path: &Path, text: &str) -> io::Result<()> {
    let real = realpath(path, 0);
    let mut tmp = real.clone().into_os_string();
    tmp.push(".gitdashy.tmp");
    let tmp = PathBuf::from(tmp);
    // ponytail: created 0600 and widened only to whatever the target already was. Writing at the umask
    // first put a 0600 settings.json (env blocks, API keys) on disk as 0644 for the length of the
    // write, in the same directory. Narrow first is free; the other order has a window.
    // ponytail: O_CREAT ignores the mode when the file already exists, and a .gitdashy.tmp left by a
    // crash would then keep whatever mode it had. Removing it first makes the 0600 unconditional.
    // ponytail: a target that does not exist yet is CREATED 0600 rather than at the umask. These are
    // one user's own config files and one of them holds API keys, so narrow is the right default.
    let _ = fs::remove_file(&tmp);
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    {
        use io::Write;
        let mut f = opts.open(&tmp)?;
        f.write_all(text.as_bytes())?;
    }
    if let Ok(meta) = fs::metadata(&real) {
        fs::set_permissions(&tmp, meta.permissions())?;
    }
    fs::rename(&tmp, &real)
}

/// ponytail: one writer, so the symlink and mode rules cannot hold in one place and not the other.
fn write_json(path: &Path, data: &Value) -> io::Result<()> {
    write_text(path, &serde_json::to_string_pretty(data).unwrap_or_default())
}

/// `p` absolute and lexically normalised (`.` and `..` folded), like Python's abspath: no symlink is followed.
fn abspath(p: &Path) -> PathBuf {
    let p = if p.is_absolute() {
        p.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("/")).join(p)
    };
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// `~` and `~/...` expanded, nothing else touched.
fn expanduser(p: &Path) -> PathBuf {
    match p.strip_prefix("~") {
        Ok(rest) => config::home().join(rest),
        Err(_) => p.to_path_buf(),
    }
}

/// Symlinks resolved as far as they exist, like Python's realpath: a path that is not there yet is
/// still an answer, not an error.
fn realpath(p: &Path, depth: usize) -> PathBuf {
    let p = abspath(p);
    if let Ok(c) = fs::canonicalize(&p) {
        return c;
    }
    if depth < 40 {
        if let Ok(t) = fs::read_link(&p) {
            return realpath(&p.parent().unwrap_or(Path::new("/")).join(t), depth + 1);
        }
        if let (Some(par), Some(name)) = (p.parent(), p.file_name()) {
            if !par.as_os_str().is_empty() {
                return realpath(par, depth + 1).join(name);
            }
        }
    }
    p
}

fn is_link(p: &Path) -> bool {
    fs::symlink_metadata(p).map(|m| m.file_type().is_symlink()).unwrap_or(false)
}

/// Python's lexists: there, even as a dangling link.
fn lexists(p: &Path) -> bool {
    fs::symlink_metadata(p).is_ok()
}

fn ours(link: &Path, target: &Path) -> bool {
    is_link(link) && realpath(link, 0) == realpath(target, 0)
}

fn symlink(target: &Path, link: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link)
    }
    #[cfg(not(unix))]
    {
        std::os::windows::fs::symlink_dir(target, link)
    }
}

/// Is `p` a file this user may execute.
fn executable(p: &Path) -> bool {
    let Ok(meta) = fs::metadata(p) else { return false };
    if !meta.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        meta.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

/// shlex.quote: the argument as one shell word.
fn quote(s: &str) -> String {
    if s.is_empty() {
        return "''".into();
    }
    if s.chars().all(|c| c.is_alphanumeric() || "@%+=:,./-_".contains(c)) {
        return s.to_string();
    }
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

/// What install would change on this machine, in the user's own paths. Returns report lines.
pub fn explain() -> Vec<String> {
    let d = claude_dir();
    let mut out = Vec::new();
    out.push("gitdashy install wires this machine so every agent session reads what your reviews learned.".into());
    out.push("".into());
    out.push("Reviews do not need this. They read memory through their own prompt and always have; this is".into());
    out.push("only so a coding session sees the same facts. It is additive, and reversible.".into());
    out.push("".into());
    out.push("It will:".into());
    for (link, target) in links() {
        let state = if ours(&link, &target) {
            "already correct"
        } else if lexists(&link) {
            "EXISTS, will be left alone"
        } else {
            "new"
        };
        out.push(format!("  · symlink {} -> {}   [{state}]", tilde(&link), tilde(&target)));
    }
    let md = d.join("CLAUDE.md");
    let n = BLOCK.matches("\n@").count(); // ponytail: counted, not written down: "four" outlived the block it described
    out.push(format!(
        "  · append {n} import{} to {}, inside a marked block{}",
        if n != 1 { "s" } else { "" },
        tilde(&md),
        if read(&md).contains(IMPORT) { "   [already there]" } else { "   [new]" }
    ));
    // ponytail: the consent screen must name the migration it is asking consent for. It said nothing
    // about removing a symlink and rewriting a block in the user's own config; this install's whole
    // promise is that it shows what it will touch first. The same call apply() makes, in dry mode.
    for line in retire(true) {
        out.push(format!("  · {}", line.strip_prefix("would ").unwrap_or(&line)));
    }
    out.push("".into());
    out.push("It will NOT: install hooks, touch settings.json, change any repo, or send anything anywhere.".into());
    out.push("Cross-repo facts load live through the symlink: the session reads the same file a review".into());
    out.push("writes, so nothing is copied and nothing goes stale. Per-repo facts are separate: run".into());
    out.push("`gitdashy init` inside a repo to add those, or leave them out.".into());
    out.push("".into());
    out.push("Reverse it any time with `gitdashy install --uninstall`, which removes only what it wrote.".into());
    out
}

/// Wire this machine so every session reads review memory. Returns report lines.
pub fn apply(dry: bool) -> Vec<String> {
    let d = claude_dir();
    if !d.is_dir() {
        return vec![format!("FAIL  no agent config directory at {}: is claude installed?", tilde(&d))];
    }
    let mut out = Vec::new();
    let did = if dry { "would " } else { "" };
    let local = config::get().local_memory;
    for (link, target) in links() {
        if ours(&link, &target) {
            out.push(format!("ok    {} already points at {}", tilde(&link), tilde(&target)));
        } else if lexists(&link) {
            // ponytail: never replace something we did not make
            out.push(format!("SKIP  {} exists and is not ours: left alone", tilde(&link)));
        } else {
            let pending = if target.is_dir() { "" } else { "   (waits until there is one)" };
            out.push(format!("{did}link  {} -> {}{pending}", tilde(&link), tilde(&target)));
            if !dry {
                // ponytail: make your own memory dir rather than leaving a link to nothing. The team's is
                // left dangling on purpose: it exists once you join, and a missing import is skipped.
                let r = if target == local { fs::create_dir_all(&target) } else { Ok(()) };
                if let Err(e) = r.and_then(|_| symlink(&target, &link)) {
                    out.push(format!("FAIL  could not link {}: {e}", tilde(&link)));
                }
            }
        }
    }
    out.extend(retire(dry));
    let md = d.join("CLAUDE.md");
    let text = read(&md);
    if text.contains(IMPORT) {
        out.push(format!("ok    {} already imports the review memory", tilde(&md)));
    } else {
        out.push(format!("{did}add   the import block to {}", tilde(&md)));
        if !dry {
            if let Err(e) = append(&md, &format!("{}\n{BLOCK}", missing_newline(&text))) {
                out.push(format!("FAIL  could not write {}: {e}", tilde(&md)));
            }
        }
    }
    out
}

/// "\n" when `text` is there and does not end in one: what an append needs before its own blank line.
fn missing_newline(text: &str) -> &'static str {
    if !text.is_empty() && !text.ends_with('\n') {
        "\n"
    } else {
        ""
    }
}

fn append(path: &Path, text: &str) -> io::Result<()> {
    use io::Write;
    fs::OpenOptions::new().append(true).create(true).open(path)?.write_all(text.as_bytes())
}

/// Undo exactly what apply() wrote, and say so when something is not ours to undo.
pub fn remove(dry: bool) -> Vec<String> {
    let mut out = Vec::new();
    let did = if dry { "would " } else { "" };
    for (link, target) in links() {
        if ours(&link, &target) {
            out.push(format!("{did}remove  {}", tilde(&link)));
            if !dry {
                if let Err(e) = fs::remove_file(&link) {
                    out.push(format!("FAIL    could not remove {}: {e}", tilde(&link)));
                }
            }
        } else if lexists(&link) {
            out.push(format!("SKIP    {} is not the link we made: left alone", tilde(&link)));
        } else {
            out.push(format!("ok      {} is not there", tilde(&link)));
        }
    }
    if let Some(old) = stale_team_link() {
        out.push(format!("{did}remove  {}   (the retired team link)", tilde(&old)));
        if !dry {
            if let Err(e) = fs::remove_file(&old) {
                out.push(format!("FAIL    could not remove {}: {e}", tilde(&old)));
            }
        }
    }
    let md = claude_dir().join("CLAUDE.md");
    let text = read(&md);
    if text.contains(BEGIN) && text.contains(END) {
        out.push(format!("{did}remove  the import block from {}", tilde(&md)));
        if !dry {
            if let Err(e) = write_text(&md, &strip_blocks(&text, BEGIN, END)) {
                out.push(format!("FAIL    could not write {}: {e}", tilde(&md)));
            }
        }
    } else if text.contains(IMPORT) {
        out.push(format!("SKIP    {} imports the memory but not in a block we wrote: remove it by hand", tilde(&md)));
    } else {
        out.push(format!("ok      {} does not import it", tilde(&md)));
    }
    out
}

#[derive(Serialize)]
struct Entry<'a> {
    into: &'a str,
    repo: &'a str,
    root: &'a str,
    loader: &'a str,
}

/// [(into, repo, root, loader)] for every mirror this machine refreshes, newest entry per path.
///
/// ponytail: deduplicated on READ, so register can append without a lock. The hook runs at every
/// session start, and two starting together would otherwise interleave a read-modify-write and drop one.
/// ponytail: root and loader may be "" (entries written before they were recorded).
/// ponytail: append-only in both directions, so the file only grows. Self-limiting per path (a
/// tombstoned entry leaves this list, so refresh writes one tombstone and not one per tick) but
/// repeated init/--forget cycles never shrink it. Compact on read-then-rewrite if it ever matters.
pub fn registered() -> Vec<(PathBuf, String, PathBuf, PathBuf)> {
    let mut seen: Vec<(PathBuf, String, PathBuf, PathBuf)> = Vec::new();
    for line in read(&config::get().registry).lines() {
        if line.trim().is_empty() {
            continue;
        }
        let e: Value = match serde_json::from_str(line) {
            Ok(Value::Object(m)) => Value::Object(m),
            _ => {
                // ponytail: the format before this was "<into>\t<repo>". Dropping such a line silently read
                // the whole registry back as empty, so every mirror on that machine stopped refreshing with
                // nothing said. Only reachable on a machine that ran this branch before the change, which
                // is exactly the machines reviewing it.
                let mut part = line.split('\t');
                let into = part.next().unwrap_or("");
                if !into.starts_with(std::path::MAIN_SEPARATOR) {
                    continue; // a line we cannot read is not a reason to lose the rest
                }
                json!({"into": into, "repo": part.next().unwrap_or("")})
            }
        };
        let field = |k: &str| e.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let into = if field("into").is_empty() { field("forget") } else { field("into") };
        if into.is_empty() {
            continue;
        }
        let at = seen.iter().position(|s| s.0 == Path::new(&into));
        if e.get("forget").is_some() {
            if let Some(i) = at {
                seen.remove(i);
            }
        } else {
            let entry = (PathBuf::from(&into), field("repo"), PathBuf::from(field("root")), PathBuf::from(field("loader")));
            match at {
                Some(i) => seen[i] = entry,
                None => seen.push(entry),
            }
        }
    }
    seen
}

/// Remember to refresh this mirror. Returns true when it was not already known.
pub fn register(into: &Path, repo: &str, root: &Path, loader: &Path) -> bool {
    let into = abspath(&expanduser(into));
    if registered().iter().any(|e| e.0 == into) {
        return false;
    }
    // ponytail: JSON, because the fields are PATHS. A tab in a directory name is legal and split a
    // delimited line into the wrong fields silently; a quote or a newline would have been worse.
    let line = serde_json::to_string(&Entry {
        into: &into.to_string_lossy(),
        repo,
        root: &root.to_string_lossy(),
        loader: &loader.to_string_lossy(),
    })
    .unwrap_or_default();
    // one append, atomic enough; duplicates die on read
    append(&config::get().registry, &format!("{line}\n")).is_ok()
}

/// Stop refreshing this mirror. Returns true when it was known.
pub fn unregister(into: &Path) -> bool {
    let into = abspath(&expanduser(into));
    if !registered().iter().any(|e| e.0 == into) {
        return false;
    }
    // ponytail: append a tombstone rather than rewrite. register() appends without a lock because the
    // hook runs at every session start; a truncating rewrite here would drop an entry appended during it.
    let line = json!({"forget": into.to_string_lossy()}).to_string();
    append(&config::get().registry, &format!("{line}\n")).is_ok()
}

/// `git -C dir args`, stdout trimmed, None on any failure. Killed after 60 seconds.
fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let mut child = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let start = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(s)) => break s,
            Ok(None) if start.elapsed() < Duration::from_secs(60) => std::thread::sleep(Duration::from_millis(20)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    if !status.success() {
        return None;
    }
    let mut s = String::new();
    use io::Read;
    child.stdout.take()?.read_to_string(&mut s).ok()?;
    Some(s.trim().to_string())
}

/// The git repo `path` sits in, or None: asked from the nearest directory that exists.
fn toplevel(path: &Path) -> Option<PathBuf> {
    let mut base = path.to_path_buf();
    while !base.is_dir() {
        match base.parent() {
            Some(p) if p != base => base = p.to_path_buf(),
            _ => break,
        }
    }
    git(&base, &["rev-parse", "--show-toplevel"]).filter(|s| !s.is_empty()).map(PathBuf::from)
}

/// The directory holding info/exclude. ponytail: NOT <root>/.git: in a linked worktree or a
/// submodule that is a FILE, and building a path through it fails. Ask git.
fn gitdir(root: &Path) -> Option<PathBuf> {
    let d = git(root, &["rev-parse", "--git-common-dir"]).filter(|s| !s.is_empty())?;
    let d = PathBuf::from(d);
    Some(if d.is_absolute() { d } else { root.join(d) })
}

/// Python's relpath: `path` relative to `start`, lexically, with `..` where it has to climb.
fn relpath(path: &Path, start: &Path) -> String {
    let (p, s) = (abspath(path), abspath(start));
    let pc: Vec<_> = p.components().collect();
    let sc: Vec<_> = s.components().collect();
    let common = pc.iter().zip(sc.iter()).take_while(|(a, b)| a == b).count();
    let mut parts: Vec<String> = vec!["..".to_string(); sc.len() - common];
    parts.extend(pc[common..].iter().map(|c| c.as_os_str().to_string_lossy().into_owned()));
    if parts.is_empty() {
        ".".into()
    } else {
        parts.join("/")
    }
}

/// Keep the mirror out of git via info/exclude.
///
/// ponytail: never .gitignore. That file is tracked and belongs to everyone; a mirror is one machine's
/// local copy, and committing an ignore rule for it puts your setup in someone else's history.
fn exclude(root: &Path, rel: &str) -> String {
    let Some(gd) = gitdir(root) else {
        return "FAIL  cannot find the git directory: refusing to write a mirror git could commit".into();
    };
    let p = gd.join("info").join("exclude");
    let rule = format!("{}/", rel.trim_end_matches('/'));
    if read(&p).lines().any(|l| l == rule) {
        return format!("ok    {rule} is already excluded");
    }
    let r = fs::create_dir_all(gd.join("info"))
        .and_then(|_| append(&p, &format!("\n# gitdashy mirror (local, never commit)\n{rule}\n")));
    match r {
        Ok(()) => format!("added {rule} to .git/info/exclude"),
        Err(e) => format!("FAIL  could not write {}: {e}", tilde(&p)),
    }
}

fn import(loader: &Path, line: &str) -> String {
    let text = read(loader);
    if text.lines().any(|l| l == line) {
        return format!("ok    {} already imports {line}", tilde(loader));
    }
    let r = match loader.parent() {
        Some(d) if !d.as_os_str().is_empty() => fs::create_dir_all(d),
        _ => Ok(()),
    }
    .and_then(|_| append(loader, &format!("{}\n{MIRROR_COMMENT}\n{line}\n", missing_newline(&text))));
    match r {
        Ok(()) => format!("added {line} to {}", tilde(loader)),
        Err(e) => format!("FAIL  could not write {}: {e}", tilde(loader)),
    }
}

/// The `@...` line a loader imports `into` through.
fn import_line(into: &Path, loader: &Path) -> String {
    format!("@{}/repo.md", relpath(into, loader.parent().unwrap_or(Path::new(""))))
}

/// Take out the import line we added, and the comment above it. The mirror files stay.
///
/// ponytail: what makes a mirror live is the import, not the file. Removing that leaves readable facts
/// behind rather than reaching outside the agent config to delete data, while making sure no session
/// goes on reading memory that nothing refreshes any more.
fn unimport(loader: &Path, into: &Path) -> String {
    let line = import_line(into, loader);
    let text = read(loader);
    let kept: Vec<&str> = text.lines().filter(|l| l.trim() != line && l.trim() != MIRROR_COMMENT).collect();
    if kept.len() == text.lines().count() {
        return String::new();
    }
    let body = if kept.iter().any(|k| !k.trim().is_empty()) {
        format!("{}\n", kept.join("\n").trim_end_matches('\n'))
    } else {
        String::new()
    };
    io_err(write_text(loader, &body))
}

/// Wire one repo: ignore the mirror, import it, keep it fresh, write it now. Returns report lines.
pub fn wire_repo(into: &Path, loader: &Path, repo: &str) -> Vec<String> {
    let mut out = Vec::new();
    let into = abspath(&expanduser(into));
    let loader = abspath(&expanduser(loader));
    let root = toplevel(&into);
    out.push(match &root {
        Some(r) => exclude(r, &relpath(&into, r)),
        None => format!("note  {} is not inside a git repo: nothing to exclude", tilde(&into)),
    });
    out.push(import(&loader, &import_line(&into, &loader)));
    out.push(if register(&into, repo, root.as_deref().unwrap_or(Path::new("")), &loader) {
        format!("added {} to the refresh list, as {repo}", tilde(&into))
    } else {
        format!("ok    {} is already refreshed every tick", tilde(&into))
    });
    out.push(format!("      {}", mirror::sync(&into, repo, false, false)));
    out
}

/// Where a corpus's files come from: a directory on disk, or the one built into this binary.
///
/// PORT-NOTE: Python's `full_apply(corpus)` took the shipped corpus's directory. The Rust binary ships
/// it embedded (`CORPUS`), so an EMPTY `corpus` path means "the embedded one" everywhere a corpus path
/// is taken; a non-empty path is read from disk as before.
enum Src {
    Dir(PathBuf),
    Embedded,
}

impl Src {
    fn of(corpus: &Path) -> Src {
        if corpus.as_os_str().is_empty() {
            Src::Embedded
        } else {
            Src::Dir(corpus.to_path_buf())
        }
    }
    fn has(&self, rel: &str) -> bool {
        match self {
            Src::Dir(d) => d.join(rel).exists(),
            Src::Embedded => CORPUS.get_entry(rel).is_some(),
        }
    }
    fn has_identity(&self) -> bool {
        match self {
            Src::Dir(d) => d.join("identity").is_dir(),
            Src::Embedded => CORPUS.get_dir("identity").is_some(),
        }
    }
    /// The identity markdown a corpus offers, sorted, or [] when it has none.
    fn names(&self) -> Vec<String> {
        let mut names: Vec<String> = match self {
            Src::Dir(d) => md_names(&d.join("identity")).unwrap_or_default(),
            Src::Embedded => CORPUS
                .get_dir("identity")
                .map(|d| {
                    d.files().filter_map(|f| f.path().file_name()).map(|n| n.to_string_lossy().into_owned()).collect()
                })
                .unwrap_or_default(),
        };
        names.retain(|n| n.ends_with(".md") && !n.ends_with(".template"));
        names.sort();
        names
    }
    fn text(&self, rel: &str) -> String {
        match self {
            Src::Dir(d) => read(&d.join(rel)),
            Src::Embedded => CORPUS.get_file(rel).and_then(|f| f.contents_utf8()).unwrap_or_default().to_string(),
        }
    }
    /// See corpus_remembers.
    fn remembers(&self) -> Option<bool> {
        match self {
            Src::Dir(d) => corpus_remembers(Some(&d.join("identity"))),
            Src::Embedded => self
                .has_identity()
                .then(|| self.names().iter().any(|n| self.text(&format!("identity/{n}")).contains("gitdashy remember"))),
        }
    }
    /// Put a copy at `dest`. "" or why not.
    fn install(&self, dest: &Path) -> String {
        match self {
            Src::Dir(d) => io_err(copy_dir(d, dest)),
            Src::Embedded => io_err(fs::create_dir_all(dest).and_then(|_| CORPUS.extract(dest))),
        }
    }
}

fn copy_dir(src: &Path, dst: &Path) -> io::Result<()> {
    fs::create_dir_all(dst)?;
    for e in fs::read_dir(src)? {
        let e = e?;
        let to = dst.join(e.file_name());
        if e.file_type()?.is_dir() {
            copy_dir(&e.path(), &to)?;
        } else {
            fs::copy(e.path(), &to)?;
        }
    }
    Ok(())
}

/// The identity markdown a corpus offers, sorted, or [] when it has none.
pub fn corpus_files(corpus: &Path) -> Vec<String> {
    Src::of(corpus).names()
}

fn corpus_block(names: &[String], from: &Path) -> String {
    let body = names.iter().map(|n| format!("@identity/{n}")).collect::<Vec<_>>().join("\n");
    let label = from.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "the shipped corpus".into());
    format!(
        "{CBEGIN}
# Agent corpus

How this machine's agent works: what to establish before changing code, when to stop and ask,
and who it is working with. Installed by `gitdashy install --full` from {label}.

{body}
{CEND}
"
    )
}

/// What a full install changes. Returns report lines.
pub fn full_explain(corpus: &Path, url: &str) -> Vec<String> {
    let d = claude_dir();
    let home = config::get().corpus_home;
    let mut out = Vec::new();
    // ponytail: explain what apply will DO. apply imports from corpus_home when it exists; reading the
    // shipped corpus here named the wrong files and the wrong cost to anyone who had pointed
    // corpus_home at their own corpus: "import 3 files: AGENT.md, AGENTS.md, RULES.md" on a machine
    // about to import six others.
    let src = if home.is_dir() { Src::Dir(home.clone()) } else { Src::of(corpus) };
    // ponytail: with --corpus URL and no corpus_home yet, the remote's identity/ cannot be read before the
    // clone. Naming the SHIPPED corpus's files and token cost here described a corpus that was about to be
    // replaced by a different one: an unknown is disclosed as unknown, never filled in with a stand-in.
    let unread = !url.is_empty() && !home.is_dir();
    let names = src.names();
    let words: usize = names.iter().map(|n| src.text(&format!("identity/{n}")).split_whitespace().count()).sum();
    out.push("gitdashy install --full puts an agent corpus on this machine, so every coding session".into());
    out.push("works to the same discipline, and adds the review-memory wiring `install` does.".into());
    out.push("".into());
    out.push("This is the big one. Read it before agreeing.".into());
    out.push("".into());
    out.push("It will:".into());
    out.push(format!(
        "  · {} to {}{}",
        if url.is_empty() { "copy the corpus gitdashy ships".to_string() } else { format!("clone {url}") },
        tilde(&home),
        if home.is_dir() { "   [EXISTS, will be left alone]" } else { "   [new]" }
    ));
    out.push(format!("  · symlink {} -> that corpus's identity/", tilde(&d.join("identity"))));
    let into = tilde(&d.join("CLAUDE.md"));
    if unread {
        out.push(format!("  · import that corpus's identity/*.md into {into}: which files, and how many, cannot be"));
        out.push("    known until it is cloned".into());
    } else {
        out.push(format!("  · import {} files into {into}: {}", names.len(), names.join(", ")));
    }
    out.push("  · seed USER.md from the template, for you to fill in, if it is not there already".into());
    out.push(format!("  · write a SessionStart and a Stop hook script into {}", tilde(&d.join("hooks"))));
    out.push(format!("  · register a SessionStart and a Stop hook in {}", tilde(&d.join("settings.json"))));
    out.push("  · everything plain `gitdashy install` does, for review memory".into());
    out.push("".into());
    out.push("What that costs, every session on this machine, permanently:".into());
    if unread {
        out.push("  · however many tokens that corpus's identity/ holds: unknown until it is cloned".into());
    } else {
        out.push(format!(
            "  · about {} tokens of instructions, before you have typed anything",
            thousands((words as f64 * 1.35) as u64)
        ));
    }
    out.push("  · one hook at the start of every session, and one at the end, in every repo".into());
    out.push("".into());
    out.push("The SessionStart hook seeds .agent/ notes in a repo, excludes them from git (via".into());
    out.push(".git/info/exclude, never the tracked .gitignore), and mirrors that repo's review memory.".into());
    out.push("It writes nothing that git can see, and exits quietly if it is not in a repo.".into());
    out.push("".into());
    // ponytail: the Stop hook can BLOCK a stop, which is a thing done TO the session rather than for it,
    // so the consent screen says so in those words. #31's blocking finding was this same rule one door
    // along: a hook that gained a new kind of power and a consent screen that still described the old one.
    out.push("The Stop hook reads the session transcript when a session ends and, if you interrupted".into());
    out.push("or refused tool calls enough times, asks the agent once to write down what it learned.".into());
    out.push("It can hold a session open for that one question: never twice, and never on a routine".into());
    out.push("session. It reads only the transcript, and sends nothing anywhere.".into());
    out.push("".into());
    // ponytail: until this corpus shipped a bin/, the corpus was DATA: markdown imported into context,
    // templates copied. The hook now RUNS a script out of it, so a --corpus URL is no longer only text you
    // read: it is code that executes at every session start. That is a different thing to agree to, and
    // consent that does not name it is not consent to it.
    out.push("It also RUNS one script from that corpus if it ships an executable bin/budget-check.sh:".into());
    out.push("shell, at every session start, in every repo. A corpus is code you run, not only text you".into());
    out.push("read: `--corpus URL` grants that to whoever can push to it.".into());
    out.push("".into());
    out.push("`gitdashy install --full --uninstall` reverses all of it. The corpus is left on disk,".into());
    out.push("because by then you may have edited it.".into());
    out
}

/// 12345 -> "12,345", the way Python's `{:,}` printed the token cost.
fn thousands(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// One registered hook. ponytail: a TABLE, so a third hook is a row rather than a fourth copy of the
/// register block and a fourth branch in uninstall. The Stop hook takes no argument: everything it
/// judges comes in on stdin.
struct Hook {
    event: &'static str,
    /// The file name under `<claude_dir>/hooks/`.
    name: &'static str,
    text: &'static str,
    /// What the status line says while it runs.
    saying: &'static str,
    takes_home: bool,
}

// ponytail: gitdashy's own, not the corpus's. Pointing at <corpus>/bin/ meant any corpus that did not
// happen to ship this exact file registered a hook to a missing command, in every session, everywhere.
const HOOK_TABLE: [Hook; 2] = [
    Hook {
        event: "SessionStart",
        name: "claude-session-start.sh",
        text: SESSION_START_HOOK,
        saying: "Preparing repo notes",
        takes_home: true,
    },
    Hook { event: "Stop", name: "claude-stop.sh", text: STOP_HOOK, saying: "Checking what this session learned", takes_home: false },
];

/// Where a hook script is installed. ponytail: the FULL path is what a registered command is matched
/// on: enough path to be ours. A bare filename would match, and uninstall would delete, somebody
/// else's hook that happened to be called the same thing.
pub fn hook_path(name: &str) -> PathBuf {
    claude_dir().join("hooks").join(name)
}

/// Write one hook script with the running binary's path in place of the placeholder, executable.
fn write_hook(script: &Path, text: &str) -> io::Result<()> {
    let exe = std::env::current_exe()?;
    if let Some(d) = script.parent() {
        fs::create_dir_all(d)?;
    }
    write_text(script, &text.replace(PLACEHOLDER, &exe.to_string_lossy()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(script, fs::Permissions::from_mode(0o755))?;
    }
    Ok(())
}

/// How many of `event`'s hooks there are in total, across every group.
pub fn count(settings: &Value, event: &str) -> usize {
    groups(settings, event).iter().map(|g| g.get("hooks").and_then(Value::as_array).map_or(0, Vec::len)).sum()
}

fn groups<'a>(settings: &'a Value, event: &str) -> &'a [Value] {
    settings.get("hooks").and_then(|h| h.get(event)).and_then(Value::as_array).map_or(&[], Vec::as_slice)
}

/// `event`'s groups with our hook taken out. Empty groups are dropped.
///
/// ponytail: callers compare HOOK counts, never group counts. Ours can end up sharing a group with
/// somebody else's: then the group survives, the count of groups is unchanged, and a group-count
/// check concludes we were never installed and appends a second copy.
pub fn hooks(settings: &Value, script: &str, event: &str) -> Vec<Value> {
    let mut out = Vec::new();
    for group in groups(settings, event) {
        let kept: Vec<Value> = group
            .get("hooks")
            .and_then(Value::as_array)
            .map(|hs| {
                hs.iter()
                    .filter(|h| !h.get("command").and_then(Value::as_str).unwrap_or("").contains(script))
                    .cloned()
                    .collect()
            })
            .unwrap_or_default();
        if !kept.is_empty() {
            let mut g = group.clone();
            g["hooks"] = Value::Array(kept);
            out.push(g);
        }
    }
    out
}

fn count_of(kept: &[Value], event: &str) -> usize {
    count(&json!({"hooks": {event: kept}}), event)
}

type Undo = Vec<(String, Box<dyn FnOnce() -> String>)>;

/// Install a corpus and its hooks, then the memory wiring. Returns report lines.
///
/// ponytail: every failure below unwinds what this run had already done. Returning halfway used to
/// leave the clone, the symlink or the imports in place, and the worst of those poisoned the command
/// for good, because the next run short-circuits on corpus_home existing and fails identically forever,
/// naming no directory to delete. A half-install that reports FAIL is not a safe state to leave.
pub fn full_apply(corpus: &Path, url: &str, dry: bool) -> Vec<String> {
    let mut out = Vec::new();
    let mut undo: Undo = Vec::new();
    // ponytail: only fail() unwound, so anything that RAISED walked out past the undo list: a
    // permission error on the symlink left the fresh clone at corpus_home, which is the exact
    // poisoning the unwinding was added to remove, reached by a different door. Every exit from
    // this function now goes through fail(), whether it was chosen or thrown.
    match full_go(corpus, url, dry, &mut out, &mut undo) {
        Ok(lines) => lines,
        Err(msg) => full_fail(out, undo, &msg),
    }
}

/// ponytail: an undo that failed is reported, not swallowed. A half-unwound install is the one
/// state worth naming out loud: it is what the next run trips over, and silence here was how
/// the original poisoning stayed invisible for four review rounds.
fn full_fail(mut out: Vec<String>, undo: Undo, msg: &str) -> Vec<String> {
    let (mut done, mut left) = (Vec::new(), Vec::new());
    for (what, act) in undo.into_iter().rev() {
        let err = act();
        if err.is_empty() {
            done.push(what);
        } else {
            left.push(format!("{what} ({err})"));
        }
    }
    out.push(format!("FAIL  {msg}"));
    if !done.is_empty() {
        out.push(format!("      undone: {}", done.join(", ")));
    }
    if !left.is_empty() {
        out.push(format!("      COULD NOT UNDO: {}: remove by hand", left.join(", ")));
    }
    out
}

fn full_go(corpus: &Path, url: &str, dry: bool, out: &mut Vec<String>, undo: &mut Undo) -> Result<Vec<String>, String> {
    let d = claude_dir();
    let did = if dry { "would " } else { "" };
    let corpus_home = config::get().corpus_home;
    let e = |r: io::Error| r.to_string();
    if !d.is_dir() {
        return Ok(vec![format!("FAIL  no agent config directory at {}: is claude installed?", tilde(&d))]);
    }
    if corpus_home.is_dir() {
        out.push(format!("ok    {} is already there: left as it is", tilde(&corpus_home)));
    } else {
        out.push(format!("{did}install  the corpus into {}", tilde(&corpus_home)));
        if !dry {
            // ponytail: the clone path checked its error; the copy path did not, and then symlinked,
            // imported and hooked a directory that was never there.
            let err = if url.is_empty() { Src::of(corpus).install(&corpus_home) } else { team::clone(url, &corpus_home) };
            if corpus_home.is_dir() {
                // ponytail: the path is bound HERE, into the closure. Reading the config when the undo
                // runs would let anything that changes it between these two points retarget a
                // recursive delete, and the tests change it routinely.
                let p = corpus_home.clone();
                undo.push((tilde(&corpus_home), Box::new(move || knowledge::rmtree_owned(&p))));
            }
            if !err.is_empty() {
                return Err(err);
            }
        }
    }
    // What is read: the installed corpus once it is there, the source before it is (a dry run).
    let src = if !dry || corpus_home.is_dir() { Src::Dir(corpus_home.clone()) } else { Src::of(corpus) };
    let home = match &src {
        Src::Dir(p) => p.clone(),
        Src::Embedded => corpus_home.clone(),
    };
    let link = d.join("identity");
    let ident = home.join("identity");
    if ours(&link, &ident) {
        out.push(format!("ok    {} already points at the corpus", tilde(&link)));
    } else if lexists(&link) {
        out.push(format!("SKIP  {} exists and is not ours: left alone, so nothing is imported", tilde(&link)));
    } else if !src.has_identity() {
        // ponytail: refuse rather than leave a link to nothing. An identity that is not there imports
        // nothing, and a dangling symlink in the agent config is worse than an install that stopped.
        // ponytail: checked on a dry run too: --dry-run is what you reach for to find out why the real
        // one failed, and it used to answer with a clean plan in exactly the state that had broken it.
        return Err(format!("{} has no identity/: nothing to install", tilde(&ident)));
    } else {
        out.push(format!("{did}link  {} -> {}", tilde(&link), tilde(&ident)));
        if !dry {
            symlink(&ident, &link).map_err(e)?;
            let l = link.clone();
            undo.push((tilde(&link), Box::new(move || io_err(fs::remove_file(&l)))));
        }
    }
    let user = ident.join("USER.md");
    if src.has("identity/USER.md") {
        out.push("ok    USER.md is already filled in".into());
    } else if src.has("identity/USER.md.template") {
        out.push(format!("{did}seed  USER.md from the template: fill it in, it is the highest-value file here"));
        if !dry {
            fs::copy(ident.join("USER.md.template"), &user).map_err(e)?;
            // ponytail: an unwound install must not leave a template in a corpus that was already
            // there. Only reachable when corpus_home pre-existed (otherwise the clone's own undo
            // takes it), which is exactly the case where the directory is not ours to litter.
            let u = user.clone();
            undo.push((format!("the seeded {}", tilde(&user)), Box::new(move || io_err(fs::remove_file(&u)))));
        }
    }
    if src.remembers() == Some(false) {
        // ponytail: said at the one moment they could act on it. gitdashy cannot write their corpus;
        // it can say that without this line the reviews have no second observer and every draft
        // they ever file stays at (1). The dashboard's Knowledge row keeps saying it afterwards.
        out.push(
            "NOTE  this corpus never tells a session to `gitdashy remember`: add that to its AGENT.md, \
             or nothing a session learns reaches the reviews"
                .into(),
        );
    }
    let md = d.join("CLAUDE.md");
    let text = read(&md);
    if text.contains(CBEGIN) {
        out.push(format!("ok    {} already imports the corpus", tilde(&md)));
    } else {
        out.push(format!("{did}add   the corpus imports to {}", tilde(&md)));
        if !dry {
            append(&md, &format!("{}\n{}", missing_newline(&text), corpus_block(&src.names(), &home))).map_err(e)?;
            // ponytail: read FIRST, and undo with what was read. A truncate-then-read wrote back an
            // empty file: the user's whole global CLAUDE.md, destroyed by the code added to stop this
            // path leaving things behind.
            // ponytail: a file we created is removed, not left empty. Undo means the state before.
            let (m, before) = (md.clone(), text.clone());
            undo.push((format!("the imports in {}", tilde(&md)), Box::new(move || unwrite(&m, &before))));
        }
    }
    let sp = d.join("settings.json");
    let raw = read(&sp);
    let mut settings: Value = serde_json::from_str(if raw.trim().is_empty() { "{}" } else { &raw })
        .map_err(|_| format!("{} is not valid JSON: fix it first", tilde(&sp)))?;
    if !settings.is_object() {
        return Err(format!("{} is not a JSON object: fix it first", tilde(&sp)));
    }
    let mut wrote = false;
    for h in &HOOK_TABLE {
        let script = hook_path(h.name);
        let m = script.to_string_lossy().into_owned();
        if count_of(&hooks(&settings, &m, h.event), h.event) != count(&settings, h.event) {
            out.push(format!("ok    the {} hook is already registered", h.event));
            continue;
        }
        out.push(format!("{did}hook  register {} -> {}", h.event, tilde(&script)));
        if dry {
            continue;
        }
        let fresh = !lexists(&script);
        write_hook(&script, h.text).map_err(e)?;
        if fresh {
            let s = script.clone();
            undo.push((tilde(&script), Box::new(move || io_err(fs::remove_file(&s)))));
        }
        if !executable(&script) {
            // ponytail: only reachable if the write landed as something that cannot run, but reported
            // per hook, because one broken script must not silently cost you the other.
            *out.last_mut().unwrap() =
                format!("SKIP  {} is missing or not executable: no {} hook", tilde(&script), h.event);
            continue;
        }
        let cmd = quote(&m) + &if h.takes_home { format!(" {}", quote(&home.to_string_lossy())) } else { String::new() };
        let group = json!({"hooks": [{"type": "command", "command": cmd, "timeout": 10, "statusMessage": h.saying}]});
        let hooks_obj = settings.as_object_mut().unwrap().entry("hooks").or_insert_with(|| json!({}));
        if !hooks_obj.is_object() {
            *hooks_obj = json!({});
        }
        let list = hooks_obj.as_object_mut().unwrap().entry(h.event).or_insert_with(|| json!([]));
        if !list.is_array() {
            *list = json!([]);
        }
        list.as_array_mut().unwrap().push(group);
        wrote = true;
    }
    if wrote {
        write_json(&sp, &settings).map_err(e)?;
    }
    let mut lines = std::mem::take(out);
    lines.push(String::new());
    if dry {
        lines.push(format!("{did}do    everything plain `install` does"));
    } else {
        lines.extend(apply(false));
    }
    Ok(lines)
}

/// Reverse a full install. The corpus itself stays: by now you may have edited it.
pub fn full_remove(dry: bool) -> Vec<String> {
    let d = claude_dir();
    let mut out = Vec::new();
    let did = if dry { "would " } else { "" };
    let corpus_home = config::get().corpus_home;
    let (link, ident) = (d.join("identity"), corpus_home.join("identity"));
    if ours(&link, &ident) {
        out.push(format!("{did}remove  {}", tilde(&link)));
        if !dry {
            if let Err(e) = fs::remove_file(&link) {
                out.push(format!("FAIL    could not remove {}: {e}", tilde(&link)));
            }
        }
    } else if lexists(&link) {
        out.push(format!("SKIP    {} is not the link we made: left alone", tilde(&link)));
    }
    let md = d.join("CLAUDE.md");
    let text = read(&md);
    if text.contains(CBEGIN) && text.contains(CEND) {
        out.push(format!("{did}remove  the corpus imports from {}", tilde(&md)));
        if !dry {
            if let Err(e) = write_text(&md, &strip_blocks(&text, CBEGIN, CEND)) {
                out.push(format!("FAIL    could not write {}: {e}", tilde(&md)));
            }
        }
    }
    let sp = d.join("settings.json");
    let raw = read(&sp);
    let mut settings: Option<Value> = serde_json::from_str(if raw.trim().is_empty() { "{}" } else { &raw }).ok();
    if settings.is_none() {
        out.push(format!("SKIP    {} is not valid JSON: remove the hook by hand", tilde(&sp)));
    }
    // ponytail: every hook in the table, not the one this branch happened to add. An uninstall that
    // leaves a Stop hook pointing into a checkout the user then deletes fails at the end of every
    // session, forever, with nothing naming gitdashy as the cause.
    let mut dropped = false;
    for h in &HOOK_TABLE {
        let script = hook_path(h.name);
        if let Some(settings) = settings.as_mut() {
            if !groups(settings, h.event).is_empty() {
                let kept = hooks(settings, &script.to_string_lossy(), h.event);
                if count_of(&kept, h.event) != count(settings, h.event) {
                    out.push(format!("{did}remove  the {} hook from {}", h.event, tilde(&sp)));
                    if !dry {
                        let hooks_obj = settings["hooks"].as_object_mut().unwrap();
                        if kept.is_empty() {
                            hooks_obj.remove(h.event);
                        } else {
                            hooks_obj.insert(h.event.into(), Value::Array(kept));
                        }
                        dropped = true;
                    }
                }
            }
        }
        if lexists(&script) {
            out.push(format!("{did}remove  {}", tilde(&script)));
            if !dry {
                if let Err(e) = fs::remove_file(&script) {
                    out.push(format!("FAIL    could not remove {}: {e}", tilde(&script)));
                }
            }
        }
    }
    if dropped {
        if let Some(s) = &settings {
            if let Err(e) = write_json(&sp, s) {
                out.push(format!("FAIL    could not write {}: {e}", tilde(&sp)));
            }
        }
    }
    let known = registered();
    if !known.is_empty() {
        out.push(format!(
            "{did}forget  {} mirror{}: the import each repo uses is removed, the facts themselves are left as a \
             snapshot you can read or delete",
            known.len(),
            if known.len() > 1 { "s" } else { "" }
        ));
        if !dry {
            for (into, _repo, _root, loader) in &known {
                if !loader.as_os_str().is_empty() && loader.exists() {
                    unimport(loader, into);
                }
                unregister(into);
            }
        }
    }
    out.push(format!("note    {} is left on disk: you may have edited it", tilde(&corpus_home)));
    out.push(String::new());
    out.extend(remove(dry));
    out
}

// ponytail: ONLY what is true of you whatever you are working on. "Role" and "What you own" used to be
// here and are not: a person's role differs per project, and what they own is a property OF a project,
// which put a paragraph about one product into a file every session in every repo loads. The corpus's
// own USER.md is the proof: ten of its thirteen sections are about one platform, and its cross-cutting
// section says so out loud ("these are cross-cutting: they hold in every repo"). Ownership moved to the
// project brief, where it is scoped by binding and where a review of that repo is actually told it.
pub const ASK_YOU: [(&str, &str); 2] =
    [("Name", "what you would like to be called"), ("How you work", "where you want friction and where you do not")];
pub const ASK_PROJECT: [(&str, &str); 5] = [
    ("The project", "what it is, and who uses it"),
    ("Why it matters", "the outcome that makes the work worth doing"),
    ("Constraints", "regulatory, contractual, performance: anything with real consequences"),
    ("How the code is shaped", "what a newcomer would otherwise learn the hard way"),
    // ponytail: WHO, not only what. Ownership is a property of the project, so it belongs
    // here rather than in USER.md, and a review of one of these repos is told it, which is
    // the point. Shared with the team when the brief is a team's: on a small team "who
    // answers for this" is something everyone benefits from and nobody should have to ask.
    ("Who does what", "who owns which parts, you included"),
];

/// ponytail: a heading twice APPENDS. Overwriting meant a rewrite deleted the first one silently.
fn add(out: &mut Vec<(String, String)>, key: &str, buf: &[&str]) {
    let body = unescape(&buf.join("\n")).trim().to_string();
    match out.iter_mut().find(|(k, _)| k == key) {
        Some((_, have)) => *have = format!("{have}\n\n{body}").trim().to_string(),
        None => out.push((key.to_string(), body)),
    }
}

/// {heading: body} from a brief, in file order. Anything above the first `## ` is not a section.
///
/// ponytail: fences and escapes both matter, because the bodies are text somebody typed. A "## " inside
/// a code block is not a heading, and one that compose() escaped was never a heading; miss either and
/// setup, which rewrites the file from what this returns, silently rearranges what you wrote.
pub fn sections(text: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut key: Option<String> = None;
    let mut buf: Vec<&str> = Vec::new();
    for (line, plain) in outside(text) {
        if let (Some(rest), true) = (line.strip_prefix("## "), plain) {
            if let Some(k) = &key {
                add(&mut out, k, &buf);
            }
            key = Some(rest.trim().to_string());
            buf.clear();
        } else if key.is_some() {
            buf.push(line);
        }
    }
    if let Some(k) = &key {
        add(&mut out, k, &buf);
    }
    out
}

/// Keep an answer from becoming structure, without touching what is inside a fence.
///
/// ponytail: the reader learned about fences and the writer did not, so a hand-written code block
/// containing `# a comment` was written back as `\# a comment`: round-tripping through sections()
/// while rendering a literal backslash on disk, and USER.md is read by the agent as-is, not through
/// sections(). A guard only half of a round trip knows about corrupts the other half.
fn escape(body: &str) -> String {
    let mut out = Vec::new();
    let mut at = None;
    for l in body.lines() {
        let was = at;
        at = fence(l, at);
        out.push(if l.starts_with('#') && was.is_none() && at.is_none() { format!("\\{l}") } else { l.to_string() });
    }
    // ponytail: an answer with an unclosed fence would otherwise bleed into the sections after it:
    // the writer tracks fences per body, the reader per file, so the next read hands back one section
    // swallowing the rest. Closing it here keeps the writer's invariant local to the answer.
    if let Some((ch, width)) = at {
        out.push(ch.to_string().repeat(width));
    }
    out.join("\n")
}

fn unescape(body: &str) -> String {
    // ponytail: a literal "\#" somebody typed comes back as "#". Lossy, and rarer than the corruption
    // escaping prevents, but it is a real edit to their text, so it is written down rather than assumed.
    outside(body)
        .into_iter()
        .map(|(l, plain)| if plain && l.starts_with("\\#") { &l[1..] } else { l })
        .collect::<Vec<_>>()
        .join("\n")
}

/// A markdown brief from (heading, body) pairs, dropping the empty ones.
///
/// ponytail: `extra` carries sections nobody asked about: added by hand, or by an older version that
/// asked different questions. A rewrite that only knew its own questions would delete them.
pub fn compose(title: &str, lead: &str, answers: &[(String, String)], extra: &[(String, String)]) -> String {
    // ponytail: escaped, so an answer containing "## Role" is a line of prose and not a second section.
    // Without it a typed answer could forge headings that the next read would hand back as real ones.
    let body: String = answers
        .iter()
        .chain(extra.iter())
        .filter(|(_, v)| !v.is_empty())
        .map(|(k, v)| format!("## {k}\n\n{}\n\n", escape(v)))
        .collect();
    if body.is_empty() {
        String::new()
    } else {
        format!("{SETUP_MARK}\n# {title}\n\n{lead}\n\n{body}")
    }
}

/// True when there is nothing left to offer. `project` false asks only about USER.md.
///
/// ponytail: the install-time offer passes project=false, because it no longer asks the project
/// question, and a "done" that still waited on a brief nobody was going to be asked for would offer
/// the prompt forever on a machine that answered everything it was asked.
pub fn setup_done(corpus_home: Option<&Path>, project: bool) -> bool {
    let home = corpus_home.map(Path::to_path_buf).unwrap_or_else(|| config::get().corpus_home);
    let user = read(&home.join("identity").join("USER.md"));
    let tmpl = read(&home.join("identity").join("USER.md.template"));
    let mine = !user.trim().is_empty() && user.trim() != tmpl.trim(); // a seeded template is not done
    mine && (!project || memory::brief_written())
}

/// Walk the briefs a corpus needs, writing only what was answered. Returns report lines.
/// `ask(question, current)` returns the answer, or None to skip; `current` is the first line of what
/// a blank answer keeps, "" when there is nothing yet.
///
/// ponytail: asked rather than templated. A blank template is a template nobody fills in, and an agent
/// that knows neither who you are nor what the work is for reasons from the code alone, which is the
/// one thing it can already see.
/// ponytail: `project` false skips the project brief, and `install --full` passes it. WHO YOU ARE is a
/// property of the machine; WHAT THE WORK IS FOR is a property of a repo, and a person works on more
/// than one. Asked once at install time it wrote a single ~/.prs_memory/project.md that every repo
/// bound to no team then read: one product's brief in every review of every other, which is the exact
/// failure brief() was rewritten to stop. Binding already scopes it; `gitdashy setup` still asks.
pub fn setup(ask: &mut dyn FnMut(&str, &str) -> Option<String>, corpus_home: Option<&Path>, project: bool) -> Vec<String> {
    let mut out = Vec::new();
    let home = corpus_home.map(Path::to_path_buf).unwrap_or_else(|| config::get().corpus_home);
    let user = home.join("identity").join("USER.md");
    // ponytail: a marker only compose() writes. Sniffing for the words "gitdashy setup" matched the
    // shipped TEMPLATE, which says them in prose, so the file install --full seeds looked like one
    // setup had written, and editing its blanks in place then lost everything on the next run.
    // ponytail: the file install --full SEEDS is the shipped template, byte for byte. It is not empty
    // and carries no marker, so the guard below read it as a file someone had written by hand and
    // refused to ask, leaving the guided path dead in exactly the case it exists for. Untouched
    // template means untouched; one edit of their own and it is theirs again.
    let text = read(&user);
    let seeded = text.trim() == read(&home.join("identity").join("USER.md.template")).trim();
    if user.exists() && !text.trim().is_empty() && !seeded && !text.contains(SETUP_MARK) {
        // ponytail: the brief refuses when it exists; this file must too, or re-running to change one
        // line destroys the rest. Only a file setup itself wrote is safe to rewrite.
        out.push(format!("ok     {} is yours already: edit it directly to change it", tilde(&user)));
    } else if home.join("identity").is_dir() {
        // ponytail: blank means KEEP, not erase. compose() rewrites the whole file, so a run that only
        // answered one question used to delete every other section, including ones added by hand, while
        // three separate messages promised every question was skippable.
        let have = sections(&text);
        let mut said: Vec<(String, String)> = Vec::new();
        for (k, hint) in ASK_YOU {
            let now = have.iter().find(|(h, _)| h == k).map(|(_, v)| v.as_str()).unwrap_or("");
            let current: String = now.lines().next().unwrap_or("").chars().take(60).collect();
            let answer = ask(&format!("{k}: {hint}"), &current).filter(|a| !a.is_empty());
            said.push((k.to_string(), answer.unwrap_or_else(|| now.to_string())));
        }
        // ponytail: the file's own order, then anything new. Appending the unasked ones moved a section
        // written above ## Name to the bottom on every single run: the file never settled.
        let mut order: Vec<String> = have.iter().map(|(k, _)| k.clone()).collect();
        order.extend(ASK_YOU.iter().map(|(k, _)| k.to_string()).filter(|k| !order.contains(k)));
        let answers: Vec<(String, String)> = order
            .iter()
            .map(|k| {
                let v = said
                    .iter()
                    .chain(have.iter())
                    .find(|(h, _)| h == k)
                    .map(|(_, v)| v.clone())
                    .unwrap_or_default();
                (k.clone(), v)
            })
            .collect();
        let text = compose("Who you are", "Written by `gitdashy setup`. Edit it freely; it is yours.", &answers, &[]);
        if text.is_empty() {
            out.push(format!("ok     {} left as it was: nothing answered", tilde(&user)));
        } else {
            match write_text(&user, &text) {
                Ok(()) => out.push(format!("wrote  {}", tilde(&user))),
                Err(e) => out.push(format!("FAIL   could not write {}: {e}", tilde(&user))),
            }
        }
    } else {
        out.push(format!("SKIP   no corpus at {}: run `gitdashy install --full` first", tilde(&home)));
    }
    if !project {
        // ponytail: BEFORE anything that works out which brief would be written. Below this the code
        // resolves a team and says "writing your own brief": a sentence about a file nobody is being
        // asked for, printed to someone who just declined to be asked. The pointer matters more than the
        // skip: they will look for the question they are used to, so this says where it went.
        out.push(String::new());
        out.push("note   no project brief asked for here: what the work is for belongs to a repo, not".into());
        out.push("       to this machine. `gitdashy setup` writes one; `gitdashy teams --new NAME` and".into());
        out.push("       `gitdashy bind --owner OWNER --team NAME` give each project its own.".into());
        return out;
    }

    // ponytail: a team brief now reaches only the repos BOUND to that team, so the old line ("every
    // review reads it") became false the moment selection stopped being "yours and theirs, always".
    // Saying which reviews read it is the part someone acts on.
    // ponytail: the brief of the team the repo you STAND IN is bound to: that is what "a brief belongs to
    // a repo" means once there is a keyboard in front of it. Picking the team by COUNT wrote the team's
    // brief from inside an unbound side project whenever exactly one team was joined, and yours from
    // inside a bound repo whenever two were: which file this wrote was decided by how many teams you were
    // in, not by where you were. Every note names a command that exists; setup parses no arguments.
    let here = team::origin_slug(Path::new("."));
    let mut slug = if here.is_empty() { String::new() } else { bind::of(&here) };
    if here.is_empty() {
        out.push(
            "note   no git origin here: writing your own brief; run this inside a repo bound to a team to write that team's"
                .into(),
        );
    } else if slug.is_empty() {
        out.push(format!("note   {here} is bound to no team: writing your own brief; `gitdashy bind` binds it to one"));
    } else if bind::team_dir(&slug).is_none() {
        out.push(format!("note   {here} is bound to team {slug}, which this machine has not joined: writing your own brief"));
        slug.clear();
    }
    let mine = slug.is_empty();
    let Some(dest) = memory::brief_path(&slug) else {
        out.push("ok     no brief written: nowhere to write one".into());
        return out;
    };
    // ponytail: says what it COVERS, not just whose it is. "yours" reads as "scoped to me" and it is the
    // opposite: one file, every unbound repo, so a second project inherits the first one's brief.
    let whose = if mine {
        "yours, and EVERY repo bound to no team reads it: one brief for all of them, so bind each project to \
         its own team (`gitdashy teams --new`, `gitdashy bind`) if you have more than one"
            .to_string()
    } else {
        format!(
            "the team's, shared with everyone in {slug}, and every review of a repo bound to it, {here} included, reads it"
        )
    };
    if dest.exists() {
        out.push(format!("ok     {} already written: edit it directly to change it", tilde(&dest)));
        return out;
    }
    out.push(String::new());
    out.push(format!("Now what the work is for. This brief is {whose}."));
    let got: Vec<(String, String)> =
        ASK_PROJECT.iter().map(|(k, hint)| (k.to_string(), ask(&format!("{k}: {hint}"), "").unwrap_or_default())).collect();
    let text =
        compose("What is being built", "Written by `gitdashy setup`. Reviews of the repos it covers read this.", &got, &[]);
    if text.is_empty() {
        out.push("ok     no brief written: `gitdashy setup` again whenever you want one".into());
        return out;
    }
    let r = match dest.parent() {
        Some(p) => fs::create_dir_all(p),
        None => Ok(()),
    }
    .and_then(|_| write_text(&dest, &text));
    match r {
        Ok(()) => out.push(format!("wrote  {}", tilde(&dest))),
        Err(e) => {
            out.push(format!("FAIL   could not write {}: {e}", tilde(&dest)));
            return out;
        }
    }
    if !mine {
        if let Some(d) = team::dir_of(&slug) {
            team::push_dir(&d, "memory: the project brief", "sync");
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// Tests that touch the global config or CLAUDE_CONFIG_DIR take this.
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn lock() -> std::sync::MutexGuard<'static, ()> {
        TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn s(v: &str) -> String {
        v.to_string()
    }

    #[test]
    fn stripping_a_block_keeps_the_files_last_newline() {
        let text = format!("# mine\n\n{CBEGIN}\nx\n{CEND}\n\nmore\n");
        assert_eq!(strip_blocks(&text, CBEGIN, CEND), "# mine\nmore\n");
        assert_eq!(strip_blocks("# mine\nmore", CBEGIN, CEND), "# mine\nmore");
    }

    #[test]
    fn every_block_goes_and_a_fenced_quote_stays() {
        let text = format!("a\n{BEGIN}\nx\n{END}\nb\n{BEGIN}\ny\n{END}\n```\n{BEGIN}\nquoted\n{END}\n```\n");
        let (inside, rest) = split_blocks(&text, BEGIN, END);
        assert_eq!(inside, "x\ny");
        assert!(rest.contains("quoted") && rest.contains("```"));
        assert!(!rest.contains("x\n") && rest.starts_with("a\nb\n"));
        // ~~~ is a fence too, and a longer fence is not closed by a shorter one
        let text = format!("~~~~\n{BEGIN}\n~~~\n{END}\n~~~~\n");
        assert_eq!(strip_blocks(&text, BEGIN, END), text);
    }

    #[test]
    fn an_unclosed_marker_is_not_a_block_of_ours() {
        let text = format!("{BEGIN}\n{STALE}x\nmine\n");
        let (inside, rest) = split_blocks(&text, BEGIN, END);
        assert_eq!(inside, "");
        assert_eq!(rest, text);
        assert!(!inside.contains(STALE));
    }

    #[test]
    fn writing_through_a_symlink_keeps_the_link_and_the_mode() {
        let t = tempfile::tempdir().unwrap();
        let real = t.path().join("dotfiles").join("CLAUDE.md");
        fs::create_dir_all(real.parent().unwrap()).unwrap();
        fs::write(&real, "# mine\n").unwrap();
        let link = t.path().join("CLAUDE.md");
        symlink(&real, &link).unwrap();
        write_text(&link, "new\n").unwrap();
        assert!(is_link(&link));
        assert_eq!(fs::read_to_string(&real).unwrap(), "new\n");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let sp = t.path().join("settings.json");
            fs::write(&sp, "{}").unwrap();
            fs::set_permissions(&sp, fs::Permissions::from_mode(0o600)).unwrap();
            write_text(&sp, "{\"a\": 1}").unwrap();
            assert_eq!(fs::metadata(&sp).unwrap().permissions().mode() & 0o777, 0o600);
            let fresh = t.path().join("fresh.json");
            write_text(&fresh, "{}").unwrap();
            assert_eq!(fs::metadata(&fresh).unwrap().permissions().mode() & 0o777, 0o600);
        }
        assert!(!t.path().join("settings.json.gitdashy.tmp").exists());
    }

    #[test]
    fn registry_round_trip_newest_wins_and_old_lines_survive() {
        let _g = lock();
        let t = tempfile::tempdir().unwrap();
        let reg = t.path().join("mirrors");
        config::update(|c| c.registry = reg.clone());
        let into = t.path().join("repo").join(".agent").join("team");
        assert!(register(&into, "o/r", t.path(), Path::new("")));
        assert!(!register(&into, "o/r", t.path(), Path::new("")));
        assert_eq!(registered(), vec![(into.clone(), s("o/r"), t.path().to_path_buf(), PathBuf::new())]);
        assert!(unregister(&into));
        assert!(!unregister(&into));
        assert!(registered().is_empty());
        // newest wins: registered again after a tombstone, with a new repo name
        assert!(register(&into, "o/other", Path::new(""), Path::new("")));
        assert_eq!(registered()[0].1, "o/other");
        // a pre-JSON line, a tab in a path, a path that looks like a tombstone, and junk
        let tabbed = t.path().join("a\tb");
        let mut text = fs::read_to_string(&reg).unwrap();
        text.push_str("/old/mirror\told/repo\nnot json at all\n");
        fs::write(&reg, text).unwrap();
        assert!(register(&tabbed, "x/y", Path::new(""), Path::new("")));
        assert!(register(Path::new("/forget"), "z/z", Path::new(""), Path::new("")));
        let known = registered();
        assert!(known.iter().any(|e| e.0 == Path::new("/old/mirror") && e.1 == "old/repo"));
        assert!(known.iter().any(|e| e.0 == tabbed && e.1 == "x/y"));
        assert!(known.iter().any(|e| e.0 == Path::new("/forget")));
        assert_eq!(known.len(), 4);
        // the file only grows: a tombstone is appended, never a rewrite
        let before = fs::read_to_string(&reg).unwrap();
        assert!(unregister(&tabbed));
        let after = fs::read_to_string(&reg).unwrap();
        assert!(after.starts_with(&before) && after.contains("\"forget\""));
    }

    #[test]
    fn hooks_and_count_share_a_group_with_somebody_else() {
        let settings = json!({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "'/x/hooks/claude-session-start.sh' /c"},
                       {"type": "command", "command": "someone-else"}]},
            {"hooks": []},
            {"matcher": "x", "hooks": [{"type": "command", "command": "/opt/theirs/claude-session-start.sh"}]}
        ]}});
        assert_eq!(count(&settings, "SessionStart"), 3);
        assert_eq!(count(&settings, "Stop"), 0);
        let kept = hooks(&settings, "/x/hooks/claude-session-start.sh", "SessionStart");
        assert_eq!(kept.len(), 2); // the empty group is dropped, the shared one survives
        assert_eq!(count_of(&kept, "SessionStart"), 2);
        assert_eq!(kept[0]["hooks"][0]["command"], "someone-else");
        assert_eq!(kept[1]["matcher"], "x");
        let none = hooks(&settings, "/y/hooks/claude-session-start.sh", "SessionStart");
        assert_eq!(count_of(&none, "SessionStart"), 3);
    }

    #[test]
    fn sections_and_compose_round_trip() {
        let body = "```python\n# a comment\nx = 1\n```";
        let text = compose("W", "l", &[(s("Name"), s(body))], &[]);
        assert!(!text.contains("\\# a comment") && text.contains("# a comment"));
        assert!(text.starts_with(SETUP_MARK));
        assert_eq!(sections(&text), vec![(s("Name"), s(body))]);
        // structure outside a fence is neutralised
        let forged = compose("W", "l", &[(s("Name"), s("x\n## Role\n\nCTO"))], &[]);
        assert_eq!(sections(&forged).len(), 1);
        assert_eq!(sections(&forged)[0].1, "x\n## Role\n\nCTO");
        // a repeated heading appends
        assert_eq!(sections("## A\n\none\n\n## A\n\ntwo\n"), vec![(s("A"), s("one\n\ntwo"))]);
        // tilde fences, info strings, longer fences
        assert_eq!(sections("## A\n\n~~~\n## B\n~~~\n"), vec![(s("A"), s("~~~\n## B\n~~~"))]);
        assert_eq!(sections("## A\n\n```\n```python\n```\n"), vec![(s("A"), s("```\n```python\n```"))]);
        assert_eq!(sections("## A\n\n````\n```\n## B\n````\n"), vec![(s("A"), s("````\n```\n## B\n````"))]);
        // an unclosed fence in an answer is closed by the writer
        let text = compose("W", "l", &[(s("Name"), s("```")), (s("Role"), s("CTO"))], &[]);
        assert!(sections(&text).iter().any(|(k, v)| k == "Role" && v == "CTO"));
        // empty answers are dropped, extra sections kept, nothing at all is ""
        let text = compose("t", "l", &[(s("A"), s("")), (s("B"), s("b"))], &[(s("Hand added"), s("mine"))]);
        assert_eq!(sections(&text), vec![(s("B"), s("b")), (s("Hand added"), s("mine"))]);
        assert_eq!(compose("t", "l", &[(s("A"), s(""))], &[]), "");
    }

    #[test]
    fn setup_with_a_scripted_ask() {
        let t = tempfile::tempdir().unwrap();
        let home = t.path().join("corpus");
        fs::create_dir_all(home.join("identity")).unwrap();
        let user = home.join("identity").join("USER.md");
        let mut answers = vec![Some(s("Nils")), Some(s("ask first"))].into_iter();
        let mut ask = |_q: &str, _c: &str| answers.next().flatten();
        let out = setup(&mut ask, Some(&home), false);
        let text = fs::read_to_string(&user).unwrap();
        assert!(text.contains("## Name\n\nNils") && text.contains("## How you work\n\nask first"));
        assert!(out.iter().any(|l| l.starts_with("wrote") && l.contains("USER.md")));
        assert!(out.iter().any(|l| l.contains("no project brief asked for here")));
        // hand-added sections stay, blank keeps, the order holds, and the current value is shown
        fs::write(&user, text + "\n## Hand added\n\nsomething I wrote\n").unwrap();
        let mut seen = Vec::new();
        let mut partial = vec![Some(s("Martin")), Some(s(""))].into_iter();
        let mut ask = |q: &str, c: &str| {
            seen.push(format!("{q}|{c}"));
            partial.next().flatten()
        };
        setup(&mut ask, Some(&home), false);
        assert!(seen[0].starts_with("Name: ") && seen[0].ends_with("|Nils"));
        let got = sections(&fs::read_to_string(&user).unwrap());
        assert_eq!(got[0], (s("Name"), s("Martin")));
        assert_eq!(got[1], (s("How you work"), s("ask first")));
        assert_eq!(got[2], (s("Hand added"), s("something I wrote")));
        // a file you wrote yourself is never touched
        fs::write(&user, "# me\n").unwrap();
        let out = setup(&mut |_q: &str, _c: &str| Some(s("x")), Some(&home), false);
        assert!(out[0].contains("yours already"));
        assert_eq!(fs::read_to_string(&user).unwrap(), "# me\n");
        // a seeded template is still fillable, and answering nothing writes nothing
        fs::write(home.join("identity").join("USER.md.template"), "tmpl\n").unwrap();
        fs::write(&user, "tmpl\n").unwrap();
        let out = setup(&mut |_q: &str, _c: &str| None, Some(&home), false);
        assert!(out[0].contains("nothing answered"), "{out:?}");
        assert!(!setup_done(Some(&home), false));
        let out = setup(&mut |_q: &str, _c: &str| Some(s("N")), Some(&home), false);
        assert!(out[0].starts_with("wrote"));
        assert!(setup_done(Some(&home), false));
        // no corpus at all
        let out = setup(&mut |_q: &str, _c: &str| Some(s("N")), Some(&t.path().join("nope")), false);
        assert!(out[0].starts_with("SKIP"));
    }

    #[test]
    fn full_apply_then_full_remove_on_a_temp_config_dir() {
        let _g = lock();
        let t = tempfile::tempdir().unwrap();
        let cfg = t.path().join("claude");
        fs::create_dir_all(&cfg).unwrap();
        std::env::set_var("CLAUDE_CONFIG_DIR", &cfg);
        let corpus_home = t.path().join("corpus-home");
        config::update(|c| {
            c.corpus_home = corpus_home.clone();
            c.local_memory = t.path().join("mem");
            c.memory_dir = t.path().join("mem");
            c.registry = t.path().join("mirrors");
            c.teams = t.path().join("teams");
            c.team = t.path().join("team");
        });
        fs::write(cfg.join("CLAUDE.md"), "# my own rules\n\nkeep me\n").unwrap();
        fs::write(
            cfg.join("settings.json"),
            r#"{"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}"#,
        )
        .unwrap();

        let dry = full_apply(Path::new(""), "", true);
        assert!(dry.iter().any(|l| l.starts_with("would install")), "{dry:?}");
        assert!(!corpus_home.exists() && !cfg.join("hooks").exists());

        let out = full_apply(Path::new(""), "", false);
        assert!(!out.iter().any(|l| l.starts_with("FAIL") || l.starts_with("SKIP")), "{out:?}");
        assert!(corpus_home.join("identity").join("AGENT.md").is_file());
        assert!(corpus_home.join("identity").join("USER.md").is_file()); // seeded
        assert!(ours(&cfg.join("identity"), &corpus_home.join("identity")));
        assert!(ours(&cfg.join("prs-memory"), &t.path().join("mem")));
        let md = fs::read_to_string(cfg.join("CLAUDE.md")).unwrap();
        assert!(md.contains("keep me") && md.contains(CBEGIN) && md.contains(BEGIN) && md.contains("@identity/AGENT.md"));
        let exe = std::env::current_exe().unwrap().to_string_lossy().into_owned();
        for h in &HOOK_TABLE {
            let script = hook_path(h.name);
            assert!(executable(&script), "{script:?}");
            let text = fs::read_to_string(&script).unwrap();
            assert!(!text.contains(PLACEHOLDER) && text.contains(&exe));
        }
        let got: Value = serde_json::from_str(&fs::read_to_string(cfg.join("settings.json")).unwrap()).unwrap();
        assert_eq!(got["model"], "opus");
        assert_eq!(got["hooks"]["Stop"][0]["hooks"][0]["command"], "mine");
        assert_eq!(count(&got, "Stop"), 2);
        assert_eq!(count(&got, "SessionStart"), 1);
        let start = got["hooks"]["SessionStart"][0]["hooks"][0]["command"].as_str().unwrap();
        assert!(start.contains("claude-session-start.sh") && start.ends_with(&quote(&corpus_home.to_string_lossy())));
        let stop = got["hooks"]["Stop"][1]["hooks"][0]["command"].as_str().unwrap();
        assert_eq!(stop, quote(&hook_path("claude-stop.sh").to_string_lossy()));

        // again: nothing doubled
        let out = full_apply(Path::new(""), "", false);
        assert!(out.iter().filter(|l| l.contains("already registered")).count() == 2, "{out:?}");
        let got: Value = serde_json::from_str(&fs::read_to_string(cfg.join("settings.json")).unwrap()).unwrap();
        assert_eq!(count(&got, "Stop"), 2);
        assert_eq!(count(&got, "SessionStart"), 1);
        assert_eq!(fs::read_to_string(cfg.join("CLAUDE.md")).unwrap().matches(CBEGIN).count(), 1);

        let out = full_remove(false);
        assert!(!out.iter().any(|l| l.starts_with("FAIL")), "{out:?}");
        assert_eq!(fs::read_to_string(cfg.join("CLAUDE.md")).unwrap(), "# my own rules\n\nkeep me\n");
        assert!(!lexists(&cfg.join("identity")) && !lexists(&cfg.join("prs-memory")));
        assert!(!lexists(&hook_path("claude-stop.sh")) && !lexists(&hook_path("claude-session-start.sh")));
        let got: Value = serde_json::from_str(&fs::read_to_string(cfg.join("settings.json")).unwrap()).unwrap();
        assert_eq!(got["hooks"]["Stop"][0]["hooks"][0]["command"], "mine");
        assert!(got["hooks"].get("SessionStart").is_none());
        assert!(corpus_home.is_dir()); // you may have edited it

        // a broken settings.json undoes the symlink and the imports
        fs::remove_dir_all(&corpus_home).unwrap();
        fs::write(cfg.join("settings.json"), "{not json").unwrap();
        let out = full_apply(Path::new(""), "", false);
        assert!(out.iter().any(|l| l.starts_with("FAIL") && l.contains("valid JSON")), "{out:?}");
        assert!(out.iter().any(|l| l.contains("undone:")), "{out:?}");
        assert!(!lexists(&cfg.join("identity")) && !corpus_home.exists());
        assert_eq!(fs::read_to_string(cfg.join("CLAUDE.md")).unwrap(), "# my own rules\n\nkeep me\n");
        std::env::remove_var("CLAUDE_CONFIG_DIR");
    }

    #[test]
    fn relpath_and_quote_match_python() {
        assert_eq!(relpath(Path::new("/a/b/c"), Path::new("/a/x")), "../b/c");
        assert_eq!(relpath(Path::new("/a/b"), Path::new("/a/b")), ".");
        assert_eq!(relpath(Path::new("/a/b/.agent/team"), Path::new("/a/b")), ".agent/team");
        assert_eq!(quote("/plain/path-1.sh"), "/plain/path-1.sh");
        assert_eq!(quote("has space"), "'has space'");
        assert_eq!(quote("it's"), "'it'\"'\"'s'");
        assert_eq!(quote(""), "''");
        assert_eq!(thousands(1234567), "1,234,567");
        assert_eq!(thousands(999), "999");
    }
}
