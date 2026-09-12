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

It gives two rules:

- **Automatic promotion needs two independent observations.** Nothing crosses on
  its own until two reviews that did not know about each other landed on the same
  claim, folded by the token matcher of §3.1. A model is asked about near-misses
  that matcher *refused*, across two people, and never about a fold it already
  made (§4b-3).
- **A hand promotion needs one keypress and no recurrence.** `W` → `t` accepts a
  single draft, and `P` → `t` sends a fact the team has not got. Someone who has
  read the line is the second opinion the counter stands in for, so the count does
  not apply to them. Both need the repo bound; `W` → `t` also needs the team's
  consent, because it goes through the same `_pool()` the automatic path does.
  **`P` → `t` does not, and sends to a team that answered no.** Consent is about
  what leaves without anyone sending it; `t` is somebody sending it.

The binding and the consent are each one keypress, once per repo and once per team.
Nothing prompts about an individual fact. §3.3 has the detail.

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
             │ automatic, when the repo is bound and that team consented
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
  │  Written on promotion, withdrawn on forget — always yours,    │
  │  while the TEAM's copy stays if another backer remains.       │
  │  NEVER read into any prompt, any mirror, or the dream.        │
  │  Only for repos bound to the team (§4b-3), never the rest.    │
  └───────────────────────────────────────────────────────────────┘

  ┌─ project brief (declared, not learned) ───────────────────────┐
  │  <private>/project.md   and   <team>/memory/project.md        │
  │  What is being built, for whom, under what constraints.       │
  │  ONE per review, chosen by ~/.prs_bindings: the team a repo   │
  │  is bound to, else yours. Never both — two statements of the  │
  │  purpose in one prompt is worse than none. Written by people, │
  │  so a reviewer knows what the code is FOR before judging      │
  │  whether a change serves it. The promotion pipeline never     │
  │  touches it: not dreamt over, not promoted into, not shared.  │
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

Fuzzy match is `difflib.SequenceMatcher` over **tokens** — words, lowercased,
backticks and punctuation dropped — at ratio ≥ `NEAR` (0.88). Before the ratio is
taken at all, the two lines must carry the same count of each qualifier group
(negation, exclusivity): a single inserted `not` moves a sequence ratio by about
0.08, so without that guard two lines stating opposite things fold into one. §7
has the numbers and why tokens rather than characters.

### 3.2 Promotion — automatic

A draft reaching `PROMOTE_AT` (2) independent reviews leaves the drafts file and
is appended to `mine/<repo>.md`. No prompt, no keypress.

**Why drafts are never read back:** if a draft were fed into a review prompt, the
reviewer would meet its own earlier guess as evidence and agree with itself. The
count would measure repetition, not durability. Rediscovery is the entire signal,
so the reviewer must arrive at the fact again *blind*.

### 3.3 Promotion — into the team, also automatic

A fact that becomes yours joins the team's file in the same call, with no
keypress. Two things gate it, and neither is a decision made per fact:

| gate | what it is |
|---|---|
| `team_visible` | the team can already see the repo's name — the table below |
| consent | that team answered yes, once, at launch (`~/.prs_memory/.publishing`) |

This was one keypress until v1.43.0, and the argument for the keypress was that a
wrong fact in the team's memory lands where nobody who could correct it will see
it happen. The two observations now come from two people's independent reviews.
The gate itself is the one
§3.2 describes. §4b-3 has the full account, including what a *no* to consent does
and where a model gets asked.

**`P` shows your facts and lets you take one back.** It lists your facts for
repos bound to a team, one at a time (a fact is a sentence you must read to judge;
a column of clipped sentences is how something wrong gets waved through), and says
of each whether the team has it:

- `t` sends one that never went — a fact older than consent, promoted while the
  repo was bound to nothing, or held back because this team answered no to
  automatic publishing. `t` overrides that no, for this one fact, because you are
  the one sending it. A fact the team already has does not offer the key.
- `x` forgets it from your memory, from the evidence pool, and from the team's
  file unless a teammate independently reached it too — §4b-3 says why theirs
  survives yours.
- `esc` leaves it alone.

Facts two people have independently accepted sort first and are marked
**★ N people found this**, so the strongest evidence is what you see, not what
you have to go looking for.

**Corroboration without publishing drafts.** The pool holds only facts that
already passed someone's own recurrence test — two of *their* reviews agreed. Two
people's pools agreeing is four independent reviews across two humans. Raw drafts
never leave your machine; what is shared is what you already accepted, and even
that is evidence only, never context.

**When does a fact pool?** On promotion, and only if the repo belongs to the team
— `team_visible()`, which asks the binding:

| condition | why it is enough |
|---|---|
| the repo is bound to a joined team | you said so, visibly, and can take it back |
| the fact is general | it names no repo, so its project decides (§4b-3) |

It used to be the shared review log, or the team already holding memory for the
repo. Both were true enough while the log was the only rule, and both were wrong
for the job: reviewing one PR put a repo in the log for ever, with no undo, and
the thing being decided is whether facts about your private work reach other
people. Joining still seeds bindings from the log, so nothing stopped working; the
decision became one you can see and reverse. A repo bound to nothing never has its
name leave your machine, though its facts still become yours.

### 3.4 Discard

| what | when |
|---|---|
| a proposed fact | on arrival, if already known in `mine` or `team` |
| a draft | when it reaches the threshold (it becomes a fact) |
| a fact of yours | `x` in the `P` screen — yours and your pool line always; the team's only when no other backer remains |
| any fact, merged or dropped | `Z` dream, after you approve the diff |
| a mirror file | when its source is empty or gone — a mirror never outlives its source |

Drafts below threshold are never garbage-collected today. **Open issue** — see §8.

---

## 4. Reads

| reader | sees | never sees |
|---|---|---|
| review prompt | ONE `project.md` — the bound team's, else yours, named in the prompt — then the facts, every block labelled by source | drafts, the other brief |
| agent session, any repo | `general.md` live, through a symlink in the user's config — **and both briefs, unscoped** | drafts |
| agent session, one repo | `.agent/team/repo.md` — that repo's facts, mirrored, and so scoped by the binding | drafts |
| `Z` dream | `mine/*.md` and `team/*.md`, keyed by source — and writes back only `mine/` | drafts, pool |
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

## 3b. Reading a review against the code

A finding is `{kind, loc, text}` — `blocking`, `note` or `nit`, a `file.py:141`, and a line. The summary
lists them; **`2` puts them back on the lines they name.** `gh pr diff` is parsed into files and hunks,
each finding is anchored to its new-side line number, and the note is drawn under it.

- **`D`** switches between *marks only* — the marked hunks with ±3 lines of context, which is what a
  review of a 1,400-line diff actually concerns — and the whole change.
- **`n`/`N`** moves to the next anchor and the window follows: a mark when you are reading the review,
  a file when you are reading the diff. Both are the same gesture.
- **`c`** cycles the context: ±3, ±8, the line alone.

A finding about a file the diff does **not** touch is kept and shown as `not in this diff` — a review's
most important line is sometimes about something the change should have contained, and the code tab
must not be quieter than the summary it replaces.

The diff is cached on the PR's head sha, so a push invalidates it and nothing else does.

## 4a. Several teams

```
~/.prs_teams/<key>/               one checkout per team; the directory name IS the key
    team.json                      name, description, and what it declares it covers
    memory/{general,<repo>,project}.md
    memory/pool/<user>/*.md
    reviewed.jsonl                 that team's shared review log
~/.prs_memory/                     yours
~/.prs_reviewed.jsonl              yours — reviews of repos bound to no team
```

The filesystem is the registry: a directory under `~/.prs_teams` holding a `.git` **is** a joined team,
the same way `~/.prs_team` being a checkout used to mean "team mode is on". There is no config file to
fall out of step with what is actually on disk.

`bind.team_dir(slug)` is the one seam. Every read and every write asks it the same question — *which
directory does this slug mean* — which is why going from one team to many changed that function's body
rather than three dozen call sites.

**The review log is per team, merged on read.** A review is appended to the log of the team its repo is
bound to; an unbound repo's goes to yours. `log.reviewed()` reads all of them newest-first, so a
teammate's review still appears in your list — per team, and only for repos that team owns. (It used to
return reversed *file* order and call that newest-first, which is only the same thing when appends
arrive in time order. With several logs it never is.)

**A general fact needs exactly one team.** It names no repo, so no binding selects a team for it, and
with two joined that is two different claims — so it stays yours until you say where it goes.

**A team is a git repo — or just a directory — that pools what reviews learn.** Whoever can reach it is
on the team. There is no service and no account, and nothing here assumes GitHub: `git clone` takes any
URL, and a bare `owner/name` is expanded to a GitHub URL as a convenience and nothing more.

**Its name and description live inside it**, in `team.json`, so everyone who clones it sees the same
ones. `memory/project.md` beside it is the brief — what the work is for, its constraints, its shape.

**The key is the name you gave it, fixed at creation.** The *location* — a path today, a git URL
tomorrow — is separate and changeable. That split is what makes the local-then-hosted move safe: an
origin-derived key does not exist until the team is hosted, so every binding pointing at the team would
have gone dead at exactly the moment it got a URL. The display name in `team.json` can be edited
freely, because it is not the key.

```sh
gitdashy teams --new "NeoMedSys Platform" --desc "Precision-medicine platform."
gitdashy teams --new "Acme" --at /srv/shared/acme-mem     # kept elsewhere, linked
gitdashy bind --owner neomedsys --team neomedsys-platform # add repos to it
gitdashy teams --team neomedsys-platform --connect git@somewhere:us/mem.git   # when you have a repo
gitdashy teams --join git@somewhere:us/mem.git            # a colleague, from any host
gitdashy teams --team neomedsys-platform --cover neomedsys   # declared in the team: joiners get it bound
```

`--at` takes an **empty** directory, or one that does not exist yet; one that already holds something is
refused rather than adopted. A path given to `--join` must be a **bare** repo — git refuses pushes into a
checkout — so a team on a shared drive is `git init --bare` there, `--connect` from one machine and `--join`
from the rest. `--join` refuses a repo that is already one of your teams, and writes a `team.json` into a
repo that has none, so everyone keys it the same way. `--connect` refuses a remote holding history that is
not this team's (that is a team to join) and accepts one holding the team's own, so a host can be moved.

**What a team covers is declared in it.** `covers` in `team.json` lists owners (`acme/*`) and repos
(`acme/api`). Bindings stay per machine: the declaration seeds, it does not own, so a `--forget` sticks
and `--uncover` leaves what it seeded in place.

**Adoption happens at `setup()` — joining — and nowhere else.** Not in `activate()`, which runs on every
command and every launch. Seeding there meant a `covers` line pushed to the team repo *after* you joined
bound owner-wide rules on your machine with no keypress and nothing that said so. A binding decides which
brief a review reads **and** whether facts about those repos may be pooled and shared into that team, so
anyone who could push to the team could reach repos it has never held a fact about. The log seeding beside
it is disclosure-neutral by construction; a claim is not. Joining is the consent; a claim that appears
later is listed by `gitdashy teams` and taken with `bind --owner`, which is a keypress. *Automate promotion
where being wrong costs only you. Require a keypress where it costs other people.*

Repo claims are seeded for **every** team before any owner rule, because the store resolves an exact
binding ahead of an owner rule and seeding has to deliver the same order — per team, one team's `acme/*`
was written first and another team's `acme/api` was then skipped as already resolved, so which team won a
contested repo came down to the alphabetical order of team names. A target two joined teams both claim is
left alone rather than guessed at, exactly as a repo in two logs is.

`T` in the dashboard does the same: a list, a number opens a team's own screen with its verbs (`e` brief,
`d` describe, `c` connect, `o` cover an owner, `x` leave), `n` starts one, `a` joins one. The TUI never asks
where a team should live; that is `--at`, in a shell.

A team needs **no remote to be useful** — memory works local-only the same way. `connect` pushes what
is already committed, so the `team.json` you wrote before you had a repo is what the next person clones
and keys by.

**Migration.** A pre-plural `~/.prs_team` moves to `~/.prs_teams/<slug>/` on first launch, after a
backup. It refuses rather than coping: no origin to key it by, a destination that exists, or any
uncommitted or unpushed work. `os.rename`, never copy-then-delete, so a failure leaves the source
exactly where it was.

## 4b. Which brief a review gets

Facts are keyed by repo (`owner__repo.md`) with `general.md` for what holds everywhere. The brief was
the only store keyed by **nothing** — one file per person, injected into every review of every repo. In
a team you got yours *and* theirs, concatenated: two statements of what the work is for, in one prompt.

`~/.prs_bindings` fixes the selection. Same file shape as `~/.prs_mirrors` — one JSON object per line,
deduplicated on read, removal by tombstone:

```json
{"repo": "acme/api", "team": "org/platform"}
{"forget": "acme/api"}
```

An **owner rule** covers a whole org in one line, and an exact binding or a `--forget` tombstone
overrides it — so the one repo under that owner which is not the project can be left out, which a
pattern alone cannot express:

```json
{"owner": "neomedsys", "team": "neomedsys/review-memory"}
{"forget": "neomedsys/someones-fork"}
```

Precedence lives in one function (`bind._pick`), so the team a review uses and the label a screen shows
can never disagree: exact binding, then an explicit unbind, then the owner rule.

### What a binding grants

Binding a repo to a team is a **trust decision**, not only a routing one. A bound repo's mirror
(`.agent/team/repo.md`) carries the team's `project.md` and its general facts, and a coding session
loads that file as *instructions*. `team.pull()` fetches both over git from the team remote.

So: **anyone who can push to a team's remote can put text into the context of every session in every
repo bound to that team.** Not a new capability — the retired global `prs-team` link carried the same
text to every session on the machine, more widely and with no binding to scope it — but the boundary is
now worth naming, because binding is the control:

- Bind to a team whose remote you would give commit access to.
- A repo bound to nothing reads your own memory and nothing else.
- `gitdashy bind <repo> --forget` withdraws it; the mirror stops being refreshed on the next tick.

The same is true of the facts themselves: they are prose a model wrote and two runs agreed on, and they
reach a reviewer's prompt. Recurrence is a filter for *durability*, not for intent.

Keyed by the origin slug, so a URL, an ssh remote and a bare `owner/name` all land on the same row and a
binding survives a re-clone or a move. Selection for repo `R`:

| | brief | what the surface says |
|---|---|---|
| bound to a team you are in, which has one | that team's | `team org/t` |
| bound to a team you are in, which has none | yours | `yours · team org/t has no brief` |
| bound to a team you are not in | yours | `yours · not in team org/t` |
| bound to nothing | yours | `yours · acme/api is bound to no team` |
| none anywhere | — | `no brief written` |

> **The session path is scoped the same way** (since 2026-09-08). `gitdashy install` writes one link and
> imports one file — `@prs-memory/general.md`. A repo's mirror (`repo.md`) carries the ONE brief
> `brief(repo)` picks and the bound team's general facts above the repo's own, from the same
> `sources(repo)` a review uses, so a session and a review of one repo are told the same things by the
> same team. An install from before this wrote a global `prs-team` link and imported both briefs; the
> next `gitdashy install`, or the next launch, retires the link and rewrites the block.

**The binding decides everything the team knows about a repo**, not just the brief. `memory.sources(repo)`
returns your memory alone for an unbound repo, so it reads no team facts — not even `general.md` — and
`team_visible(repo)` follows the same rule, so a fact about private work is never pooled as evidence or
offered for sharing. The repo argument is required: the unscoped set lives in `every_source()` under its
own name, for the two readers that must see all of memory (the dream, and the backup). Making it a flag
on `sources()` would have left the unscoped set one forgotten argument away.

**Never two.** `memory.brief(repo)` returns the text *and* where it came from, so a caller cannot put a
brief in front of a reviewer without holding the answer to "which one, and why that one" — the prompt
says it, and so does the detail pane. The previous defect was invisible precisely because nothing named
the brief that went into every prompt.

**Why declared and not inferred.** Memory visibility used to use the covering rule: the team's shared
review log names the repo. It bootstraps on its own and needs no upkeep — but a log entry is a side
effect of reviewing one PR, nothing ever removes one, and two teams can both name a repo with no way to
prefer either. An irreversible state change caused by a side effect, deciding both what a review is told
and what gets published to other people. So it is **retired**, not kept beside this one: two mechanisms
answering adjacent questions can disagree and nothing would notice.

Joining a team seeds bindings from its log **once**, which is what makes retiring it affordable — the
bootstrap still happens, into a store you can read and take back: `gitdashy bind`, `--owner`, `--forget`,
`--list`. A `--forget` leaves a tombstone, so the next startup's seeding does not undo it, and seeding
skips repos an owner rule already covers rather than pinning them to a team nobody chose repo by repo.

## 4b-2. Two spellings of one fact

The promotion gate compares token **sequences** (`NEAR`, difflib over `_toks`). That is right for folding
automatically — a false match writes a fact nobody said — but it is order-sensitive, so the same claim
worded in another order is never folded:

```
- (1) [r:7a2c] CI reports skipping for the format-check job
- (1) [r:91cf] the format-check job in CI reports skipping every run     seq 0.375 — two rows, forever
```

Both sit one review short of the gate, and no later review joins them: it matches one wording or the
other. `W` then `s` scans for these. The scan compares **content words as a set** (`OVERLAP`, stopwords
dropped), which is order-insensitive and finds them — measured over 165 real drafts, 2991 pairs the gate
had rejected, 2717 at zero overlap, 12 at or above 0.30. A person decides each pair; nothing folds on its own.

**Contradictions are never one fact.** Both measures compare wording, and a single inserted negation moves a
sequence ratio by about 0.08 — so `drafts are read into the prompt` and `drafts are *never* read into the
prompt` scored 0.92 against the 0.88 gate. Folded, counted as two observations, and whichever wording
arrived first was promoted as confirmed. Reachable whenever the code changes between two reviews. Both
`_same` and `_overlap` now compare *negation parity* first and refuse outright when it differs; parity
rather than presence, so two ways of stating the same negative still read as one fact. `not` and `no` are
also no longer stopwords in `_overlap`: a stopword list for retrieval drops words that carry no topic,
and this measure asks whether two lines say the same thing, where negation is the word that reverses the
answer. With them dropped it scored a line against its own opposite at 1.00.

**Word rules find candidates; they never decide.** The scan compares content words as a set, which is
order-insensitive and therefore finds the rewordings the gate misses — but two sentences with their
subject and object swapped share *every* token and state opposite things, so no threshold over words is
safe to fold on at any height. Above the floor the candidates go to the model, one question per pair:
*are these the same claim?* It is answering about wording, not about truth — the two observations already
happened, in two reviews that did not know about each other, and what it repairs is the matcher's
blindness. It writes nothing: the reply is `true`/`false` per numbered pair, a key that was never sent is
discarded, and a person still presses `y`. If the model cannot be reached, times out, or answers
something that is not JSON, **every** candidate is kept — a filter that empties the list on failure is
worse than no filter, because "nothing to fold" would be believed.

**Folding earns a count only across different reviews.** Each observation records which review made it —
`- (2) [r:7a2c,r:91cf] the fact` — because two drafts at `(1)` are either two reviews the matcher failed to
fold, which is a promotion it lost, or *one* review that worded a thing twice, which is the
self-confirmation `PROMOTE_AT` exists to refuse. Only the ids tell those apart:

| the two rows | folding gives |
|---|---|
| different review ids | the sum — the count was earned, and it may promote |
| the same review id | the max — one opinion stays one opinion |
| no ids (written before this, or an id collision) | the max — unknown origin is not proven different |

The id is generated inside `append()`, because one call to `append` **is** one review. A collision reads
as "same review", so the failure direction refuses to sum rather than summing wrongly. Files written
before ids existed still parse, with `()` for provenance.

## 4b-3. Two people are the second observation

One machine almost never proposes the same fact twice. Measured on a real store: **140 drafts, every one
at `(1)`, and not one specific fact ever promoted by recurrence.** Two people reviewing the same repo do
land on the same facts, and that is stronger independence than same-machine recurrence — different
person, different PR, different moment.

So drafts are pooled the way accepted facts already are:

```
<team>/memory/pool/<user>/<repo>.md      facts that person accepted     (evidence, never read)
<team>/memory/drafts/<user>/<repo>.md    what their reviews proposed    (evidence, never read)
```

Same disclosure rule as the evidence pool — `team_visible`, so a repo bound to nothing publishes nothing
— and the same hard invariant: nothing under either path is read by `sources()`, `scope_text()` or the
mirror, so a teammate's guess can never reach a prompt. What is new is that these are *unconfirmed*, so
what a colleague sees includes wrong guesses about a bound repo.

**Reaching the team is automatic too, as of v1.43.0.** It used to be one keypress, on the argument that
a wrong fact in your own memory you meet again tomorrow and fix, while a wrong fact in the team's lands
where nobody who could correct it will see it happen. That argument was written when a promotion meant
two runs of one model on one machine. A promotion now means two reviews that did not know about each
other — usually two people — and a model that read both and called them the same claim. The keypress was
also, measurably, where the pipeline stopped: nine facts on a real machine, none of which a colleague
ever saw.

What guards it is unchanged and is the whole of the safety: `team_visible`. A repo bound to nothing
publishes nothing. Binding is the decision, made once, visibly, and undoable.

**One keypress remains, and it is about the contract rather than about a fact.** A binding made before
this meant "reviews of this repo read that team's context"; it did not mean "publish my facts, and my
reviewers' unconfirmed guesses, there". So each joined team is asked once, at launch, with the counts
of what is waiting — `~/.prs_memory/.publishing` records the answer, a no included, and nothing pools or
publishes to a team that has not said yes. `_team_for()` mirrors `_project()` step for step so the
permission is looked up for the same team the write goes to.

**So `forget()` now withdraws from the team as well.** Nobody chose to publish it, so nobody should have
to know it was published in order to remove it. `P` lists your facts for bound repos and says which the
team has; `x` removes one from your memory, from theirs, and from the evidence pool — but it leaves the team's
copy alone while another contributor is still behind it, since nothing would put it back for them. The team's copy is
matched exactly rather than by similarity — it is the one file where a wrong removal costs everyone.

**A general fact belongs to a project, not to you.** `general.md` used to mean "true for me everywhere",
which is why it had nowhere to go the moment you joined a second team: `_the_one_team()` refuses to guess,
so eight general facts sat unpoolable and unshareable with nothing on screen saying why. It means "true
across THIS PROJECT" now, and the project is the team of the repo you were in when you observed it —
`gitdashy remember --general` keeps the directory's origin, and `P` uses the row you are on. The
team-level `general.md` that receives it already existed and was already read for every bound repo; the
only thing missing was a way to get a fact into it. An unbound context still selects nothing: context
narrows the answer, it never invents one.

**What a teammate contributes is read from the bound team's pool only**, and capped per person
per repo — those lines reach a model and a `true` promotes, so the size of one person's file is
the size of a prompt they get to write. Both sides are JSON-encoded and the prompt names them as
data; the judge is given no tools and runs in safe mode, and only a boolean is read back. The
residual is inherent to a shared team: a teammate can promote one of your drafts by writing the
same claim, which is what corroboration means.

**Two residual risks, named rather than fixed.** `whoami()` is `$USER`, and that is the only thing
separating your pooled drafts from a teammate's when the self-confirmation guard reads them: two
people who both run as `ubuntu` never corroborate each other, and one person on two machines with
different usernames does. And a teammate can promote one of your drafts by writing the same claim
themselves — which is what corroboration *is*, so it is a property of a shared team rather than a
defect, but it is worth knowing that the second observation is not automatically a second opinion.

**The count is over distinct review ids across both people.** Your `[r:7a2c]` and their `[r:91cf]` is two
runs that did not know about each other, which is what `PROMOTE_AT` has always meant. Your own pooled
file is excluded when reading theirs: counting it would let one review confirm itself by a route that
did not exist when that rule was written.

**Two thresholds, because they are two jobs.** `OVERLAP` (0.30) sizes a list a *person* reads, where a
false candidate costs their attention. `CROSS` (0.12) feeds the *model*, where a false candidate costs
one true/false answer and a missed one costs a fact that never promotes. The loose threshold is only
safe because something reads the candidates — so if the model cannot be asked, `cross_check` promotes
**nothing**, while the person's scan keeps every candidate. `judged()` returns `None` for "not asked"
rather than a list, precisely so those two callers can take opposite directions.

It runs in two places. At the end of `review()`, for the repo just reviewed, where new observations
arrive and a background thread already exists; it can never fail the review, since the verdict is
posted by then and a promotion missed today happens next time. And on every refresh tick, right after
`team.pull()`, as `memory.sweep()` — that is the moment a teammate's pooled drafts exist on this
machine, and it is the only thing that reaches a repo you have stopped reviewing, or a backlog of
drafts written before pooling existed.

**A sweep is nearly free on a quiet machine.** Pooling is file writes. The model is asked only about
pairs it has not seen: a verdict of *different* is recorded by pair id in `~/.prs_memory/.settled`, so
a rejected pair is never bought twice — drafts never expire, so without that a tick would pay for the
same answer every five minutes. Agreement is not recorded, because an agreed pair leaves the queue by
promoting and cannot come back. An unreachable model settles nothing and is asked again next tick.

## 4c. Looking at what is not a fact yet

`gitdashy drafts` lists them; **`W`** in the dashboard shows one at a time, `t` accepts it as a fact,
`x` drops it.

**Reading drafts does not weaken the invariant that guards them.** "Never read into a prompt" keeps the
MODEL from meeting its own guess as evidence and agreeing with itself — that is what would make the
count measure repetition instead of durability. A PERSON cannot self-confirm, so showing them costs
nothing. The window was missing because the two got conflated, not because anyone decided against it.

`t` is the only path into your memory that is not recurrence, and it takes a person. `PROMOTE_AT` is a
proxy for a judgement you may already have: recurrence is the right gate for a line nobody has read,
but once you HAVE read it and know it is true, requiring a second review to rediscover it is asking the
machine to re-derive what you can already see. It pools like any promotion, so the evidence trail says
the same thing either way.

`x` is the prune the drafts store never had. Everything else self-limits — facts go with `forget`, which
withdraws the pool line with them — and drafts only ever grew (SPEC §8).

A `pre-review` row carries no count, because a pre-review and the real review are one model on one
diff. It sorts last, and it is still promotable by hand: a person reading it is a second opinion in a
way a second run of the same model is not.

## 5. Writes and pushes

| event | writes | pushes |
|---|---|---|
| review proposes facts | drafts, promotions into `mine`, and the pool | private + team repo |
| `gitdashy remember` | the same drafts, and the pool on promotion | private + team repo |
| `P` → `t` | the team memory file; the pool line stays | team repo |
| `P` → `x` | removes from `mine`, from the team's file when no other backer remains, and from the pool | private + team repo |
| `n` / `g` edit | `mine` only — team memory is not hand-editable from the TUI | private repo |
| `Z` dream | `mine` only, after you approve — the team's are read, never written | private repo |
| review verdict | `reviewed.jsonl` | team repo |
| joining a team | seeds the **log** only | team repo |

**Joining a team no longer copies your memory in.** That was a bulk publish of
every private fact you had, unreviewed, in one action.

**The dream reads team memory and rewrites none of it.** It has to read it, or it
merges your facts into duplicates of theirs, and it is told never to move a line
from `mine/` into `team/` — sharing is your decision, not the model's. What it
proposes for a team's files is dropped: `writable()` keeps the `mine/` half and
`write()` lands only that, so the panel lists your files and says how many of
theirs were read and left alone.

The reason is the asymmetry of §1, applied to the one keypress that deletes. A
dream returned `general.md` empty once and eight facts went with it, recovered
because a backup and a commit exist. The same keypress against a team's file
empties it for everyone, and the backup is on the machine that pressed `y`.

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

**One exception**: if the memory directory sits inside another git repository, no history is
started — nesting a repo there surprises the tooling you already have and is not gitdashy's
call to make. This is not only a `PRS_MEMORY` choice: `git init ~` is a normal dotfiles
setup, and it makes the *default* `~/.prs_memory` nested too. So the dashboard says so, on
the Memory row under `K`:

```
L  Memory   ~/.prs_memory · no history (inside another git repo)
```

The compressed copies below still cover you, and the enclosing repo's own history covers
whatever it tracks. To get git history as well, move memory somewhere outside that repo
with `L`.

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

**A dream that deletes asks twice.** Any file it would empty is marked `→ DELETED`, sorted to the top
of the accept panel, and the footer says how many files accepting destroys. Then a second prompt names
them and the number of facts at stake. The two questions are separate because tidying is what you almost
always want and destroying rode in on the same keypress: eight cross-repo facts went that way on
2026-09-04, as `8 → 0` in a list of line counts.

The one that most needs this: `Z` (dream) applies a model's output verbatim and **deletes any
file it returned empty**. It is one keypress, and before this it was unrecoverable on a default
install. It now takes a backup and a commit first, so accepting a bad dream is a decision you
can walk back.

## 6. What is deliberately *not* done

- **No prompt about one fact.** Promotion into the team is
  automatic as of v1.43.0 (§3.3); what is still deliberately absent is any prompt
  that asks about one fact. The keypresses are the binding and the consent, taken
  once each, so only one threshold (`PROMOTE_AT`) decides anything.
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
   their owner, on forget. gitdashy has no concept of membership — a team
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
   The pool self-prunes on forget, so only drafts grow unboundedly.
4. **`PROMOTE_AT` has data now, and same-machine recurrence never fired.** On one real store:
   140 drafts, every one at `(1)`, one per-repo fact ever confirmed against eight general ones
   (which recur naturally, being about reviewer discipline). Two causes, since separated: the
   matcher could not see a rewording, and a dense architectural fact is stated once or not at
   all. Cross-person corroboration is the answer to the second; the number to watch now is how
   often two people's reviewers land on one fact.
5. **Team memory has no hand-edit path** from the TUI any more — `n`/`g` now edit
   yours. You can still edit the team checkout directly with git.
6. **Nothing here has met a real review yet.** The whole path is test-verified
   only. Numbers from real use should settle issues 1-3.

---
