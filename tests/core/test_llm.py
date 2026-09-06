import io
import json
import subprocess
import urllib.error
import urllib.request

import pytest

from dashy import config
from dashy.core import llm
from dashy.core.review import review

from conftest import PR, Result


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
	seen, calls = [], []
	verdict = json.dumps({"verdict": "approve", "summary": "s", "body": "b", "findings": []})
	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(seen, answer(verdict)))
	def fake_run(cmd, **kw):
		calls.append(cmd)
		return Result(json.dumps({"title": "a pr"}) if "--json" in cmd else "DIFF-BODY")
	monkeypatch.setattr(subprocess, "run", fake_run)
	assert review(dict(PR), "local:qwen3") == "✓ approved"
	assert ["gh", "pr", "diff", "7", "--repo", "a/b"] in calls
	assert not any(c[0] == "claude" for c in calls)  # the CLI is never touched
	sent = json.loads(seen[0].data)["messages"][-1]["content"]
	assert "DIFF-BODY" in sent and "title: a pr" in sent
	# ponytail: `gh pr view` without --json asks GraphQL for projectCards and exits 1 on repos with
	# classic projects. Naming the fields is the fix, so the test names it too.
	assert all("--json" in c for c in calls if c[:3] == ["gh", "pr", "view"])


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
