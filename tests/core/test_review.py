import json
import os
import re
import subprocess

import pytest

from dashy import config
from dashy.core import github, log, memory, review as review_mod
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


def test_review_reads_memory_and_only_drafts_what_it_proposes(monkeypatch, tmp_path):
	from dashy.core import memory
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "general.md").write_text("- always run make lint\n")
	(tmp_path / "a__b.md").write_text("- uses tabs\n- db layer is generated\n")
	prompts = []
	def fake_run(cmd, **kw):
		if cmd[0] == "claude":
			prompts.append(cmd[2])
		return claude_out(verdict="approve", body="b", memory="ci is slow, do not flag timeouts")
	monkeypatch.setattr(subprocess, "run", fake_run)
	monkeypatch.setattr(config, "DEPTH", "high")
	monkeypatch.setattr(config, "EFFORT", "max")
	review(dict(PR), "opus")
	assert "## General\n### mine\n- always run make lint" in prompts[0]
	assert "## a/b\n### mine\n- uses tabs\n- db layer is generated" in prompts[0]
	assert memory.drafts("a/b") == [(1, "ci is slow, do not flag timeouts")]  # one review only drafts
	assert "ci is slow" not in memory.read("a/b")  # so the next review cannot be shown its own guess
	assert memory.read("x/y") == "## General\n### mine\n- always run make lint"
	e = log.reviewed()[0]
	assert e["tag"] == "high/max" and "opus high/max" in log.detail(e["review"])


def test_a_second_independent_review_turns_a_draft_into_a_fact(monkeypatch, tmp_path):
	from dashy.core import memory
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	monkeypatch.setattr(subprocess, "run",
	                    lambda cmd, **kw: claude_out(verdict="approve", body="b", memory="ci is slow, do not flag timeouts"))
	review(dict(PR), "opus")
	assert memory.drafts("a/b") == [(1, "ci is slow, do not flag timeouts")]
	review(dict(PR), "opus")
	assert memory.drafts("a/b") == []
	assert open(memory.path("a/b")).read() == "- ci is slow, do not flag timeouts\n"
	review(dict(PR), "opus")  # now settled: proposing it a third time adds nothing
	assert memory.drafts("a/b") == []
	assert open(memory.path("a/b")).read() == "- ci is slow, do not flag timeouts\n"


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


def test_review_runs_claude_scoped_with_the_lens(monkeypatch):
	"""The corpus/CLAUDE.md of whatever dir gitdashy was launched from must not reach the reviewer."""
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return claude_out(verdict="approve", body="b")
	monkeypatch.setattr(subprocess, "run", fake_run)
	review(dict(PR), "opus")
	cmd = calls[1]
	assert "--safe-mode" in cmd
	assert cmd[cmd.index("--append-system-prompt") + 1] == review_mod.LENS
	assert cmd.index("--safe-mode") < cmd.index("--allowedTools")  # flags precede the tool grant, not the prompt


def test_a_review_is_told_what_the_team_is_building(monkeypatch, tmp_path):
	from dashy.core import memory, team
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mine"))
	monkeypatch.setattr(config, "TEAM", str(tmp_path / "team"))
	(tmp_path / "team" / "memory").mkdir(parents=True)
	(tmp_path / "team" / "memory" / "project.md").write_text("We build X for surgeons.\n")
	monkeypatch.setattr(team, "on", lambda: True)
	prompts = []
	def fake_run(cmd, **kw):
		if cmd[0] == "claude":
			prompts.append(cmd[2])
		return claude_out(verdict="approve", body="b")
	monkeypatch.setattr(subprocess, "run", fake_run)
	review(dict(PR), "opus")
	assert "What this is being built for, and for whom:" in prompts[0]
	assert "### team" in prompts[0] and "We build X for surgeons." in prompts[0]  # labelled, like any source
	assert prompts[0].index("being built for") < prompts[0].index("Respond with ONLY")


def test_a_review_does_not_care_where_the_dashboard_was_started(monkeypatch, tmp_path):
	"""Launched from a directory that has since been deleted, every review failed to start at all."""
	import os
	seen = {}
	def fake_run(cmd, **kw):
		if cmd and cmd[0] == "claude":  # gh calls come after and would overwrite it
			seen["cwd"] = kw.get("cwd")
		return claude_out(verdict="approve", body="b")
	monkeypatch.setattr(subprocess, "run", fake_run)
	review(dict(PR), "opus")
	assert seen["cwd"] and os.path.isabs(seen["cwd"])  # ours, not inherited
	assert seen["cwd"] != os.getcwd()


def test_a_dream_does_not_either(monkeypatch, tmp_path):
	from dashy.core import memory
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	(tmp_path / "general.md").write_text("- a fact\n")
	seen = {}
	def fake_run(cmd, **kw):
		seen["cwd"] = kw.get("cwd")
		return claude_out(summary="s", files={})
	monkeypatch.setattr(subprocess, "run", fake_run)
	memory.dream("opus")
	assert seen["cwd"] and seen["cwd"] != __import__("os").getcwd()


def test_a_self_review_posts_nothing_and_writes_a_file(monkeypatch, tmp_path):
	"""GitHub refuses to let you approve your own PR, and a verdict on your own work is not a review_mod."""
	mem = tmp_path / "mem"
	mem.mkdir()
	monkeypatch.setattr(config, "MEMORY_DIR", str(mem))
	monkeypatch.setattr(config, "TEAM", "")
	monkeypatch.setattr(review_mod, "SELF_DIR", str(tmp_path / "out"))
	posted = []
	monkeypatch.setattr(review_mod.github, "post_review", lambda *a, **k: posted.append(a))
	monkeypatch.setattr(review_mod.github, "comment", lambda *a, **k: posted.append(a))
	monkeypatch.setattr(review_mod.log, "log_review", lambda *a, **k: posted.append(a))
	monkeypatch.setattr(review_mod, "_verdict", lambda repo, n, model, prev=None: {
		"verdict": "request_changes", "summary": "adds a thing",
		"body": "## Findings\n- foo.py:1 is wrong", "memory": "the api owns no DDL"})

	pr = {"repository": {"nameWithOwner": "acme/api"}, "number": 7, "url": "u"}
	status, dest = review_mod.self_review(pr, "opus")

	assert posted == []                       # nothing reached GitHub, nothing was logged
	assert "not posted" in status
	assert os.path.isfile(dest) and dest.startswith(str(tmp_path / "out"))
	body = open(dest).read()
	assert "Not posted" in body and "foo.py:1 is wrong" in body
	assert [t for _n, t in memory.self_drafts("acme/api")] == ["the api owns no DDL"]
	assert memory.known("acme/api") == []      # and it is not a fact yet


def test_a_self_review_does_not_claim_a_previous_review_it_did_not_write(monkeypatch, tmp_path):
	"""PREV says 'you already reviewed this PR'. A real reviewer's verdict is not ours to speak for."""
	seen = {}
	monkeypatch.setattr(review_mod, "SELF_DIR", str(tmp_path / "out"))
	monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
	monkeypatch.setattr(config, "TEAM", "")
	def spy(repo, n, model, prev=None):
		seen["prev"] = prev
		return {"verdict": "comment", "summary": "s", "body": "b", "memory": ""}
	monkeypatch.setattr(review_mod, "_verdict", spy)
	monkeypatch.setattr(review_mod.log, "last", lambda url: {"at": "2026-01-01", "verdict": "approve", "body": "x"})
	review_mod.self_review({"repository": {"nameWithOwner": "a/b"}, "number": 1, "url": "u"}, "opus")
	assert seen["prev"] is None


def test_both_paths_build_the_prompt_the_same_way():
	"""A pre-review that reasons differently is worthless as a preview of the real one."""
	import inspect
	assert "_verdict(" in inspect.getsource(review_mod.review)
	assert "_verdict(" in inspect.getsource(review_mod.self_review)
	assert "PROMPT.format" not in inspect.getsource(review_mod.review)  # one builder, not two
