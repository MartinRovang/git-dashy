//! Which model answers a prompt. Port of dashy/core/llm.py. Bare name = the claude CLI; "provider:model" =
//! an OpenAI-compatible API (openrouter, local).
//!
//! ponytail: the OpenAI path is ONE chat completion, no tool loop, so the caller pastes the PR into the
//! prompt for those backends. Claude reads it itself, with the one command it is given. Add a tool loop when
//! a backend proves it can drive one, not before.

use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Value};

use crate::config;
use crate::types::CheckResult;

/// ponytail: openrouter takes low/medium/high only; claude's two extra levels collapse onto high
fn reasoning(effort: &str) -> &'static str {
    match effort {
        "low" => "low",
        "medium" => "medium",
        _ => "high",
    }
}
/// bytes; an answer is a few KB, so anything past this is padding or a broken upstream
pub const BODY_MAX: u64 = 8 << 20;
/// s per socket read; the whole-request bound is `timeout`
pub const READ_TIMEOUT: u64 = 60;

/// The first JSON object in a model's answer (stops at that object's end).
///
/// ponytail: the stream deserializer STOPS at the object's end. Slicing to the last `}` swept up whatever
/// the model wrote after it, a sign-off, a fenced example, and the parse died with "trailing characters".
pub fn obj(text: &str) -> Result<Value> {
    let i = text
        .find('{')
        .ok_or_else(|| anyhow!("no JSON object in the answer"))?;
    let mut stream = serde_json::Deserializer::from_str(&text[i..]).into_iter::<Value>();
    Ok(stream
        .next()
        .ok_or_else(|| anyhow!("no JSON object in the answer"))??)
}

/// ("claude", name) for a bare name, ("openrouter"|"local", name) for a prefixed one.
pub fn provider(model: &str) -> (String, String) {
    match model.split_once(':') {
        Some((name, rest)) if !rest.is_empty() && config::endpoints().contains_key(name) => {
            (name.to_string(), rest.to_string())
        }
        _ => ("claude".to_string(), model.to_string()),
    }
}

/// Run the prompt. (text, cost_usd, ms). `env` reaches the claude subprocess only.
///
/// ponytail: `env` is how a review scopes the one command it hands out, and it goes here rather than
/// into the prompt because the prompt is what an attacker writes.
pub fn ask(
    prompt: &str,
    model: &str,
    system: &str,
    tools: &str,
    timeout_secs: u64,
    env: &[(String, String)],
) -> Result<(String, Option<f64>, u64)> {
    ask_at(
        prompt,
        model,
        system,
        tools,
        timeout_secs,
        env,
        &config::get().effort,
    )
}

/// Which claude conversation a call belongs to.
///
/// ponytail: a review names its session up front (`New`) instead of reading one back out of the output, so
/// the id exists before the model runs and a review that dies halfway still has one to point at. `Resume`
/// continues it: the agent keeps everything it already read with its tools. Checked against claude
/// 2.1.273: a print-mode session resumes from any directory, after the one it ran in is deleted, under
/// --safe-mode -- so the throwaway cwd every call gets is no obstacle.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Session<'a> {
    None,
    New(&'a str),
    Resume(&'a str),
}

/// A fresh session id: a random UUID v4, which is the only shape --session-id takes.
pub fn new_session_id() -> String {
    let mut b = [0u8; 16];
    getrandom::fill(&mut b).expect("randomness");
    b[6] = (b[6] & 0x0f) | 0x40; // version 4
    b[8] = (b[8] & 0x3f) | 0x80; // RFC 4122 variant
    let h: String = b.iter().map(|x| format!("{x:02x}")).collect();
    format!(
        "{}-{}-{}-{}-{}",
        &h[0..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..32]
    )
}

/// Whether `s` is a session id this code could have made: 36 chars of lowercase hex and dashes in the
/// 8-4-4-4-12 shape. It goes onto an argv from a file on disk, so it is checked, not trusted.
pub fn session_ok(s: &str) -> bool {
    let groups: Vec<&str> = s.split('-').collect();
    groups.iter().map(|g| g.len()).eq([8, 4, 4, 4, 12])
        && groups.iter().all(|g| {
            g.bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        })
}

/// `ask` at an effort of the caller's choosing rather than the picked one; "" leaves it to the model.
pub fn ask_at(
    prompt: &str,
    model: &str,
    system: &str,
    tools: &str,
    timeout_secs: u64,
    env: &[(String, String)],
    effort: &str,
) -> Result<(String, Option<f64>, u64)> {
    ask_session(
        prompt,
        model,
        system,
        tools,
        timeout_secs,
        env,
        effort,
        Session::None,
    )
}

/// `ask_at` inside a claude session. A session only exists for the claude CLI: `Resume` on any other
/// backend is an error, since there is nothing to continue, and `New` there is ignored.
#[allow(clippy::too_many_arguments)]
pub fn ask_session(
    prompt: &str,
    model: &str,
    system: &str,
    tools: &str,
    timeout_secs: u64,
    env: &[(String, String)],
    effort: &str,
    session: Session,
) -> Result<(String, Option<f64>, u64)> {
    let cfg = config::get();
    if cfg.demo {
        return Ok((
            "{\"verdict\":\"approve\",\"summary\":\"demo\",\"body\":\"demo\",\"findings\":[]}".into(),
            Some(0.01),
            1200,
        ));
    }
    let (who, name) = provider(model);
    let started = Instant::now();
    log::debug!("ask {who}:{name} tools={tools} prompt={} chars", prompt.len());
    if who == "claude" {
        return ask_claude(prompt, &name, system, tools, timeout_secs, env, effort, session);
    }
    if let Session::Resume(_) = session {
        bail!("{who}: a conversation needs the claude CLI");
    }
    let endpoints = config::endpoints();
    let (base, key_env) = endpoints
        .get(who.as_str())
        .expect("provider() only names known endpoints");
    let mut messages = Vec::new();
    if !system.is_empty() {
        messages.push(json!({"role": "system", "content": system}));
    }
    messages.push(json!({"role": "user", "content": prompt}));
    let mut sent = json!({"model": name, "messages": messages, "stream": false});
    if who == "openrouter" {
        // ponytail: only there. A local server has no cost, and may not ignore the field
        // asks for usage.cost, in credits, on the response
        sent["usage"] = json!({"include": true});
        // ponytail: --effort was claude-only, so a reasoning model behind OpenRouter thought as hard as it
        // liked and a 2k-token diff took minutes. Same knob, same names, one translation table.
        if !effort.is_empty() {
            sent["reasoning"] = json!({"effort": reasoning(effort)});
        }
    }
    let body = serde_json::to_vec(&sent)?;
    let timeout = Duration::from_secs(timeout_secs);
    // ponytail: the read timeout is per socket read, not per request. OpenRouter pads a slow generation
    // with whitespace to hold the connection open, so bytes keep arriving and that timeout never fires:
    // a wedged upstream spins the dashboard row forever with nothing to press. timeout_global bounds the
    // whole call, and BODY_MAX bounds its size; every pad byte would otherwise sit in memory until then.
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .http_status_as_error(false)
        .timeout_connect(Some(timeout.min(Duration::from_secs(READ_TIMEOUT))))
        .timeout_recv_response(Some(timeout.min(Duration::from_secs(READ_TIMEOUT))))
        .timeout_global(Some(timeout))
        .build()
        .into();
    let url = format!("{}/chat/completions", base.trim_end_matches('/'));
    let mut req = agent.post(&url).header("Content-Type", "application/json");
    let key = std::env::var(key_env).unwrap_or_default();
    if !key.is_empty() {
        // a local server usually wants none
        req = req.header("Authorization", format!("Bearer {key}"));
    }
    let resp = req.send(&body[..]).map_err(|e| http_err(&who, e))?;
    let code = resp.status().as_u16();
    let raw = resp
        .into_body()
        .with_config()
        .limit(BODY_MAX)
        .read_to_vec()
        .map_err(|e| http_err(&who, e))?;
    if code >= 400 {
        // ponytail: the body names which of key, model, credit or context length it was; "HTTP Error 404"
        // on its own sends you looking in the wrong place, and the row only has room for one line.
        bail!("{who} {code}: {}", detail(&raw));
    }
    let got: Value = serde_json::from_slice(&raw).with_context(|| format!("{who}: answer is not JSON"))?;
    if got.get("choices").is_none() {
        // openrouter answers 200 with an error object for upstream failures
        bail!("{who}: {}", detail(&raw));
    }
    let cost = got
        .get("usage")
        .and_then(|u| u.get("cost"))
        .and_then(Value::as_f64); // absent unless we asked, and unless the provider reports it
    let text = got["choices"][0]["message"]["content"]
        .as_str()
        .ok_or_else(|| anyhow!("{who}: no message content in the answer"))?;
    Ok((
        text.trim().to_string(),
        cost,
        started.elapsed().as_millis() as u64,
    ))
}

/// A ureq failure as the one line Python's exceptions gave the row.
fn http_err(who: &str, e: ureq::Error) -> anyhow::Error {
    match e {
        ureq::Error::Timeout(ureq::Timeout::Global) => {
            anyhow!("{who}: no complete answer before the timeout")
        }
        // ponytail: one silent read, not the whole budget, before the row clears
        ureq::Error::Timeout(_) => anyhow!("{who}: no bytes for {READ_TIMEOUT}s"),
        ureq::Error::BodyExceedsLimit(_) => anyhow!("{who}: answer over {} MB, gave up", BODY_MAX >> 20),
        other => anyhow!("{who}: {other}"),
    }
}

static DIRS: AtomicU64 = AtomicU64::new(0);

/// The claude argv, without the prompt, which goes in on stdin.
///
/// ponytail: the prompt goes in on stdin. As an argument it hit Linux's 128 KB cap on one argv string
/// (E2BIG), and the dream prompt carries every memory file, already ~90 KB (#80).
pub fn claude_args(name: &str, system: &str, tools: &str, effort: &str, session: Session) -> Vec<String> {
    let mut a: Vec<String> = ["-p", "--output-format", "json", "--safe-mode", "--model", name]
        .iter()
        .map(|s| s.to_string())
        .collect();
    let mut push = |k: &str, v: &str| {
        a.push(k.to_string());
        a.push(v.to_string());
    };
    if !system.is_empty() {
        push("--append-system-prompt", system);
    }
    if !tools.is_empty() {
        push("--allowedTools", tools);
    }
    if !effort.is_empty() {
        push("--effort", effort);
    }
    match session {
        Session::None => {}
        Session::New(id) => push("--session-id", id),
        Session::Resume(id) => push("--resume", id),
    }
    a
}

#[allow(clippy::too_many_arguments)]
fn ask_claude(
    prompt: &str,
    name: &str,
    system: &str,
    tools: &str,
    timeout_secs: u64,
    env: &[(String, String)],
    effort: &str,
    session: Session,
) -> Result<(String, Option<f64>, u64)> {
    if let Session::New(id) | Session::Resume(id) = session {
        if !session_ok(id) {
            bail!("claude: {id:?} is not a session id");
        }
    }
    // ponytail: a test build never starts the real CLI. One did, from a cross-check whose pair was not
    // worded the same: it ran `claude` on the operator's machine and left a session transcript in their
    // ~/.claude/projects. Anything under test that reaches a model gets "not asked", which every caller
    // already has to handle.
    if cfg!(test) {
        bail!("claude: a test never runs the claude CLI");
    }
    let mut cmd = Command::new("claude");
    cmd.args(claude_args(name, system, tools, effort, session));
    // ponytail: same reason as a review, see review::review: a fresh, empty cwd.
    let here = std::env::temp_dir().join(format!(
        "gitdashy-{}-{}",
        std::process::id(),
        DIRS.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&here)?;
    // ponytail: `env` overlays this process's own, so a caller adds one variable rather than handing the
    // child an environment built from scratch: PATH and the token still arrive.
    cmd.current_dir(&here)
        .envs(env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let out = run(cmd, prompt, Duration::from_secs(timeout_secs));
    let _ = std::fs::remove_dir_all(&here);
    let out = out?;
    let result: Value = serde_json::from_str(&out).context("claude: output is not JSON")?;
    let text = result["result"]
        .as_str()
        .ok_or_else(|| anyhow!("claude: no result in the output"))?;
    Ok((
        text.trim().to_string(),
        result.get("total_cost_usd").and_then(Value::as_f64),
        result.get("duration_ms").and_then(Value::as_u64).unwrap_or(0),
    ))
}

/// stdout of a finished, successful command; Err on a non-zero exit or once `timeout` passes (killed).
fn run(mut cmd: Command, input: &str, timeout: Duration) -> Result<String> {
    let mut child = cmd
        .stdin(Stdio::piped())
        .spawn()
        .context("claude: could not start")?;
    let mut stdin = child.stdin.take().expect("piped");
    let input = input.to_string();
    // a thread, so a prompt bigger than the pipe buffer cannot block on a child that is not reading yet;
    // dropping stdin at the end is the EOF claude waits for
    std::thread::spawn(move || {
        let _ = stdin.write_all(input.as_bytes());
    });
    let mut stdout = child.stdout.take().expect("piped");
    let mut stderr = child.stderr.take().expect("piped");
    let out = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stdout.read_to_string(&mut s);
        s
    });
    let err = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stderr.read_to_string(&mut s);
        s
    });
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(st) = child.try_wait()? {
            break st;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            bail!("claude: timed out after {}s", timeout.as_secs());
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    let stdout = out.join().unwrap_or_default();
    let stderr = err.join().unwrap_or_default();
    if !status.success() {
        let why: String = stderr.trim().chars().take(200).collect();
        bail!("claude exited with {status}: {why}");
    }
    Ok(stdout)
}

/// The human half of an error body: the message if it is the usual JSON shape, else the raw text.
pub fn detail(raw: &[u8]) -> String {
    let text = String::from_utf8_lossy(raw).trim().to_string();
    let clip = |s: String| s.chars().take(200).collect::<String>();
    let got = match serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|v| v.get("error").cloned())
    {
        Some(e) => e,
        None => return clip(text),
    };
    let shown = match &got {
        Value::Object(o) => o.get("message").cloned().unwrap_or(got.clone()),
        _ => got,
    };
    clip(match shown {
        Value::String(s) => s,
        other => other.to_string(),
    })
}

/// Prove the backend answers at all.
pub fn ping(model: &str) -> Vec<CheckResult> {
    let who = provider(model).0;
    match ask("Reply with exactly: OK", model, "", "", 120, &[]) {
        Ok((said, _, _)) => vec![CheckResult {
            name: format!("{who} answers"),
            ok: said.to_uppercase().contains("OK"),
            detail: said.chars().take(80).collect(),
        }],
        // ponytail: a check that reports its own failure, so every backend fails the same way
        Err(e) => {
            log::error!("ping {model} failed: {e:#}");
            vec![CheckResult {
                name: format!("could not reach {who}"),
                ok: false,
                detail: e.to_string().chars().take(120).collect(),
            }]
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_session_is_named_up_front_or_resumed_and_never_both() {
        let id = "5e3ae8e0-544e-4128-88af-fe301d354aae";
        let args = |s| claude_args("opus", "", "", "", s);
        let has = |a: &[String], k: &str, v: &str| a.windows(2).any(|w| w[0] == k && w[1] == v);
        let new = args(Session::New(id));
        assert!(has(&new, "--session-id", id) && !new.contains(&"--resume".to_string()));
        let again = args(Session::Resume(id));
        assert!(has(&again, "--resume", id) && !again.contains(&"--session-id".to_string()));
        let none = args(Session::None);
        assert!(!none.iter().any(|a| a == "--resume" || a == "--session-id"));
        // the scope a review runs under must survive a resume: same flags, whatever the session
        let scoped = claude_args("opus", "lens", "Bash(gh api:*)", "high", Session::Resume(id));
        assert!(has(&scoped, "--allowedTools", "Bash(gh api:*)"));
        assert!(
            scoped.contains(&"--safe-mode".to_string()) && has(&scoped, "--append-system-prompt", "lens")
        );
    }

    #[test]
    fn a_session_id_is_a_uuid_v4_and_nothing_else_passes() {
        let id = new_session_id();
        assert!(session_ok(&id), "{id}");
        assert_eq!(&id[14..15], "4", "version nibble");
        assert!("89ab".contains(&id[19..20]), "variant nibble");
        assert_ne!(new_session_id(), new_session_id());
        for bad in [
            "",
            "not-a-uuid",
            "5E3AE8E0-544E-4128-88AF-FE301D354AAE",
            "5e3ae8e0-544e-4128-88af-fe301d354aa",
            "--resume",
            "5e3ae8e0-544e-4128-88af-fe301d354aaz",
        ] {
            assert!(!session_ok(bad), "{bad}");
        }
    }

    use std::sync::MutexGuard;

    /// One request the fake server saw: its path, headers and body.
    struct Seen {
        url: String,
        headers: Vec<(String, String)>,
        body: Value,
    }

    /// A one-shot OpenAI-compatible server answering `status` with `raw`. Returns (base url, what it saw).
    fn serve(status: u16, raw: &'static str) -> (String, std::thread::JoinHandle<Seen>) {
        let server = tiny_http::Server::http("127.0.0.1:0").unwrap();
        let base = format!("http://{}/v1", server.server_addr());
        let handle = std::thread::spawn(move || {
            let mut req = server.recv().unwrap();
            let mut body = String::new();
            req.as_reader().read_to_string(&mut body).unwrap();
            let seen = Seen {
                url: req.url().to_string(),
                headers: req
                    .headers()
                    .iter()
                    .map(|h| (h.field.as_str().to_string(), h.value.to_string()))
                    .collect(),
                body: serde_json::from_str(&body).unwrap(),
            };
            req.respond(tiny_http::Response::from_string(raw).with_status_code(status))
                .unwrap();
            seen
        });
        (base, handle)
    }

    fn setup(who: &str, base: &str, key: Option<&str>, effort: &str) -> MutexGuard<'static, ()> {
        let g = crate::config::test_lock();
        let (url_env, key_env) = match who {
            "openrouter" => ("PRS_OPENROUTER_URL", "OPENROUTER_API_KEY"),
            _ => ("PRS_LOCAL_URL", "PRS_LOCAL_KEY"),
        };
        std::env::set_var(url_env, base);
        match key {
            Some(k) => std::env::set_var(key_env, k),
            None => std::env::remove_var(key_env),
        }
        config::update(|c| {
            c.effort = effort.into();
            c.demo = false;
        });
        g
    }

    fn header<'a>(s: &'a Seen, name: &str) -> Option<&'a str> {
        s.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }

    #[test]
    fn a_test_never_runs_the_claude_cli() {
        let got = ask("p", "opus", "", "", 1, &[]);
        assert!(got.is_err_and(|e| e.to_string().contains("a test never runs the claude CLI")));
    }

    #[test]
    fn provider_splits_only_known_prefixes() {
        assert_eq!(provider("opus"), ("claude".into(), "opus".into()));
        assert_eq!(
            provider("openrouter:x-ai/grok-4"),
            ("openrouter".into(), "x-ai/grok-4".into())
        );
        assert_eq!(provider("local:qwen3"), ("local".into(), "qwen3".into()));
        assert_eq!(provider("weird:thing"), ("claude".into(), "weird:thing".into()));
        assert_eq!(provider("local:"), ("claude".into(), "local:".into()));
    }

    #[test]
    fn openai_backend_posts_prompt_and_key() {
        let (base, seen) = serve(200, r#"{"choices": [{"message": {"content": " hi "}}]}"#);
        let _g = setup("openrouter", &base, Some("sk-test"), "medium");
        let (text, cost, _ms) = ask("prompt here", "openrouter:x-ai/grok-4", "lens", "", 30, &[]).unwrap();
        assert_eq!((text.as_str(), cost), ("hi", None));
        let s = seen.join().unwrap();
        assert_eq!(s.url, "/v1/chat/completions");
        assert_eq!(header(&s, "Authorization"), Some("Bearer sk-test"));
        assert_eq!(s.body["model"], "x-ai/grok-4");
        let contents: Vec<&str> = s.body["messages"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["content"].as_str().unwrap())
            .collect();
        assert_eq!(contents, ["lens", "prompt here"]);
        assert_eq!(s.body["usage"], json!({"include": true}));
        assert_eq!(s.body["reasoning"], json!({"effort": "medium"}));
    }

    #[test]
    fn local_backend_sends_no_key_cost_or_reasoning() {
        let (base, seen) = serve(200, r#"{"choices": [{"message": {"content": "ok"}}]}"#);
        let _g = setup("local", &base, None, "low");
        assert_eq!(ask("p", "local:qwen3", "", "", 30, &[]).unwrap().1, None);
        let s = seen.join().unwrap();
        assert!(header(&s, "Authorization").is_none());
        assert!(s.body.get("usage").is_none() && s.body.get("reasoning").is_none());
        assert_eq!(s.body["messages"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn effort_becomes_the_reasoning_budget() {
        for (effort, want) in [
            ("typo", Some("high")),
            ("max", Some("high")),
            ("low", Some("low")),
            ("", None),
        ] {
            let (base, seen) = serve(200, r#"{"choices": [{"message": {"content": "hi"}}]}"#);
            let _g = setup("openrouter", &base, None, effort);
            ask("p", "openrouter:m", "", "", 30, &[]).unwrap();
            let s = seen.join().unwrap();
            assert_eq!(
                s.body
                    .get("reasoning")
                    .map(|r| r["effort"].as_str().unwrap().to_string()),
                want.map(String::from)
            );
        }
    }

    #[test]
    fn openrouter_reports_the_cost_it_asked_for() {
        let (base, _seen) = serve(
            200,
            r#"{"choices": [{"message": {"content": "hi"}}], "usage": {"cost": 0.0123}}"#,
        );
        let _g = setup("openrouter", &base, None, "");
        assert_eq!(
            ask("p", "openrouter:x-ai/grok-4", "", "", 30, &[]).unwrap().1,
            Some(0.0123)
        );
    }

    #[test]
    fn http_error_body_becomes_the_message() {
        let (base, _seen) = serve(401, r#"{"error":{"message":"User not found.","code":401}}"#);
        let _g = setup("openrouter", &base, None, "");
        let e = ask("p", "openrouter:x-ai/grok-4", "", "", 30, &[])
            .unwrap_err()
            .to_string();
        assert_eq!(e, "openrouter 401: User not found.");
    }

    #[test]
    fn error_object_with_200_is_not_read_as_an_answer() {
        let (base, _seen) = serve(200, r#"{"error":{"message":"upstream is down"}}"#);
        let _g = setup("local", &base, None, "");
        let e = ask("p", "local:qwen3", "", "", 30, &[]).unwrap_err().to_string();
        assert_eq!(e, "local: upstream is down");
    }

    #[test]
    fn unreachable_backend_pings_as_a_failure() {
        let _g = setup("local", "http://127.0.0.1:1/v1", None, "");
        let got = ping("local:qwen3");
        assert_eq!(got.len(), 1);
        assert!(!got[0].ok && got[0].name == "could not reach local");
        let (base, _seen) = serve(200, r#"{"choices": [{"message": {"content": "OK"}}]}"#);
        drop(_g);
        let _g = setup("local", &base, None, "");
        let got = ping("local:qwen3");
        assert!(got[0].ok && got[0].name == "local answers" && got[0].detail == "OK");
    }

    #[test]
    fn demo_mode_answers_without_a_backend() {
        let _g = crate::config::test_lock();
        config::update(|c| c.demo = true);
        let got = ask("p", "opus", "", "", 1, &[]);
        config::update(|c| c.demo = false);
        let (text, cost, ms) = got.unwrap();
        assert_eq!(obj(&text).unwrap()["verdict"], "approve");
        assert_eq!((cost, ms), (Some(0.01), 1200));
    }

    #[test]
    fn detail_reads_the_usual_shapes() {
        assert_eq!(
            detail(br#"{"error":{"message":"User not found.","code":401}}"#),
            "User not found."
        );
        assert_eq!(detail(br#"{"error":"plain"}"#), "plain");
        assert_eq!(detail(br#"{"error":{"code":7}}"#), r#"{"code":7}"#);
        assert_eq!(detail(b"  not json  "), "not json");
        assert_eq!(detail(&[b'x'; 300]).len(), 200);
    }

    #[test]
    fn obj_ignores_trailing_prose() {
        assert_eq!(
            obj("here you go:\n{\"verdict\": \"approve\"}\n\nHope that helps! :)").unwrap(),
            json!({"verdict": "approve"})
        );
        assert_eq!(
            obj("{\"a\": {\"b\": 1}} then ```{\"not\": \"mine\"}```").unwrap(),
            json!({"a": {"b": 1}})
        );
        assert!(obj("no object here").is_err());
        assert!(obj("{broken").is_err());
    }

    #[test]
    fn run_kills_a_command_that_outlives_its_timeout() {
        let mut cmd = Command::new("sleep");
        cmd.arg("5").stdout(Stdio::piped()).stderr(Stdio::piped());
        let started = Instant::now();
        assert!(run(cmd, "", Duration::from_millis(200)).is_err());
        assert!(started.elapsed() < Duration::from_secs(3));
        let mut cmd = Command::new("sh");
        cmd.args(["-c", "echo out; echo err >&2; exit 3"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        assert!(run(cmd, "", Duration::from_secs(5))
            .unwrap_err()
            .to_string()
            .contains("err"));
        // the prompt arrives on stdin, whole, past both argv's 128 KB cap and the pipe buffer
        let mut cmd = Command::new("wc");
        cmd.arg("-c").stdout(Stdio::piped()).stderr(Stdio::piped());
        let big = "x".repeat(200_000);
        assert_eq!(run(cmd, &big, Duration::from_secs(5)).unwrap().trim(), "200000");
        // a child that exits without reading: the writer gets EPIPE, run neither hangs nor panics
        let mut cmd = Command::new("sh");
        cmd.args(["-c", "exit 0"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let started = Instant::now();
        assert_eq!(run(cmd, &big, Duration::from_secs(5)).unwrap(), "");
        assert!(started.elapsed() < Duration::from_secs(3));
    }
}
