"""Apply the emergency job schema to the configured application database."""

from pathlib import Path

import psycopg
from cryptography.fernet import Fernet

from settings import get_settings


def main():
    settings = get_settings()
    database_url = settings.database_url
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    if len(settings.emergency_worker_secret) < 32:
        raise RuntimeError("EMERGENCY_WORKER_SECRET must contain at least 32 characters")
    if len(settings.agent_server_auth_secret) < 32:
        raise RuntimeError("AGENT_SERVER_AUTH_SECRET must contain at least 32 characters")
    if not settings.emergency_job_encryption_key:
        raise RuntimeError("EMERGENCY_JOB_ENCRYPTION_KEY is required")
    Fernet(settings.emergency_job_encryption_key.encode())
    migration = Path(__file__).resolve().parent.parent / "migrations" / "002_emergency_jobs.sql"
    with psycopg.connect(database_url) as connection:
        connection.execute(migration.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
