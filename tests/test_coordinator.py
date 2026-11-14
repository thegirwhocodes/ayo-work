import asyncio
from pathlib import Path

from harness.coordinator import (Advice, Coordinator, MemoryUpdate, TOPIC_QUERIES, Triage, load_skills_text)
from harness.corpus.ingest import draft_source_from_markdown
from harness.support import merge_profile, sanitize_profile


class FakeLLM:
    def __init__(self, triage=None, gate=None):
        self.calls, self.gate = [], gate
        self.triage = triage or Triage(topics=["disclosure"], risk="elevated")

    async def __call__(self, schema, system, user):
        self.calls.append((schema, system, user))
        if self.gate:
            await self.gate.wait()
        if schema is Triage:
            return self.triage
        return Advice(stance="Be calm.", do=["Offer a choice"], avoid=["Ask why"], next_move="Ask what would help.")


class Embeddings:
    def __init__(self):
        self.queries = []

    def embed_query(self, text):
        self.queries.append(text)
        return [0.0] * 1024


class Corpus:
    def search(self, vector):
        return [{"text": "Listen without judgement.", "source": "WHO", "section": "S", "page": "p. 1", "url": "u"}]


def run(coro):
    return asyncio.run(coro)


def test_guidance_is_built_in_background_and_cited():
    async def go():
        embeddings = Embeddings()
        c = Coordinator(FakeLLM(), "SKILLTEXT", embeddings, Corpus())
        assert c.latest() == {"status": "unavailable"}
        c.observe("my uncle hurt me", "I'm here with you.")
        assert c.latest() == {"status": "pending"}
        await c.idle()
        g = c.latest()
        assert g["status"] == "ready" and g["risk"] == "elevated" and g["stale"] is False
        assert g["next_move"] == "Ask what would help."
        assert g["sources"] == [{"source": "WHO", "section": "S", "page": "p. 1"}]
        # Only the fixed topic query reaches the embedding provider, never caller words.
        assert embeddings.queries == [TOPIC_QUERIES["disclosure"]]
        assert "uncle" not in " ".join(embeddings.queries)
        # Skills and retrieved excerpts reach the advice prompt.
        advice_call = c.llm.calls[1]
        assert "SKILLTEXT" in advice_call[1] and "Listen without judgement." in advice_call[2]
        await c.close()
    run(go())


def test_observe_never_blocks_and_turns_coalesce():
    async def go():
        gate = asyncio.Event()
        llm = FakeLLM(gate=gate)
        c = Coordinator(llm)
        for i in range(5):
            c.observe(f"turn {i}")  # returns immediately although the LLM is stuck
        await asyncio.sleep(0.01)
        assert c.latest() == {"status": "pending"}
        gate.set()
        await c.idle()
        g = c.latest()
        assert g["as_of_turn"] == 5 and g["turns_behind"] == 0
        assert len([x for x in llm.calls if x[0] is Triage]) <= 2  # 5 turns coalesced
        c.observe("another")
        assert c.latest()["turns_behind"] == 1
        await c.close()
    run(go())


def test_stale_flag_when_guidance_lags():
    async def go():
        c = Coordinator(FakeLLM())
        c.observe("hi")
        await c.idle()
        c._closed = True  # stop analysing; later turns pile up
        c._turn += 3
        assert c.latest()["stale"] is True
    run(go())


def test_failures_keep_previous_guidance_and_do_not_raise():
    async def go():
        c = Coordinator(FakeLLM())
        c.observe("hi")
        await c.idle()
        async def boom(*a):
            raise RuntimeError("down")
        c.llm = boom
        c.observe("again")
        await c.idle()
        assert c.latest()["status"] == "ready" and c.latest()["turns_behind"] == 1
        await c.close()
    run(go())


def test_memory_builds_incrementally_and_notifies():
    async def go():
        saved = []
        llm = FakeLLM(Triage(memory=MemoryUpdate(speaking_pace="slow", helpful_approaches=["grounding"])))
        c = Coordinator(llm, on_profile=saved.append)
        c.observe("hello")
        await c.idle()
        assert saved[-1] == {"speaking_pace": "slow", "helpful_approaches": ["grounding"]}
        llm.triage = Triage(memory=MemoryUpdate(avoid_approaches=["grounding"], helpful_approaches=["choices"]))
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
        class Long(FakeLLM):
            async def __call__(self, schema, system, user):
                if schema is Advice:
                    return Advice(stance="x" * 5000, do=["a"] * 20, avoid=[], next_move="n")
                return await super().__call__(schema, system, user)
        c = Coordinator(Long())
        c.observe("hi")
        await c.idle()
        g = c.latest()
        assert len(g["stance"]) == 300 and len(g["do"]) == 5
        await c.close()
    run(go())


def test_skills_text_includes_bundled_skills():
    text = load_skills_text()
    assert "empathy/SKILL.md" in text and "cbt/SKILL.md" in text


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
