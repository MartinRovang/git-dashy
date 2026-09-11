import json
import os
import threading
import time

from dashy import config
from dashy.core import heartbeat


def beat_file(tmp_path, monkeypatch):
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "settings.json"))
	return tmp_path / ".prs_dashboard"


def test_a_beat_this_process_just_wrote_reads_as_alive(monkeypatch, tmp_path):
	p = beat_file(tmp_path, monkeypatch)
	heartbeat.beat(300)
	assert json.loads(p.read_text())["pid"] == os.getpid()
	assert heartbeat.alive()


def test_no_beat_at_all_is_not_alive(monkeypatch, tmp_path):
	beat_file(tmp_path, monkeypatch)
	assert not heartbeat.alive()


def test_a_beat_older_than_the_interval_it_declared_is_not_alive(monkeypatch, tmp_path):
	"""A suspended laptop leaves a live pid and yesterday's beat. The pid is not the whole answer."""
	p = beat_file(tmp_path, monkeypatch)
	p.write_text(json.dumps({"pid": os.getpid(), "host": heartbeat.HOST,
	                         "at": time.time() - 300 - heartbeat.GRACE - 1, "interval": 300}))
	assert not heartbeat.alive()
	p.write_text(json.dumps({"pid": os.getpid(), "host": heartbeat.HOST,
	                         "at": time.time() - 300, "interval": 300}))
	assert heartbeat.alive()  # inside the interval plus the grace a long tick is allowed


def test_a_beat_from_another_machine_is_not_alive(monkeypatch, tmp_path):
	"""A memory dir synced between machines carries this file. A pid means nothing on the other one,
	and a live unrelated process with that number would read as a running dashboard."""
	p = beat_file(tmp_path, monkeypatch)
	p.write_text(json.dumps({"pid": os.getpid(), "host": heartbeat.HOST + "-other",
	                         "at": time.time(), "interval": 300}))
	assert not heartbeat.alive()


def test_a_dead_pid_is_not_alive(monkeypatch, tmp_path):
	p = beat_file(tmp_path, monkeypatch)
	# ponytail: a pid that cannot exist. 2**22 is above every Linux pid_max default, so this is not a
	# race against the machine happening to have that process — it is never a live one.
	p.write_text(json.dumps({"pid": 2 ** 22, "host": heartbeat.HOST, "at": time.time(), "interval": 300}))
	assert not heartbeat.alive()


def test_a_corrupt_beat_is_not_alive_rather_than_an_exception(monkeypatch, tmp_path):
	"""This is read on the mirror path, which a SessionStart hook calls. A half-written file there
	must degrade to "nothing is running", never to a traceback in someone's session start."""
	p = beat_file(tmp_path, monkeypatch)
	p.write_text('{"pid": 1, "ho')
	assert not heartbeat.alive()
	p.write_text('{"pid": "not a number", "host": "' + heartbeat.HOST + '", "at": 0, "interval": 300}')
	assert not heartbeat.alive()


def test_demo_mode_writes_no_beat_anywhere(monkeypatch, tmp_path):
	"""Demo mode promises to touch nothing on the machine it runs on, and says so in the README."""
	monkeypatch.setattr(config, "SETTINGS", "")
	heartbeat.beat(300)
	assert not heartbeat.alive()
	assert list(tmp_path.iterdir()) == []


def test_an_interval_longer_than_the_dropdown_offers_is_honoured(monkeypatch, tmp_path):
	"""`--interval` is a free int; `i` cycles config.INTERVALS but the flag is not limited to them. A
	clamp to the longest of those made an honest `--interval 1800` read as dead 990 seconds into every
	cycle — the header saying "nothing is refreshing it" with the dashboard on screen, and a hook's
	sync claiming the lock underneath one that was about to pull."""
	assert 1800 > max(config.INTERVALS)  # the case the clamp broke, and a legitimate one
	p = beat_file(tmp_path, monkeypatch)
	p.write_text(json.dumps({"pid": os.getpid(), "host": heartbeat.HOST,
	                         "at": time.time() - 1000, "interval": 1800}))
	assert heartbeat.alive()
	p.write_text(json.dumps({"pid": os.getpid(), "host": heartbeat.HOST,
	                         "at": time.time() - 1800 - heartbeat.GRACE - 1, "interval": 1800}))
	assert not heartbeat.alive()  # and it still lapses, at the interval it actually declared


def test_a_beat_is_never_read_half_written(monkeypatch, tmp_path):
	"""A plain open(p, "w") truncates first, and a reader landing in that window reads "no dashboard"
	and goes and pulls — in the one place the beat exists to stop that."""
	beat_file(tmp_path, monkeypatch)
	heartbeat.beat(300)
	seen, stop = [], threading.Event()

	def watch():
		while not stop.is_set():
			seen.append(heartbeat.alive())

	t = threading.Thread(target=watch)
	t.start()
	for _ in range(400):
		heartbeat.beat(300)
	stop.set()
	t.join(5)
	assert seen and all(seen), f"{seen.count(False)} of {len(seen)} reads saw no dashboard"


def test_only_one_process_holds_the_pull_lock(monkeypatch, tmp_path):
	"""The beat closes dashboard-versus-hook and not hook-versus-hook: a background sync writes no
	beat, so two sessions opened at once both saw nothing running and both ran pull --rebase in one
	checkout. team._lock is a threading.Lock and does not reach across processes."""
	beat_file(tmp_path, monkeypatch)
	assert heartbeat.claim()
	assert not heartbeat.claim()          # the second caller is told, rather than joining in
	heartbeat.unclaim()
	assert heartbeat.claim()              # and it is a lock, not a one-shot
	heartbeat.unclaim()


def test_a_lock_left_by_a_dead_process_is_broken_rather_than_waited_on(monkeypatch, tmp_path):
	"""A process killed while holding it would otherwise stop every later sync from pulling, for ever
	and silently — a worse failure than the race the lock prevents, and one nobody would think to look
	for. Breaking it reopens exactly the window that existed before the lock."""
	beat_file(tmp_path, monkeypatch)
	lock = tmp_path / heartbeat.LOCK
	lock.write_text("9999999")
	os.utime(lock, (time.time() - heartbeat.STUCK - 1,) * 2)
	assert heartbeat.claim()
	heartbeat.unclaim()
	lock.write_text("9999999")            # and a fresh one is still respected
	assert not heartbeat.claim()


def test_unclaim_releases_only_a_lock_this_process_still_holds(monkeypatch, tmp_path):
	"""team.pull() walks every joined team at a git timeout each, so a slow pull can outlive STUCK. A
	second claimant then breaks the lock and takes its own — and this process finishing removed THAT
	one's file and let a third in, so the lock stopped holding under exactly the slow pulls that make
	it worth having."""
	beat_file(tmp_path, monkeypatch)
	lock = tmp_path / heartbeat.LOCK
	assert heartbeat.claim()
	lock.write_text("424242")          # a second claimant broke ours and took its own
	heartbeat.unclaim()
	assert lock.exists() and lock.read_text() == "424242"  # not ours to give back
	assert not heartbeat.claim()       # and still held, as far as everyone else is concerned


def test_unclaiming_a_lock_that_is_not_there_is_not_an_error(monkeypatch, tmp_path):
	"""It runs in a finally. Raising there would replace a failed pull with a traceback out of a
	SessionStart hook."""
	beat_file(tmp_path, monkeypatch)
	heartbeat.unclaim()


def test_demo_mode_takes_no_lock_and_leaves_no_file(monkeypatch, tmp_path):
	"""Demo mode promises to touch nothing on the machine it runs on. It also pulls nothing, so there
	is nobody to race: the claim succeeds and writes nowhere."""
	monkeypatch.setattr(config, "SETTINGS", "")
	assert heartbeat.claim()
	heartbeat.unclaim()
	assert list(tmp_path.iterdir()) == []


def test_breaking_a_stale_lock_has_one_winner(monkeypatch, tmp_path):
	"""Two claimants could both see the old mtime. With os.remove both then succeeded: the first
	creates its lock, the second's remove deletes it, and two processes pull. rename is atomic and
	single-winner — whoever loses gets ENOENT and keeps the answer it was given."""
	beat_file(tmp_path, monkeypatch)
	lock = tmp_path / heartbeat.LOCK
	lock.write_text("9999999")
	os.utime(lock, (time.time() - heartbeat.STUCK - 1,) * 2)
	assert heartbeat.claim()
	assert (tmp_path / (heartbeat.LOCK + ".stale")).exists()  # moved aside, not deleted under a racer
	assert not heartbeat.claim()                              # and the fresh one is ours alone
	heartbeat.unclaim()


def test_a_lock_whose_write_fails_is_not_left_behind(monkeypatch, tmp_path):
	"""os.write sat outside the try, so ENOSPC gave a traceback out of `gitdashy sync-memory` and left
	an EMPTY lock — which unclaim refused to remove, since int("") raises, so it stuck for all of
	STUCK. Failing to record the pid is not a reason to hold a lock nobody can release."""
	beat_file(tmp_path, monkeypatch)

	def no(*a):
		raise OSError("no space left on device")

	monkeypatch.setattr(heartbeat.os, "write", no)
	assert heartbeat.claim()                       # nowhere to record it is not a reason to stop
	assert not (tmp_path / heartbeat.LOCK).exists()  # and nothing is left for STUCK to time out
