import json

from dashy import config


def test_saved_settings_load_unless_env_wins(tmp_path, monkeypatch):
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "s.json"))
	monkeypatch.setenv("PRS_THEME", "nord")
	config.save({"theme": "dracula", "notify": False, "window": None, "bogus": 1})
	monkeypatch.setattr(config, "THEME", "dashy")
	monkeypatch.setattr(config, "NOTIFY", True)
	monkeypatch.setattr(config, "WINDOW", 4)
	config.load()
	assert config.THEME == "dashy" and config.NOTIFY is False and config.WINDOW is None  # env beats file, file beats default
	(tmp_path / "s.json").write_text("{not json")
	config.load()  # a broken file is ignored, not fatal


def test_stale_checklist_values_are_dropped_on_load(tmp_path, monkeypatch):
	"""ponytail was a voice before it was a hunter; a settings file from then must not stop startup."""
	monkeypatch.setattr(config, "SETTINGS", str(tmp_path / "s.json"))
	config.save({"voice": ["review", "ponytail", "caveman"], "hunter": ["ponytail", "gone"]})
	monkeypatch.setattr(config, "VOICE", ["review"])
	monkeypatch.setattr(config, "HUNTER", [])
	config.load()
	assert config.VOICE == ["review", "caveman"] and config.HUNTER == ["ponytail"]
	config.save({"voice": ["ponytail"]})
	config.load()
	assert config.VOICE == ["review"]  # nothing valid left: the plain review, never an empty body
