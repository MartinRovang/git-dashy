import io
import json
import subprocess
import urllib.error
import urllib.request

import pytest

from dashy import config
from dashy.core import github, llm
from dashy.core.review import review

from conftest import PR, Body, Result


def test_provider_splits_only_known_prefixes():
	assert llm.provider("opus") == ("claude", "opus")
	assert llm.provider("openrouter:x-ai/grok-4") == ("openrouter", "x-ai/grok-4")
	assert llm.provider("local:qwen3") == ("local", "qwen3")
	assert llm.provider("weird:thing") == ("claude", "weird:thing")  # not an endpoint: a claude model name


class Body:
	"""A response body handed over in one chunk, then end of stream."""
	def __init__(self, raw): self.left = raw
	def read(self, n=None): out, self.left = self.left, b""; return out
	def __enter__(self): return self
	def __exit__(self, *a): return False


def fake_urlopen(seen, raw):
	def go(req, timeout=None):
		seen.append(req)
		return Body(raw)
	return go


def answer(content):
	return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def test_openai_backend_posts_prompt_and_key(monkeypatch):
	seen = []
	monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, answer(" hi ")))
	text, cost, ms = llm.ask("prompt here", "openrouter:x-ai/grok-4", system="lens")
	assert (text, cost) == ("hi", None) and ms >= 0
	req = seen[0]
	assert req.full_url == "https://openrouter.ai/api/v1/chat/completions"
	assert req.headers["Authorization"] == "Bearer sk-test"
	body = json.loads(req.data)
	assert body["model"] == "x-ai/grok-4"
	assert [m["content"] for m in body["messages"]] == ["lens", "prompt here"]


def test_local_backend_sends_no_key_when_unset(monkeypatch):
	seen = []
	monkeypatch.delenv("PRS_LOCAL_KEY", raising=False)
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, answer("ok")))
	llm.ask("p", "local:qwen3")
	assert seen[0].full_url.startswith("http://localhost:1234/")
	assert "Authorization" not in seen[0].headers


def test_review_on_openai_backend_pastes_the_pr_and_posts(monkeypatch):
	"""No tool loop on this backend, so the PR has to arrive in the prompt — and the model never runs."""
	seen, calls, asked = [], [], []
	verdict = json.dumps({"verdict": "approve", "summary": "s", "body": "b", "findings": []})
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Result("{}"))
	def http(req, timeout=None):
		if req.full_url.startswith(github.API):  # the PR, its diff, the hello and the verdict
			asked.append((req.full_url, req.headers.get("Accept", "")))
			return Body(b"DIFF-BODY" if "diff" in req.headers.get("Accept", "") else b'{"title": "a pr"}')
		return fake_urlopen(seen, answer(verdict))(req, timeout)
	monkeypatch.setattr(urllib.request, "urlopen", http)
	assert review(dict(PR), "local:qwen3") == "✓ approved"
	assert not calls  # neither the claude CLI nor gh is touched
	assert ("https://api.github.com/repos/a/b/pulls/7", "application/vnd.github.v3.diff") in asked
	sent = json.loads(seen[0].data)["messages"][-1]["content"]
	assert "DIFF-BODY" in sent and "title: a pr" in sent


class Dribble:
	"""A body that pads forever and never ends — OpenRouter holding a slow generation open."""
	def __init__(self): self.reads = 0
	def read(self, n=None): self.reads += 1; return b" "
	def __enter__(self): return self
	def __exit__(self, *a): return False


def test_padding_that_never_ends_stops_at_the_deadline(monkeypatch):
	body = Dribble()
	monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: body)
	with pytest.raises(TimeoutError):
		llm.ask("p", "local:qwen3", timeout=0)
	assert body.reads  # it read, then gave up, rather than blocking on the socket forever


class Firehose:
	"""A body that pads fast — a padder at MB/s must hit the byte cap long before the deadline."""
	def read(self, n=None): return b" " * n
	def __enter__(self): return self
	def __exit__(self, *a): return False


def test_padding_that_never_ends_stops_at_the_byte_cap(monkeypatch):
	monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Firehose())
	with pytest.raises(OSError, match="MB"):
		llm.ask("p", "local:qwen3", timeout=900)  # the deadline is far away; the cap fires first


def test_silent_upstream_gets_a_short_socket_timeout(monkeypatch):
	import socket
	seen = {}
	def go(req, timeout=None):
		seen["timeout"] = timeout
		raise socket.timeout("timed out")
	monkeypatch.setattr(urllib.request, "urlopen", go)
	with pytest.raises(TimeoutError):
		llm.ask("p", "local:qwen3", timeout=900)
	assert seen["timeout"] == llm.READ_TIMEOUT < 900  # one read, not the whole budget


def test_unknown_effort_falls_back_to_high(monkeypatch):
	seen = []
	monkeypatch.setattr(config, "EFFORT", "typo")
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, answer("ok")))
	llm.ask("p", "openrouter:m")
	assert json.loads(seen[0].data)["reasoning"] == {"effort": "high"}


def test_self_check_on_another_backend_only_pings(monkeypatch):
	from dashy.core import review as review_mod
	seen, calls = [], []
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, answer("OK")))
	monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Result(""))
	got = review_mod.self_check("local:qwen3")
	assert [ok for _, ok, _ in got] == [True] and got[0][0] == "local answers"
	assert not calls  # the claude flag probes are skipped, not run against nothing


def test_http_error_body_becomes_the_message(monkeypatch):
	def boom(req, timeout=None):
		raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
		                             io.BytesIO(b'{"error":{"message":"User not found.","code":401}}'))
	monkeypatch.setattr(urllib.request, "urlopen", boom)
	with pytest.raises(OSError, match="openrouter 401: User not found."):
		llm.ask("p", "openrouter:x-ai/grok-4")


def test_error_object_with_200_is_not_read_as_an_answer(monkeypatch):
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([], b'{"error":{"message":"upstream is down"}}'))
	with pytest.raises(OSError, match="upstream is down"):
		llm.ask("p", "local:qwen3")


def test_openrouter_asks_for_cost_and_reports_it(monkeypatch):
	seen = []
	raw = json.dumps({"choices": [{"message": {"content": "hi"}}], "usage": {"cost": 0.0123}}).encode()
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, raw))
	assert llm.ask("p", "openrouter:x-ai/grok-4")[1] == 0.0123
	assert json.loads(seen[0].data)["usage"] == {"include": True}


def test_local_backend_neither_asks_for_cost_nor_invents_one(monkeypatch):
	seen = []
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, answer("hi")))
	assert llm.ask("p", "local:qwen3")[1] is None
	assert "usage" not in json.loads(seen[0].data)


def test_effort_becomes_the_reasoning_budget(monkeypatch):
	seen = []
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, answer("hi")))
	monkeypatch.setattr(config, "EFFORT", "low")
	llm.ask("p", "openrouter:x-ai/grok-4")
	assert json.loads(seen[0].data)["reasoning"] == {"effort": "low"}
	monkeypatch.setattr(config, "EFFORT", "max")  # openrouter has no level above high
	llm.ask("p", "openrouter:x-ai/grok-4")
	assert json.loads(seen[-1].data)["reasoning"] == {"effort": "high"}
	monkeypatch.setattr(config, "EFFORT", "")  # "" means the model's own default, so say nothing
	llm.ask("p", "openrouter:x-ai/grok-4")
	assert "reasoning" not in json.loads(seen[-1].data)


def test_local_backend_is_sent_no_reasoning_field(monkeypatch):
	seen = []
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, answer("hi")))
	monkeypatch.setattr(config, "EFFORT", "low")
	llm.ask("p", "local:qwen3")
	assert "reasoning" not in json.loads(seen[0].data)


def test_ask_without_an_env_leaves_the_child_environment_alone(monkeypatch):
	"""ponytail: only ever exercised incidentally. `env=None` must pass None, not an empty dict — a dict
	would be a REPLACEMENT environment, and the child would lose PATH and the token with it."""
	seen = {}
	def fake_run(cmd, **kw):
		seen["env"] = kw.get("env", "absent")
		return Result(json.dumps({"result": "ok"}))
	monkeypatch.setattr(subprocess, "run", fake_run)
	llm.ask("hi", "sonnet")
	assert seen["env"] is None
	llm.ask("hi", "sonnet", env={"PRS_API_REPO": "a/b"})
	assert seen["env"]["PRS_API_REPO"] == "a/b" and "PATH" in seen["env"]


def test_obj_ignores_trailing_prose():
	"""A sign-off after the object used to make json.loads die with "Extra data"."""
	assert llm.obj('here you go:\n{"verdict": "approve"}\n\nHope that helps! :)') == {"verdict": "approve"}
	assert llm.obj('{"a": {"b": 1}} then ```{"not": "mine"}```') == {"a": {"b": 1}}
