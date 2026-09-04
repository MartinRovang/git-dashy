# A starter corpus

A small set of instructions that shape how a coding agent works in your repos. It is
deliberately minimal: enough discipline to be worth loading in every session, short
enough that you will actually read it before agreeing to it.

`gitdashy install --full` installs this one. `--corpus <url>` installs yours instead —
any git repo with an `identity/` directory of markdown and a `USER.md.template` works.

## What is here

| file | what it does |
|---|---|
| `identity/AGENTS.md` | the role: what to establish before changing code, and when to stop |
| `identity/AGENT.md` | conduct: how to work inside that role day to day |
| `identity/RULES.md` | the one rule that is not negotiable |
| `identity/USER.md.template` | who *you* are — blank, for you to fill in |
| `repo-template/` | seeds for a repo's own local notes |

Roughly 1,200 words in total, so about 1,600 tokens in every session. Your own corpus
will be bigger; that is fine, but know what you are loading.

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
