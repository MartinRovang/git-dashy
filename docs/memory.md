# gitdashy memory — full specification

How review memory works, and why it is shaped this way. If you are changing
anything under `dashy/core/memory.py`, read this first.

---

## 0. What needs installing

Reviews read and write memory with **no setup at all**: the memory directory
appears on first use and every review reads it back. That half is just gitdashy.

The half that reaches a regular coding session is what installs — and until now it
lived in one contributor's private agent corpus, so nobody else could reach it.

| | scope |
|---|---|
| `gitdashy install` | machine — two symlinks and two imports in the agent config |
| `gitdashy init --into DIR --loader FILE` | repo — the mirror, its ignore rule, its import, and the refresh |

`init` registers the path in `~/.prs_mirrors`, and the running dashboard re-mirrors
each entry on the refresh tick it already uses to pull the team. No session hook:
that would mean editing global settings and living inside a start-up timeout, and
the mirror only has to be as fresh as the facts, which change slowly.

---

## 1. The one rule

> A fact is not a fact because a model wrote it. It is a fact because it recurred.

Everything below follows from that plus one asymmetry:

**Automate promotion where being wrong costs only you. Require a keypress where
it costs other people.** A wrong fact in your own memory, you meet again tomorrow
and correct. A wrong fact in the team's memory lands in contexts where nobody who
could correct it will ever see it happen.

---

## 2. The seven stores

```
  ┌─ drafts ──────────────────────────────────────────────────────┐
  │  <private>/drafts/<owner>__<repo>.md                          │
  │  - (2) neo-api CI reports "skipping" for format-check         │
  │        ▲ how many independent reviews landed on this          │
  │                                                               │
  │  NEVER read into any prompt. Not by reviews, not by sessions, │
  │  not by the dream.                                            │
  └───────────────────────────────────────────────────────────────┘
             │ automatic, at PROMOTE_AT independent hits
             ▼
  ┌─ mine ────────────────────────────────────────────────────────┐
  │  <private>/general.md         your facts, every repo          │
  │  <private>/<owner>__<repo>.md your facts, one repo            │
  │  Pushed straight to your own private git repo, if it is one.  │
  └───────────────────────────────────────────────────────────────┘
             │ MANUAL — P, then t. Never automatic.
             ▼
  ┌─ team ────────────────────────────────────────────────────────┐
  │  <team>/memory/general.md                                     │
  │  <team>/memory/<owner>__<repo>.md                             │
  │  Everyone reads these. Everyone's sessions read these.        │
  └───────────────────────────────────────────────────────────────┘
             │ gitdashy sync-memory --into PATH
             ▼
  ┌─ mirror ──────────────────────────────────────────────────────┐
  │  <any repo>/.agent/team/general.md                            │
  │  <any repo>/.agent/team/repo.md                               │
  │  READ-ONLY. mine + team merged, exactly what a review sees.   │
  │  Refuses to write anywhere git would commit it.               │
  └───────────────────────────────────────────────────────────────┘

  ┌─ pool (evidence, not memory) ─────────────────────────────────┐
  │  <team>/memory/pool/<user>/<owner>__<repo>.md                 │
  │  Facts each person has ALREADY accepted for themselves.       │
  │  Written on promotion, withdrawn on share or forget.          │
  │  NEVER read into any prompt, any mirror, or the dream.        │
  │  Only for repos already named in the shared review log.       │
  └───────────────────────────────────────────────────────────────┘

  ┌─ project brief (declared, not learned) ───────────────────────┐
  │  <private>/project.md   and   <team>/memory/project.md        │
  │  What is being built, for whom, under what constraints.       │
  │  Two sources like everything else: on your own it is yours,   │
  │  in a team theirs joins it. Written by people, once. Read by  │
  │  every session and every review — so a reviewer knows what    │
  │  the code is FOR before judging whether a change serves it.   │
  │  The promotion pipeline never touches it: not dreamt over,    │
  │  not promoted into, never offered for sharing.                │
  └───────────────────────────────────────────────────────────────┘

  ┌─ log (separate axis) ─────────────────────────────────────────┐
  │  <team>/reviewed.jsonl  in a team  ·  ~/.prs_reviewed.jsonl   │
  │  Review history. Genuinely shared — it is what happened, not  │
  │  a claim about the world, so it needs no gate.                │
  └───────────────────────────────────────────────────────────────┘
```

`<private>` is `config.MEMORY_DIR` (`$PRS_MEMORY`, default `~/.prs_memory`).
`<team>` is `config.TEAM` (`$PRS_TEAM`, default `~/.prs_team`).

`<private>` may itself be a git checkout, in which case gitdashy pushes it after
every review — that is how your facts and drafts follow you between machines
without passing through the team. `L` takes a repo as well as a path: it clones
into a sibling, moves what is already there across, then swaps. Cloning straight
in is not possible, since git wants an empty directory and yours holds the facts
you are trying to keep.

---

## 3. How a fact travels

### 3.0 Where a claim comes from

Two surfaces propose facts, and until recently only one of them ever did:

| surface | how |
|---|---|
| a review | automatic — the `memory` field of every review |
| a coding session | `gitdashy remember`, which the shipped corpus now instructs the agent to use |

The second was built and then nothing called it, so every fact in the store came from a
review. That made the loop one-way: reviews learned, sessions only read. A session's
claim is a single claim, which is precisely what the gate below is for — it drafts, and
something else has to arrive at the same thing independently before it counts.

### 3.1 Arrival

A review returns up to three lines in its `memory` field. For each:

1. **Already known?** If it fuzzy-matches anything in `mine` or `team`, at either
   the general or the repo scope, it is **dropped on arrival**. Re-proposing what
   is already settled says nothing new.
2. **Seen before as a draft?** Fuzzy-match against that repo's drafts. On a hit,
   increment the count and **keep the first wording** — the count is what carries
   meaning, not the phrasing.
3. **Otherwise** add it as a new draft at count 1.

Fuzzy match is `difflib.SequenceMatcher` on a normalised string (lowercased,
backticks stripped, whitespace collapsed), ratio ≥ `NEAR` (0.82).

### 3.2 Promotion — automatic

A draft reaching `PROMOTE_AT` (2) independent reviews leaves the drafts file and
is appended to `mine/<repo>.md`. No prompt, no keypress.

**Why drafts are never read back:** if a draft were fed into a review prompt, the
reviewer would meet its own earlier guess as evidence and agree with itself. The
count would measure repetition, not durability. Rediscovery is the entire signal,
so the reviewer must arrive at the fact again *blind*.

### 3.3 Promotion — manual

`P` lists facts of yours the team does not have, one at a time (a fact is a
sentence you must read to judge; a column of clipped sentences is how something
wrong gets waved through).

- `t` appends it to the team's file and pushes.
- `x` forgets it from your own memory and pushes.
- `esc` leaves it alone.

There is no automatic path into team memory. But `P` is not a flat list: facts
two people have independently accepted sort first and are marked
**★ N people found this**, so the strongest evidence is what you see, not what
you have to go looking for.

**Corroboration without publishing drafts.** The pool holds only facts that
already passed someone's own recurrence test — two of *their* reviews agreed. Two
people's pools agreeing is four independent reviews across two humans. Raw drafts
never leave your machine; what is shared is what you already accepted, and even
that is evidence only, never context.

**When does a fact pool?** On promotion, and only if the team can already see the
repo's name — which means one of:

| condition | why it is enough |
|---|---|
| the repo is in the shared review log | reviewing there already showed them the name |
| the team holds memory for the repo | they demonstrably work on it |
| the fact is general | it names no repo, so there is nothing to disclose |

The log is what bootstraps this: it fills as you review, where "repos the team
already has memory for" would have started empty and never filled. Team memory is
there as well, or a repo you only ever *code* in could never corroborate, despite
being just as plainly theirs. And a repo that is neither — a side project you
reviewed privately — never has its name leave your machine, though its facts still
become yours.

### 3.4 Discard

| what | when |
|---|---|
| a proposed fact | on arrival, if already known in `mine` or `team` |
| a draft | when it reaches the threshold (it becomes a fact) |
| a fact of yours | `x` in the `P` screen |
| any fact, merged or dropped | `Z` dream, after you approve the diff |
| a mirror file | when its source is empty or gone — a mirror never outlives its source |

Drafts below threshold are never garbage-collected today. **Open issue** — see §8.

---

## 4. Reads

| reader | sees | never sees |
|---|---|---|
| review prompt | `project.md` (yours, then the team's), then the facts — every block labelled by source | drafts |
| agent session, any repo | `general.md` live, through a symlink in the user's config | drafts |
| agent session, one repo | `.agent/team/repo.md` — that repo's facts, mirrored | drafts |
| `Z` dream | `mine/*.md` and `team/*.md`, keyed by source | drafts, pool |
| nothing, ever | — | the pool is written and counted, never read as context |

Sessions read memory by two routes, and the split is deliberate. Cross-repo facts
go in globally, because a user-level `CLAUDE.md` import **does** follow a symlink
out of its tree — that is how an identity corpus loads — so one symlink and one
`@` line make `general.md` live everywhere with nothing to sync. Per-repo facts
cannot ride that route: a session in one repo has no business loading facts about
ten others, so they arrive through the mirror, scoped to the repo they describe.

So the mirror writes the repo file only. Mirroring general as well would put every
cross-repo fact in context twice, once by each route. `--general` opts back in, for
anyone who has not wired the user-level import.

A project-level import refuses a symlink, whether it points at a file or a
directory. Both were tested. That asymmetry is the whole reason the mirror copies.

Reviews are unaffected by the global route because they run `--safe-mode`, which
drops `CLAUDE.md` entirely — so memory reaches a review only through
`memory.read()`, and never twice.

Because drafts are excluded everywhere, the review prompt's existing line —
*"Memory from earlier reviews, trust it"* — is now defensible: everything under it
has either recurred across two independent reviews or been shared by a human.

---

## 5. Writes and pushes

| event | writes | pushes |
|---|---|---|
| review proposes facts | drafts, promotions into `mine`, and the pool | private + team repo |
| `gitdashy remember` | the same drafts, and the pool on promotion | private + team repo |
| `P` → `t` | team memory file, withdraws from the pool | team repo |
| `P` → `x` | removes from `mine`, withdraws from the pool | private + team repo |
| `n` / `g` edit | `mine` only — team memory is not hand-editable from the TUI | private repo |
| `Z` dream | `mine` and `team`, after you approve | both |
| review verdict | `reviewed.jsonl` | team repo |
| joining a team | seeds the **log** only | team repo |

**Joining a team no longer copies your memory in.** That was a bulk publish of
every private fact you had, unreviewed, in one action.

**The dream may write team memory directly** — it is already gated, since it shows
a diff and waits for `y`. It is explicitly told never to move a line from `mine/`
into `team/`: sharing is your decision, not the model's.

---

## 5b. Getting it back

Memory is the only thing here that cannot be recreated. A review can be run again, a mirror
is derived, a clone can be re-cloned — but a fact you lose is gone. Two nets sit under it,
and neither needs any setup.

**Local git history.** The first time anything writes to your memory directory, gitdashy
runs `git init` in it. Every rewrite goes through one function, so this holds for a review,
`remember`, `forget`, a hand edit through `n`/`g`, and a dream alike — not just the paths
someone remembered to add it to.

No remote is required and none is added; the commit is the point, and a missing origin is
the normal state of a machine that has not joined a team. Commits are authored by **your**
git identity; the fixed `gitdashy` identity is used only when the machine has none
configured at all, since that is exactly the machine with no other backup.

**One exception**: if `PRS_MEMORY` points inside another git repository — a notes repo, a
dotfiles checkout — no history is started, because nesting a repo there surprises the
tooling you already have and is not gitdashy's call to make. The compressed copies below
still cover you, and the enclosing repo's own history covers whatever it tracks.

Every write is a commit:

```
git -C ~/.prs_memory log --oneline
git -C ~/.prs_memory show HEAD~1:general.md      # read it as it was
git -C ~/.prs_memory checkout HEAD~1 -- general.md   # put it back
```

Joining a team later still works: a checkout with **local-only history** is not "already a
checkout", so `L` with a URL adopts it as before. The old history is not merged — it has a
different root — but it is not deleted either. It moves to `~/.prs_memory.local-history-<n>`
beside the directory and stays there until you remove it. `git --git-dir <that> log` reads it.

**Compressed copies.** Every `.md` across both sources is tarred into
`~/.prs_backups/<utc>-<reason>-<hash>.tar.gz` — on each refresh tick, and always immediately
before a dream rewrites anything. The newest 30 are kept. An archive is written only when the
content hash differs from the newest one, so an idle dashboard does not fill the directory
with identical tarballs. Restore with `tar xzf`; the paths inside are `mine/` and `team/`.

The directory is outside every synced tree, so backups are never pushed anywhere.

The one that most needs this: `Z` (dream) applies a model's output verbatim and **deletes any
file it returned empty**. It is one keypress, and before this it was unrecoverable on a default
install. It now takes a backup and a commit first, so accepting a bad dream is a decision you
can walk back.

## 6. What is deliberately *not* done

- **No automatic team promotion, even on corroboration.** Two people agreeing is
  strong enough evidence to justify it, and it is deliberately still one keypress:
  corroboration changes what `P` shows you first, not what happens without you.
  That also means only one threshold (`PROMOTE_AT`) actually decides anything.
- **No raw drafts shared, and no hashing.** SimHash over fact text would let
  corroboration work without publishing any wording at all. Not built: everyone in
  the pool already has push access to the same private repo and reads the same
  code, so the threat model does not justify it. Revisit if that stops being true.
- **No PR-based approval.** Considered and rejected: a PR per review is churn, and
  the queue puts approval where you already are.
- **No config file.** Locations persist as facts about the filesystem — a symlink,
  or a folder that has a `.git`. Env vars still win and the UI says so.
- **No unbounded header cost.** The share count is *not* shown in the header,
  because computing it is a directory scan and the header redraws every 500ms.

---

## 7. Constants worth arguing about

| name | value | meaning |
|---|---|---|
| `PROMOTE_AT` | 2 | independent reviews before a draft becomes yours |
| corroboration | 2 people | pools agreeing before `P` marks it ★ (display only) |
| `NEAR` | 0.88 | difflib ratio over **tokens** at which two wordings are the same fact |
| `CLONE` | 300s | cap on any command that talks to a remote |

`NEAR` compares tokens, not characters. On lines this short a character ratio puts
one wrong word above the bar — `format-check` against `type-check` scored 0.886 —
so a second review would "confirm" a fact that says the wrong thing, keeping the
first wording. On tokens the populations separate: measured rewordings bottom out
at 0.889, different facts top out at 0.857. That margin is thin, and it wants a
number from real use as much as the next one does.

`PROMOTE_AT = 2` is a guess. Two feels right for a repo you review often and slow
for one you touch monthly. It should be revisited with real numbers.

---

## 8. Open issues

1. **A departed teammate's evidence keeps counting.** Pool entries are pruned by
   their owner, on share or forget. gitdashy has no concept of membership — a team
   is a git repo, not a member list — so it cannot know someone left, and their
   backing lingers. Their observation was real when they made it, so this may be
   correct; it is at least undecided.
2. **Observations are not tagged by surface.** The counter records that a fact
   was seen twice, not that a review and a session saw it independently. So
   `gitdashy remember` twice will confirm a fact — a deliberate escape hatch
   (editing `mine/` by hand with `n`/`g` does the same thing more directly), but
   it means "confirmed across surfaces" is a hope, not a guarantee. Tagging each
   observation would make it one.
3. **Drafts never expire.** A fact proposed once, three months ago, sits forever.
   Wants either an age cap or inclusion in the dream (as drafts, clearly marked).
   The pool self-prunes on share and forget, so only drafts grow unboundedly.
4. **`PROMOTE_AT` is unvalidated.** No data yet.
5. **Team memory has no hand-edit path** from the TUI any more — `n`/`g` now edit
   yours. You can still edit the team checkout directly with git.
6. **Nothing here has met a real review yet.** The whole path is test-verified
   only. Numbers from real use should settle issues 1-3.

---
