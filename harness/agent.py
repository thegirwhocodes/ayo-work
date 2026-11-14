"""Create the Deep Agents harness."""
import os
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.utils import create_file_data
from langchain_google_genai import ChatGoogleGenerativeAI

from harness.tools import EmergencyTools, VoiceVerificationTool


def load_skill_files():
    """Seed the agent's in-state filesystem with the bundled skill documents."""
    skills_directory = Path(__file__).resolve().parent / "skills"
    return {
        f"/skills/{path.relative_to(skills_directory).as_posix()}": create_file_data(
            path.read_text(encoding="utf-8")
        )
        for path in skills_directory.rglob("*.md")
    }


def create_harness(voice_tool: VoiceVerificationTool):
    """Build a harness using the verification tool for this call."""
    model = ChatGoogleGenerativeAI(
        model=os.getenv("HARNESS_MODEL", "gemini-2.5-flash"),
        api_key=os.getenv("GEMINI_API_KEY"),
    )
    return create_deep_agent(
        model=model,
        tools=[voice_tool.verify_speaker],
        skills=["/skills/"],
        system_prompt="Call verify_speaker exactly once immediately. Report its result without guessing.",
        subagents=[],
    )


def create_emergency_harness(emergency_tools: EmergencyTools):
    """Create a separate harness for a single relayed emergency action."""
    model = ChatGoogleGenerativeAI(
        model=os.getenv("HARNESS_MODEL", "gemini-2.5-flash"),
        api_key=os.getenv("GEMINI_API_KEY"),
    )
    return create_deep_agent(
        model=model,
        tools=[emergency_tools.send_text_message, emergency_tools.request_location,
               emergency_tools.start_phone_call],
        system_prompt=("Call the named tool once immediately. Use only that tool; "
                       "report its result without adding claims about delivery or dispatch."),
        subagents=[],
    )
