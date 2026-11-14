import asyncio
import json

from harness.emergency_jobs import cancel_job, create_job, execute_job, jobs
from harness.emergency_subagent import graph
from harness.tools import EmergencyCallContext, EmergencyTools
from app.voice.twilio_stream import TwilioMediaBridge


def test_job_is_one_use_and_rechecks_consent(monkeypatch):
    async def exercise():
        context = EmergencyCallContext("+12345678901", responder_consent=True)
        tools = EmergencyTools(context, "send_text_message", "help")
        token, future = create_job(tools)
        context.responder_consent = False
        assert (await execute_job(token))["status"] == "finished"
        assert await future == {"status": "consent_required"}
        assert (await execute_job(token))["reason"] == "invalid_or_expired_job"
        assert token not in jobs
    asyncio.run(exercise())


def test_cancelled_job_does_not_execute():
    async def exercise():
        token, future = create_job(EmergencyTools(EmergencyCallContext("+12345678901"), "start_phone_call"))
        cancel_job(token)
        assert future.cancelled()
        assert (await execute_job(token))["reason"] == "invalid_or_expired_job"
    asyncio.run(exercise())


def test_invalid_subagent_message_does_not_reach_callback():
    result = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": "not a job"}]}))
    assert "Invalid emergency job" in json.dumps(result)


def test_relay_returns_while_harness_is_still_running():
    async def exercise():
        release = asyncio.Event()

        class Agent:
            async def ainvoke(self, payload, config=None):
                await release.wait()

        bridge = TwilioMediaBridge(object(), "prompt")
        bridge.harness_agent = Agent()
        result = await bridge.relay_to_harness("relay_to_harness", {"action": "start_phone_call"})
        assert result == {"status": "pending"}
        assert len(bridge.emergency_tasks) == 1
        for task in bridge.emergency_tasks:
            task.cancel()
        await asyncio.gather(*bridge.emergency_tasks, return_exceptions=True)

    asyncio.run(exercise())
