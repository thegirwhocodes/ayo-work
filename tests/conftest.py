"""Keep tests from ever reaching real providers or leaking state between tests."""

import pytest

from app.location import lane


@pytest.fixture(autouse=True)
def _location_isolation(monkeypatch):
    lane.reset_for_tests()

    async def refuse(recipient, text):  # a test that forgets to inject a messenger must not text anyone
        return {"status": "unavailable", "reason": "blocked_in_tests"}

    monkeypatch.setattr(lane, "_default_messenger", refuse)
    for name in ("LOCATION_LINK_BASE_URL", "PUBLIC_BASE_URL", "LOCATION_CONSOLE_KEY", "SIM_DEMO_NUMBERS",
                 "SIM_PRINCETON_LATLNG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SIM_LOCATION_DELAY_S", "0")
    yield
    lane.reset_for_tests()
