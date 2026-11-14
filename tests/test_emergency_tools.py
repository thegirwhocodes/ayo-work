import asyncio
import json

import httpx
import pytest

from app.voice.twilio_stream import TwilioMediaBridge
from app.voice.gemini_live import LiveConfig, build_setup_message
from harness.agent import create_emergency_harness
from harness.tools import EmergencyCallContext, EmergencyTools, PhotonMessaging, request_location, send_text_message
from settings import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def run(coroutine):
    return asyncio.run(coroutine)


def test_photon_sms_uses_project_sender_and_reports_submission(monkeypatch):
    monkeypatch.setenv("PHOTON_API_KEY", "secret")
    monkeypatch.setenv("PHOTON_PROJECT_ID", "project")
    monkeypatch.setenv("PHOTON_SENDER_ID", "sender")
    monkeypatch.setenv("EMERGENCY_RESPONDER_NUMBER", "+12345678901")
    requests = []

    def handler(request):
        requests.append((request.url.path, json.loads(request.content), request.headers["Authorization"]))
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json={"id": "recipient"})
        if request.url.path.endswith("/spaces"):
            return httpx.Response(201, json={"id": "space"})
        return httpx.Response(202, json={"id": "message"})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            photon = PhotonMessaging(client)
            context = EmergencyCallContext("+19876543210", responder_consent=True)
            result = await send_text_message(context, "Help needed", photon)
            location = await request_location(context, photon)
            return result, location

    result, location = run(exercise())
    assert result == {"status": "submitted", "message_id": "message"}
    assert requests[0][1] == {"platform": "sms", "handle": "+12345678901"}
    assert requests[1][1]["participantIds"] == ["sender", "recipient"]
    assert requests[2][1]["content"]["text"] == "Help needed"
    assert all(item[2] == "Bearer secret" for item in requests)
    assert requests[3][1]["handle"] == "+19876543210"
    assert location["status"] == "submitted"
    assert "Do not promise" in location["voice_instruction"]


def test_no_consent_and_photon_failure_do_not_claim_success(monkeypatch):
    context = EmergencyCallContext("+19876543210")
    assert run(send_text_message(context, "Help", PhotonMessaging())) == {"status": "consent_required"}
    monkeypatch.setenv("PHOTON_API_KEY", "secret")
    monkeypatch.setenv("PHOTON_PROJECT_ID", "project")
    monkeypatch.setenv("PHOTON_SENDER_ID", "sender")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            return await request_location(context, PhotonMessaging(client))

    assert run(exercise())["status"] == "unavailable"


def test_consent_and_relay_fail_closed(monkeypatch):
    bridge = TwilioMediaBridge(object(), "prompt")
    bridge.emergency_context.caller_number = "+19876543210"
    assert run(bridge.relay_to_harness("relay_to_harness", {"action": "send_text_message", "message": "help"}))["status"] == "consent_required"

    async def give_consent():
        await bridge._on_turn_complete("", "May I text the emergency responder with what you shared?")
        await bridge._on_turn_complete("Yes please", "Okay.")
    run(give_consent())
    assert bridge.emergency_context.responder_consent

    class Agent:
        def __init__(self, tools):
            self.tools = tools

        async def ainvoke(self, payload):
            await getattr(self.tools, self.tools.action)()
            return {"messages": [{"content": "Call placed"}]}

    monkeypatch.setattr("app.voice.twilio_stream.create_emergency_harness", Agent)
    result = run(bridge.relay_to_harness("relay_to_harness", {"action": "start_phone_call"}))
    assert result == {"status": "not_implemented"}
    assert run(bridge.relay_to_harness("relay_to_harness", {"action": "invalid"}))["status"] == "unavailable"


def test_tool_is_restricted_to_requested_action():
    tools = EmergencyTools(EmergencyCallContext("+19876543210"), "start_phone_call")
    assert json.loads(run(tools.request_location()))["reason"] == "action_not_requested"
    assert json.loads(run(tools.start_phone_call()))["status"] == "not_implemented"


def test_gemini_is_exposed_only_to_relay_and_harness_has_three_tools(monkeypatch):
    captured = {}
    monkeypatch.setattr("harness.agent.ChatGoogleGenerativeAI", lambda **kwargs: object())
    monkeypatch.setattr("harness.agent.create_deep_agent", lambda **kwargs: captured.update(kwargs) or object())
    tools = EmergencyTools(EmergencyCallContext("+19876543210"), "start_phone_call")
    create_emergency_harness(tools)
    assert [tool.__name__ for tool in captured["tools"]] == [
        "send_text_message", "request_location", "start_phone_call"
    ]
    bridge = TwilioMediaBridge(object(), "prompt")
    from app.voice.twilio_stream import RELAY_DECLARATION
    setup = build_setup_message(LiveConfig(system_prompt=bridge.build_system_prompt(), tools=[RELAY_DECLARATION]))
    declarations = setup["setup"]["tools"][0]["functionDeclarations"]
    assert [tool["name"] for tool in declarations] == ["relay_to_harness"]
    assert "Never promise" in setup["setup"]["systemInstruction"]["parts"][0]["text"]
