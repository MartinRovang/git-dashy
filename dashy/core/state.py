"""Background refresh loop and everything the UI reads."""
import os
import threading
import time

from .. import config
from . import github, install, log, mirror, review as review_mod, team, update


def refresh_mirrors():
	"""Re-mirror every repo `gitdashy init` registered. Never raises: a bad entry must not stop a refresh."""
	for into, repo in install.registered():
		# ponytail: only into a repo that is still there. makedirs would otherwise rebuild the tree of a
		# repo you deleted and write memory back into it — a dashboard resurrecting folders behind you.
		if not os.path.isdir(os.path.dirname(os.path.dirname(into)) or "."):
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
		self.auto, self.auto_baseline = False, None  # baseline: RR urls present when auto was switched on
		self.window, self.subs, self.drafts = 4, "all", False  # drafts: show draft PRs (hidden by default)
		self.expanded = set()  # REVIEWED urls with older reviews unfolded (space toggles)
		self.hints = False  # ? toggles: show each setting's key next to it in the header
		self.update = ""  # newer released version, refreshed with each fetch
		self.fetching = False

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
			self.wake.set()  # refetch so an approved PR drops off the list
		with self.lock:
			self.reviews[pr["url"]] = "reviewing..."
		threading.Thread(target=run, daemon=True).start()

	def loop(self):
		while True:
			t0, self.fetching = time.time(), True
			team.pull()  # newest team log + memory before we read them
			refresh_mirrors()  # ponytail: here, not in a session hook — no global config, no timeout budget
			data = github.fetch()
			stale = log.mark_rereviews(data)
			newer = update.update_available()
			if self.fetched_at is None:
				time.sleep(max(0, config.SPLASH_MIN - (time.time() - t0)))  # let the splash breathe on the first load
			with self.lock:
				self.sections, self.fetched_at, self.update, self.fetching = data, time.time(), newer, False
				for u in stale:  # forget the old verdict so Enter / auto can review the new push
					if self.reviews.get(u) != "reviewing...":
						self.reviews.pop(u, None)
				new = [p for name, prs, _ in data if name == "REVIEW REQUESTED" for p in prs or []
				       if self.auto and p["url"] not in self.auto_baseline and p["url"] not in self.reviews] if self.auto else []
			for p in new:
				self.start_review(p)
			while not self.wake.wait(1) and time.time() < self.fetched_at + self.interval:
				pass  # 1s slices so an interval change via i takes effect now
			self.wake.clear()
