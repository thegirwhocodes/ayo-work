"""Apply the support schema migration to a pgvector-enabled PostgreSQL database."""
import os
from pathlib import Path
import psycopg

migration = Path(__file__).resolve().parent.parent / "migrations" / "001_support_rag.sql"
with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
    connection.execute(migration.read_text(encoding="utf-8"))
