//! The data shapes every module shares. JSON names match what the Python version wrote to disk and
//! what the frontend reads, so old logs and the page keep working unchanged.

use serde::{Deserialize, Serialize};

/// `{"login": ...}` as GitHub nests it.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Login {
    #[serde(default)]
    pub login: String,
}

/// `{"nameWithOwner": ..., "name": ...}` as GitHub nests it.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Repository {
    #[serde(rename = "nameWithOwner", default)]
    pub name_with_owner: String,
    #[serde(default)]
    pub name: String,
}

/// One pull request row. The GitHub fields plus what the dashboard paints beside them.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Pr {
    #[serde(default)]
    pub number: u64,
    #[serde(default = "unknown_title")]
    pub title: String,
    #[serde(default)]
    pub url: String,
    #[serde(rename = "updatedAt", default)]
    pub updated_at: String,
    #[serde(rename = "isDraft", default)]
    pub is_draft: bool,
    /// None when the author's account is gone.
    #[serde(default)]
    pub author: Option<Login>,
    #[serde(default)]
    pub repository: Repository,
    /// MINE only: "✓ approved", "↻ re-review requested", ... from reviewDecision.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub status: String,
    /// CI glyph for the head commit: ✓ ✗ ● or "".
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub checks: String,
    /// "✓bob ·alice": everyone asked to review or who did.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub reviewers: String,
    /// Head commit sha; "" when graphql did not return one.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub head: String,
    /// "↻ re-review · was ✓ approved" on a REVIEW REQUESTED row already in the log.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub prev: String,
    /// The log entry, on a REVIEWED row.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub review: Option<Box<LogEntry>>,
    /// "adaptive/medium $0.42 3m" on a REVIEWED row.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub tag: String,
}

fn unknown_title() -> String {
    "?".into()
}

impl Pr {
    pub fn repo(&self) -> &str {
        &self.repository.name_with_owner
    }
    pub fn author(&self) -> &str {
        self.author.as_ref().map(|a| a.login.as_str()).unwrap_or("")
    }
    /// The row as it is logged: the GitHub fields only, and never `checks` (stale by the time anyone reads it).
    pub fn for_log(&self) -> Pr {
        Pr {
            checks: String::new(),
            status: String::new(),
            prev: String::new(),
            review: None,
            tag: String::new(),
            ..self.clone()
        }
    }
}

/// One finding a reviewer listed. `kind` is blocking | note | nit.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Finding {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub loc: String,
    #[serde(default)]
    pub text: String,
}

/// One line of the review log (~/.prs_reviewed.jsonl and each team's).
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct LogEntry {
    pub at: String,
    #[serde(default)]
    pub model: String,
    pub pr: Pr,
    #[serde(default)]
    pub depth: String,
    #[serde(default)]
    pub effort: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub head: String,
    #[serde(default)]
    pub cost: Option<f64>,
    #[serde(default)]
    pub ms: Option<u64>,
    pub verdict: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub findings: Vec<Finding>,
}

/// What the reviewer answered, parsed out of the model's JSON.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Verdict {
    /// approve | request_changes | comment
    pub verdict: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub body: String,
    /// Raw, because it is model output: `log::findings` checks each one.
    #[serde(default)]
    pub findings: Vec<serde_json::Value>,
    /// Facts the review proposes for memory.
    #[serde(default)]
    pub remember: Vec<String>,
    #[serde(default)]
    pub cost: Option<f64>,
    #[serde(default)]
    pub ms: Option<u64>,
    /// Depth the review actually ran with, and why, when adaptive chose one.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub depth_used: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub depth_reason: String,
}

/// One dashboard section: MINE, REVIEW REQUESTED, ASSIGNED, REVIEWED.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Section {
    pub name: String,
    /// None when the fetch failed; `err` says why.
    pub prs: Option<Vec<Pr>>,
    pub err: Option<String>,
}

/// One CI check on a PR's head commit. `state` is ok | err | fail | run | skip.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Check {
    pub name: String,
    pub state: String,
}

/// Branch, diff size and CI checks for one PR.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Detail {
    pub branch: String,
    pub add: Option<i64>,
    pub del: Option<i64>,
    pub files: Option<i64>,
    pub checks: Vec<Check>,
}

/// One line of a diff hunk. `n` is the new-file line number, `del` the old-file one.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Line {
    pub n: Option<u32>,
    /// " ", "+" or "-".
    pub sign: String,
    pub text: String,
    pub del: Option<u32>,
    /// Finding kinds anchored on this line.
    #[serde(default)]
    pub marks: Vec<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Hunk {
    pub header: String,
    pub start: u32,
    pub lines: Vec<Line>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct DiffFile {
    pub path: String,
    pub add: u32,
    pub dele: u32,
    pub hunks: Vec<Hunk>,
}

/// A finding placed on the diff: which file and line it landed on.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Mark {
    pub kind: String,
    pub loc: String,
    pub text: String,
    pub path: String,
    pub n: u32,
    /// Index into the files list.
    pub file: usize,
}

/// One unconfirmed observation: how often it recurred, which review runs said it, the fact.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Draft {
    pub count: u32,
    pub ids: Vec<String>,
    pub fact: String,
}

/// A self-check line: what was checked, whether it passed, detail.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct CheckResult {
    pub name: String,
    pub ok: bool,
    pub detail: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pr_reads_a_python_log_row() {
        let raw = r#"{"number":7,"title":"t","url":"u","updatedAt":"2026-01-01T00:00:00+00:00","isDraft":false,
            "author":{"login":"a"},"repository":{"nameWithOwner":"acme/api","name":"api"},"head":"abc"}"#;
        let p: Pr = serde_json::from_str(raw).unwrap();
        assert_eq!(p.repo(), "acme/api");
        assert_eq!(p.author(), "a");
        let back = serde_json::to_value(&p).unwrap();
        assert_eq!(back["updatedAt"], "2026-01-01T00:00:00+00:00");
        assert!(back.get("status").is_none());
    }

    #[test]
    fn pr_with_gone_author_and_missing_title() {
        let p: Pr = serde_json::from_str(r#"{"number":1,"url":"u","author":null}"#).unwrap();
        assert_eq!(p.author(), "");
        assert_eq!(p.title, "?");
    }
}
