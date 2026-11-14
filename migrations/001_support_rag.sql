CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS corpus_sources (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 title text NOT NULL, publisher text NOT NULL, url text NOT NULL,
 version text NOT NULL, license text NOT NULL, content_hash text NOT NULL,
 review_status text NOT NULL CHECK (review_status IN ('pending', 'approved', 'rejected')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (url, version)
);
CREATE TABLE IF NOT EXISTS corpus_chunks (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 source_id bigint NOT NULL REFERENCES corpus_sources(id) ON DELETE CASCADE,
 content text NOT NULL, section text NOT NULL, page_reference text,
 sequence integer NOT NULL, content_hash text NOT NULL,
 review_status text NOT NULL CHECK (review_status IN ('pending', 'approved', 'rejected')),
 embedding_model text NOT NULL, embedding_version text NOT NULL,
 embedding vector(1024) NOT NULL, UNIQUE (source_id, sequence)
);
CREATE INDEX IF NOT EXISTS corpus_chunks_source_status_idx ON corpus_chunks (source_id, review_status);
CREATE INDEX IF NOT EXISTS corpus_chunks_embedding_idx ON corpus_chunks USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS caller_support_memory (
 caller_key text PRIMARY KEY, ciphertext bytea NOT NULL,
 consented_at timestamptz NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS caller_memory_audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, caller_key text NOT NULL,
 action text NOT NULL CHECK (action IN ('consent', 'read', 'update', 'delete')),
 created_at timestamptz NOT NULL DEFAULT now()
);
