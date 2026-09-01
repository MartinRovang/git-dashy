import subprocess

import pytest

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
