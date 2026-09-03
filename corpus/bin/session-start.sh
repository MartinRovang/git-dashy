#!/usr/bin/env bash
# Seed a repo's local agent state and point it at its review memory. Idempotent, quiet, fast.
# Registered as a SessionStart hook by `gitdashy install --full`. Nothing it writes is ever committed.
set -uo pipefail

CORPUS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git rev-parse --show-toplevel >/dev/null 2>&1 || exit 0
cd "$(git rev-parse --show-toplevel)" || exit 0

# 1. local ignore FIRST, so nothing is ever briefly visible to git
EX=.git/info/exclude
mkdir -p .git/info && touch "$EX"
for p in ".agent/" "CLAUDE.local.md"; do
  grep -qxF "$p" "$EX" 2>/dev/null || echo "$p" >> "$EX"
done

# 2. seed this repo's own notes, never overwriting
mkdir -p .agent
for f in STATE.md PROJECT-MEMORY.md; do
  [ -e ".agent/$f" ] || cp "$CORPUS/repo-template/$f" ".agent/$f" 2>/dev/null || true
done

# 3. the loader. CLAUDE.local.md is local scope; CLAUDE.md is checked in - never use it here.
if [ ! -e CLAUDE.local.md ]; then
  printf '# This repo, local notes (never committed)\n\n@.agent/STATE.md\n@.agent/PROJECT-MEMORY.md\n' > CLAUDE.local.md
fi

# 4. this repo's review memory, if gitdashy is around. --no-pull: a hook has seconds, not a network.
command -v gitdashy >/dev/null 2>&1 && \
  gitdashy init --into .agent/team --loader CLAUDE.local.md >/dev/null 2>&1 || true
exit 0
