import asyncio
import os
from unittest.mock import MagicMock

from langsmith.run_helpers import get_tracing_context

from harness.case_manager import CaseManager
from harness.tracing import TRACING_VARIABLES, disable_live_tracing, trace_context
from tests.test_case_manager import Team, advice


def test_live_tracing_is_switched_off_whatever_the_environment_says(monkeypatch):
    for name in TRACING_VARIABLES:
        monkeypatch.setenv(name, "true")
    disable_live_tracing()
    assert all(os.environ[name] == "false" for name in TRACING_VARIABLES)


def test_trace_context_forces_tracing_off_or_on_with_the_given_settings():
    with trace_context(None):
        assert get_tracing_context()["enabled"] is False
    with trace_context({"project_name": "demo", "tags": ["synthetic"], "metadata": {"synthetic": True}, "client": MagicMock()}):
        context = get_tracing_context()
        assert context["enabled"] is True and context["project_name"] == "demo" and context["tags"] == ["synthetic"]



class Spy:
    """Wraps the case-manager agent and records whether tracing was on while it ran."""

    def __init__(self, agent):
        self.agent, self.enabled = agent, []

    async def ainvoke(self, *args, **kwargs):
        self.enabled.append(get_tracing_context()["enabled"])
        return await self.agent.ainvoke(*args, **kwargs)


def cycle(manager):
    manager.agent = Spy(manager.agent)
    asyncio.run(manager("Child: hi\nAssistant: hello", {}))
    return manager.agent.enabled


def test_a_real_call_cycle_is_never_traced_even_if_the_environment_enables_tracing(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    manager = CaseManager(Team(advice=[advice()], verdicts=["satisfied"], calls=[]))
    assert manager.trace is None
    assert cycle(manager) == [False]


def test_only_a_case_manager_given_trace_settings_traces():
    # A mock client takes the runs, so this test never uploads anything.
    client = MagicMock()
    manager = CaseManager(Team(advice=[advice()], verdicts=["satisfied"], calls=[]),
                          trace={"client": client, "project_name": "demo", "tags": ["synthetic"]})
    assert cycle(manager) == [True]
    assert client.create_run.called or client.multipart_ingest.called or client.method_calls  # runs went to the stand-in


def test_live_calls_are_untraced_unless_opted_in(monkeypatch):
    from harness.tracing import live_trace_settings, live_tracing_allowed

    monkeypatch.delenv("TRACE_LIVE_CALLS", raising=False)
    assert not live_tracing_allowed() and live_trace_settings() is None
    monkeypatch.setenv("TRACE_LIVE_CALLS", "true")
    monkeypatch.setenv("LANGSMITH_PROJECT", "p")
    settings = live_trace_settings()
    assert settings["project_name"] == "p" and "live-test-call" in settings["tags"]
