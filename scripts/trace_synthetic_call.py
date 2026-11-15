#!/usr/bin/env python3
"""Run one made-up call through the case manager with LangSmith tracing on, to get a trace you can show.

    python -m scripts.trace_synthetic_call
    python -m scripts.trace_synthetic_call --project my-demo-project

The conversation below is invented for this demo. It is the ONLY input this script accepts, on purpose: real
calls are never traced (a trace records every prompt and reply, which on a real call are a child's words), and
the live application switches tracing off for itself (harness/tracing.py). Tracing here is switched on per run,
for this script's own case manager only.

Needs GEMINI_API_KEY and LANGSMITH_API_KEY. Retrieval from the reviewed corpus is used when DATABASE_URL and
COHERE_API_KEY are set; otherwise the case manager runs without a researcher.

Open the project in LangSmith afterwards: each cycle is one run named case_manager, with the orchestrator, the
specialists it delegated to, the advisor reading a skill, and the rubric grader's verdicts underneath.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

# (what the child says, what the voice agent answers). Invented. The risk rises over the call.
SCRIPT = [
    ("Hi. I can't sleep. There is shouting downstairs and I'm scared.",
     "I am right here with you. You are not alone. Where are you right now?"),
    ("I'm in my room. My dad is really angry again. He throws things when he is like this.",
     "Thank you for telling me. That sounds really frightening. Is your door closed?"),
    ("Yes. I don't want him to know I'm awake. Can you promise you won't tell anyone?",
     "I care about you too much to keep a secret if someone is hurting you. We will work this out together."),
    ("He just broke something and I think he is coming up the stairs.",
     "I am staying with you. Can you get somewhere safe, or is there a grown-up you trust nearby?"),
]
DEFAULT_PROJECT = "sabi-safe-synthetic-demo"
TRACE_TAGS = ["synthetic", "hackathon-demo"]


def trace_settings(project: str) -> dict:
    return {"project_name": project, "tags": TRACE_TAGS, "metadata": {"synthetic": True, "script": "night-fear-v1"}}


async def run_script(analyst, turns=SCRIPT, quiet: bool = False) -> list[dict]:
    """Feed the scripted turns to a coordinator one at a time and return the guidance after each."""
    from harness.coordinator import Coordinator

    coordinator = Coordinator(analyst, min_interval=0.0, cycle_timeout=300.0)
    guidance = []
    try:
        for number, (child, assistant) in enumerate(turns, 1):
            coordinator.observe(child, assistant)
            await coordinator.idle()
            latest = coordinator.latest()
            guidance.append(latest)
            if not quiet:
                print(f"turn {number}: status={latest.get('status')} risk={latest.get('risk')} checked={latest.get('checked')}")
    finally:
        await coordinator.close()
    return guidance


def build_analyst(project: str):
    from harness.case_manager import gemini_case_manager

    names = [os.getenv("COORDINATOR_MODEL") or os.getenv("HARNESS_MODEL", "gemini-3.5-flash")]
    names += [m.strip() for m in os.getenv("COORDINATOR_FALLBACK_MODELS", "gemini-3.5-flash-lite").split(",") if m.strip()]
    search = None
    if os.getenv("DATABASE_URL") and os.getenv("COHERE_API_KEY"):
        from harness.corpus.model import cohere_embeddings
        from harness.corpus.store import CorpusStore
        from harness.tools.search_reference import ReferenceSearch

        search = ReferenceSearch(cohere_embeddings(), CorpusStore(os.environ["DATABASE_URL"]))
    return gemini_case_manager(names, os.environ["GEMINI_API_KEY"], search, trace=trace_settings(project))


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", default=os.getenv("LANGSMITH_PROJECT") or DEFAULT_PROJECT,
                        help=f"LangSmith project for the trace (default {DEFAULT_PROJECT})")
    args = parser.parse_args()
    missing = [name for name in ("GEMINI_API_KEY", "LANGSMITH_API_KEY") if not os.getenv(name)]
    if missing:
        print("Set " + " and ".join(missing) + " first (see .env.example).", file=sys.stderr)
        return 2

    print(f"Running a synthetic call ({len(SCRIPT)} turns), tracing to LangSmith project '{args.project}'")
    asyncio.run(run_script(build_analyst(args.project)))
    from langchain_core.tracers.langchain import wait_for_all_tracers

    wait_for_all_tracers()  # send the buffered trace before the process exits
    print(f"Done. Open LangSmith, project '{args.project}', and look for the runs tagged 'synthetic'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
