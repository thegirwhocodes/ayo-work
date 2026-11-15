"""Topic-only retrieval over the reviewed child-safeguarding corpus: the researcher subagent's one tool.

The tool accepts a topic from a closed list, never free text, and looks up the fixed query for that topic.
Caller words therefore cannot reach the embedding provider, whatever the model decides to pass. Every excerpt
found is also collected here, outside the model, so citations come from what was retrieved and not from what
the model chooses to repeat.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from harness.case_models import TOPIC_QUERIES, Topic

logger = logging.getLogger("sabi.harness.search_reference")

DESCRIPTION = (
    "Look up reviewed child-safeguarding guidance for one topic. Pass only a topic from the list; free text is not "
    "accepted. Returns numbered excerpts from approved sources. Call it once per topic you were given."
)


class SearchArgs(BaseModel):
    topic: Topic = Field(description="One topic from the closed list.")


class ReferenceSearch:
    def __init__(self, embeddings, corpus, timeout: float = 5.0, limit: int = 6):
        self.embeddings, self.corpus, self.timeout, self.limit = embeddings, corpus, timeout, limit
        self.found: list[dict] = []
        self._seen: set[str] = set()

    def reset(self):
        """Forget the previous cycle's excerpts."""
        self.found, self._seen = [], set()

    @property
    def sources(self) -> list[dict]:
        return [{key: row.get(key) for key in ("source", "section", "page")} for row in self.found]

    async def search(self, topic: str) -> str:
        query = TOPIC_QUERIES.get(topic)
        if query is None:
            return "Unknown topic."
        try:
            vector = await asyncio.wait_for(asyncio.to_thread(self.embeddings.embed_query, query), self.timeout)
            rows = await asyncio.wait_for(asyncio.to_thread(self.corpus.search, vector), self.timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Retrieval unavailable: %s", type(exc).__name__)
            return "Reference search is unavailable. Continue without it."
        lines = []
        for row in rows:
            key = hashlib.sha256(row["text"].encode()).hexdigest()
            if key in self._seen or len(self.found) >= self.limit:
                continue
            self._seen.add(key)
            self.found.append(row)
            lines.append(f"[{len(self.found)}] {row['text']}")
        return "\n\n".join(lines) or "No approved excerpts for this topic."


def build_search_reference_tool(search: ReferenceSearch) -> StructuredTool:
    async def search_reference(topic: str) -> str:
        return await search.search(topic)

    return StructuredTool.from_function(coroutine=search_reference, name="search_reference",
                                        description=DESCRIPTION, args_schema=SearchArgs)
