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
| `identity/USER.md.template` | who you are — blank, for you to fill in |
| `repo-template/` | seeds for a repo's own local notes |

Roughly 1,200 words in total, so about 1,600 tokens in every session. Your own corpus
will be bigger; that is fine, but know what you are loading.

## Making it yours

Fork it, or copy it somewhere and point `--corpus` at that. It is a starting point with
opinions, not a standard — the useful version of this file is the one you have argued
with and changed.

The one part you should fill in immediately is `USER.md`. An agent that knows what you
are building and who it is for makes better calls than one reasoning from the code alone.
