"""Apply the support schema migration to a pgvector-enabled PostgreSQL database."""
import os
from pathlib import Path

import psycopg

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "001_support_rag.sql"


def main():
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute(MIGRATION.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
