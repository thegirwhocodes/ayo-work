import asyncio
import json

import httpx
import pytest

from app.voice.twilio_stream import TwilioMediaBridge
from app.voice.gemini_live import LiveConfig, build_setup_message
from harness.agent import create_harness
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


def test_message_timeout_is_unknown_and_not_retried(monkeypatch):
    monkeypatch.setenv("PHOTON_API_KEY", "secret")
    monkeypatch.setenv("PHOTON_PROJECT_ID", "project")
    monkeypatch.setenv("PHOTON_SENDER_ID", "sender")
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json={"id": "recipient"})
        if request.url.path.endswith("/spaces"):
            return httpx.Response(200, json={"id": "space"})
        raise httpx.ReadTimeout("submission response lost", request=request)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await request_location(EmergencyCallContext("+19876543210"), PhotonMessaging(client))

    assert run(exercise())["status"] == "unknown"
    assert sum(path.endswith("/messages") for path in attempts) == 1


def test_consent_and_relay_fail_closed(monkeypatch):
    bridge = TwilioMediaBridge(object(), "prompt")
    bridge.emergency_context.caller_number = "+19876543210"
    assert run(bridge.relay_to_harness("relay_to_harness", {"action": "send_text_message", "message": "help"}))["status"] == "consent_required"

    async def give_consent():
        await bridge._on_turn_complete("", "May I text the emergency responder with what you shared?")
        await bridge._on_turn_complete("Yes please", "Okay.")
    run(give_consent())
    assert bridge.emergency_context.responder_consent

    finished = {}

    async def create(tools, session_id):
        return "x" * 43

    async def poll(token):
        return finished.get(token)

    async def cancel(token):
        pass

    monkeypatch.setattr("app.voice.twilio_stream.create_job", create)
    monkeypatch.setattr("app.voice.twilio_stream.get_job_result", poll)
    monkeypatch.setattr("app.voice.twilio_stream.cancel_job", cancel)

    class Agent:
        async def ainvoke(self, payload, config=None):
            import re
            token = re.search(r"emergency-job:([A-Za-z0-9_-]{43})", payload["messages"][0]["content"]).group(1)
            finished[token] = {"status": "not_implemented"}

    class Session:
        def __init__(self):
            self.messages = []

        async def send_text(self, text):
            self.messages.append(text)

    async def exercise():
        bridge.harness_agent = Agent()
        bridge.gemini_session = Session()
        result = await bridge.relay_to_harness("relay_to_harness", {"action": "start_phone_call"})
        assert result == {"status": "pending"}
        await asyncio.gather(*bridge.emergency_tasks)
        return bridge.gemini_session.messages

    messages = run(exercise())
    assert '"status": "not_implemented"' in messages[0]
    assert run(bridge.relay_to_harness("relay_to_harness", {"action": "invalid"}))["status"] == "unavailable"


def test_tool_is_restricted_to_requested_action():
    tools = EmergencyTools(EmergencyCallContext("+19876543210"), "start_phone_call")
    assert json.loads(run(tools.request_location()))["reason"] == "action_not_requested"
    assert json.loads(run(tools.start_phone_call()))["status"] == "not_implemented"


def test_gemini_is_exposed_only_to_relay_and_main_harness_has_async_subagent(monkeypatch):
    captured = {}
    monkeypatch.setattr("harness.agent.ChatGoogleGenerativeAI", lambda **kwargs: object())
    monkeypatch.setattr("harness.agent.create_deep_agent", lambda **kwargs: captured.update(kwargs) or object())
    class VoiceTool:
        async def verify_speaker(self):
            return "ok"
    create_harness(VoiceTool())
    assert captured["subagents"][0]["name"] == "emergency"
    assert captured["subagents"][0]["graph_id"] == "emergency"
    bridge = TwilioMediaBridge(object(), "prompt")
    from app.voice.twilio_stream import RELAY_DECLARATION
    setup = build_setup_message(LiveConfig(system_prompt=bridge.build_system_prompt(), tools=[RELAY_DECLARATION]))
    declarations = setup["setup"]["tools"][0]["functionDeclarations"]
    assert [tool["name"] for tool in declarations] == ["relay_to_harness"]
    assert "Never promise" in setup["setup"]["systemInstruction"]["parts"][0]["text"]
