"""Create the Deep Agents harness."""
import os

from deepagents import create_deep_agent
from langchain_google_genai import ChatGoogleGenerativeAI

from harness.tools import VoiceVerificationTool


def create_harness(voice_tool: VoiceVerificationTool):
    """Build a harness using the verification tool for this call."""
    model = ChatGoogleGenerativeAI(
        model=os.getenv("HARNESS_MODEL", "gemini-2.5-flash"),
        api_key=os.getenv("GEMINI_API_KEY"),
    )
    return create_deep_agent(
        model=model,
        tools=[voice_tool.verify_speaker],
        system_prompt="Call verify_speaker exactly once immediately. Report its result without guessing.",
        subagents=[],
    )
