//! The Necronomicon: the main points of memory, ranked by how often they come up.
//!
//! Learning asks the model for a few short points per scope (general, then each repo), each citing the
//! facts it was drawn from. A fact "comes up" when a review proposes it again (memory::append). A point
//! scores by its facts' hits and fades while none of them come up; deranking by hand sinks it faster.
//!
//! ponytail: a digest beside memory, never a rewrite of it. The dream is what rewrites facts and it asks
//! first; this only reads them, so a learn has nothing to accept once it lands.
//! ponytail: nothing learns by itself. A day after the last learn the footer's book reminds you and the
//! button glows; a model call on a timer is spend nobody pressed a key for.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::{config, llm, memory};

const FILE: &str = "necronomicon.json";
const DAY: u64 = 86_400;
/// A point nothing reminds loses half its score every this many seconds.
const HALF_LIFE: f64 = 14.0 * DAY as f64;
/// At or above: on the open page. Below FADED: in the depths, only shown when you dig.
const OPEN: f64 = 0.5;
const FADED: f64 = 0.15;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Point {
    /// "" for general, else "owner/repo".
    pub scope: String,
    pub text: String,
    /// The facts, word for word, this point was drawn from.
    #[serde(default)]
    pub from: Vec<String>,
    /// How many times it was deranked by hand, less the times it was raised.
    #[serde(default)]
    pub down: u32,
    /// When it was first learned, or last raised out of the depths: where fading starts from.
    #[serde(default)]
    pub born: u64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Hit {
    pub n: u32,
    pub at: u64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Tome {
    #[serde(default)]
    pub learned_at: u64,
    #[serde(default)]
    pub points: Vec<Point>,
    /// fact text -> how often a review proposed it again.
    #[serde(default)]
    pub hits: BTreeMap<String, Hit>,
}

// ponytail: one lock around read-modify-write. A review reminding, the UI deranking and a learn landing
// all rewrite the same file from different threads.
static LOCK: Mutex<()> = Mutex::new(());

fn guard() -> MutexGuard<'static, ()> {
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// ponytail: beside the memory checkout, never in it. memory_dir is a git repo whose push runs `git add -A`:
/// inside, every review would push this machine's ranks, and a derank left the tree dirty so `pull --rebase`
/// refused. Named after the dir, so a switch of memory dir gets its own book.
fn path() -> PathBuf {
    let dir = config::get().memory_dir;
    let name = dir
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    dir.with_file_name(format!("{name}.{FILE}"))
}

fn read() -> Tome {
    std::fs::read_to_string(path())
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

/// ponytail: temp sibling and rename, as write_publishing does: a torn file would reset every rank.
fn write(t: &Tome) {
    let p = path();
    let tmp = p.with_extension("json.part");
    let body = serde_json::to_string_pretty(t).unwrap_or_default();
    if std::fs::write(&tmp, body)
        .and_then(|_| std::fs::rename(&tmp, &p))
        .is_err()
    {
        let _ = std::fs::remove_file(&tmp);
        log::error!("could not write {}", p.display());
    }
}

/// A review proposed `fact`, which memory already holds.
pub fn remind(fact: &str) {
    let _g = guard();
    let mut t = read();
    let h = t.hits.entry(fact.to_string()).or_default();
    h.n += 1;
    h.at = now();
    write(&t);
}

/// (score, hits): hits of its facts, fading from the last time one came up, quartered per derank.
pub fn score(p: &Point, hits: &BTreeMap<String, Hit>, at: u64) -> (f64, u32) {
    let mine: Vec<&Hit> = p.from.iter().filter_map(|f| hits.get(f)).collect();
    let n: u32 = mine.iter().map(|h| h.n).sum();
    let last = mine.iter().map(|h| h.at).fold(p.born, u64::max);
    let age = at.saturating_sub(last) as f64;
    let s = (1.0 + n as f64) * 0.5f64.powf(age / HALF_LIFE) / 4f64.powi(p.down as i32);
    (s, n)
}

pub fn tier(score: f64) -> &'static str {
    if score >= OPEN {
        "open"
    } else if score >= FADED {
        "faded"
    } else {
        "depths"
    }
}

/// When the book next asks to be learned: a day after the last learn, or now if it never was.
/// A learn that fails leaves it due.
pub fn next() -> u64 {
    next_at(read().learned_at)
}

fn next_at(learned_at: u64) -> u64 {
    if learned_at == 0 {
        0
    } else {
        learned_at + DAY
    }
}

/// Every point, best first, with its score and tier.
pub fn view() -> Value {
    let t = read();
    let at = now();
    let mut points: Vec<(f64, Value)> = t
        .points
        .iter()
        .map(|p| {
            let (s, n) = score(p, &t.hits, at);
            (
                s,
                json!({"scope": p.scope, "text": p.text, "from": p.from, "hits": n, "down": p.down,
                       "score": (s * 100.0).round() / 100.0, "tier": tier(s)}),
            )
        })
        .collect();
    points.sort_by(|a, b| b.0.total_cmp(&a.0));
    json!({"learnedAt": t.learned_at, "nextAt": next_at(t.learned_at), "points": points.into_iter().map(|(_, v)| v).collect::<Vec<_>>()})
}

/// Derank (`up` false) or raise one point. Raising a point that was never deranked starts its fading
/// over, which is how something dug out of the depths stays out. False when no such point.
pub fn rank(scope: &str, text: &str, up: bool) -> bool {
    let _g = guard();
    let mut t = read();
    let Some(p) = t.points.iter_mut().find(|p| p.scope == scope && p.text == text) else {
        return false;
    };
    match (up, p.down) {
        (false, _) => p.down += 1,
        (true, 0) => p.born = now(),
        (true, _) => p.down -= 1,
    }
    write(&t);
    true
}

pub const PROMPT: &str =
    "You keep the Necronomicon: the few things worth remembering from a code reviewer's memory.
Below are the facts memory holds, numbered, under the scope they belong to: `general` is true of every
repo, `owner/repo` of that repo alone.

For each scope, write the main points: 1 to 7 short sentences, the most important first. A point may
merge several facts. Use only what the facts say. Cite every fact a point draws from by its number.

{facts}

Answer with ONE JSON object and nothing else:
{\"points\": [{\"scope\": \"general\", \"text\": \"<one sentence>\", \"from\": [1, 3]}, ...]}";

/// (prompt, numbered facts) over all of memory.
pub fn prompt() -> (String, Vec<String>) {
    let mut scopes: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (_source, name, _at, facts) in memory::books() {
        let scope = memory::repo_of(&name).unwrap_or_else(|| "general".into());
        let list = scopes.entry(scope).or_default();
        for f in facts {
            if !list.contains(&f) {
                list.push(f);
            }
        }
    }
    let (mut all, mut body) = (Vec::new(), String::new());
    // general first, the repos after it in name order
    let general = scopes.remove("general").map(|f| ("general".to_string(), f));
    for (scope, facts) in general.into_iter().chain(scopes) {
        body.push_str(&format!("### {scope}\n"));
        for f in facts {
            all.push(f);
            body.push_str(&format!("[{}] {}\n", all.len(), all.last().unwrap()));
        }
        body.push('\n');
    }
    (PROMPT.replace("{facts}", body.trim_end()), all)
}

/// Turn the model's answer into points. A point keeps the rank of the old one it shares most of its
/// facts with, so rewording a point on the next learn does not wipe a derank or restart its fading.
pub fn merge(answer: &Value, facts: &[String], old: &[Point], at: u64) -> Vec<Point> {
    let mut out = Vec::new();
    for p in answer["points"].as_array().into_iter().flatten() {
        let text = p["text"].as_str().unwrap_or("").trim().to_string();
        if text.is_empty() {
            continue;
        }
        let scope = match p["scope"].as_str().unwrap_or("general") {
            "general" | "" => String::new(),
            s => s.to_string(),
        };
        let from: Vec<String> = p["from"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|i| i.as_u64())
            .filter_map(|i| facts.get((i as usize).wrapping_sub(1)).cloned())
            .collect();
        let shared = |o: &Point| o.from.iter().filter(|f| from.contains(f)).count();
        let heir = old
            .iter()
            .filter(|o| {
                o.scope == scope && (o.text == text || shared(o) * 2 >= from.len().max(1) && shared(o) > 0)
            })
            .max_by_key(|o| shared(o));
        out.push(Point {
            down: heir.map_or(0, |o| o.down),
            born: heir.map_or(at, |o| o.born),
            scope,
            text,
            from,
        });
    }
    out
}

/// Read all of memory and write down its main points. What the job reports.
pub fn learn(model: &str) -> Result<Value> {
    let (prompt, facts) = prompt();
    if facts.is_empty() {
        return Err(anyhow!("memory holds no facts to learn from"));
    }
    // ponytail: no tools and no system prompt, as the dream: the facts are in the prompt
    let (text, _cost, _ms) = llm::ask(&prompt, model, "", "", memory::TIMEOUT, &[])?;
    let answer = llm::obj(&text)?;
    let _g = guard();
    let mut t = read();
    let points = merge(&answer, &facts, &t.points, now());
    if points.is_empty() {
        return Err(anyhow!("the model wrote down no points"));
    }
    t.points = points;
    t.learned_at = now();
    // hits of facts memory no longer holds rank nothing
    t.hits.retain(|f, _| facts.contains(f));
    write(&t);
    Ok(json!({"points": t.points.len()}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn point(text: &str, from: &[&str], down: u32, born: u64) -> Point {
        Point {
            scope: String::new(),
            text: text.into(),
            from: from.iter().map(|s| s.to_string()).collect(),
            down,
            born,
        }
    }

    #[test]
    fn a_point_fades_without_reminders_and_sinks_when_deranked() {
        let at = 100 * DAY;
        let none = BTreeMap::new();
        assert_eq!(tier(score(&point("x", &["f"], 0, at), &none, at).0), "open");
        assert_eq!(
            tier(score(&point("x", &["f"], 0, at - 14 * DAY), &none, at).0),
            "open",
            "one half-life is 0.5"
        );
        assert_eq!(
            tier(score(&point("x", &["f"], 0, at - 30 * DAY), &none, at).0),
            "faded"
        );
        assert_eq!(
            tier(score(&point("x", &["f"], 0, at - 60 * DAY), &none, at).0),
            "depths"
        );
        assert_eq!(tier(score(&point("x", &["f"], 1, at), &none, at).0), "faded");
        assert_eq!(tier(score(&point("x", &["f"], 2, at), &none, at).0), "depths");
        // an old point whose fact keeps coming up stays open
        let hits = BTreeMap::from([("f".to_string(), Hit { n: 3, at: at - DAY })]);
        let (s, n) = score(&point("x", &["f"], 0, at - 90 * DAY), &hits, at);
        assert_eq!((tier(s), n), ("open", 3));
    }

    #[test]
    fn merge_resolves_citations_and_keeps_the_rank_of_a_reworded_point() {
        let facts = vec!["uses tabs".to_string(), "run make lint".to_string()];
        let old = vec![point("Tabs, always.", &["uses tabs"], 2, 5)];
        let answer = json!({"points": [
            {"scope": "general", "text": "Indent with tabs.", "from": [1, 9]},
            {"scope": "acme/api", "text": "Lint first.", "from": [2]},
            {"scope": "general", "text": "  "},
        ]});
        let got = merge(&answer, &facts, &old, 50);
        assert_eq!(got.len(), 2);
        assert_eq!(
            (
                got[0].scope.as_str(),
                got[0].from.clone(),
                got[0].down,
                got[0].born
            ),
            ("", vec!["uses tabs".to_string()], 2, 5)
        );
        assert_eq!(
            (got[1].scope.as_str(), got[1].down, got[1].born),
            ("acme/api", 0, 50)
        );
    }

    #[test]
    fn prompt_numbers_general_first_and_each_fact_once() {
        let _g = config::test_lock();
        let tmp = tempfile::tempdir().unwrap();
        let mine = tmp.path().join("mine");
        config::update(|c| {
            c.memory_dir = mine.clone();
            c.teams = tmp.path().join("teams");
        });
        std::fs::create_dir_all(&mine).unwrap();
        std::fs::write(mine.join("acme__api.md"), "- uses tabs\n").unwrap();
        std::fs::write(mine.join("general.md"), "- run make lint\n- run make lint\n").unwrap();
        std::fs::write(mine.join("project.md"), "- a team brief, not a fact\n").unwrap();
        let (p, facts) = prompt();
        assert_eq!(facts, vec!["run make lint".to_string(), "uses tabs".to_string()]);
        assert!(
            p.contains("### general\n[1] run make lint\n\n### acme/api\n[2] uses tabs"),
            "{p}"
        );

        remind("uses tabs");
        remind("uses tabs");
        assert_eq!(read().hits["uses tabs"].n, 2);
    }
}
