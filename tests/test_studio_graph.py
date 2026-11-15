import asyncio
import json
from pathlib import Path

import pytest

from harness.studio import build_graph, make_graph
from harness.tools.search_reference import ReferenceSearch
from tests.test_case_manager import Corpus, Embeddings, Team, advice

ROOT = Path(__file__).resolve().parent.parent


def test_studio_graph_shows_the_case_manager_between_prepare_and_report():
    graph = build_graph(Team(advice=[advice()], verdicts=["satisfied"], calls=[]))
    assert {"prepare", "case_manager", "report"} <= set(graph.get_graph().nodes)


def test_studio_graph_turns_a_typed_transcript_into_the_advisors_analysis():
    team = Team(advice=[advice(next_move="Stay with them.")], verdicts=["satisfied"], calls=[])
    graph = build_graph(team, search=ReferenceSearch(Embeddings(), Corpus()))
    result = asyncio.run(graph.ainvoke({"transcript": "Child: I can't sleep\nAssistant: I am here.", "profile": {}}))
    analysis = result["analysis"]
    assert analysis["risk"] == "urgent" and analysis["next_move"] == "Stay with them."
    assert analysis["ran"] == ["advisor", "researcher", "triage"]


def test_langgraph_json_registers_the_case_manager_next_to_the_emergency_agent():
    config = json.loads((ROOT / "langgraph.json").read_text())
    assert config["graphs"]["case_manager"] == "./harness/studio.py:make_graph"
    assert config["graphs"]["emergency"] == "./harness/emergency_subagent.py:graph"
    path, _, name = config["graphs"]["case_manager"].partition(":")
    assert (ROOT / path).is_file() and callable(make_graph) and name == "make_graph"


def test_the_studio_graph_needs_a_model_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        make_graph()


def test_the_studio_graph_stays_out_of_the_production_agent_server_image():
    assert "studio" not in (ROOT / "Dockerfile.agent-server").read_text()
