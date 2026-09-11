import subprocess


from dashy.core import update
from dashy.core.update import update_available

from conftest import Result


LS_REMOTE = "abc\trefs/tags/v0.9.0\ndef\trefs/tags/v1.10.0\nfed\trefs/tags/v1.2.0\n"


def test_update_available_offers_newer_release(monkeypatch):
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: Result(LS_REMOTE))
	monkeypatch.setattr(update, "VERSION", "1.2.0")
	assert update_available() == "1.10.0"  # numeric compare, not lexical


def test_update_available_silent_when_current(monkeypatch):
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: Result(LS_REMOTE))
	monkeypatch.setattr(update, "VERSION", "1.10.0")
	assert update_available() == ""


def test_update_available_is_empty_when_git_fails(monkeypatch):
	def boom(cmd, **kw):
		raise subprocess.CalledProcessError(1, cmd, stderr="no origin")
	monkeypatch.setattr(subprocess, "run", boom)
	assert update_available() == ""


def test_binary_release_beginning_at_v2():
	assert not update.is_binary_release("1.47.1")
	assert update.is_binary_release("2.0.0")
	assert update.is_binary_release("10.1.0")


def test_asset_name_follows_the_machine(monkeypatch):
	monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
	monkeypatch.setattr(update.platform, "machine", lambda: "arm64")
	assert update.asset_name() == "gitdashy-macos-arm64"


def test_migrate_downloads_over_the_entrypoint(monkeypatch, tmp_path):
	monkeypatch.setattr(update.os.path, "expanduser", lambda p: str(tmp_path))
	monkeypatch.setattr(update.platform, "system", lambda: "Linux")
	monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")

	class Resp:
		def __init__(self, data):
			self.buf = data
		def read(self, n=-1):
			out, self.buf = self.buf, b""
			return out
		def __enter__(self):
			return self
		def __exit__(self, *a):
			return False

	monkeypatch.setattr(update.urllib.request, "urlopen", lambda url, timeout=None: Resp(b"BIN"))
	execd = {}
	monkeypatch.setattr(update.os, "execv", lambda path, argv: execd.update(path=path, argv=argv))

	update.migrate("2.0.0")

	target = tmp_path / ".local" / "bin" / "gitdashy"
	assert target.read_bytes() == b"BIN"
	assert execd["path"] == str(target)


def test_apply_update_migrates_for_v2(monkeypatch):
	seen = {}
	monkeypatch.setattr(update, "migrate", lambda version: seen.setdefault("version", version))
	assert update.apply_update("2.0.0") == "2.0.0"
	assert seen == {"version": "2.0.0"}


def test_apply_update_still_checks_out_a_legacy_tag(monkeypatch):
	runs = []

	def fake_run(*a, **kw):
		runs.append(a[0])
		raise subprocess.CalledProcessError(1, a[0], stderr="boom")

	def fail_migrate(version):
		raise AssertionError("a legacy tag must not migrate")

	monkeypatch.setattr(update.subprocess, "run", fake_run)
	monkeypatch.setattr(update, "migrate", fail_migrate)
	assert update.apply_update("1.48.0") == "boom"
	assert [cmd[3] for cmd in runs] == ["fetch"]  # the checkout never gets a chance; the fetch failed
