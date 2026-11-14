import pytest

from app.voice.gemini_live import DEFAULT_MODEL, LiveConfig, build_setup_message


@pytest.mark.parametrize("model", ["gemini-3.8-live", "models/gemini-3.8-live", " gemini-3.8-live "])
def test_model_is_qualified_exactly_once(model):
    setup = build_setup_message(LiveConfig(system_prompt="prompt", model=model))
    assert setup["setup"]["model"] == "models/gemini-3.8-live"


def test_model_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_MODEL", "models/gemini-3.8-live")
    assert build_setup_message(LiveConfig(system_prompt="prompt"))["setup"]["model"] == "models/gemini-3.8-live"
    monkeypatch.delenv("GEMINI_LIVE_MODEL")
    assert build_setup_message(LiveConfig(system_prompt="prompt"))["setup"]["model"] == f"models/{DEFAULT_MODEL}"
