//! Where memory lives, and moving it. Port of dashy/core/knowledge.py.
//!
//! ponytail: the filesystem carries the setting, not a config file. Pointing memory somewhere new
//! replaces the dir with a symlink to it, the same trick as team mode, which is "on" because
//! ~/.prs_teams holds a checkout. Env vars still win; they are set before we ever run.

use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use regex::Regex;

use crate::{config, mirror, team};

const GIT_TIMEOUT: u64 = 60;

/// Where the team checkouts live unless moved: ~/.prs_teams.
pub fn default_store() -> PathBuf {
    config::home().join(".prs_teams")
}

pub fn tilde(path: &Path) -> String {
    let home = config::home();
    let p = path.to_string_lossy();
    let h = home.to_string_lossy();
    match p.strip_prefix(&format!("{}/", h)) {
        Some(rest) => format!("~/{}", rest),
        None => p.into_owned(),
    }
}

fn is_link(p: &Path) -> bool {
    std::fs::symlink_metadata(p)
        .map(|m| m.file_type().is_symlink())
        .unwrap_or(false)
}

/// os.path.lexists: the path itself is there, even as a dangling link.
fn lexists(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok()
}

/// os.path.realpath: links resolved as far as the path exists; the rest joined on, normalised.
fn realpath(p: &Path) -> PathBuf {
    let p = abspath_of(p);
    if let Ok(r) = std::fs::canonicalize(&p) {
        return r;
    }
    let mut rest = Vec::new();
    let mut cur = p.clone();
    loop {
        if let Ok(r) = std::fs::canonicalize(&cur) {
            let mut out = r;
            for c in rest.iter().rev() {
                out.push(c);
            }
            return out;
        }
        match (cur.parent(), cur.file_name()) {
            (Some(parent), Some(name)) => {
                rest.push(name.to_os_string());
                cur = parent.to_path_buf();
            }
            _ => return p,
        }
    }
}

/// `path` for the header: ~-shortened, and "→ target" when it is a symlink pointing elsewhere.
pub fn show(path: &Path) -> String {
    let target = if is_link(path) { Some(realpath(path)) } else { None };
    match target {
        Some(t) if t != path => format!("{} → {}", tilde(path), tilde(&t)),
        _ => tilde(path),
    }
}

/// " · no history (why)" for the Memory row, or "".
///
/// ponytail: on the row rather than announced once. A safety net that is OFF should say so every time
/// you look at it: a message you scrolled past is indistinguishable from never having been told.
pub fn history_note() -> String {
    let why = team::no_history(&config::get().memory_dir);
    if why.is_empty() {
        String::new()
    } else {
        format!(" · no history ({})", why)
    }
}

/// True when the team home is not where it would be by default: only then is it worth a header row.
pub fn store_moved() -> bool {
    let teams = config::get().teams;
    is_link(&teams) || teams != default_store()
}

/// The memory dir your own facts live in. ponytail: team mode does NOT repoint this any more: the
/// team is a second source that memory::sources() reads alongside, not a replacement for yours.
pub fn effective() -> PathBuf {
    config::get().memory_dir
}

fn expanduser(s: &str) -> PathBuf {
    if s == "~" {
        config::home()
    } else if let Some(rest) = s.strip_prefix("~/") {
        config::home().join(rest)
    } else {
        PathBuf::from(s)
    }
}

/// True when `s` names a git remote rather than a local directory.
///
/// ponytail: an existing directory always wins, so a real path is never mistaken for a repo. `./x/y`
/// is a path because of the dot; a bare `x/y` that does not exist is read as owner/name, as T does.
pub fn is_remote(s: &str) -> bool {
    let s = s.trim();
    if s.is_empty() || expanduser(s).is_dir() {
        return false;
    }
    // ponytail: the owner half may not START with a dot, or "./notes" matched owner/name and gitdashy
    // went to clone it from GitHub. The docstring above had claimed the dot made it a path since the
    // day it was written; nothing implemented that, and nothing checked.
    let scp = Regex::new(r"^[^@/\s]+@[^:/\s]+:").expect("regex");
    let bare = Regex::new(r"^[\w-][\w.-]*/[\w.-]+$").expect("regex");
    s.contains("://") || scp.is_match(s) || bare.is_match(s)
}

/// Lexically normalise `.` and `..` the way os.path.abspath does.
fn normalise(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            Component::CurDir => {}
            Component::ParentDir => {
                if !matches!(out.components().next_back(), Some(Component::RootDir) | None) {
                    out.pop();
                }
            }
            c => out.push(c.as_os_str()),
        }
    }
    out
}

fn abspath_of(p: &Path) -> PathBuf {
    if p.is_absolute() {
        normalise(p)
    } else {
        match std::env::current_dir() {
            Ok(cwd) => normalise(&cwd.join(p)),
            Err(_) => p.to_path_buf(),
        }
    }
}

/// os.path.abspath, but it cannot fail. ponytail: a relative path needs the cwd, and a cwd that
/// has been deleted makes getcwd() throw, which, inside the UI, used to take the whole dashboard down.
pub fn abspath(path: &str) -> PathBuf {
    abspath_of(&expanduser(path))
}

/// True when memory written at `path` would land in a repo that does not ignore it.
///
/// ponytail: asked before the directory is created, so a typo does not leave a stray dir behind.
pub fn inside_git(path: &Path) -> bool {
    // cannot even resolve it; whatever writes next will say why, more clearly than this
    mirror::tracked_checked(&abspath(&path.to_string_lossy()), mirror::NAMES).unwrap_or(false)
}

#[cfg(unix)]
fn symlink(target: &Path, at: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(target, at)
}

#[cfg(not(unix))]
fn symlink(target: &Path, at: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_dir(target, at)
}

fn sorted_names(d: &Path) -> std::io::Result<Vec<String>> {
    let mut names: Vec<String> = std::fs::read_dir(d)?
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    Ok(names)
}

/// Make `path` a symlink to `new`, moving what is already there. Returns "" or an error string.
pub fn repoint(path: &Path, new: &Path, env: &str) -> String {
    if std::env::var(env).map(|v| !v.is_empty()).unwrap_or(false) {
        return format!("{} is set in the environment; unset it to change this here", env);
    }
    let new = abspath(&new.to_string_lossy());
    if realpath(path) == new {
        return String::new();
    }
    if lexists(&new) && !new.is_dir() {
        return format!("{} exists and is not a directory", tilde(&new));
    }
    match repoint_inner(path, &new) {
        Ok(msg) => msg,
        Err(e) => e.to_string(),
    }
}

fn repoint_inner(path: &Path, new: &Path) -> std::io::Result<String> {
    std::fs::create_dir_all(new)?;
    if is_link(path) {
        std::fs::remove_file(path)?; // only a pointer; there is nothing under it to move
    } else if path.is_dir() {
        // ponytail: EVERY collision is found before ANYTHING moves: the same fix adopt() got, and
        // the same bug. Moving first and refusing after scattered general.md, drafts/ and project.md
        // into the destination and left this directory a stub, under a message saying to merge them
        // by hand as though nothing had happened. Pointing memory at an existing repo (a corpus,
        // a notes repo) is the normal way to hit it, because both ends have a .git.
        let names = sorted_names(path)?;
        let clash: Vec<&String> = names.iter().filter(|n| lexists(&new.join(n))).collect();
        if !clash.is_empty() {
            return Ok(format!(
                "{} exists in both {} and {}; merge by hand, or pick a directory of its own",
                clash.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "),
                tilde(path),
                tilde(new)
            ));
        }
        let mut moved: Vec<&String> = Vec::new();
        for name in &names {
            if let Err(e) = std::fs::rename(path.join(name), new.join(name)) {
                for name in moved.iter().rev() {
                    // ponytail: half-moved is the same loss by a slower route
                    let _ = std::fs::rename(new.join(name), path.join(name));
                }
                return Ok(e.to_string());
            }
            moved.push(name);
        }
        std::fs::remove_dir(path)?;
    } else if lexists(path) {
        return Ok(format!("{} exists and is not a directory", tilde(path)));
    }
    symlink(new, path)?;
    Ok(String::new())
}

fn with_suffix(p: &Path, suffix: &str) -> PathBuf {
    PathBuf::from(format!("{}{}", p.display(), suffix))
}

/// Make your memory directory a checkout of `url`, keeping the facts already in it. "" or an error.
///
/// ponytail: clone to a sibling, move what is there across, then swap. Cloning straight in is not an
/// option: git wants an empty directory, and yours holds the facts you are trying to keep.
pub fn adopt(url: &str, dest: Option<&Path>) -> String {
    let dest = dest
        .map(Path::to_path_buf)
        .unwrap_or_else(|| config::get().local_memory);
    let dest = dest.as_path();
    if std::env::var("PRS_MEMORY")
        .map(|v| !v.is_empty())
        .unwrap_or(false)
    {
        return "PRS_MEMORY is set in the environment; unset it to change this here".into();
    }
    // ponytail: local-only history is not "already a checkout": every memory dir has it now, since it is
    // what makes a bad dream recoverable. Only a checkout with an ORIGIN is already pointed somewhere.
    if team::is_repo(dest) && team::has_remote(dest) {
        return format!(
            "{} is already a checkout of {}",
            tilde(dest),
            team::origin_url(dest)
        );
    }
    if is_link(dest) {
        return format!(
            "{} points at {}; point it back to a plain directory first",
            tilde(dest),
            tilde(&realpath(dest))
        );
    }
    // ponytail: your memory dir gets pushed, and it holds drafts/. Making it the TEAM repo would publish
    // every unconfirmed guess to everyone: the one thing the whole design promises never happens.
    // ponytail: ANY joined team, not "the" one. Your memory holds drafts and is pushed; making it a team
    // repo publishes every unconfirmed guess to everyone in it, and with several joined the wrong one is
    // just as bad as the right one.
    if !url.is_empty()
        && team::dirs()
            .iter()
            .any(|d| team::same_remote(url, &team::origin_url(d)))
    {
        return "that is a team repo — your memory holds drafts, which are yours alone. Use a different one."
            .into();
    }
    let keep: Vec<String> = if dest.is_dir() {
        sorted_names(dest)
            .unwrap_or_default()
            .into_iter()
            .filter(|n| n != ".git")
            .collect()
    } else {
        Vec::new()
    };
    let tmp = with_suffix(dest, ".incoming");
    let _ = std::fs::remove_dir_all(&tmp);
    let err = team::clone(url, &tmp);
    if !err.is_empty() {
        let _ = std::fs::remove_dir_all(&tmp);
        return err;
    }
    // ponytail: EVERY collision is found before ANYTHING moves. Checking inside the move loop meant a
    // clash on the third name rmtree'd a tmp that already held the first two: your facts and your
    // drafts/, deleted, while the message said "merge it by hand" as though nothing had happened.
    // Sorted order made it the likely path, not an exotic one: a memory repo has a general.md, and
    // "general.md" sorts after "acme__api.md" and "drafts".
    let clash: Vec<&str> = keep
        .iter()
        .filter(|n| lexists(&tmp.join(n)))
        .map(String::as_str)
        .collect();
    if !clash.is_empty() {
        let _ = std::fs::remove_dir_all(&tmp); // safe here, and only here: nothing of yours is in it yet
        return format!(
            "{} exists in both {} and the repo; merge it by hand",
            clash.join(", "),
            tilde(dest)
        );
    }
    let mut moved = 0usize;
    let mut old_git: Option<PathBuf> = None;
    let swap = |moved: &mut usize, old_git: &mut Option<PathBuf>| -> std::io::Result<()> {
        for name in &keep {
            std::fs::rename(dest.join(name), tmp.join(name))?;
            *moved += 1;
        }
        if dest.join(".git").is_dir() {
            // ponytail: your local history has a different root than the repo you are adopting, so it
            // cannot be merged in, but it is the record of everything before today and it is not ours
            // to delete. It goes to a sibling with a name that never collides, and stays until you
            // remove it. `git -C <that> log` still reads it.
            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let aside = with_suffix(dest, &format!(".local-history-{}", now));
            std::fs::rename(dest.join(".git"), &aside)?;
            *old_git = Some(aside);
        }
        if dest.is_dir() {
            std::fs::remove_dir(dest)?;
        }
        std::fs::rename(&tmp, dest)
    };
    if let Err(e) = swap(&mut moved, &mut old_git) {
        // ponytail: put back what moved. A half-moved memory dir is the same loss by a slower route:
        // the files exist, but nothing reads them from there and nothing says where they went.
        let _ = std::fs::create_dir_all(dest);
        if let Some(aside) = old_git.filter(|p| p.is_dir()) {
            let _ = std::fs::rename(aside, dest.join(".git"));
        }
        for name in keep[..moved].iter().rev() {
            let _ = std::fs::rename(tmp.join(name), dest.join(name));
        }
        return e.to_string();
    }
    team::union_attrs(dest);
    team::push_dir(
        dest,
        &format!("gitdashy: memory from {}", crate::heartbeat::host()),
        "mine",
    );
    String::new()
}

/// Point the solo memory dir at `new`. Returns "" or an error string.
pub fn set_local(new: &Path) -> String {
    let err = repoint(&config::get().local_memory, new, "PRS_MEMORY");
    if err.is_empty() {
        config::update(|c| c.memory_dir = c.local_memory.clone()); // ponytail: always yours now, in a team or not
    }
    err
}

/// Point the team checkout dir at `new`. Returns "" or an error string.
pub fn set_store(new: &Path) -> String {
    if team::on() {
        return "leave every team first — the refresh thread is pulling in those folders".into();
    }
    repoint(&config::get().teams, new, "PRS_TEAMS")
}

/// Work in a team checkout the remote does not have. -1 when that cannot be told.
///
/// ponytail: commits ahead AND a dirty tree. A push that failed earlier (no git identity configured,
/// say) leaves files staged but uncommitted, which is zero commits ahead and still someone's work.
/// leave() deletes this directory, so the question has to be "is anything here unsaved", not "how many
/// commits".
pub fn unpushed(d: &Path) -> i64 {
    // ponytail: NEVER fails. team::migrate() documents that it cannot (it runs before the first draw,
    // and a panic there is a dashboard that never appears) but it calls this. -1 already means "cannot
    // tell", which is the same answer a hung git should give, and every caller treats it as "do not delete".
    let d = d.to_string_lossy().to_string();
    let Ok(dirty) = mirror::run_git(&["-C", &d, "status", "--porcelain"], GIT_TIMEOUT) else {
        return -1;
    };
    if !dirty.status.success() || !String::from_utf8_lossy(&dirty.stdout).trim().is_empty() {
        return -1; // uncommitted work is as unsaved as an unpushed commit, and we cannot count it
    }
    // ponytail: the check has to match the COMMAND. Counting @{u}..HEAD needs an UPSTREAM, not a
    // remote, and `connect` does `remote add` before it pushes, so a typo'd URL leaves origin set
    // with no upstream. Asking has_remote there still answered -1 and still refused to leave.
    // With nothing to compare against there is nothing unpushed: the only question is whether
    // anything is uncommitted, which is the check above.
    let Ok(up) = mirror::run_git(&["-C", &d, "rev-parse", "--abbrev-ref", "@{u}"], GIT_TIMEOUT) else {
        return -1;
    };
    if !up.status.success() {
        return 0;
    }
    let Ok(r) = mirror::run_git(&["-C", &d, "rev-list", "--count", "@{u}..HEAD"], GIT_TIMEOUT) else {
        return -1;
    };
    if !r.status.success() {
        return -1;
    }
    String::from_utf8_lossy(&r.stdout).trim().parse().unwrap_or(-1)
}

/// Delete a directory this program created. Returns "" or an error string.
///
/// ponytail: rmtree is `rm -rf` with no confirmation and no trash, so it gets a gate rather than a
/// comment. Three refusals, each for a way the path can stop being the thing we think we own:
/// a symlink (deleting what it POINTS AT is never what "remove this directory" meant), something
/// that is not a directory, and a path shallow enough to be a home or a filesystem root.
/// ponytail: errors come back instead of being ignored. A half-deleted tree is precisely the state
/// that poisons the next run, and ignoring them is what stops anyone finding out.
pub fn rmtree_owned(path: &Path) -> String {
    let real = realpath(path);
    if is_link(path) {
        // ponytail: says refusing, because it refuses. The old wording described an action it never took,
        // which is the kind of message that gets believed by the first caller to reach it.
        return format!(
            "refusing: {} is a link to {}; remove the link if that is what you meant",
            tilde(path),
            tilde(&real)
        );
    }
    if !path.is_dir() {
        return if lexists(path) {
            format!("{} is not a directory", tilde(path))
        } else {
            String::new()
        };
    }
    let home = realpath(&config::home());
    if real == Path::new("/") || real == home || real.parent().is_none_or(|p| p == real) {
        return format!("refusing to delete {}", tilde(&real));
    }
    match std::fs::remove_dir_all(path) {
        Ok(()) => String::new(),
        Err(e) => e.to_string(),
    }
}

/// Drop ONE team's checkout. Returns "" or an error string.
///
/// ponytail: takes a slug now. Leaving used to mean "the team", and with several joined an unqualified
/// verb would delete whichever happened to be first: the caller says which, and the confirm prompt
/// names it, because this is the one action here that removes files.
pub fn leave(slug: &str) -> String {
    let joined = team::joined();
    let slug = if slug.is_empty() && joined.len() == 1 {
        joined[0].as_str()
    } else {
        slug
    };
    let d = if slug.is_empty() { None } else { team::dir_of(slug) };
    let Some(d) = d else {
        return if slug.is_empty() {
            format!(
                "say which team: {}",
                if joined.is_empty() {
                    "none joined".to_string()
                } else {
                    joined.join(", ")
                }
            )
        } else {
            "not in that team".into()
        };
    };
    let ahead = unpushed(&d);
    if ahead != 0 {
        // ponytail: -1 (no upstream, no git) is also "do not delete": the log may exist only here.
        // ponytail: "push them first" is wrong for a team with nowhere to push: -1 means uncommitted
        // work now, not unpushed commits, whenever there is no upstream to compare against.
        return if ahead > 0 {
            format!("{} has {} unpushed reviews; push them first", slug, ahead)
        } else {
            format!("{} has uncommitted work in it; commit or discard it first", slug)
        };
    }
    // ponytail: a symlinked TEAM used to be resolved with realpath and deleted at the far end. If you
    // pointed it at a checkout you actually work in, "leave the team" deleted that repo. The link is
    // ours to remove; what it points at is yours, and it is said out loud rather than silently kept.
    if is_link(&d) {
        if let Err(e) = std::fs::remove_file(&d) {
            return e.to_string();
        }
    } else {
        let err = rmtree_owned(&d);
        if !err.is_empty() {
            return err;
        }
    }
    // ponytail: nothing to move back. The log was never repointed: each team keeps its own and yours
    // holds the unbound repos, so leaving one just removes a source that log.logs() stops listing.
    if let Ok(mut n) = team::NAME.lock() {
        *n = team::joined().join(", ");
    }
    if let Ok(mut e) = team::ERROR.lock() {
        e.clear();
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;
    use std::sync::Mutex;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn git(args: &[&str]) {
        assert!(
            Command::new("git").args(args).status().unwrap().success(),
            "git {args:?}"
        );
    }

    #[test]
    fn show_names_the_target_of_a_symlink() {
        let d = tempfile::tempdir().unwrap();
        let real = d.path().join("real");
        let link = d.path().join("link");
        std::fs::create_dir(&real).unwrap();
        symlink(&real, &link).unwrap();
        let got = show(&link);
        assert!(
            got.ends_with(&format!(
                "link → {}",
                tilde(&std::fs::canonicalize(&real).unwrap())
            )),
            "{got}"
        );
        assert!(!show(&real).contains('→'));
    }

    #[test]
    fn tilde_shortens_only_under_home() {
        let home = config::home();
        assert_eq!(tilde(&home.join("x")), "~/x");
        assert_eq!(tilde(&home), home.display().to_string());
        assert_eq!(tilde(Path::new("/nope/x")), "/nope/x");
    }

    #[test]
    fn store_moved_only_off_the_default() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempfile::tempdir().unwrap();
        let before = config::get().teams;
        config::update(|c| c.teams = default_store());
        assert!(!store_moved());
        config::update(|c| c.teams = d.path().join("elsewhere"));
        assert!(store_moved());
        config::update(|c| c.teams = before);
    }

    #[test]
    fn is_remote_tells_a_repo_from_a_directory() {
        let d = tempfile::tempdir().unwrap();
        assert!(is_remote("git@github.com:NilsPontus/Np_Claude_Agentic.git"));
        assert!(is_remote("https://github.com/org-mem.git"));
        assert!(is_remote("ssh://git@host/org-mem"));
        assert!(is_remote("org/mem"));
        assert!(is_remote("owner/name"));
        assert!(!is_remote(d.path().to_str().unwrap()));
        assert!(!is_remote("./org-mem"));
        assert!(!is_remote("./notes"));
        assert!(!is_remote("../shared/memory"));
        assert!(!is_remote("/abs/path/mem"));
        assert!(!is_remote("~/work/mem"));
        assert!(!is_remote(""));
    }

    #[test]
    fn abspath_normalises_and_expands() {
        assert_eq!(abspath("/a/b/../c/./d"), PathBuf::from("/a/c/d"));
        assert_eq!(abspath("/../x"), PathBuf::from("/x"));
        assert_eq!(abspath("~/x"), config::home().join("x"));
        assert!(
            abspath("some/relative/thing").is_absolute()
                || abspath("some/relative/thing") == Path::new("some/relative/thing")
        );
    }

    #[test]
    fn inside_git_sees_an_unignored_path_under_a_repo() {
        let d = tempfile::tempdir().unwrap();
        let repo = d.path().join("repo");
        std::fs::create_dir(&repo).unwrap();
        git(&["init", "-q", repo.to_str().unwrap()]);
        assert!(inside_git(&repo.join("notes").join("mem"))); // nearest existing ancestor is the repo itself
        std::fs::create_dir_all(repo.join(".git/info")).unwrap();
        std::fs::write(repo.join(".git/info/exclude"), "notes/\n").unwrap();
        assert!(!inside_git(&repo.join("notes").join("mem")));
        assert!(!inside_git(&d.path().join("plain"))); // outside any repo
    }

    #[test]
    fn repoint_moves_what_is_there_and_leaves_a_symlink() {
        let d = tempfile::tempdir().unwrap();
        let (old, new) = (d.path().join("old"), d.path().join("new"));
        std::fs::create_dir(&old).unwrap();
        std::fs::write(old.join("general.md"), "- a fact\n").unwrap();
        assert_eq!(repoint(&old, &new, "PRS_TEST_UNSET_VAR"), "");
        assert!(is_link(&old));
        assert_eq!(
            std::fs::canonicalize(&old).unwrap(),
            std::fs::canonicalize(&new).unwrap()
        );
        assert_eq!(
            std::fs::read_to_string(new.join("general.md")).unwrap(),
            "- a fact\n"
        );
        assert_eq!(
            std::fs::read_to_string(old.join("general.md")).unwrap(),
            "- a fact\n"
        );
        assert_eq!(repoint(&old, &new, "PRS_TEST_UNSET_VAR"), ""); // already there
    }

    #[test]
    fn repoint_refuses_when_the_env_var_owns_it() {
        let d = tempfile::tempdir().unwrap();
        let err = repoint(&d.path().join("old"), &d.path().join("new"), "HOME");
        assert!(err.contains("HOME is set in the environment"), "{err}");
        assert!(!d.path().join("new").exists());
    }

    #[test]
    fn repoint_refuses_rather_than_picking_a_winner() {
        let d = tempfile::tempdir().unwrap();
        let (old, new) = (d.path().join("old"), d.path().join("new"));
        std::fs::create_dir_all(old.join("drafts")).unwrap();
        std::fs::create_dir(&new).unwrap();
        std::fs::write(old.join("general.md"), "mine\n").unwrap();
        std::fs::write(new.join("general.md"), "theirs\n").unwrap();
        std::fs::write(old.join("drafts/x.md"), "- (1) a guess\n").unwrap();
        let err = repoint(&old, &new, "PRS_TEST_UNSET_VAR");
        assert!(
            err.contains("merge by hand") && err.contains("general.md"),
            "{err}"
        );
        assert_eq!(
            std::fs::read_to_string(new.join("general.md")).unwrap(),
            "theirs\n"
        );
        assert_eq!(std::fs::read_to_string(old.join("general.md")).unwrap(), "mine\n");
        assert!(old.join("drafts/x.md").exists() && !new.join("drafts").exists());
        // and a file in the way of the new location is refused too
        std::fs::write(d.path().join("afile"), "x").unwrap();
        assert!(repoint(&old, &d.path().join("afile"), "PRS_TEST_UNSET_VAR").contains("is not a directory"));
    }

    #[test]
    fn set_store_refuses_nothing_when_no_team_is_joined_and_set_local_repoints() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempfile::tempdir().unwrap();
        let before = config::get();
        let old = d.path().join("old");
        std::fs::create_dir(&old).unwrap();
        config::update(|c| {
            c.local_memory = old.clone();
            c.memory_dir = d.path().join("somewhere-else");
        });
        if std::env::var("PRS_MEMORY")
            .map(|v| !v.is_empty())
            .unwrap_or(false)
        {
            assert!(set_local(&d.path().join("new")).contains("PRS_MEMORY"));
        } else {
            assert_eq!(set_local(&d.path().join("new")), "");
            assert!(is_link(&old));
            assert_eq!(config::get().memory_dir, old); // always yours now
        }
        config::update(|c| {
            c.local_memory = before.local_memory.clone();
            c.memory_dir = before.memory_dir.clone();
        });
    }

    #[test]
    fn rmtree_owned_refuses_links_roots_and_non_directories() {
        let d = tempfile::tempdir().unwrap();
        let real = d.path().join("real");
        std::fs::create_dir(&real).unwrap();
        let link = d.path().join("link");
        symlink(&real, &link).unwrap();
        assert!(rmtree_owned(&link).starts_with("refusing:"));
        assert!(real.exists());
        assert_eq!(rmtree_owned(&d.path().join("missing")), "");
        std::fs::write(d.path().join("afile"), "x").unwrap();
        assert!(rmtree_owned(&d.path().join("afile")).ends_with("is not a directory"));
        assert!(rmtree_owned(Path::new("/")).starts_with("refusing to delete"));
        assert!(rmtree_owned(&config::home()).starts_with("refusing to delete"));
        assert_eq!(rmtree_owned(&real), "");
        assert!(!real.exists());
    }

    #[test]
    fn unpushed_counts_only_when_there_is_an_upstream() {
        let d = tempfile::tempdir().unwrap();
        let store = d.path().join("org-t");
        std::fs::create_dir(&store).unwrap();
        let s = store.to_str().unwrap();
        git(&["init", "-q", s]);
        std::fs::write(store.join("reviewed.jsonl"), "{\"x\":1}\n").unwrap();
        assert_eq!(unpushed(&store), -1); // no upstream AND dirty
        git(&["-C", s, "add", "-A"]);
        git(&[
            "-C",
            s,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "x",
        ]);
        assert_eq!(unpushed(&store), 0); // nothing to compare against is not "cannot tell"
        assert_eq!(unpushed(&d.path().join("nope")), -1);
    }

    #[test]
    fn leave_says_which_team_when_none_is_joined() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempfile::tempdir().unwrap();
        let before = config::get().teams;
        config::update(|c| c.teams = d.path().join("teams"));
        let got = leave("");
        assert!(got.starts_with("say which team: "), "{got}");
        assert_eq!(leave("org-t"), "not in that team");
        // a joined team with a clean, committed checkout is removed, and the header strip follows
        let store = d.path().join("teams").join("org-t");
        std::fs::create_dir_all(&store).unwrap();
        let s = store.to_str().unwrap();
        git(&["init", "-q", s]);
        std::fs::write(store.join("reviewed.jsonl"), "{}\n").unwrap();
        assert!(leave("org-t").contains("uncommitted work"));
        git(&["-C", s, "add", "-A"]);
        git(&[
            "-C",
            s,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "x",
        ]);
        assert_eq!(leave("org-t"), "");
        assert!(!store.exists());
        assert_eq!(team::NAME.lock().unwrap().as_str(), "");
        config::update(|c| c.teams = before);
    }
}
