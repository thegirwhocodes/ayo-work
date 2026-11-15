"""Photon 2.0 SMS sender.

Flow, per the Photon API (https://api.photon.codes/openapi.json):

1. Resolve the sender: the project-owned SMS Agent User that fronts your purchased line.
   Set PHOTON_RESOURCE_ID (the ``pho_res_...`` ID of the purchased number) and the sender is looked
   up automatically (resource -> phone number -> ``POST /users``). PHOTON_SENDER_ID (``pho_usr_...``)
   skips the lookup if you already know it.
2. ``POST /users`` for the recipient, then ``POST /spaces`` for the direct SMS Space.
3. ``POST /messages``. Photon answers 202 once the message is persisted for asynchronous delivery;
   that is acceptance, not delivery.

``POST /spaces`` and ``POST /messages`` both require an ``Idempotency-Key`` header. The message is never
retried: if the outcome is uncertain the result is ``unknown``, so a text is never sent twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid

import httpx

from settings import get_settings

E164 = re.compile(r"\+[1-9][0-9]{6,14}")
CODE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
API = "https://api.photon.codes/v1/projects"

# (project, resource) -> sender User ID, resolved once per process.
_SENDERS: dict[tuple[str, str], str] = {}
_RESOLVE_LOCK = asyncio.Lock()


def _problem(response: httpx.Response) -> str | None:
    """A short machine code from Photon's error body (e.g. SENDER_NOT_SENDABLE); never free text."""
    try:
        body = response.json()
    except ValueError:
        return None
    for candidate in (body.get("code"), (body.get("error") or {}).get("code") if isinstance(body.get("error"), dict) else None):
        if isinstance(candidate, str) and CODE.fullmatch(candidate):
            return candidate
    return None


def _unavailable(reason: str, response: httpx.Response | None = None) -> dict:
    result = {"status": "unavailable", "reason": reason}
    if response is not None:
        result["photon_status"] = response.status_code
        if code := _problem(response):
            result["photon_error"] = code
    return result


class PhotonMessaging:
    """Direct SMS through a project-owned Photon line."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    async def send(self, recipient: str, text: str) -> dict:
        settings = get_settings()
        project, api_key = settings.photon_project_id, settings.photon_api_key
        sender, resource = settings.photon_sender_id, settings.photon_resource_id
        if not (project and api_key and (sender or resource)):
            return {"status": "unavailable", "reason": "photon_not_configured"}
        if not E164.fullmatch(recipient or ""):
            return {"status": "unavailable", "reason": "invalid_recipient"}
        headers = {"Authorization": f"Bearer {api_key}"}
        base = f"{API}/{project}"
        try:
            if self.client is not None:
                return await self._send(self.client, base, headers, project, sender, resource, recipient, text)
            async with httpx.AsyncClient(timeout=15.0) as client:
                return await self._send(client, base, headers, project, sender, resource, recipient, text)
        except (httpx.HTTPError, KeyError, ValueError):
            return {"status": "unavailable", "reason": "photon_request_failed"}

    async def _send(self, client, base, headers, project, sender, resource, recipient, text) -> dict:
        sender_id = sender or await self._resolve_sender(client, base, headers, project, resource)
        if isinstance(sender_id, dict):  # a failure result from the lookup
            return sender_id

        user = await client.post(f"{base}/users", headers=headers, json={"platform": "sms", "handle": recipient})
        if user.status_code not in (200, 201):
            return _unavailable("recipient_rejected", user)
        pair = hashlib.sha256(f"{sender_id}|{user.json()['id']}".encode()).hexdigest()[:40]
        space = await client.post(f"{base}/spaces", json={"platform": "sms", "participantIds": [sender_id, user.json()["id"]]},
                                  headers={**headers, "Idempotency-Key": f"sabi-space-{pair}"})
        if space.status_code not in (200, 201):
            return _unavailable("space_rejected", space)

        try:
            response = await client.post(f"{base}/messages", headers={**headers, "Idempotency-Key": f"sabi-msg-{uuid.uuid4().hex}"},
                                         json={"spaceId": space.json()["id"], "senderId": sender_id,
                                               "content": {"type": "text", "format": "plain", "text": text}})
        except httpx.TransportError:
            # Photon may have accepted the message before the connection failed.
            return {"status": "unknown", "reason": "message_submission_uncertain"}
        if response.status_code >= 500:
            return {"status": "unknown", "reason": "message_submission_uncertain"}
        if response.status_code == 409:
            return _unavailable("sender_not_sendable", response)  # e.g. line still activating or no approved 10DLC campaign
        if response.status_code != 202:
            return _unavailable("message_rejected", response)
        message = response.json()
        return {"status": "submitted", "message_id": message["id"]}

    async def _resolve_sender(self, client, base, headers, project, resource):
        key = (project, resource)
        if key in _SENDERS:
            return _SENDERS[key]
        async with _RESOLVE_LOCK:
            if key in _SENDERS:
                return _SENDERS[key]
            line = await client.get(f"{base}/platforms/resources/{resource}", headers=headers)
            if line.status_code != 200:
                return _unavailable("sender_line_not_found", line)
            body = line.json()
            number = (body.get("detail") or {}).get("phoneNumber")
            if body.get("state") != "active" or "sms" not in (body.get("abilities") or []) or not number:
                return {"status": "unavailable", "reason": "sender_line_not_ready"}
            user = await client.post(f"{base}/users", headers=headers, json={"platform": "sms", "handle": number})
            if user.status_code not in (200, 201):
                return _unavailable("sender_rejected", user)
            data = user.json()
            owner = (data.get("details") or {}).get("resourceId")
            if owner and owner != resource:  # never send as a line this project does not own
                return {"status": "unavailable", "reason": "sender_not_owned"}
            _SENDERS[key] = data["id"]
            return data["id"]
