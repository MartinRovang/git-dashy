"""Background refresh loop and everything the UI reads."""
import os
import pathlib
import subprocess
import threading
import time

from .. import config
from . import github, install, log, memory, mirror, review as review_mod, team, update


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
			continue


class State:
	def __init__(self, interval=config.INTERVAL, model=config.DEFAULT_MODEL):
		self.interval, self.sections, self.fetched_at, self.lock = interval, [], None, threading.Lock()
		self.model = model
		self.wake, self.reviews = threading.Event(), {}  # reviews: url -> status string
		self.running = set()  # urls with a review or pre-review in flight — see in_flight()
		# ponytail: the PR's updatedAt when a finished status was last seen. A verdict describes one
		# revision; once the PR moves, it is stale and must stop masking what GitHub now says.
		self.seen_at = {}
		self.done_at = {}  # url -> when a status landed, so an older in-flight fetch cannot baseline it
		self.auto, self.auto_baseline = False, None  # baseline: RR urls present when auto was switched on
		# ponytail: main's persisted settings win over the branch's hardcoded defaults — config.WINDOW
		# and friends came later and are the whole point of the settings file.
		self.window, self.subs, self.drafts = config.WINDOW, config.SUB, config.DRAFTS
		self.details, self.detailing = {}, set()  # url -> detail dict, and the ones in flight
		self.pane = True  # the detail pane, toggled with ⏎
		self.expanded = set()  # REVIEWED urls with older reviews unfolded (space toggles)
		self.hints = False  # ? toggles: show each setting's key next to it in the header
		self.update = ""  # newer released version, refreshed with each fetch
		self.fetching = False
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
				for k in [k for k in self.details if k[0] == key[0]]:
					del self.details[k]
				self.details[key] = got
				self.detailing.discard(key)
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
			status = review_mod.review(pr, model)  # module attr: --demo and tests swap it
			with self.lock:
				self.reviews[pr["url"]] = status
				self.running.discard(pr["url"])
				self.seen_at.pop(pr["url"], None)
				self.done_at[pr["url"]] = time.time()
			self.wake.set()  # refetch so an approved PR drops off the list
		with self.lock:
			self.reviews[pr["url"]] = "reviewing..."
			self.running.add(pr["url"])
		threading.Thread(target=run, daemon=True).start()

	def start_self_review(self, pr):
		"""Pre-review one of MY PRs. Posts nothing; the file it writes is found again by its name."""
		model = self.model
		def run():
			status, _dest = review_mod.self_review(pr, model)  # module attr: --demo and tests swap it
			with self.lock:
				self.reviews[pr["url"]] = status  # ponytail: the path is not kept — it is derivable
				self.running.discard(pr["url"])
				self.seen_at.pop(pr["url"], None)
				self.done_at[pr["url"]] = time.time()
			self.wake.set()
		with self.lock:
			self.reviews[pr["url"]] = "pre-reviewing..."
			self.running.add(pr["url"])
		threading.Thread(target=run, daemon=True).start()

	def loop(self):
		while True:
			t0, self.fetching = time.time(), True
			team.pull()  # newest team log + memory before we read them
			memory.history()  # ponytail: before the backup, so the first commit is memory as it arrived —
			memory.backup("tick")  # and so the Memory row can say "no history" before a write, not after
			refresh_mirrors()  # ponytail: here, not in a session hook — no global config, no timeout budget
			data = github.fetch()
			stale = log.mark_rereviews(data)
			newer = update.update_available()
			if self.fetched_at is None:
				time.sleep(max(0, config.SPLASH_MIN - (time.time() - t0)))  # let the splash breathe on the first load
			with self.lock:
				self.sections, self.fetched_at, self.update, self.fetching = data, time.time(), newer, False
				for u in stale:  # forget the old verdict so r / auto can review the new push
					if not in_flight(self, u):
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
				for _name, prs, _err in data:
					for p in prs or []:
						u = p["url"]
						if u not in self.reviews or in_flight(self, u):
							continue
						# ponytail: .get, not a subscript. A KeyError here runs on the refresh thread and
						# takes the whole loop down; a PR without the field simply never goes stale.
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
			while not self.wake.wait(1) and time.time() < self.fetched_at + self.interval:
				pass  # 1s slices so an interval change via i takes effect now
			self.wake.clear()


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
