"""The emergency subagent's three tools: send_text_message, start_phone_call, request_location.

The subagent runs in the isolated agent server, which holds no Photon or Twilio secrets. Each tool therefore
asks the application, through the authenticated job callback, to perform that one action for the current
job. The application enforces consent, runs each action at most once, and sends only the message the caller
approved, so the model chooses which action to take but can never write or change what is sent.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from harness.emergency_plan import BACKUP_ACTION, VOICE_NOTES
from harness.tool_log import log_tool_event, timed


class NoArguments(BaseModel):
    """These tools take no arguments: the approved message and the recipients are fixed by the job."""


SEND_TEXT_MESSAGE = (
    "Send the caller-approved message to the emergency responder by SMS. Use it when the voice agent requested "
    "send_text_message, or as a fallback when start_phone_call failed. Only works if the caller consented to the "
    "responder being contacted (the briefing says whether they did); without consent it refuses and sends nothing. "
    "Takes no arguments: the message was approved by the caller and cannot be changed. A result of 'submitted' "
    "means the SMS provider accepted it, not that it was delivered or read."
)
START_PHONE_CALL = (
    "Place an automated phone call to the emergency responder that reads the caller-approved message aloud. Use it "
    "when the voice agent requested start_phone_call, or as a fallback when send_text_message failed. Only works if "
    "the caller consented to the responder being contacted; without consent it refuses and calls no one. Takes no "
    "arguments. A result of 'submitted' means the call was requested, not that anyone answered."
)
REQUEST_LOCATION = (
    "Text the CALLER (never the responder) asking them to reply with their location, only if it is safe. Use it when "
    "the voice agent requested request_location. It does not need responder consent and must never replace "
    "contacting the responder. Takes no arguments. A result of 'submitted' means the text was accepted, not that the "
    "caller has replied."
)
DESCRIPTIONS = {"send_text_message": SEND_TEXT_MESSAGE, "start_phone_call": START_PHONE_CALL, "request_location": REQUEST_LOCATION}


def build_emergency_tools(run_action: Callable[[str], Awaitable[dict]]) -> list[StructuredTool]:
    """Bind the three tools to a job. ``run_action(name)`` performs one action and returns its result."""

    def make(name: str) -> StructuredTool:
        async def call() -> str:
            with timed() as timer:
                result = await run_action(name)
            log_tool_event("subagent_tool_call", tool=name, status=result.get("status"), reason=result.get("reason"),
                           duration_ms=timer.ms)
            return json.dumps(result)

        return StructuredTool.from_function(coroutine=call, name=name, description=DESCRIPTIONS[name], args_schema=NoArguments)

    return [make(name) for name in ("send_text_message", "start_phone_call", "request_location")]


TEXT_BACKUP_RESPONDER = (
    "Send the caller-approved message to the BACKUP emergency responder by SMS. Use it only after the first responder "
    "could not be reached: neither send_text_message nor start_phone_call came back 'submitted'. Needs the caller's "
    "consent like the others and refuses without it; if no backup responder is configured it says so and sends nothing. "
    "Takes no arguments. 'submitted' means the SMS provider accepted it, not that it was delivered or read."
)
WAIT_FOR_EVENTS = (
    "Wait up to the given number of seconds (1 to 60) for news about this emergency, then report it. It returns early "
    "as soon as something arrives. Events are only 'responder_acknowledged' (the responder replied that they have "
    "seen the message) and 'location_received' (the caller sent their location). It also reports the status of every "
    "action so far, seconds elapsed, seconds left in the escalation window, whether a check-in is due, and which voice "
    "notes were already sent. If 'job_open' is false the call ended or the caller withdrew consent: stop at once."
)
NOTIFY_VOICE_AGENT = (
    "Ask the application to tell the voice agent something, once per note. 'check_in': minutes have passed with no "
    "acknowledgement, so the voice agent should gently ask the caller whether they are still safe. "
    "'call_emergency_number': the voice agent should encourage the caller to call the local emergency number now, if "
    "it is safe. 'responder_unreachable': no responder channel was accepted. You choose only which note; the "
    "application decides the exact words, so nothing you write reaches the caller."
)
EVENT_POLL_SECONDS = 2.0


class WaitArguments(BaseModel):
    seconds: int = Field(default=30, ge=1, le=60, description="How long to wait for news, in seconds (1 to 60).")


class NoteArguments(BaseModel):
    note: Literal[VOICE_NOTES] = Field(description="Which note the voice agent should receive.")  # type: ignore[valid-type]


def build_plan_tools(run_action: Callable[[str], Awaitable[dict]], poll_events: Callable[[], Awaitable[dict]],
                     send_note: Callable[[str], Awaitable[dict]]) -> list[StructuredTool]:
    """Everything the escalation-planning agent may use.

    The three responder tools from ``build_emergency_tools``, plus the backup responder text, a bounded wait for
    events, and a note to the voice agent chosen from a closed list. ``poll_events()`` returns the job's
    state (see ``harness.emergency_jobs.poll_job_events``); ``send_note(name)`` stores one note for the app.
    """
    async def text_backup() -> str:
        with timed() as timer:
            result = await run_action(BACKUP_ACTION)
        log_tool_event("subagent_tool_call", tool=BACKUP_ACTION, status=result.get("status"), reason=result.get("reason"),
                       duration_ms=timer.ms)
        return json.dumps(result)

    async def wait(seconds: int = 30) -> str:
        loop = asyncio.get_running_loop()
        deadline, heard = loop.time() + seconds, []
        with timed() as timer:
            while True:
                state = await poll_events()
                if state.get("status") != "ok":
                    report = {"job_open": False, "reason": state.get("reason")}
                    break
                heard += state["events"]
                if heard or loop.time() >= deadline or not state["window_remaining_seconds"]:
                    report = {"job_open": True, **{**state, "events": heard}}
                    report.pop("status", None)
                    break
                await asyncio.sleep(min(EVENT_POLL_SECONDS, max(0.0, deadline - loop.time())))
        log_tool_event("subagent_tool_call", tool="wait_for_events", status="open" if report["job_open"] else "closed",
                       events=heard, duration_ms=timer.ms)
        return json.dumps(report)

    async def notify(note: str) -> str:
        result = await send_note(note)
        log_tool_event("subagent_tool_call", tool="notify_voice_agent", note=note, status=result.get("status"),
                       reason=result.get("reason"))
        return json.dumps(result)

    return build_emergency_tools(run_action) + [
        StructuredTool.from_function(coroutine=text_backup, name=BACKUP_ACTION, description=TEXT_BACKUP_RESPONDER, args_schema=NoArguments),
        StructuredTool.from_function(coroutine=wait, name="wait_for_events", description=WAIT_FOR_EVENTS, args_schema=WaitArguments),
        StructuredTool.from_function(coroutine=notify, name="notify_voice_agent", description=NOTIFY_VOICE_AGENT, args_schema=NoteArguments),
    ]


def build_call_tools(context) -> list[StructuredTool]:
    """The same three tools for the main harness agent, bound to this call and run in the application.

    Same descriptions and guards as the subagent's: consent is enforced by the tools themselves, the message
    is the one the caller approved (``context.message``), and each action runs at most once per call.
    """
    from harness.tools.emergency import EmergencyTools

    done: dict[str, dict] = {}

    async def run_action(name: str) -> dict:
        if name not in done:
            tools = EmergencyTools(context, name, context.message)
            await tools.run(name)
            done[name] = tools.result or {"status": "unavailable", "reason": "tool_not_called"}
        return done[name]

    return build_emergency_tools(run_action)
