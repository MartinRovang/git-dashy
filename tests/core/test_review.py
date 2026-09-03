import json
import re
import subprocess

import pytest

from dashy import config
from dashy.core import github, log, review as review_mod
from dashy.core.review import review

from conftest import PR, Result, claude_out


def test_review_posts_verdict_and_logs(monkeypatch):
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return claude_out(verdict="request_changes", summary="adds x", body="nope")
	monkeypatch.setattr(subprocess, "run", fake_run)
	assert review(dict(PR), "sonnet") == "✗ changes requested"
	assert calls[0][:6] == ["gh", "pr", "comment", "7", "--repo", "a/b"]
	assert re.match(r'<img src="https://\S+/sprites/\S+\.png" width="120">\n\n\*\*Dashy is on its way!', calls[0][-1])
	assert calls[0][-1].endswith("**Dashy is on its way!** Reviewing with model **sonnet**, effort **medium** and depth **adaptive** (Dashy picks the depth from the diff size and risk).")
	assert calls[1][0] == "claude" and calls[1][calls[1].index("--model") + 1] == "sonnet"
	assert calls[2][:6] == ["gh", "pr", "review", "7", "--repo", "a/b"]
	assert "--request-changes" in calls[2] and calls[2][-1] == "nope"
	entry = json.loads(open(log.LOG).read())
	assert entry["verdict"] == "request_changes" and entry["summary"] == "adds x"
	assert entry["model"] == "sonnet" and entry["pr"]["url"] == "u"


@pytest.mark.parametrize("verdict,flag,status", [
	("approve", "--approve", "✓ approved"),
	("comment", "--comment", "~ commented"),
])
def test_review_verdict_flags(monkeypatch, verdict, flag, status):
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return claude_out(verdict=verdict, body="b")
	monkeypatch.setattr(subprocess, "run", fake_run)
	assert review(dict(PR), "opus") == status
	assert flag in calls[2]


def test_review_unparseable_output_is_error_and_not_posted(monkeypatch):
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return Result(json.dumps({"result": "I could not review this"}))
	monkeypatch.setattr(subprocess, "run", fake_run)
	assert review(dict(PR), "opus").startswith("error:")
	assert len(calls) == 2 and calls[1][0] == "claude" and not __import__("os").path.exists(log.LOG)


def test_review_unknown_verdict_is_error(monkeypatch):
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: claude_out(verdict="lgtm", body="b"))
	assert review(dict(PR), "opus").startswith("error:")


def test_review_gh_failure_surfaces_stderr(monkeypatch):
	def fake_run(cmd, **kw):
		if cmd[:3] == ["gh", "pr", "review"]:
			raise subprocess.CalledProcessError(1, cmd, stderr="line1\nfatal: nope\n")
		return claude_out(verdict="approve", body="b")
	monkeypatch.setattr(subprocess, "run", fake_run)
	assert review(dict(PR), "opus") == "error: fatal: nope"


def test_review_timeout_is_error(monkeypatch):
	def fake_run(cmd, **kw):
		raise subprocess.TimeoutExpired(cmd, 1)
	monkeypatch.setattr(subprocess, "run", fake_run)
	assert review(dict(PR), "opus").startswith("error:")


def test_review_appends_instructions_file(monkeypatch, tmp_path):
	f = tmp_path / "rules.md"
	f.write_text("Always check the changelog.")
	monkeypatch.setattr(config, "INSTRUCTIONS", str(f))
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return claude_out(verdict="approve", body="b")
	monkeypatch.setattr(subprocess, "run", fake_run)
	assert review(dict(PR), "opus") == "✓ approved"
	assert calls[1][2].endswith("Additional instructions from the reviewer:\nAlways check the changelog.")


def test_review_missing_instructions_file_is_error(monkeypatch, tmp_path):
	monkeypatch.setattr(config, "INSTRUCTIONS", str(tmp_path / "nope.md"))
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: claude_out(verdict="approve", body="b"))
	assert review(dict(PR), "opus").startswith("error:")
	assert not __import__("os").path.exists(log.LOG)


# ---- reviewed log / detail ----


def test_review_depth_and_effort(monkeypatch):
	calls = []
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or claude_out(verdict="approve", body="b"))
	monkeypatch.setattr(config, "DEPTH", "high")
	monkeypatch.setattr(config, "EFFORT", "max")
	review(dict(PR), "opus")
	assert "Depth: very in-depth" in calls[1][2] and calls[1][-2:] == ["--effort", "max"]
	assert calls[0][-1].endswith("effort **max** and depth **high** (set by the reviewer).")
	calls.clear()
	monkeypatch.setattr(config, "EFFORT", "")
	review(dict(PR), "opus")
	assert "--effort" not in calls[1] and "effort **default**" in calls[0][-1]


def test_review_adaptive_appends_depth_used(monkeypatch):
	calls = []
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or claude_out(
		verdict="approve", body="b", depth_used="high", depth_reason="touches auth"))
	review(dict(PR), "opus")
	assert calls[2][-1] == "b\n\n_Dashy reviewed at **high** depth: touches auth_"
	assert log.reviewed()[0]["review"]["body"].endswith("touches auth_")
	calls.clear()
	monkeypatch.setattr(config, "DEPTH", "high")
	review(dict(PR), "opus")
	assert calls[2][-1] == "b"  # set depth: nothing to explain


def test_review_reads_and_appends_memory(monkeypatch, tmp_path):
	from dashy.core import memory
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	memory.append(None, "always run make lint")
	memory.append("a/b", "- uses tabs\n\n• db layer is generated")
	prompts = []
	def fake_run(cmd, **kw):
		if cmd[0] == "claude":
			prompts.append(cmd[2])
		return claude_out(verdict="approve", body="b", memory="ci is slow, do not flag timeouts")
	monkeypatch.setattr(subprocess, "run", fake_run)
	monkeypatch.setattr(config, "DEPTH", "high")
	monkeypatch.setattr(config, "EFFORT", "max")
	review(dict(PR), "opus")
	assert "## General\n- always run make lint" in prompts[0] and "## a/b\n- uses tabs\n- db layer is generated" in prompts[0]
	assert open(memory.path("a/b")).read().endswith("- ci is slow, do not flag timeouts\n")
	assert memory.read("x/y") == "## General\n- always run make lint"
	e = log.reviewed()[0]
	assert e["tag"] == "high/max" and "opus high/max" in log.detail(e["review"])


def test_rereview_prompt_includes_earlier_review(monkeypatch):
	prompts = []
	def fake_run(cmd, **kw):
		if cmd[0] == "claude":
			prompts.append(cmd[2])
		return claude_out(verdict="approve", body="fixed now")
	monkeypatch.setattr(subprocess, "run", fake_run)
	review(dict(PR), "opus")
	assert "RE-REVIEW" not in prompts[0]
	log.log_review(dict(PR), "opus", {"verdict": "request_changes", "body": "- cache never invalidated"}, at="2026-01-02T03:04:05+00:00")
	calls = []
	monkeypatch.setattr(github, "comment", lambda repo, n, body: calls.append(body))
	review(dict(PR), "opus")
	assert "**Dashy is on its way!** Re-reviewing (was ✗ changes requested on 2026-01-02) with model" in calls[0]
	assert "RE-REVIEW: you already reviewed this PR on 2026-01-02 with verdict request_changes" in prompts[1]
	assert "- cache never invalidated" in prompts[1]
