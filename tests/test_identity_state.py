"""Voice identity states: a failed or impossible voice check never hangs up on the child."""
import asyncio
import base64
import json

import pytest

from app.voice import twilio_stream
from app.voice.twilio_stream import TwilioMediaBridge
from harness.caller_identity import sign_caller
from harness.tools import VoiceVerificationResult

NUMBER = "+2348012345678"


def media():
    return {"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}


def start(token=None):
    token = sign_caller(NUMBER, "secret") if token is None else token
    return {"event": "start", "start": {"streamSid": "s", "customParameters": {"caller_token": token}}}


def run_check(monkeypatch, events, *, collector_done=True, result="matched", store_fails=False, timeout=None):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    feed = iter(events)

    class WebSocket:
        async def receive_text(self):
            try:
                return json.dumps(next(feed))
            except StopIteration:
                await asyncio.sleep(3600)

    class Collector:
        def add(self, pcm):
            return collector_done

        def audio_16khz(self):
            return b"sample"

    class Store:
        def __init__(self, url): pass

        def initialize(self):
            if store_fails:
                raise ConnectionError("no database")

    class Verifier:
        def __init__(self, store, threshold): pass

        def verify(self, caller_id, audio):
            if result is None:
                raise RuntimeError("extractor failed")
            return VoiceVerificationResult(result, 0.9)

    class Agent:
        def __init__(self, tool, emergency_context=None): self.tool, self.emergency_context = tool, emergency_context

    monkeypatch.setattr(twilio_stream, "SpeechCollector", Collector)
    monkeypatch.setattr(twilio_stream, "PostgresVoiceStore", Store)
    monkeypatch.setattr(twilio_stream, "SpeakerVerifier", Verifier)
    monkeypatch.setattr(twilio_stream, "create_harness", Agent)
    if timeout is not None:
        monkeypatch.setattr(twilio_stream, "START_TIMEOUT_S", timeout)
    bridge = TwilioMediaBridge(WebSocket(), "prompt")

    async def call():
        # the real flow: wait for `start`, then feed media into the quiet background check
        await bridge._await_start()
        for event in feed:
            if event.get("event") == "media":
                bridge._collect_for_verification(b"\x00" * 320)
        if bridge._verify_task:
            await bridge._verify_task

    asyncio.run(call())
    return bridge


@pytest.mark.parametrize("status,state", [("matched", "known"), ("enrolled", "new"), ("not_matched", "unknown"), ("weird", "unsure")])
def test_result_maps_to_identity_state(monkeypatch, status, state):
    bridge = run_check(monkeypatch, [start(), media()], result=status)
    assert bridge.identity_state == state and bridge.identity_reason == status


def test_known_and_new_keep_memory_eligibility_unknown_does_not(monkeypatch):
    assert run_check(monkeypatch, [start(), media()], result="matched").verification_status == "matched"
    assert run_check(monkeypatch, [start(), media()], result="not_matched").verification_status == "not_matched"
    assert run_check(monkeypatch, [start(), media()], result="weird").verification_status is None


def test_too_little_speech_is_unsure_not_a_hangup(monkeypatch):
    bridge = run_check(monkeypatch, [start(), media()], collector_done=False)  # whispering or silent child: no voiced audio
    assert bridge.identity_state == "unsure" and bridge.identity_reason == "not_checked"
    assert bridge._verify_task is None and bridge.emergency_context.caller_number == NUMBER


def test_the_check_never_blocks_the_conversation(monkeypatch):
    """_await_start returns at `start`; enough speech later spawns a task instead of awaiting the check."""
    bridge = run_check(monkeypatch, [start()])
    assert bridge._voice_collector is not None and bridge._verify_task is None  # nothing asked of the caller yet


def test_database_down_is_unsure_not_a_hangup(monkeypatch):
    bridge = run_check(monkeypatch, [start(), media()], store_fails=True)
    assert bridge.identity_state == "unsure" and bridge.identity_reason == "check_failed:ConnectionError"


def test_verifier_failure_is_unsure(monkeypatch):
    bridge = run_check(monkeypatch, [start(), media()], result=None)
    assert bridge.identity_state == "unsure" and bridge.identity_reason == "check_failed:RuntimeError"


def test_forged_caller_token_is_still_fatal(monkeypatch):
    with pytest.raises(Exception):
        run_check(monkeypatch, [start(token="forged"), media()])


def test_hangup_before_the_stream_starts_is_still_fatal(monkeypatch):
    with pytest.raises(RuntimeError):
        run_check(monkeypatch, [{"event": "stop"}])
    with pytest.raises(RuntimeError, match="never started"):
        run_check(monkeypatch, [], timeout=0.2)


def test_emergency_agent_is_built_on_demand_when_the_check_was_skipped(monkeypatch):
    bridge = run_check(monkeypatch, [start(), media()], collector_done=False)
    assert bridge.harness_agent is None
    bridge._ensure_harness_agent()
    assert bridge.harness_agent is not None
    assert bridge.harness_agent.emergency_context is bridge.emergency_context  # always carries the emergency tools
