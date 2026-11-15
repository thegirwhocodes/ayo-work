"""Ayo pieces layered on the pipeline: private prompt, recorded greeting, first-words replay,
runtime-context slot, and the silence check-in."""
import asyncio
import base64
import json
import time

from app.voice.gemini_live import LiveConfig
from app.voice.openai_realtime import SILENCE_CHECKIN_S, SILENCE_NOTE, OpenAIRealtimeSession
from app.voice.twilio_stream import CONSENT_QUESTION, TwilioMediaBridge
from harness.caller_identity import sign_caller


def test_private_prompt_is_used_when_configured(monkeypatch, tmp_path):
    from app import main

    private = tmp_path / "ayo_prompt.md"
    private.write_text("You are Ayo.\n\n{RUNTIME_CONTEXT}\n", encoding="utf-8")
    monkeypatch.setenv("AYO_PROMPT_PATH", str(private))
    assert main.load_system_prompt().startswith("You are Ayo.")
    monkeypatch.setenv("AYO_PROMPT_PATH", str(tmp_path / "missing.md"))
    assert main.load_system_prompt() == main.PROMPT_PATH.read_text(encoding="utf-8")  # public fallback
    monkeypatch.delenv("AYO_PROMPT_PATH")
    assert main.load_system_prompt() == main.PROMPT_PATH.read_text(encoding="utf-8")


def test_runtime_context_slot_gets_location_and_consent_rules_stay():
    bridge = TwilioMediaBridge(None, "Ayo header\n\n{RUNTIME_CONTEXT}\n\nAyo footer")
    bridge.handle_start({"customParameters": {"sabi_country": "NG", "sabi_region": "Nigeria"}})
    prompt = bridge.build_system_prompt()
    assert "{RUNTIME_CONTEXT}" not in prompt
    header, rest = prompt.split("CALLER CONTEXT", 1)
    assert header.startswith("Ayo header") and "Ayo footer" in rest  # briefing sits in the slot
    assert "May I contact the emergency responder with what you shared?" in prompt
    assert CONSENT_QUESTION in prompt.lower().replace("?", "").replace("'", "")


def test_prompt_without_slot_is_unchanged():
    bridge = TwilioMediaBridge(None, "plain prompt")
    assert bridge.build_system_prompt().startswith("plain prompt\n\n---\n\n## CALLER CONTEXT")


def test_twiml_never_asks_the_caller_to_speak(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    client = TestClient(main.app)
    for greeting in (b"", b"\xff" * 10):
        monkeypatch.setattr(main, "GREETING_AUDIO", greeting)
        xml = client.post("/voice/incoming", headers={"host": "x"}).text
        assert "<Say>" not in xml and "speak for a few seconds" not in xml and "<Connect>" in xml


def test_bundled_greeting_is_the_default(monkeypatch):
    from app import main

    monkeypatch.delenv("AYO_GREETING_AUDIO", raising=False)
    assert main.load_greeting_audio() == (main.REPO_ROOT / main.DEFAULT_GREETING_AUDIO).read_bytes() != b""
    monkeypatch.setenv("AYO_GREETING_AUDIO", "off")
    assert main.load_greeting_audio() == b""


def test_greeting_plays_at_start_and_the_conversation_opens_without_waiting(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    sent, spoken = [], []

    class WebSocket:
        async def receive_text(self):
            await asyncio.sleep(0)
            return json.dumps(next(events))

        async def send_text(self, text):
            sent.append(json.loads(text))

    class Engine:
        async def send_text(self, text):
            spoken.append(text)

    greeting = bytes(range(256)) * 10  # 2560 bytes -> 2 media messages
    events = iter([{"event": "start", "start": {"streamSid": "s1", "customParameters": {
        "caller_token": sign_caller("+15550001111", "secret")}}}])
    bridge = TwilioMediaBridge(WebSocket(), "prompt", greeting_audio=greeting)

    async def go():
        await bridge._await_start()  # returns at `start`: no speech is required from the caller
        await bridge._greeting_task
        bridge.gemini_session = Engine()
        await bridge._open_conversation()
    asyncio.run(go())

    media = [m for m in sent if m["event"] == "media"]
    assert len(media) == 2 and all(m["streamSid"] == "s1" for m in media)
    assert b"".join(base64.b64decode(m["media"]["payload"]) for m in media) == greeting
    assert spoken == []  # the recording is the opening line; the engine does not repeat it


def test_without_a_recording_the_engine_speaks_first(monkeypatch):
    spoken = []

    class Engine:
        async def send_text(self, text):
            spoken.append(text)

    bridge = TwilioMediaBridge(None, "prompt", greeting_audio=b"")
    bridge.gemini_session = Engine()
    asyncio.run(bridge._open_conversation())
    assert spoken and "Greet the caller" in spoken[0]


def test_system_prompt_discloses_voice_recognition_without_asking_for_speech():
    prompt = TwilioMediaBridge(None, "prompt").build_system_prompt()
    assert "never ask the caller to say" in prompt and "recognize their voice next time" in prompt
