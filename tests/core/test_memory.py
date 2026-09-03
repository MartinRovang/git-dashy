import subprocess

import pytest

from dashy import config
from dashy.core import memory

from conftest import claude_out


def test_dream_returns_cleaned_files_and_write_applies(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	memory.append(None, "run make lint")
	memory.append("a/b", "uses tabs\nuses tabs\nrun make lint")
	memory.append("c/d", "old junk")
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return claude_out(summary="merged tabs, moved lint to general, emptied c/d",
		                  files={"a__b.md": "- uses tabs", "c__d.md": "", "bogus.md": "- nope"})
	monkeypatch.setattr(subprocess, "run", fake_run)
	summary, new = memory.dream("sonnet")
	assert calls[0][:2] == ["claude", "-p"] and "### general.md" in calls[0][2] and "--model" in calls[0]
	assert "--safe-mode" in calls[0]  # dream has a JSON contract too, no ambient CLAUDE.md
	assert summary.startswith("merged") and set(new) == {"general.md", "a__b.md", "c__d.md"}
	assert new["general.md"] == "- run make lint\n"  # untouched files keep their content
	memory.write(new)
	assert open(memory.path("a/b")).read() == "- uses tabs\n"
	assert not (tmp_path / "c__d.md").exists() and not (tmp_path / "bogus.md").exists()


def test_dream_with_no_memory_raises(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "none"))
	with pytest.raises(ValueError):
		memory.dream("opus")
