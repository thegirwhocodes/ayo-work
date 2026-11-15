import os

# The app loads .env on import. Tests must never inherit a developer's opt-in to tracing live calls.
os.environ["TRACE_LIVE_CALLS"] = "false"

import pytest

from harness.tool_log import bind_call, clear_events


@pytest.fixture(autouse=True)
def isolated_event_buffer():
    """Each test starts with no bound call and an empty dashboard event buffer."""
    bind_call(None)
    clear_events()
    yield
    bind_call(None)
    clear_events()
