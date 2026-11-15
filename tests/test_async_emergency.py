import asyncio
import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet

from app.voice.twilio_stream import TwilioMediaBridge
from app.main import app
from fastapi.testclient import TestClient
from harness.emergency_jobs import (cancel_job, cancel_session_jobs, create_job, finish_job, get_job_result, job_briefing,
                                    run_job_action, summarize, token_hash)
from harness.emergency_subagent import graph
from harness.tools import EmergencyCallContext, EmergencyTools
from settings import get_settings


@pytest.fixture
def job_database(monkeypatch):
    url = os.getenv("TEST_EMERGENCY_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_EMERGENCY_DATABASE_URL to an isolated Postgres database")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.setenv("EMERGENCY_JOB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with psycopg.connect(url) as connection:
        connection.execute((Path(__file__).parent.parent / "migrations/002_emergency_jobs.sql").read_text())
    yield url
    get_settings.cache_clear()


def test_job_is_encrypted_and_each_action_runs_once_across_connections(job_database):
    async def exercise():
        session = str(uuid.uuid4())
        token = await create_job(EmergencyTools(EmergencyCallContext("+12345678901"), "start_phone_call", "help me"), session)
        with psycopg.connect(job_database) as connection:
            row = connection.execute("SELECT token_hash,payload FROM emergency_jobs WHERE token_hash=%s", (token_hash(token),)).fetchone()
        assert token not in row[0] and b"+12345678901" not in bytes(row[1]) and b"help me" not in bytes(row[1])
        results = await asyncio.gather(run_job_action(token, "start_phone_call"), run_job_action(token, "start_phone_call"))
        assert results[0] == results[1] or {"in_progress_or_interrupted"} & {r.get("reason") for r in results}
        assert (await run_job_action(token, "start_phone_call")) == {"status": "consent_required"}  # stored, not re-run
        assert await finish_job(token) == {"status": "finished"}
        outcome = await get_job_result(token)
        assert outcome["status"] == "consent_required" and set(outcome["actions"]) == {"start_phone_call"}
        assert await get_job_result(token) is None
        assert (await run_job_action(token, "start_phone_call"))["reason"] == "invalid_or_expired_job"  # a closed job is dead
    asyncio.run(exercise())


def test_briefing_exposes_flags_but_never_content(job_database):
    async def exercise():
        context = EmergencyCallContext("+12345678901", responder_consent=True)
        token = await create_job(EmergencyTools(context, "send_text_message", "secret words"), str(uuid.uuid4()))
        briefing = await job_briefing(token)
        assert briefing == {"status": "ok", "requested_action": "send_text_message", "responder_consent": True,
                            "has_message": True, "actions_done": []}
        assert "secret words" not in json.dumps(briefing) and "12345678901" not in json.dumps(briefing)
        assert (await job_briefing("x" * 43))["reason"] == "invalid_or_expired_job"
    asyncio.run(exercise())


def test_unknown_actions_are_refused(job_database):
    async def exercise():
        token = await create_job(EmergencyTools(EmergencyCallContext("+12345678901"), "request_location"), str(uuid.uuid4()))
        assert (await run_job_action(token, "delete_everything"))["reason"] == "action_not_allowed"
    asyncio.run(exercise())


def test_revoked_or_cancelled_jobs_are_not_executed(job_database):
    async def exercise():
        session = str(uuid.uuid4())
        context = EmergencyCallContext("+12345678901", responder_consent=True)
        token = await create_job(EmergencyTools(context, "send_text_message", "help"), session)
        await cancel_session_jobs(session, responder_only=True)
        assert (await run_job_action(token, "send_text_message"))["reason"] == "invalid_or_expired_job"
        other = await create_job(EmergencyTools(context, "start_phone_call", "help"), session)
        await cancel_job(other)
        assert (await run_job_action(other, "start_phone_call"))["reason"] == "invalid_or_expired_job"
    asyncio.run(exercise())


def test_tampered_payload_fails_closed(job_database):
    async def exercise():
        token = await create_job(EmergencyTools(EmergencyCallContext("+12345678901"), "start_phone_call"), str(uuid.uuid4()))
        with psycopg.connect(job_database) as connection:
            connection.execute("UPDATE emergency_jobs SET payload=%s WHERE token_hash=%s", (b"corrupt", token_hash(token)))
        assert (await run_job_action(token, "start_phone_call")) == {"status": "unavailable", "reason": "tool_failed"}
        assert (await finish_job(token)) == {"status": "finished"}
        assert await get_job_result(token) == {"status": "unavailable", "reason": "tool_failed"}
    asyncio.run(exercise())


def test_summary_prefers_what_actually_happened():
    assert summarize({})["reason"] == "no_action_taken"
    mixed = summarize({"send_text_message": {"status": "consent_required"}, "request_location": {"status": "submitted"}})
    assert mixed["status"] == "submitted"
    assert summarize({"a": {"status": "unavailable", "reason": "x"}, "b": {"status": "unknown", "reason": "y"}})["reason"] == "y"


def test_invalid_subagent_message_does_not_reach_callback():
    result = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": "not a job"}]}))
    assert "Invalid emergency job" in json.dumps(result)


def test_worker_callback_requires_shared_secret_and_routes_actions(monkeypatch):
    secret = "x" * 40
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", secret)
    get_settings.cache_clear()
    calls = []

    async def run(token, action):
        calls.append(("run", action))
        return {"status": "submitted"}

    async def brief(token):
        calls.append(("briefing",))
        return {"status": "ok"}

    async def finish(token):
        calls.append(("finish",))
        return {"status": "finished"}

    monkeypatch.setattr("app.main.run_job_action", run)
    monkeypatch.setattr("app.main.job_briefing", brief)
    monkeypatch.setattr("app.main.finish_job", finish)
    client = TestClient(app)
    token, headers = "x" * 43, {"X-Emergency-Worker-Key": secret}
    try:
        assert client.post("/internal/emergency-job", json={"token": token, "action": "briefing"}).status_code == 403
        assert client.post("/internal/emergency-job", json={"token": token, "action": "briefing"}, headers=headers).json() == {"status": "ok"}
        assert client.post("/internal/emergency-job", json={"token": token, "action": "request_location"}, headers=headers).json() == {"status": "submitted"}
        assert client.post("/internal/emergency-job", json={"token": token, "action": "finish"}, headers=headers).json() == {"status": "finished"}
        assert client.post("/internal/emergency-job", json={"token": token, "action": "rm -rf"}, headers=headers).status_code == 400
        assert client.post("/internal/emergency-job", json={"token": token}, headers=headers).status_code == 400
        assert calls == [("briefing",), ("run", "request_location"), ("finish",)]
    finally:
        get_settings.cache_clear()


def test_relay_returns_while_harness_is_still_running(monkeypatch):
    async def create(tools, session_id):
        return "x" * 43
    async def cancel(token):
        pass
    monkeypatch.setattr("app.voice.twilio_stream.create_job", create)
    monkeypatch.setattr("app.voice.twilio_stream.cancel_job", cancel)

    async def exercise():
        release = asyncio.Event()
        class Agent:
            async def ainvoke(self, payload, config=None):
                await release.wait()
        bridge = TwilioMediaBridge(object(), "prompt")
        bridge.harness_agent = Agent()
        bridge.emergency_context.responder_consent = True
        assert (await bridge.relay_to_harness("relay_to_harness", {"action": "start_phone_call", "message": "help"}))["status"] == "pending"
        assert len(bridge.emergency_tasks) == 1
        for task in bridge.emergency_tasks:
            task.cancel()
        await asyncio.gather(*bridge.emergency_tasks, return_exceptions=True)
    asyncio.run(exercise())


def test_agent_server_auth_rejects_bad_credentials(monkeypatch):
    from harness.agent_auth import authenticate

    secret = "a" * 40
    monkeypatch.setenv("AGENT_SERVER_AUTH_SECRET", secret)
    get_settings.cache_clear()
    try:
        assert asyncio.run(authenticate(f"Bearer {secret}")) == "sabi-safe-app"
        for bad in (None, "", "Bearer wrong", secret):
            with pytest.raises(Exception):
                asyncio.run(authenticate(bad))
        monkeypatch.setenv("AGENT_SERVER_AUTH_SECRET", "short")
        get_settings.cache_clear()
        with pytest.raises(Exception):
            asyncio.run(authenticate("Bearer short"))
    finally:
        get_settings.cache_clear()


class FakeCallback:
    """Stands in for httpx.AsyncClient and the application's job callback."""

    def __init__(self, briefing, results=None):
        self.briefing, self.results, self.posts = briefing, results or {}, []

    def __call__(self, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, json, headers):
        self.posts.append((url, json, headers))
        action = json["action"]
        payload = (self.briefing if action == "briefing" else {"status": "finished"} if action == "finish"
                   else self.results.get(action, {"status": "submitted"}))

        class Response:
            def raise_for_status(self): pass
            def json(self_inner): return payload
        return Response()


def _subagent(monkeypatch, callback, agent):
    secret = "w" * 40
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", secret)
    get_settings.cache_clear()
    monkeypatch.setattr("harness.emergency_subagent.httpx.AsyncClient", callback)
    monkeypatch.setattr("harness.emergency_subagent.run_agent", agent)
    token = "t" * 43
    try:
        result = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": f"emergency-job:{token}"}]}))
    finally:
        get_settings.cache_clear()
    return token, secret, result


BRIEFING = {"status": "ok", "requested_action": "send_text_message", "responder_consent": True, "has_message": True, "actions_done": []}


def test_subagent_runs_the_requested_action_first_and_hands_the_plan_tools_to_the_agent(monkeypatch):
    callback = FakeCallback(BRIEFING)
    seen = {}

    async def agent(tools, briefing, statuses):
        by_name = {tool.name: tool for tool in tools}
        seen["tools"], seen["statuses"] = set(by_name), statuses
        await by_name["start_phone_call"].ainvoke({})

    token, secret, result = _subagent(monkeypatch, callback, agent)
    assert seen["tools"] == {"send_text_message", "start_phone_call", "request_location", "text_backup_responder",
                             "wait_for_events", "notify_voice_agent"}
    assert seen["statuses"] == {"send_text_message": "submitted"}  # the first action already ran, with no model call
    assert [post[1]["action"] for post in callback.posts] == ["briefing", "send_text_message", "start_phone_call", "events", "finish"]
    assert all(post[1]["token"] == token and post[2] == {"X-Emergency-Worker-Key": secret} for post in callback.posts)
    assert "Emergency job: finished" in json.dumps(result)


def test_the_requested_action_still_runs_first_when_the_model_fails(monkeypatch):
    callback = FakeCallback(BRIEFING)

    async def broken(tools, briefing, statuses):
        raise RuntimeError("model unavailable")

    _subagent(monkeypatch, callback, broken)
    assert [post[1]["action"] for post in callback.posts] == ["briefing", "send_text_message", "events", "finish"]


def test_subagent_falls_back_if_the_model_calls_nothing(monkeypatch):
    callback = FakeCallback({**BRIEFING, "requested_action": "request_location"})

    async def idle(tools, briefing, statuses):
        return None

    _subagent(monkeypatch, callback, idle)
    assert [post[1]["action"] for post in callback.posts] == ["briefing", "request_location", "finish"]


def test_subagent_does_nothing_without_a_valid_briefing(monkeypatch):
    callback = FakeCallback({"status": "unavailable", "reason": "invalid_or_expired_job"})
    ran = []

    async def agent(tools, briefing, statuses):
        ran.append(1)

    _, _, result = _subagent(monkeypatch, callback, agent)
    assert [post[1]["action"] for post in callback.posts] == ["briefing"] and not ran
    assert "Emergency job: unavailable" in json.dumps(result)


def test_subagent_tools_describe_when_to_use_them():
    from harness.tools.emergency_agent_tools import DESCRIPTIONS, build_emergency_tools

    tools = {tool.name: tool for tool in build_emergency_tools(lambda action: asyncio.sleep(0))}
    for name, tool in tools.items():
        assert tool.description == DESCRIPTIONS[name] and len(tool.description) > 150
        assert "Use it when" in tool.description and "submitted" in tool.description
    assert "consent" in tools["send_text_message"].description and "consent" in tools["start_phone_call"].description
    assert "CALLER" in tools["request_location"].description
