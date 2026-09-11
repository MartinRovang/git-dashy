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
