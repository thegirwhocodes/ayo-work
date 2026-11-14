"""Agent Protocol graph: execute one opaque, authorized emergency job."""

import re
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from settings import get_settings


class EmergencyState(TypedDict):
    messages: list


async def execute_emergency_job(state: EmergencyState) -> dict:
    message = state["messages"][-1]
    content = message.content if hasattr(message, "content") else message.get("content", "")
    match = re.fullmatch(r"emergency-job:([A-Za-z0-9_-]{43})", content.strip())
    if not match:
        return {"messages": [{"role": "assistant", "content": "Invalid emergency job."}]}
    callback = get_settings().emergency_callback_url
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(callback, json={"token": match.group(1)})
            response.raise_for_status()
            status = response.json().get("status")
    except (httpx.HTTPError, ValueError):
        status = "unavailable"
    return {"messages": [{"role": "assistant", "content": f"Emergency job: {status}."}]}


builder = StateGraph(EmergencyState)
builder.add_node("execute_emergency_job", execute_emergency_job)
builder.add_edge(START, "execute_emergency_job")
builder.add_edge("execute_emergency_job", END)
graph = builder.compile()
