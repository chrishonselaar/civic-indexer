-- Embedding cache. Vectors are derived artifacts like chunks
-- (envelope-desk-checks.md Q5) but expensive to recompute, so they are cached
-- here keyed by content hash; the OpenSearch projection stays freely
-- rebuildable. Keys are urns (kind='doc') or chunk_ids (kind='chunk').
CREATE TABLE embeddings (
    kind         text NOT NULL CHECK (kind IN ('doc', 'chunk')),
    key          text NOT NULL,
    source_id    text NOT NULL,
    model        text NOT NULL,
    content_hash text NOT NULL,
    dim          int  NOT NULL,
    vector       real[] NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, key, model)
);

CREATE INDEX embeddings_by_source ON embeddings (source_id, model, kind);
