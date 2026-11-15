"""Internal-only API: the emergency worker callback and voice-enrollment reset.

This is a separate FastAPI app served on its own port (INTERNAL_PORT, default 8001) by a second
listener started in app/main.py. That port is never published by Docker Compose and binds to
127.0.0.1 unless INTERNAL_HOST says otherwise, so these routes cannot be reached from the public
internet by construction. They are NOT mounted on the public app (port 8000), which also serves
Twilio's webhook and the media socket.

Every route also requires the shared worker secret, as a second layer.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re

from fastapi import FastAPI, Request, Response

from harness.datastore.voice import PostgresVoiceStore
from harness.emergency_jobs import execute_job
from settings import get_settings

logger = logging.getLogger("ayo.internal")

internal_app = FastAPI(title="Ayo internal", docs_url=None, redoc_url=None, openapi_url=None)


def _authorized(request: Request) -> bool:
    secret = get_settings().emergency_worker_secret
    supplied = request.headers.get("X-Emergency-Worker-Key", "")
    if len(secret) < 32:
        return False
    # compare_digest raises on non-ASCII str input, so compare bytes.
    return hmac.compare_digest(supplied.encode("utf-8", "replace"), secret.encode("utf-8"))


@internal_app.post("/internal/emergency-job")
async def emergency_job(request: Request):
    """Consume a one-use job capability from an authenticated agent worker."""
    if not _authorized(request):
        return Response(status_code=403)
    body = await request.json()
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or len(token) != 43:
        return Response(status_code=400)
    try:
        return await execute_job(token)
    except Exception as exc:
        logger.warning("Emergency job store unavailable: %s", type(exc).__name__)
        return Response(status_code=503)


@internal_app.delete("/internal/voice-enrollment")
async def forget_voice(request: Request):
    """Reset a caller's voiceprint (for example after their voice changed or a different child
    now uses the phone). The next call from that number enrolls afresh."""
    if not _authorized(request):
        return Response(status_code=403)
    body = await request.json()
    caller = body.get("caller_id") if isinstance(body, dict) else None
    if not isinstance(caller, str) or not re.fullmatch(r"\+[1-9][0-9]{6,14}", caller):
        return Response(status_code=400)
    try:
        store = PostgresVoiceStore(get_settings().database_url)
        deleted = await asyncio.to_thread(store.delete, caller)
    except Exception as exc:
        logger.warning("Voice enrollment reset failed: %s", type(exc).__name__)
        return Response(status_code=503)
    return {"status": "deleted" if deleted else "not_found"}
