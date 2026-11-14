import asyncio
import pytest

from harness.support import extract_preferences
from harness.corpus.ingest import vector_literal


def test_preferences_are_restricted_and_explicit():
    memory = extract_preferences("Please talk slower, and my uncle hurt me", {})
    assert memory == {"speaking_pace": "slow"}
    assert extract_preferences("don't breathe", memory)["breathing"] == "avoid"


def test_reject_bad_vectors():
    with pytest.raises(ValueError):
        vector_literal([0] * 1023)
    with pytest.raises(ValueError):
        vector_literal([float("nan")] * 1024)


def test_unreviewed_source_is_rejected_before_db_access():
    from harness.corpus.ingest import ingest_source
    source = dict(title="a", publisher="b", url="c", version="1", license="x", sections=[{}], review_status="pending")
    with pytest.raises(ValueError, match="review"):
        ingest_source(None, source, None)


def test_cohere_adapter_uses_explicit_input_types():
    from types import SimpleNamespace
    from harness.corpus.model import CohereSupportEmbeddings
    calls = []
    class Client:
        def embed(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(embeddings=SimpleNamespace(float_=[[0.1] * 1024 for _ in kwargs["texts"]]))
    adapter = object.__new__(CohereSupportEmbeddings)
    adapter.client = Client()
    assert len(adapter.embed_documents(["source"])[0]) == 1024
    assert len(adapter.embed_query("query")) == 1024
    assert [call["input_type"] for call in calls] == ["search_document", "search_query"]
    assert all(call["output_dimension"] == 1024 and call["truncate"] == "NONE" for call in calls)


def test_enrollment_cannot_load_returning_memory(monkeypatch):
    from app.voice.twilio_stream import TwilioMediaBridge
    import app.voice.twilio_stream as bridge_module
    class Store:
        retention_days = 30
        def __init__(self, *args): pass
        def purge_expired(self): pass
        def read(self, caller): raise AssertionError("new enrollment must not load memory")
    monkeypatch.setattr(bridge_module, "SupportMemoryStore", Store)
    monkeypatch.setenv("SUPPORT_MEMORY_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_GUIDANCE_ENABLED", "false")
    bridge = TwilioMediaBridge(None, "test")
    bridge.verification_status = "enrolled"
    bridge.emergency_context.caller_number = "+12345678901"
    asyncio.run(bridge._setup_support())
    assert bridge.memory is None


def test_no_consent_means_no_memory_write():
    from app.voice.twilio_stream import TwilioMediaBridge
    class Store:
        def save(self, *args, **kwargs): raise AssertionError("no consent")
        def delete(self, *args): raise AssertionError("not matched")
    bridge = TwilioMediaBridge(None, "test")
    bridge.memory_store = Store()
    bridge.verification_status = "enrolled"
    asyncio.run(bridge._process_memory("yes", False))
    assert bridge.memory is None
