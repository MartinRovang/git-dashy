//! Reviewing a PR with a model and posting the verdict. Port of dashy/core/review.py.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, UNIX_EPOCH};

use anyhow::{anyhow, Context, Result};
use serde_json::Value;

use crate::types::{CheckResult, LogEntry, Pr, Verdict};
use crate::{bind, config, github, llm, log as rlog, memory, team};

pub const PROMPT: &str =
    "Review pull request {repo}#{number}. Look for bugs, logic errors, security issues and missing tests.
{depth}{project}{memory}{prev}";
// ponytail: the contract goes LAST, after the tools, the pasted PR and the voices: everything appended
// to the prompt used to land after it. A re-review was the case that showed: {prev} puts a whole earlier
// review between the instruction to append sections and the moment of writing one, and since that earlier
// review has no sections in it, the nearest example says not to write any. Twice it dropped them.
pub const CONTRACT: &str = r#"

Respond with ONLY a JSON object, no prose, no code fences:
{"verdict": "approve" | "request_changes" | "comment", "summary": "<one line, max 12 words: what the PR changes>",
 "body": "<markdown review, concise, list concrete findings with file:line{sections}>",
 "findings": [{"kind": "blocking" | "note" | "nit", "loc": "<file:line, or the file alone>", "text": "<one line, max 12 words>"}],
 "depth_used": "low" | "medium" | "high", "depth_reason": "<one line: why that depth, e.g. '3-line docs change' or 'touches auth and db migration'>",
 "memory": "<0-3 short lines of overarching facts about this repo worth remembering for future reviews (architecture, conventions, effects on other repos or the database, which authors own which areas); never what this PR itself did; not already in memory; usually empty string>"}
"findings" is the same review as "body", one line each, so a dashboard can list them: every blocking
finding must appear there. Empty list when there is nothing to report.
Use request_changes only for real defects, approve if it is mergeable, comment if unsure."#;
pub const PREV: &str = "

This is a RE-REVIEW: you already reviewed this PR on {at} with verdict {verdict}. The PR has been updated since.
Your earlier review was:
{body}

Do not treat the PR as new. Say which earlier findings are fixed and which still stand, then review what changed since.
Anything that review said about your tools or the machine may since have been fixed — it was written against
an older environment. Re-run the command before repeating a claim that one is missing or broken.";
pub const DEPTH: &[(&str, &str)] = &[
    (
        "low",
        "Depth: minimal. Skim the diff once, flag only obvious defects, keep the body to a few lines.",
    ),
    (
        "medium",
        "Depth: medium. Read the whole diff carefully, check the changed logic and its tests.",
    ),
    (
        "high",
        "Depth: very in-depth. Read the whole diff, then read the surrounding files the changes touch, \
         trace callers, check edge cases, error paths, concurrency and security thoroughly.",
    ),
    (
        "adaptive",
        "Depth: adaptive. Judge from the diff size and risk: a few trivial lines get a quick skim, \
         a large or risky change gets a very in-depth review that reads the surrounding code too.",
    ),
];
// ponytail: each is a prompt fragment; the model writes the sections into body, so no new JSON field
pub const VOICE: &[(&str, &str)] = &[
    ("review", ""),
    (
        "caveman",
        "\n\nAppend a section `---\n**Caveman**`: the verdict in caveman speech. Short sentences. \
         No articles. No hedging. Ten lines max.",
    ),
    (
        "bot",
        "\n\nAppend a section `---\n**Bot**`: the verdict as a terse machine log, one \
         `[LEVEL] file:line message` per finding, no prose.",
    ),
];
// a lens, not a style: each hunts one class of problem the main review is not told to chase
pub const HUNTER: &[(&str, &str)] = &[
    (
        "ponytail",
        "\n\nAppend a section `---\n**Ponytail**`: hunt ONLY over-engineering. One line per finding, \
         `file:L<n>: <delete|stdlib|native|yagni|shrink>: what. replacement.`, then `net: -N lines possible.` \
         Nothing to cut: `Lean already. Ship.`",
    ),
    (
        "security",
        "\n\nAppend a section `---\n**Security**`: hunt ONLY security. Trust boundaries, injection, authz, \
         secrets, unsafe deserialisation, SSRF, path traversal. One line per finding, `file:L<n>: <class>: what. fix.` \
         Nothing found: `No exposure seen.`",
    ),
    (
        "tests",
        "\n\nAppend a section `---\n**Tests**`: hunt ONLY test coverage. Changed logic with no test, tests that \
         cannot fail, mocks that hide the seam under test. One line per finding, `file:L<n>: what is unproven. the test.` \
         Nothing found: `Covered.`",
    ),
    (
        "humanizer",
        "\n\nAppend a section `---\n**Humanizer**`: hunt ONLY AI-sounding prose the PR adds: description, docs, \
         comments, user-facing strings. Not-X-but-Y contrasts, one-line closers, staged run-ups, forced triads, \
         dashes as the universal connector, inflated significance, sales language, stock AI words (delve, \
         pivotal, seamless, robust), bold as decoration, chatbot residue. One line per finding, \
         `file:L<n>: <tell>: the phrase. plain rewrite.` Never add a fact the text lacks. Nothing found: `Reads human.`",
    ),
];
pub const EXPLORE: &str = "

Read the PR with `{cmd} api <github api path>`: a GET against the GitHub API, files decoded, `--diff` for
a unified diff instead of json. It is the only command available to you. Start with the first two:

  {cmd} api /repos/{repo}/pulls/{number}            the description, author, base and head
  {cmd} api /repos/{repo}/pulls/{number} --diff     the diff
  {cmd} api /repos/{repo}/contents/<file>?ref=<head sha>      read a file (no ref = base branch)
  {cmd} api /repos/{repo}/git/trees/<head sha>?recursive=1    every path at that commit, to find one
  {cmd} api \"/search/code?q=<symbol>\"               where a symbol is used, within this repo

Use the head SHA — `head.sha` from the first call — and never the head BRANCH name. A PR from a fork has
its branch in the fork, not here, so a branch name 404s; the commit itself resolves against {repo}
whichever repo it was pushed from.

Reads are confined to {repo}{also}: a path outside that is refused, and a code search is narrowed to
{repo} itself (a search naming another repo, user or org is refused outright rather than narrowed).
That is a boundary, not a hint — do not spend turns trying to widen it.

Look things up rather than assuming: a type or a contract inferred from a call site is how real defects
survive review.
";
pub const ALSO: &str =
    " and the other repos bound to the team {team} — read a sibling by path when this change
depends on one; `/repos/<owner>/<name>/contents/...` and `git/trees` work there the same way";
pub const NO_TOOLS: &str = "

You cannot run any commands. Judge the PR from what follows and say what you could not check.
";
pub const PR_FOLLOWS: &str = "

The pull request and its full diff follow.

";
pub const NO_REVIEW: &str = "\n\nDo NOT write the standard review prose: \"body\" holds ONLY the sections below. \"findings\" stays as specified.";
pub const HELLO: &str = "**Dashy is on its way!** {what} with model **{model}**, effort **{effort}**, depth **{depth}** ({why}), voices **{voices}**{hunters}.";
// other depths: set by the reviewer
pub const WHY: &[(&str, &str)] = &[("adaptive", "Dashy picks the depth from the diff size and risk")];

/// How a review calls back into gitdashy to read the repo: this very binary.
///
/// ponytail: one read-only GET command instead of a shell. It carries no token of its own, gitdashy
/// resolves that, so the reviewer can read the repo and nothing else.
/// ponytail: the RUNNING binary, not a name on PATH. A review ran with a prompt from here while
/// `gitdashy` on PATH was an older install with no `api` command: the reviewer's first tool call fell
/// through into the dashboard and crashed. The running code is the code that has the command.
pub fn api_cmd() -> String {
    std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "gitdashy".into())
}

// ponytail: --safe-mode drops CLAUDE.md, skills, hooks and MCP for this call. Two reasons: a personal
// CLAUDE.md is a dialogue protocol, and this call has no dialogue: it has a JSON contract it can break by
// answering in prose. And without it the prompt would depend on which directory gitdashy was launched from.
pub const SAFE: &str = "--safe-mode";
pub const LENS: &str = "You are reviewing a pull request. Reason about structure before style.

For every change ask: where does the state live and who owns it; where does feedback or observability live;
what breaks if this is deleted; and when does the timing work — ordering, async boundaries, races. Danger
concentrates in the seams: between services, across process and async boundaries, at database calls, wherever
two systems agree on a contract. Read the definition of a thing, not just the code that uses it — inferring a
type or a contract from a call site is how real defects survive review. Before flagging a deviation, check
whether it is already the established pattern in this codebase; an intentional oddity is not a defect.
Security is structural, not a checklist appended at the end. Watch for duplicated or doubled logic, and say
plainly what you verified first-hand and what you took on trust.";
pub const TIMEOUT: u64 = 900;

pub const MARKER: &str = "GITDASHY_SELFCHECK_MARKER";

pub const SELF_HEADER: &str = "# Pre-review — {repo}#{n}

> **Not posted.** gitdashy reviewed your own PR at your request, {at}, with {model} at {depth} depth.
> Nothing was sent to GitHub and nothing was logged. Findings wait in the self drafts pool; a later real
> review that lands on the same fact by itself confirms it.

**Verdict (advisory):** {verdict} — {summary}

---

";

/// GitHub's author_association: standing in the base repo.
pub const TRUSTED: &[&str] = &["OWNER", "MEMBER", "COLLABORATOR"];

/// The value beside `key` in one of the constant tables, "" when absent.
fn table(t: &[(&'static str, &'static str)], key: &str) -> Option<&'static str> {
    t.iter().find(|(k, _)| *k == key).map(|(_, v)| *v)
}

/// `{name}` placeholders filled from `vals` in one pass: a brace that names nothing stays as it is, and
/// a value is never scanned for placeholders of its own (a pasted review may contain any text).
fn fill(template: &str, vals: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(i) = rest.find('{') {
        out.push_str(&rest[..i]);
        let after = &rest[i + 1..];
        let hit = after.find('}').and_then(|j| {
            let key = &after[..j];
            vals.iter().find(|(k, _)| *k == key).map(|(_, v)| (j, *v))
        });
        match hit {
            Some((j, v)) => {
                out.push_str(v);
                rest = &after[j + 1..];
            }
            None => {
                out.push('{');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// A subprocess' stdout, killed when `timeout` passes. Err carries stderr, or why it could not run.
fn run_timed(cmd: &mut Command, timeout: Duration) -> Result<String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let start = Instant::now();
    loop {
        if child.try_wait()?.is_some() {
            break;
        }
        if start.elapsed() > timeout {
            let _ = child.kill();
            let _ = child.wait();
            return Err(anyhow!("timed out after {}s", timeout.as_secs()));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let out = child.wait_with_output()?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(anyhow!(
            "{}",
            if err.is_empty() {
                format!("exit {}", out.status)
            } else {
                err
            }
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Prove the three things a review depends on.
///
/// ponytail: unit tests assert the flags are passed; only a real call proves claude honours them. If
/// --safe-mode stopped suppressing CLAUDE.md the reviews would not fail, they would just quietly inherit
/// whatever is on the machine, so nothing else would ever tell us.
pub fn self_check(model: &str) -> Vec<CheckResult> {
    if llm::provider(model).0 != "claude" {
        return llm::ping(model); // the flags below are claude's; another backend can only prove it answers
    }
    let check = |name: &str, ok: bool, detail: &str| CheckResult {
        name: name.into(),
        ok,
        detail: detail.chars().take(80).collect(),
    };
    let said = match self_check_call(model) {
        Ok(s) => s,
        Err(e) => {
            return vec![CheckResult {
                name: "could not run claude".into(),
                ok: false,
                detail: e.to_string().trim().chars().take(120).collect(),
            }];
        }
    };
    vec![
        check(
            "--append-system-prompt reaches the model",
            said.contains("SCOPED"),
            &said,
        ),
        check(
            "--safe-mode hides the local CLAUDE.md",
            !said.contains(MARKER) && format!(" {said} ").contains(" NO "),
            &said,
        ),
        check("--allowedTools still runs tools", said.contains("TOOLOK"), &said),
    ]
}

/// The marker round trip: claude run in a scratch dir whose CLAUDE.md it must not see. What it said.
fn self_check_call(model: &str) -> Result<String> {
    // tempfile is a dev dependency only: a scratch dir by hand, removed when the check is over
    let d = std::env::temp_dir().join(format!(
        "gitdashy-selfcheck-{}-{}",
        std::process::id(),
        UNIX_EPOCH.elapsed().map(|t| t.as_nanos()).unwrap_or(0)
    ));
    std::fs::create_dir_all(&d)?;
    let got = self_check_in(&d, model);
    let _ = std::fs::remove_dir_all(&d);
    got
}

fn self_check_in(d: &Path, model: &str) -> Result<String> {
    std::fs::write(
        d.join("CLAUDE.md"),
        format!("# local\n\nThe marker is {MARKER}.\n"),
    )?;
    let prompt = format!(
        "Run the bash command: echo TOOLOK\nThen reply with exactly three words: your codename, then YES or NO \
         for whether your instructions mention {MARKER}, then the command's output. Nothing else."
    );
    let mut cmd = Command::new("claude");
    cmd.args([
        "-p",
        &prompt,
        "--output-format",
        "json",
        SAFE,
        "--append-system-prompt",
        "Your codename is SCOPED.",
    ])
    .args(["--allowedTools", "Bash(echo:*)", "--model", model])
    .current_dir(d);
    let raw = run_timed(&mut cmd, Duration::from_secs(300))?;
    let v: Value = serde_json::from_str(&raw)?;
    Ok(v.get("result")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("no result in claude's output"))?
        .trim()
        .into())
}

/// Where a pre-review of this PR lives. One place that knows the name, and it is deterministic.
pub fn self_review_path(repo: &str, n: u64) -> PathBuf {
    self_review_path_in(&config::get().self_dir, repo, n)
}

fn self_review_path_in(dir: &Path, repo: &str, n: u64) -> PathBuf {
    let slug = memory::slug(repo);
    let stem = slug.strip_suffix(".md").unwrap_or(&slug);
    dir.join(format!("{stem}__{n}.md"))
}

/// When the pre-review on disk was written, 0.0 when none.
///
/// ponytail: the FILESYSTEM is the state. It was an in-memory dict, so restarting gitdashy left every
/// pre-review on disk unreachable: `p` would silently run a new one over a file already sitting there.
/// A deterministic name means nothing has to be remembered across a restart.
pub fn self_review_at(repo: &str, n: u64) -> f64 {
    mtime(&self_review_path(repo, n))
}

fn mtime(p: &Path) -> f64 {
    std::fs::metadata(p)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// The ticked boxes in table order.
pub fn on(table: &[&str], chosen: &[String]) -> Vec<String> {
    table
        .iter()
        .filter(|v| chosen.iter().any(|c| c == *v))
        .map(|v| v.to_string())
        .collect()
}

/// The prompt tail for the chosen voices and hunters. config::normalise keeps voice non-empty.
pub fn tail() -> String {
    let c = config::get();
    tail_for(&c.voice, &c.hunter)
}

fn tail_for(voice: &[String], hunter: &[String]) -> String {
    let v = on(config::VOICES, voice);
    let mut out = if v.iter().any(|x| x == "review") {
        String::new()
    } else {
        NO_REVIEW.to_string()
    };
    out.extend(v.iter().map(|x| table(VOICE, x).unwrap_or("")));
    out.extend(
        on(config::HUNTERS, hunter)
            .iter()
            .map(|h| table(HUNTER, h).unwrap_or("")),
    );
    out
}

/// "Caveman", "Ponytail": the section title of a voice or hunter.
fn title(s: &str) -> String {
    let mut c = s.chars();
    c.next()
        .map(|f| f.to_uppercase().collect::<String>() + &c.as_str().to_lowercase())
        .unwrap_or_default()
}

/// The section headings the body must end with, in order. "" when none were asked for.
///
/// ponytail: the SCHEMA has to name them. Asking for them above and then describing "body" as a plain
/// markdown review left the last, most concrete word saying nothing about sections, and a re-review
/// dropped three of four. The instruction that says what a field contains is the field's description.
pub fn sections() -> String {
    let c = config::get();
    sections_for(&c.voice, &c.hunter)
}

fn sections_for(voice: &[String], hunter: &[String]) -> String {
    let names: Vec<String> = on(config::VOICES, voice)
        .iter()
        .filter(|x| *x != "review")
        .chain(on(config::HUNTERS, hunter).iter())
        .map(|x| format!("**{}**", title(x)))
        .collect();
    if names.is_empty() {
        return String::new();
    }
    format!(
        ", then every one of these sections, each after a `---` line, in this order: {} — none of them may be left out",
        names.join(", ")
    )
}

/// True when this PR was opened by someone who already has standing in `repo`.
///
/// ponytail: the premise of the whole boundary is that the diff is written by someone untrusted. When
/// GitHub says the author is the owner, an org member or a collaborator, that premise does not hold:
/// they can read the repo without writing a diff to ask. An outsider's fork PR is the case it does.
/// ponytail: fails CLOSED. The wider read is a privilege, so a call that did not answer must not grant
/// it. One GET beside a model call is not the cost worth optimising away.
/// ponytail: it describes standing in the BASE repo only. An org member need not have access to every
/// private repo the org owns, so this narrows the exposure rather than removing it, which is why it
/// is a gate on a DECLARED set and not a licence to read anything.
pub fn trusted_author(repo: &str, n: u64) -> bool {
    match github::api(&format!("/repos/{repo}/pulls/{n}"), 30) {
        Ok(v) => v
            .get("author_association")
            .and_then(Value::as_str)
            .map(|a| TRUSTED.contains(&a))
            .unwrap_or(false),
        Err(_) => false,
    }
}

/// Everything a prompt is built from, gathered by `_verdict` so the assembly itself is a pure function.
#[derive(Clone, Debug, Default)]
pub struct Inputs<'a> {
    pub repo: &'a str,
    pub number: u64,
    pub depth: &'a str,
    /// (text, whose) from memory::brief.
    pub brief: (String, String),
    pub memory: String,
    pub prev: Option<&'a LogEntry>,
    /// The instructions file's content, when one is configured.
    pub instructions: Option<String>,
    /// Claude fetches the PR itself with `cmd`; any other backend gets `pasted`.
    pub claude: bool,
    pub cmd: String,
    /// The team whose repos may also be read, "" for none.
    pub team: String,
    pub pasted: String,
    pub voice: Vec<String>,
    pub hunter: Vec<String>,
}

/// The whole prompt: the instruction, the brief, the memory, the earlier review, the tools, the voices
/// and the contract last. Err when the depth is not one the tables know.
pub fn prompt(i: &Inputs) -> Result<String> {
    let depth = table(DEPTH, i.depth).ok_or_else(|| anyhow!("unknown depth {:?}", i.depth))?;
    let number = i.number.to_string();
    let (brief, whose) = &i.brief;
    let project = if brief.is_empty() {
        String::new()
    } else {
        format!("\n\nWhat this is being built for, and for whom ({whose}):\n{brief}")
    };
    let mem = if i.memory.is_empty() {
        String::new()
    } else {
        format!("\n\nMemory from earlier reviews, trust it:\n{}", i.memory)
    };
    let prev = i
        .prev
        .map(|p| {
            let at: String = p.at.chars().take(10).collect();
            fill(PREV, &[("at", &at), ("verdict", &p.verdict), ("body", &p.body)])
        })
        .unwrap_or_default();
    let mut out = fill(
        PROMPT,
        &[
            ("repo", i.repo),
            ("number", &number),
            ("depth", depth),
            ("project", &project),
            ("memory", &mem),
            ("prev", &prev),
        ],
    );
    if let Some(text) = &i.instructions {
        out += "\n\nAdditional instructions from the reviewer:\n";
        out += text;
    }
    // ponytail: claude fetches the PR itself, with the one command it is given: pasting it in as well
    // was the same bytes twice, and as an argv string a big diff died with E2BIG before claude started.
    // A backend with no tool loop still gets it pasted; that goes over HTTP, where size is not a limit.
    if i.claude {
        let also = if i.team.is_empty() {
            String::new()
        } else {
            fill(ALSO, &[("team", &i.team)])
        };
        out += &fill(
            EXPLORE,
            &[
                ("cmd", &i.cmd),
                ("repo", i.repo),
                ("number", &number),
                ("also", &also),
            ],
        );
    } else {
        out += NO_TOOLS;
        out += PR_FOLLOWS;
        out += &i.pasted;
    }
    // how to write the body, then its shape: last, both
    out += &tail_for(&i.voice, &i.hunter);
    out += &fill(CONTRACT, &[("sections", &sections_for(&i.voice, &i.hunter))]);
    Ok(out)
}

/// What the model answered, parsed. `memory` may be a string of lines or a list; `verdict` must be there.
/// PORT-NOTE: types::Verdict carries the proposed facts as `remember: Vec<String>`; Python kept the raw
/// "memory" string. The lines are the same, joined back with "\n" for memory::append.
pub fn parse_verdict(text: &str) -> Result<Verdict> {
    let mut v = llm::obj(text)?;
    let obj = v
        .as_object_mut()
        .ok_or_else(|| anyhow!("the answer is not a JSON object"))?;
    if !obj.get("verdict").map(Value::is_string).unwrap_or(false) {
        return Err(anyhow!("no verdict in the answer"));
    }
    let remember: Vec<String> = match obj.remove("memory") {
        Some(Value::String(s)) => memory_lines(&s),
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(Value::as_str)
            .flat_map(memory_lines)
            .collect(),
        _ => Vec::new(),
    };
    obj.insert("remember".into(), serde_json::to_value(remember)?);
    Ok(serde_json::from_value(v)?)
}

fn memory_lines(s: &str) -> Vec<String> {
    s.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(String::from)
        .collect()
}

/// The adaptive rule: when the reviewer chose the depth, the body says which and why.
pub fn with_depth_note(mut v: Verdict, depth: &str) -> Verdict {
    if depth == "adaptive" && !v.depth_used.is_empty() {
        v.body += &format!(
            "\n\n_Dashy reviewed at **{}** depth: {}_",
            v.depth_used, v.depth_reason
        );
    }
    v
}

/// Build the prompt, run the reviewer, return its parsed verdict. Err on failure.
///
/// ponytail: one implementation, because a pre-review that reasons differently from the real one is
/// worth nothing as a preview of it. The only differences are what the caller does with the result.
fn verdict(repo: &str, n: u64, model: &str, prev: Option<&LogEntry>) -> Result<Verdict> {
    let c = config::get();
    // ponytail: the repo SELECTS the brief now: one, by binding, instead of yours and the team's
    // concatenated into every review of every repo. `whose` goes into the prompt rather than being
    // dropped: a reviewer weighs "the team that owns this repo says" differently from "the person
    // running me says, about their work in general", and it is the same value the UI shows.
    let mem = memory::read(repo);
    let brief = memory::brief(Some(repo), None);
    // read per review, so the file can be edited while gitdashy runs
    let instructions = if c.instructions.is_empty() {
        None
    } else {
        Some(
            std::fs::read_to_string(&c.instructions)
                .with_context(|| format!("instructions {}", c.instructions))?,
        )
    };
    let claude = llm::provider(model).0 == "claude";
    let cmd = api_cmd();
    let tools = if claude {
        format!("Bash({cmd} api:*)")
    } else {
        String::new()
    };
    // ponytail: two locks, and both have to open. The DECLARED set says which repos may ever be read
    // together: a diff cannot name one, it can only pick from what a person bound. Author standing says
    // when that is offered at all: an outsider's fork PR is the case the boundary exists for, and it
    // gets the repo under review and nothing else, exactly as before.
    let team = if claude && !c.demo && trusted_author(repo, n) {
        bind::of(repo)
    } else {
        String::new()
    };
    let pasted = if claude {
        String::new()
    } else {
        github::context(repo, n)?
    };
    let text = prompt(&Inputs {
        repo,
        number: n,
        depth: &c.depth,
        brief,
        memory: mem,
        prev,
        instructions,
        claude,
        cmd,
        team: team.clone(),
        pasted,
        voice: c.voice.clone(),
        hunter: c.hunter.clone(),
    })?;
    // ponytail: the scope rides the environment, not the prompt or the argv: see github::scoped.
    let env: Vec<(String, String)> = if claude {
        vec![
            (github::SCOPE.into(), repo.into()),
            (github::SCOPE_TEAM.into(), team),
        ]
    } else {
        Vec::new()
    };
    let (answer, cost, ms) = llm::ask(&text, model, LENS, &tools, TIMEOUT, &env)?;
    let mut v = parse_verdict(&answer)?;
    v.cost = cost;
    v.ms = Some(ms);
    Ok(with_depth_note(v, &c.depth))
}

/// (written_at, moved_since) for this PR's pre-review. (0.0, false) when there is none.
///
/// ponytail: ONE implementation. The pane and the `p` handler each did this comparison themselves, and
/// a pane that says "read" while the key re-runs is exactly the drift two copies invite, which the
/// comment beside one of them warned about while being the second copy.
pub fn self_review_state(pr: &Pr) -> (f64, bool) {
    let at = self_review_at(pr.repo(), pr.number);
    if at == 0.0 {
        return (0.0, false);
    }
    let updated = chrono::DateTime::parse_from_rfc3339(&pr.updated_at.replace('Z', "+00:00"))
        .map(|t| t.timestamp() as f64)
        .unwrap_or(0.0);
    (at, updated > at)
}

/// "error: <last line of the failure, 80 chars>": the row status for a review that did not finish.
fn error_status(e: &anyhow::Error) -> String {
    let text = e.to_string();
    let last = text.trim().lines().last().unwrap_or("?");
    format!("error: {}", last.chars().take(80).collect::<String>())
}

/// Pre-review your own PR. Posts NOTHING. Returns (status, path-to-the-written-review).
///
/// ponytail: nothing is posted, and not only because GitHub refuses to let you approve your own PR:
/// a verdict on your own work is not a review, it is a second opinion from the same head. It goes to a
/// file you read and act on, and the PR stays clean for whoever actually reviews it.
/// ponytail: findings go to the SELF drafts pool, which never promotes on its own. See memory::append_self.
/// PORT-NOTE: Python never raised here: an error is the status string "error: ..." with an empty path,
/// so this returns Ok in every case and the Result of the stub is never Err.
pub fn self_review(pr: &Pr, model: &str) -> Result<(String, PathBuf)> {
    Ok(match self_review_inner(pr, model) {
        Ok(r) => r,
        Err(e) => (error_status(&e), PathBuf::new()),
    })
}

fn self_review_inner(pr: &Pr, model: &str) -> Result<(String, PathBuf)> {
    let (repo, n) = (pr.repo(), pr.number);
    // ponytail: no `prev`: PREV claims "you already reviewed this", and a real reviewer's verdict is
    // not ours to speak for
    let v = verdict(repo, n, model, None)?;
    let c = config::get();
    std::fs::create_dir_all(&c.self_dir)?;
    let dest = self_review_path(repo, n);
    let kept = memory::append_self(repo, &v.remember.join("\n"));
    if !kept.is_empty() {
        // ponytail: every other writer pushes after writing: review(), remember, edit_memory, the dream.
        // Scratch or not, a memory dir backed by git must not depend on the next unrelated write to
        // carry these across; that is the kind of silent exception nobody remembers is there.
        team::push_dir(&c.memory_dir, &format!("memory: pre-review {repo}#{n}"), "mine");
    }
    let shown = config::status(&v.verdict).unwrap_or(&v.verdict).to_string();
    let header = fill(
        SELF_HEADER,
        &[
            ("repo", repo),
            ("n", &n.to_string()),
            ("at", &chrono::Local::now().format("%Y-%m-%d %H:%M").to_string()),
            ("model", model),
            ("depth", &c.depth),
            ("verdict", &shown),
            ("summary", &v.summary),
        ],
    );
    std::fs::write(&dest, format!("{header}{}\n", v.body))?;
    let waiting = if kept.is_empty() {
        String::new()
    } else {
        format!(" · {} waiting", kept.len())
    };
    Ok((format!("{shown} (not posted){waiting}"), dest))
}

/// Review the PR, post the verdict, log it. The row's status string.
/// PORT-NOTE: as with self_review, Python returned "error: ..." instead of raising; this is never Err.
pub fn review(pr: &Pr, model: &str) -> Result<String> {
    Ok(review_inner(pr, model).unwrap_or_else(|e| error_status(&e)))
}

fn review_inner(pr: &Pr, model: &str) -> Result<String> {
    let (repo, n) = (pr.repo(), pr.number);
    let c = config::get();
    let prev = rlog::last(&pr.url);
    let what = match &prev {
        Some(p) => {
            let was = config::status(&p.verdict).ok_or_else(|| anyhow!("unknown verdict {:?}", p.verdict))?;
            format!(
                "Re-reviewing (was {was} on {})",
                p.at.chars().take(10).collect::<String>()
            )
        }
        None => "Reviewing".to_string(),
    };
    let hunters = on(config::HUNTERS, &c.hunter).join(", ");
    let hello = fill(
        HELLO,
        &[
            ("what", &what),
            ("voices", &on(config::VOICES, &c.voice).join(", ")),
            (
                "hunters",
                &if hunters.is_empty() {
                    String::new()
                } else {
                    format!(" and hunters **{hunters}**")
                },
            ),
            ("model", model),
            ("effort", if c.effort.is_empty() { "default" } else { &c.effort }),
            ("depth", &c.depth),
            ("why", table(WHY, &c.depth).unwrap_or("set by the reviewer")),
        ],
    );
    if !c.demo {
        github::comment(repo, n, &hello)?;
    }
    let v = verdict(repo, n, model, prev.as_ref())?;
    if !c.demo {
        github::post_review(repo, n, &v.verdict, &v.body)?;
    }
    // drafts, and whatever a second review confirmed
    let mut promoted = memory::append(repo, &v.remember.join("\n"), "");
    // ponytail: and whatever a TEAMMATE independently observed. This is where new observations arrive
    // and it is already a background thread, so it is where the cross-person count belongs: the
    // alternative was a tick, which would ask a model on a clock rather than when something changed.
    // ponytail: before the pushes, so one push carries the drafts, the promotions and the pool.
    // ponytail: NEVER fails the review. The verdict is posted by now; a model call that throws here
    // would turn a finished review into an error on the row, over a promotion that can happen next
    // time. Same contract team::push has two lines down.
    match std::panic::catch_unwind(|| memory::cross_check(repo, model)) {
        Ok(more) => promoted.extend(more),
        Err(_) => log::error!("cross-check failed for {repo}"),
    }
    let status = rlog::log_review(pr, model, &v, None)?;
    team::push(&format!("review {repo}#{n}: {}", v.verdict));
    let confirmed = if promoted.is_empty() {
        String::new()
    } else {
        format!(", {} confirmed", promoted.len())
    };
    team::push_dir(&c.memory_dir, &format!("memory: {repo}#{n}{confirmed}"), "mine");
    Ok(status)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strs(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn every_voice_and_hunter_has_a_prompt_fragment() {
        for v in config::VOICES {
            assert!(table(VOICE, v).is_some(), "voice {v}");
        }
        for h in config::HUNTERS {
            assert!(table(HUNTER, h).is_some(), "hunter {h}");
        }
        for d in config::DEPTHS {
            assert!(table(DEPTH, d).is_some(), "depth {d}");
        }
    }

    #[test]
    fn on_keeps_table_order() {
        assert_eq!(
            on(config::VOICES, &strs(&["bot", "review"])),
            strs(&["review", "bot"])
        );
        assert_eq!(
            on(config::HUNTERS, &strs(&["tests", "nope", "ponytail"])),
            strs(&["ponytail", "tests"])
        );
        assert!(on(config::HUNTERS, &[]).is_empty());
    }

    #[test]
    fn tail_follows_option_order_and_can_replace_the_review() {
        let t = tail_for(&strs(&["bot", "review"]), &strs(&["tests", "ponytail"]));
        let (b, p, s) = (
            t.find("**Bot**").unwrap(),
            t.find("**Ponytail**").unwrap(),
            t.find("**Tests**").unwrap(),
        );
        assert!(b < p && p < s);
        assert!(!t.contains("**Caveman**") && !t.contains("**Security**") && !t.contains("Do NOT"));
        let t = tail_for(&strs(&["caveman"]), &[]);
        assert!(t.contains("Do NOT write the standard review") && t.contains("**Caveman**"));
        let t = tail_for(&strs(&["review"]), &[]);
        assert!(!t.contains("Append a section") && !t.contains("Do NOT"));
    }

    #[test]
    fn sections_name_every_voice_and_hunter_in_order() {
        assert_eq!(sections_for(&strs(&["review"]), &[]), "");
        let s = sections_for(
            &strs(&["bot", "caveman", "review"]),
            &strs(&["humanizer", "ponytail"]),
        );
        assert!(s.starts_with(", then every one of these sections"));
        assert!(s.contains("**Caveman**, **Bot**, **Ponytail**, **Humanizer**"));
        assert!(s.ends_with("none of them may be left out"));
    }

    #[test]
    fn self_review_path_is_deterministic() {
        // memory::slug is a stub until its owner ports it; the shape of the name is what this checks
        let p = self_review_path_in(Path::new("/x"), "acme/api", 7);
        assert!(p.starts_with("/x"));
        let name = p.file_name().unwrap().to_string_lossy().to_string();
        assert!(name.ends_with("__7.md"), "{name}");
        assert!(!name.contains(".md__"), "{name}");
        assert_eq!(self_review_path_in(Path::new("/x"), "acme/api", 7), p);
    }

    #[test]
    fn a_missing_pre_review_is_zero_and_a_present_one_is_found() {
        let d = tempfile::tempdir().unwrap();
        let p = self_review_path_in(d.path(), "acme/api", 7);
        assert_eq!(mtime(&p), 0.0);
        std::fs::write(&p, "# a pre-review\n").unwrap();
        assert!(mtime(&p) > 0.0);
    }

    fn inputs<'a>(prev: Option<&'a LogEntry>) -> Inputs<'a> {
        Inputs {
            repo: "a/b",
            number: 7,
            depth: "adaptive",
            brief: ("We build X for surgeons.".into(), "team org-t".into()),
            memory: "## General\n### mine\n- always run make lint".into(),
            prev,
            instructions: Some("Always check the changelog.".into()),
            claude: true,
            cmd: "gitdashy".into(),
            team: String::new(),
            pasted: String::new(),
            voice: strs(&["review", "bot"]),
            hunter: strs(&["ponytail"]),
        }
    }

    #[test]
    fn prompt_has_the_contract_last_and_everything_before_it() {
        let p = prompt(&inputs(None)).unwrap();
        assert!(p.starts_with("Review pull request a/b#7."));
        assert!(p.contains("Depth: adaptive."));
        assert!(
            p.contains("What this is being built for, and for whom (team org-t):\nWe build X for surgeons.")
        );
        assert!(p.contains("Memory from earlier reviews, trust it:\n## General"));
        assert!(p.contains("Additional instructions from the reviewer:\nAlways check the changelog."));
        assert!(p.contains("gitdashy api /repos/a/b/pulls/7 --diff"));
        assert!(p.contains("?ref=<head sha>") && p.contains("never the head BRANCH name"));
        assert!(!p.contains("RE-REVIEW") && !p.contains("and the other repos bound to the team"));
        assert!(p.contains("**Bot**") && p.contains("**Ponytail**"));
        assert!(p.contains("in this order: **Bot**, **Ponytail** — none"));
        let contract = p.find("Respond with ONLY a JSON object").unwrap();
        assert!(contract > p.find("being built for").unwrap());
        assert!(contract > p.find("Append a section").unwrap());
        assert!(p.contains(r#"{"verdict": "approve" | "request_changes" | "comment""#)); // braces intact
        assert!(p.contains("Use request_changes only for real defects"));
        assert!(p.len() < 8_000);
    }

    #[test]
    fn a_rereview_prompt_includes_the_earlier_review() {
        let prev = LogEntry {
            at: "2099-01-02T03:04:05+00:00".into(),
            verdict: "request_changes".into(),
            body: "- cache never invalidated {memory}".into(),
            ..Default::default()
        };
        let p = prompt(&inputs(Some(&prev))).unwrap();
        assert!(
            p.contains("RE-REVIEW: you already reviewed this PR on 2099-01-02 with verdict request_changes")
        );
        assert!(p.contains("- cache never invalidated {memory}")); // pasted text is never re-filled
        assert!(p.find("RE-REVIEW").unwrap() < p.find("Additional instructions").unwrap());
    }

    #[test]
    fn a_trusted_author_widens_the_read_and_a_pasted_pr_replaces_the_tools() {
        let mut i = inputs(None);
        i.team = "acme/platform".into();
        let p = prompt(&i).unwrap();
        assert!(p.contains("and the other repos bound to the team acme/platform"));
        let mut i = inputs(None);
        i.claude = false;
        i.pasted = "PASTED PR".into();
        let p = prompt(&i).unwrap();
        assert!(p.contains("You cannot run any commands") && p.ends_with("comment if unsure."));
        assert!(p.contains("The pull request and its full diff follow.\n\nPASTED PR"));
        assert!(!p.contains("gitdashy api"));
        let mut i = inputs(None);
        i.depth = "nope";
        assert!(prompt(&i).is_err());
    }

    #[test]
    fn fill_leaves_unknown_braces_alone() {
        assert_eq!(fill("{a} {b} {c} {", &[("a", "1"), ("b", "{c}")]), "1 {c} {c} {");
        assert_eq!(fill("x{}y", &[]), "x{}y");
    }

    #[test]
    fn hello_names_the_run() {
        let h = fill(
            HELLO,
            &[
                ("what", "Reviewing"),
                ("model", "sonnet"),
                ("effort", "medium"),
                ("depth", "adaptive"),
                ("why", table(WHY, "adaptive").unwrap()),
                ("voices", "review"),
                ("hunters", ""),
            ],
        );
        assert_eq!(h, "**Dashy is on its way!** Reviewing with model **sonnet**, effort **medium**, depth **adaptive** (Dashy picks the depth from the diff size and risk), voices **review**.");
    }

    #[test]
    fn verdict_is_read_past_trailing_prose() {
        let raw = r#"Sure:
{"verdict": "approve", "summary": "s", "body": "b", "depth_used": "high", "depth_reason": "touches auth",
 "memory": "- ci is slow\n\n- db layer is generated", "findings": [{"kind": "nit", "loc": "a.py:1", "text": "t"}]}

Hope that helps! {not json}"#;
        let v = parse_verdict(raw).unwrap();
        assert_eq!(v.verdict, "approve");
        assert_eq!(v.remember, strs(&["- ci is slow", "- db layer is generated"]));
        assert_eq!(v.findings.len(), 1);
        assert_eq!(v.depth_used, "high");
        assert!(parse_verdict(r#"{"summary": "no verdict here"}"#).is_err());
    }

    #[test]
    fn adaptive_depth_explains_itself_and_a_set_depth_does_not() {
        let v = Verdict {
            verdict: "approve".into(),
            body: "b".into(),
            depth_used: "high".into(),
            depth_reason: "touches auth".into(),
            ..Default::default()
        };
        assert_eq!(
            with_depth_note(v.clone(), "adaptive").body,
            "b\n\n_Dashy reviewed at **high** depth: touches auth_"
        );
        assert_eq!(with_depth_note(v.clone(), "high").body, "b");
        let quiet = Verdict {
            depth_used: String::new(),
            ..v
        };
        assert_eq!(with_depth_note(quiet, "adaptive").body, "b");
    }

    #[test]
    fn error_status_is_the_last_line_cut_short() {
        let e = anyhow!("first\nsecond {}", "x".repeat(200));
        let s = error_status(&e);
        assert!(s.starts_with("error: second "));
        assert_eq!(s.chars().count(), "error: ".len() + 80);
        assert_eq!(error_status(&anyhow!("  \n ")), "error: ?");
    }

    #[test]
    fn self_header_and_titles() {
        assert_eq!(title("ponytail"), "Ponytail");
        let h = fill(
            SELF_HEADER,
            &[
                ("repo", "a/b"),
                ("n", "7"),
                ("at", "now"),
                ("model", "opus"),
                ("depth", "high"),
                ("verdict", "~ commented"),
                ("summary", "s"),
            ],
        );
        assert!(h.starts_with("# Pre-review — a/b#7\n\n> **Not posted.**"));
        assert!(h.contains("**Verdict (advisory):** ~ commented — s"));
    }
}
