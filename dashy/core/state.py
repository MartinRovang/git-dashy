"""Background refresh loop and everything the UI reads."""
import logging
import os
import pathlib
import subprocess
import threading
import time

from .. import config
from . import diff, github, heartbeat, install, log, memory, mirror, review as review_mod, team, update

LOG = logging.getLogger(__name__)


def _evict(cache, drop):
	"""Drop every entry the predicate names, so one PR keeps one entry — not one per push or review."""
	for k in [k for k in cache if drop(k)]:
		del cache[k]


def in_flight(state, url):
	"""True while a review or pre-review of this PR is running.

	ponytail: membership, not a suffix. The status channel also carries a truncated stderr line, so
	sniffing for "..." meant an error whose last line happened to end in one would pin the UI at a 50ms
	timeout, count as a running agent, block the key from ever retrying that PR, and survive the stale
	sweep — permanently, on wording nobody controls. The set is written by the two functions that start
	work and cleared by the two that finish it; nothing has to be parsed.
	"""
	return url in state.running


def refresh_mirrors():
	"""Re-mirror every repo `gitdashy init` registered. Never raises: a bad entry must not stop a refresh."""
	for into, repo, root, _loader in install.registered():
		# ponytail: refresh what is THERE. init creates the mirror, so a refresh never has cause to make
		# one — and makedirs would otherwise rebuild the tree of a repo you deleted and write memory
		# back into it. Asking about `into` itself needs no recorded root and holds for any --into shape.
		if not os.path.isdir(into) or (root and not os.path.isdir(root)):
			install.unregister(into)
			continue
		try:
			mirror.sync(into, repo, pull=False)  # already pulled above; and this must not touch the network
		except Exception:  # noqa: BLE001 — a stale registry entry is not worth losing the refresh loop over
			LOG.exception("mirror sync %s failed", into)
			continue


class State:
	def __init__(self, interval=config.INTERVAL, model=config.DEFAULT_MODEL):
		self.interval, self.sections, self.fetched_at, self.lock = interval, [], None, threading.Lock()
		# ponytail: since this dashboard STARTED, and not persisted. The question a badge answers is
		# "has anything landed that I have not looked at", and a dashboard the operator leaves running
		# for days is exactly where that goes unnoticed. Persisting it would mean a file that changes
		# whenever the team does, inside a directory push_dir commits, for a number that is only ever
		# read by the header of the process that wrote it.
		self.arrived = {}
		self.model = model
		self.sweeping = threading.Event()  # ponytail: one draft sweep at a time; see start_sweep
		self.wake, self.reviews = threading.Event(), {}  # reviews: url -> status string
		self.running = set()  # urls with a review or pre-review in flight — see in_flight()
		self.started_at = {}  # url -> when it started, so a row in flight can say how long it has been
		# ponytail: the PR's updatedAt when a finished status was last seen. A verdict describes one
		# revision; once the PR moves, it is stale and must stop masking what GitHub now says.
		self.seen_at = {}
		self.done_at = {}  # url -> when a status landed, so an older in-flight fetch cannot baseline it
		self.auto, self.auto_baseline = False, None  # baseline: RR urls present when auto was switched on
		# ponytail: main's persisted settings win over the branch's hardcoded defaults — config.WINDOW
		# and friends came later and are the whole point of the settings file.
		self.window, self.subs, self.drafts = config.WINDOW, config.SUB, config.DRAFTS
		self.details, self.detailing = {}, set()  # url -> detail dict, and the ones in flight
		self.diffs, self.diffing = {}, set()  # (repo, number, head, findings) -> (files, marks), and in flight
		self.pane = True  # the detail pane, toggled with ⏎
		# ponytail: which face of the pane, and how much of the diff. Both live on State rather than in
		# the draw, so moving between rows keeps where you were — a review you are reading line by line
		# should not snap back to the summary because you glanced at the row above.
		self.pane_tab = "summary"  # "summary" | "code"
		self.code_scope = "marks"  # "marks" | "diff" — only the marked hunks, or the whole change
		self.code_at = 0  # which mark n/N is on
		self.code_pr = ""  # the PR code_at counts marks in; moving row resets the jump
		self.code_context = diff.CONTEXTS[0]  # lines kept either side of a marked line; c cycles it
		self.expanded = set()  # REVIEWED urls with older reviews unfolded (space toggles)
		self.hints = False  # ? toggles: show each setting's key next to it in the header
		self.update = ""  # newer released version, refreshed with each fetch
		self.fetching = False
		self.error = ""  # why the last refresh failed, "" while ticks are landing — see loop()
		self.known = None  # urls wanted from me at the last fetch; None until the first fetch lands

	def want_detail(self, pr):
		# ponytail: keyed by (url, updatedAt), not url. A PR that moved has a different branch head, diff
		# size and CI result, and the pane kept showing the old ones until a restart — the same staleness
		# the pre-review path had, for the same reason: caching what changes as though it does not.
		"""The selected PR's detail, or None while it is being fetched.

		ponytail: fetched once per PR, off the draw thread. draw() runs 20 times a second — anything that
		talks to the network from there would stutter the whole dashboard on every keypress.
		"""
		if pr is None:
			return None
		key = (pr["url"], pr.get("updatedAt", ""))
		with self.lock:
			if key in self.details:
				return self.details[key]
			if key in self.detailing:
				return None
			self.detailing.add(key)
		def run():
			got = github.detail(pr["repository"]["nameWithOwner"], pr["number"])
			with self.lock:
				# ponytail: drop what we knew about this PR at any other revision, so the cache cannot
				# grow one entry per push for a branch someone is iterating on.
				_evict(self.details, lambda k: k[0] == key[0])
				self.details[key] = got
				self.detailing.discard(key)
		threading.Thread(target=run, daemon=True).start()
		return None

	def want_diff(self, repo, number, head, findings):
		"""(files, marks) for one PR's diff, or None while it is being read.

		ponytail: off the draw thread for the same reason want_detail is — `gh pr diff` is a subprocess
		and draw() runs 20 times a second, so a slow one stuttered the whole dashboard and a timing-out
		one froze it. It ANCHORS here too, not in the pane: anchor() tags the lines it marks, so calling
		it twice over one parse appends every note twice, and the pane redraws constantly.
		"""
		if not repo or number is None:
			return None
		# ponytail: the findings are part of the key. A re-review changes what is marked without moving
		# the head, and the pane would have gone on showing the previous round's marks.
		sig = tuple((f.get("kind"), f.get("loc"), f.get("text")) for f in findings)
		# ponytail: the GENERATION is in the key. Without it f cleared diff._CACHE while this cache went
		# on answering from the failed read above it, so one gh blip pinned "no diff to show" for the
		# rest of the session — the retry reached the layer nobody was asking.
		key = (repo, number, head, sig, diff.generation())
		with self.lock:
			if key in self.diffs:
				return self.diffs[key]
			if key in self.diffing:
				return None
			self.diffing.add(key)
		def run():
			got = diff.load(repo, number, head, findings)
			with self.lock:
				_evict(self.diffs, lambda k: k[:2] == (repo, number))
				self.diffs[key] = got
				self.diffing.discard(key)
		threading.Thread(target=run, daemon=True).start()
		return None

	def set_auto(self, on, include_existing=False):
		"""include_existing: review what is already listed too, not just what shows up later."""
		with self.lock:
			self.auto = on
			self.auto_baseline = None if not on else set() if include_existing else set(self._rr_urls())
		if on and include_existing:
			self.wake.set()  # refetch now so the listed PRs start without waiting for the next tick

	def pending_rr(self):
		"""Review-requested PRs with no verdict or review in flight."""
		with self.lock:
			return [u for u in self._rr_urls() if u not in self.reviews]

	def _rr_urls(self):
		return [p["url"] for name, prs, _ in self.sections if name == "REVIEW REQUESTED" for p in prs or []]

	def start_review(self, pr):
		model = self.model
		def run():
			# ponytail: the row spins until this thread writes a status, so an exception review() does not
			# catch would leave it spinning for the rest of the session with nothing to press. Catch here
			# too, and the row says what happened.
			LOG.info("review %s with %s", pr["url"], model)
			try:
				status = review_mod.review(pr, model)  # module attr: --demo and tests swap it
			except Exception as e:
				LOG.exception("review %s failed", pr["url"])
				status = f"error: {e}"[:88]
			LOG.info("review %s -> %s", pr["url"], status)
			with self.lock:
				self.reviews[pr["url"]] = status
				self.running.discard(pr["url"])
				self.seen_at.pop(pr["url"], None)
				self.started_at.pop(pr["url"], None)
				self.done_at[pr["url"]] = time.time()
			self.wake.set()  # refetch so an approved PR drops off the list
		with self.lock:
			self.reviews[pr["url"]] = "reviewing..."
			self.running.add(pr["url"])
			self.started_at[pr["url"]] = time.time()
		threading.Thread(target=run, daemon=True).start()

	def start_self_review(self, pr):
		"""Pre-review one of MY PRs. Posts nothing; the file it writes is found again by its name."""
		model = self.model
		def run():
			try:  # ponytail: same reason as start_review — a dead thread must not wedge the row
				status, _dest = review_mod.self_review(pr, model)  # module attr: --demo and tests swap it
			except Exception as e:
				LOG.exception("self-review %s failed", pr["url"])
				status = f"error: {e}"[:88]
			LOG.info("self-review %s -> %s", pr["url"], status)
			with self.lock:
				self.reviews[pr["url"]] = status  # ponytail: the path is not kept — it is derivable
				self.running.discard(pr["url"])
				self.seen_at.pop(pr["url"], None)
				self.started_at.pop(pr["url"], None)
				self.done_at[pr["url"]] = time.time()
			self.wake.set()
		with self.lock:
			self.reviews[pr["url"]] = "pre-reviewing..."
			self.running.add(pr["url"])
			self.started_at[pr["url"]] = time.time()
		threading.Thread(target=run, daemon=True).start()

	def loop(self):
		"""Refresh forever. A tick that raises is reported and retried; it never ends the thread.

		ponytail: the guard is here rather than on each call this makes, because the ways a tick can
		raise are not enumerable — it reads a log other machines append to and merge, a registry, a
		settings file and four subprocesses. One unreadable line in the review log used to unwind out of
		this thread's run(), and nothing restarts it: fetching stayed True, no key reaches a thread that
		is gone, and f only sets an event nobody waits on any more. The dashboard was over, silently.
		"""
		while True:
			t0 = time.time()
			try:
				self.tick(t0)
				base = self.fetched_at  # ponytail: the fetch's own time, so the header's countdown agrees
			except Exception as e:  # noqa: BLE001 — the whole point: a failed tick is a row, not the end
				LOG.exception("tick failed")
				with self.lock:
					self.fetching = False
					self.error = (str(e).strip().splitlines() or [type(e).__name__])[-1][:60]
				# ponytail: from THIS attempt, not from fetched_at. That holds the last SUCCESSFUL fetch,
				# so the moment one tick failed the deadline was already in the past — the loop fell
				# straight out of the wait and retried every second, hammering gh for as long as the
				# failure lasted. It also covers the FIRST tick, where fetched_at is still None.
				base = t0
			# ponytail: `base + self.interval` is evaluated per slice, and `interval` is the reason.
			# Hoisting it into a variable above the loop is the natural way to write this and silently
			# breaks `i`: settings()["i"] assigns state.interval and does NOT set state.wake, so
			# dropping 30m to 1m waited out the remaining 29 instead of refetching now.
			while not self.wake.wait(1) and time.time() < base + self.interval:
				pass  # 1s slices, so a change to either side takes effect within the second
			self.wake.clear()

	def start_sweep(self):
		"""Pool and cross-check drafts on a thread of their own. Returns at once; never raises.

		ponytail: NOT on the refresh thread. It was, immediately after the pull and before
		`github.fetch()`, with `fetching` already true and a 300s model timeout — so the first sweep
		after an upgrade, with a whole backlog newly poolable, blocked the PR list for as long as the
		model took. Catching the exception was never the risk; the wait was. The comment claimed the
		list must not depend on a model and the code put one in front of it.
		ponytail: one at a time. A slow sweep must not have a second one started on top of it by the
		next tick, both writing the same pool files and both pushing the same checkout.
		"""
		if self.sweeping.is_set():
			return
		self.sweeping.set()
		def run():
			try:
				memory.sweep(self.model)
			except Exception:  # noqa: BLE001 — surfaced in the debug log, never on the header
				LOG.exception("draft sweep failed")
			finally:
				self.sweeping.clear()
		threading.Thread(target=run, daemon=True).start()

	def take_arrivals(self):
		"""What has arrived since anyone last looked, and forget it. Under the lock: the refresh thread
		adds to this while the UI reads it, and an arrival landing between a copy and a clear was
		silently dropped."""
		with self.lock:
			got = dict(self.arrived)
			self.arrived.clear()
			return got

	def tick(self, t0):
		"""One refresh: pull, mirror, fetch, sweep stale verdicts, start auto reviews, notify."""
		LOG.debug("tick")
		with self.lock:  # ponytail: the failure path clears this under the lock; both sides now agree
			self.fetching = True
		# ponytail: FIRST, and before the pull it is a claim about. Anything reading this while the tick
		# runs should be told a dashboard is here, or it does the pull itself — which is the one thing
		# the beat exists to prevent. Written every tick rather than once at startup, so a dashboard
		# that stopped refreshing (suspended, wedged) stops counting as one.
		heartbeat.beat(self.interval)
		# ponytail: the lines around the pull, with your own facts excluded — see memory.arrivals.
		# ponytail: the snapshot must not be able to cost the pull. _read raises on anything but a
		# missing file, so one unreadable team file — a permission, a half-written merge, a non-UTF-8
		# byte a teammate pushed — would have skipped that tick's git entirely, for the sake of a
		# badge. The badge is the optional half.
		# ponytail: ValueError as well as OSError. UnicodeDecodeError is a ValueError, so `except
		# OSError` alone missed exactly the case a team-pushed file produces, which is the one that
		# arrives without anybody on this machine doing anything.
		try:
			was = {k: memory.team_lines(k) for k in team.joined()}
		except (OSError, ValueError):
			LOG.exception("could not count team facts before the pull")
			was = {}
		# ponytail: the tick takes the LOCK too. It used to beat and then pull unguarded, on the
		# argument that a beat is enough — but a hook sync that claimed the lock while no dashboard was
		# up keeps pulling after one starts, and the first tick then rebased the same checkout beside
		# it. "Only one process ever pulls a given checkout" was in the README and was not true.
		# ponytail: the lock is not waited for. A tick that cannot have it skips the pull and takes the
		# next one — the alternative is blocking the refresh thread, and everything after this line
		# (the PR list, the mirrors) has nothing to do with the team's git.
		if heartbeat.claim():
			try:
				team.pull()  # newest team log + memory before we read them
			finally:
				heartbeat.unclaim()
		# ponytail: guarded on THIS side too. The pull is what brings in the unreadable file, so the
		# read after it is the likelier of the two to meet one — and everything below here, the sweep,
		# the mirrors and the PR list, was riding on a counter.
		try:
			grew = {key: n for key, before in was.items() if (n := memory.arrivals(key, before))}
		except (OSError, ValueError):
			LOG.exception("could not count what the pull brought")
			grew = {}
		if grew:
			with self.lock:  # ponytail: read on the UI thread, like every other cross-thread field
				for key, n in grew.items():
					self.arrived[key] = self.arrived.get(key, 0) + n
		self.start_sweep()
		memory.history()  # ponytail: before the backup, so the first commit is memory as it arrived —
		memory.backup("tick")  # and so the Memory row can say "no history" before a write, not after
		refresh_mirrors()  # ponytail: here, not in a session hook — no global config, no timeout budget
		data = github.fetch()
		stale = log.mark_rereviews(data)
		newer = update.update_available()
		if self.fetched_at is None:
			time.sleep(max(0, config.SPLASH_MIN - (time.time() - t0)))  # let the splash breathe on the first load
		# ponytail: beaten AGAIN, at the end. The first beat is stamped at tick start and alive() allows
		# one interval plus GRACE, but the next tick starts at fetched_at + interval — so any tick
		# longer than GRACE made a healthy dashboard read as dead until it came round again, and one
		# slow team pull is enough at a 120s git timeout. So the tick beats again at the end.
		heartbeat.beat(self.interval)
		with self.lock:
			self.sections, self.fetched_at, self.update, self.fetching = data, time.time(), newer, False
			self.error = ""  # this tick landed, so whatever the last one said is over
			for u in stale:  # forget the old verdict so r / auto can review the new push
				# ponytail: same guard as the sweep below. `stale` is read off the REVIEWED section of
				# THIS fetch, so a fetch that started before our verdict landed still holds the previous
				# entry, calls the head we just reviewed a new push, drops the verdict — and auto starts
				# the identical review a second later.
				if not in_flight(self, u) and self.done_at.get(u, 0) <= t0:
					self.reviews.pop(u, None)
					self.seen_at.pop(u, None)
			# ponytail: and forget it for ANY row whose PR has moved since. `stale` comes from
			# log.mark_rereviews, which only ever names REVIEW REQUESTED urls — so a finished
			# pre-review masked GitHub's decision on a MINE row until restart, and a colleague
			# approving your PR never showed. One rule for both: a verdict describes one revision.
			# ponytail: baselined on the first fetch that STARTED after the work finished. Posting a
			# review bumps updatedAt itself, so the value we held is already stale — and a fetch that
			# was in flight when the verdict landed carries the pre-post value, which is why the
			# start time is compared rather than merely "the next fetch".
			for name, prs, _err in data:
				if name == "REVIEWED":
					# ponytail: not a live row. Its updatedAt is the log timestamp, which never equals
					# the live row's value, so sweeping it dropped every verdict on the next tick.
					continue
				for p in prs or []:
					u = p["url"]
					if u not in self.reviews or in_flight(self, u):
						continue
					# ponytail: .get, not a subscript. A KeyError here runs on the refresh thread and
					# takes the whole loop down; a PR without the field simply never goes stale.
					# ponytail: by head on a REVIEW REQUESTED row, like mark_rereviews. updatedAt there
					# comes from the lagging search index — the tick after a verdict can still read the
					# pre-post value and the next the post-post one — and a reply on the thread bumps
					# it too; both dropped the verdict and auto reviewed the same head again. On MINE
					# rows updatedAt stays: a colleague's approval must be allowed to unmask GitHub.
					if name == "REVIEW REQUESTED":
						at = p.get("head")  # no head means the graphql call failed; not a reason to call it moved
					else:
						at = p.get("updatedAt")
					if at is None:
						continue
					if self.done_at.get(u, 0) > t0:
						# ponytail: this fetch STARTED before the work finished, so it carries the
						# pre-post updatedAt. Baselining on it would sweep our own verdict on the
						# next tick — which the comment below claimed to avoid and did not.
						continue
					if u not in self.seen_at:
						self.seen_at[u] = at
					elif self.seen_at[u] != at:
						self.reviews.pop(u, None)
						self.seen_at.pop(u, None)
			new = [p for name, prs, _ in data if name == "REVIEW REQUESTED" for p in prs or []
			       if self.auto and p["url"] not in self.auto_baseline and p["url"] not in self.reviews] if self.auto else []
		for p in new:
			self.start_review(p)
		asks = [(name, prs, err) for name, prs, err in data if name in ("REVIEW REQUESTED", "ASSIGNED")]
		if not any(err for _, _, err in asks):  # a failed section would look like every PR left, then came back
			wanted = {p["url"]: (p, name) for name, prs, _ in asks for p in prs}
			if self.known is not None and config.NOTIFY:
				for u in wanted.keys() - self.known:
					notify(*wanted[u])
			self.known = set(wanted)


def notify_cmd(pr, section):
	"""The notify-send argv for a PR that just asked for me. Raises on a payload missing a field."""
	what = "wants a review" if section == "REVIEW REQUESTED" else "assigned you"
	return ["notify-send", "-a", "gitdashy", "-u", "normal", "-c", "im.received", "-A", "open=Open PR",
	        "-i", str(pathlib.Path(__file__).parents[1] / "notify.png"),
	        f'#{pr["number"]} {pr["title"]}', f'<b>{pr["repository"]["name"]}</b> · {pr["author"]["login"]} {what}']


def notify(pr, section):
	"""Desktop popup with an Open button. Silent if notify-send is missing or the payload is odd (deleted author)."""
	def run():  # ponytail: -A blocks until dismissed, so wait in a thread; notify-send only (Linux)
		try:
			if subprocess.run(notify_cmd(pr, section), capture_output=True, text=True).stdout.strip() == "open":
				github.open_in_browser(pr["url"])
		except (OSError, KeyError, TypeError):
			pass  # a popup is decoration; the refresh loop must outlive it
	threading.Thread(target=run, daemon=True).start()
