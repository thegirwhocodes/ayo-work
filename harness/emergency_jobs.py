"""Encrypted, one-use emergency jobs shared across application workers."""

import hashlib
import json
import secrets

import psycopg
from cryptography.fernet import Fernet

from harness.tools import EmergencyCallContext, EmergencyTools
from settings import get_settings


def job_store_config():
    settings = get_settings()
    if not settings.database_url or not settings.emergency_job_encryption_key:
        raise ValueError("Emergency job database and encryption key are required")
    return settings.database_url, Fernet(settings.emergency_job_encryption_key.encode())


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_job(tools: EmergencyTools, session_id: str) -> str:
    database_url, cipher = job_store_config()
    if len(get_settings().emergency_worker_secret) < 32:
        raise ValueError("Emergency worker secret must contain at least 32 characters")
    token = secrets.token_urlsafe(32)
    payload = cipher.encrypt(json.dumps({
        "caller_number": tools.context.caller_number,
        "responder_consent": tools.context.responder_consent,
        "action": tools.action,
        "message": tools.message,
    }).encode())
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        await connection.execute(
            "INSERT INTO emergency_jobs (token_hash, session_id, action, payload, state, expires_at) "
            "VALUES (%s, %s, %s, %s, 'pending', now() + interval '2 minutes')",
            (token_hash(token), session_id, tools.action, payload),
        )
    return token


async def execute_job(token: str) -> dict:
    database_url, cipher = job_store_config()
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        cursor = await connection.execute(
            "UPDATE emergency_jobs SET state = 'processing' "
            "WHERE token_hash = %s AND state = 'pending' AND expires_at > now() RETURNING payload",
            (token_hash(token),),
        )
        row = await cursor.fetchone()
    if row is None:
        return {"status": "unavailable", "reason": "invalid_or_expired_job"}
    try:
        payload = json.loads(cipher.decrypt(bytes(row[0])))
        context = EmergencyCallContext(payload["caller_number"], payload["responder_consent"])
        tools = EmergencyTools(context, payload["action"], payload["message"])
        await tools.run(tools.action)
        result = tools.result or {"status": "unavailable", "reason": "tool_not_called"}
    except Exception:
        result = {"status": "unavailable", "reason": "tool_failed"}
    encrypted_result = cipher.encrypt(json.dumps(result).encode())
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        await connection.execute(
            "UPDATE emergency_jobs SET state = 'finished', payload = ''::bytea, result = %s "
            "WHERE token_hash = %s AND state = 'processing'",
            (encrypted_result, token_hash(token)),
        )
    return {"status": "finished"}


async def get_job_result(token: str) -> dict | None:
    database_url, cipher = job_store_config()
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        cursor = await connection.execute(
            "DELETE FROM emergency_jobs WHERE token_hash = %s AND state = 'finished' RETURNING result",
            (token_hash(token),),
        )
        row = await cursor.fetchone()
    return json.loads(cipher.decrypt(bytes(row[0]))) if row else None


async def cancel_job(token: str):
    database_url, _ = job_store_config()
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        await connection.execute(
            "UPDATE emergency_jobs SET state = 'cancelled', payload = ''::bytea WHERE token_hash = %s AND state = 'pending'",
            (token_hash(token),),
        )


async def cancel_session_jobs(session_id: str, responder_only: bool = False):
    database_url, _ = job_store_config()
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        await connection.execute(
            "UPDATE emergency_jobs SET state = 'cancelled', payload = ''::bytea WHERE session_id = %s AND state = 'pending' "
            "AND (%s = false OR action = 'send_text_message')",
            (session_id, responder_only),
        )


async def purge_expired_jobs():
    database_url, _ = job_store_config()
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        await connection.execute("DELETE FROM emergency_jobs WHERE expires_at < now() - interval '1 hour'")
