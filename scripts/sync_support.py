"""Container init step: apply the support schema, then sync corpus chunks.

The migration must succeed. Chunk syncing is best-effort so an embedding-provider
outage cannot keep the phone service from starting; pass --strict to fail on it.
"""
import argparse
import logging
import os
import sys

import psycopg

from harness.corpus.model import LazyEmbeddings, cohere_embeddings
from harness.corpus.sync import sync_corpus
from scripts.migrate_support import main as migrate

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("sabi.support-init")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="exit non-zero if any source fails to sync")
    strict = parser.parse_args().strict
    migrate()
    logger.info("Support schema is up to date.")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as connection:
        results = sync_corpus(connection, LazyEmbeddings(cohere_embeddings))
    for title, status in results:
        logger.info("Corpus %s: %s", title, status)
    if strict and any(status.startswith("failed") for _, status in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
