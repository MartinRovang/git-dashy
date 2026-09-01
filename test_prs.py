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
	assert [n for n, _, _ in secs] == ["MINE", "REVIEW REQUESTED", "ASSIGNED"]

def test_review_parses_and_posts(monkeypatch):
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
