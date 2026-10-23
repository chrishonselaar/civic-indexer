"""Retrieval math + rerank client: RRF fusion, doc-vector pooling, Voyage rerank.

Settled decisions (design/README.md): hybrid BM25 + dense fused with RRF
(pooled recall@100 0.80 each alone -> 0.98 fused), cross-encoder rerank of the
hybrid top-100 via the Voyage rerank-2.5 API (+0.187 nDCG@10 over bge-m3
alone, ~$0.002/search, no fixed GPU). Pure-python on purpose: importable
without the `embed` extra installed.
"""

import math
import os

import requests

RRF_K = 60  # standard constant; equal-weight fusion (weighted fusion is a later experiment)
RERANK_MODEL = "rerank-2.5"
RERANK_URL = "https://api.voyageai.com/v1/rerank"
RERANK_DOC_CHARS = 4000  # rerank on the head of each doc, not megabytes of OCR text


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal-rank fusion over ranked id lists. Returns (id, score) best-first.

    Ties broken by first-list (BM25) order so results are deterministic.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            if doc_id not in first_seen:
                first_seen[doc_id] = order
                order += 1
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))


def l2_normalized_mean(vectors: list[list[float]]) -> list[float]:
    """Doc-level vector = L2-normalized mean of its chunk vectors."""
    if not vectors:
        raise ValueError("no vectors to pool")
    dim = len(vectors[0])
    mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in mean))
    if norm == 0:
        return mean
    return [x / norm for x in mean]


def rerank_available() -> bool:
    return bool(os.environ.get("VOYAGE_API_KEY"))


def voyage_rerank(
    query: str, documents: list[str], *, model: str = RERANK_MODEL, timeout: int = 30
) -> list[tuple[int, float]]:
    """Rerank documents against query. Returns (input_index, relevance) best-first."""
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY not set")
    resp = requests.post(
        RERANK_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "query": query,
            "documents": [d[:RERANK_DOC_CHARS] for d in documents],
            "model": model,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [(r["index"], r["relevance_score"]) for r in data]
