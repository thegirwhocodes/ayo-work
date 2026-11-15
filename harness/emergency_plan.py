"""The escalation plan for one emergency job: its limits, its closed vocabularies and its deterministic floor.

The emergency agent (``harness/emergency_subagent.py``) plans the follow-up to a responder request: if the
first channel fails it tries the other one, then a backup responder, waits for the responder to acknowledge,
checks in with the child, and finally tells the voice agent to point the child at the local emergency number.

Two things here are deliberately NOT up to the model:

* the vocabularies. The application can tell a running job only what ``EVENT_KINDS`` names, and the agent can
  ask the voice agent only for what ``VOICE_NOTES`` names. No caller or responder words reach the model, and
  the model never writes what the voice agent is told: the application owns that wording.
* the floor. ``run_floor`` is the ladder as plain code. It runs after the agent whether or not the agent
  finished, so a model outage or a model that stops early never leaves the child without the next channel.
  Every step is idempotent because the job store runs each action at most once.

This module imports nothing outside the standard library, because it also runs inside the Agent Server image.
"""

from __future__ import annotations

from typing import Awaitable, Callable

EVENT_KINDS = ("responder_acknowledged", "location_received")
VOICE_NOTES = ("check_in", "call_emergency_number", "responder_unreachable")

RESPONDER_CHANNELS = ("send_text_message", "start_phone_call")
BACKUP_ACTION = "text_backup_responder"
LADDER = RESPONDER_CHANNELS + (BACKUP_ACTION,)

# How long an escalation may run, and when the agent should check in if nobody has acknowledged.
PLAN_WINDOW_SECONDS = 600
CHECK_IN_AFTER_SECONDS = 180

JOB_CLOSED = "invalid_or_expired_job"


def next_rung(requested: str, statuses: dict[str, str]) -> str | None:
    """The next responder action the ladder calls for, or None when it is done.

    The ladder exists only for a request to reach the responder (not for a location request). It ends as soon
    as any responder action was submitted, it never pushes past a refusal for missing consent, and it never
    repeats an action that was already attempted (``unknown`` counts: it may have been sent).
    Order: the requested channel, the other channel, then the backup responder.
    """
    if requested not in RESPONDER_CHANNELS:
        return None
    if any(statuses.get(action) == "submitted" for action in LADDER) or "consent_required" in statuses.values():
        return None
    other = [channel for channel in RESPONDER_CHANNELS if channel != requested]
    return next((action for action in (requested, *other, BACKUP_ACTION) if action not in statuses), None)


async def run_floor(requested: str, statuses: dict[str, str],
                    act: Callable[[str], Awaitable[dict]], note: Callable[[str], Awaitable[dict]]) -> dict[str, str]:
    """Walk the rest of the ladder in plain code and return the final per-action statuses.

    ``act(action)`` runs one action through the job store and returns its result; ``note(name)`` asks the
    application to tell the voice agent something from ``VOICE_NOTES``. If every rung was tried and none was
    submitted, the voice agent is told the responder could not be reached and to point the child at the
    local emergency number. A closed job (call ended, consent withdrawn) stops everything silently.
    """
    statuses = dict(statuses)
    closed = False
    while not closed and (rung := next_rung(requested, statuses)):
        result = await act(rung)
        closed = result.get("reason") == JOB_CLOSED
        statuses[rung] = result.get("status", "unavailable")
    contacted = [action for action in LADDER if action in statuses]
    if (not closed and requested in RESPONDER_CHANNELS and contacted
            and not any(statuses[action] == "submitted" for action in contacted)
            and "consent_required" not in statuses.values()):
        await note("responder_unreachable")
        await note("call_emergency_number")
    return statuses
