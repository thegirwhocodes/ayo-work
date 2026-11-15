import asyncio
import inspect
import json

import pytest

from app.voice.gemini_live import GeminiLiveSession, LiveConfig
from app.voice.openai_realtime import (
    OpenAIRealtimeSession, VOICE_STYLE_NOTE, build_session_update, split_sentences, strip_voice_tags,
)
from harness.tools.declarations import RELAY_DECLARATION, SUPPORT_GUIDANCE_DECLARATION
from harness.tools.openai_format import to_openai_tools
from settings import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_tool_conversion_keeps_schema_and_lowercases_types():
    relay, guide = to_openai_tools([RELAY_DECLARATION, SUPPORT_GUIDANCE_DECLARATION])
    assert relay["type"] == "function" and relay["name"] == "relay_to_harness"
    assert relay["parameters"]["type"] == "object"
    assert relay["parameters"]["properties"]["action"]["type"] == "string"
    assert relay["parameters"]["properties"]["action"]["enum"] == ["send_text_message", "request_location", "start_phone_call"]
    assert relay["parameters"]["required"] == ["action"]
    assert guide["parameters"] == {"type": "object", "properties": {}}
    assert RELAY_DECLARATION["parameters"]["type"] == "OBJECT"  # source declaration untouched


def test_session_update_is_text_only_mulaw_with_tools():
    msg = build_session_update(LiveConfig(system_prompt="PROMPT", tools=[RELAY_DECLARATION]))
    session = msg["session"]
    assert msg["type"] == "session.update" and session["output_modalities"] == ["text"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert session["audio"]["input"]["turn_detection"]["silence_duration_ms"] == 1200
    assert session["instructions"] == "PROMPT" + VOICE_STYLE_NOTE
    assert session["tools"][0]["name"] == "relay_to_harness" and session["tool_choice"] == "auto"


def test_sentence_splitting_and_tag_stripping():
    sentences, rest = split_sentences("Hello there. Are you okay? I am lis")
    assert sentences == ["Hello there.", "Are you okay?"] and rest == "I am lis"
    assert split_sentences("It costs 3.50 naira")[0] == []
    assert strip_voice_tags("[softly] May I text the emergency responder with what you shared?") == \
        "May I text the emergency responder with what you shared?"


def test_same_public_interface_as_gemini():
    def public(cls):
        return {n for n, m in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}
    assert public(GeminiLiveSession) <= public(OpenAIRealtimeSession)
    gemini_params = set(inspect.signature(GeminiLiveSession.__init__).parameters)
    assert gemini_params <= set(inspect.signature(OpenAIRealtimeSession.__init__).parameters)


class FakeWS:
    def __init__(self, events, gap=0.0):
        self.events, self.gap, self.sent = events, gap, []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for event in self.events:
            if self.gap:
                await asyncio.sleep(self.gap)
            if callable(event):
                await event()
                continue
            yield json.dumps(event)

    async def close(self):
        pass


def make_session(events, *, tts_chunks=None, tool_result=None, gap=0.0, tts_delay=0.0):
    record = {"audio": [], "turns": [], "tools": [], "interrupted": 0, "spoken": []}

    async def on_audio(pcm):
        record["audio"].append(pcm)

    async def on_interrupted():
        record["interrupted"] += 1

    async def on_turn_complete(caller, model):
        record["turns"].append((caller, model))

    async def on_tool_call(name, args):
        record["tools"].append((name, args))
        return tool_result or {"status": "pending"}

    async def tts(sentence):
        record["spoken"].append(sentence)
        for chunk in (tts_chunks or [b"\x01\x02\x03", b"\x04\x05"]):
            if tts_delay:
                await asyncio.sleep(tts_delay)
            yield chunk

    session = OpenAIRealtimeSession(LiveConfig(system_prompt="p", tools=[RELAY_DECLARATION]),
                                    on_audio=on_audio, on_interrupted=on_interrupted,
                                    on_turn_complete=on_turn_complete, on_tool_call=on_tool_call,
                                    api_key="test", tts=tts)
    session._ws = FakeWS(events, gap)
    session.fake_ws = session._ws  # keep a handle: close() clears _ws
    return session, record


def test_reply_spoken_transcript_paired_late_and_tool_answered():
    events = [
        {"type": "input_audio_buffer.speech_started"},
        {"type": "input_audio_buffer.committed", "item_id": "item_1"},
        {"type": "response.created"},
        {"type": "response.output_text.delta", "delta": "Thank you for telling me. "},
        {"type": "response.output_text.delta", "delta": "[softly] May I text the emergency responder with what you shared?"},
        {"type": "response.function_call_arguments.done", "call_id": "call_9", "name": "relay_to_harness",
         "arguments": '{"action": "request_location"}'},
        {"type": "response.done"},
        # OpenAI's transcription of the caller arrives AFTER the reply finished
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "item_1", "transcript": "I need help"},
    ]
    session, record = make_session(events)

    async def go():
        session._start_workers()
        await session.receive_forever()
        await asyncio.sleep(0.2)
        await session.close()
    asyncio.run(go())

    assert record["turns"] == [("I need help", "Thank you for telling me. May I text the emergency responder with what you shared?")]
    assert record["spoken"] == ["Thank you for telling me.", "[softly] May I text the emergency responder with what you shared?"]
    assert all(len(chunk) % 2 == 0 for chunk in record["audio"])  # whole PCM16 samples only
    assert sum(map(len, record["audio"])) == 8  # 5 bytes per sentence -> 4 whole-sample bytes each, odd byte dropped
    assert record["tools"] == [("relay_to_harness", {"action": "request_location"})]
    outputs = [m for m in session.fake_ws.sent if m["type"] == "conversation.item.create"]
    assert outputs[0]["item"] == {"type": "function_call_output", "call_id": "call_9", "output": json.dumps({"status": "pending"})}
    assert session.fake_ws.sent[-1] == {"type": "response.create"}


def test_barge_in_stops_voice_and_mutes_rest_of_reply():
    events = [
        {"type": "input_audio_buffer.committed", "item_id": "item_1"},
        {"type": "response.created"},
        {"type": "response.output_text.delta", "delta": "This is a long first sentence. "},
        {"type": "input_audio_buffer.speech_started"},  # caller talks over Ayo
        {"type": "response.output_text.delta", "delta": "This must never be spoken. "},
        {"type": "response.done"},
    ]
    session, record = make_session(events, tts_chunks=[b"\x00\x00"] * 50, gap=0.02, tts_delay=0.01)

    async def go():
        session._start_workers()
        await session.receive_forever()
        await asyncio.sleep(0.3)
        await session.close()
    asyncio.run(go())

    assert record["interrupted"] == 1
    assert "This must never be spoken." not in record["spoken"]
    assert len(record["audio"]) < 50  # playback stopped part-way


def test_send_text_waits_for_active_reply_before_asking_again():
    session, _ = make_session([])
    session._ws = FakeWS([])

    async def go():
        session._response_active = True
        await session.send_text("Emergency action result: pending")
        assert {"type": "response.create"} not in session._ws.sent  # only one active reply allowed
        await session._finish_response()
    asyncio.run(go())
    assert session._ws.sent[0]["item"]["role"] == "system"
    assert session._ws.sent[-1] == {"type": "response.create"}


def test_bridge_picks_engine_from_setting(monkeypatch):
    from app.voice import twilio_stream

    chosen = []

    async def fake_connect(self, timeout=12.0):
        chosen.append(type(self).__name__)

    monkeypatch.setattr(OpenAIRealtimeSession, "connect", fake_connect)
    monkeypatch.setattr(GeminiLiveSession, "connect", fake_connect)
    for engine, expected in (("openai", "OpenAIRealtimeSession"), ("gemini", "GeminiLiveSession"), ("", "GeminiLiveSession")):
        monkeypatch.setenv("VOICE_ENGINE", engine)
        get_settings.cache_clear()
        bridge = twilio_stream.TwilioMediaBridge(None, "prompt")
        assert asyncio.run(bridge._connect_gemini()) is True
        assert chosen[-1] == expected


def test_connect_requires_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    get_settings.cache_clear()
    from app.voice.gemini_live import LiveSetupError

    async def noop(*a, **k): ...
    session = OpenAIRealtimeSession(LiveConfig(system_prompt="p"), on_audio=noop, on_interrupted=noop, on_turn_complete=noop)
    with pytest.raises(LiveSetupError, match="OPENAI_API_KEY"):
        asyncio.run(session.connect())

