from settings import get_settings


def test_settings_env_overrides_dotenv_and_cache_can_be_cleared(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PHOTON_API_KEY", raising=False)
    (tmp_path / ".env").write_text("PHOTON_PROJECT_ID=from-file\nPHOTON_API_KEY=file-secret\n")
    monkeypatch.setenv("PHOTON_PROJECT_ID", "from-env")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.photon_project_id == "from-env"
        assert settings.photon_api_key == "file-secret"
        assert "file-secret" not in repr(settings)
        monkeypatch.setenv("PHOTON_PROJECT_ID", "updated")
        assert get_settings() is settings
        get_settings.cache_clear()
        assert get_settings().photon_project_id == "updated"
    finally:
        get_settings.cache_clear()
