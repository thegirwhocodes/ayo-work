"""Create the Deep Agents harness."""
import os
from pathlib import Path

from deepagents import AsyncSubAgent, create_deep_agent
from deepagents.backends.utils import create_file_data
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import InMemorySaver

from harness.tools import VoiceVerificationTool
from settings import get_settings


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
        system_prompt=("Coordinate voice verification and emergency work. Only when verification is "
                       "requested, call verify_speaker. For an emergency job, call start_async_task with subagent_type "
                       "emergency and pass the job reference exactly as supplied. Do not wait or poll."),
        subagents=[AsyncSubAgent(
            name="emergency",
            description="Run an authorized emergency action in the background.",
            graph_id="emergency",
            url=get_settings().agent_server_url,
            notify_parent=False,
        )],
        checkpointer=InMemorySaver(),
    )
