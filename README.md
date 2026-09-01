<p align="center">
  <img src="logo.png" alt="PR Helper — smarter reviews, better code" width="620">
</p>

<h1 align="center">github-dashy</h1>

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
gitdashy --instructions review-rules.md   # your own text, appended to every review prompt
gitdashy --version        # 1.4.0
gitdashy --demo           # canned PRs, fake reviewer — no gh, no claude, no real log
gitdashy --help
```

## Keys

| key | what |
|-----|------|
| `j` / `k`, `↑` / `↓` | move |
| `o` | open the PR in your browser |
| `Enter` | on a REVIEW REQUESTED row: Claude reviews it and posts the verdict. On a REVIEWED row: read the summary + review in `less` |
| `a` | toggle auto mode |
| `t` | cycle the REVIEWED window: 1h / 4h / 6h / all |
| `s` | cycle summary lines: all / open PRs only / off |
| `m` | cycle model: opus / sonnet / fable |
| `u` | shown when a newer release exists — opens the update panel |
| `r` | refresh now |
| `q` | quit |

## The review

`Enter` on a review-requested PR runs `claude` headless against `<repo>#<number>`, then posts the
result with `gh pr review` as an **approve**, **request changes**, or **comment**. Reviews are
appended to `~/.prs_reviewed.jsonl` (one JSON object per line) and show up in the REVIEWED section,
where `Enter` opens the summary and full review. A PR that gets a new review request after a verdict
is flagged `↻ re-review · was <verdict>` and can be reviewed again.

`--instructions FILE` (or `PRS_INSTRUCTIONS`) appends your own text file to the prompt — house
rules, things to always check, what to ignore. It is read fresh for every review, so you can edit it
while the dashboard is running. A missing file shows as `error:` on the row instead of reviewing
without it.

Auto mode (`a` or `--auto`) does the same thing unattended for every review request that appears
*after* you turn it on — what's already on screen is the baseline and is left alone.

## Versioning & self-update

The version lives in one place — `VERSION` in `prs.py` — and shows in the stats strip and via
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
