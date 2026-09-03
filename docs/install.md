# Installing — what it does, and what it does not

**Reviews need none of this.** gitdashy learns and remembers with no setup at all: the
memory directory appears on first use, reviews read it back, and knowledge accumulates
per repo. If that is all you want, install nothing.

What installs is one thing only: making a **coding session** read the same knowledge
your reviews already have.

---

## The three tiers

| you do | what you get | what it touches |
|---|---|---|
| nothing | reviews learn and remember, per repo and across repos | `~/.prs_memory/` only |
| press `T` in the dashboard | the same, shared with a team and gated by `P` | a private git repo you name |
| `gitdashy install` | **coding sessions read it too** | two symlinks and one block in your agent config |
| `gitdashy init` in a repo | sessions there also read that repo's own facts | that repo's `.git/info/exclude` and the file you name |

Each tier is additive and independent, and turning one on later is **retroactive** —
months of learning become visible the moment you install, with nothing to migrate.

---

## Where knowledge actually lives

One store, at every tier. Installing does not move, copy or convert it.

```
~/.prs_memory/
  general.md                     facts true of every repo
  neomedsys__neo-api.md          that repo's facts
  drafts/                        seen once; not facts yet, and never read into a prompt

~/.prs_team/memory/              the team's, same shape, only when you are in a team
```

`$PRS_MEMORY` and `$PRS_TEAM` move them; so do `L` and `C` in the dashboard.

---

## What `gitdashy install` writes

Two symlinks and one marked block. Nothing else.

```
~/.claude/prs-memory  ->  ~/.prs_memory
~/.claude/prs-team    ->  ~/.prs_team/memory      (harmless before you have a team)

~/.claude/CLAUDE.md   +=  <!-- gitdashy:begin -->
                          … @prs-memory/general.md
                          … @prs-team/general.md
                          <!-- gitdashy:end -->
```

It **asks first**, showing exactly the above with your real paths and whether each item
is new, already correct, or something it will refuse to touch. `--dry-run` shows and
stops. `--yes` skips the question. Without a terminal it refuses rather than assuming.

**It does not** install hooks, touch `settings.json`, modify any repository, or send
anything anywhere.

### Why a symlink and not a copy

A **user-level** `CLAUDE.md` import follows a symlink out of its own tree. So the session
reads *the same file* a review writes — one file, no copy, nothing to sync, nothing to go
stale. Fix a wrong fact with `g` and every later session is corrected immediately.

### Undoing it

```sh
gitdashy install --uninstall
```

Removes only what it wrote. If it finds the imports outside its marked block — because
you added them by hand — it says so and leaves them, rather than deleting your lines.

---

## What `gitdashy init` writes, per repo

A **project-level** import refuses to follow a symlink, whether it points at a file or a
directory. Both were tested. So a repo's own facts have to be *copied* in:

```sh
cd ~/src/my-repo
gitdashy init --into .agent/team --loader CLAUDE.local.md
```

- writes the mirror at `--into`
- excludes it through `.git/info/exclude` — **never** the tracked `.gitignore`, which
  belongs to everyone; one machine's local copy has no business in their history
- appends one import line to `--loader`
- registers the path, so the running dashboard re-mirrors it on each refresh

No hooks: a session-start hook would mean editing global settings and living inside a
start-up timeout, for a copy that only has to be as fresh as facts that change slowly.
The consequence is that it goes stale while gitdashy is not running.

The mirror is read-only. It carries a header naming its source and when it was taken, and
it is deleted when its source goes — a mirror never outlives what it mirrors.

---

## Private and team stay separate

Installing does not merge them.

| | yours | the team's |
|---|---|---|
| store | `~/.prs_memory/` | `~/.prs_team/memory/` — different directory, different repo |
| drafts | `drafts/`, yours only | never |
| session route | `@prs-memory/general.md` | `@prs-team/general.md` — separate symlink, separate import |
| in a prompt | `### mine` | `### team <name>` — read together, always labelled |

Exactly three things write into team memory: **`P` → `t`** (you press it, one fact at a
time), **`Z` dream** (you approve a diff first, and it is told never to move a line from
yours into theirs), and the **evidence pool**, which is never read as memory by anything.

Joining a team copies the review **log** — shared history, a record of what happened —
and deliberately not your memory.
