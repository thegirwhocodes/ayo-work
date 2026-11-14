import asyncio
import base64
import json

import pytest

from app.voice.twilio_stream import TwilioMediaBridge
from harness.caller_identity import sign_caller, verify_caller
from harness.tools import SpeakerVerifier, VoiceVerificationResult, VoiceVerificationTool
from harness.agent import create_harness
from harness.call_audio import SpeechCollector
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator
from app.main import app


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get(self, caller_id):
        return self.values.get(caller_id)

    def enroll(self, caller_id, embedding):
        if caller_id in self.values:
            return False
        self.values[caller_id] = embedding
        return True


def test_enrollment_and_subsequent_matching_and_mismatch():
    store = MemoryStore()
    features = iter([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    verifier = SpeakerVerifier(store, 0.75, extractor=lambda audio: next(features))
    assert verifier.verify("+123", b"audio").status == "enrolled"
    assert verifier.verify("+123", b"audio").status == "matched"
    assert verifier.verify("+123", b"audio").status == "not_matched"
    assert store.get("+123") == [1.0, 0.0]


def test_signed_caller_cannot_be_modified():
    token = sign_caller("+1234567890", "secret")
    assert verify_caller(token, "secret") == "+1234567890"
    with pytest.raises(ValueError):
        verify_caller(token, "wrong-secret")


def test_silence_never_counts_as_sufficient_speech():
    collector = SpeechCollector(seconds=1)
    assert not collector.add(bytes(16000))
    with pytest.raises(ValueError, match="not enough speech"):
        collector.audio_16khz()


def test_incoming_webhook_rejects_unsigned_and_signs_valid_caller(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    client = TestClient(app)
    form = {"From": "+1234567890", "FromCountry": "US"}
    assert client.post("/voice/incoming", data=form).status_code == 403
    signature = RequestValidator("secret").compute_signature("http://testserver/voice/incoming", form)
    response = client.post("/voice/incoming", data=form, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert 'name="caller_token"' in response.text
    assert 'name="sabi_country"' in response.text


def test_harness_uses_tool_result_not_model_text(monkeypatch):
    store = MemoryStore()
    verifier = SpeakerVerifier(store, 0.75, extractor=lambda audio: [1.0, 0.0])

    class Agent:
        def __init__(self, tool):
            self.tool = tool

        async def ainvoke(self, messages):
            await self.tool()
            return {"messages": [{"content": "not_matched"}]}

    monkeypatch.setattr("harness.agent.ChatGoogleGenerativeAI", lambda **kwargs: object())
    monkeypatch.setattr("harness.agent.create_deep_agent", lambda **kwargs: Agent(kwargs["tools"][0]))
    voice_tool = VoiceVerificationTool(verifier, "+123", b"audio")
    agent = create_harness(voice_tool)
    asyncio.run(agent.ainvoke({"messages": []}))
    assert voice_tool.result.status == "enrolled"


@pytest.mark.parametrize("tool_called", [True, False])
def test_twilio_verification_uses_signed_number_and_fails_closed(monkeypatch, tool_called):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    received = {}

    class WebSocket:
        async def receive_text(self):
            return json.dumps(next(events))

    class Collector:
        def add(self, pcm):
            return True

        def audio_16khz(self):
            return b"sample"

    class Store:
        def __init__(self, url):
            pass

        def initialize(self):
            pass

    class Agent:
        def __init__(self, voice_tool):
            self.voice_tool = voice_tool

        async def ainvoke(self, messages):
            received.update(caller_id=self.voice_tool.caller_id, audio=self.voice_tool.audio_data)
            if tool_called:
                self.voice_tool.result = VoiceVerificationResult("not_matched", 0.1)

    monkeypatch.setattr("app.voice.twilio_stream.SpeechCollector", Collector)
    monkeypatch.setattr("app.voice.twilio_stream.PostgresVoiceStore", Store)
    monkeypatch.setattr("app.voice.twilio_stream.create_harness", Agent)
    events = iter([
        {"event": "start", "start": {"streamSid": "s", "customParameters": {"caller_token": sign_caller("+1234567890", "secret"), "sabi_country": "US"}}},
        {"event": "media", "media": {"payload": base64.b64encode(b"audio").decode()}},
    ])
    bridge = TwilioMediaBridge(WebSocket(), "prompt")
    error = "did not match" if tool_called else "not called"
    with pytest.raises(RuntimeError, match=error):
        asyncio.run(bridge.verify_call())
    assert received == {"caller_id": "+1234567890", "audio": b"sample"}
    assert bridge.location.country == "US"
