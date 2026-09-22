SPAGHETTI AUDIT — RULEBOOK v1.3
(Consolidated and renumbered. Supersedes all earlier drafts.)

PURPOSE
Score repositories for maintainability, readability and traceability.
Working is not the bar. The bar: every line is understood, deterministic
and maintainable by someone who didn't write it.

Core rule: when a check objects, fix the cause. Never silence the check.

SCOPE
Platform:     all checks apply in full.
Landing page: F17 is a warning, not a fail. All other checks unchanged.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FAIL CHECKS
Each instance = 1 fail. Any fail blocks merge.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Hacks
F01 Silenced check — a safety or verification check switched off
    instead of satisfied: certificate validation, type checking,
    linting, warnings, compiler errors.
    → Search for every suppression marker and bypass flag. Each hit counts.
F02 Catch-all type — a type that accepts anything, used to avoid
    defining the real one. External data is parsed into a defined type
    at the boundary.
    → Review signatures and data structures.
F03 Swallowed error — an error caught and dropped, replaced with a
    default, or turned into success.
    → Review every error-handling site.
F04 Hidden failure — a fallback or retry that masks a failure instead
    of surfacing it. Idempotency checks are exempt.
    → Review every fallback path and every loop around a fallible call.
F05 Unfinished code — placeholders, stubs or "not implemented" paths
    reachable in production.

Non-determinism (same input must give same output)
F06 Hidden global mutable state.
F07 Time, randomness or IDs generated inside computation instead of
    passed in from the boundary.
F08 Output or behaviour depends on unordered iteration.
F09 Concurrency without an owner and a cancellation path.
F10 Sleeps, delays or timing tricks used to fix ordering.

Structure
F11 Circular dependency between modules or components.
    → Generate the dependency graph.
F12 Duplicated state — state that mirrors other data, or exists only
    to keep other state in sync.
F13 Chained side effects — a side effect changes state that triggers
    another side effect.

Observability
F14 Error path or external call (DB, storage, HTTP, IPC) without a log.
F15 Log line deleted or reworded as part of an unrelated change.
    → Review diffs in the audit period.

Ownership
F16 Author cannot explain their code.
    → Explain test (see procedure).

Frontend
F17 Frontend does something smart. One instance = fail. (Regulatory.)
    Smart means any of:
    - Computation: totals, scores, derived numbers
    - Validation logic in JavaScript (see below)
    - Decision-making: thresholds, status rules, business conditions,
      permissions
    - Data transformation: sorting, filtering, grouping, reshaping or
      converting server data — including user-driven sorting and
      filtering. Use URL parameters and let the server do it.
    - Optimistic updates (the client computes the result)
    Allowed:
    - UI-only concerns: open/closed, hover, focus, layout
    - Rendering what the server sent, including choosing which
      component to show from a server-provided value
    - Cache invalidation after a mutation (it asks the server again)
    Rendering a decision is fine. Making one is a fail.

    Validation:
    - Native HTML attributes (required, type, min/max, minlength/
      maxlength, step, pattern) are allowed as UX feedback.
    - Any value that encodes a business rule (limits, ranges,
      patterns) must come from the server, never be hardcoded.
    - The server validates and returns field-level errors;
      the frontend renders them.
F18 Effect used to fetch data. Use server components, server actions
    or a data-fetching library.
F19 Effect used to compute derived values.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WARNING CHECKS
Each instance = 1 warning. Must be flagged and justified in the PR.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Size and shape
W01 File or component over 600 lines.
W02 Function doing more than one thing.
    → It can't be described in one sentence without "and".
W03 Data passed down more than 2 levels.
W04 Implicit coupling — modules that must change together with no
    explicit contract between them.

Abstraction
W05 Logic duplicated 3+ times without extraction.
W06 Shared helper or component with fewer than 3 real uses.
W07 Wrapper that only passes things through.
    Dispatchers that route by condition are exempt.
W08 Design pattern with a single implementation, or without a stated need.
W09 Options, flags or extension points built for hypothetical needs.
W10 Dead or commented-out code.

Purity and modularity
W11 Impure function where a pure one was possible.
W12 I/O mixed into computation instead of kept at the boundary.
W13 Public surface larger than what other modules actually use.
W14 Shared state without a written justification.
W15 Defensive checks or validation not required by the spec.
    Idempotency checks are exempt.

Observability
W16 Non-trivial work or a state transition without entry/exit or
    transition logs.

Frontend — state
W17 Component state holding non-UI data. Only UI concerns (open/closed,
    hover, focus) live in component state; everything else comes from
    props, the server or the URL.
W18 State that should survive a refresh kept in component state
    instead of the URL.

Frontend — simplest approach
W19 Client component without a genuine need (browser API, event handler
    or UI-only state). Server components by default.
W20 Navigation via onClick where a link would work.
W21 State + handler where a form action would work.
W22 Mutation via manual API call + state instead of a Server Action,
    where one applies.
W23 Hand-managed loading state instead of Suspense/streaming.
W24 Custom element where a native element would work.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KLOC = non-blank, non-comment source lines in audited code ÷ 1000.
Excludes tests, generated code and vendored code.

Accumulation: every module (directory/package) with 3+ warnings
adds 1 fail.

F = fails ÷ KLOC        (including accumulation fails)
W = warnings ÷ KLOC

Score = max(0, 100 − 50×F − 10×W)

Grade
A   score ≥ 90 and zero fails
B   score ≥ 75
C   score ≥ 50
D   score < 50

Gate: release-ready only with zero fails, whatever the score.

Weights and cutoffs are starting points. Recalibrate after the first
2–3 audits and record the change as a new rulebook version.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROCEDURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Pin — record repo, commit hash and rulebook version.
2. Scope — mark each audited area as platform or landing page.
   Under 20 KLOC: audit everything. Otherwise: every module changed in
   the last 90 days plus two picked at random. List them.
3. Mechanical pass — run the type checker and linters in strictest
   mode, a dependency graph and a duplication scan. Map every hit to a
   check ID.
4. Manual pass — review every error-handling site, external call,
   fallback, concurrency point and frontend data flow in scope.
5. Explain test — per active author, pick 3 random functions from their
   last 90 days of commits. They explain each without notes or tools.
   Any failure = F16.
6. Record every finding:
   Check ID | Module | File:line | One-sentence description
7. Compute score and grade. Report: commit, KLOC, fails, warnings,
   score, grade, three worst modules.
8. Re-audit quarterly and before every release. Track the trend.

Disputes: the rule text decides. If the rule text is ambiguous, fix it
in a new rulebook version. Never decide case by case.