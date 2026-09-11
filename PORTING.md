# Porting dashy (Python) to Rust: rules for every module

You are porting ONE module (or a small set) of the Python package `dashy/` into the Rust crate at the
repo root. The Python file is the spec: same behaviour, same file formats on disk, same strings the UI
shows, same edge cases. The Python tests under `tests/` show the behaviours that matter; port the
important ones as `#[cfg(test)]` tests in your module.

## Layout

- `Cargo.toml` at the repo root, crate `gitdashy`, lib `src/lib.rs` + bin `src/main.rs`.
- Your module's stub already exists in `src/<name>.rs` with the public signatures other modules call.
  Keep those signatures (names, arguments, return types). You may add functions and private helpers freely.
  If a stub signature cannot express the Python behaviour, keep the stub AND add what you need, then
  leave a `// PORT-NOTE:` comment on the stub explaining. Do not edit other modules' files.
- Shared types are in `src/types.rs` (Pr, LogEntry, Finding, Verdict, Section, Detail, DiffFile, Hunk,
  Line, Mark, Draft, CheckResult). Config is `src/config.rs`: read with `config::get()` (a clone),
  change with `config::update(|c| ...)`. Paths that Python had as module globals are fields on Config
  (memory_dir, log, teams, team, bindings, self_dir, backups, registry, corpus_home, settings, debug_log,
  local_memory, local_log). `config.demo` is true under --demo.
- Dependencies already in Cargo.toml: anyhow, base64, chrono, clap, fs4 (flock), getrandom, include_dir,
  log, regex, serde, serde_json, sha2, simplelog, thiserror, tiny_http, ureq 3 (blocking HTTP, json
  feature), urlencoding; dev: tempfile. Do not add dependencies. Use std for subprocesses (git, claude).
- Logging: use the `log` crate macros (debug!, warn!, error!) where Python used logging.

## Conventions

- Errors: functions that Python documented as "returns '' or an error string" keep that shape (String,
  "" = ok). Functions that raised return `Result` (anyhow::Result unless the stub says otherwise).
  "Never raises" in the Python docstring means the Rust function must not panic and must not return Err.
- Python `None` for a missing value is `Option`. Python dicts with fixed keys are the structs in types.rs;
  dicts with dynamic keys are `HashMap`/`BTreeMap` or `serde_json::Value`.
- Timestamps: Python wrote ISO 8601 with offset, seconds precision (`2026-09-11T19:12:47+00:00`). Write
  the same shape with chrono (`to_rfc3339_opts(SecondsFormat::Secs, false)` gives `+00:00`). Read both
  naive and offset forms, and `Z`.
- JSON on disk (logs, settings, registry, team.json) must stay readable by the Python version and vice
  versa: keep the key names. Use serde with `rename` where the Rust name differs.
- Text on disk (memory files, drafts, bindings) must stay byte-compatible: same line formats.
- Keep the Python comments that explain WHY (the `ponytail:` ones) as Rust comments where the code they
  explain is ported. Drop comments that explain Python mechanics. No em dashes in any new text: use a
  colon or comma.
- Subprocesses: `std::process::Command`. Never put secrets in argv (git gets them via GIT_CONFIG_* env,
  see github::git_auth). Timeouts: spawn and poll with `try_wait` in a loop, kill on expiry.
- File locking: the `fs4` crate (`FileExt::try_lock_exclusive`).
- Tests use `tempfile::tempdir()` and point config at it with `config::update(|c| c.memory_dir = ...)`.
  Tests run in parallel threads in one process and config is global: for tests that need config, set
  ALL the paths you use to the temp dir and prefer testing pure helpers that take paths as arguments.
  Mark tests that need the global config with a shared mutex in your module (`static TEST_LOCK: Mutex<()>`).
- Run `cargo test <module>::` and `cargo clippy --all-targets -- -D warnings` for your module before you
  report. `cargo fmt` at the end. Do not leave `todo!()`, `unimplemented!()` or stubs returning defaults
  in your module: everything in the Python file gets ported.

## Report

When done, report in under 200 words: what you ported, which stub signatures you had to change or
supplement (with the PORT-NOTE), which Python behaviours you could not port and why, and the test count.
