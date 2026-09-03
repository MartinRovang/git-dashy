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
command -v gitdashy >/dev/null 2>&1 && \
  gitdashy init --into .agent/team --loader CLAUDE.local.md >/dev/null 2>&1 || true
exit 0
