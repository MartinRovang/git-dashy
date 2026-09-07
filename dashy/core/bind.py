"""Which team a repo belongs to. Declared by the user, never inferred from what they happened to review.

A brief is DECLARED — somebody states what the work is for — so it gets DECLARED selection. The
alternative on the table was the covering rule ("the team's shared review log names this repo"), which
is how memory visibility is decided today, and the two look interchangeable. They are not:

  - a log entry is a SIDE EFFECT of reviewing one PR, so reviewing once silently changes what every
    later review of that repo is told the work is for
  - nothing ever removes a repo from a log, so the decision has no undo
  - two teams can both name a repo and the log gives no way to prefer one
  - nothing on screen says which brief a review is about to get

ponytail: same file shape as install.REGISTRY — one JSON object per line, deduplicated on read, removal
by tombstone. Not a second format invented for a second registry: that one already survived a review
round on appending without a lock and on reading back a file an older version wrote.
"""
import json
import os

from .. import config
from . import team

BINDINGS = os.environ.get("PRS_BINDINGS", os.path.expanduser("~/.prs_bindings"))


def _read():
	try:
		with open(BINDINGS) as f:
			return f.read()
	except OSError:
		return ""  # ponytail: no bindings is the normal state, and an unreadable file must not select a brief


def key(repo):
	"""`repo` as the owner/name this keys on, "" when it is not one.

	ponytail: team.slug_of, so a URL, an ssh remote and a bare owner/name all land on the same key —
	that is what makes a binding survive a re-clone and a move, and two checkouts of one repo agree.
	ponytail: a single segment is REFUSED rather than stored. "notes" would key a row nothing ever
	matches, which reads back as unbound forever with no way to tell it from never having bound it.
	ponytail: lowercased, and ONLY here. This is the one store fed by something a person types — every
	other key in the system arrives as GitHub's own nameWithOwner — so `bind Acme/API` must find the row
	a review of `acme/api` looks up. It is a lookup key, not a filename anyone reads back.
	"""
	# ponytail: a BARE name must be exactly owner/name. team.slug_of keeps the last two segments of
	# anything, so "acme/api/pull/19" — a URL someone trimmed by hand — came back as "pull/19", passed
	# the check below, and bound a repo that does not exist while reporting success. A real URL or path
	# is still handed to slug_of, which is what it is for; the guard is only on the bare form.
	raw = (repo or "").strip().rstrip("/").removesuffix(".git")
	bare = not (raw.startswith(("/", "./", "../", "~")) or "://" in raw or "@" in raw)
	if bare and raw.count("/") != 1:
		return ""
	s = team.slug_of(repo).lower()
	return s if s.count("/") == 1 and all(part for part in s.split("/")) else ""


def owner_key(s):
	"""`s` as an owner name, "" when it is not one. Accepts "acme" and "acme/*"."""
	s = (s or "").strip().rstrip("/").lower()
	s = s[:-2] if s.endswith("/*") else s
	return s if s and "/" not in s and s != "*" else ""


def _entries():
	"""(repos, owners, touched) — live repo→team, live owner→team, and every repo any line has named."""
	repos, owners, touched = {}, {}, set()
	for line in _read().splitlines():
		if not line.strip():
			continue
		try:
			e = json.loads(line)
		except ValueError:
			continue  # ponytail: one unreadable line is not a reason to lose the rest of the file
		if "owner" in e or "forget_owner" in e:
			o = e.get("owner") if "owner" in e else e.get("forget_owner")
			if not isinstance(o, str) or not (o := owner_key(o)):
				continue
			if "forget_owner" in e:
				owners.pop(o, None)
			elif isinstance(e.get("team"), str) and e["team"]:
				owners[o] = e["team"]
			continue
		repo = e.get("repo") or e.get("forget")
		if not isinstance(repo, str) or not (repo := key(repo)):
			continue
		touched.add(repo)
		if "forget" in e:
			repos.pop(repo, None)
		elif isinstance(e.get("team"), str) and e["team"]:
			repos[repo] = e["team"]
	return repos, owners, touched


def bindings():
	"""{repo: team} for every repo bound by name."""
	return _entries()[0]


def owners():
	"""{owner: team} for every owner-wide rule."""
	return _entries()[1]


def _pick(entry, repo):
	"""(kind, slug) for `repo` against one read of the store: ("team"|"owner", slug) or ("", "").

	ponytail: THE precedence, in one place. of(), why() and resolver() all come through here, so the
	rule cannot drift between the value a review uses and the label a screen shows. It reads: an exact
	binding first, then an explicit unbind (which beats a pattern — the one repo in an org that is not
	the project has to be excludable), then the owner rule.
	"""
	repos, owners_, touched = entry
	if not (r := key(repo)):
		return "", ""
	if r in repos:
		return "team", repos[r]
	if r in touched:
		return "", ""
	o = r.split("/")[0]
	return ("owner", owners_[o]) if o in owners_ else ("", "")


def resolver():
	"""A repo -> team function built from ONE read of the store, for a caller resolving many repos.

	ponytail: a draw resolves every visible PR. Calling of() per row would reopen ~/.prs_bindings once
	per row on every keypress, and the answers could differ within a single frame.
	"""
	entry = _entries()
	return lambda repo: _pick(entry, repo)[1]


def of(repo):
	"""The team `repo` is bound to, "" when it is unbound or is not an owner/name."""
	return _pick(_entries(), repo)[1]


def _append(entry):
	"""Add one line. Returns "" or why it could not — never raises.

	ponytail: seed() runs from team.activate(), which runs at startup, inside curses. An unwritable
	~/.prs_bindings — a directory of that name, a read-only home, a full disk — would otherwise unwind
	out of the wrapper and take the whole dashboard down before it drew anything. This program has
	killed a session that way once already, over a path that could not be resolved.
	"""
	try:
		os.makedirs(os.path.dirname(BINDINGS) or ".", exist_ok=True)
		with open(BINDINGS, "a") as f:  # one append; duplicates and reversals are resolved on read
			f.write(json.dumps(entry) + "\n")
	except OSError as e:
		return str(e)
	return ""


def bind(repo, to):
	"""Bind `repo` to team `to`. Returns "" or why it did not."""
	if not (r := key(repo)):
		return f"{repo!r} is not an owner/name"
	if not to:
		return "a binding needs a team"
	if bindings().get(r) == to:
		return ""  # already what was asked for; appending it again buys nothing
	return _append({"repo": r, "team": to})


def forget(repo):
	"""Unbind `repo`, and record that it was unbound on purpose. Returns "" or why it could not.

	ponytail: "" or a reason, like bind() — not a bool for "was it bound". Whether it WAS bound is a
	separate question with its own answer in of(), and a bool cannot distinguish "there was nothing to
	remove" from "the tombstone could not be written", which are opposite outcomes for the caller.
	ponytail: the tombstone is written even when there was nothing to remove, because seed() reads
	`touched` and not the live map. Without it, unbinding a repo that was seeded once gets it seeded
	again at the next startup, and the unbind looks like it did nothing. It is also what excludes one
	repo from an owner rule.
	"""
	if not (r := key(repo)):
		return f"{repo!r} is not an owner/name"
	return _append({"forget": r})


def excluded():
	"""Repos deliberately unbound while an owner rule would otherwise cover them.

	ponytail: listed, or the exclusion is invisible — and "why is this repo not getting the team's
	facts" would have no answer on any surface. A tombstone that nothing displays is the same silent
	selection this whole store exists to remove.
	"""
	repos, owners_, touched = _entries()
	return sorted(r for r in touched if r not in repos and r.split("/")[0] in owners_)


def bind_owner(owner, to):
	"""Bind every repo under `owner` to team `to`. Returns "" or why it did not."""
	if not (o := owner_key(owner)):
		return f"{owner!r} is not an owner"
	if not to:
		return "a binding needs a team"
	if owners().get(o) == to:
		return ""
	return _append({"owner": o, "team": to})


def forget_owner(owner):
	"""Drop the owner-wide rule. Returns "" or why it could not."""
	if not (o := owner_key(owner)):
		return f"{owner!r} is not an owner"
	return _append({"forget_owner": o})


def seed(to, repos):
	"""Bind every repo in `repos` that has never been bound or unbound. Returns what it wrote.

	ponytail: this is the bootstrap, and it is why the covering rule can be dropped rather than kept
	beside this one. Joining a team, or upgrading into a version that has bindings, would otherwise
	leave every repo unbound — so the team's brief silently stops appearing in reviews that had it
	yesterday. Seeding from the shared log reproduces exactly what the covering rule selected, ONCE,
	into a store you can read and change.
	ponytail: `touched`, not `bindings()`. A repo you deliberately unbound has a tombstone and no live
	entry, and seeding off the live map would re-bind it on the next startup forever.
	"""
	if not to:
		return []
	# ponytail: ONE read, reused for every repo. `of(r)` per repo re-opened ~/.prs_bindings once per
	# entry, and with the mirror registry folded in that is len(log) + len(registry) reads at startup,
	# inside curses. _pick against the entry we already hold answers the same question.
	entry = _entries()
	touched = entry[2]
	wrote = []
	for repo in repos:
		# ponytail: the owner rule too, so a repo it already covers is not given a redundant row of its
		# own. Writing one would pin it to whatever team the rule named at seed time, and removing the
		# rule later would leave rows nobody chose repo by repo.
		if (r := key(repo)) and r not in touched and not _pick(entry, r)[1] and not _append({"repo": r, "team": to}):
			touched.add(r)  # ponytail: `repos` may name one repo twice; the file must not
			wrote.append(r)
	return wrote


def team_dir(slug):
	"""The memory directory of the team `slug` names, "" when that team is not on this machine.

	ponytail: a FUNCTION, though today it is one comparison. Multi-team replaces its body and nothing
	else — every caller already asks "which directory does this slug mean" instead of reaching for
	config.TEAM itself, which is the shape that makes 35 call sites into one.
	"""
	# ponytail: folded on BOTH sides. Repo keys are lowercased because they are typed; a team slug is
	# typed too — `--team Org/Mem` while in org/mem resolved to no team dir at all and fell back to your
	# own brief, reporting "not in team Org/Mem" about the team you were sitting in. Compared rather
	# than stored folded, so --list still shows the slug as GitHub spells it.
	return os.path.join(config.TEAM, "memory") if slug and team.on() and slug.lower() == (team.NAME or "").lower() else ""


def team_key():
	"""The slug a binding would name for the team we are in, "" when there is none to bind to."""
	return team.NAME if team.on() else ""
