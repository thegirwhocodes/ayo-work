"""Emergency tools scoped to one call: Photon SMS (photon_sms.py) and the Twilio outbound call (start_phone_call.py)."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx

from harness.emergency_plan import BACKUP_ACTION
from harness.tool_log import log_tool_event, timed
from harness.tools.photon_sms import PhotonMessaging
from harness.tools.start_phone_call import TwilioCaller, start_phone_call
from settings import get_settings


@dataclass
class EmergencyCallContext:
    caller_number: str
    responder_consent: bool = False
    message: str = ""  # what the caller approved sharing, set when the voice agent relays an emergency request


async def send_text_message(context: EmergencyCallContext, text: str, photon: PhotonMessaging, backup: bool = False) -> dict:
    """Text the configured responder (or, with ``backup``, the backup responder) only after the caller agrees."""
    if not context.responder_consent:
        return {"status": "consent_required"}
    if not text or not text.strip():
        return {"status": "unavailable", "reason": "empty_message"}
    settings = get_settings()
    recipient = settings.emergency_backup_responder_number if backup else settings.emergency_responder_number
    if not recipient:
        return {"status": "unavailable", "reason": "backup_not_configured" if backup else "responder_not_configured"}
    return await photon.send(recipient, text.strip())


async def request_location(context: EmergencyCallContext, photon: PhotonMessaging) -> dict:
    """Ask the caller by SMS to reply with their location."""
    result = await photon.send(context.caller_number, "If it is safe to do so, please reply with your location or address. You can also tell us on the call.")
    return {**result, "voice_instruction": "Ask whether the caller wants someone to come get them. Do not promise that help has been dispatched or will arrive."}


class EmergencyTools:
    """Bind one relay request to its allowed tool and authoritative result."""

    def __init__(self, context: EmergencyCallContext, action: str, message: str = "", photon: PhotonMessaging | None = None,
                 caller: TwilioCaller | None = None):
        self.context = context
        self.action = action
        self.message = message
        self.photon = photon or PhotonMessaging()
        self.caller = caller or TwilioCaller()
        self.result = None
        self.lock = asyncio.Lock()

    async def send_text_message(self) -> str:
        """Submit the caller-approved message to the configured emergency responder."""
        return await self.run("send_text_message")

    async def request_location(self) -> str:
        """Text the caller a request for their location."""
        return await self.run("request_location")

    async def start_phone_call(self) -> str:
        """Call the emergency responder with the caller-approved message."""
        return await self.run("start_phone_call")

    async def run(self, action: str) -> str:
        import json
        async with self.lock:
            if action != self.action:
                log_tool_event("emergency_tool", tool=action, status="unavailable", reason="action_not_requested")
                return json.dumps({"status": "unavailable", "reason": "action_not_requested"})
            if self.result is None:
                with timed() as timer:
                    if action == "send_text_message":
                        self.result = await send_text_message(self.context, self.message, self.photon)
                    elif action == BACKUP_ACTION:
                        self.result = await send_text_message(self.context, self.message, self.photon, backup=True)
                    elif action == "request_location":
                        self.result = await request_location(self.context, self.photon)
                    else:
                        self.result = await start_phone_call(self.context, self.message, self.caller)
                log_tool_event("emergency_tool", tool=action, status=self.result.get("status"), reason=self.result.get("reason"),
                               photon_status=self.result.get("photon_status"), photon_error=self.result.get("photon_error"),
                               twilio_error_code=self.result.get("twilio_error_code"), message_id=self.result.get("message_id"),
                               call_sid=self.result.get("call_sid"), duration_ms=timer.ms)
            return json.dumps(self.result)
