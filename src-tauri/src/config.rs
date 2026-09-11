//! Tunables, paths and env overrides. Port of dashy/config.py.
//!
//! ponytail: one `Config` behind a global RwLock. Python had module globals that `load()`, the
//! settings screen and `--demo` all rewrote; here the same thing is `config::update(|c| ...)`.
//! Readers take a cheap clone with `config::get()`, so no lock is held across any real work.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{OnceLock, RwLock};

use serde::{Deserialize, Serialize};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub const MODELS: &[&str] = &["opus", "sonnet", "fable"];
pub const EFFORTS: &[&str] = &["", "low", "medium", "high", "xhigh", "max"];
pub const DEPTHS: &[&str] = &["adaptive", "low", "medium", "high"];
pub const VOICES: &[&str] = &["review", "caveman", "bot"];
pub const HUNTERS: &[&str] = &["ponytail", "security", "tests", "humanizer"];
pub const INTERVALS: &[u64] = &[60, 120, 300, 600, 900];
pub const SUBS: &[&str] = &["all", "open", "off"];
/// Hours of REVIEWED history to show; `None` = all.
pub const WINDOWS: &[Option<u64>] = &[Some(1), Some(4), Some(6), None];
pub const SPLASH_MIN: f64 = 1.0;

/// Verdict -> the status string every row and log reader shows.
pub fn status(verdict: &str) -> Option<&'static str> {
    match verdict {
        "approve" => Some("✓ approved"),
        "request_changes" => Some("✗ changes requested"),
        "comment" => Some("~ commented"),
        _ => None,
    }
}

/// Provider -> (base url, env var holding the api key). See llm.rs.
pub fn endpoints() -> HashMap<&'static str, (String, &'static str)> {
    HashMap::from([
        (
            "openrouter",
            (
                env_or("PRS_OPENROUTER_URL", "https://openrouter.ai/api/v1"),
                "OPENROUTER_API_KEY",
            ),
        ),
        (
            "local",
            (
                env_or("PRS_LOCAL_URL", "http://localhost:1234/v1"),
                "PRS_LOCAL_KEY",
            ),
        ),
    ])
}

pub fn home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn env_or(var: &str, default: &str) -> String {
    std::env::var(var)
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| default.to_string())
}

fn env_path(var: &str, default: &str) -> PathBuf {
    std::env::var_os(var)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(default))
}

/// Everything the app reads at runtime. Defaults come from the environment, then `load()` lays the
/// saved settings over them, then flags and the settings screen change them through `update()`.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Config {
    pub models: Vec<String>,
    pub model: String,
    pub effort: String,
    pub depth: String,
    pub voice: Vec<String>,
    pub hunter: Vec<String>,
    /// Text file appended to the review prompt.
    pub instructions: String,
    /// The OLD single team checkout; migrated into `teams`.
    pub team: PathBuf,
    /// One checkout per team, dir name = slug.
    pub teams: PathBuf,
    /// general.md + one md per repo.
    pub memory_dir: PathBuf,
    /// jsonl, one review per line.
    pub log: PathBuf,
    pub debug_log: PathBuf,
    /// The solo locations, kept so K can show where memory lives and leaving has a home.
    pub local_memory: PathBuf,
    pub local_log: PathBuf,
    pub interval: u64,
    pub notify: bool,
    pub theme: String,
    pub sub: String,
    pub window: Option<u64>,
    pub drafts: bool,
    /// Runtime picks land here. `None` (demo) means never write.
    pub settings: Option<PathBuf>,
    /// Pre-reviews of your own PRs.
    pub self_dir: PathBuf,
    pub backups: PathBuf,
    pub bindings: PathBuf,
    /// Mirrors `gitdashy init` registered.
    pub registry: PathBuf,
    pub corpus_home: PathBuf,
    /// --demo: fakes instead of GitHub, the model and the network.
    pub demo: bool,
    pub debug: bool,
}

impl Default for Config {
    fn default() -> Self {
        let split = |v: &str| -> Vec<String> {
            std::env::var(v)
                .unwrap_or_default()
                .split(',')
                .filter(|s| !s.is_empty())
                .map(String::from)
                .collect()
        };
        let memory_dir = env_path("PRS_MEMORY", ".prs_memory");
        let log = env_path("PRS_LOG", ".prs_reviewed.jsonl");
        Config {
            models: MODELS
                .iter()
                .map(|s| s.to_string())
                .chain(split("PRS_MODELS"))
                .collect(),
            model: env_or("PRS_MODEL", "opus"),
            effort: std::env::var("PRS_EFFORT").unwrap_or_else(|_| "medium".into()),
            depth: env_or("PRS_DEPTH", "adaptive"),
            voice: {
                let v = std::env::var("PRS_VOICE").unwrap_or_else(|_| "review".into());
                v.split(',').filter(|s| !s.is_empty()).map(String::from).collect()
            },
            hunter: split("PRS_HUNTER"),
            instructions: std::env::var("PRS_INSTRUCTIONS").unwrap_or_default(),
            team: env_path("PRS_TEAM", ".prs_team"),
            teams: env_path("PRS_TEAMS", ".prs_teams"),
            local_memory: memory_dir.clone(),
            local_log: log.clone(),
            memory_dir,
            log,
            debug_log: env_path("PRS_DEBUG_LOG", ".prs_debug.log"),
            interval: 300,
            notify: std::env::var("PRS_NOTIFY").map(|v| v != "0").unwrap_or(true),
            theme: env_or("PRS_THEME", "dashy"),
            sub: "all".into(),
            window: Some(4),
            drafts: false,
            settings: Some(env_path("PRS_SETTINGS", ".prs_settings.json")),
            self_dir: home().join(".prs_reviews"),
            backups: home().join(".prs_backups"),
            bindings: env_path("PRS_BINDINGS", ".prs_bindings"),
            registry: home().join(".prs_mirrors"),
            corpus_home: home().join(".agent-corpus"),
            demo: false,
            debug: false,
        }
    }
}

/// What the settings file holds: the keys the dashboard can change. Mirrors config.SAVED.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Saved {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interval: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subs: Option<String>,
    /// `Some(None)` is "all", written as JSON null.
    #[serde(default, skip_serializing_if = "Option::is_none", with = "window_field")]
    pub window: Option<Option<u64>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub drafts: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub depth: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub effort: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notify: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub theme: Option<String>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "one_or_many"
    )]
    pub voice: Option<Vec<String>>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "one_or_many"
    )]
    pub hunter: Option<Vec<String>>,
}

mod window_field {
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    pub fn serialize<S: Serializer>(v: &Option<Option<u64>>, s: S) -> Result<S::Ok, S::Error> {
        v.unwrap_or(None).serialize(s)
    }
    pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<Option<Option<u64>>, D::Error> {
        Ok(Some(Option::<u64>::deserialize(d)?))
    }
}

/// ponytail: a saved checklist may be a string from an older version; accept either shape.
fn one_or_many<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<Vec<String>>, D::Error> {
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum V {
        One(String),
        Many(Vec<String>),
    }
    Ok(match Option::<V>::deserialize(d)? {
        None => None,
        Some(V::One(s)) => Some(vec![s]),
        Some(V::Many(v)) => Some(v),
    })
}

impl Saved {
    /// Read a settings file; `{}` for a missing or broken one.
    pub fn read(path: &Path) -> Saved {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default()
    }
}

static CONFIG: OnceLock<RwLock<Config>> = OnceLock::new();

fn cell() -> &'static RwLock<Config> {
    CONFIG.get_or_init(|| RwLock::new(Config::default()))
}

/// A snapshot of the current config.
pub fn get() -> Config {
    cell().read().unwrap_or_else(|e| e.into_inner()).clone()
}

/// Change the config in place.
pub fn update(f: impl FnOnce(&mut Config)) {
    let mut c = cell().write().unwrap_or_else(|e| e.into_inner());
    f(&mut c);
}

/// Saved settings override the defaults; an env var or CLI flag still wins over the file.
/// Also normalises the checklists: a box that no longer exists is dropped, and voice is never empty.
pub fn load() {
    let env = |v: &str| std::env::var_os(v).is_some();
    update(|c| {
        let saved = c.settings.as_deref().map(Saved::read).unwrap_or_default();
        if let (Some(v), false) = (saved.model, env("PRS_MODEL")) {
            c.model = v;
        }
        if let Some(v) = saved.interval {
            c.interval = v;
        }
        if let Some(v) = saved.subs {
            c.sub = v;
        }
        if let Some(v) = saved.window {
            c.window = v;
        }
        if let Some(v) = saved.drafts {
            c.drafts = v;
        }
        if let (Some(v), false) = (saved.depth, env("PRS_DEPTH")) {
            c.depth = v;
        }
        if let (Some(v), false) = (saved.effort, env("PRS_EFFORT")) {
            c.effort = v;
        }
        if let (Some(v), false) = (saved.notify, env("PRS_NOTIFY")) {
            c.notify = v;
        }
        if let (Some(v), false) = (saved.theme, env("PRS_THEME")) {
            c.theme = v;
        }
        if let (Some(v), false) = (saved.voice, env("PRS_VOICE")) {
            c.voice = v;
        }
        if let (Some(v), false) = (saved.hunter, env("PRS_HUNTER")) {
            c.hunter = v;
        }
        normalise(c);
    });
}

/// Drop checklist boxes that no longer exist; voice is never empty. The one place that rule lives.
pub fn normalise(c: &mut Config) {
    c.voice.retain(|v| VOICES.contains(&v.as_str()));
    if c.voice.is_empty() {
        c.voice = vec!["review".into()];
    }
    c.hunter.retain(|h| HUNTERS.contains(&h.as_str()));
}

/// Everything the settings can change, in the shape `save` writes.
pub fn snapshot(c: &Config) -> Saved {
    Saved {
        model: Some(c.model.clone()),
        interval: Some(c.interval),
        subs: Some(c.sub.clone()),
        window: Some(c.window),
        drafts: Some(c.drafts),
        depth: Some(c.depth.clone()),
        effort: Some(c.effort.clone()),
        notify: Some(c.notify),
        theme: Some(c.theme.clone()),
        voice: Some(c.voice.clone()),
        hunter: Some(c.hunter.clone()),
    }
}

/// Persist the settings. ponytail: `settings: None` (demo) means never write.
pub fn save(values: &Saved) -> std::io::Result<()> {
    if let Some(p) = get().settings {
        std::fs::write(p, serde_json::to_string_pretty(values).unwrap_or_default())?;
    }
    Ok(())
}

/// `~`-shortened path for display.
pub fn tilde(p: &Path) -> String {
    let h = home();
    match p.strip_prefix(&h) {
        Ok(rest) if h != Path::new(".") => format!("~/{}", rest.display()),
        _ => p.display().to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saved_round_trips_and_accepts_old_shapes() {
        let s: Saved = serde_json::from_str(r#"{"voice":"caveman","window":null,"drafts":true}"#).unwrap();
        assert_eq!(s.voice, Some(vec!["caveman".into()]));
        assert_eq!(s.window, Some(None));
        let text = serde_json::to_string(&s).unwrap();
        assert!(text.contains("\"window\":null"));
        assert!(!text.contains("model"));
    }

    #[test]
    fn normalise_drops_unknown_boxes_and_keeps_a_voice() {
        let mut c = Config {
            voice: vec!["ponytail".into()],
            hunter: vec!["ponytail".into(), "nope".into()],
            ..Default::default()
        };
        normalise(&mut c);
        assert_eq!(c.voice, vec!["review"]);
        assert_eq!(c.hunter, vec!["ponytail"]);
    }

    #[test]
    fn status_strings() {
        assert_eq!(status("approve"), Some("✓ approved"));
        assert_eq!(status("nope"), None);
    }
}
