# Presence: who is online, who is reviewing what

## Goal

Two members of a team should not both run Claude on the same PR. The dashboard shows who is online
and which PRs each of them is reviewing right now. Advisory, not a lock: `r` on a claimed row asks
before continuing.

Out of scope: chat, live sync of reviews or memory, anything instant. Presence changes about once a
minute, so it is polled on the refresh tick. Websockets are the upgrade path if live sync is ever
wanted; the server below is where they would bolt on.

## Transport: Tailcat

Maze (the owner's machine) runs the presence server behind Tailcat:

```
tailcat --key=presence serve 8477
```

`--key=presence` saves a long-lived key so the `tc1q...` address is stable across restarts. The
address embeds a pre-shared key, so knowing it is the auth. Members install the `tailcat` binary and
paste the address into gitdashy once.

The dashboard never opens a socket. Once per tick it runs

```
tailcat <address> 8477
```

writes one HTTP/1.0 request to stdin, reads the response from stdout, and the process exits. Same
rules as `team._remote`: bounded timeout, never prompts, failure is a header string not a hang.
Tailcat is built for short-lived connections; one per tick is exactly that.

No TLS. Tailcat is WireGuard end to end and the address embeds the keys, so plain HTTP inside the pipe
is already encrypted and authenticated. The server binds 127.0.0.1 on maze; only Tailcat reaches it.

## Server: `presence_server.py`

One stdlib file at the repo root, run on maze. No dependencies. In-memory:

```
roster = {login: {"at": epoch, "reviewing": ["owner/repo#42", ...]}}
```

Routes:

- `POST /beat` body `{"login": str, "reviewing": [str]}` → records `at = now`, returns roster.
- `GET /` → returns roster.

Roster in responses is filtered to entries seen within `STALE = 180` seconds. Response is JSON:
`{"online": {login: {"reviewing": [...]}}}`. Restart loses state; that is correct for presence.
Bad JSON or missing login → 400. Body over 4 KB → 413. No other validation; login is self-asserted from
`gh` identity and the address is the trust boundary.

## Client: `dashy/core/presence.py`

Module-level state read by the UI, mirroring `team.ERROR` / `team.NAME`:

```
ONLINE = {}   # login -> [pr keys] as last returned by the server
ERROR = ""    # last failure, shown in the header until the next success
```

`beat(reviewing)` sends the heartbeat and replaces `ONLINE`. Called from the refresh tick, when a
review starts, and when it finishes. `reviewing` is `state.running`, the set of in-flight review URLs
the row spinner already reads. Off when `config.PRESENCE` is empty: no subprocess, `ONLINE` stays
empty.

Timeout 15 s. A dead or missing `tailcat` binary, a non-200, or unparseable JSON sets `ERROR` and
leaves `ONLINE` as it was.

`config.PRESENCE` is the Tailcat address, from `PRS_PRESENCE` or the settings file key `presence`. Set at runtime through
a key in the Knowledge group (`W`), which prompts for the address like `L` prompts for a path.

## UI

- Header: `online: bob, alice` chip beside the team name, others only, sorted. Hidden when off or
  empty. `presence: <error>` in the error slot when `ERROR` is set, same place team sync errors go.
- REVIEW REQUESTED rows: `bob reviewing` in the state column when another login has that PR in its
  list. Own reviews are unaffected.
- `r` on such a row: `bob is reviewing this — continue? y/n`. `y` proceeds as today.
- `K` (knowledge panel) shows the presence address or `off`.
- Demo mode fakes a roster with one other member reviewing one row.

## Testing

- `tests/test_presence_server.py`: start the handler on an ephemeral port in a thread, POST two beats,
  GET, assert both present; age one past STALE by patching time, assert it drops; 400 on bad JSON.
- `tests/test_presence.py`: fake `subprocess.run`,
  assert `ONLINE` on success, `ERROR` text and untouched `ONLINE` on failure, no call when off.
- Row rendering: one test that a row claimed by another login shows `<login> reviewing`.

## Not doing

- Locking: advisory only. Two people can still review the same PR if both say `y`.
- Identity: login is whatever `gh` says. The Tailcat address is the only gate.
- Persistence on the server. Presence that survives a restart is stale by definition.
- Websockets. See Goal.
