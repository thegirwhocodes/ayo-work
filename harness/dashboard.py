"""Turn the content-free tool events into a live view of each call's case.

Input is the event buffer in ``harness.tool_log``. Output is what ``app/dashboard.py`` serves: for each call its
risk timeline, case plan, consent state, emergency actions and whether the responder has acknowledged.

This module copies a fixed set of fields out of each event; it never passes an event through. Transcripts, names
and phone numbers are not in the events to begin with (the logger drops them), and a field that is not named
here cannot reach the dashboard even if someone later logs it by mistake. Calls are shown by a short hash of the
call SID, never the SID itself.
"""

from __future__ import annotations

import re
import time
from typing import Iterable, Optional

from harness.tool_log import short_ref

MAX_CALLS = 20
MAX_TIMELINE = 40
MAX_LOG = 60
LIVE_WITHIN_SECONDS = 3600

SPECIALISTS = ("triage", "researcher", "advisor")
LADDER = ("send_text_message", "start_phone_call", "text_backup_responder", "request_location")
RESPONDER_STATES = ("none", "check_in_sent", "emergency_number_advised", "unreachable", "acknowledged")
NOTE_STATES = {"check_in": "check_in_sent", "call_emergency_number": "emergency_number_advised",
               "responder_unreachable": "unreachable"}

# One short, fixed phrase per event kind for the activity log. Event fields are never interpolated into free text.
LOG_LABELS = {
    "call_started": "Call connected",
    "call_ended": "Call ended",
    "case_manager_cycle": "Case manager analysed the call",
    "responder_consent_changed": "Responder consent changed",
    "emergency_job_created": "Emergency request queued",
    "emergency_action": "Emergency action result",
    "emergency_job_finished": "Emergency job finished",
    "emergency_job_error": "Emergency job failed",
    "emergency_job_event": "Update from outside the call",
    "emergency_voice_note": "Voice agent told to follow up",
}


def _tag(value, limit: int = 40) -> Optional[str]:
    """A status-like value reduced to lowercase letters, digits and _ : - so only a short token can pass."""
    if value is None:
        return None
    return re.sub(r"[^a-z0-9_:\-]", "", str(value).lower())[:limit] or None


def _int(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _new_call(ref: str, ts: float) -> dict:
    return {"ref": ref, "started": ts, "last_seen": ts, "ended": None, "risk": None, "risk_timeline": [],
            "analysis": {"cycles": 0, "ran": [], "rubric": None, "checks": 0},
            "consent": {"state": "not_asked", "changes": 0},
            "responder": {"state": "none", "location_received": False, "ts": None},
            "escalation": {name: None for name in LADDER}, "requests": [], "jobs": {}, "guidance": {"turns_behind": None},
            "log": []}


def _label(call: dict, ts: float, event: str, detail: Optional[str] = None):
    call["log"].append({"ts": ts, "event": event, "label": LOG_LABELS.get(event, event), "detail": detail})
    del call["log"][:-MAX_LOG]


def _set_responder(call: dict, state: str, ts: float):
    # An acknowledgement ends the escalation; nothing after it changes the state.
    if call["responder"]["state"] != "acknowledged":
        call["responder"].update(state=state, ts=ts)


def build_snapshot(events: Iterable[dict], now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    calls: dict[str, dict] = {}
    by_session: dict[str, str] = {}
    pending_checks: dict[str, int] = {}
    last_seq = 0

    for event in events:
        last_seq = max(last_seq, int(event.get("seq", 0)))
        sid = event.get("call_sid")
        name, ts = event.get("event"), float(event.get("ts", now))
        if name == "emergency_job_event":  # arrives from outside the call, by session reference
            ref = by_session.get(event.get("session_ref"))
        else:
            ref = short_ref(sid) if sid else None
        if ref is None:
            continue
        if name == "call_started":
            calls[ref] = _new_call(ref, ts)
            if event.get("session_ref"):
                by_session[event["session_ref"]] = ref
        call = calls.get(ref)
        if call is None:
            continue
        call["last_seen"] = ts

        if name == "call_started":
            _label(call, ts, name)
        elif name == "call_ended":
            call["ended"] = ts
            _label(call, ts, name)
        elif name == "case_manager_rubric":
            pending_checks[ref] = pending_checks.get(ref, 0) + 1
        elif name == "case_manager_cycle":
            ok = bool(event.get("advice"))
            ran = [step for step in SPECIALISTS if step in (event.get("ran") or [])]
            risk = event.get("risk") if ok and event.get("risk") in ("routine", "elevated", "urgent") else None
            checks = pending_checks.pop(ref, 0)
            call["risk_timeline"].append({"ts": ts, "risk": risk, "ran": ran, "rubric": _tag(event.get("rubric")),
                                          "checks": checks, "duration_ms": _int(event.get("duration_ms"))})
            del call["risk_timeline"][:-MAX_TIMELINE]
            if risk:
                call["risk"] = risk
            call["analysis"] = {"cycles": call["analysis"]["cycles"] + 1, "ran": ran, "rubric": _tag(event.get("rubric")),
                                "checks": checks}
            _label(call, ts, name, f"risk {risk or 'unknown'}")
        elif name == "responder_consent_changed":
            call["consent"] = {"state": "granted" if event.get("granted") else "withdrawn",
                               "changes": call["consent"]["changes"] + 1}
            _label(call, ts, name, "granted" if event.get("granted") else "withdrawn")
        elif name == "tool_call":
            if event.get("tool") == "get_support_guidance":
                call["guidance"] = {"turns_behind": _int(event.get("turns_behind"))}
            elif event.get("tool") == "relay_to_harness" and event.get("action") in LADDER:
                call["requests"].append({"ts": ts, "action": event["action"], "status": _tag(event.get("status")),
                                         "reason": _tag(event.get("reason"))})
                del call["requests"][:-10]
        elif name == "emergency_job_created":
            call["jobs"][_tag(event.get("job_ref"), 16)] = {"action": _tag(event.get("action")), "status": "queued"}
            _label(call, ts, name, _tag(event.get("action")))
        elif name == "emergency_action":
            action = event.get("action")
            if action in LADDER:
                call["escalation"][action] = _tag(event.get("status"))
            _label(call, ts, name, f"{action}: {_tag(event.get('status'))}")
        elif name == "emergency_job_finished":
            job = call["jobs"].setdefault(_tag(event.get("job_ref"), 16), {"action": None})
            job.update(status=_tag(event.get("status")), reason=_tag(event.get("reason")), duration_ms=_int(event.get("duration_ms")))
            for pair in event.get("action_statuses") or []:
                action, _, status = str(pair).partition(":")
                if action in LADDER:
                    call["escalation"][action] = _tag(status)
            _label(call, ts, name, _tag(event.get("status")))
        elif name == "emergency_job_error":
            _label(call, ts, name, _tag(event.get("error")))
        elif name == "emergency_job_event":
            if event.get("kind") == "responder_acknowledged":
                _set_responder(call, "acknowledged", ts)
            elif event.get("kind") == "location_received":
                call["responder"]["location_received"] = True
            _label(call, ts, name, _tag(event.get("kind")))
        elif name == "emergency_voice_note" and event.get("note") in NOTE_STATES:
            _set_responder(call, NOTE_STATES[event["note"]], ts)
            _label(call, ts, name, event["note"])

    ordered = sorted(calls.values(), key=lambda call: call["started"], reverse=True)[:MAX_CALLS]
    for call in ordered:
        call["live"] = call["ended"] is None and now - call["last_seen"] < LIVE_WITHIN_SECONDS
        call["jobs"] = [{"ref": ref, **job} for ref, job in call["jobs"].items()]
        call["log"] = list(reversed(call["log"]))
    return {
        "server_time": now, "last_seq": last_seq, "calls": ordered,
        "summary": {"live": sum(call["live"] for call in ordered),
                    "urgent": sum(1 for call in ordered if call["live"] and call["risk"] == "urgent"),
                    "submitted": sum(1 for call in ordered for status in call["escalation"].values() if status == "submitted"),
                    "acknowledged": sum(1 for call in ordered if call["responder"]["state"] == "acknowledged")},
    }
