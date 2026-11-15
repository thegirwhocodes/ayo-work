"""Keep the vector store in step with the corpus manifest.

Each source's content digest (plus the chunking/embedding pipeline signature) is
compared with the stored one. Unchanged sources are skipped without chunking or
calling the embedding provider; changed ones are re-chunked and re-embedded.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from harness.corpus.ingest import ingest_source, is_current, sections_from_markdown

logger = logging.getLogger("sabi.corpus.sync")
MANIFEST = Path(__file__).resolve().parent / "sources.json"
LOCK_KEY = 7_262_001  # serialises concurrent starts (e.g. several replicas)


def load_sources(manifest=MANIFEST):
    manifest = Path(manifest)
    sources = []
    for entry in json.loads(manifest.read_text(encoding="utf-8")):
        entry = dict(entry)
        entry["sections"] = sections_from_markdown(manifest.parent / entry.pop("markdown"))
        sources.append(entry)
    return sources


def sync_corpus(connection, make_embeddings, manifest=MANIFEST):
    """`make_embeddings` is called only when a source must be embedded. Return [(title, status)] where status is skipped/unchanged/ingested/failed."""
    results = []
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    try:
        for source in load_sources(manifest):
            title = source["title"]
            if source.get("review_status") != "approved" or not source.get("rights_verified"):
                # Withdrawing approval in the manifest also withdraws it from retrieval.
                with connection.cursor() as cur:
                    cur.execute("UPDATE corpus_sources SET review_status='pending', updated_at=now() "
                                "WHERE url=%s AND version=%s AND review_status='approved'",
                                (source["url"], source["version"]))
                results.append((title, "skipped: not approved"))
                continue
            try:
                if is_current(connection, source):
                    results.append((title, "unchanged"))
                    continue
                # Only now is an embedding client (and its API key) needed.
                count = ingest_source(connection, source, make_embeddings())
                results.append((title, f"ingested {count} chunks"))
            except Exception as exc:
                logger.error("Corpus ingest failed for %s: %s", title, type(exc).__name__)
                results.append((title, f"failed: {type(exc).__name__}"))
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return results
