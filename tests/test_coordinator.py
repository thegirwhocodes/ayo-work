import asyncio
from pathlib import Path

from harness.case_models import Advice, CaseAnalysis, MemoryUpdate
from harness.coordinator import Coordinator
from harness.corpus.ingest import draft_source_from_markdown
from harness.support import merge_profile, sanitize_profile

GOOD = Advice(risk="elevated", stance="Be calm.", do=["Offer a choice"], avoid=["Ask why"], next_move="Ask what would help.")


class FakeAnalyst:
    """Stands in for the case manager: records what it is asked and returns a canned analysis."""

    def __init__(self, advice=None, memory=None, topics=("disclosure",), gate=None):
        self.calls, self.gate = [], gate
        self.advice, self.memory, self.topics = advice or GOOD, memory or MemoryUpdate(), list(topics)

    async def __call__(self, transcript, profile):
        self.calls.append((transcript, profile))
        if self.gate:
            await self.gate.wait()
        return CaseAnalysis(advice=self.advice, topics=self.topics, memory=self.memory, rubric="satisfied",
                            sources=[{"source": "WHO", "section": "S", "page": "p. 1"}])


def run(coro):
    return asyncio.run(coro)


def test_guidance_is_built_in_background_and_cited():
    async def go():
        analyst = FakeAnalyst()
        c = Coordinator(analyst)
        assert c.latest() == {"status": "unavailable"}
        c.observe("my uncle hurt me", "I'm here with you.")
        assert c.latest() == {"status": "pending"}
        await c.idle()
        g = c.latest()
        assert g["status"] == "ready" and g["risk"] == "elevated" and g["stale"] is False
        assert g["next_move"] == "Ask what would help." and g["checked"] == "satisfied"
        assert g["sources"] == [{"source": "WHO", "section": "S", "page": "p. 1"}]
        assert g["topics"] == ["disclosure"]
        # The analyst sees the transcript and the known preferences.
        assert "Child: my uncle hurt me" in analyst.calls[0][0] and analyst.calls[0][1] == {}
        await c.close()
    run(go())


def test_risk_comes_from_the_advisor_and_empty_topics_fall_back_to_a_default():
    async def go():
        urgent = Advice(risk="urgent", stance="Be calm.", next_move="Ask if they are safe.")
        c = Coordinator(FakeAnalyst(advice=urgent, topics=()))
        c.observe("help")
        await c.idle()
        assert c.latest()["risk"] == "urgent" and c.latest()["topics"] == ["communication"]
        await c.close()
    run(go())


def test_advice_that_breaks_a_hard_rule_never_reaches_the_voice_agent():
    async def go():
        bad = Advice(stance="Tell them help is on the way.", avoid=["Do not ask why"],
                     do=["Ask why he did it?", "Say it is our secret, I promise.", "Text the responder now.",
                         "Text the responder only after the child clearly says yes.", "Offer a slow breath."],
                     next_move="Tell them it will stay confidential.")
        c = Coordinator(FakeAnalyst(advice=bad))
        c.observe("hi")
        await c.idle()
        g = c.latest()
        assert g["do"] == ["Text the responder only after the child clearly says yes.", "Offer a slow breath."]
        assert g["avoid"] == ["Do not ask why"]  # an avoid-list reminder is not an instruction to ask
        assert g["next_move"] == "Stay with them and ask what would help right now."
        assert g["stance"] == "Stay with them and ask what would help right now."
        await c.close()
    run(go())


def test_observe_never_blocks_and_turns_coalesce():
    async def go():
        gate = asyncio.Event()
        analyst = FakeAnalyst(gate=gate)
        c = Coordinator(analyst)
        for i in range(5):
            c.observe(f"turn {i}")  # returns immediately although the LLM is stuck
        await asyncio.sleep(0.01)
        assert c.latest() == {"status": "pending"}
        gate.set()
        await c.idle()
        g = c.latest()
        assert g["as_of_turn"] == 5 and g["turns_behind"] == 0
        assert len(analyst.calls) <= 2  # 5 turns coalesced
        c.observe("another")
        assert c.latest()["turns_behind"] == 1
        await c.close()
    run(go())


def test_stale_flag_when_guidance_lags():
    async def go():
        c = Coordinator(FakeAnalyst())
        c.observe("hi")
        await c.idle()
        c._closed = True  # stop analysing; later turns pile up
        c._turn += 3
        assert c.latest()["stale"] is True
    run(go())


def test_failures_keep_previous_guidance_and_do_not_raise():
    async def go():
        c = Coordinator(FakeAnalyst())
        c.observe("hi")
        await c.idle()
        async def boom(*a):
            raise RuntimeError("down")
        c.analyst = boom
        c.observe("again")
        await c.idle()
        assert c.latest()["status"] == "ready" and c.latest()["turns_behind"] == 1
        await c.close()
    run(go())


def test_memory_builds_incrementally_and_notifies():
    async def go():
        saved = []
        analyst = FakeAnalyst(memory=MemoryUpdate(speaking_pace="slow", helpful_approaches=["grounding"]))
        c = Coordinator(analyst, on_profile=saved.append)
        c.observe("hello")
        await c.idle()
        assert saved[-1] == {"speaking_pace": "slow", "helpful_approaches": ["grounding"]}
        analyst.memory = MemoryUpdate(avoid_approaches=["grounding"], helpful_approaches=["choices"])
        c.observe("no")
        await c.idle()
        assert c.profile == {"speaking_pace": "slow", "helpful_approaches": ["choices"], "avoid_approaches": ["grounding"]}
        c.reset_profile()
        assert c.profile == {}
        await c.close()
    run(go())


def test_profile_rejects_anything_outside_the_closed_vocabulary():
    dirty = {"name": "Ada", "speaking_pace": "slow", "helpful_approaches": ["grounding", "my uncle hurt me"],
             "breathing": "whatever"}
    assert sanitize_profile(dirty) == {"speaking_pace": "slow", "helpful_approaches": ["grounding"]}
    assert merge_profile({}, {"avoid_approaches": ["x"] * 3 + ["choices"]}) == {"avoid_approaches": ["choices"]}


def test_guidance_text_is_bounded():
    async def go():
        c = Coordinator(FakeAnalyst(advice=Advice(stance="x" * 5000, do=["a"] * 20, avoid=[], next_move="n")))
        c.observe("hi")
        await c.idle()
        g = c.latest()
        assert len(g["stance"]) == 300 and len(g["do"]) == 5
        await c.close()
    run(go())


def test_skills_are_read_by_the_case_manager_on_demand_not_pasted_into_prompts():
    from harness import coordinator
    assert not hasattr(coordinator, "load_skills_text")


def test_markdown_draft_is_unapproved_and_skips_references():
    md = Path(__file__).resolve().parents[1] / "harness/corpus/md/responding-to-child-maltreatement.md"
    draft = draft_source_from_markdown(md, {"title": "t"})
    assert draft["review_status"] == "pending" and draft["rights_verified"] is False
    assert len(draft["sections"]) > 50 and draft["sections"][0]["page"].startswith("p. 2")
    assert all("PDF page 9" not in s["page"] for s in draft["sections"])


def test_bridge_persists_learned_profile_only_after_consent():
    from app.voice.twilio_stream import TwilioMediaBridge

    async def go():
        saved = []
        class Store:
            def save(self, caller, profile, **kwargs):
                saved.append(profile)
                return True
        bridge = TwilioMediaBridge(None, "test")
        bridge.memory_store = Store()
        bridge._on_profile({"speaking_pace": "slow"})  # no consent yet: memory is None
        await asyncio.sleep(0.01)
        assert saved == []
        bridge.memory = {}
        bridge._on_profile({"speaking_pace": "slow"})
        await asyncio.gather(*bridge.memory_tasks)
        assert saved == [{"speaking_pace": "slow"}] and bridge.memory == {"speaking_pace": "slow"}
    run(go())


def test_cycles_are_spaced_by_min_interval():
    async def go():
        import time
        stamps = []

        class Timing(FakeAnalyst):
            async def __call__(self, transcript, profile):
                stamps.append(time.monotonic())
                return await super().__call__(transcript, profile)

        c = Coordinator(Timing(), min_interval=0.2)
        c.observe("one")
        await asyncio.sleep(0.05)
        c.observe("two")  # arrives during the spacing wait
        await c.idle()
        assert len(stamps) == 2 and stamps[1] - stamps[0] >= 0.2
        await c.close()
    run(go())


def test_gemini_case_manager_uses_the_first_model_and_keeps_the_rest_as_fallbacks(monkeypatch):
    import sys
    from types import SimpleNamespace
    from harness import case_manager

    class Chat:
        def __init__(self, model, api_key):
            self.name = model

    built = {}
    monkeypatch.setitem(sys.modules, "langchain_google_genai", SimpleNamespace(ChatGoogleGenerativeAI=Chat))
    monkeypatch.setattr(case_manager, "CaseManager", lambda model, fallbacks, **kw: built.update(model=model, fallbacks=fallbacks, kw=kw))
    case_manager.gemini_case_manager(["primary", "backup", "primary"], "key", search="S")
    assert built["model"].name == "primary" and [m.name for m in built["fallbacks"]] == ["backup"]  # de-duplicated, order kept
    assert built["kw"] == {"search": "S"}
