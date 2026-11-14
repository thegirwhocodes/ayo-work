"""In-process, one-use capabilities for emergency subagent jobs."""

import asyncio
import secrets
import time
from dataclasses import dataclass

from harness.tools import EmergencyTools


@dataclass
class EmergencyJob:
    tools: EmergencyTools
    result: asyncio.Future
    expires_at: float


jobs: dict[str, EmergencyJob] = {}


def create_job(tools: EmergencyTools) -> tuple[str, asyncio.Future]:
    token = secrets.token_urlsafe(32)
    result = asyncio.get_running_loop().create_future()
    jobs[token] = EmergencyJob(tools, result, time.monotonic() + 120)
    return token, result


async def execute_job(token: str) -> dict:
    job = jobs.pop(token, None)
    if job is None or time.monotonic() > job.expires_at:
        return {"status": "unavailable", "reason": "invalid_or_expired_job"}
    try:
        await job.tools.run(job.tools.action)
        result = job.tools.result or {"status": "unavailable", "reason": "tool_not_called"}
    except Exception:
        result = {"status": "unavailable", "reason": "tool_failed"}
    if not job.result.done():
        job.result.set_result(result)
    return {"status": "finished"}


def cancel_job(token: str):
    job = jobs.pop(token, None)
    if job is not None and not job.result.done():
        job.result.cancel()
