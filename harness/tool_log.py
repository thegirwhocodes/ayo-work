"""Structured, content-free logging for tool calls.

Every line is ``tool_event {json}`` on the ``sabi.tool`` logger, e.g.::

    tool_event {"action": "start_phone_call", "call_sid": "CA...", "duration_ms": 412, "event": "tool_call", "status": "consent_required", "tool": "relay_to_harness"}

Only identifiers, outcomes and timings belong here. Names and values that could carry what a child said,
or a phone number, are dropped by ``REDACTED_KEYS`` even if a caller passes them by mistake, and long
strings are truncated.
"""

from __future__ import annotations

import collections
import contextvars
import hashlib
import itertools
import json
import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("sabi.tool")

# The most recent events, kept in memory for the live case dashboard (app/dashboard.py). They are the same
# content-free payloads that reach the log, plus a sequence number and a timestamp. Per process, never persisted.
_EVENTS: collections.deque = collections.deque(maxlen=5000)
_SEQUENCE = itertools.count(1)
# The call this task belongs to. Tasks the call starts inherit it, so events logged deep inside a call
# (the case manager, an emergency job) are attributed to it without every caller passing the call id.
_CALL: contextvars.ContextVar = contextvars.ContextVar("sabi_call_sid", default=None)

REDACTED_KEYS = frozenset({
    "text", "message", "content", "transcript", "caller_text", "model_text", "body", "twiml", "briefing",
    "recipient", "number", "phone", "caller_number", "handle", "to", "from", "args", "result",
})
MAX_VALUE = 160


def _clean(value):
    if isinstance(value, str):
        return value if len(value) <= MAX_VALUE else value[:MAX_VALUE] + "..."
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in list(value)[:20]]
    return _clean(str(value))


def bind_call(call_sid) -> None:
    """Attribute every event logged from this task, and tasks it starts, to this call."""
    _CALL.set(call_sid or None)


def short_ref(value) -> str:
    """A short one-way reference for an identifier, so a dashboard can group events without showing the identifier."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:8]


def recent_events(after: int = 0) -> list[dict]:
    """Buffered events with a sequence number above ``after``, oldest first."""
    return [dict(event) for event in tuple(_EVENTS) if event["seq"] > after]


def clear_events() -> None:
    _EVENTS.clear()


def log_tool_event(event: str, **fields) -> dict:
    """Emit one structured line and return the payload (handy for tests)."""
    payload = {"event": event}
    call = _CALL.get()
    if call and fields.get("call_sid") is None:
        fields["call_sid"] = call
    for key, value in fields.items():
        if value is None or key.lower() in REDACTED_KEYS:
            continue
        payload[key] = _clean(value)
    logger.info("tool_event %s", json.dumps(payload, sort_keys=True, default=str))
    _EVENTS.append({**payload, "seq": next(_SEQUENCE), "ts": time.time()})
    return payload


@contextmanager
def timed():
    """``with timed() as t: ...`` then ``t.ms`` is the elapsed whole milliseconds."""
    class Timer:
        ms = 0

    timer, start = Timer(), time.perf_counter()
    try:
        yield timer
    finally:
        timer.ms = int((time.perf_counter() - start) * 1000)
