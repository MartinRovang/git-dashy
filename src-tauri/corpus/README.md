# A starter corpus

A small set of instructions that shape how a coding agent works in your repos. It is
deliberately minimal: enough discipline to be worth loading in every session, short
enough that you will actually read it before agreeing to it.

`gitdashy install --full` installs this one. `--corpus <url>` installs yours instead —
any git repo with an `identity/` directory of markdown and a `USER.md.template` works.

## What is here

| file | what it does |
|---|---|
| `identity/AGENT.md` | the role and how to work in it: what to establish before changing code, when to stop, how to finish and report |
| `identity/RULES.md` | the one rule that is not negotiable |
| `identity/USER.md.template` | who *you* are — blank, for you to fill in |
| `repo-template/` | seeds for a repo's own local notes |

Roughly 1,000 words in total, so about 1,350 tokens in every session. Your own corpus
will be bigger; that is fine — and you do not have to remember what you are loading:
the session hook prints one `[budget]` line at every start with the identity's size and
the repo's `STATE.md`. A corpus that ships `bin/budget-check.sh` has that run instead.

## Making it yours

Fork it, or copy it somewhere and point `--corpus` at that. It is a starting point with
opinions, not a standard — the useful version of this file is the one you have argued
with and changed.

## Two files, two owners

`USER.md` is **yours**: your role, how you work, what you own. It stays on your machine.

What the **team** is building — the objective, who it is for, the constraints that change
what is acceptable — belongs in the team's shared knowledge repo, as `project.md`. Written
once, read by everyone who joins, and given to every review, so a reviewer knows what the
code is *for* before judging whether a change serves it.

That split is the point. Nobody should have to restate the project in their own file, and
nobody's personal preferences should end up in the team's.

`gitdashy` seeds `project.md` with a template when a team repo is created. Fill it in
together, early — it is short, and it changes what reviews notice.
