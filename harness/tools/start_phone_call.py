"""start_phone_call: place an outbound call to the configured emergency responder.

The call is made through Twilio's REST API with inline TwiML, so no public callback
URL is needed. The responder hears an automated briefing built from what the caller
agreed to share. A result of "submitted" means Twilio accepted the call request; it
does not mean anyone answered.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

import httpx

from settings import get_settings

if TYPE_CHECKING:
    from harness.tools.emergency import EmergencyCallContext

E164 = re.compile(r"\+[1-9][0-9]{6,14}")
MAX_SPOKEN_MESSAGE = 600
VOICE_INSTRUCTION = (
    "A call request was submitted to the emergency responder. Do not say the responder answered, is on the way, "
    "or that help is coming. Keep supporting the caller and encourage them to call the local emergency number if it is safe."
)


def build_briefing_twiml(caller_number: str, message: str) -> str:
    """Spoken briefing for the responder, read twice, with every value XML-escaped."""
    spoken = " ".join(message.split())[:MAX_SPOKEN_MESSAGE]
    caller = (f'The caller\'s phone number is <say-as interpret-as="telephone">{escape(caller_number)}</say-as>. '
              if E164.fullmatch(caller_number or "") else "")
    briefing = ("This is an automated emergency alert from Sabi Safe, a support line for children. "
                f"A caller has asked for help and agreed to share this message. {escape(spoken)} {caller}")
    return ('<?xml version="1.0" encoding="UTF-8"?><Response><Pause length="1"/>'
            f"<Say>{briefing}</Say><Pause length=\"2\"/><Say>I will repeat. {escape(spoken)} {caller}</Say></Response>")


class TwilioCaller:
    """Outbound call submission via the Twilio Calls API."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    async def call(self, recipient: str, twiml: str) -> dict:
        settings = get_settings()
        sid, token, sender = settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_phone_number
        if not all((sid, token, sender)):
            return {"status": "unavailable", "reason": "twilio_not_configured"}
        if not E164.fullmatch(recipient or ""):
            return {"status": "unavailable", "reason": "invalid_recipient"}
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"

        async def submit(client: httpx.AsyncClient) -> dict:
            try:
                response = await client.post(url, auth=(sid, token),
                                             data={"To": recipient, "From": sender, "Twiml": twiml})
            except httpx.TransportError:
                # Twilio may have accepted the request before the connection failed.
                return {"status": "unknown", "reason": "call_submission_uncertain"}
            if response.status_code >= 500:
                return {"status": "unknown", "reason": "call_submission_uncertain"}
            if response.status_code != 201:
                # Twilio error codes (e.g. 21219 for an unverified number on a trial account) are not sensitive.
                try:
                    code = response.json().get("code")
                except ValueError:
                    code = None
                return {"status": "unavailable", "reason": "twilio_rejected", "twilio_error_code": code}
            try:
                return {"status": "submitted", "call_sid": response.json()["sid"]}
            except (KeyError, ValueError):
                return {"status": "unavailable", "reason": "unexpected_twilio_response"}

        try:
            if self.client is not None:
                return await submit(self.client)
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await submit(client)
        except httpx.HTTPError:
            return {"status": "unavailable", "reason": "twilio_request_failed"}


async def start_phone_call(context: "EmergencyCallContext", message: str, caller: TwilioCaller) -> dict:
    """Call the configured responder, only after the caller agrees to share the message."""
    if not context.responder_consent:
        return {"status": "consent_required"}
    if not message or not message.strip():
        return {"status": "unavailable", "reason": "empty_message"}
    recipient = get_settings().emergency_responder_number
    if not recipient:
        return {"status": "unavailable", "reason": "responder_not_configured"}
    result = await caller.call(recipient, build_briefing_twiml(context.caller_number, message))
    if result.get("status") == "submitted":
        result["voice_instruction"] = VOICE_INSTRUCTION
    return result
