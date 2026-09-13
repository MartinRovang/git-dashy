# Team presence: plan

Status: design agreed, not started. Parked on `feat/team-presence`.

## Goal

See in gitdashy which members of your team are online right now.

## Design

Two parts:

- **Server (Python, lives on maze, not in this repo).** One stdlib file, `~/presence/server.py`,
  on `http.server`. Listens on localhost; `cloudflared` tunnels a hostname to it, so maze opens no
  port. Runs as a systemd service.
- **Client (Rust, in gitdashy).** Heartbeat from the existing refresh loop, dots in the sidebar.

### Protocol

```
POST https://presence.<domain>/beat
Authorization: Bearer <team secret>
{"team": "<team key>", "user": "<github login>"}

200 {"online": ["martin", "ola"]}
401 on a wrong secret
```

- One call both says "I'm here" and returns who else is, so there is no separate GET.
- Server state: in-memory `team -> {user -> last_seen}`. No database; a restart empties it and
  everyone is back within one beat.
- Client beats every 30s. The server counts a user online while `last_seen` is within 90s (three
  missed beats, so one slow request does not flicker anyone offline).

### Config

The team's `team.json` gets:

```json
"presence": {"url": "https://presence.example.com", "secret": "..."}
```

- It is in the team repo, so everyone who can clone the team gets it. Git access is the membership
  check.
- No `presence` key means no beats: solo users and `--demo` send nothing.
- The login sent is unverified, which is acceptable for a presence dot. Sending GitHub tokens to
  the server was rejected: it would hand maze everyone's `repo`-scoped token.

### Client

- `team.rs`: read `presence` off `info_raw`, through the same cleaning other team.json fields get.
- A beat per joined team with presence, on its own 30s timer (the board refresh is 300s by default),
  with `ureq` (already a dependency). A failure greys the dots; it never raises an error.
- `web.rs` payload: `teams[].online: string[]`.
- `Sidebar.tsx`: `team: acme ● martin, ola`.

### Why HTTP and not WebSocket

WebSocket only improves how fast someone shows as offline (instant versus about 90s). It would add a
dependency and reconnect logic on both sides, and the dashboard refreshes every 300s anyway.

## Open questions

- **Offline teammates.** Default: show only who is online. Alternatives: the server remembers
  everyone it has seen (`kari (2h ago)`, a small JSON file on maze), or `team.json` lists members.
- Hostname under which the tunnel is exposed.

## Tests

- Rust: a unit test that the beat body and parsing round-trip against a stub `tiny_http` server,
  and that a team without `presence` sends nothing.
- Server: an `assert`-based `__main__` self-check for expiry (a beat older than 90s drops out) and
  the 401 on a wrong secret.
