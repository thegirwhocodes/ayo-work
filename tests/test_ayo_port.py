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


def test_twiml_drops_robot_voice_only_when_greeting_is_configured(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    client = TestClient(main.app)
    monkeypatch.setattr(main, "GREETING_AUDIO", b"")
    assert "<Say>" in client.post("/voice/incoming", headers={"host": "x"}).text
    monkeypatch.setattr(main, "GREETING_AUDIO", b"\xff" * 10)
    xml = client.post("/voice/incoming", headers={"host": "x"}).text
    assert "<Say>" not in xml and "<Connect>" in xml


def test_greeting_plays_and_first_words_are_replayed(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    sent, replayed = [], []

    class WebSocket:
        async def receive_text(self):
            await asyncio.sleep(0)
            return json.dumps(next(events))

        async def send_text(self, text):
            sent.append(json.loads(text))

    class Collector:
        calls = 0

        def add(self, pcm):
            Collector.calls += 1
            return Collector.calls == 3

    class Engine:
        async def send_audio(self, pcm):
            replayed.append(pcm)

    monkeypatch.setattr("app.voice.twilio_stream.SpeechCollector", Collector)
    greeting = bytes(range(256)) * 10  # 2560 bytes -> 2 media messages
    events = iter([{"event": "start", "start": {"streamSid": "s1", "customParameters": {
        "caller_token": sign_caller("+15550001111", "secret")}}}] +
        [{"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}] * 3)
    bridge = TwilioMediaBridge(WebSocket(), "prompt", greeting_audio=greeting)

    async def go():
        collector = Collector()
        # run only the event-reading half of verify_call (no database / ECAPA here)
        while True:
            data = json.loads(await bridge.ws.receive_text())
            if data["event"] == "start":
                bridge.handle_start(data["start"])
                bridge._start_greeting()
            else:
                pcm = __import__("app.voice.audio", fromlist=["mulaw_to_pcm16"]).mulaw_to_pcm16(
                    base64.b64decode(data["media"]["payload"]))
                bridge._keep_first_words(pcm)
                if collector.add(pcm):
                    break
        await bridge._greeting_task
        bridge.gemini_session = Engine()
        await bridge._replay_first_words()
    asyncio.run(go())

    media = [m for m in sent if m["event"] == "media"]
    assert len(media) == 2 and all(m["streamSid"] == "s1" for m in media)
    assert b"".join(base64.b64decode(m["media"]["payload"]) for m in media) == greeting
    assert sum(map(len, replayed)) == 3 * 160 * 2 and all(len(p) <= 320 for p in replayed)
    assert bridge._first_words == bytearray()  # replayed once, then cleared


def test_verify_call_starts_greeting_on_signed_start(monkeypatch):
    """The real verify_call kicks off the greeting right after the caller token checks out."""
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    started = []
    bridge = TwilioMediaBridge(None, "prompt", greeting_audio=b"\xff" * 1600)
    monkeypatch.setattr(bridge, "_start_greeting", lambda: started.append(bridge.stream_sid))

    class WebSocket:
        async def receive_text(self):
            return json.dumps(next(events))

    events = iter([{"event": "start", "start": {"streamSid": "s9", "customParameters": {
        "caller_token": sign_caller("+15550001111", "secret")}}}, {"event": "stop"}])
    bridge.ws = WebSocket()
    try:
        asyncio.run(bridge.verify_call())
    except RuntimeError:
        pass  # "call ended before verification"
    assert started == ["s9"]


def test_first_words_buffer_is_capped():
    bridge = TwilioMediaBridge(None, "prompt")
    from app.voice.twilio_stream import FIRST_WORDS_MAX_BYTES

    for _ in range(100):
        bridge._keep_first_words(b"\x00" * 10000)
    assert len(bridge._first_words) == FIRST_WORDS_MAX_BYTES


def _session():
    async def noop(*args):
        return None
    return OpenAIRealtimeSession(LiveConfig(system_prompt="p"), on_audio=noop, on_interrupted=noop,
                                 on_turn_complete=noop, api_key="test", tts=lambda s: None)


def test_silence_check_in_rules():
    session = _session()
    now = time.monotonic()
    session._last_caller_activity = now - 100
    assert not session._silence_due(now)                     # Ayo has not spoken yet
    session._ayo_has_spoken = True
    assert session._silence_due(now)
    session._playback_until = now - 1                         # Ayo only just stopped talking
    assert not session._silence_due(now)
    session._playback_until = now - SILENCE_CHECKIN_S
    session._caller_speaking = True
    assert not session._silence_due(now)                      # child is talking
    session._caller_speaking, session._response_active = False, True
    assert not session._silence_due(now)                      # a reply is on its way
    session._response_active, session._checked_in = False, True
    assert not session._silence_due(now)                      # one check-in per silence


def test_silence_check_in_sends_one_note_and_child_speech_rearms():
    session = _session()

    class FakeWS:
        sent = []

        async def send(self, raw):
            self.sent.append(json.loads(raw))

    session._ws = FakeWS()
    session._ayo_has_spoken = True
    session._last_caller_activity = time.monotonic() - 60

    async def go():
        worker = asyncio.create_task(session._silence_worker())
        await asyncio.sleep(1.2)
        assert session._checked_in
        await session._handle({"type": "input_audio_buffer.speech_started"})
        assert not session._checked_in and session._caller_speaking
        worker.cancel()
    asyncio.run(go())
    notes = [m for m in session._ws.sent if m["type"] == "conversation.item.create"]
    assert len(notes) == 1 and notes[0]["item"]["content"][0]["text"] == SILENCE_NOTE


def test_outbound_call_keys_identity_and_location_on_the_person_not_ayos_number():
    from app.main import _caller_view

    outbound = {"Direction": "outbound-api", "From": "+17153122345", "FromCountry": "US",
                "To": "+2348012345678", "ToCountry": "NG", "ToCity": "LAGOS"}
    view = _caller_view(outbound)
    assert view["From"] == "+2348012345678" and view["FromCountry"] == "NG" and view["FromCity"] == "LAGOS"
    inbound = {"Direction": "inbound", "From": "+2348012345678", "To": "+17153122345"}
    assert _caller_view(inbound) is inbound


def test_outbound_webhook_signs_the_callee_and_validates_the_original_signature(monkeypatch):
    from fastapi.testclient import TestClient
    from twilio.request_validator import RequestValidator
    from app import main

    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    form = {"Direction": "outbound-api", "From": "+17153122345", "To": "+2348012345678", "CallSid": "CA1"}
    sig = RequestValidator("secret").compute_signature("http://testserver/voice/incoming", form)
    response = TestClient(main.app).post("/voice/incoming", data=form, headers={"X-Twilio-Signature": sig})
    assert response.status_code == 200 and 'name="caller_token"' in response.text
    token = response.text.split('name="caller_token" value="')[1].split('"')[0]
    from harness.caller_identity import verify_caller
    assert verify_caller(token, "secret") == "+2348012345678"
