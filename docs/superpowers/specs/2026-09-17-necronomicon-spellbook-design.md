# Necronomicon as a spellbook

Date: 2026-09-17 · Branch: `feat/spellbook`

## Why

The Necronomicon today is a book of memory points (Learn writes them, `necro.rs` ranks them). A collaborator
is reworking the memory system, so the book gets a new job: the reviewer's abilities, as spells, passives and voices.

## What a spell, a passive and a voice are

- **Spell**: a one-time, in-depth investigation of one topic, cast on one PR. Where a passive adds a
  section to every review, a spell goes deep on its topic for this PR only: it reads beyond the diff (callers,
  migrations, config, tests) and reports what it found. Spells are written by the user. A few starter spells ship
  (e.g. `migration-audit`, `auth-check`, `test-gaps`).
- **Passive**: today's hunters (`config::HUNTERS`: ponytail, security, tests, perf, humanizer). Built in, on or
  off, added to every review.
- **Voice**: today's voices (`config::VOICES`: review, caveman, bot). Built in, on or off.

Passives and voices stay built in: the book toggles them, it does not edit them.

## The book (frontend, `src/components/Necronomicon.tsx`)

Same name, same book look, same chapter and leaf navigation (arrow keys, leaves). Three chapters:

1. **Spells**: the left page lists spells and the right page shows the selected one. You can edit its name and
   instructions, then save, delete, or **cast on the selected PR** (disabled when no PR is selected).
   A "new spell" entry starts an empty one.
2. **Passives**: each hunter with its description, the exact prompt text it adds, and an equip switch.
3. **Voices**: the same layout for voices.

The switches post the same `voice` / `hunter` settings the sidebar already posts. The book copies no prompt text:
it all comes from `GET /api/spells`.

Removed: memory points, rank up/down, Learn button, `NextLearn` footer countdown, the `learn` field in state,
`necro.rs`, `/api/necronomicon`.

## Left sidebar (`src/components/Sidebar.tsx`)

A new **Necronomicon** group (Lucide `Skull` icon; Knowledge already uses `BookOpen`), for quick use only:

- voice chips and passive chips (moved out of the Agent group, same toggle behaviour as before)
- a spells list: pressing a spell casts it on the selected PR
- an "open the book" link that switches the view to the Necronomicon

When shut, the sidebar shows only the group's icon, like the other groups.

## Casting from a PR

The PR options menu (`setMenuAt` / `ActsMenu`) gets `Cast ▸ <spell>` entries. All three entry points (sidebar,
book, PR menu) do the same call.

## Server (Rust, `src-tauri/src`)

### `spells.rs` (new)

- Spells live as `<config::home()>/spells/<name>.md`. The file name is the spell's name and the body holds its instructions.
- Names must match `^[a-z0-9-]{1,40}$`. Any other name is refused, so a name cannot leave the folder.
- `list() -> Vec<(name, text)>` is sorted by name. If the folder does not exist, it is created with the starter
  spells first. An existing but empty folder stays empty: the user deleted them.
- `save(name, text)`, `delete(name)`.

### Endpoints (`web.rs`)

- `GET /api/spells` returns `{ spells: [{name, text}], passives: [{name, about, prompt, on}], voices: [{name, about, prompt, on}] }`.
  `prompt` is the `HUNTER` / `VOICE` table text from `review.rs`, `about` is a one-line description added next to it, and `on`
  comes from config.
- `POST /api/spells` takes `{op: "save" | "delete", name, text?}`. A bad name gets a 400 with the reason.
- `POST /api/review` takes an optional `spell` name. An unknown spell gets a 400.

### Prompt (`review.rs`)

- `Inputs` gets `spell: Option<(String, String)>` (name, instructions).
- When set, `prompt()` adds a **cast block** before the voices/hunters tail. The block names the spell and tells
  the model that this run is an in-depth investigation of that topic on this PR: go past the diff where the topic needs it, and
  give it the most weight. It then includes the instructions.
- `sections_for` puts the spell's section (`**Migration-audit**`, via `title`) first, so the contract
  requires it.
- Everything else is a normal review run: model/depth/effort, memory brief, DB repo, posting/holding rules, log,
  held queue. The review HELLO line names the spell being cast.

## Errors

- Casting with no PR selected: the controls are disabled, and the server does not guess.
- A spell deleted while a cast is queued: `/api/review` returns 400 `unknown spell`, which shows as a toast.
- Spell folder not writable: save returns the IO error as a 500, which shows as a toast. Nothing is lost silently.

## Tests

- Rust `spells.rs`: name check refuses `../x`, `A`, `""`, and 41 chars; save then list returns it;
  delete removes it; a missing folder gets starters and an empty folder does not.
- Rust `review.rs`: `prompt()` with a spell contains the cast block and the spell section comes first in the
  contract. Without a spell, the output is unchanged (existing tests still pass).
- TS: none today for the book. Add one only if the chapter model moves into a plain function.

## Out of scope

- Editing or adding passives and voices.
- Learning spells from past reviews.
- Casting several spells in one run.
- Any memory-system work (the collaborator owns it).
