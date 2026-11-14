"""Photon-backed emergency tools scoped to one call."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx

from settings import get_settings


@dataclass
class EmergencyCallContext:
    caller_number: str
    responder_consent: bool = False


class PhotonMessaging:
    """Photon 2.0 project API for direct SMS messages."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    async def send(self, recipient: str, text: str) -> dict:
        settings = get_settings()
        project = settings.photon_project_id
        api_key = settings.photon_api_key
        sender = settings.photon_sender_id
        if not all((project, api_key, sender)):
            return {"status": "unavailable", "reason": "photon_not_configured"}
        if not re.fullmatch(r"\+[1-9][0-9]{6,14}", recipient or ""):
            return {"status": "unavailable", "reason": "invalid_recipient"}
        base = f"https://api.photon.codes/v1/projects/{project}"
        headers = {"Authorization": f"Bearer {api_key}"}

        async def submit(client):
            user = await client.post(f"{base}/users", headers=headers, json={"platform": "sms", "handle": recipient})
            user.raise_for_status()
            space = await client.post(f"{base}/spaces", headers=headers, json={"platform": "sms", "participantIds": [sender, user.json()["id"]]})
            space.raise_for_status()
            try:
                response = await client.post(f"{base}/messages", headers=headers, json={
                    "spaceId": space.json()["id"], "senderId": sender,
                    "content": {"type": "text", "format": "plain", "text": text},
                })
            except httpx.TransportError:
                # The provider may have accepted the message before the connection failed.
                return {"status": "unknown", "reason": "message_submission_uncertain"}
            if response.status_code >= 500:
                return {"status": "unknown", "reason": "message_submission_uncertain"}
            response.raise_for_status()
            if response.status_code != 202:
                return {"status": "unavailable", "reason": "unexpected_photon_response"}
            return {"status": "submitted", "message_id": response.json()["id"]}

        try:
            if self.client is not None:
                return await submit(self.client)
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await submit(client)
        except (httpx.HTTPError, KeyError, ValueError):
            return {"status": "unavailable", "reason": "photon_request_failed"}


async def send_text_message(context: EmergencyCallContext, text: str, photon: PhotonMessaging) -> dict:
    """Text the configured responder only after the caller agrees."""
    if not context.responder_consent:
        return {"status": "consent_required"}
    if not text or not text.strip():
        return {"status": "unavailable", "reason": "empty_message"}
    recipient = get_settings().emergency_responder_number
    if not recipient:
        return {"status": "unavailable", "reason": "responder_not_configured"}
    return await photon.send(recipient, text.strip())


async def request_location(context: EmergencyCallContext, photon: PhotonMessaging) -> dict:
    """Ask the caller by SMS to reply with their location."""
    result = await photon.send(context.caller_number, "If it is safe to do so, please reply with your location or address. You can also tell us on the call.")
    return {**result, "voice_instruction": "Ask whether the caller wants someone to come get them. Do not promise that help has been dispatched or will arrive."}


async def start_phone_call() -> dict:
    """Placeholder for the future outbound voice architecture."""
    return {"status": "not_implemented"}


class EmergencyTools:
    """Bind one relay request to its allowed tool and authoritative result."""

    def __init__(self, context: EmergencyCallContext, action: str, message: str = "", photon: PhotonMessaging | None = None):
        self.context = context
        self.action = action
        self.message = message
        self.photon = photon or PhotonMessaging()
        self.result = None
        self.lock = asyncio.Lock()

    async def send_text_message(self) -> str:
        """Submit the caller-approved message to the configured emergency responder."""
        return await self.run("send_text_message")

    async def request_location(self) -> str:
        """Text the caller a request for their location."""
        return await self.run("request_location")

    async def start_phone_call(self) -> str:
        """Request an outbound call, which is currently unavailable."""
        return await self.run("start_phone_call")

    async def run(self, action: str) -> str:
        import json
        async with self.lock:
            if action != self.action:
                return json.dumps({"status": "unavailable", "reason": "action_not_requested"})
            if self.result is None:
                if action == "send_text_message":
                    self.result = await send_text_message(self.context, self.message, self.photon)
                elif action == "request_location":
                    self.result = await request_location(self.context, self.photon)
                else:
                    self.result = await start_phone_call()
            return json.dumps(self.result)
