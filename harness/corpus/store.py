"""Approved corpus retrieval."""
import psycopg
from harness.corpus.ingest import MODEL, vector_literal


class CorpusStore:
    def __init__(self, database_url):
        self.database_url = database_url

    def search(self, vector, limit=4):
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute("""SELECT c.content, s.title, s.url, c.section, c.page_reference
                FROM corpus_chunks c JOIN corpus_sources s ON s.id=c.source_id
                WHERE c.review_status='approved' AND s.review_status='approved'
                  AND c.embedding_model=%s AND c.embedding_version=%s
                ORDER BY c.embedding <=> %s::vector LIMIT %s""",
                (MODEL, MODEL, vector_literal(vector), limit)).fetchall()
        return [{"text": r[0], "source": r[1], "url": r[2], "section": r[3], "page": r[4]} for r in rows]
