"""Create the Deep Agents harness."""
import os

from deepagents import AsyncSubAgent, create_deep_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import InMemorySaver

from harness.tools import VoiceVerificationTool
from harness.tools.emergency_agent_tools import build_call_tools
from settings import get_settings


def create_harness(voice_tool: VoiceVerificationTool, emergency_context=None):
    """Build a harness using the verification tool for this call."""
    model = ChatGoogleGenerativeAI(
        model=os.getenv("HARNESS_MODEL", "gemini-3.5-flash"),
        api_key=os.getenv("GEMINI_API_KEY"),
    )
    return create_deep_agent(
        model=model,
        tools=[voice_tool.verify_speaker] + (build_call_tools(emergency_context) if emergency_context is not None else []),
        system_prompt=("Coordinate voice verification and emergency work. Only when verification is "
                       "requested, call verify_speaker. When given an emergency job reference ('emergency-job:' followed by a token), "
                       "call start_async_task exactly once with subagent_type emergency and the reference as the task description, "
                       "unchanged. The emergency subagent runs the requested action itself, then plans and runs the follow-up "
                       "(other channel, backup responder, waiting for an acknowledgement, a check-in). Do not wait or poll. Use your "
                       "own copies of send_text_message, start_phone_call and request_location only if explicitly told to act "
                       "directly, never in addition to a subagent run for the same job."),
        subagents=[AsyncSubAgent(
            name="emergency",
            description=("Emergency actions agent. Start it once for each emergency job reference the application gives you "
                         "('emergency-job:<token>'), passing the reference unchanged as the task. It first runs the requested action "
                         "(send_text_message or start_phone_call to the emergency responder, only with the caller's consent, or "
                         "request_location to text the caller), then a planning agent escalates if needed: the other responder channel, "
                         "a backup responder, waiting for an acknowledgement and a check-in. It runs in the background and reports "
                         "to the application. Never use it for anything else."),
            graph_id="emergency",
            url=get_settings().agent_server_url,
            headers={"Authorization": f"Bearer {get_settings().agent_server_auth_secret}"},
            notify_parent=False,
        )],
        checkpointer=InMemorySaver(),
    )
