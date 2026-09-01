"""Background refresh loop and everything the UI reads."""
import threading
import time

from .. import config
from . import github, log, review as review_mod, update


class State:
	def __init__(self, interval=config.INTERVAL, model=config.DEFAULT_MODEL):
		self.interval, self.sections, self.fetched_at, self.lock = interval, [], None, threading.Lock()
		self.model = model
		self.wake, self.reviews = threading.Event(), {}  # reviews: url -> status string
		self.auto, self.auto_baseline = False, None  # baseline: RR urls present when auto was switched on
		self.window, self.subs = 4, "all"
		self.update = ""  # newer released version, refreshed with each fetch

	def set_auto(self, on):
		with self.lock:
			self.auto = on
			self.auto_baseline = set(self._rr_urls()) if on else None

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
			t0 = time.time()
			data = github.fetch()
			stale = log.mark_rereviews(data)
			newer = update.update_available()
			if self.fetched_at is None:
				time.sleep(max(0, config.SPLASH_MIN - (time.time() - t0)))  # let the splash breathe on the first load
			with self.lock:
				self.sections, self.fetched_at, self.update = data, time.time(), newer
				for u in stale:  # forget the old verdict so Enter / auto can review the new push
					if self.reviews.get(u) != "reviewing...":
						self.reviews.pop(u, None)
				new = [p for name, prs, _ in data if name == "REVIEW REQUESTED" for p in prs or []
				       if self.auto and p["url"] not in self.auto_baseline and p["url"] not in self.reviews] if self.auto else []
			for p in new:
				self.start_review(p)
			self.wake.wait(self.interval)
			self.wake.clear()
