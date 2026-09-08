#!/usr/bin/env bash
# Seed a repo's local notes and point it at its review memory. Idempotent, quiet, fast.
# Registered as a SessionStart hook by `gitdashy install --full`. Nothing it writes is ever committed.
#
# This is CLAUDE CODE SPECIFIC and named so: it knows CLAUDE.local.md and the @import syntax, and it is
# registered in Claude's settings.json. gitdashy ships it rather than the corpus, because three of the
# four things it does are gitdashy's own — only the repo templates belong to whichever corpus you
# installed. Another agent is wired by hand with `gitdashy init --into DIR --loader FILE`, which
# assumes nothing.
set -uo pipefail

CORPUS="${1:-$HOME/.agent-corpus}"   # where install --full puts it; only its repo-template/ is used
git rev-parse --show-toplevel >/dev/null 2>&1 || exit 0
cd "$(git rev-parse --show-toplevel)" || exit 0

# 1. The ignore FIRST, and nothing at all if it cannot be written. .git is a FILE in a linked worktree
#    or a submodule, so the path is asked for rather than assumed; without -e a failure here used to
#    fall through and seed files git could see, which is the one thing this must never do.
GITDIR="$(git rev-parse --git-common-dir 2>/dev/null)" || exit 0
[ -n "$GITDIR" ] || exit 0
case "$GITDIR" in /*) ;; *) GITDIR="$PWD/$GITDIR" ;; esac
mkdir -p "$GITDIR/info" 2>/dev/null || exit 0
EX="$GITDIR/info/exclude"
touch "$EX" 2>/dev/null || exit 0
for p in ".agent/" "CLAUDE.local.md"; do
  grep -qxF "$p" "$EX" 2>/dev/null || echo "$p" >> "$EX" || exit 0
done
grep -qxF ".agent/" "$EX" 2>/dev/null || exit 0  # ponytail: verify, never assume the write landed

# 2. seed this repo's own notes from the installed corpus, if it has any. Never overwriting, and never
#    a problem when it has none — a corpus is free to ship no templates at all.
mkdir -p .agent
for f in STATE.md PROJECT-MEMORY.md; do
  [ -e ".agent/$f" ] || [ ! -f "$CORPUS/repo-template/$f" ] || cp "$CORPUS/repo-template/$f" ".agent/$f" 2>/dev/null || true
done

# 3. the loader. CLAUDE.local.md is local scope; CLAUDE.md is checked in - never use it here.
if [ ! -e CLAUDE.local.md ]; then
  printf '# This repo, local notes (never committed)\n\n@.agent/STATE.md\n@.agent/PROJECT-MEMORY.md\n' > CLAUDE.local.md
fi

# 4. this repo's review memory, if gitdashy is around. --no-pull: a hook has seconds, not a network.
if command -v gitdashy >/dev/null 2>&1; then
  gitdashy init --into .agent/team --loader CLAUDE.local.md >/dev/null 2>&1 || true
  # drafts a review or a session filed for THIS repo that nothing has confirmed. Local store only, so
  # it fits the hook's budget; silent when there are none, or when the repo has no origin to be named
  # by. The count is the pull toward W that was missing.
  # ponytail: CAPPED, like the corpus check below, and for the same reason — except the third party
  # here is gitdashy itself at a version this hook did not ship with. A `gitdashy` that predates
  # --count does not reject the flag, it IGNORES it and prints the whole store: measured at 117 lines
  # and 99 drafts across every repo on this machine, into the context of every session, at every start.
  gitdashy drafts --count 2>/dev/null | head -3 || true
fi
# 5. The one thing this hook says out loud. "Know what you are loading" was a sentence in a README,
#    and a guard that has to be remembered is not a guard; the corpus that shipped this hook grew to
#    twice its stated ceiling before anyone measured. One line, at the moment it is true, in context.
#    A corpus that ships its own check knows its own budgets better — it runs instead, and this step
#    is silent. Never blocks: a budget is information at session start, not a gate.
if [ -x "$CORPUS/bin/budget-check.sh" ]; then
  # ponytail: BOUNDED. This is a third-party script whose stdout lands in the session context at every
  # start — an unbounded pipe let a check that printed 200 lines put 200 of them there, in every repo,
  # forever. A budget is a handful of lines by definition; anything past that is a broken check, and the
  # cap is what stops it costing the session it is reporting on.
  "$CORPUS/bin/budget-check.sh" 2>/dev/null | head -5 | sed 's/^/[budget] /' || true
else
  ID="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/identity"
  tok() { cat "$@" 2>/dev/null | awk '{w+=NF} END{printf "%d", w*1.35}'; }
  line=""
  # ponytail: no identity/ at all is an ABSENCE, and "identity ~0 tok" reports it as a measurement —
  # a reader acts on 0 as though the corpus were loaded and empty. Say nothing rather than say zero.
  # The glob is tested for a real file: unmatched, bash leaves the pattern itself as the argument.
  for f in "$ID"/*.md; do
    [ -f "$f" ] && { line="identity ~$(tok "$ID"/*.md) tok"; break; }
  done
  [ -f .agent/STATE.md ] && line="${line:+$line · }.agent/STATE.md ~$(tok .agent/STATE.md) tok"
  [ -n "$line" ] && echo "[budget] $line"
fi
exit 0
