"""The case manager as a LangGraph graph, for LangGraph Studio (``langgraph dev``).

Studio shows what the case manager does on a call: which specialists the orchestrator delegates to, the advisor
reading a skill, the rubric grading its advice and asking for a revision. This is the same agent a live call runs
(``harness.case_manager.build_agent``), wrapped in a small outer graph so you can type a conversation in:

    prepare        writes the case file the specialists read, from ``transcript`` and ``profile``
    case_manager   the Deep Agents team (orchestrator -> triage / researcher / advisor, advice graded by the rubric)
    report         reads the advisor's structured answer into ``analysis``

Run it with synthetic text only. Studio is a development tool and a real child's words do not belong in it.

Input:   {"transcript": "Child: I can't sleep, I'm scared\\nAssistant: I'm right here with you.", "profile": {}}
Output:  ``analysis`` with risk, stance, do, avoid, next_move and the specialists that ran.

It is deliberately not in the production Agent Server image (Dockerfile.agent-server): the live call runs the case
manager inside the application, not through this graph.
"""

from __future__ import annotations

import os
from typing import Annotated, Optional, Sequence

from deepagents.backends.utils import create_file_data
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from harness.case_manager import CASE_PATH, build_agent, render_case_file, specialist_replies
from harness.case_models import Advice
from harness.tools.search_reference import ReferenceSearch


class StudioState(TypedDict, total=False):
    transcript: str  # the conversation so far, one "Child: ..." or "Assistant: ..." line per turn
    profile: dict  # known communication preferences (closed vocabulary), optional
    messages: Annotated[list, add_messages]
    files: dict
    analysis: dict


async def prepare(state: StudioState) -> dict:
    case = render_case_file(state.get("transcript", ""), state.get("profile") or {})
    return {"messages": [HumanMessage(content="Analyse the call now.")], "files": {CASE_PATH: create_file_data(case)}}


async def report(state: StudioState) -> dict:
    replies = specialist_replies(state.get("messages", []))
    try:
        advice = Advice.model_validate_json(replies.get("advisor") or "")
    except Exception:
        return {"analysis": {"error": "the advisor returned no advice", "ran": sorted(replies)}}
    return {"analysis": {**advice.model_dump(), "ran": sorted(replies)}}


def build_graph(model, fallbacks: Sequence = (), search: Optional[ReferenceSearch] = None):
    """Wrap the case-manager agent (a subgraph, expandable in Studio) between ``prepare`` and ``report``."""
    builder = StateGraph(StudioState)
    builder.add_node("prepare", prepare)
    builder.add_node("case_manager", build_agent(model, fallbacks, search))
    builder.add_node("report", report)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "case_manager")
    builder.add_edge("case_manager", "report")
    builder.add_edge("report", END)
    return builder.compile(name="case_manager_studio")


def make_graph():
    """Studio entry point (langgraph.json). Models come from the environment, as on a call."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY to run the case manager in Studio")
    names = [os.getenv("COORDINATOR_MODEL") or os.getenv("HARNESS_MODEL", "gemini-3.5-flash")]
    names += [m.strip() for m in os.getenv("COORDINATOR_FALLBACK_MODELS", "gemini-3.5-flash-lite").split(",") if m.strip()]
    chats = [ChatGoogleGenerativeAI(model=name, api_key=key) for name in dict.fromkeys(names)]
    search = None
    if os.getenv("DATABASE_URL") and os.getenv("COHERE_API_KEY"):
        from harness.corpus.model import cohere_embeddings
        from harness.corpus.store import CorpusStore

        search = ReferenceSearch(cohere_embeddings(), CorpusStore(os.environ["DATABASE_URL"]))
    return build_graph(chats[0], chats[1:], search)
