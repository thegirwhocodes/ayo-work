"""LangSmith tracing policy: real calls are never traced; a synthetic, scripted call can be.

A trace records every prompt and model reply, and on a real call those are a child's words. So:

* The live application turns tracing off for the whole process (``disable_live_tracing``, called at startup),
  whatever the environment says.
* The case manager traces only when it is handed an explicit ``trace`` setting (``trace_context``). The only
  caller that does is ``scripts/trace_synthetic_call.py``, which feeds it a made-up conversation.

Studio (``langgraph dev``) and the synthetic script run in their own processes, so they are not affected by the
application's switch. Studio traces only if you start it with LangSmith tracing on, and you then type the input.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Optional

TRACING_VARIABLES = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


def disable_live_tracing() -> None:
    """Switch LangSmith tracing off for this process. Call before any model call is made."""
    for name in TRACING_VARIABLES:
        os.environ[name] = "false"


@contextmanager
def trace_context(trace: Optional[dict]):
    """Run a block with tracing forced off (``trace`` is None) or on with the given project, tags and metadata.

    Forcing it off here, and not only through the environment, keeps a real call untraced even if
    something re-enabled tracing after startup.
    """
    from langsmith import tracing_context

    if trace is None:
        with tracing_context(enabled=False):
            yield
    else:
        # ``trace`` is passed to langsmith.tracing_context as is: project_name, tags, metadata, and for tests a
        # stand-in ``client`` that records runs in memory instead of uploading them.
        with tracing_context(enabled=True, **trace):
            yield
