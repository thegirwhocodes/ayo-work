from harness.corpus import ingest
from harness.corpus.model import LazyEmbeddings
from harness.corpus.sync import load_sources, sync_corpus


class Cursor:
    def __init__(self, log): self.log = log
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.log.append(sql.split()[0] + " " + sql.split()[1])


class Connection:
    def __init__(self): self.log = []
    def cursor(self): return Cursor(self.log)


def test_lazy_embeddings_do_not_need_a_key_until_used():
    def factory():
        raise AssertionError("must not be built when nothing is embedded")
    LazyEmbeddings(factory)  # constructing is free
    built = []
    class Inner:
        def embed_query(self, t): return [1.0]
    lazy = LazyEmbeddings(lambda: built.append(1) or Inner())
    lazy.embed_query("a"); lazy.embed_query("b")
    assert built == [1]


def test_manifest_loads_sections_and_ships_unapproved():
    sources = load_sources()
    assert sources[0]["review_status"] == "pending" and sources[0]["rights_verified"] is False
    assert len(sources[0]["sections"]) > 50 and "markdown" not in sources[0]


def test_unapproved_source_is_skipped_without_embedding():
    result = sync_corpus(Connection(), embeddings=None)
    assert result[0][1] == "skipped: not approved"


def test_digest_depends_on_pipeline_signature(monkeypatch):
    assert ingest.digest(ingest.PIPELINE + "\nx") != ingest.digest("\nx")
    assert ingest.MODEL in ingest.PIPELINE and str(ingest.DIMENSIONS) in ingest.PIPELINE
