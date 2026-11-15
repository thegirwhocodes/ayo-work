"""Agent Protocol graph: the emergency escalation agent.

One job, three steps (the nodes show up in LangGraph Studio):

1. ``first_action``: no model. Read the job's briefing and run the action the voice agent asked for, straight
   away. The child's request never waits on a model, and a model outage can never lose it.
2. ``plan_escalation``: a Deep Agent (the same model as the main harness agent, ``HARNESS_MODEL``) plans the
   follow-up with ``write_todos`` and carries it out with its tools: try the other responder channel, then the
   backup responder, wait for the responder to acknowledge, check in with the child, and finally have the voice
   agent point the child at the local emergency number (see ``harness/emergency_plan.py``). The application
   feeds it events while it runs: ``responder_acknowledged`` and ``location_received``.
   Afterwards the deterministic floor (``run_floor``) completes whatever the ladder still calls for, whether
   the agent finished, stopped early or failed.
3. ``finish_job``: close the job and store one combined outcome for the application to collect.

This server holds no Photon or Twilio secrets. Every tool asks the application, through the authenticated
callback, to perform one action for this job (see ``harness/tools/emergency_agent_tools.py``); the application
enforces consent, runs each action at most once, and sends only the message the caller approved. So the model
chooses what happens next but can never write or change what is sent, or what the voice agent is told.
"""

import asyncio
import hashlib
import re
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from harness import emergency_plan as plan
from harness.emergency_plan import RESPONDER_CHANNELS, run_floor
from harness.tool_log import log_tool_event, timed
from harness.tools.emergency_agent_tools import build_plan_tools
from settings import get_settings

TASK = re.compile(r"emergency-job:([A-Za-z0-9_-]{43})")

SYSTEM_PROMPT = """You are the emergency escalation agent for Sabi Safe, a support line for children who may be in danger.
A voice agent is on the phone with a child and asked for an emergency action. The application has ALREADY done that first
action; you are told its result. Your job is the follow-up. Start by writing a short plan with write_todos, keep it up to
date, then carry it out. You act through tools only. Use no other tools.

The ladder, in order. Stop climbing as soon as a responder action comes back 'submitted'.
1. The first action is done. If it came back 'submitted', go to step 4.
2. If it was unavailable or unknown, try the OTHER responder channel once (SMS and phone call are the two channels).
3. If neither channel was submitted, try text_backup_responder once.
4. After something was submitted, wait with wait_for_events (up to 60 seconds at a time) and read what comes back.
   - 'responder_acknowledged': the responder has seen it. The escalation is over; finish.
   - 'location_received': the caller sent a location. It is not an acknowledgement; keep waiting.
5. If check_in_due is true and nobody has acknowledged, call notify_voice_agent with 'check_in' once, then keep waiting.
6. When window_remaining_seconds reaches 0 with no acknowledgement, call notify_voice_agent with 'call_emergency_number' and finish.
7. If every channel failed: call notify_voice_agent with 'responder_unreachable', then 'call_emergency_number', and finish.

Rules:
- 'consent_required' means the caller has not agreed to contact the responder. Do nothing more that contacts anyone.
- If wait_for_events reports job_open false, the call ended or consent was withdrawn: stop at once, do nothing else.
- Each action and each note works once; repeating one returns the stored result. Never try to work around a refusal.
- Do not use request_location: the voice agent did not ask for it.
- You cannot write or change what is sent or what the voice agent says; the caller approved the message and the
  application words the notes.
- Never claim more than a tool result says. 'submitted' means a provider accepted it, not that it was delivered, read or answered.
- When you are done, reply with one short sentence stating exactly what happened, including anything that failed."""


class EmergencyState(TypedDict, total=False):
    messages: list
    token: str
    briefing: dict
    statuses: dict


def _text(message) -> str:
    content = message.content if hasattr(message, "content") else message.get("content", "")
    return content if isinstance(content, str) else ""


def _reply(text: str, **state) -> dict:
    return {"messages": [{"role": "assistant", "content": text}], **state}


def _job_ref(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


async def ask(token: str, action: str, **extra) -> dict:
    """One authenticated call to the application's job callback."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(settings.emergency_callback_url, json={"token": token, "action": action, **extra},
                                         headers={"X-Emergency-Worker-Key": settings.emergency_worker_secret})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log_tool_event("emergency_subagent_run", job_ref=_job_ref(token), phase="callback_failed", action=action,
                           error=type(exc).__name__)
            return {"status": "unavailable", "reason": "callback_failed"}


def _briefing_task(briefing: dict, statuses: dict) -> str:
    requested = briefing["requested_action"]
    return (f"The voice agent requested: {requested}. "
            f"Caller consent to contact the emergency responder: {'yes' if briefing['responder_consent'] else 'NO'}. "
            f"A caller-approved message is on file: {'yes' if briefing['has_message'] else 'NO'}. "
            f"The application already ran it. Result so far: {statuses}. "
            f"The escalation window is {plan.PLAN_WINDOW_SECONDS} seconds; check in after {plan.CHECK_IN_AFTER_SECONDS} seconds "
            "without an acknowledgement. Write your plan, then carry it out.")


async def run_agent(tools, briefing: dict, statuses: dict) -> None:
    """Plan and run the follow-up with a Deep Agent. Patched in tests."""
    from deepagents import FilesystemPermission, create_deep_agent
    from langchain.agents.middleware import TodoListMiddleware
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = get_settings()
    model = ChatGoogleGenerativeAI(model=settings.harness_model, api_key=settings.gemini_api_key)
    agent = create_deep_agent(
        model=model, tools=tools, system_prompt=SYSTEM_PROMPT, name="emergency-escalation",
        middleware=[TodoListMiddleware()],  # the visible plan; deepagents 0.7 ships no planning tool of its own
        subagents=[],
        # The built-in file tools have no use here; deny them so the model has nothing but the job's tools to act with.
        permissions=[FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny")],
    )
    await asyncio.wait_for(
        agent.ainvoke({"messages": [{"role": "user", "content": _briefing_task(briefing, statuses)}]},
                      config={"recursion_limit": 100}),
        plan.PLAN_WINDOW_SECONDS + 30)


async def first_action(state: EmergencyState) -> dict:
    match = TASK.fullmatch(_text(state["messages"][-1]).strip())
    if not match:
        return _reply("Invalid emergency job.")
    settings = get_settings()
    if len(settings.emergency_worker_secret) < 32:
        return _reply("Emergency worker authentication is not configured.")
    token = match.group(1)
    log_tool_event("emergency_subagent_run", job_ref=_job_ref(token), phase="started", model=settings.harness_model)
    briefing = await ask(token, "briefing")
    if briefing.get("status") != "ok":
        log_tool_event("emergency_subagent_run", job_ref=_job_ref(token), phase="no_briefing", reason=briefing.get("reason"))
        return _reply("Emergency job: unavailable.")
    requested = briefing["requested_action"]
    log_tool_event("emergency_subagent_run", job_ref=_job_ref(token), phase="briefing", requested_action=requested,
                   responder_consent=briefing["responder_consent"])
    with timed() as timer:
        result = await ask(token, requested)
    log_tool_event("emergency_subagent_run", job_ref=_job_ref(token), phase="first_action", action=requested,
                   status=result.get("status"), reason=result.get("reason"), duration_ms=timer.ms)
    return {"token": token, "briefing": briefing, "statuses": {requested: result.get("status", "unavailable")}}


def _after_first_action(state: EmergencyState) -> str:
    return "plan_escalation" if state.get("token") else END


async def plan_escalation(state: EmergencyState) -> dict:
    token, briefing, statuses = state["token"], state["briefing"], dict(state["statuses"])
    requested, ref = briefing["requested_action"], _job_ref(token)

    async def act(action: str) -> dict:
        return await ask(token, action)

    async def note(name: str) -> dict:
        return await ask(token, "note", note=name)

    async def poll() -> dict:
        return await ask(token, "events")

    # A location request has nothing to escalate, and an unconsented responder request must not be pushed.
    if requested in RESPONDER_CHANNELS and statuses.get(requested) != "consent_required":
        with timed() as timer:
            try:
                await run_agent(build_plan_tools(act, poll, note), briefing, statuses)
            except Exception as exc:
                log_tool_event("emergency_subagent_run", job_ref=ref, phase="agent_failed", error=type(exc).__name__)
        log_tool_event("emergency_subagent_run", job_ref=ref, phase="agent_done", duration_ms=timer.ms)
        current = await poll()
        if current.get("status") != "ok":  # the call ended or consent was withdrawn: nothing more to do
            return {}
        statuses = {**statuses, **current["results"]}
        final = await run_floor(requested, statuses, act, note)
        if final != statuses:
            log_tool_event("emergency_subagent_run", job_ref=ref, phase="floor", actions=sorted(set(final) - set(statuses)))
    return {}


async def finish_job(state: EmergencyState) -> dict:
    token = state["token"]
    status = (await ask(token, "finish")).get("status", "unavailable")
    log_tool_event("emergency_subagent_run", job_ref=_job_ref(token), phase="finished", status=status)
    return _reply(f"Emergency job: {status}.")


builder = StateGraph(EmergencyState)
builder.add_node("first_action", first_action)
builder.add_node("plan_escalation", plan_escalation)
builder.add_node("finish_job", finish_job)
builder.add_edge(START, "first_action")
builder.add_conditional_edges("first_action", _after_first_action, {"plan_escalation": "plan_escalation", END: END})
builder.add_edge("plan_escalation", "finish_job")
builder.add_edge("finish_job", END)
graph = builder.compile()
