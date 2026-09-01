import prs

def test_rows_and_age():
	p = {"url": "u", "updatedAt": "2020-01-01T00:00:00Z", "number": 1, "title": "t", "isDraft": False,
	     "repository": {"nameWithOwner": "a/b"}, "author": {"login": "me"}}
	rs = prs.rows([("MINE", [p], None), ("REVIEW REQUESTED", [], None), ("ASSIGNED", None, "boom\nmore")])
	kinds = [k for k, _ in rs]
	assert kinds == ["head", "pr", "blank", "head", "empty", "blank", "head", "err", "blank"], kinds
	assert rs[0][1] == "MINE (1)" and rs[6][1] == "ASSIGNED (!)" and rs[7][1] == "boom"
	assert prs.age("2020-01-01T00:00:00Z").endswith("d")

def test_fetch_dedups():
	secs = prs.fetch()
	urls = [p["url"] for _, ps, _ in secs for p in ps or []]
	assert len(urls) == len(set(urls))
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED", "REVIEWED"]

def test_review_parses_and_posts(monkeypatch, tmp_path):
	monkeypatch.setattr(prs, "LOG", str(tmp_path / "log.jsonl"))
	calls = []
	def fake_run(cmd, **kw):
		calls.append(cmd)
		class R: stdout = '{"result": "Sure:\\n{\\"verdict\\": \\"request_changes\\", \\"body\\": \\"nope\\"}"}'
		return R()
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	pr = {"repository": {"nameWithOwner": "a/b"}, "number": 7, "url": "u"}
	assert prs.review(pr, "opus") == "✗ changes requested"
	assert calls[0][calls[0].index("--model") + 1] == "opus"
	assert calls[1][:6] == ["gh", "pr", "review", "7", "--repo", "a/b"] and "--request-changes" in calls[1]
	assert calls[1][-1] == "nope"

def test_auto_reviews_only_new(monkeypatch):
	started = []
	monkeypatch.setattr(prs.State, "start_review", lambda self, p: started.append(p["url"]))
	old = {"url": "old"}; new = {"url": "new"}
	monkeypatch.setattr(prs, "fetch", lambda: [("REVIEW REQUESTED", [old, new], None)])
	st = prs.State(0)
	st.sections = [("REVIEW REQUESTED", [old], None)]
	st.set_auto(True)
	# run one iteration of loop
	monkeypatch.setattr(st.wake, "wait", lambda t: (_ for _ in ()).throw(SystemExit))
	try: st.loop()
	except SystemExit: pass
	assert started == ["new"]

def test_review_logs_and_reviewed_reads(monkeypatch, tmp_path):
	monkeypatch.setattr(prs, "LOG", str(tmp_path / "log.jsonl"))
	def fake_run(cmd, **kw):
		class R: stdout = '{"result": "{\\"verdict\\": \\"approve\\", \\"summary\\": \\"adds x\\", \\"body\\": \\"lgtm\\"}"}'
		return R()
	monkeypatch.setattr(prs.subprocess, "run", fake_run)
	pr = {"repository": {"nameWithOwner": "a/b", "name": "b"}, "number": 7, "url": "u", "title": "T",
	      "isDraft": False, "author": {"login": "me"}, "updatedAt": "2020-01-01T00:00:00Z"}
	assert prs.review(pr, "opus") == "✓ approved"
	got = prs.reviewed()
	assert len(got) == 1 and got[0]["status"] == "✓ approved" and got[0]["url"] == "u"
	d = prs.detail(got[0]["review"])
	assert "a/b#7  T" in d and "adds x" in d and "lgtm" in d
	assert prs.rows([("REVIEWED", got, None)])[1][1]["section"] == "REVIEWED"
