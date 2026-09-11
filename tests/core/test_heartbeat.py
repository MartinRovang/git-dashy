import json
import os
import subprocess
import sys
import threading
import time

from dashy import config
from dashy.core import heartbeat

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def test_unclaiming_a_lock_that_is_not_there_is_not_an_error(monkeypatch, tmp_path):
	"""It runs in a finally. Raising there would replace a failed pull with a traceback out of a
	SessionStart hook."""
	beat_file(tmp_path, monkeypatch)
	heartbeat.unclaim()


def test_only_one_holder_of_the_pull_lock_at_a_time(monkeypatch, tmp_path):
	"""The beat closes dashboard-versus-hook and not hook-versus-hook: a background sync writes no
	beat, so two sessions opened at once both see nothing running. team._lock is a threading.Lock and
	does not reach across processes."""
	beat_file(tmp_path, monkeypatch)
	assert heartbeat.claim()
	assert not heartbeat.claim()     # the second caller is told, rather than joining in
	heartbeat.unclaim()
	assert heartbeat.claim()         # and it is a lock, not a one-shot
	heartbeat.unclaim()


def test_two_processes_cannot_hold_the_pull_lock_together(monkeypatch, tmp_path):
	"""The point of the whole thing, and the case a hand-rolled O_EXCL lock got wrong twice: a stale
	break that stats an mtime and then renames is two steps, so two claimants that both read the old
	mtime could both come away holding it. Real processes here, not threads, because that is what the
	session hook starts and it is the only shape that can prove the kernel is doing this."""
	beat_file(tmp_path, monkeypatch)
	code = ("import sys, time; sys.path.insert(0, %r);"
	        "from dashy import config; config.SETTINGS = %r;"
	        "from dashy.core import heartbeat;"
	        "got = heartbeat.claim(); print(int(got)); sys.stdout.flush();"
	        "time.sleep(float(sys.argv[1]))") % (ROOT, config.SETTINGS)
	first = subprocess.Popen([sys.executable, "-c", code, "3"], stdout=subprocess.PIPE, text=True)
	assert first.stdout.readline().strip() == "1"        # it has the lock and is holding it
	second = subprocess.run([sys.executable, "-c", code, "0"], capture_output=True, text=True)
	assert second.stdout.strip() == "0", second.stderr   # and nobody else gets it meanwhile
	first.kill()
	first.wait(5)
	# ponytail: the kernel releases a flock when the holder DIES, however it dies. The hand-rolled lock
	# needed a stale timeout for this, which is what made it breakable and therefore racy.
	assert heartbeat.claim()
	heartbeat.unclaim()


def test_demo_mode_takes_no_lock_and_leaves_no_file(monkeypatch, tmp_path):
	"""Demo mode promises to touch nothing on the machine it runs on. It pulls nothing either, so
	there is nobody to race: the claim succeeds and writes nowhere."""
	monkeypatch.setattr(config, "SETTINGS", "")
	assert heartbeat.claim()
	heartbeat.unclaim()
	assert list(tmp_path.iterdir()) == []
