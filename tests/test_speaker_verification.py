import asyncio
import base64
import json
import sys

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

        async def ainvoke(self, messages, config=None):
            await self.tool()
            return {"messages": [{"content": "not_matched"}]}

    monkeypatch.setattr("harness.agent.ChatGoogleGenerativeAI", lambda **kwargs: object())
    monkeypatch.setattr("harness.agent.create_deep_agent", lambda **kwargs: Agent(kwargs["tools"][0]))
    voice_tool = VoiceVerificationTool(verifier, "+123", b"audio")
    agent = create_harness(voice_tool)
    asyncio.run(agent.ainvoke({"messages": []}))
    assert voice_tool.result.status == "enrolled"



def _check_voice(monkeypatch, status, harness=None):
    """Run the bridge's voice check against a fake verifier; returns the bridge and what the verifier saw."""
    from app.voice import twilio_stream
    ran = []

    class Store:
        def __init__(self, url):
            pass

        def initialize(self):
            pass

    class Verifier:
        def __init__(self, store, threshold):
            pass

        def verify(self, caller_id, audio):
            ran.append((caller_id, audio))
            if status is None:
                raise RuntimeError("extractor failed")
            return VoiceVerificationResult(status, 0.9)

    class Collector:
        def audio_16khz(self):
            return b"sample"

    monkeypatch.setattr(twilio_stream, "PostgresVoiceStore", Store)
    monkeypatch.setattr(twilio_stream, "SpeakerVerifier", Verifier)
    if harness is not None:
        monkeypatch.setattr(twilio_stream, "create_harness", harness)
    bridge = TwilioMediaBridge(None, "prompt")
    asyncio.run(bridge._check_voice(Collector(), "+1234567890"))
    return bridge, ran


@pytest.mark.parametrize("status,state", [("matched", "known"), ("not_matched", "unknown"), ("enrolled", "new")])
def test_voice_check_sets_identity_from_the_verifier(monkeypatch, status, state):
    bridge, ran = _check_voice(monkeypatch, status)
    assert ran == [("+1234567890", b"sample")]
    assert bridge.verification_status == status and bridge.identity_state == state


def test_voice_check_uses_no_llm(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("verification must not go through the harness model")

    bridge, _ = _check_voice(monkeypatch, "enrolled", harness=forbidden)
    assert bridge.verification_status == "enrolled"


def test_verifier_failure_never_ends_the_call(monkeypatch):
    """verify_call turns any failure of the check into 'unsure'; the call carries on."""
    from app.voice import twilio_stream
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setattr(twilio_stream, "SpeechCollector", type("C", (), {"add": lambda self, pcm: True, "audio_16khz": lambda self: b"s"}))
    monkeypatch.setattr(twilio_stream, "PostgresVoiceStore", type("S", (), {"__init__": lambda self, url: None, "initialize": lambda self: None}))

    class Verifier:
        def __init__(self, store, threshold):
            pass

        def verify(self, caller_id, audio):
            raise RuntimeError("extractor failed")

    monkeypatch.setattr(twilio_stream, "SpeakerVerifier", Verifier)
    events = iter([
        {"event": "start", "start": {"streamSid": "s", "customParameters": {"caller_token": sign_caller("+1234567890", "secret"), "sabi_country": "US"}}},
        {"event": "media", "media": {"payload": base64.b64encode(b"audio").decode()}},
    ])

    class WebSocket:
        async def receive_text(self):
            return json.dumps(next(events))

    bridge = TwilioMediaBridge(WebSocket(), "prompt")
    asyncio.run(bridge.verify_call())  # does not raise
    assert bridge.emergency_context.caller_number == "+1234567890" and bridge.location.country == "US"
    assert bridge.identity_state == "unsure" and bridge.verification_status is None

def test_ensure_speaker_model_skips_download_when_files_present(tmp_path, monkeypatch):
    from harness.tools import verify_voice
    for name in verify_voice.MODEL_FILES:
        (tmp_path / name).write_text("x")
    monkeypatch.setenv("SPEAKER_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)  # any import/use would fail
    assert verify_voice.ensure_speaker_model() == tmp_path


def test_ensure_speaker_model_downloads_when_missing(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from harness.tools import verify_voice
    calls = []

    def fake_download(repo_id, local_dir, allow_patterns):
        calls.append(repo_id)
        for name in allow_patterns:
            (tmp_path / name).write_text("x")

    monkeypatch.setenv("SPEAKER_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=fake_download))
    assert verify_voice.ensure_speaker_model() == tmp_path
    assert (tmp_path / "label_encoder.ckpt").is_file() and not (tmp_path / "label_encoder.ckpt").is_symlink()
    assert calls == [verify_voice.MODEL_SOURCE]
    assert verify_voice.ensure_speaker_model() == tmp_path and len(calls) == 1  # second call: no download
