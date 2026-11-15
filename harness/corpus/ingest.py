"""Ingest a reviewed, sectioned JSON corpus manifest into pgvector."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL = "embed-v4.0"
DIMENSIONS = 1024
# Anything that changes how text becomes vectors belongs here, so changing it
# changes every source digest and triggers a re-embed.
PIPELINE = f"{MODEL}|{DIMENSIONS}|semantic-percentile|tiktoken-cl100k-700-50|v1"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_sections(sections, embeddings):
    # SemanticChunker is deliberately used within sections to retain attribution.
    from langchain_experimental.text_splitter import SemanticChunker
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    semantic = SemanticChunker(embeddings, breakpoint_threshold_type="percentile")
    bounded = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base", chunk_size=700, chunk_overlap=50
    )
    result = []
    for section in sections:
        for part in semantic.split_text(section["text"]):
            for text in bounded.split_text(part):
                if text.strip():
                    result.append((section["heading"], section.get("page"), text))
    return result


def sections_from_markdown(path):
    """Split a page-sectioned corpus Markdown file into attributed sections."""
    import re
    sections = []
    for block in re.split(r"^## ", Path(path).read_text(encoding="utf-8"), flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        match = re.fullmatch(r"handbook page (\d+), PDF page (\d+)", title.strip())
        body = body.strip()
        if not match or not body:
            continue  # the reference list and similar non-guidance sections
        first_line = body.splitlines()[0].strip("# ").strip()[:120]
        sections.append({"heading": first_line, "page": f"p. {match.group(1)} (PDF {match.group(2)})", "text": body})
    return sections


def draft_source_from_markdown(path, metadata):
    """Turn a corpus Markdown file into an UNAPPROVED manifest entry.

    A person must review it and set review_status/rights_verified before ingestion.
    """
    return {**metadata, "review_status": "pending", "rights_verified": False, "sections": sections_from_markdown(path)}


def source_digest(source):
    """Digest of the content plus the pipeline signature that turns it into vectors."""
    canonical = json.dumps(source["sections"], sort_keys=True, ensure_ascii=False)
    return digest(PIPELINE + "\n" + canonical)


def is_current(connection, source):
    """True when this exact content is already approved and embedded (nothing to do)."""
    with connection.cursor() as cur:
        cur.execute("""SELECT s.content_hash, s.review_status,
            (SELECT count(*) FROM corpus_chunks c WHERE c.source_id = s.id AND c.review_status='approved')
            FROM corpus_sources s WHERE s.url=%s AND s.version=%s""", (source["url"], source["version"]))
        existing = cur.fetchone()
    return bool(existing and existing[0] == source_digest(source) and existing[1] == "approved" and existing[2] > 0)


def ingest_source(connection, source, embeddings):
    """Prepare embeddings before opening a transaction; publish one complete version."""
    required = ("title", "publisher", "url", "version", "license", "sections")
    if any(not source.get(key) for key in required):
        raise ValueError("source metadata and sections are required")
    if source.get("review_status") != "approved" or not source.get("rights_verified"):
        raise ValueError("source review and rights verification are required")
    source_hash = source_digest(source)
    if is_current(connection, source):
        return 0
    chunks = split_sections(source["sections"], embeddings)
    if not chunks:
        raise ValueError("no content extracted")
    vectors = embeddings.embed_documents([chunk[2] for chunk in chunks])
    if len(vectors) != len(chunks) or any(len(v) != DIMENSIONS for v in vectors):
        raise ValueError("invalid embedding dimensions or count")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""INSERT INTO corpus_sources (title,publisher,url,version,license,content_hash,review_status)
                VALUES (%s,%s,%s,%s,%s,%s,'approved')
                ON CONFLICT (url,version) DO UPDATE SET title=EXCLUDED.title, publisher=EXCLUDED.publisher,
                  license=EXCLUDED.license, content_hash=EXCLUDED.content_hash,
                  review_status='approved', updated_at=now() RETURNING id""",
                (source["title"],source["publisher"],source["url"],source["version"],source["license"],source_hash))
            source_id = cur.fetchone()[0]
            cur.execute("UPDATE corpus_sources SET review_status='rejected', updated_at=now() WHERE url=%s AND id<>%s AND review_status='approved'", (source["url"], source_id))
            cur.execute("DELETE FROM corpus_chunks WHERE source_id=%s", (source_id,))
            for index, ((heading, page, content), vector) in enumerate(zip(chunks, vectors)):
                cur.execute("""INSERT INTO corpus_chunks
                    (source_id,content,section,page_reference,sequence,content_hash,review_status,embedding_model,embedding_version,embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,'approved',%s,%s,%s::vector)""",
                    (source_id,content,heading,page,index,digest(content),MODEL,MODEL,vector_literal(vector)))
    return len(chunks)


def vector_literal(vector):
    import math
    if len(vector) != DIMENSIONS or not all(math.isfinite(float(x)) for x in vector):
        raise ValueError("invalid vector")
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


def main():
    import argparse
    import os
    import psycopg
    from harness.corpus.model import cohere_embeddings
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--draft-from-md", type=Path, help="write a pending manifest entry from this Markdown file instead of ingesting")
    args = parser.parse_args()
    if args.draft_from_md:
        metadata = {"title": "Responding to child maltreatment: a clinical handbook for health professionals",
                    "publisher": "World Health Organization", "url": "https://www.who.int/publications/b/54842",
                    "version": "2022", "license": "CC BY-NC-SA 3.0 IGO; noncommercial use only"}
        draft = draft_source_from_markdown(args.draft_from_md, metadata)
        args.manifest.write_text(json.dumps([draft], indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Drafted {len(draft['sections'])} sections to {args.manifest}; review before approving.")
        return
    sources = json.loads(args.manifest.read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as connection:
        for source in sources:
            count = ingest_source(connection, source, cohere_embeddings())
            print(f"{source['title']}: {count} chunks ingested")


if __name__ == "__main__":
    main()
