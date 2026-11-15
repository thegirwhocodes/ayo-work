"""Encrypted, one-use emergency jobs shared across application workers."""

import hashlib
import json
import secrets
import time

import psycopg
from cryptography.fernet import Fernet

from harness import emergency_plan as plan
from harness.emergency_plan import BACKUP_ACTION, EVENT_KINDS, VOICE_NOTES
from harness.tool_log import log_tool_event, short_ref
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
        "created": int(time.time()),
        "events": [],
        "notes": [],
    }).encode())
    # The capability lives as long as the escalation plan may run, and no longer.
    ttl = plan.PLAN_WINDOW_SECONDS + 120
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        await connection.execute(
            "INSERT INTO emergency_jobs (token_hash, session_id, action, payload, state, expires_at) "
            "VALUES (%s, %s, %s, %s, 'pending', now() + %s * interval '1 second')",
            (token_hash(token), session_id, tools.action, payload, ttl),
        )
    return token


ACTIONS = ("send_text_message", "request_location", "start_phone_call", BACKUP_ACTION)
IN_PROGRESS = {"status": "unknown", "reason": "in_progress_or_interrupted"}
_STATUS_ORDER = ("submitted", "unknown", "consent_required", "unavailable")


def _connect(database_url):
    return psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000")


def summarize(results: dict) -> dict:
    """Fold per-action results into one outcome: submitted > unknown > consent_required > unavailable."""
    if not results:
        return {"status": "unknown", "reason": "no_action_taken", "actions": {}}
    statuses = [result.get("status") for result in results.values()]
    overall = next((status for status in _STATUS_ORDER if status in statuses), statuses[0])
    first = next(result for result in results.values() if result.get("status") == overall)
    summary = {"status": overall, "actions": results}
    if first.get("reason"):
        summary["reason"] = first["reason"]
    return summary


async def _load(connection, cipher, token: str, lock: bool):
    """Return the decrypted payload of a live pending job, None if there is none, or False if it is unreadable."""
    cursor = await connection.execute(
        "SELECT payload FROM emergency_jobs WHERE token_hash = %s AND state = 'pending' AND expires_at > now()"
        + (" FOR UPDATE" if lock else ""), (token_hash(token),))
    row = await cursor.fetchone()
    if row is None:
        return None
    try:
        return json.loads(cipher.decrypt(bytes(row[0])))
    except Exception:
        return False


async def job_briefing(token: str) -> dict:
    """What the subagent may know: the requested action and the consent state, never the caller's words."""
    database_url, cipher = job_store_config()
    async with await _connect(database_url) as connection:
        payload = await _load(connection, cipher, token, lock=False)
    if payload is None:
        return {"status": "unavailable", "reason": "invalid_or_expired_job"}
    if payload is False:
        return {"status": "unavailable", "reason": "tool_failed"}
    return {"status": "ok", "requested_action": payload["action"], "responder_consent": bool(payload["responder_consent"]),
            "has_message": bool((payload.get("message") or "").strip()), "actions_done": sorted(payload.get("results", {}))}


async def run_job_action(token: str, action: str) -> dict:
    """Run one emergency action for a job, at most once per action.

    The action is recorded as in progress before it runs, so a retry, a duplicate call or a crash can never
    send the same text or place the same call twice; they get the stored (or ``unknown``) result instead.
    """
    if action not in ACTIONS:
        return {"status": "unavailable", "reason": "action_not_allowed"}
    database_url, cipher = job_store_config()
    async with await _connect(database_url) as connection:
        async with connection.transaction():
            payload = await _load(connection, cipher, token, lock=True)
            if payload is None:
                return {"status": "unavailable", "reason": "invalid_or_expired_job"}
            if payload is False:
                return {"status": "unavailable", "reason": "tool_failed"}
            results = payload.setdefault("results", {})
            if action in results:
                return results[action]
            results[action] = IN_PROGRESS
            await connection.execute("UPDATE emergency_jobs SET payload = %s WHERE token_hash = %s",
                                     (cipher.encrypt(json.dumps(payload).encode()), token_hash(token)))
    try:
        context = EmergencyCallContext(payload["caller_number"], payload["responder_consent"])
        tools = EmergencyTools(context, action, payload["message"])
        await tools.run(action)
        result = tools.result or {"status": "unavailable", "reason": "tool_not_called"}
    except Exception as exc:
        log_tool_event("emergency_job_error", job_ref=token_hash(token)[:12], action=action, error=type(exc).__name__)
        result = {"status": "unavailable", "reason": "tool_failed"}
    async with await _connect(database_url) as connection:
        async with connection.transaction():
            current = await _load(connection, cipher, token, lock=True)
            if current:  # the job may have been cancelled while the action ran
                current.setdefault("results", {})[action] = result
                await connection.execute("UPDATE emergency_jobs SET payload = %s WHERE token_hash = %s",
                                         (cipher.encrypt(json.dumps(current).encode()), token_hash(token)))
    return result


async def finish_job(token: str) -> dict:
    """Close the job: erase the payload and store the combined outcome for the app to collect once."""
    database_url, cipher = job_store_config()
    async with await _connect(database_url) as connection:
        async with connection.transaction():
            payload = await _load(connection, cipher, token, lock=True)
            if payload is None:
                return {"status": "unavailable", "reason": "invalid_or_expired_job"}
            summary = {"status": "unavailable", "reason": "tool_failed"} if payload is False else summarize(payload.get("results", {}))
            if payload and payload.get("notes"):
                summary["notes"] = payload["notes"]  # so a note written just before closing still reaches the call
            await connection.execute(
                "UPDATE emergency_jobs SET state = 'finished', payload = ''::bytea, result = %s WHERE token_hash = %s",
                (cipher.encrypt(json.dumps(summary).encode()), token_hash(token)))
    log_tool_event("emergency_job_closed", job_ref=token_hash(token)[:12], status=summary.get("status"),
                   actions=sorted(summary.get("actions", {})))
    return {"status": "finished"}


def _statuses(payload: dict) -> dict[str, str]:
    return {name: (result or {}).get("status", "unknown") for name, result in payload.get("results", {}).items()}


async def _save(connection, cipher, key: str, payload: dict):
    await connection.execute("UPDATE emergency_jobs SET payload = %s WHERE token_hash = %s",
                             (cipher.encrypt(json.dumps(payload).encode()), key))


async def poll_job_events(token: str) -> dict:
    """The agent's view of a running job: events it has not seen yet (each is delivered once), the status of
    every action so far, and how long the escalation window has left. Never any caller or responder words."""
    database_url, cipher = job_store_config()
    async with await _connect(database_url) as connection:
        async with connection.transaction():
            payload = await _load(connection, cipher, token, lock=True)
            if payload is None:
                return {"status": "unavailable", "reason": plan.JOB_CLOSED}
            if payload is False:
                return {"status": "unavailable", "reason": "tool_failed"}
            seen = payload.get("events_read", 0)
            fresh = [event["kind"] for event in payload.get("events", [])[seen:]]
            if fresh:
                payload["events_read"] = seen + len(fresh)
                await _save(connection, cipher, token_hash(token), payload)
    elapsed = max(0, int(time.time()) - int(payload.get("created", time.time())))
    return {"status": "ok", "events": fresh, "results": _statuses(payload), "elapsed_seconds": elapsed,
            "window_remaining_seconds": max(0, plan.PLAN_WINDOW_SECONDS - elapsed),
            "check_in_due": elapsed >= plan.CHECK_IN_AFTER_SECONDS, "notes_sent": list(payload.get("notes", []))}


async def post_job_event(session_id: str, kind: str) -> int:
    """Tell every open job of a call that something happened (a responder replied, the caller sent a location).

    Only kinds from ``EVENT_KINDS`` are accepted. Returns how many jobs were told; closed jobs are skipped.
    """
    if kind not in EVENT_KINDS:
        raise ValueError("unknown event kind")
    database_url, cipher = job_store_config()
    told = 0
    async with await _connect(database_url) as connection:
        async with connection.transaction():
            cursor = await connection.execute(
                "SELECT token_hash, payload FROM emergency_jobs WHERE session_id = %s AND state = 'pending' "
                "AND expires_at > now() FOR UPDATE", (session_id,))
            for key, raw in await cursor.fetchall():
                try:
                    payload = json.loads(cipher.decrypt(bytes(raw)))
                except Exception:
                    continue
                payload.setdefault("events", []).append({"kind": kind, "at": int(time.time())})
                await _save(connection, cipher, key, payload)
                told += 1
    log_tool_event("emergency_job_event", kind=kind, jobs=told, session_ref=short_ref(session_id))
    return told


async def add_voice_note(token: str, note: str) -> dict:
    """The agent asks the application to tell the voice agent something. Each note is passed on once per job."""
    if note not in VOICE_NOTES:
        return {"status": "unavailable", "reason": "note_not_allowed"}
    database_url, cipher = job_store_config()
    async with await _connect(database_url) as connection:
        async with connection.transaction():
            payload = await _load(connection, cipher, token, lock=True)
            if payload is None:
                return {"status": "unavailable", "reason": plan.JOB_CLOSED}
            if payload is False:
                return {"status": "unavailable", "reason": "tool_failed"}
            notes = payload.setdefault("notes", [])
            if note in notes:
                return {"status": "ok", "note": note, "already_sent": True}
            notes.append(note)
            await _save(connection, cipher, token_hash(token), payload)
    return {"status": "ok", "note": note}


async def job_progress(token: str) -> dict | None:
    """The application's read-only view of a running job: finished action results and the voice notes so far.

    ``None`` once the job is closed or gone; the final outcome is then collected with ``get_job_result``.
    """
    database_url, cipher = job_store_config()
    async with await _connect(database_url) as connection:
        payload = await _load(connection, cipher, token, lock=False)
    if not payload:
        return None
    done = {name: result for name, result in payload.get("results", {}).items() if result != IN_PROGRESS}
    return {"results": done, "notes": list(payload.get("notes", []))}


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
    # A subagent may text or call the responder in any job, so withdrawing consent cancels every pending job.
    database_url, _ = job_store_config()
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        await connection.execute(
            "UPDATE emergency_jobs SET state = 'cancelled', payload = ''::bytea WHERE session_id = %s AND state = 'pending'",
            (session_id,),
        )


async def purge_expired_jobs():
    database_url, _ = job_store_config()
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, options="-c statement_timeout=5000") as connection:
        await connection.execute("DELETE FROM emergency_jobs WHERE expires_at < now() - interval '1 hour'")
