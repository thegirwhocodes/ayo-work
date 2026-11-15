import asyncio

from harness.case_models import Advice
from scripts.trace_synthetic_call import DEFAULT_PROJECT, SCRIPT, TRACE_TAGS, run_script, trace_settings
from tests.test_coordinator import FakeAnalyst


def test_the_synthetic_script_runs_every_turn_through_the_coordinator():
    analyst = FakeAnalyst(advice=Advice(risk="urgent", stance="Be calm.", next_move="Ask if they are safe."))
    guidance = asyncio.run(run_script(analyst, quiet=True))
    assert len(guidance) == len(SCRIPT) and guidance[-1]["status"] == "ready" and guidance[-1]["risk"] == "urgent"
    assert analyst.calls  # the case manager was asked to analyse the invented conversation


def test_traces_are_labelled_synthetic():
    settings = trace_settings(DEFAULT_PROJECT)
    assert settings["project_name"] == DEFAULT_PROJECT and "synthetic" in settings["tags"] == TRACE_TAGS and settings["metadata"]["synthetic"] is True
