"""Which model answers a prompt. Default is the claude CLI; "provider:model" goes to an OpenAI-compatible API.

ponytail: the OpenAI path is ONE chat completion, no tool loop, so the caller pastes the PR into the
prompt for those backends. Claude reads it itself, with the one command it is given. Add a tool loop when
a backend proves it can drive one, not before.
"""
import json
import logging
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from .. import config

REASONING = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
# ponytail: openrouter takes low/medium/high only; claude's two extra levels collapse onto high
BODY_MAX = 8 << 20  # bytes; an answer is a few KB, so anything past this is padding or a broken upstream
READ_TIMEOUT = 60  # s per socket read; the whole-request bound is `timeout`, in read_by


def provider(model):
	"""('claude', name) for a bare name, ('openrouter'|'local', name) for a prefixed one."""
	name, _, rest = model.partition(":")
	return (name, rest) if rest and name in config.ENDPOINTS else ("claude", model)


def ask(prompt, model, system="", tools="", timeout=900):
	"""Run the prompt. Returns (text, cost_usd_or_None, ms). Raises like subprocess does."""
	who, name = provider(model)
	started = time.time()
	logging.getLogger(__name__).debug("ask %s:%s tools=%s prompt=%d chars", who, name, tools, len(prompt))
	if who == "claude":
		cmd = ["claude", "-p", prompt, "--output-format", "json", "--safe-mode", "--model", name]
		if system:
			cmd += ["--append-system-prompt", system]
		if tools:
			cmd += ["--allowedTools", tools]
		if config.EFFORT:
			cmd += ["--effort", config.EFFORT]
		with tempfile.TemporaryDirectory() as here:  # ponytail: same reason as a review — see review.review
			out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout, cwd=here).stdout
		result = json.loads(out)
		return result["result"].strip(), result.get("total_cost_usd"), result.get("duration_ms")
	base, key_env = config.ENDPOINTS[who]
	messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
	sent = {"model": name, "messages": messages, "stream": False}
	if who == "openrouter":  # ponytail: only there — a local server has no cost, and may not ignore the field
		sent["usage"] = {"include": True}  # asks for usage.cost, in credits, on the response
		# ponytail: --effort was claude-only, so a reasoning model behind OpenRouter thought as hard as it
		# liked and a 2k-token diff took minutes. Same knob, same names, one translation table.
		if config.EFFORT:
			sent["reasoning"] = {"effort": REASONING.get(config.EFFORT, "high")}
	body = json.dumps(sent).encode()
	headers = {"Content-Type": "application/json"}
	key = os.environ.get(key_env, "")
	if key:  # a local server usually wants none
		headers["Authorization"] = "Bearer " + key
	req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body, headers=headers)
	try:
		with urllib.request.urlopen(req, timeout=min(timeout, READ_TIMEOUT)) as r:
			got = json.loads(read_by(r, started + timeout))
	except socket.timeout:  # ponytail: one silent read, not the whole budget, before the row clears
		raise TimeoutError(f"{who}: no bytes for {READ_TIMEOUT}s") from None
	except urllib.error.HTTPError as e:
		# ponytail: the body names which of key, model, credit or context length it was; "HTTP Error 404"
		# on its own sends you looking in the wrong place, and the row only has room for one line.
		raise OSError(f"{who} {e.code}: {detail(e.read())}") from None
	if "choices" not in got:  # openrouter answers 200 with an error object for upstream failures
		raise OSError(f"{who}: {detail(json.dumps(got).encode())}")
	cost = (got.get("usage") or {}).get("cost")  # absent unless we asked, and unless the provider reports it
	return got["choices"][0]["message"]["content"].strip(), cost, int((time.time() - started) * 1000)


def detail(raw):
	"""The human half of an error body: the message if it is the usual JSON shape, else the raw text."""
	text = raw.decode(errors="replace").strip()
	try:
		got = json.loads(text)["error"]
		return str(got.get("message", got) if isinstance(got, dict) else got)[:200]
	except (ValueError, KeyError, TypeError):
		return text[:200]


def ping(model):
	"""Prove the backend answers at all. [(name, ok, detail)] — the non-claude half of review.self_check."""
	try:
		said, _, _ = ask("Reply with exactly: OK", model, timeout=120)
	except Exception as e:  # ponytail: a check that reports its own failure, so every backend fails the same way
		logging.getLogger(__name__).exception("ping %s failed", model)
		return [(f"could not reach {provider(model)[0]}", False, str(getattr(e, "reason", None) or e)[:120])]
	return [(f"{provider(model)[0]} answers", "OK" in said.upper(), said[:80])]


def read_by(r, deadline):
	"""The whole body, or TimeoutError once `deadline` passes.

	ponytail: urlopen's timeout is per socket read, not per request. OpenRouter pads a slow generation
	with whitespace to hold the connection open, so bytes keep arriving and that timeout never fires —
	a wedged upstream spins the dashboard row forever with nothing to press. This bounds the whole read,
	and BODY_MAX bounds its size — every pad byte would otherwise sit in memory until the deadline.
	"""
	out, size = [], 0
	while True:
		chunk = r.read(65536)
		if not chunk:
			return b"".join(out)
		out.append(chunk)
		size += len(chunk)
		if size > BODY_MAX:
			raise OSError(f"answer over {BODY_MAX >> 20} MB, gave up")
		if time.time() > deadline:
			raise TimeoutError("no complete answer before the timeout")
