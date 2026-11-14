from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from harness.agent import load_skill_files


def test_bundled_skills_are_discovered_by_deep_agents():
    prompts = []

    class Model(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(str(messages[0].content))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    agent = create_deep_agent(model=Model(responses=["ok"]), skills=["/skills/"], subagents=[])
    files = load_skill_files()
    agent.invoke({"messages": [{"role": "user", "content": "hello"}], "files": files})

    assert "/skills/cbt/SKILL.md" in prompts[0]
    assert "/skills/empathy/SKILL.md" in prompts[0]
    assert "/skills/cbt/references/foundation.md" in files
    assert "/skills/empathy/safeguards.md" in files
