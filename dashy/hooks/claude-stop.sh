#!/usr/bin/env bash
# Ask a session that fought something to file what it learned. Idempotent, quiet, fast.
# Registered as a Stop hook by `gitdashy install --full`. Nothing it writes is ever committed.
#
# This is CLAUDE CODE SPECIFIC and named so: it knows Claude's Stop-hook JSON on stdin and the
# {"decision":"block"} it answers with. It is a thin adapter ON PURPOSE — every judgement it appears
# to make (what counts as friction, how much is too much, what to say) lives in `gitdashy friction`,
# so a second agent wiring `gitdashy friction --interrupts N --denials N` gets the same answer rather
# than a second opinion. See dashy/core/friction.py for why the policy is only stated once.
set -uo pipefail

# ponytail: OUR OWN entry point, resolved from this script's location — never `gitdashy` off PATH.
# This runs after every session, so a PATH lookup means whatever `gitdashy` happens to come first gets
# executed at every session end; a shadowing binary anywhere earlier on PATH inherits that. The path
# is derived rather than baked in so a moved or re-cloned checkout still works.
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd)" || exit 0
GITDASHY="$HERE/prs.py"

# ponytail: no entry point, no ask. A checkout half-removed is the normal state of a machine mid-
# uninstall, and a hook that fails loudly at the end of every session is a hook the user removes.
# Fails CLOSED: the alternative is falling back to PATH, which is the thing this avoids.
[ -x "$GITDASHY" ] || exit 0

# ponytail: the whole body goes to one call, which parses it, decides, and prints Claude's answer or
# nothing. Reading JSON here in bash would put a second parser in a second language on the path that
# runs after every single session — and the one thing this must never do is fail closed on a stop.
"$GITDASHY" friction --claude-hook 2>/dev/null || true
exit 0
