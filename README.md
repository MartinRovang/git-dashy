<p align="center">
  <img src="logo.png" alt="PR Helper — smarter reviews, better code" width="620">
</p>

<h1 align="center">git-dashy</h1>

<p align="center">Smarter reviews. Better code. — a terminal PR dashboard with a one-key Claude review.</p>

A terminal dashboard for the PRs you actually care about — yours, the ones waiting on your
review, the ones assigned to you — with a one-key Claude review that posts the verdict back to
GitHub.

<p align="center">
  <img src="screenshot.png" alt="github-dashy running in a terminal" width="900">
</p>

## Requirements

- Python 3.9+ (stdlib only — `curses`, no pip install)
- [`gh`](https://cli.github.com) authenticated (`gh auth login`)
- [`claude`](https://claude.com/claude-code) on PATH, for the review feature only

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/MartinRovang/github-dashy/main/install.sh | sh
```

Clones to `~/.github-dashy`, checks out the newest release tag, and links it as `gitdashy` in
`~/.local/bin` (and `prs`, for older installs) (override with `DIR=` / `BIN=`). Re-running it updates in place. Or do it by hand:

```sh
git clone https://github.com/MartinRovang/github-dashy.git ~/.github-dashy
ln -s ~/.github-dashy/prs.py ~/.local/bin/gitdashy
```

## Run

```sh
gitdashy                  # 300s refresh
gitdashy --interval 60
gitdashy --auto           # review every review-requested PR that shows up from now on
gitdashy --model sonnet
gitdashy --effort high --depth adaptive   # claude effort level; review depth judged from the PR size
gitdashy --instructions review-rules.md   # your own text, appended to every review prompt
gitdashy --version        # 1.16.0
gitdashy --demo           # canned PRs, fake reviewer — no gh, no claude, no real log
gitdashy --help
```

MINE rows show GitHub's review decision for your own PRs: `✓ approved`, `✗ changes requested`,
`· awaiting review`, or `↻ re-review requested` when you pushed after a changes-requested and asked
again but the reviewer has not looked yet.

## Keys

| key | what |
|-----|------|
| `j` / `k`, `↑` / `↓` | move |
| `o` | open the PR in your browser |
| `Enter` | on a REVIEW REQUESTED row: Claude reviews it and posts the verdict. On a REVIEWED row: read the summary + review in `less` |
| `a` | toggle auto mode |
| `t` | pick the REVIEWED window: 1h / 4h / 6h / all |
| `Space` | on a REVIEWED row: unfold / fold the older reviews of that PR (stacked under the newest, collapsed by default) |
| `s` | pick summary lines: all / open PRs only / off |
| `?` | show each setting's key next to it in the header |
| `D` | show / hide draft PRs (hidden by default) |
| `m` | pick the model: opus / sonnet / fable |
| `d` | pick review depth: adaptive / low / medium / high |
| `e` | pick claude effort: default / low / medium / high / xhigh / max |
| `i` | pick the refresh interval: 1 / 2 / 5 / 10 / 15 min (the header shows `next refresh Ns / Nm`) |
| `n` | edit this repo's review memory in `$EDITOR` |
| `g` | edit the general review memory in `$EDITOR` |
| `Z` | dream: Claude tidies all memory files (merge, dedupe, drop stale), you approve before anything is written |
| `T` | team setup: share log + memory through a git repo (see Team) |
| `u` | shown when a newer release exists — opens the update panel |
| `r` | refresh now |
| `q` | quit |

`m` `d` `e` `s` `t` `i` open a dropdown under the setting: `j`/`k` or the same key moves, `Enter` picks, `Esc` (or `q`) keeps.
`R` and `V` open the Reviewer and View groups as a menu: `Enter` on a row opens that setting, `Esc` (or `q`) steps back.
`S` opens both groups under one Settings menu. On a narrow terminal the header tightens, then folds the groups
into menu chips (`☰ Reviewer`), then nests them under one `☰ Settings` chip; the same keys work from any of them.

## The review

`Enter` on a review-requested PR runs `claude` headless against `<repo>#<number>`, then posts the
result with `gh pr review` as an **approve**, **request changes**, or **comment**. Reviews are
appended to `~/.prs_reviewed.jsonl` (one JSON object per line) and show up in the REVIEWED section,
where `Enter` opens the summary and full review. A PR that gets a new review request after a verdict
is flagged `↻ re-review · was <verdict>` and can be reviewed again.

`--depth LEVEL` sets how hard the reviewer looks: `low` skims for obvious defects, `medium` reads the
whole diff, `high` also reads the surrounding code and traces callers, and `adaptive` (the default)
lets Claude pick from the size and risk of the diff. `--effort LEVEL` is passed straight to
`claude --effort` (low to max) and controls how much thinking the model spends. `d` and `e` pick
them at runtime; the header's `reviewer` group shows them as `depth <depth>` and `effort <effort>`.

`--instructions FILE` (or `PRS_INSTRUCTIONS`) appends your own text file to the prompt — house
rules, things to always check, what to ignore. It is read fresh for every review, so you can edit it
while the dashboard is running. A missing file shows as `error:` on the row instead of reviewing
without it.

Every REVIEWED row carries a small `depth/effort` tag showing what the review ran with.

### Memory

Reviews remember. Each review may return up to three durable facts about the repo (conventions,
recurring pitfalls, intentional oddities); they are appended to `~/.prs_memory/<owner>__<repo>.md`
and fed back into the prompt for every later review of that repo. `~/.prs_memory/general.md` goes
into every review regardless of repo. Both are plain markdown bullet lists — `n` opens the selected
PR's repo memory and `g` the general one in `$EDITOR`, so you can add, prune or correct freely.
`Z` dreams: Claude reads every memory file, merges duplicates, drops stale or contradictory lines and
moves repo-independent facts to general, then shows a summary and per-file line counts; `v` opens the full summary and diff in `less`. Nothing is
written until you press `y`.

### Team

Press `T` and give a repo (`org/review-team`, private recommended). gitdashy clones it with `gh` into
`~/.prs_team` (`PRS_TEAM` overrides), offers to create it if it does not exist, and copies your current
log and memory in. From then on the review log and all memory files live there: every refresh pulls,
every review or memory edit commits and pushes. Files are appended only and merge with git's union
driver, so two people reviewing at once do not conflict. Everyone on the team sees the same REVIEWED
history, re-review detection works across people, and each teammate's agent learns from everyone's
reviews. The header's `reviewer` group shows `team org/review-team`, or the last git error in red. To leave, delete
the folder.

Auto mode (`a` or `--auto`) does the same thing unattended for every review request that appears
*after* you turn it on. `--auto` also reviews what is already listed; `a` asks whether to include
the ones on screen or leave them as the baseline.

## Versioning & self-update

The version lives in one place — `VERSION` in `prs.py` — and shows in the header badge and via
`gitdashy --version`. Releases are tagged `vX.Y.Z`.

Each refresh also lists the release tags on `origin` (`git ls-remote`, so no `gh` auth and no API
rate limit). If a tag is numerically newer than `VERSION`, the header shows `↑ v1.1.0 · u`; pressing
`u` confirms, checks that tag out, and re-execs the script with the same arguments. You track
releases, not `main`. Non-git installs, no origin, or no network: the badge just never appears.

## Environment

| var | default | what |
|-----|---------|------|
| `PRS_MODEL` | `opus` | model used for reviews |
| `PRS_LOG` | `~/.prs_reviewed.jsonl` | review log path |
| `PRS_EFFORT` | `medium` | `--effort` passed to claude: low, medium, high, xhigh, max |
| `PRS_DEPTH` | `adaptive` | review depth: low (skim), medium, high (very in-depth), adaptive (judged from the diff size) |
| `PRS_TEAM` | `~/.prs_team` | team checkout; team mode is on when it contains a `.git` |
| `PRS_MEMORY` | `~/.prs_memory` | memory directory: `general.md` + one file per repo |
| `PRS_INSTRUCTIONS` | (none) | text file appended to every review prompt; `--instructions` overrides |

## Layout

```
prs.py            entry point — a shim onto the package
dashy/
  cli.py          argv, --help, curses.wrapper
  config.py       tunables and env overrides
  demo.py         canned PRs and a fake reviewer
  ui/             what draws
    screen.py     colours, draw(), the key loop
    art.py        splash: name, spinner, logo
    rows.py       sections -> flat draw rows, age()
  core/           what talks to the outside world
    state.py      background refresh loop, shared state
    github.py     everything that shells out to gh
    review.py     runs Claude headless, posts the verdict
    log.py        ~/.prs_reviewed.jsonl store + detail view
    update.py     release check and self-update
tests/            mirrors dashy/: one test file per module
```

Swappable seams, for adding things: `core.github.fetch`, `core.review.review` and
`core.update.update_available` are looked up as module attributes at call time — that is how `--demo`
replaces all three, and how the tests stay off the network.

## Tests

```sh
python3 -m pytest -q
```

Created by Martin Soria Røvang.
