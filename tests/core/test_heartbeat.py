import json
import os
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


def test_a_beat_cannot_declare_an_interval_longer_than_the_dashboard_offers(monkeypatch, tmp_path):
	"""alive() trusts a number out of a file. One that says a year makes a dashboard that stopped a
	year ago still read as running, which suppresses every background pull and the staleness warning
	with it. Local write access there is already game over, so this is hardening, not a boundary."""
	p = beat_file(tmp_path, monkeypatch)
	p.write_text(json.dumps({"pid": os.getpid(), "host": heartbeat.HOST,
	                         "at": time.time() - 86400, "interval": 31536000}))
	assert not heartbeat.alive()


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
