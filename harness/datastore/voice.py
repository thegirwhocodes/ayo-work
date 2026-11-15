"""PostgreSQL storage for enrolled speaker embeddings."""

import psycopg

from harness.tools import MODEL_SOURCE


class PostgresVoiceStore:
    def __init__(self, database_url: str):
        if not database_url:
            raise ValueError("DATABASE_URL is required for voice verification")
        self.database_url = database_url

    def initialize(self):
        with psycopg.connect(self.database_url) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS voice_enrollments (
                    caller_id TEXT PRIMARY KEY,
                    model_source TEXT NOT NULL,
                    embedding REAL[] NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

    def get(self, caller_id: str) -> list[float] | None:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT model_source, embedding FROM voice_enrollments WHERE caller_id = %s",
                (caller_id,),
            ).fetchone()
        if row is None:
            return None
        if row[0] != MODEL_SOURCE:
            raise ValueError("enrollment uses a different embedding model")
        return row[1]

    def delete(self, caller_id: str) -> bool:
        """Forget this caller's voiceprint so the next call enrolls afresh. True if one existed."""
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute(
                "DELETE FROM voice_enrollments WHERE caller_id = %s RETURNING caller_id", (caller_id,),
            ).fetchone()
        return result is not None

    def enroll(self, caller_id: str, embedding: list[float]) -> bool:
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute(
                "INSERT INTO voice_enrollments (caller_id, model_source, embedding) "
                "VALUES (%s, %s, %s) ON CONFLICT (caller_id) DO NOTHING RETURNING caller_id",
                (caller_id, MODEL_SOURCE, embedding),
            ).fetchone()
        return result is not None
