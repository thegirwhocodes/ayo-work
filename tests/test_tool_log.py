import asyncio
import json
import logging

from app.voice.twilio_stream import TwilioMediaBridge
from harness.tool_log import log_tool_event
from harness.tools import EmergencyCallContext, EmergencyTools


def events(caplog):
    return [json.loads(r.getMessage().split("tool_event ", 1)[1]) for r in caplog.records if r.getMessage().startswith("tool_event ")]


def test_logger_drops_content_and_numbers_and_truncates(caplog):
    caplog.set_level(logging.INFO, logger="sabi.tool")
    payload = log_tool_event("tool_call", tool="x", message="my uncle hurt me", recipient="+12345678901", text="hi",
                             caller_number="+1999", note="y" * 500, status="ok", none_field=None)
    line = caplog.records[0].getMessage()
    assert "uncle" not in line and "12345678901" not in line and "+1999" not in line and "none_field" not in line
    assert payload["status"] == "ok" and payload["note"].endswith("...") and len(payload["note"]) < 200
    assert line.startswith("tool_event {") and json.loads(line[len("tool_event "):])["event"] == "tool_call"


def test_every_live_tool_call_is_logged_without_content(caplog):
    caplog.set_level(logging.INFO, logger="sabi.tool")
    bridge = TwilioMediaBridge(None, "prompt")
    bridge.call_sid = "CAtest"
    secret = "my uncle hit me and lives at 5 Main Street"
    result = asyncio.run(bridge.relay_to_harness("relay_to_harness", {"action": "send_text_message", "message": secret}))
    assert result == {"status": "consent_required"}
    asyncio.run(bridge.relay_to_harness("get_support_guidance", {}))
    asyncio.run(bridge.relay_to_harness("nonsense", {}))
    logged = events(caplog)
    assert [e["tool"] for e in logged] == ["relay_to_harness", "get_support_guidance", "nonsense"]
    first = logged[0]
    assert first["action"] == "send_text_message" and first["status"] == "consent_required" and first["call_sid"] == "CAtest"
    assert first["message_chars"] == len(secret) and first["responder_consent"] is False and "duration_ms" in first
    assert logged[2]["status"] == "unavailable" and logged[2]["reason"] == "unknown_tool"
    assert secret not in caplog.text and "Main Street" not in caplog.text


def test_emergency_tool_execution_logs_the_outcome(caplog):
    caplog.set_level(logging.INFO, logger="sabi.tool")
    tools = EmergencyTools(EmergencyCallContext("+19876543210"), "send_text_message", "sensitive words")
    asyncio.run(tools.run("send_text_message"))
    asyncio.run(tools.run("request_location"))  # not the requested action
    logged = events(caplog)
    assert logged[0]["event"] == "emergency_tool" and logged[0]["tool"] == "send_text_message" and logged[0]["status"] == "consent_required"
    assert logged[1]["reason"] == "action_not_requested"
    assert "sensitive words" not in caplog.text and "9876543210" not in caplog.text


def test_emergency_result_tells_the_agent_what_it_may_claim():
    bridge = TwilioMediaBridge(None, "prompt")
    sent = []

    class Session:
        async def send_text(self, text, **kw):
            sent.append(text)

    bridge.gemini_session = Session()
    asyncio.run(bridge._report_emergency_result({"status": "submitted", "actions": {"request_location": {"status": "submitted"},
                                                                                "send_text_message": {"status": "consent_required"}}}))
    asyncio.run(bridge._report_emergency_result({"status": "unavailable", "reason": "photon_not_configured", "actions": {}}))
    assert "Do not say anything was delivered" in sent[0] and "Nothing was sent" not in sent[0]
    assert '"request_location": "submitted"' in sent[0] and '"send_text_message": "consent_required"' in sent[0]
    assert "Nothing was sent" in sent[1] and "local emergency number" in sent[1]
