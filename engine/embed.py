"""Embedding phase: bge-m3 chunk + doc vectors into the Postgres cache.

Settled embedder (design/README.md, embeddings A/B): self-hosted open bge-m3,
plain encode (no instruction prefixes), normalized, 1024-dim. Chunk texts are
encoded directly; the doc-level vector is the L2-normalized mean of the doc's
chunk vectors (recommend mode operates on doc vectors, see-also on chunks).

Cache invalidation is by content hash: re-running `civic embed <source>` only
encodes new/changed text, so OCR-tail upgrades re-embed exactly the upgraded
docs. Requires the `embed` extra (sentence-transformers); import stays lazy so
the rest of the engine never pays for torch.
"""

import hashlib
import os

from engine import blobstore, db
from engine.chunker import DocumentChunker
from engine.retrieval import l2_normalized_mean

MODEL_ID = "BAAI/bge-m3"
DIMENSION = 1024
MAX_SEQ = 2048  # matches the A/B eval setting; chunks are ~750 words
BATCH = 16  # sized for small local GPUs; halved automatically on OOM

_model = None


def model():
    global _model
    if _model is None:
        # A stale stored HF token (~/.cache/huggingface/token) turns public-repo
        # downloads into "Repository Not Found"; bge-m3 is public, so never send it.
        os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
        from sentence_transformers import SentenceTransformer

        # Query-time encodes at fp32 (no torch_dtype) while bulk pod-encoded doc/
        # chunk vectors are stored fp16 (experiments/embed_pod/pod_embed.py, matching
        # the fp16 A/B benchmark). Mixing is intentional and harmless: fp16 vs fp32 of
        # the same bge-m3 vector differ by ~1e-3 cosine, far below the relevance gap.
        try:
            _model = SentenceTransformer(MODEL_ID)
        except Exception as e:
            # GPU busy/full (e.g. a batch embed run owns it): queries fall back
            # to CPU rather than competing for VRAM.
            if "cuda" not in str(e).lower() and "memory" not in str(e).lower():
                raise
            _model = SentenceTransformer(MODEL_ID, device="cpu")
        _model.max_seq_length = MAX_SEQ
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Encode normalized vectors, halving batch size on CUDA OOM."""
    m = model()
    batch = BATCH
    while True:
        try:
            arr = m.encode(
                texts,
                batch_size=batch,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return [row.tolist() for row in arr]
        except RuntimeError as e:
            if "out of memory" in str(e).lower() and batch > 1:
                import torch

                torch.cuda.empty_cache()
                batch = max(1, batch // 2)
            else:
                raise


def embed_query(q: str) -> list[float]:
    return embed_texts([q])[0]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_source(source_id: str, *, encode_batch: int = 64) -> dict[str, int]:
    """Fill the embedding cache for a source's projectable docs. Idempotent."""
    from engine.project.projector import _select_projectable

    with db.tx() as cur:
        rows = _select_projectable(cur, source_id)
        cur.execute(
            "SELECT kind, key, content_hash FROM embeddings "
            "WHERE source_id = %s AND model = %s",
            (source_id, MODEL_ID),
        )
        cached = {(r["kind"], r["key"]): r["content_hash"] for r in cur.fetchall()}

    chunker = DocumentChunker()
    docs: list[dict] = []  # {urn, doc_hash, chunks: [(chunk_id, text, hash)]}
    for row in rows:
        if not row["text_ref"]:
            continue
        path = blobstore.from_ref(row["text_ref"])
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        chunks = chunker.chunk_document(text, row["urn"])
        if not chunks:
            continue
        docs.append(
            {
                "urn": row["urn"],
                "doc_hash": _hash(text),
                "chunks": [(c.chunk_id, c.text, _hash(c.text)) for c in chunks],
            }
        )

    todo = [
        (chunk_id, text, chash)
        for d in docs
        for chunk_id, text, chash in d["chunks"]
        if cached.get(("chunk", chunk_id)) != chash
    ]

    encoded = 0
    for i in range(0, len(todo), encode_batch):
        batch = todo[i : i + encode_batch]
        vectors = embed_texts([text for _, text, _ in batch])
        with db.tx() as cur:
            for (chunk_id, _, chash), vec in zip(batch, vectors):
                db.upsert(
                    cur,
                    "embeddings",
                    {
                        "kind": "chunk", "key": chunk_id, "source_id": source_id,
                        "model": MODEL_ID, "content_hash": chash,
                        "dim": DIMENSION, "vector": vec,
                    },
                    conflict="kind, key, model",
                )
        encoded += len(batch)

    # Doc vectors: normalized mean of the doc's chunk vectors.
    docs_written = 0
    with db.tx() as cur:
        for d in docs:
            if cached.get(("doc", d["urn"])) == d["doc_hash"]:
                continue
            cur.execute(
                "SELECT vector FROM embeddings WHERE kind = 'chunk' AND model = %s "
                "AND key = ANY(%s) ",
                (MODEL_ID, [c[0] for c in d["chunks"]]),
            )
            vectors = [r["vector"] for r in cur.fetchall()]
            if not vectors:
                continue
            db.upsert(
                cur,
                "embeddings",
                {
                    "kind": "doc", "key": d["urn"], "source_id": source_id,
                    "model": MODEL_ID, "content_hash": d["doc_hash"],
                    "dim": DIMENSION, "vector": l2_normalized_mean(vectors),
                },
                conflict="kind, key, model",
            )
            docs_written += 1

    return {
        "docs_seen": len(docs),
        "chunks_encoded": encoded,
        "doc_vectors_written": docs_written,
    }
