"""Regression tests for the problems found in the harness review (14 Nov 2026).

One group per finding: memory consent, the voice gate, consent scope and lifetime, the internal
callback port, the harness model as a single point of failure, and Compose needing everything.
"""

import asyncio
import base64
import json
import socket
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app import main
from app.internal import internal_app
from app.voice import twilio_stream
from app.voice.consent import (
    CALLER_TEXT, QUESTIONS, RESPONDER, ConsentTracker, memory_question_asked, normalize,
)
from app.voice.twilio_stream import TwilioMediaBridge
from harness.caller_identity import ANONYMOUS, sign_caller, verify_caller
from harness.tools import EmergencyCallContext, EmergencyTools, PhotonMessaging
from settings import get_settings

run = asyncio.run
NUMBER = "+19876543210"
RESPONDER_Q = QUESTIONS[RESPONDER]
CALLER_Q = QUESTIONS[CALLER_TEXT]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Session:
    """Stands in for the voice engine: records what the app tells the model."""

    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(text)

    async def receive_forever(self):
        await asyncio.sleep(3600)

    async def send_audio(self, pcm):
        pass

    async def close(self):
        pass


def bridge_with_session():
    bridge = TwilioMediaBridge(None, "prompt")
    bridge.emergency_context.caller_number = NUMBER
    bridge.gemini_session = Session()
    return bridge, bridge.gemini_session


async def say_yes(bridge, question):
    await bridge._on_turn_complete("", question)
    await bridge._on_turn_complete("Yes please", "Okay.")


# --------------------------------------------------------------------------- #
# 1. Memory consent could never be recognised (digits were stripped)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spoken", [
    "May I remember your support preferences for future calls for up to 30 days?",
    "May I remember your support preferences for future calls for up to thirty days? You can say no.",
    "may i remember your support preferences for future calls for up to 7 days",
])
def test_memory_question_is_recognised_whatever_the_number_looks_like(spoken):
    assert memory_question_asked(spoken)
    assert not memory_question_asked("Tell me about your day.")


def test_memory_consent_pending_is_set_by_the_real_turn_handler():
    bridge = TwilioMediaBridge(None, "prompt")
    bridge.memory_store = type("Store", (), {"retention_days": 30})()
    seen = []

    async def fake_process(answer, pending):
        seen.append((answer, pending))

    bridge._process_memory = fake_process

    async def go():
        await bridge._on_turn_complete("", "May I remember your support preferences for future calls for up to 30 days?")
        assert bridge.memory_consent_pending is True
        await bridge._on_turn_complete("Yes, please.", "Okay.")
        await asyncio.gather(*bridge.memory_tasks)

    run(go())
    assert seen == [("yes please", True)]


def test_memory_is_actually_saved_after_a_yes():
    class Store:
        retention_days = 30

        def __init__(self):
            self.saves = []

        def save(self, caller, memory, consent=False, matched=False):
            self.saves.append((caller, consent, matched))
            return True

    bridge = TwilioMediaBridge(None, "prompt")
    bridge.memory_store = Store()
    bridge.emergency_context.caller_number = NUMBER
    bridge.verification_status = "matched"

    async def go():
        await bridge._on_turn_complete("", "May I remember your support preferences for future calls for up to 30 days?")
        await bridge._on_turn_complete("Yes please", "Okay.")
        await asyncio.gather(*bridge.memory_tasks)

    run(go())
    assert bridge.memory_store.saves == [(NUMBER, True, True)]


def test_a_question_asked_two_turns_ago_does_not_count_for_memory():
    bridge = TwilioMediaBridge(None, "prompt")
    bridge.memory_store = type("Store", (), {"retention_days": 30})()
    run(bridge._on_turn_complete("", "May I remember your support preferences for future calls for up to 30 days?"))
    run(bridge._on_turn_complete("", "Take your time."))
    assert bridge.memory_consent_pending is False


# --------------------------------------------------------------------------- #
# 2. Consent: per action, single use, expiring; a no withdraws it
# --------------------------------------------------------------------------- #
def test_yes_after_the_question_grants_only_that_action():
    tracker = ConsentTracker(clock=Clock())
    tracker.observe_model(f"Okay. {RESPONDER_Q}")
    assert tracker.pending == RESPONDER
    assert tracker.observe_caller("Yes, please.").granted == RESPONDER
    assert tracker.allows(RESPONDER) and not tracker.allows(CALLER_TEXT)


def test_a_yes_with_no_question_before_it_grants_nothing():
    tracker = ConsentTracker(clock=Clock())
    assert tracker.observe_caller("yes").granted is None
    assert not tracker.allows(RESPONDER)


def test_the_question_must_be_in_the_turn_right_before_the_yes():
    tracker = ConsentTracker(clock=Clock())
    tracker.observe_model(RESPONDER_Q)
    tracker.observe_model("Take your time, I am here.")  # the question was not the last thing asked
    assert tracker.observe_caller("yes").granted is None


def test_consent_is_single_use():
    tracker = ConsentTracker(clock=Clock())
    tracker.observe_model(RESPONDER_Q)
    tracker.observe_caller("yes")
    assert tracker.consume(RESPONDER) is True
    assert tracker.consume(RESPONDER) is False


def test_consent_expires():
    clock = Clock()
    tracker = ConsentTracker(ttl_s=60, clock=clock)
    tracker.observe_model(RESPONDER_Q)
    tracker.observe_caller("yes")
    clock.now = 59
    assert tracker.allows(RESPONDER)
    clock.now = 61
    assert not tracker.allows(RESPONDER) and not tracker.consume(RESPONDER)


def test_anything_but_a_clear_yes_after_the_question_is_a_no():
    tracker = ConsentTracker(clock=Clock())
    tracker.observe_model(RESPONDER_Q)
    assert tracker.observe_caller("hmm, maybe").granted is None
    assert not tracker.allows(RESPONDER)


def test_a_no_withdraws_everything_that_was_granted():
    tracker = ConsentTracker(clock=Clock())
    for question in (RESPONDER_Q, CALLER_Q):
        tracker.observe_model(question)
        tracker.observe_caller("yes")
    outcome = tracker.observe_caller("No, stop.")
    assert outcome.said_no and set(outcome.revoked) == {RESPONDER, CALLER_TEXT}
    assert not tracker.allows(RESPONDER) and not tracker.allows(CALLER_TEXT)


@pytest.mark.parametrize("reply", ["no", "No, stop", "please don't", "I do not want that", "nope", "cancel that", "I don't know"])
def test_anything_that_sounds_like_a_no_withdraws_consent(reply):
    tracker = ConsentTracker(clock=Clock())
    tracker.observe_model(RESPONDER_Q)
    tracker.observe_caller("yes")
    assert tracker.allows(RESPONDER)
    assert tracker.observe_caller(reply).said_no
    assert not tracker.allows(RESPONDER)


@pytest.mark.parametrize("reply", ["yes", "Yes, please.", "yes you may", "please do"])
def test_a_clear_yes_still_grants(reply):
    tracker = ConsentTracker(clock=Clock())
    tracker.observe_model(RESPONDER_Q)
    assert tracker.observe_caller(reply).granted == RESPONDER


def test_when_two_questions_are_in_one_turn_the_last_one_counts():
    tracker = ConsentTracker(clock=Clock())
    tracker.observe_model(f"{RESPONDER_Q} Also, {CALLER_Q}")
    assert tracker.pending == CALLER_TEXT


@pytest.mark.parametrize("action,question,kind", [
    ("send_text_message", RESPONDER_Q, RESPONDER),
    ("start_phone_call", RESPONDER_Q, RESPONDER),
    ("request_location", CALLER_Q, CALLER_TEXT),
])
def test_every_action_needs_its_own_yes_and_a_yes_is_used_once(monkeypatch, action, question, kind):
    dispatched = []

    async def fake_run(self, tools):
        dispatched.append(tools)

    monkeypatch.setattr(TwilioMediaBridge, "run_emergency", fake_run)
    bridge, _ = bridge_with_session()
    request = {"action": action, "message": "help"}

    async def go():
        refused = await bridge.relay_to_harness("relay_to_harness", request)
        assert refused["status"] == "consent_required" and refused["ask"] == QUESTIONS[kind]
        await say_yes(bridge, question)
        assert await bridge.relay_to_harness("relay_to_harness", request) == {"status": "pending"}
        await asyncio.gather(*bridge.emergency_tasks)
        again = await bridge.relay_to_harness("relay_to_harness", request)
        assert again["status"] == "consent_required"  # the yes was spent

    run(go())
    assert len(dispatched) == 1 and dispatched[0].action == action
    context = dispatched[0].context
    assert (context.responder_consent, context.caller_text_consent) == (kind == RESPONDER, kind == CALLER_TEXT)


def test_a_yes_to_texting_the_responder_does_not_cover_texting_the_callers_own_phone(monkeypatch):
    monkeypatch.setattr(TwilioMediaBridge, "run_emergency", lambda self, tools: asyncio.sleep(0))
    bridge, _ = bridge_with_session()

    async def go():
        await say_yes(bridge, RESPONDER_Q)
        return await bridge.relay_to_harness("relay_to_harness", {"action": "request_location"})

    assert run(go())["status"] == "consent_required"


def test_an_unused_yes_expires(monkeypatch):
    monkeypatch.setattr(TwilioMediaBridge, "run_emergency", lambda self, tools: asyncio.sleep(0))
    bridge, _ = bridge_with_session()
    clock = Clock()
    bridge.consent = ConsentTracker(ttl_s=30, clock=clock)

    async def go():
        await say_yes(bridge, RESPONDER_Q)
        clock.now = 31
        return await bridge.relay_to_harness("relay_to_harness", {"action": "send_text_message", "message": "help"})

    assert run(go())["status"] == "consent_required"


def test_a_no_cancels_queued_jobs(monkeypatch):
    calls = []

    async def cancel(session_id, responder_only=False):
        calls.append(responder_only)

    monkeypatch.setattr(twilio_stream, "cancel_session_jobs", cancel)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("EMERGENCY_JOB_ENCRYPTION_KEY", "k")
    get_settings.cache_clear()
    try:
        bridge, _ = bridge_with_session()
        run(bridge._on_turn_complete("No thanks", "Okay."))
    finally:
        get_settings.cache_clear()
    assert calls == [False]  # everything queued, not just responder texts


def test_the_system_prompt_names_both_questions():
    prompt = TwilioMediaBridge(None, "prompt").build_system_prompt()
    assert "May I text the emergency responder with what you shared?" in prompt
    assert "May I text your phone to ask where you are?" in prompt
    assert "one yes covers one action only" in prompt


# --------------------------------------------------------------------------- #
# 3. Voice gate: a repeated start event, withheld numbers, and a way to reset
# --------------------------------------------------------------------------- #
def start_event(token, sid="s1"):
    return {"event": "start", "start": {"streamSid": sid, "customParameters": {"caller_token": token}}}


def media_event():
    return {"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}


def bridge_over(events, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    feed = iter(events)

    class WebSocket:
        async def receive_text(self):
            try:
                return json.dumps(next(feed))
            except StopIteration:
                await asyncio.sleep(3600)

    return TwilioMediaBridge(WebSocket(), "prompt")


def test_a_second_start_event_cannot_change_who_the_caller_is(monkeypatch):
    first, second = "+2348011111111", "+2348022222222"
    bridge = bridge_over([start_event(sign_caller(first, "secret"), "first"),
                          start_event(sign_caller(second, "secret"), "second"), media_event()], monkeypatch)

    class Collector:
        def add(self, pcm):
            return True

        def audio_16khz(self):
            return b"x"

    async def check(collector, caller_id):
        bridge._set_identity("new", "enrolled")

    monkeypatch.setattr(twilio_stream, "SpeechCollector", Collector)
    monkeypatch.setattr(bridge, "_check_voice", check)
    run(bridge.verify_call())
    assert bridge.emergency_context.caller_number == first and bridge.stream_sid == "first"


def test_a_start_event_in_the_main_loop_never_reconnects_the_voice_engine(monkeypatch):
    bridge = bridge_over([start_event("anything", "again"), {"event": "stop"}], monkeypatch)
    connects = []

    async def nothing(*a, **k):
        return None

    async def connect():
        connects.append(1)
        bridge.gemini_session = Session()
        return True

    monkeypatch.setattr(bridge, "verify_call", nothing)
    monkeypatch.setattr(bridge, "_setup_support", nothing)
    monkeypatch.setattr(bridge, "_replay_first_words", nothing)
    monkeypatch.setattr(bridge, "_connect_gemini", connect)
    run(asyncio.wait_for(bridge.run(), timeout=5))
    assert connects == [1]  # connected once, after the voice check; the extra start changed nothing


@pytest.mark.parametrize("withheld", ["anonymous", "client:alice", "unknown", "", "+1"])
def test_a_withheld_number_gets_a_signed_numberless_token(monkeypatch, withheld):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    form = {"From": withheld, "CallSid": "CA1"}
    signature = RequestValidator("secret").compute_signature("http://testserver/voice/incoming", form)
    response = TestClient(main.app).post("/voice/incoming", data=form, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200  # used to be a 400: a child with a blocked number was turned away
    token = response.text.split('name="caller_token" value="')[1].split('"')[0]
    assert verify_caller(token, "secret") == ANONYMOUS


def test_every_request_is_signature_checked_even_with_no_caller(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    assert TestClient(main.app).post("/voice/incoming", data={}).status_code == 403


def test_only_the_anonymous_marker_bypasses_the_number_format():
    with pytest.raises(ValueError):
        sign_caller("not-a-number", "secret")
    with pytest.raises(ValueError):
        verify_caller("anonymous", "secret")  # an unsigned marker is not a token


def test_an_anonymous_caller_carries_on_at_once_with_no_number(monkeypatch):
    bridge = bridge_over([start_event(sign_caller(ANONYMOUS, "secret")), media_event()], monkeypatch)
    began = time.monotonic()
    run(bridge.verify_call())
    assert time.monotonic() - began < 2  # does not sit waiting for speech it will never check
    assert bridge.identity_state == "unsure" and bridge.identity_reason == "no_caller_number"
    assert bridge.emergency_context.caller_number == ""


def test_a_recognised_caller_can_have_their_voice_forgotten(monkeypatch):
    deleted = []

    class Store:
        def __init__(self, url):
            pass

        def delete(self, caller):
            deleted.append(caller)
            return True

    monkeypatch.setattr(twilio_stream, "PostgresVoiceStore", Store)
    bridge, _ = bridge_with_session()
    bridge.identity_state = "known"

    async def go():
        await bridge._on_turn_complete("Forget my voice.", "Okay.")
        await asyncio.gather(*bridge.memory_tasks)

    run(go())
    assert deleted == [NUMBER] and bridge.identity_reason == "voice_forgotten"


def test_an_unrecognised_voice_cannot_trigger_a_reset(monkeypatch):
    class Store:
        def __init__(self, url):
            raise AssertionError("must not be used")

    monkeypatch.setattr(twilio_stream, "PostgresVoiceStore", Store)
    bridge, _ = bridge_with_session()
    bridge.identity_state = "unknown"
    run(bridge._on_turn_complete("Forget my voice.", "Okay."))
    assert not bridge.memory_tasks


def test_operator_can_reset_a_voice_enrollment_on_the_internal_app(monkeypatch):
    secret = "s" * 40
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", secret)
    get_settings.cache_clear()
    deleted = []

    class Store:
        def __init__(self, url):
            pass

        def delete(self, caller):
            deleted.append(caller)
            return True

    monkeypatch.setattr("app.internal.PostgresVoiceStore", Store)
    client = TestClient(internal_app)
    try:
        url = "/internal/voice-enrollment"
        assert client.request("DELETE", url, json={"caller_id": NUMBER}).status_code == 403
        key = {"X-Emergency-Worker-Key": secret}
        assert client.request("DELETE", url, json={"caller_id": "nope"}, headers=key).status_code == 400
        assert client.request("DELETE", url, json={"caller_id": NUMBER}, headers=key).json() == {"status": "deleted"}
        assert deleted == [NUMBER]
        assert TestClient(main.app).request("DELETE", url, json={"caller_id": NUMBER}, headers=key).status_code == 404
    finally:
        get_settings.cache_clear()


def test_when_the_voice_engine_cannot_start_the_caller_hears_a_message_not_silence(monkeypatch):
    sent = []

    class FakeCalls:
        def __init__(self, sid):
            self.sid = sid

        def update(self, twiml=None):
            sent.append((self.sid, twiml))

    class FakeClient:
        def __init__(self, account, token):
            assert (account, token) == ("AC1", "tok")

        def calls(self, sid):
            return FakeCalls(sid)

    monkeypatch.setattr("twilio.rest.Client", FakeClient)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    get_settings.cache_clear()
    try:
        bridge = TwilioMediaBridge(None, "prompt")
        bridge.handle_start({"callSid": "CA123", "customParameters": {"sabi_country": "NG", "sabi_region": "Nigeria"}})
        run(bridge._tell_caller_we_are_down())
        no_call = TwilioMediaBridge(None, "prompt")
        run(no_call._tell_caller_we_are_down())  # no call sid: nothing to redirect, and no error
    finally:
        get_settings.cache_clear()
    assert len(sent) == 1 and sent[0][0] == "CA123"
    assert "call 1 1 2 now" in sent[0][1] and "<Hangup/>" in sent[0][1]


def test_run_plays_the_fallback_message_when_the_engine_fails_to_connect(monkeypatch):
    bridge = bridge_over([], monkeypatch)
    told = []

    async def nothing(*a, **k):
        return None

    async def fail():
        return False

    async def tell():
        told.append(1)

    class WebSocket:
        closed = False

        async def close(self):
            WebSocket.closed = True

    bridge.ws = WebSocket()
    monkeypatch.setattr(bridge, "verify_call", nothing)
    monkeypatch.setattr(bridge, "_setup_support", nothing)
    monkeypatch.setattr(bridge, "_connect_gemini", fail)
    monkeypatch.setattr(bridge, "_tell_caller_we_are_down", tell)
    run(bridge.run())
    assert told == [1] and WebSocket.closed


# --------------------------------------------------------------------------- #
# 4. The internal routes are on their own listener, not the public port
# --------------------------------------------------------------------------- #
def test_internal_listener_is_a_separate_port_that_the_public_app_does_not_serve(monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    secret = "s" * 40
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", secret)
    monkeypatch.setenv("INTERNAL_HOST", "127.0.0.1")
    monkeypatch.setenv("INTERNAL_PORT", str(port))
    monkeypatch.setattr(main, "ENABLE_AUDIOSOCKET", False)
    get_settings.cache_clear()
    url = f"http://127.0.0.1:{port}/internal/emergency-job"
    body = {"token": "x" * 43}
    try:
        with TestClient(main.app) as public:
            response = None
            for _ in range(60):
                try:
                    response = httpx.post(url, json=body, timeout=2)
                    break
                except httpx.ConnectError:
                    time.sleep(0.1)
            assert response is not None and response.status_code == 403  # reachable, but refuses without the key
            assert public.post("/internal/emergency-job", json=body, headers={"X-Emergency-Worker-Key": secret}).status_code == 404
        with pytest.raises(httpx.ConnectError):
            httpx.post(url, json=body, timeout=2)  # and it shuts down with the app
    finally:
        get_settings.cache_clear()


def test_internal_defaults_are_loopback_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("INTERNAL_HOST", "INTERNAL_PORT", "EMERGENCY_DISPATCH", "EMERGENCY_CALLBACK_URL"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()
    assert settings.internal_host == "127.0.0.1" and settings.internal_port == 8001
    assert settings.emergency_dispatch == "auto"
    assert settings.emergency_callback_url == "http://127.0.0.1:8001/internal/emergency-job"


# --------------------------------------------------------------------------- #
# 5. The harness model is only a courier: an approved text is sent even if it is down
# --------------------------------------------------------------------------- #
@pytest.fixture
def photon(monkeypatch):
    sent = []

    async def send(self, recipient, text):
        sent.append((recipient, text))
        return {"status": "submitted", "message_id": "m1"}

    monkeypatch.setattr(PhotonMessaging, "send", send)
    monkeypatch.setenv("EMERGENCY_RESPONDER_NUMBER", "+15550001111")
    monkeypatch.delenv("EMERGENCY_DISPATCH", raising=False)
    get_settings.cache_clear()
    yield sent
    get_settings.cache_clear()


def responder_text():
    return EmergencyTools(EmergencyCallContext(NUMBER, responder_consent=True), "send_text_message", "Help")


def fake_job_store(monkeypatch, *, token="x" * 43):
    state = {"finished": {}, "executed": [], "cancelled": []}

    async def create(tools, session_id):
        state["tools"] = tools
        return token

    async def execute(t):
        state["executed"].append(t)
        await state["tools"].run(state["tools"].action)  # what the real execute_job does
        state["finished"][t] = state["tools"].result
        return {"status": "finished"}

    async def poll(t):
        return state["finished"].get(t)

    async def cancel(t):
        state["cancelled"].append(t)

    monkeypatch.setattr(twilio_stream, "create_job", create)
    monkeypatch.setattr(twilio_stream, "execute_job", execute)
    monkeypatch.setattr(twilio_stream, "get_job_result", poll)
    monkeypatch.setattr(twilio_stream, "cancel_job", cancel)
    return state


def test_direct_mode_never_uses_the_job_store_or_the_harness(monkeypatch, photon):
    monkeypatch.setenv("EMERGENCY_DISPATCH", "direct")
    get_settings.cache_clear()

    async def boom(*a, **k):
        raise AssertionError("must not be used in direct mode")

    monkeypatch.setattr(twilio_stream, "create_job", boom)
    bridge, session = bridge_with_session()
    bridge.harness_agent = type("Agent", (), {"ainvoke": boom})()
    run(bridge.run_emergency(responder_text()))
    assert photon == [("+15550001111", "Help")]
    assert '"status": "submitted"' in session.messages[0]


def test_job_store_down_falls_back_to_a_direct_send(monkeypatch, photon):
    async def down(tools, session_id):
        raise ConnectionError("postgres is down")

    monkeypatch.setattr(twilio_stream, "create_job", down)
    bridge, session = bridge_with_session()
    bridge.harness_agent = object()
    run(bridge.run_emergency(responder_text()))
    assert len(photon) == 1 and '"status": "submitted"' in session.messages[0]


def test_harness_model_outage_does_not_stop_an_approved_text(monkeypatch, photon):
    state = fake_job_store(monkeypatch)

    class Agent:
        async def ainvoke(self, payload, config=None):
            raise RuntimeError("model is down")

    bridge, session = bridge_with_session()
    bridge.harness_agent = Agent()
    began = time.monotonic()
    run(bridge.run_emergency(responder_text()))
    assert time.monotonic() - began < 5  # straight to the fallback, no 20 to 90 second wait
    assert state["executed"] == ["x" * 43] and len(photon) == 1
    assert '"status": "submitted"' in session.messages[0] and state["cancelled"] == ["x" * 43]


def test_agent_server_that_never_answers_is_covered_after_the_wait(monkeypatch, photon):
    state = fake_job_store(monkeypatch)
    monkeypatch.setattr(twilio_stream, "AGENT_RESULT_WAIT_S", 0.3)

    class Agent:
        async def ainvoke(self, payload, config=None):
            return None  # the model "handed it over" but nothing ever comes back

    bridge, session = bridge_with_session()
    bridge.harness_agent = Agent()
    run(bridge.run_emergency(responder_text()))
    assert state["executed"] == ["x" * 43] and len(photon) == 1


def test_a_stuck_earlier_job_cannot_hold_this_one_up(monkeypatch, photon):
    state = fake_job_store(monkeypatch)
    monkeypatch.setattr(twilio_stream, "AGENT_DISPATCH_TIMEOUT_S", 0.3)

    class Agent:
        async def ainvoke(self, payload, config=None):
            raise AssertionError("never reached: the lock is held")

    async def go():
        bridge, session = bridge_with_session()
        bridge.harness_agent = Agent()
        await bridge.harness_lock.acquire()  # an earlier job is stuck holding the harness
        began = time.monotonic()
        await bridge.run_emergency(responder_text())
        return time.monotonic() - began, session

    elapsed, session = run(go())
    assert elapsed < 5 and len(photon) == 1 and state["executed"]


def test_agent_server_success_is_not_sent_twice(monkeypatch, photon):
    state = fake_job_store(monkeypatch)

    class Agent:
        async def ainvoke(self, payload, config=None):
            # the Agent Server path: the callback claims and runs the job
            await twilio_stream.execute_job("x" * 43)

    bridge, session = bridge_with_session()
    bridge.harness_agent = Agent()
    run(bridge.run_emergency(responder_text()))
    assert len(photon) == 1 and state["executed"] == ["x" * 43]  # the app did not run it a second time


def test_no_harness_agent_at_all_still_sends(monkeypatch, photon):
    def broken(tool):
        raise RuntimeError("no model key")

    monkeypatch.setattr(twilio_stream, "create_harness", broken)
    bridge, session = bridge_with_session()
    bridge.harness_agent = None
    run(bridge.run_emergency(responder_text()))  # used to be "harness_not_ready", and nothing was sent
    assert len(photon) == 1 and '"status": "submitted"' in session.messages[0]


def test_a_failing_tool_is_reported_not_swallowed(monkeypatch):
    async def explode(self, recipient, text):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(PhotonMessaging, "send", explode)
    monkeypatch.setenv("EMERGENCY_RESPONDER_NUMBER", "+15550001111")
    monkeypatch.setenv("EMERGENCY_DISPATCH", "direct")
    get_settings.cache_clear()
    try:
        bridge, session = bridge_with_session()
        run(bridge.run_emergency(responder_text()))
    finally:
        get_settings.cache_clear()
    assert '"reason": "tool_failed"' in session.messages[0]


def test_relay_never_reports_harness_not_ready(monkeypatch, photon):
    monkeypatch.setenv("EMERGENCY_DISPATCH", "direct")
    get_settings.cache_clear()
    bridge, session = bridge_with_session()

    async def go():
        await say_yes(bridge, RESPONDER_Q)
        result = await bridge.relay_to_harness("relay_to_harness", {"action": "send_text_message", "message": "Help"})
        await asyncio.gather(*bridge.emergency_tasks)
        return result

    assert run(go()) == {"status": "pending"} and len(photon) == 1


# --------------------------------------------------------------------------- #
# 6. Compose: nothing optional is needed to start the app
# --------------------------------------------------------------------------- #
def test_compose_starts_the_app_alone_and_keeps_the_internal_port_private():
    compose = yaml.safe_load((Path(__file__).parent.parent / "docker-compose.yml").read_text())
    services = compose["services"]
    for name in ("emergency-migrate", "agent-server", "agent-redis", "support-init", "postgres"):
        assert services[name].get("profiles"), f"{name} must be behind a profile"
    dependencies = services["sabi-safe"]["depends_on"]
    assert dependencies and all(isinstance(v, dict) and v.get("required") is False for v in dependencies.values())
    assert services["sabi-safe"].get("profiles") is None  # the app is always started
    assert not any("8001" in str(port) for port in services["sabi-safe"]["ports"])  # internal port never published
    assert services["agent-server"]["environment"]["EMERGENCY_CALLBACK_URL"].endswith("sabi-safe:8001/internal/emergency-job")
    environment = services["sabi-safe"]["environment"]
    assert "EMERGENCY_DISPATCH=${EMERGENCY_DISPATCH:-direct}" in environment
    assert "INTERNAL_PORT=8001" in environment


def test_env_example_documents_the_new_settings():
    text = (Path(__file__).parent.parent / ".env.example").read_text()
    for name in ("EMERGENCY_DISPATCH", "INTERNAL_HOST", "INTERNAL_PORT"):
        assert f"\n{name}=" in text, f"{name} missing from .env.example"
    assert "8001/internal/emergency-job" in text
