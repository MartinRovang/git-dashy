# Necronomicon Spellbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Necronomicon into a spellbook. It holds user-written spells that are cast once on a PR, the built-in passives (hunters) and the built-in voices. Equipped spells can be cast from the sidebar and the right-click menu.

**Architecture:** Each spell is a `.md` file in `~/.prs_spells/`. Casting a spell is a normal review: the server wraps the spell in a cast frame and sends it through the existing `ask` path (`state.start_review(&pr, Ran::Manual, &ask)`), which already carries trusted, private, leak-checked instructions for one review. Equipped spells are a `spells` setting, handled like `hunter`. The old memory-points book (`necro.rs`, Learn, the footer countdown) is removed.

**Tech Stack:** Rust (tauri backend, `serde_json`, `tempfile` in tests), React 19 + TypeScript + Vite, Lucide icons, `mock/api.ts` dev mock.

**Spec:** `docs/superpowers/specs/2026-09-17-necronomicon-spellbook-design.md`

## Global Constraints

- Branch `feat/spellbook`, cut from `main`. PR #141 (`feat/rail-icons`) is still open and rewrites `Sidebar.tsx`'s `Group`. Task 6 is written against `main`'s `Group` (`k`/`digest` props). Whichever of the two merges second rebases onto the other.
- Spell names match `^[a-z0-9-]{1,40}$`. Nothing else is read, written or deleted.
- A spell's text is at most `spells::TEXT_MAX` (8000) chars. `ASK_MAX` in `web.rs` rises from 8000 to 9000 so the cast frame fits on top of it.
- Every spell, passive and voice shows a Lucide icon. For now it's a placeholder, one per kind (`Glyph` in `Necronomicon.tsx`).
- **Deviation from spec (simpler, same behaviour):** there is no `Inputs.spell` field and no `sections_for` change. The cast frame goes through `ask`, which is already trusted, private, logged by length only and leak-checked by `quotes_instructions`. The spec's "HELLO line names the spell" is dropped: `ask` is private, so the PR must not learn a spell was cast.
- Casting uses the same gate as "Review with instructions" (`R`): only on REVIEW REQUESTED rows that are not busy and not already reviewed.
- Every PR gets a separate `chore: X.Y.Z` commit bumping `src-tauri/Cargo.toml` + the `gitdashy` entry in `src-tauri/Cargo.lock` (minor bump for this feature).
- Commits end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Run: `cd src-tauri && cargo test`, `pnpm test`, `pnpm build`, `pnpm lint`.

## File map

| File | Change |
|---|---|
| `src-tauri/src/spells.rs` | **new**: the spell store, starters, name check, cast frame |
| `src-tauri/src/lib.rs` | `pub mod spells;`, drop `pub mod necro;` |
| `src-tauri/src/necro.rs` | **delete** |
| `src-tauri/src/config.rs` | `spells_dir: PathBuf` path, `spells: Vec<String>` equipped setting |
| `src-tauri/src/review.rs` | `ABOUT` table: one-line description per voice and hunter |
| `src-tauri/src/web.rs` | `/api/spells` GET/POST, `spell` on `/api/review`, `spells` in settings, necro removal |
| `src-tauri/src/memory.rs:2086` | drop the `crate::necro::remind(t);` call (one line) |
| `mock/api.ts` | necro removed, spells added |
| `src/types.ts` | `learn` removed, `spells` setting, `Book` type |
| `src/components/Necronomicon.tsx` | rewritten as the three-chapter book |
| `src/index.css` | Learn/points/whispers styles removed, spell editor styles added |
| `src/App.tsx` | `NextLearn` removed, `cast()` added, Sidebar/ActsMenu wiring |
| `src/components/Sidebar.tsx` | voices/hunters leave Agent, new Necronomicon group |
| `src/components/Acts.tsx` | `Cast a spell ▸` row with a dropdown of equipped spells |

---

### Task 1: The spell store (`spells.rs`)

**Files:**
- Create: `src-tauri/src/spells.rs`
- Modify: `src-tauri/src/lib.rs` (add `pub mod spells;` next to the other mods)
- Modify: `src-tauri/src/config.rs` (the `Config` path field and its default, next to `dbrepo` at lines 152–153 and 215)

**Interfaces:**
- Produces:
  - `config::Config.spells_dir: PathBuf` (env `PRS_SPELLS`, default `~/.prs_spells`)
  - `spells::name_ok(name: &str) -> bool`
  - `spells::list_in(dir: &Path) -> Vec<(String, String)>`, sorted by name, seeds the starters when `dir` does not exist
  - `spells::save_in(dir: &Path, name: &str, text: &str) -> anyhow::Result<()>`
  - `spells::delete_in(dir: &Path, name: &str) -> anyhow::Result<()>`
  - `spells::get_in(dir: &Path, name: &str) -> Option<String>`
  - `spells::list() / save(name, text) / delete(name) / get(name)`: the same against `config::get().spells_dir`
  - `spells::cast(name: &str, text: &str) -> String`: the `ask` text a cast review runs with

- [ ] **Step 1: Add the config path**

In `config.rs`, under `pub dbrepo: PathBuf,` add:

```rust
    /// One .md per spell, named after it. See spells.rs.
    pub spells_dir: PathBuf,
```

and under `dbrepo: env_path("PRS_DBREPO", ".prs_dbrepo"),` add:

```rust
            spells_dir: env_path("PRS_SPELLS", ".prs_spells"),
```

- [ ] **Step 2: Write the failing tests** at the bottom of the new `src-tauri/src/spells.rs`

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_stay_in_the_folder() {
        assert!(name_ok("migration-audit"));
        assert!(name_ok("a1"));
        for bad in ["", "../x", "A", "a b", "a/b", "a.md", &"a".repeat(41)] {
            assert!(!name_ok(bad), "{bad:?}");
        }
    }

    #[test]
    fn a_missing_folder_gets_starters_and_an_empty_one_does_not() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("spells");
        let names: Vec<String> = list_in(&dir).into_iter().map(|(n, _)| n).collect();
        assert_eq!(names, STARTERS.iter().map(|(n, _)| n.to_string()).collect::<Vec<_>>());
        for (n, _) in STARTERS {
            delete_in(&dir, n).unwrap();
        }
        assert!(list_in(&dir).is_empty(), "deleted starters stay deleted");
    }

    #[test]
    fn save_get_delete() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().to_path_buf(); // exists: no starters
        save_in(&dir, "zeta", "look at z").unwrap();
        save_in(&dir, "alpha", "look at a").unwrap();
        assert_eq!(list_in(&dir), vec![("alpha".into(), "look at a".into()), ("zeta".into(), "look at z".into())]);
        assert_eq!(get_in(&dir, "zeta").as_deref(), Some("look at z"));
        assert!(save_in(&dir, "../evil", "x").is_err());
        assert!(save_in(&dir, "long", &"x".repeat(TEXT_MAX + 1)).is_err());
        delete_in(&dir, "zeta").unwrap();
        assert_eq!(get_in(&dir, "zeta"), None);
        assert!(delete_in(&dir, "zeta").is_err(), "deleting nothing says so");
    }

    #[test]
    fn cast_frames_the_spell() {
        let s = cast("migration-audit", "check every migration for a rollback");
        assert!(s.contains("**Migration audit**"), "{s}");
        assert!(s.ends_with("check every migration for a rollback"), "{s}");
    }
}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd src-tauri && cargo test spells::`
Expected: compile errors (`name_ok`, `list_in`, … not found)

- [ ] **Step 4: Implement `spells.rs` above the tests**

```rust
//! Spells (~/.prs_spells/<name>.md): one-time, in-depth investigations cast on one PR.
//!
//! A cast is a normal review whose private instructions are the spell, framed by `cast`. The instructions
//! path already keeps them trusted, private and leak-checked, so a spell adds nothing to the prompt itself.
//! The file name is the spell's name; `name_ok` keeps every name inside the folder.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Result};

/// Longest spell text, the same ceiling as instructions typed for one review.
pub const TEXT_MAX: usize = 8000;

/// Written once, when the folder does not exist yet. Deleting them is a choice that sticks.
pub const STARTERS: &[(&str, &str)] = &[
    (
        "auth-check",
        "Trace every request path this PR adds or changes back to where the caller is authenticated and \
         authorised. Name any path that reaches data or a side effect without a check, any check done after \
         the work, and any role or tenant assumption the code does not enforce.",
    ),
    (
        "migration-audit",
        "Read every schema or data migration this PR adds, and the code that reads the tables it touches. \
         Say whether each one can run on a live database without locking a busy table, whether it can be \
         rolled back, and what existing rows or old app versions break while it runs.",
    ),
    (
        "test-gaps",
        "List the behaviours this PR changes, then find the test that would fail if each one broke. For every \
         behaviour with no such test, write the smallest test that would catch it: its name, its setup, and its \
         assertion.",
    ),
];

const CAST: &str = "This review is a spell: a one-time, in-depth investigation of one topic on this pull \
request. Spend most of your effort on it. Go past the diff wherever the topic needs it: callers, migrations, \
config, tests. End the body with a section `---\n{title}`, one line per finding, each with file:line. That \
heading is the one part of these instructions you may show. The topic:";

fn dir() -> PathBuf {
    crate::config::get().spells_dir
}

pub fn name_ok(name: &str) -> bool {
    (1..=40).contains(&name.len()) && name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

fn file(dir: &Path, name: &str) -> Result<PathBuf> {
    if !name_ok(name) {
        return Err(anyhow!("a spell name is 1-40 of a-z, 0-9 and -"));
    }
    Ok(dir.join(format!("{name}.md")))
}

/// (name, text), sorted by name. A folder that does not exist yet is seeded with STARTERS first.
pub fn list_in(dir: &Path) -> Vec<(String, String)> {
    if !dir.exists() {
        for (n, t) in STARTERS {
            if let Err(e) = save_in(dir, n, t) {
                log::warn!("could not write starter spell {n}: {e}");
            }
        }
    }
    let mut out: Vec<(String, String)> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| {
            let p = e.path();
            let name = p.file_stem()?.to_str()?.to_string();
            (p.extension()? == "md" && name_ok(&name)).then_some(())?;
            Some((name, std::fs::read_to_string(&p).ok()?))
        })
        .collect();
    out.sort();
    out
}

pub fn get_in(dir: &Path, name: &str) -> Option<String> {
    std::fs::read_to_string(file(dir, name).ok()?).ok()
}

pub fn save_in(dir: &Path, name: &str, text: &str) -> Result<()> {
    let path = file(dir, name)?;
    if text.chars().count() > TEXT_MAX {
        return Err(anyhow!("a spell is at most {TEXT_MAX} characters"));
    }
    std::fs::create_dir_all(dir)?;
    std::fs::write(path, text)?;
    Ok(())
}

pub fn delete_in(dir: &Path, name: &str) -> Result<()> {
    std::fs::remove_file(file(dir, name)?).map_err(|e| anyhow!("no spell {name}: {e}"))
}

pub fn list() -> Vec<(String, String)> {
    list_in(&dir())
}
pub fn get(name: &str) -> Option<String> {
    get_in(&dir(), name)
}
pub fn save(name: &str, text: &str) -> Result<()> {
    save_in(&dir(), name, text)
}
pub fn delete(name: &str) -> Result<()> {
    delete_in(&dir(), name)
}

/// The instructions a cast review runs with: the frame, then the spell. `migration-audit` -> `**Migration audit**`.
pub fn cast(name: &str, text: &str) -> String {
    let words = name.replace('-', " ");
    let mut c = words.chars();
    let title = c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default();
    format!("{}\n\n{}", CAST.replace("{title}", &format!("**{title}**")), text.trim())
}
```

Add `pub mod spells;` to `src-tauri/src/lib.rs` (keep the alphabetical order of the `pub mod` list).

If the crate has no `log` macro import at module level, use `log::warn!` the way `review.rs` does, fully qualified as written above.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src-tauri && cargo test spells::`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src/spells.rs src-tauri/src/lib.rs src-tauri/src/config.rs
git commit -m "feat: spells live as .md files in ~/.prs_spells, with three starters and a cast frame"
```

---

### Task 2: Equipped spells as a setting

**Files:**
- Modify: `src-tauri/src/config.rs`: `Config` (next to `hunter`, line ~103), its default (line ~187), `Saved` (line ~269), `apply` (line ~446), `snapshot` (line ~492), tests (line ~591)
- Modify: `src-tauri/src/web.rs`: `post_settings` (after the voice/hunter loop, line ~1935)

**Interfaces:**
- Consumes: `spells::get(name) -> Option<String>`
- Produces: `config::Config.spells: Vec<String>`, `Saved.spells: Option<Vec<String>>`, and `settings.spells` in `/api/state`. `POST /api/settings {spells: [names]}` refuses names with no file.

- [ ] **Step 1: Write the failing test** in `config.rs`'s test module, next to the round-trip test that contains `"hunter":["security"]` (line ~591)

```rust
    #[test]
    fn equipped_spells_round_trip() {
        let s: Saved = serde_json::from_str(r#"{"spells":["auth-check","test-gaps"]}"#).unwrap();
        assert_eq!(s.spells, Some(vec!["auth-check".to_string(), "test-gaps".to_string()]));
        let mut c = Config::default();
        c.spells = vec!["auth-check".into()];
        assert_eq!(snapshot(&c).spells, Some(vec!["auth-check".to_string()]));
    }
```

If `Config::default()` is not how the neighbouring tests build a `Config`, copy the construction the `hunter` round-trip test uses.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src-tauri && cargo test config::tests::equipped_spells_round_trip`
Expected: compile error, no field `spells`

- [ ] **Step 3: Implement**

`Config`, under `pub hunter: Vec<String>,`:

```rust
    /// Spells on the quick list: the sidebar and the right-click menu. Names of files in spells_dir.
    pub spells: Vec<String>,
```

Default, under `hunter: split("PRS_HUNTER"),`:

```rust
            spells: Vec::new(),
```

`Saved`, under the `hunter` field (same attributes):

```rust
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "one_or_many"
    )]
    pub spells: Option<Vec<String>>,
```

`apply`, under the `saved.hunter` block:

```rust
    if let Some(v) = saved.spells {
        c.spells = v;
    }
```

`snapshot`, under `hunter: Some(c.hunter.clone()),`:

```rust
        spells: Some(c.spells.clone()),
```

`web.rs` `post_settings`, right after the `for (key, options) in [("voice", …), ("hunter", …)]` loop closes:

```rust
    if let Some(v) = body.get("spells") {
        let Some(names) = v.as_array().and_then(|a| a.iter().map(|x| x.as_str().map(String::from)).collect::<Option<Vec<_>>>()) else {
            return Err(Fail::new(400, "spells must be a list of names"));
        };
        if let Some(bad) = names.iter().find(|n| spells::get(n).is_none()) {
            return Err(Fail(400, format!("no spell {bad}")));
        }
        c.spells = names;
    }
```

Add `spells` to the `use crate::{…}` list at the top of `web.rs` (line 22).

- [ ] **Step 4: Run the tests**

Run: `cd src-tauri && cargo test`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/config.rs src-tauri/src/web.rs
git commit -m "feat: equipped spells are a setting, and only spells that exist can be equipped"
```

---

### Task 3: `/api/spells`, casting through `/api/review`, and removing necro

**Files:**
- Modify: `src-tauri/src/review.rs`: add an `ABOUT` table after `HUNTER` (line ~135)
- Modify: `src-tauri/src/web.rs`: new `get_spells`/`post_spells`, the `post_review` spell branch, the routes (lines ~2049, ~2087), removal of `get_necronomicon`/`post_necronomicon`/`start_learn` (lines 739–773) and of `"learn"` in state (line 212), and `necro` removed from the `use` list
- Modify: `src-tauri/src/memory.rs:2086`: delete the line `crate::necro::remind(t); // …` (keep the `continue;` below it)
- Modify: `src-tauri/src/lib.rs`: delete `pub mod necro;`
- Delete: `src-tauri/src/necro.rs`

**Interfaces:**
- Consumes: `spells::{list, get, save, delete, cast, name_ok}`, `review::{VOICE, HUNTER}`, `config::{VOICES, HUNTERS}`
- Produces:
  - `GET /api/spells` returns `{spells:[{name,text,on}], passives:[{name,about,prompt,on}], voices:[{name,about,prompt,on}]}`
  - `POST /api/spells {op:"save"|"delete", name, text?}` returns `{ok:true}`. Deleting also unequips.
  - `POST /api/review {url, spell}` runs a review with `ask = spells::cast(name, text)`. An unknown spell gets a 400 `no spell <name>`, and sending both `spell` and `ask` gets a 400.
  - `review::ABOUT: &[(&str, &str)]`

- [ ] **Step 1: Write the failing test** in `review.rs`'s test module

```rust
    #[test]
    fn every_voice_and_hunter_has_an_about() {
        for name in config::VOICES.iter().chain(config::HUNTERS) {
            assert!(table(ABOUT, name).is_some_and(|a| !a.is_empty()), "{name}");
        }
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src-tauri && cargo test every_voice_and_hunter_has_an_about`
Expected: compile error, `ABOUT` not found

- [ ] **Step 3: Add `ABOUT`** after the `HUNTER` table in `review.rs`

```rust
/// One line for the Necronomicon on what each voice and hunter does to a review.
pub const ABOUT: &[(&str, &str)] = &[
    ("review", "The plain review: a summary, the findings and a verdict."),
    ("caveman", "Adds the verdict again in caveman speech, ten lines at most."),
    ("bot", "Adds the findings as a terse machine log, one line each."),
    ("ponytail", "Hunts over-engineering: what to delete, and the stdlib or native thing that replaces it."),
    ("security", "Hunts security: trust boundaries, injection, authz, secrets, SSRF, path traversal."),
    ("tests", "Hunts test coverage: changed logic nothing tests, and tests that cannot fail."),
    ("perf", "Hunts runtime cost the change adds, said with how often the code runs."),
    ("humanizer", "Hunts AI-sounding prose the PR adds to strings, docs and comments."),
];
```

Make `fn table` `pub(crate)` only if `web.rs` needs it; `web.rs` below does its own lookup, so it can stay private.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd src-tauri && cargo test every_voice_and_hunter_has_an_about`
Expected: PASS

- [ ] **Step 5: Add the endpoints** in `web.rs`, replacing the removed necro handlers (lines 739–773)

```rust
/// The book: the spells on disk, and the built-in passives and voices with what they add to a review.
fn get_spells(_state: &State, _q: &Query) -> Out {
    let c = config::get();
    let about = |n: &str| review::ABOUT.iter().find(|(k, _)| *k == n).map(|(_, v)| *v).unwrap_or("");
    let built = |table: &[(&str, &str)], names: &[&str], on: &[String]| -> Vec<Value> {
        names
            .iter()
            .map(|n| {
                let prompt = table.iter().find(|(k, _)| k == n).map(|(_, v)| v.trim()).unwrap_or("");
                json!({"name": n, "about": about(n), "prompt": prompt, "on": on.iter().any(|x| x == n)})
            })
            .collect()
    };
    let spells: Vec<Value> = spells::list()
        .into_iter()
        .map(|(name, text)| {
            let on = c.spells.contains(&name);
            json!({"name": name, "text": text, "on": on})
        })
        .collect();
    Ok(json!({
        "spells": spells,
        "passives": built(review::HUNTER, config::HUNTERS, &c.hunter),
        "voices": built(review::VOICE, config::VOICES, &c.voice),
    }))
}

/// `save` writes one spell, `delete` removes it and takes it off the quick list.
fn post_spells(_state: &State, body: &Body) -> Out {
    let name = text(body, "name");
    match text(body, "op").as_str() {
        "save" => spells::save(&name, &text(body, "text")).map_err(|e| Fail(400, e.to_string()))?,
        "delete" => {
            spells::delete(&name).map_err(|e| Fail(404, e.to_string()))?;
            let _held = config::SAVING.lock().unwrap_or_else(|e| e.into_inner());
            let mut c = config::get();
            if c.spells.contains(&name) {
                c.spells.retain(|n| n != &name);
                config::normalise(&mut c);
                let saved = config::snapshot(&c);
                config::update(|cfg| *cfg = c);
                config::save(&saved)?;
            }
        }
        _ => return Err(Fail::new(400, "op must be save or delete")),
    }
    Ok(json!({"ok": true}))
}
```

These are the same four lines `post_settings` ends with (`web.rs` ~2024–2027).

Routes: in the GET match (line ~2049), replace `"/api/necronomicon" => get_necronomicon,` with `"/api/spells" => get_spells,`. In the POST match (line ~2087), replace `"/api/necronomicon" => post_necronomicon,` with `"/api/spells" => post_spells,`.

State (line ~212): delete the `"learn": {…},` line.

`post_review`, replace `let ask = text(body, "ask");` with:

```rust
    let spell = text(body, "spell");
    let mut ask = text(body, "ask");
    if !spell.is_empty() {
        if !ask.is_empty() {
            return Err(Fail::new(400, "a spell or instructions, not both"));
        }
        let Some(t) = spells::get(&spell) else {
            return Err(Fail(400, format!("no spell {spell}")));
        };
        ask = spells::cast(&spell, &t);
    }
```

The existing `ASK_MAX` check below then covers the cast too. The frame is ~400 chars, so a spell at `TEXT_MAX` can go past `ASK_MAX`. Raise `ASK_MAX` in `web.rs` to `9000` so the whole stored range casts.

- [ ] **Step 6: Remove necro**

```bash
git rm src-tauri/src/necro.rs
```

Delete `pub mod necro;` from `lib.rs`, `necro,` from the `use crate::{…}` list in `web.rs`, and the `crate::necro::remind(t);` line in `memory.rs` (keep `continue;`, and move its comment `// already approved somewhere: …` onto the `continue;` line if it isn't there already).

Then:

Run: `cd src-tauri && grep -rn necro src/ ; cargo test`
Expected: no grep output, all tests pass

- [ ] **Step 7: Commit**

```bash
git add -A src-tauri
git commit -m "feat: /api/spells lists and writes spells, /api/review casts one, and the memory-points book is gone"
```

---

### Task 4: The dev mock

**Files:**
- Modify: `mock/api.ts`: the `necro` state (lines 94–108), `learnAt` (131–132), `learn` in state (359), `learnJob` (381–389), GET `/api/necronomicon` (608–611), POST `/api/necronomicon` (742–752), `postSettings` key list (559), `postReview` (477)

**Interfaces:**
- Produces: the same `/api/spells` GET/POST shapes as Task 3. `postReview` accepts `spell`.

- [ ] **Step 1: Replace the necro state** (lines 94–108) with:

```ts
  spells: [
    { name: 'auth-check', text: 'Trace every request path this PR adds back to where the caller is authenticated and authorised.' },
    { name: 'migration-audit', text: 'Read every migration this PR adds; say whether it locks a busy table and whether it rolls back.' },
    { name: 'test-gaps', text: 'List the behaviours this PR changes and the test that would fail if each broke.' },
  ] as { name: string; text: string }[],
```

Add `spells: ['migration-audit'],` to `S.settings`, and `'spells'` to the key list in `postSettings`.

- [ ] **Step 2: Delete** `learnAt`, the `learn:` line in the state JSON, `learnJob()`, and both `/api/necronomicon` branches.

- [ ] **Step 3: Add the GET branch** where the old GET branch was

```ts
    if (path === '/api/spells') {
      const on = (list: unknown, n: string) => ((list as string[]) || []).includes(n)
      const about: Record<string, string> = {
        review: 'The plain review: a summary, the findings and a verdict.',
        caveman: 'Adds the verdict again in caveman speech.',
        bot: 'Adds the findings as a terse machine log.',
        ponytail: 'Hunts over-engineering.',
        security: 'Hunts security.',
        tests: 'Hunts test coverage.',
        perf: 'Hunts runtime cost.',
        humanizer: 'Hunts AI-sounding prose.',
      }
      const built = (names: string[], setting: string) =>
        names.map((n) => ({ name: n, about: about[n] || '', prompt: `Append a section **${n}** … (mock prompt)`, on: on(S.settings[setting], n) }))
      return json(200, {
        spells: S.spells.map((s) => ({ ...s, on: on(S.settings.spells, s.name) })),
        passives: built(HUNTERS, 'hunter'),
        voices: built(VOICES, 'voice'),
      })
    }
```

- [ ] **Step 4: Add the POST branch** where the old POST branch was

```ts
    if (path === '/api/spells') {
      const name = str(body, 'name')
      if (!/^[a-z0-9-]{1,40}$/.test(name)) return json(400, { error: 'a spell name is 1-40 of a-z, 0-9 and -' })
      const i = S.spells.findIndex((s) => s.name === name)
      if (str(body, 'op') === 'save') {
        if (i >= 0) S.spells[i].text = str(body, 'text')
        else S.spells.push({ name, text: str(body, 'text') })
        S.spells.sort((a, b) => a.name.localeCompare(b.name))
      } else {
        if (i < 0) return json(404, { error: `no spell ${name}` })
        S.spells.splice(i, 1)
        S.settings.spells = ((S.settings.spells as string[]) || []).filter((n) => n !== name)
      }
      return json(200, { ok: true })
    }
```

- [ ] **Step 5: `postReview`**: after the `ask` length check, add

```ts
  const spell = str(b, 'spell')
  if (spell && !S.spells.some((s) => s.name === spell)) return json(400, { error: `no spell ${spell}` })
```

- [ ] **Step 6: Verify and commit**

Run: `pnpm build && grep -n "necro\|learn" mock/api.ts`
Expected: build ok. Grep shows only unrelated words (e.g. "learnt").

```bash
git add mock/api.ts
git commit -m "chore: the dev mock serves spells instead of the memory-points book"
```

---

### Task 5: The book

**Files:**
- Modify: `src/types.ts`: delete `learn?` (lines 61–62), add `spells?: string[]` next to `hunter?` (line ~124) and `spells: string[]` next to `hunter` (line ~51) if that is the settings type
- Rewrite: `src/components/Necronomicon.tsx`
- Modify: `package.json`, `pnpm-lock.yaml`: `pnpm add lucide-react` (skip if #141 already brought it in)
- Modify: `src/index.css`: delete lines 334–337 (`.nnext*`), 341–357 (`.nlearn-btn*`, `nglow`/`nhop`/`nsweep`, `.nskull`), 365–368 (`.necro.learning`), 379–392 (`.npoint*`, `.nmeta*`, `.nnote`, `.nwhispers`, `.nwho`), 398 (`.nindex-whispers`), 414–415 (`.ndig`). Add the styles in Step 3.
- Modify: `src/App.tsx`: delete the `NextLearn` line (~903), change the import to `import { Necronomicon } from './components/Necronomicon'`, and render `<Necronomicon selected={current} onCast={cast} setting={setting} />` (the `cast` function lands in Step 4)

**Interfaces:**
- Consumes: `GET/POST /api/spells`, `POST /api/settings` (through App's existing `setting(name, value)`)
- Produces:
  - `export type Book = { spells: { name: string; text: string; on: boolean }[]; passives: Built[]; voices: Built[] }`, where `Built = { name: string; about: string; prompt: string; on: boolean }`, exported from `Necronomicon.tsx`
  - `export function useBook(): [Book | null, () => Promise<void>]`: loads `/api/spells` and returns a reload function. Tasks 6 and 7 use it.
  - `Necronomicon({ selected, onCast, setting })`
  - `export const ICON: Record<'spell' | 'passive' | 'voice', LucideIcon>` and `export function Glyph({ kind, name }: { kind: keyof typeof ICON; name: string })`: the icon for one spell, passive or voice. Placeholder: one icon per kind for now
  - App's `cast(p: Row, spell: string): Promise<void>`

- [ ] **Step 0: Add Lucide**

Run: `pnpm add lucide-react` (skip it if `package.json` already lists it)

- [ ] **Step 1: Rewrite `Necronomicon.tsx`**

```tsx
// The Necronomicon: the reviewer's abilities as a book. Spells are cast once on a PR, passives (hunters) and voices
// ride along on every review. Spells are yours to write; passives and voices are built in and only switched.
//
// ponytail: everything the pages say about passives and voices comes from /api/spells. This page holds no prompt text.
import { useCallback, useEffect, useState } from 'react'
import { api, errorText, post } from '../api'
import { modalCount } from '../modals'
import type { Row } from '../types'
import { MessageSquare, Crosshair, Sparkles, type LucideIcon } from 'lucide-react'

// ponytail: placeholder icons, one per kind. Give each spell, passive or voice its own once there are drawings for them;
// `name` is already passed so only this function changes.
export const ICON: Record<'spell' | 'passive' | 'voice', LucideIcon> = { spell: Sparkles, passive: Crosshair, voice: MessageSquare }
export function Glyph({ kind, name }: { kind: keyof typeof ICON; name: string }) {
  const I = ICON[kind]
  return <I className="nglyph" size={14} aria-hidden data-name={name} />
}

type Built = { name: string; about: string; prompt: string; on: boolean }
export type Book = { spells: { name: string; text: string; on: boolean }[]; passives: Built[]; voices: Built[] }

const CHAPTERS = ['Spells', 'Passives', 'Voices'] as const

/** The book's contents, and a reload. The sidebar and the right-click menu read the same. */
export function useBook(): [Book | null, () => Promise<void>] {
  const [book, setBook] = useState<Book | null>(null)
  const load = useCallback(async () => {
    const r = await api('/api/spells')
    if (r.ok) setBook(await r.json())
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  return [book, load]
}

export function Necronomicon({
  selected,
  onCast,
  setting,
}: {
  selected: Row | null
  onCast: (p: Row, spell: string) => void
  setting: (name: string, value: unknown) => void
}) {
  const [book, reload] = useBook()
  const [ch, setCh] = useState(0)
  // the spell open on the right page; '' is a new one
  const [pick, setPick] = useState<string | null>(null)
  const [draft, setDraft] = useState({ name: '', text: '' })
  const [error, setError] = useState('')

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (modalCount() > 0 || /input|textarea|select/i.test((e.target as HTMLElement).tagName)) return
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      e.preventDefault()
      setCh((c) => Math.max(0, Math.min(CHAPTERS.length - 1, c + (e.key === 'ArrowRight' ? 1 : -1))))
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [])

  if (!book) return <div className="necro"><div className="ncover-msg">opening the book…</div></div>

  const open = (name: string) => {
    const s = book.spells.find((x) => x.name === name)
    setPick(name)
    setDraft({ name, text: s?.text || '' })
    setError('')
  }
  const act = async (body: Record<string, string>) => {
    const r = await post('/api/spells', body)
    if (!r.ok) {
      setError(await errorText(r))
      return false
    }
    setError('')
    await reload()
    return true
  }
  const save = async () => {
    // a rename writes the new name and removes the old one, so it does not leave two
    if (!(await act({ op: 'save', name: draft.name, text: draft.text }))) return
    if (pick && pick !== draft.name) await act({ op: 'delete', name: pick })
    setPick(draft.name)
  }
  const flip = async (key: 'spells' | 'hunter' | 'voice', list: { name: string; on: boolean }[], name: string) => {
    const on = list.filter((x) => x.on).map((x) => x.name)
    setting(key, on.includes(name) ? on.filter((n) => n !== name) : [...on, name])
    // the setting posts and the state poll picks it up; the book re-reads so its switches follow
    setTimeout(() => void reload(), 300)
  }
  const current = pick === null ? null : book.spells.find((s) => s.name === pick)
  const canCast = !!selected && selected.section === 'REVIEW REQUESTED' && !selected.busy

  const built = (list: Built[], key: 'hunter' | 'voice') => (
    <ul className="nbuilt">
      {list.map((b) => (
        <li key={b.name}>
          <div className="nbuilt-h">
            <Glyph kind={key === 'hunter' ? 'passive' : 'voice'} name={b.name} />
            <b>{b.name}</b>
            <button className="fld" aria-pressed={b.on} onClick={() => void flip(key, list, b.name)}>
              <span>{b.on ? 'equipped' : 'unequipped'}</span>
              <span className="sw" />
            </button>
          </div>
          <p>{b.about}</p>
          {b.prompt ? <pre className="nprompt">{b.prompt}</pre> : null}
        </li>
      ))}
    </ul>
  )

  return (
    <div className="necro scroll">
      <div className="ncover">
        <div className="nhead">
          <span className="ntitle">Necronomicon</span>
          <span className="nsub">{error ? `✗ ${error}` : selected ? `open on #${selected.number} ${selected.repo}` : 'no PR selected'}</span>
        </div>
        <div className="nspread">
          <div className="npage left">
            <h2>Chapters</h2>
            <ol className="nindex">
              {CHAPTERS.map((c, i) => (
                <li key={c} className={i === ch ? 'on' : ''}>
                  <button onClick={() => setCh(i)}>{c}</button>
                  <span className="nleader" />
                  <span>{[book.spells, book.passives, book.voices][i].length}</span>
                </li>
              ))}
            </ol>
            {ch === 0 ? (
              <>
                <h3>Spells</h3>
                <ol className="nindex">
                  {book.spells.map((s) => (
                    <li key={s.name} className={s.name === pick ? 'on' : ''}>
                      <button onClick={() => open(s.name)}>
                        <Glyph kind="spell" name={s.name} /> {s.name}
                      </button>
                      <span className="nleader" />
                      <span>{s.on ? '✦' : ''}</span>
                    </li>
                  ))}
                  <li className={pick === '' ? 'on' : ''}>
                    <button onClick={() => open('')}>+ new spell</button>
                  </li>
                </ol>
              </>
            ) : null}
          </div>
          <div className="npage right">
            {ch === 0 ? (
              pick === null ? (
                <p className="nblank">Pick a spell, or write a new one. A spell is a one-time, in-depth look at one topic, cast on one PR.</p>
              ) : (
                <section className="nspell">
                  <h2>
                    <Glyph kind="spell" name={pick || 'new'} /> {pick || 'A new spell'}
                  </h2>
                  <label>
                    name
                    <input value={draft.name} placeholder="migration-audit" onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                  </label>
                  <label>
                    what it investigates
                    <textarea rows={10} value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
                  </label>
                  <div className="nspell-acts">
                    <button className="btn" onClick={() => void save()}>save</button>
                    {current ? (
                      <>
                        <button className="fld" aria-pressed={current.on} onClick={() => void flip('spells', book.spells, current.name)}>
                          <span>{current.on ? 'equipped' : 'unequipped'}</span>
                          <span className="sw" />
                        </button>
                        <button
                          className="btn go"
                          disabled={!canCast}
                          title={canCast ? `cast on #${selected!.number}` : 'select a PR waiting for your review'}
                          onClick={() => selected && onCast(selected, current.name)}
                        >
                          cast on {selected ? `#${selected.number}` : 'a PR'}
                        </button>
                        <button
                          className="btn"
                          onClick={async () => {
                            if (await act({ op: 'delete', name: current.name })) setPick(null)
                          }}
                        >
                          delete
                        </button>
                      </>
                    ) : null}
                  </div>
                </section>
              )
            ) : ch === 1 ? (
              <section>
                <h2>Passives</h2>
                {built(book.passives, 'hunter')}
              </section>
            ) : (
              <section>
                <h2>Voices</h2>
                {built(book.voices, 'voice')}
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
```

The voice switch can refuse to turn off the last voice (server 400 "at least one voice stays on"). App's `setting()` already toasts server errors, so nothing extra is needed. Check that it does: read `setting` in `App.tsx`. If it swallows errors, surface them through `setFlash` the way `call` does.

- [ ] **Step 2: Type check**

Run: `pnpm build`
Expected: errors only in `App.tsx` (the `NextLearn` import, `cast` not defined yet). Fix them in Step 4.

- [ ] **Step 3: CSS.** Delete the rules listed under **Files**, then add after `.nleader`:

```css
  .nglyph { flex: none; vertical-align: -2px; color: var(--gold); }
  .nbuilt { margin: 0; padding: 0; list-style: none; }
  .nbuilt li { margin: 0 0 18px; }
  .nbuilt-h { display: flex; align-items: center; gap: 10px; }
  .nbuilt-h b { font-size: 16px; font-variant: small-caps; letter-spacing: .05em; color: var(--blood); }
  .nbuilt-h .fld { width: auto; margin: 0 0 0 auto; }
  .nbuilt p { margin: 4px 0; }
  .nprompt { margin: 0; white-space: pre-wrap; font-family: var(--mono); font-size: 11px; color: var(--dim); }
  .nspell label { display: flex; flex-direction: column; gap: 4px; margin: 0 0 12px; font-size: 12px; font-style: italic; color: var(--dim); }
  .nspell input, .nspell textarea { font: inherit; font-style: normal; font-size: 14px; color: var(--ink); resize: vertical;
                                    background: color-mix(in srgb, var(--bg) 70%, transparent);
                                    border: 1px solid color-mix(in srgb, var(--gold) 40%, transparent); border-radius: 4px; padding: 6px 10px; }
  .nspell-acts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .nspell-acts .fld { width: auto; margin: 0; }
```

- [ ] **Step 4: App wiring**

In `App.tsx`, next to `review()` (line ~419), add:

```tsx
  async function cast(p: Row, spell: string) {
    if (!p || p.busy || p.section !== 'REVIEW REQUESTED' || isReviewed(p)) return
    if (!(await confirm(`Cast ${spell} on #${p.number}? It runs a review and posts its verdict.`))) return
    await call('/api/review', { url: p.url, spell }, `${spell} cast on #${p.number}`)
  }
```

Remove the `NextLearn` render line and import. Change `<Necronomicon />` to `<Necronomicon selected={current} onCast={(p, s) => void cast(p, s)} setting={setting} />`, using the name App already gives the settings poster that it passes to `<Sidebar setting={…}>`.

- [ ] **Step 5: Verify**

Run: `pnpm build && pnpm test && pnpm lint`
Expected: build ok, tests pass, no new lint errors

Manual, with `pnpm dev` at `http://localhost:1420/?token=dev`:
- press G until the book shows: three chapters, ← → switch them
- write a spell `foo`, save it, rename it to `bar`, save: only `bar` is listed
- equip it: ✦ shows
- select a REVIEW REQUESTED PR on the board, open the book, cast: a confirm, then a toast, and the row spins
- on Passives, switch `security` on: the Agent chips agree (until Task 6 moves them)

- [ ] **Step 6: Commit**

```bash
git add src/types.ts src/components/Necronomicon.tsx src/index.css src/App.tsx
git commit -m "feat: the Necronomicon is a spellbook: spells to write and cast, passives and voices to equip"
```

---

### Task 6: The sidebar's Necronomicon group

**Files:**
- Modify: `src/components/Sidebar.tsx`: `Props` (add `onCast`, `onBook`, `selected`), the Agent group (remove voice/hunter chips at lines ~424–431 and the `voices`/`hunters` digest `Pills`), a new `Group` after Agent
- Modify: `src/App.tsx`: pass the new props to `<Sidebar>`

**Interfaces:**
- Consumes: `useBook()` and `Book` from Task 5, App's `cast(p, spell)`, `show('necronomicon')`
- Produces: `Sidebar` props `selected: Row | null`, `onCast: (p: Row, spell: string) => void`, `onBook: () => void`

- [ ] **Step 1: Move the chips.** Cut these lines out of the Agent group:

```tsx
          <div className="sub">
            <kbd className="hint">x</kbd> voices <em>active</em>
          </div>
          <Chips values={s.voice || []} options={o.voice} onToggle={(v) => setting('voice', toggle(s.voice || [], v))} />
          <div className="sub">
            <kbd className="hint">h</kbd> hunters <em>active</em>
          </div>
          <Chips values={s.hunter || []} options={o.hunter} onToggle={(v) => setting('hunter', toggle(s.hunter || [], v))} />
```

and remove `<Pills label="voices" … />` and `<Pills label="hunters" … />` from Agent's `digest`.

- [ ] **Step 2: Add the group** after Agent's closing `</Group>`

```tsx
        <Group
          k="necro"
          label="Necronomicon"
          summary={[`${(s.voice || []).length} voice${(s.voice || []).length === 1 ? '' : 's'}`, `${(s.hunter || []).length} passive${(s.hunter || []).length === 1 ? '' : 's'}`, `${equipped.length} spell${equipped.length === 1 ? '' : 's'}`].join(' · ')}
          digest={
            <>
              <Pills label="voices" values={s.voice || []} />
              <Pills label="passives" values={s.hunter || []} />
              <Pills label="spells" values={equipped} />
            </>
          }
          open={!!open.necro}
          onToggle={() => flip('necro')}
          collapsed={collapsed}
        >
          <div className="sub">
            <kbd className="hint">x</kbd> voices <em>active</em>
          </div>
          <Chips values={s.voice || []} options={o.voice} onToggle={(v) => setting('voice', toggle(s.voice || [], v))} />
          <div className="sub">
            <kbd className="hint">h</kbd> passives <em>active</em>
          </div>
          <Chips values={s.hunter || []} options={o.hunter} onToggle={(v) => setting('hunter', toggle(s.hunter || [], v))} />
          <div className="sub">
            spells <em>{selected ? `cast on #${selected.number}` : 'select a PR'}</em>
          </div>
          {equipped.length ? (
            <div className="tags">
              {equipped.map((name) => (
                <button
                  className="tag"
                  key={name}
                  disabled={!selected || selected.section !== 'REVIEW REQUESTED' || selected.busy}
                  title={selected ? `cast ${name} on #${selected.number}` : 'select a PR waiting for your review'}
                  onClick={() => selected && onCast(selected, name)}
                >
                  <Glyph kind="spell" name={name} /> {name}
                </button>
              ))}
            </div>
          ) : (
            <div className="rules none">no spells equipped</div>
          )}
          <button className="btn dbconf" onClick={onBook}>
            open the book
          </button>
        </Group>
```

At the top of `Sidebar`, next to `const s = …`:

```tsx
  // ponytail: the setting names what is equipped, and the book is only read to drop a name whose file was deleted elsewhere
  const [book] = useBook()
  const equipped = (s.spells || []).filter((n) => !book || book.spells.some((x) => x.name === n))
```

Add the imports `import type { Row } from '../types'` (merge with the existing type import) and `import { Glyph, useBook } from './Necronomicon'`.

The voice/passive `Chips` stay text for now: `Chips` renders plain strings, and the icons show in the book. Put icons on chips only if the plain text looks off next to the spell tags. Add `selected`, `onCast` and `onBook` to `Props` and to the destructured parameters.

Note: `useBook` in the sidebar loads once on mount, so a spell deleted in the book still shows here until reload. That is acceptable because the setting is also cleaned server-side on delete, and `s.spells` comes from the 2s state poll.

- [ ] **Step 3: App wiring.** On `<Sidebar …>` add:

```tsx
          selected={current}
          onCast={(p, spell) => void cast(p, spell)}
          onBook={() => show('necronomicon')}
```

- [ ] **Step 4: Verify**

Run: `pnpm build && pnpm lint`
Expected: ok

Manual (dev): the Agent group has no chips. Equipped spell tags show the ✦ placeholder icon. The Necronomicon group toggles voices and passives, lists `migration-audit` (equipped in the mock), casts on a selected REVIEW REQUESTED PR, and "open the book" switches the view.

- [ ] **Step 5: Commit**

```bash
git add src/components/Sidebar.tsx src/App.tsx
git commit -m "feat: a Necronomicon group on the sidebar switches voices and passives and casts equipped spells"
```

---

### Task 7: Cast from the right-click menu

**Files:**
- Modify: `src/components/Acts.tsx`: an `ActsMenu` prop `spells: string[]` and `onCast`, plus a `Cast a spell ▸` row that opens an inline dropdown
- Modify: `src/App.tsx:925`: pass `spells` and `onCast`
- Test: `src/components/Acts.test.ts`

**Interfaces:**
- Consumes: `settings.spells` from the state poll, App's `cast(p, spell)`
- Consumes: `Glyph` from `./Necronomicon`
- Produces: `export function canCast(p: Row): boolean` in `Acts.tsx`

- [ ] **Step 1: Write the failing test** in `src/components/Acts.test.ts` (build rows the way the existing tests in that file do)

```ts
import { canCast } from './Acts'

test('a spell casts only where a review with instructions could start', () => {
  const rr = row({ section: 'REVIEW REQUESTED' })
  expect(canCast(rr)).toBe(true)
  expect(canCast({ ...rr, busy: true })).toBe(false)
  expect(canCast(row({ section: 'MINE' }))).toBe(false)
})
```

`row(...)` is the row factory already used in `Acts.test.ts`. If the file names it differently, use that name. If a reviewed row fixture exists there, add `expect(canCast(reviewedRow)).toBe(false)`.

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm vitest run src/components/Acts.test.ts`
Expected: FAIL, `canCast` is not exported

- [ ] **Step 3: Implement.** In `Acts.tsx`, above `acts`:

```ts
/** The same gate as `R`: a spell is a review, so it starts where a review with instructions could. */
export function canCast(p: Row): boolean {
  return p.section === 'REVIEW REQUESTED' && !p.busy && !isReviewed(p)
}
```

`ActsMenu`: add props `spells: string[]` and `onCast: (target: Row, spell: string) => void`, plus `const [casting, setCasting] = useState(false)`. After the `acts(...).map(...)` block:

```tsx
      {spells.length ? (
        <>
          <button
            role="menuitem"
            aria-expanded={casting}
            className="ai"
            disabled={!canCast(p)}
            onClick={() => setCasting((v) => !v)}
          >
            <kbd className="hint">✦</kbd>
            <b>Cast a spell {casting ? '▾' : '▸'}</b>
            <em>equipped</em>
          </button>
          {casting
            ? spells.map((name) => (
                <button
                  key={`cast:${name}`}
                  role="menuitem"
                  className="ai sub"
                  onClick={() => {
                    onClose()
                    onCast(p, name)
                  }}
                >
                  <kbd className="hint" />
                  <b>
                    <Glyph kind="spell" name={name} /> {name}
                  </b>
                  <em />
                </button>
              ))
            : null}
          <button
            role="menuitem"
            className="ai"
            onClick={() => {
              onClose()
              onAct('book', p)
            }}
          >
            <kbd className="hint" />
            <b>Open the book…</b>
            <em />
          </button>
        </>
      ) : null}
```

When `casting` flips, the menu grows, so re-run the clamp: change `useLayoutEffect(clamp, [clamp])` to `useLayoutEffect(clamp, [clamp, casting])`.

Add to `index.css` next to the `.acts .ai` rules:

```css
  .acts .ai.sub b { padding-left: 14px; font-weight: 400; }
```

App (line ~925), on `<ActsMenu …>`:

```tsx
          spells={data?.settings.spells || []}
          onCast={(p, spell) => void cast(p, spell)}
```

and in `doAct`'s `fns` add `book: () => show('necronomicon'),`.

- [ ] **Step 4: Run the tests**

Run: `pnpm test && pnpm build && pnpm lint`
Expected: all pass

Manual (dev): right-click a REVIEW REQUESTED row. "Cast a spell ▸" opens the equipped list in place. Picking one confirms, then casts. On a MINE row the entry is disabled. With nothing equipped, the entry is gone.

- [ ] **Step 5: Commit**

```bash
git add src/components/Acts.tsx src/components/Acts.test.ts src/App.tsx src/index.css
git commit -m "feat: right-click a PR to cast an equipped spell on it"
```

---

### Task 8: Version, full check, PR

- [ ] **Step 1: Full check**

Run: `cd src-tauri && cargo test && cd .. && pnpm test && pnpm build && pnpm lint && grep -rn "necro::\|NextLearn\|/api/necronomicon" src src-tauri/src mock`
Expected: everything passes, grep prints nothing

- [ ] **Step 2: Bump.** Read the current `version` in `src-tauri/Cargo.toml` on the branch, apply a minor bump (e.g. `2.27.5` → `2.28.0`, or `2.28.0` → `2.29.0` if #141 merged first), and set the same value on the `name = "gitdashy"` entry in `src-tauri/Cargo.lock`.

```bash
git commit -am "chore: X.Y.0"
```

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/spellbook
gh pr create --base main --title "feat: the Necronomicon becomes a spellbook" --body-file <body>
```

The body lists what changed per area (book, sidebar, right-click, server, removed Learn) and ends with the Claude Code attribution line. Add reviewers through REST (the gh token lacks `read:org`):

```bash
gh api -X POST repos/MartinRovang/git-dashy/pulls/<N>/requested_reviewers -f 'reviewers[]=NilsPontus'
```
