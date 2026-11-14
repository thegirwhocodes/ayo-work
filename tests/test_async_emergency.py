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
from harness.emergency_jobs import cancel_job, cancel_session_jobs, create_job, execute_job, get_job_result, token_hash
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


def test_job_is_encrypted_and_one_use_across_connections(job_database):
    async def exercise():
        session = str(uuid.uuid4())
        token = await create_job(EmergencyTools(EmergencyCallContext("+12345678901"), "start_phone_call"), session)
        with psycopg.connect(job_database) as connection:
            row = connection.execute("SELECT token_hash,payload FROM emergency_jobs WHERE token_hash=%s", (token_hash(token),)).fetchone()
        assert token not in row[0] and b"+12345678901" not in bytes(row[1])
        results = await asyncio.gather(execute_job(token), execute_job(token))
        assert sorted(item["status"] for item in results) == ["finished", "unavailable"]
        assert await get_job_result(token) == {"status": "not_implemented"}
        assert await get_job_result(token) is None
    asyncio.run(exercise())


def test_revoked_or_cancelled_jobs_are_not_executed(job_database):
    async def exercise():
        session = str(uuid.uuid4())
        context = EmergencyCallContext("+12345678901", responder_consent=True)
        token = await create_job(EmergencyTools(context, "send_text_message", "help"), session)
        await cancel_session_jobs(session, responder_only=True)
        assert (await execute_job(token))["reason"] == "invalid_or_expired_job"
        other = await create_job(EmergencyTools(context, "start_phone_call"), session)
        await cancel_job(other)
        assert (await execute_job(other))["reason"] == "invalid_or_expired_job"
    asyncio.run(exercise())


def test_tampered_payload_fails_closed(job_database):
    async def exercise():
        token = await create_job(EmergencyTools(EmergencyCallContext("+12345678901"), "start_phone_call"), str(uuid.uuid4()))
        with psycopg.connect(job_database) as connection:
            connection.execute("UPDATE emergency_jobs SET payload=%s WHERE token_hash=%s", (b"corrupt", token_hash(token)))
        assert (await execute_job(token))["status"] == "finished"
        assert await get_job_result(token) == {"status": "unavailable", "reason": "tool_failed"}
    asyncio.run(exercise())


def test_invalid_subagent_message_does_not_reach_callback():
    result = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": "not a job"}]}))
    assert "Invalid emergency job" in json.dumps(result)


def test_worker_callback_requires_shared_secret(monkeypatch):
    secret = "x" * 40
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", secret)
    get_settings.cache_clear()

    async def execute(token):
        return {"status": "finished"}

    monkeypatch.setattr("app.main.execute_job", execute)
    client = TestClient(app)
    try:
        assert client.post("/internal/emergency-job", json={"token": "x" * 43}).status_code == 403
        response = client.post("/internal/emergency-job", json={"token": "x" * 43},
                               headers={"X-Emergency-Worker-Key": secret})
        assert response.json() == {"status": "finished"}
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
        assert await bridge.relay_to_harness("relay_to_harness", {"action": "start_phone_call"}) == {"status": "pending"}
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


def test_subagent_posts_token_with_worker_key(monkeypatch):
    secret = "w" * 40
    monkeypatch.setenv("EMERGENCY_WORKER_SECRET", secret)
    get_settings.cache_clear()
    seen = {}

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"status": "finished"}

    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json, headers):
            seen.update(url=url, json=json, headers=headers)
            return FakeResponse()

    monkeypatch.setattr("harness.emergency_subagent.httpx.AsyncClient", FakeClient)
    token = "t" * 43
    try:
        result = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": f"emergency-job:{token}"}]}))
    finally:
        get_settings.cache_clear()
    assert seen["json"] == {"token": token}
    assert seen["headers"] == {"X-Emergency-Worker-Key": secret}
    assert "Emergency job: finished" in json.dumps(result)
