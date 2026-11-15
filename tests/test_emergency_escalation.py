"""The emergency escalation plan: ladder, event channel, voice notes, planning agent and bridge delivery."""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.voice.twilio_stream import VOICE_NOTE_TEXT, TwilioMediaBridge
from harness import emergency_plan as plan
from harness.emergency_jobs import (add_voice_note, create_job, finish_job, get_job_result, job_progress, poll_job_events,
                                    post_job_event, run_job_action, token_hash)
from harness.emergency_plan import next_rung, run_floor
from harness.emergency_subagent import graph
from harness.tools import EmergencyCallContext, EmergencyTools
from harness.tools.emergency_agent_tools import build_plan_tools
from settings import get_settings


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- the ladder as plain code

@pytest.mark.parametrize("requested,statuses,expected", [
    ("send_text_message", {"send_text_message": "submitted"}, None),
    ("send_text_message", {"send_text_message": "unavailable"}, "start_phone_call"),
    ("start_phone_call", {"start_phone_call": "unknown"}, "send_text_message"),  # unknown may have been sent: never repeat it
    ("send_text_message", {"send_text_message": "unavailable", "start_phone_call": "unavailable"}, "text_backup_responder"),
    ("send_text_message", {"send_text_message": "unavailable", "start_phone_call": "unavailable",
                           "text_backup_responder": "unavailable"}, None),
    ("send_text_message", {"send_text_message": "unavailable", "start_phone_call": "submitted"}, None),
    ("send_text_message", {"send_text_message": "consent_required"}, None),  # never pushes past a missing consent
    ("request_location", {"request_location": "unavailable"}, None),  # a location request has nothing to escalate
])
def test_next_rung(requested, statuses, expected):
    assert next_rung(requested, statuses) == expected


def _floor(requested, statuses, outcomes):
    calls, notes = [], []

    async def act(action):
        calls.append(action)
        return outcomes.get(action, {"status": "submitted"})

    async def note(name):
        notes.append(name)
        return {"status": "ok"}

    return run(run_floor(requested, statuses, act, note)), calls, notes


def test_floor_tries_the_other_channel_then_the_backup_and_tells_the_voice_agent_when_all_fail():
    failed = {"status": "unavailable", "reason": "provider_error"}
    final, calls, notes = _floor("send_text_message", {"send_text_message": "unavailable"},
                                 {"start_phone_call": failed, "text_backup_responder": failed})
    assert calls == ["start_phone_call", "text_backup_responder"]
    assert notes == ["responder_unreachable", "call_emergency_number"]
    assert final["text_backup_responder"] == "unavailable"


def test_floor_stops_at_the_first_submitted_action_and_says_nothing():
    final, calls, notes = _floor("send_text_message", {"send_text_message": "unavailable"}, {})
    assert calls == ["start_phone_call"] and notes == [] and final["start_phone_call"] == "submitted"


def test_floor_goes_quiet_when_the_job_closed_or_consent_is_missing():
    gone = {"status": "unavailable", "reason": plan.JOB_CLOSED}
    _, calls, notes = _floor("send_text_message", {"send_text_message": "unavailable"}, {"start_phone_call": gone})
    assert calls == ["start_phone_call"] and notes == []  # the call ended: stop everything, tell no one
    _, calls, notes = _floor("send_text_message", {"send_text_message": "consent_required"}, {})
    assert calls == [] and notes == []


# ---------------------------------------------------------------- the planning agent's tools

def _plan_tools(events=None, state=None):
    log = {"actions": [], "notes": []}
    queue = list(events or [])

    async def act(action):
        log["actions"].append(action)
        return {"status": "submitted"}

    async def poll():
        if state is not None:
            return state
        heard, queue[:] = list(queue), []
        return {"status": "ok", "events": heard, "results": {}, "elapsed_seconds": 5, "window_remaining_seconds": 595,
                "check_in_due": False, "notes_sent": []}

    async def note(name):
        log["notes"].append(name)
        return {"status": "ok", "note": name}

    return {tool.name: tool for tool in build_plan_tools(act, poll, note)}, log


def test_plan_tools_are_the_job_tools_plus_backup_wait_and_note():
    tools, _ = _plan_tools()
    assert set(tools) == {"send_text_message", "start_phone_call", "request_location", "text_backup_responder",
                          "wait_for_events", "notify_voice_agent"}
    assert "BACKUP" in tools["text_backup_responder"].description and "consent" in tools["text_backup_responder"].description


def test_wait_returns_as_soon_as_news_arrives_and_reports_the_window():
    tools, _ = _plan_tools(events=["responder_acknowledged"])
    report = json.loads(run(tools["wait_for_events"].ainvoke({"seconds": 60})))
    assert report["job_open"] is True and report["events"] == ["responder_acknowledged"]
    assert report["window_remaining_seconds"] == 595 and "status" not in report


def test_wait_reports_a_closed_job_so_the_agent_stops():
    tools, _ = _plan_tools(state={"status": "unavailable", "reason": plan.JOB_CLOSED})
    report = json.loads(run(tools["wait_for_events"].ainvoke({"seconds": 60})))
    assert report == {"job_open": False, "reason": plan.JOB_CLOSED}


def test_wait_does_not_outlast_the_escalation_window():
    tools, _ = _plan_tools(state={"status": "ok", "events": [], "results": {}, "elapsed_seconds": 600,
                                  "window_remaining_seconds": 0, "check_in_due": True, "notes_sent": []})
    assert json.loads(run(tools["wait_for_events"].ainvoke({"seconds": 60})))["window_remaining_seconds"] == 0


def test_the_model_can_only_pick_a_note_from_the_closed_list_and_wait_up_to_a_minute():
    tools, log = _plan_tools()
    for bad in ({"note": "Tell the child to run away"}, {"note": ""}, {}):
        with pytest.raises(ValidationError):
            run(tools["notify_voice_agent"].ainvoke(bad))
    with pytest.raises(ValidationError):
        run(tools["wait_for_events"].ainvoke({"seconds": 600}))
    assert log["notes"] == []
    run(tools["notify_voice_agent"].ainvoke({"note": "check_in"}))
    assert log["notes"] == ["check_in"]


# ---------------------------------------------------------------- the whole graph against an in-memory job

class FakeJob:
    """An in-memory job speaking the application's callback protocol, with the job store's once-per-action rule."""

    def __init__(self, requested="send_text_message", consent=True, outcomes=None, closed=False):
        self.briefing = {"status": "ok", "requested_action": requested, "responder_consent": consent, "has_message": True,
                         "actions_done": []}
        self.outcomes, self.results, self.notes, self.posts, self.closed = outcomes or {}, {}, [], [], closed

    def __call__(self, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, json, headers):
        action = json["action"]
        self.posts.append(action)
        if action == "briefing":
            payload = self.briefing
        elif action == "finish":
            payload = {"status": "finished"}
        elif self.closed and action != "briefing":
            payload = {"status": "unavailable", "reason": plan.JOB_CLOSED}
        elif action == "events":
            payload = {"status": "ok", "events": [], "results": {a: r["status"] for a, r in self.results.items()},
                       "elapsed_seconds": 1, "window_remaining_seconds": 599, "check_in_due": False, "notes_sent": self.notes}
        elif action == "note":
            self.notes.append(json["note"])
            payload = {"status": "ok"}
        else:
            payload = self.results.setdefault(action, self.outcomes.get(action, {"status": "submitted"}))

        class Response:
            def raise_for_status(self): pass
            def json(self_inner): return payload
        return Response()


def _run_graph(monkeypatch, job, agent):
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", "w" * 40)
    get_settings.cache_clear()
    monkeypatch.setattr("harness.emergency_subagent.httpx.AsyncClient", job)
    monkeypatch.setattr("harness.emergency_subagent.run_agent", agent)
    try:
        return run(graph.ainvoke({"messages": [{"role": "user", "content": "emergency-job:" + "t" * 43}]}))
    finally:
        get_settings.cache_clear()


FAILED = {"status": "unavailable", "reason": "provider_error"}


def test_the_first_action_is_done_before_any_model_runs(monkeypatch):
    job, order = FakeJob(), []

    async def agent(tools, briefing, statuses):
        order.append(("agent", dict(job.results)))

    _run_graph(monkeypatch, job, agent)
    assert order == [("agent", {"send_text_message": {"status": "submitted"}})]


def test_when_the_model_fails_the_floor_still_walks_the_ladder(monkeypatch):
    job = FakeJob(outcomes={"send_text_message": FAILED, "start_phone_call": FAILED, "text_backup_responder": FAILED})

    async def broken(tools, briefing, statuses):
        raise RuntimeError("model unavailable")

    result = _run_graph(monkeypatch, job, broken)
    assert job.posts == ["briefing", "send_text_message", "events", "start_phone_call", "text_backup_responder",
                         "note", "note", "finish"]
    assert job.notes == ["responder_unreachable", "call_emergency_number"]
    assert "Emergency job: finished" in json.dumps(result)


def test_a_second_channel_that_works_ends_the_ladder_without_notes(monkeypatch):
    job = FakeJob(outcomes={"send_text_message": FAILED})

    async def idle(tools, briefing, statuses):
        return None

    _run_graph(monkeypatch, job, idle)
    assert job.posts == ["briefing", "send_text_message", "events", "start_phone_call", "finish"] and job.notes == []


def test_a_submitted_first_action_needs_no_floor(monkeypatch):
    job = FakeJob()

    async def idle(tools, briefing, statuses):
        return None

    _run_graph(monkeypatch, job, idle)
    assert job.posts == ["briefing", "send_text_message", "events", "finish"]


def test_nothing_is_escalated_without_consent_or_for_a_location_request(monkeypatch):
    ran = []

    async def agent(tools, briefing, statuses):
        ran.append(1)

    no_consent = FakeJob(consent=False, outcomes={"send_text_message": {"status": "consent_required"}})
    _run_graph(monkeypatch, no_consent, agent)
    assert no_consent.posts == ["briefing", "send_text_message", "finish"]
    location = FakeJob(requested="request_location")
    _run_graph(monkeypatch, location, agent)
    assert location.posts == ["briefing", "request_location", "finish"] and not ran  # no model call for either


def test_a_job_closed_mid_escalation_is_not_pushed_further(monkeypatch):
    job = FakeJob(outcomes={"send_text_message": FAILED})

    async def hang_up(tools, briefing, statuses):
        job.closed = True  # the call ended while the agent was working

    _run_graph(monkeypatch, job, hang_up)
    assert job.posts == ["briefing", "send_text_message", "events", "finish"] and job.notes == []


def test_the_graph_is_visible_as_three_steps():
    assert {"first_action", "plan_escalation", "finish_job"} <= set(graph.get_graph().nodes)


# ---------------------------------------------------------------- the real Deep Agent, with a scripted model

def test_the_planning_agent_is_a_deep_agent_with_a_plan_and_no_file_access(monkeypatch):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from harness import emergency_subagent

    def call(name, args, id_):
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id_}])

    script = iter([
        call("write_todos", {"todos": [{"content": "Check for an acknowledgement", "status": "in_progress"}]}, "1"),
        call("write_file", {"file_path": "/notes.txt", "content": "x"}, "2"),
        call("wait_for_events", {"seconds": 5}, "3"),
        call("notify_voice_agent", {"note": "check_in"}, "4"),
        AIMessage(content="The responder acknowledged."),
    ])
    bound = {}

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            bound["names"] = [getattr(tool, "name", None) or tool.get("name") for tool in tools]
            return self

    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", lambda **kw: Model(messages=script))
    tools, log = _plan_tools(events=["responder_acknowledged"])
    run(emergency_subagent.run_agent(list(tools.values()), {"requested_action": "start_phone_call", "responder_consent": True,
                                                            "has_message": True}, {"start_phone_call": "submitted"}))
    assert "write_todos" in bound["names"] and "wait_for_events" in bound["names"]  # a visible plan, and the job's tools
    assert log["notes"] == ["check_in"] and next(script, None) is None  # the whole script ran, including the final answer


def test_file_tools_are_denied_to_the_planning_agent(monkeypatch):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from harness import emergency_subagent

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    script = iter([AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"file_path": "/x.txt", "content": "x"}, "id": "1"}]),
                   AIMessage(content="done")])
    seen = []
    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", lambda **kw: Model(messages=script))

    import deepagents

    real = deepagents.create_deep_agent

    def spy(**kwargs):
        agent = real(**kwargs)
        original_ainvoke = agent.ainvoke

        async def ainvoke(*a, **k):
            result = await original_ainvoke(*a, **k)
            seen.extend(str(m.content) for m in result["messages"] if getattr(m, "type", "") == "tool")
            return result
        agent.ainvoke = ainvoke
        return agent

    monkeypatch.setattr("deepagents.create_deep_agent", spy)
    run(emergency_subagent.run_agent([], {"requested_action": "send_text_message", "responder_consent": True, "has_message": True},
                                     {"send_text_message": "submitted"}))
    assert seen and "permission" in seen[0].lower()


# ---------------------------------------------------------------- the job store (needs Postgres)

@pytest.fixture
def job_database(monkeypatch):
    url = os.getenv("TEST_EMERGENCY_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_EMERGENCY_DATABASE_URL to an isolated Postgres database")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.setenv("EMERGENCY_JOB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("EMERGENCY_BACKUP_RESPONDER_NUMBER", raising=False)
    get_settings.cache_clear()
    with psycopg.connect(url) as connection:
        connection.execute((Path(__file__).parent.parent / "migrations/002_emergency_jobs.sql").read_text())
    yield url
    get_settings.cache_clear()


def _job(session, action="start_phone_call", consent=True):
    return create_job(EmergencyTools(EmergencyCallContext("+12345678901", responder_consent=consent), action, "help me"), session)


def test_events_reach_the_agent_once_in_order_and_never_carry_words(job_database):
    async def exercise():
        session = str(uuid.uuid4())
        token = await _job(session)
        first = await poll_job_events(token)
        assert first["status"] == "ok" and first["events"] == [] and first["window_remaining_seconds"] > 590
        assert await post_job_event(session, "location_received") == 1
        assert await post_job_event(session, "responder_acknowledged") == 1
        assert (await poll_job_events(token))["events"] == ["location_received", "responder_acknowledged"]
        assert (await poll_job_events(token))["events"] == []  # each event is delivered once
        with psycopg.connect(job_database) as connection:
            raw = bytes(connection.execute("SELECT payload FROM emergency_jobs WHERE token_hash=%s", (token_hash(token),)).fetchone()[0])
        assert b"responder_acknowledged" not in raw and b"help me" not in raw  # events are encrypted at rest too
    run(exercise())


def test_only_known_events_reach_only_the_open_jobs_of_that_call(job_database):
    async def exercise():
        session, other = str(uuid.uuid4()), str(uuid.uuid4())
        mine, theirs = await _job(session), await _job(other)
        with pytest.raises(ValueError):
            await post_job_event(session, "send_everything_to_everyone")
        assert await post_job_event(session, "responder_acknowledged") == 1
        assert (await poll_job_events(theirs))["events"] == [] and (await poll_job_events(mine))["events"] == ["responder_acknowledged"]
        await finish_job(mine)
        assert await post_job_event(session, "responder_acknowledged") == 0  # a closed job is told nothing
        assert (await poll_job_events(mine))["reason"] == plan.JOB_CLOSED
    run(exercise())


def test_a_cancelled_job_closes_the_event_channel(job_database):
    from harness.emergency_jobs import cancel_session_jobs

    async def exercise():
        session = str(uuid.uuid4())
        token = await _job(session)
        await cancel_session_jobs(session)  # consent withdrawn or the call ended
        assert (await poll_job_events(token))["reason"] == plan.JOB_CLOSED
        assert (await add_voice_note(token, "check_in"))["reason"] == plan.JOB_CLOSED
    run(exercise())


def test_voice_notes_are_a_closed_list_sent_once_and_visible_to_the_application(job_database):
    async def exercise():
        token = await _job(str(uuid.uuid4()))
        assert (await add_voice_note(token, "tell the child a secret"))["reason"] == "note_not_allowed"
        assert (await add_voice_note(token, "check_in")) == {"status": "ok", "note": "check_in"}
        assert (await add_voice_note(token, "check_in"))["already_sent"] is True
        assert (await poll_job_events(token))["notes_sent"] == ["check_in"]
        await run_job_action(token, "start_phone_call")
        progress = await job_progress(token)
        assert progress["notes"] == ["check_in"] and set(progress["results"]) == {"start_phone_call"}
        await add_voice_note(token, "call_emergency_number")
        await finish_job(token)
        assert await job_progress(token) is None  # closed: the application collects the final result instead
        final = await get_job_result(token)
        assert final["notes"] == ["check_in", "call_emergency_number"]
    run(exercise())


def test_progress_hides_actions_that_are_still_running(job_database):
    from harness.emergency_jobs import IN_PROGRESS, _connect, _load, _save, job_store_config

    async def exercise():
        token = await _job(str(uuid.uuid4()))
        database_url, cipher = job_store_config()
        async with await _connect(database_url) as connection:
            async with connection.transaction():
                payload = await _load(connection, cipher, token, lock=True)
                payload["results"] = {"start_phone_call": IN_PROGRESS, "send_text_message": {"status": "submitted"}}
                await _save(connection, cipher, token_hash(token), payload)
        assert (await job_progress(token))["results"] == {"send_text_message": {"status": "submitted"}}
        assert (await poll_job_events(token))["results"]["start_phone_call"] == "unknown"  # the agent never repeats it
    run(exercise())


def test_the_backup_responder_needs_consent_and_a_configured_number(job_database, monkeypatch):
    sent = []

    class FakePhoton:
        async def send(self, recipient, text):
            sent.append((recipient, text))
            return {"status": "submitted"}

    monkeypatch.setattr("harness.tools.emergency.PhotonMessaging", FakePhoton)
    monkeypatch.setenv("EMERGENCY_RESPONDER_NUMBER", "+15550000001")

    async def exercise():
        refused = await _job(str(uuid.uuid4()), consent=False)
        assert await run_job_action(refused, "text_backup_responder") == {"status": "consent_required"}
        unconfigured = await _job(str(uuid.uuid4()))
        assert await run_job_action(unconfigured, "text_backup_responder") == {"status": "unavailable", "reason": "backup_not_configured"}
        assert sent == []  # nothing went to anyone, and the primary responder was not used as a stand-in
        monkeypatch.setenv("EMERGENCY_BACKUP_RESPONDER_NUMBER", "+15550000002")
        get_settings.cache_clear()
        configured = await _job(str(uuid.uuid4()))
        assert await run_job_action(configured, "text_backup_responder") == {"status": "submitted"}
        assert await run_job_action(configured, "text_backup_responder") == {"status": "submitted"}
        assert sent == [("+15550000002", "help me")]  # the backup number, the approved message, and only once
    run(exercise())


def test_a_job_lives_as_long_as_its_plan_window_and_no_longer(job_database):
    async def exercise():
        token = await _job(str(uuid.uuid4()))
        with psycopg.connect(job_database) as connection:
            seconds = connection.execute("SELECT extract(epoch FROM expires_at - now()) FROM emergency_jobs WHERE token_hash=%s",
                                         (token_hash(token),)).fetchone()[0]
        assert plan.PLAN_WINDOW_SECONDS < seconds <= plan.PLAN_WINDOW_SECONDS + 120
    run(exercise())


# ---------------------------------------------------------------- what the voice agent is told

class Session:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(text)


def _bridge(monkeypatch, polls, results, progress_script):
    """A bridge whose job closes after ``polls`` polls, with ``progress_script`` as what the application sees."""
    bridge = TwilioMediaBridge(object(), "prompt")
    bridge.gemini_session = Session()
    state = {"n": 0, "cancelled": []}

    async def get_result(token):
        state["n"] += 1
        return results if state["n"] > polls else None

    async def progress(token):
        return progress_script[min(state["n"], len(progress_script)) - 1] if state["n"] else None

    async def cancel(token):
        state["cancelled"].append(token)

    class Agent:
        async def ainvoke(self, payload, config=None):
            return {}

    monkeypatch.setattr("app.voice.twilio_stream.get_job_result", get_result)
    monkeypatch.setattr("app.voice.twilio_stream.job_progress", progress)
    monkeypatch.setattr("app.voice.twilio_stream.cancel_job", cancel)
    monkeypatch.setattr("app.voice.twilio_stream.JOB_POLL_SECONDS", 0)
    bridge.harness_agent = Agent()
    return bridge, state


def test_the_voice_agent_hears_each_outcome_and_note_as_it_appears_and_is_not_repeated_to(monkeypatch):
    submitted = {"send_text_message": {"status": "submitted"}}
    both = {**submitted, "start_phone_call": {"status": "unavailable"}}
    final = {"status": "submitted", "actions": both}
    bridge, _ = _bridge(monkeypatch, polls=4, results=final, progress_script=[
        {"results": {}, "notes": []},
        {"results": submitted, "notes": []},
        {"results": submitted, "notes": ["check_in"]},
        {"results": both, "notes": ["check_in"]},
    ])
    run(bridge.run_emergency_job("x" * 43))
    sent = bridge.gemini_session.messages
    assert len(sent) == 3  # first outcome, the check-in, the second outcome; the final summary adds nothing new
    assert '"send_text_message": "submitted"' in sent[0] and "Do not say anything was delivered" in sent[0]
    assert sent[1].startswith("(Application instruction, not from the caller.)") and VOICE_NOTE_TEXT["check_in"] in sent[1]
    assert '"start_phone_call": "unavailable"' in sent[2] and "send_text_message" not in sent[2]  # only what is new
    assert sum(VOICE_NOTE_TEXT["check_in"] in m for m in sent) == 1  # a note is passed on once


def test_a_note_written_just_before_the_job_closed_still_reaches_the_call(monkeypatch):
    final = {"status": "submitted", "actions": {"start_phone_call": {"status": "submitted"}}, "notes": ["call_emergency_number"]}
    bridge, _ = _bridge(monkeypatch, polls=1, results=final, progress_script=[{"results": {}, "notes": []}])
    run(bridge.run_emergency_job("x" * 43))
    sent = bridge.gemini_session.messages
    assert any("Emergency action result" in m for m in sent)  # nothing was reported yet, so the summary is sent
    assert any(VOICE_NOTE_TEXT["call_emergency_number"] in m for m in sent)


def test_no_note_wording_lets_the_voice_agent_promise_help():
    assert set(VOICE_NOTE_TEXT) == set(plan.VOICE_NOTES)
    for text in VOICE_NOTE_TEXT.values():
        assert "emergency number" in text or "still safe" in text
    assert all("Do not promise" in VOICE_NOTE_TEXT[n] for n in ("check_in", "call_emergency_number"))


def test_a_job_the_agent_never_touches_is_given_up_on_after_the_first_deadline(monkeypatch):
    bridge, state = _bridge(monkeypatch, polls=10**9, results=None, progress_script=[{"results": {}, "notes": []}])
    monkeypatch.setattr("app.voice.twilio_stream.FIRST_RESULT_SECONDS", 0.05)
    run(bridge.run_emergency_job("x" * 43))
    assert state["cancelled"] == ["x" * 43]  # the capability is revoked so a late agent cannot act
    sent = bridge.gemini_session.messages
    assert len(sent) == 1 and "Nothing was sent" in sent[0]


def test_once_the_agent_has_acted_the_job_may_run_for_the_whole_window(monkeypatch):
    submitted = {"results": {"send_text_message": {"status": "submitted"}}, "notes": []}
    bridge, state = _bridge(monkeypatch, polls=10**9, results=None, progress_script=[submitted])
    monkeypatch.setattr("app.voice.twilio_stream.FIRST_RESULT_SECONDS", 0.05)
    monkeypatch.setattr(plan, "PLAN_WINDOW_SECONDS", 0.3)
    started = time.monotonic()
    run(bridge.run_emergency_job("x" * 43))
    assert time.monotonic() - started >= 0.3  # not cut off at the 0.05s first-action deadline
    assert state["cancelled"]


def test_a_failing_progress_look_never_ends_the_wait(monkeypatch):
    final = {"status": "submitted", "actions": {"start_phone_call": {"status": "submitted"}}}
    bridge, _ = _bridge(monkeypatch, polls=3, results=final, progress_script=[])

    async def broken(token):
        raise ConnectionError("database down")

    monkeypatch.setattr("app.voice.twilio_stream.job_progress", broken)
    run(bridge.run_emergency_job("x" * 43))
    assert any("Emergency action result" in m for m in bridge.gemini_session.messages)


# ---------------------------------------------------------------- end to end: real graph, real callback, real job store

PRIMARY, BACKUP = "+15550000001", "+15550000002"


@pytest.fixture
def end_to_end(job_database, monkeypatch):
    """The real graph talks to the real FastAPI callback (over ASGI) and Postgres; only providers and the model are fake."""
    import httpx

    from app.main import app

    real_client = httpx.AsyncClient
    monkeypatch.setenv("EMERGENCY_CALLBACK_URL", "http://app/internal/emergency-job")
    monkeypatch.setenv("EMERGENCY_RESPONDER_NUMBER", PRIMARY)
    monkeypatch.setenv("EMERGENCY_BACKUP_RESPONDER_NUMBER", BACKUP)
    get_settings.cache_clear()
    monkeypatch.setattr("harness.emergency_subagent.httpx.AsyncClient",
                        lambda **kw: real_client(transport=httpx.ASGITransport(app=app), base_url="http://app"))
    texts = []

    class FakePhoton:
        async def send(self, recipient, text):
            texts.append(recipient)
            return {"status": "submitted"} if recipient == BACKUP else {"status": "unavailable", "reason": "provider_error"}

    class FakeCaller:
        async def call(self, recipient, twiml):
            return {"status": "unavailable", "reason": "provider_error"}

    monkeypatch.setattr("harness.tools.emergency.PhotonMessaging", FakePhoton)
    monkeypatch.setattr("harness.tools.emergency.TwilioCaller", FakeCaller)
    return texts


def _script_model(monkeypatch, steps):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    script = iter([AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": str(i)}]) if n else AIMessage(content="done")
                   for i, (n, a) in enumerate(steps)])

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", lambda **kw: Model(messages=script))


async def _escalate(consent=True):
    token = await _job(str(uuid.uuid4()), action="send_text_message", consent=consent)
    reply = await graph.ainvoke({"messages": [{"role": "user", "content": f"emergency-job:{token}"}]})
    return reply, await get_job_result(token)


def test_end_to_end_the_agent_climbs_the_ladder_to_the_backup_responder(end_to_end, monkeypatch):
    _script_model(monkeypatch, [("write_todos", {"todos": [{"content": "Try the phone, then the backup", "status": "in_progress"}]}),
                                ("start_phone_call", {}), ("text_backup_responder", {}), (None, None)])
    reply, outcome = run(_escalate())
    assert "Emergency job: finished" in json.dumps(reply)
    assert outcome["status"] == "submitted"
    assert {a: r["status"] for a, r in outcome["actions"].items()} == {
        "send_text_message": "unavailable", "start_phone_call": "unavailable", "text_backup_responder": "submitted"}
    assert end_to_end == [PRIMARY, BACKUP]  # the first text went to the primary, and only the backup text to the backup


def test_end_to_end_a_model_outage_still_reaches_the_backup_through_the_floor(end_to_end, monkeypatch):
    def down(**kw):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", down)
    _, outcome = run(_escalate())
    assert outcome["status"] == "submitted" and set(outcome["actions"]) == {"send_text_message", "start_phone_call", "text_backup_responder"}
    assert end_to_end == [PRIMARY, BACKUP]


def test_end_to_end_when_nothing_works_the_voice_agent_is_told_through_the_job(end_to_end, monkeypatch):
    monkeypatch.delenv("EMERGENCY_BACKUP_RESPONDER_NUMBER")
    get_settings.cache_clear()
    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    _, outcome = run(_escalate())
    assert outcome["status"] == "unavailable"
    assert outcome["notes"] == ["responder_unreachable", "call_emergency_number"]
    assert outcome["actions"]["text_backup_responder"]["reason"] == "backup_not_configured"
    assert end_to_end == [PRIMARY]  # no backup number: nothing was sent to anyone else


def test_end_to_end_without_consent_nothing_is_sent_and_no_model_is_used(end_to_end, monkeypatch):
    def forbidden(**kw):
        raise AssertionError("the model must not run when the caller has not consented")

    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", forbidden)
    _, outcome = run(_escalate(consent=False))
    assert outcome["status"] == "consent_required" and set(outcome["actions"]) == {"send_text_message"} and end_to_end == []
