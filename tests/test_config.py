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
