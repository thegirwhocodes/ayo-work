import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.dashboard as dashboard_routes
from app.voice.twilio_stream import TwilioMediaBridge
from harness.dashboard import build_snapshot
from harness.tool_log import bind_call, log_tool_event, recent_events, short_ref

TOKEN = "t" * 32


def simulate_call(end=False):
    bind_call("CAdemo")
    log_tool_event("call_started", session_ref="sess1111")
    log_tool_event("case_manager_rubric", iteration=1)
    log_tool_event("case_manager_rubric", iteration=2)
    log_tool_event("case_manager_cycle", ran=["advisor", "researcher", "triage"], advice=True, rubric="revised",
                   risk="elevated", duration_ms=900)
    log_tool_event("case_manager_cycle", ran=["advisor"], advice=True, rubric="satisfied", risk="urgent", duration_ms=500)
    log_tool_event("tool_call", tool="get_support_guidance", status="ready", turns_behind=1)
    log_tool_event("responder_consent_changed", granted=True)
    log_tool_event("tool_call", tool="relay_to_harness", action="send_text_message", status="pending", responder_consent=True)
    log_tool_event("emergency_job_created", job_ref="abc123", action="send_text_message")
    log_tool_event("emergency_action", action="send_text_message", status="unavailable")
    log_tool_event("emergency_action", action="start_phone_call", status="submitted")
    log_tool_event("emergency_voice_note", note="check_in")
    if end:
        log_tool_event("call_ended")
    bind_call(None)


def snapshot(**kwargs):
    events = recent_events()
    return build_snapshot(events, now=events[-1]["ts"] + 1, **kwargs)


def test_events_are_buffered_with_sequence_numbers_and_the_call_they_belong_to():
    bind_call("CAone")
    log_tool_event("tool_call", tool="x")
    bind_call(None)
    log_tool_event("tool_call", tool="y")
    first, second = recent_events()
    assert first["call_sid"] == "CAone" and "call_sid" not in second
    assert second["seq"] == first["seq"] + 1 and recent_events(after=first["seq"]) == [second]


def test_tasks_a_call_starts_inherit_its_attribution():
    async def go():
        bind_call("CAtask")
        await asyncio.create_task(asyncio.sleep(0, result=log_tool_event("case_manager_cycle", advice=True)))
    asyncio.run(go())
    assert recent_events()[-1]["call_sid"] == "CAtask"


def test_bridge_start_binds_the_call_and_logs_it_with_a_session_reference():
    bridge = TwilioMediaBridge(None, "prompt")
    bridge.handle_start({"callSid": "CAx1", "streamSid": "MZ1"})
    started = recent_events()[0]
    assert started["event"] == "call_started" and started["call_sid"] == "CAx1"
    assert started["session_ref"] == short_ref(bridge.harness_config["configurable"]["thread_id"])


def test_snapshot_shows_risk_plan_consent_actions_and_acknowledgement():
    simulate_call()
    log_tool_event("emergency_job_event", kind="responder_acknowledged", jobs=1, session_ref="sess1111")
    snap = snapshot()
    call = snap["calls"][0]
    assert call["ref"] == short_ref("CAdemo") and call["live"] is True
    assert [c["risk"] for c in call["risk_timeline"]] == ["elevated", "urgent"] and call["risk"] == "urgent"
    assert call["risk_timeline"][0]["checks"] == 2 and call["risk_timeline"][1]["checks"] == 0
    assert call["analysis"]["ran"] == ["advisor"] and call["analysis"]["cycles"] == 2 and call["analysis"]["rubric"] == "satisfied"
    assert call["consent"]["state"] == "granted" and call["guidance"]["turns_behind"] == 1
    assert call["escalation"]["send_text_message"] == "unavailable" and call["escalation"]["start_phone_call"] == "submitted"
    assert call["escalation"]["text_backup_responder"] is None
    assert call["requests"][0]["action"] == "send_text_message" and call["jobs"][0]["ref"] == "abc123"
    assert call["responder"]["state"] == "acknowledged"
    assert snap["summary"] == {"live": 1, "urgent": 1, "submitted": 1, "acknowledged": 1}
    assert call["log"][0]["label"] == "Update from outside the call"  # newest first


def test_without_an_acknowledgement_the_voice_notes_show_how_far_the_escalation_went():
    simulate_call()
    assert snapshot()["calls"][0]["responder"]["state"] == "check_in_sent"
    bind_call("CAdemo")
    log_tool_event("emergency_voice_note", note="call_emergency_number")
    bind_call(None)
    assert snapshot()["calls"][0]["responder"]["state"] == "emergency_number_advised"
    log_tool_event("emergency_job_event", kind="responder_acknowledged", jobs=1, session_ref="sess1111")
    bind_call("CAdemo")
    log_tool_event("emergency_voice_note", note="responder_unreachable")  # late: must not undo the acknowledgement
    bind_call(None)
    assert snapshot()["calls"][0]["responder"]["state"] == "acknowledged"


def test_an_ended_call_is_not_live_and_a_location_reply_is_noted():
    simulate_call(end=True)
    log_tool_event("emergency_job_event", kind="location_received", jobs=1, session_ref="sess1111")
    call = snapshot()["calls"][0]
    assert call["live"] is False and call["ended"] is not None and call["responder"]["location_received"] is True
    assert snapshot()["summary"]["live"] == 0


def test_events_for_an_unknown_call_or_session_are_ignored():
    bind_call("CAghost")
    log_tool_event("case_manager_cycle", advice=True, risk="urgent")
    bind_call(None)
    log_tool_event("emergency_job_event", kind="responder_acknowledged", jobs=1, session_ref="nobody")
    assert snapshot()["calls"] == []


def test_snapshot_never_carries_content_numbers_or_the_call_sid():
    secret_call = "CAsecret0123456789"
    log_tool_event("call_started", call_sid=secret_call, session_ref="s1", message="my uncle hit me", transcript="help",
                   caller_number="+2348012345678", note="at 5 Main Street", recipient="+2348099999999")
    log_tool_event("case_manager_cycle", call_sid=secret_call, advice=True, risk="urgent", ran=["advisor", "not-a-specialist"],
                   rubric="satisfied", stance="Tell her it is her fault", extra_field="leak")
    log_tool_event("tool_call", call_sid=secret_call, tool="relay_to_harness", action="send_text_message",
                   status="Not OK!! at 5 Main Street", reason="x" * 200, message_chars=5)
    text = json.dumps(snapshot())
    for leaked in ("uncle", "2348012345678", "2348099999999", "Main Street", "CAsecret", "her fault", "leak", "not-a-specialist", "help"):
        assert leaked not in text
    call = snapshot()["calls"][0]
    # A status-like field is reduced to a short lowercase token.
    status = call["requests"][0]["status"]
    assert status == status.lower() and " " not in status and "!" not in status and len(status) <= 40
    assert len(call["requests"][0]["reason"]) <= 40


@pytest.fixture
def client(monkeypatch):
    def configure(token):
        monkeypatch.setattr(dashboard_routes, "get_settings", lambda: SimpleNamespace(dashboard_token=token))
        server = FastAPI()
        server.include_router(dashboard_routes.router)
        return TestClient(server)
    return configure


def test_dashboard_is_off_unless_a_long_enough_token_is_configured(client):
    for token in ("", "short"):
        http = client(token)
        assert http.get("/dashboard").status_code == 404
        assert http.get("/dashboard/snapshot", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_snapshot_needs_the_bearer_token(client):
    http = client(TOKEN)
    simulate_call()
    assert http.get("/dashboard/snapshot").status_code == 401
    assert http.get("/dashboard/snapshot", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert http.get("/dashboard/snapshot", headers={"Authorization": TOKEN}).status_code == 401
    good = http.get("/dashboard/snapshot", headers={"Authorization": f"Bearer {TOKEN}"})
    assert good.status_code == 200 and good.headers["cache-control"] == "no-store"
    assert good.json()["calls"][0]["ref"] == short_ref("CAdemo")


def test_page_is_served_locked_down_and_holds_no_data(client):
    http = client(TOKEN)
    simulate_call()
    page = http.get("/dashboard")
    assert page.status_code == 200 and "Sabi Safe live case view" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"] and "connect-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["referrer-policy"] == "no-referrer" and "CAdemo" not in page.text and short_ref("CAdemo") not in page.text
    assert "innerHTML" not in page.text  # data is only ever written with textContent
