import asyncio
from types import SimpleNamespace

import httpx

from harness.tools import EmergencyCallContext, EmergencyTools, TwilioCaller, build_briefing_twiml, start_phone_call
from harness.tools import start_phone_call as module  # noqa: F401  (function re-export shadows the module name)

SETTINGS = SimpleNamespace(twilio_account_sid="AC123", twilio_auth_token="tok", twilio_phone_number="+15550000000",
                           emergency_responder_number="+2348012345678")


def run(coro):
    return asyncio.run(coro)


def patch(monkeypatch, **overrides):
    import sys
    monkeypatch.setattr(sys.modules["harness.tools.start_phone_call"], "get_settings",
                        lambda: SimpleNamespace(**{**SETTINGS.__dict__, **overrides}))


def caller(handler):
    return TwilioCaller(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def consenting(number="+12345678901"):
    return EmergencyCallContext(number, responder_consent=True)


def test_call_requires_consent_and_a_message(monkeypatch):
    patch(monkeypatch)
    never = caller(lambda request: (_ for _ in ()).throw(AssertionError("no network without consent")))
    assert run(start_phone_call(EmergencyCallContext("+12345678901"), "help", never)) == {"status": "consent_required"}
    assert run(start_phone_call(consenting(), "   ", never))["reason"] == "empty_message"


def test_call_is_submitted_to_twilio_with_inline_briefing(monkeypatch):
    patch(monkeypatch)
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(201, json={"sid": "CA999", "status": "queued"})

    result = run(start_phone_call(consenting(), "A child needs help at 5 Main Street", caller(handler)))
    assert result["status"] == "submitted" and result["call_sid"] == "CA999"
    assert "did not" not in result["voice_instruction"] and "Do not say the responder answered" in result["voice_instruction"]
    assert seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Calls.json"
    assert seen["auth"].startswith("Basic ")
    assert seen["body"]["To"] == "+2348012345678" and seen["body"]["From"] == "+15550000000"
    assert "5 Main Street" in seen["body"]["Twiml"] and "+12345678901" in seen["body"]["Twiml"]


def test_twiml_escapes_markup_and_caps_length():
    twiml = build_briefing_twiml("+12345678901", "<Hangup/> & more " + "x" * 2000)
    assert "<Hangup/>" not in twiml and "&lt;Hangup/&gt; &amp; more" in twiml
    assert twiml.count("x") <= 2 * 600
    assert "telephone" not in build_briefing_twiml("not a number", "hi")  # unverifiable numbers are not read out


def test_failures_are_reported_honestly(monkeypatch):
    patch(monkeypatch)
    rejected = run(start_phone_call(consenting(), "help", caller(lambda r: httpx.Response(400, json={"code": 21219}))))
    assert rejected == {"status": "unavailable", "reason": "twilio_rejected", "twilio_error_code": 21219}
    server = run(start_phone_call(consenting(), "help", caller(lambda r: httpx.Response(503))))
    assert server["status"] == "unknown"

    def boom(request):
        raise httpx.ConnectError("down")

    assert run(start_phone_call(consenting(), "help", caller(boom)))["status"] == "unknown"


def test_unconfigured_cases_make_no_request(monkeypatch):
    never = caller(lambda request: (_ for _ in ()).throw(AssertionError("no request expected")))
    patch(monkeypatch, twilio_phone_number="")
    assert run(start_phone_call(consenting(), "help", never))["reason"] == "twilio_not_configured"
    patch(monkeypatch, emergency_responder_number="")
    assert run(start_phone_call(consenting(), "help", never))["reason"] == "responder_not_configured"
    patch(monkeypatch, emergency_responder_number="not-a-number")
    assert run(start_phone_call(consenting(), "help", never))["reason"] == "invalid_recipient"


def test_emergency_tools_runs_the_call_once(monkeypatch):
    patch(monkeypatch)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(201, json={"sid": "CA1"})

    tools = EmergencyTools(consenting(), "start_phone_call", "help", caller=caller(handler))
    assert '"submitted"' in run(tools.start_phone_call())
    run(tools.start_phone_call())
    assert len(calls) == 1


def test_relay_requires_consent_before_calling():
    from app.voice.twilio_stream import TwilioMediaBridge
    bridge = TwilioMediaBridge(None, "prompt")
    for action in ("start_phone_call", "send_text_message"):
        assert run(bridge.relay_to_harness("relay_to_harness", {"action": action, "message": "help"})) == {"status": "consent_required"}


def test_main_harness_agent_has_the_same_three_tools_with_the_same_descriptions(monkeypatch):
    from harness.tools.emergency_agent_tools import DESCRIPTIONS, build_call_tools

    context = EmergencyCallContext("+12345678901")
    tools = {t.name: t for t in build_call_tools(context)}
    assert set(tools) == set(DESCRIPTIONS) and all(tools[n].description == DESCRIPTIONS[n] for n in tools)
    # consent is still enforced on the main agent's copy, and a refusal is stored, not retried
    assert json.loads(run(tools["send_text_message"].ainvoke({})))["status"] == "consent_required"


import json  # noqa: E402
