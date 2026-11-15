from harness.corpus import ingest
from harness.corpus.sync import load_sources, sync_corpus


class Cursor:
    def __init__(self, log): self.log = log
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.log.append(sql.split()[0] + " " + sql.split()[1])


class Connection:
    def __init__(self): self.log = []
    def cursor(self): return Cursor(self.log)


def _manifest(tmp_path, **flags):
    import json
    (tmp_path / "s.md").write_text("## handbook page 1, PDF page 9\nA section.\n")
    entry = {"title": "T", "publisher": "P", "url": "u", "version": "1", "license": "L", "markdown": "s.md", **flags}
    path = tmp_path / "m.json"
    path.write_text(json.dumps([entry]))
    return path


def test_manifest_loads_sections_from_markdown(tmp_path):
    sources = load_sources(_manifest(tmp_path, review_status="pending", rights_verified=False))
    assert sources[0]["sections"][0]["page"] == "p. 1 (PDF 9)" and "markdown" not in sources[0]
    assert len(load_sources()[0]["sections"]) > 50  # the shipped manifest resolves


def test_unapproved_source_is_skipped_without_embedding(tmp_path):
    manifest = _manifest(tmp_path, review_status="pending", rights_verified=False)
    result = sync_corpus(Connection(), make_embeddings=lambda: 1 / 0, manifest=manifest)
    assert result[0][1] == "skipped: not approved"


def test_digest_depends_on_pipeline_signature(monkeypatch):
    assert ingest.digest(ingest.PIPELINE + "\nx") != ingest.digest("\nx")
    assert ingest.MODEL in ingest.PIPELINE and str(ingest.DIMENSIONS) in ingest.PIPELINE
