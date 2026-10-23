# Reranking A/B — cross-encoder over dense top-100 (2026-07-23)

Stage-1 dense retrieval (each embedder, top-100 from saved vectors) reranked by the open
multilingual cross-encoder **BAAI/bge-reranker-v2-m3**. n=1000 queries, top-100 candidates.
Run on a RunPod 3090 via `rerank_local.py`. Reproducible locally from `embeddings/*.npy`.

## Results (nDCG@10, recall@10 — before → after rerank)

| base embedder | stage-1 nDCG@10 | +rerank nDCG@10 | Δ | stage-1 r@10 | +rerank r@10 |
|---|---:|---:|---:|---:|---:|
| voyage-4-large (commercial) | **0.470** | 0.424 | **−0.046** | 0.626 | 0.562 |
| qwen4b (open, 4B) | 0.404 | 0.416 | +0.012 | 0.551 | 0.546 |
| bge-m3 (open, 0.6B) | 0.351 | 0.409 | +0.058 | 0.478 | 0.533 |
| arctic (open, 0.6B) | 0.342 | 0.388 | +0.047 | 0.483 | 0.502 |

recall@100 is unchanged by reranking (ceiling): voyage-4-large 0.803, qwen4b ~0.72,
bge-m3 ~0.67, arctic 0.681. MRR@100 tracks nDCG@10.

## The finding: the reranker is an equalizer AND a ceiling

- After the **same** cross-encoder, every embedder converges to **~0.39–0.42 nDCG@10**,
  regardless of stage-1 quality (which spanned 0.34–0.47).
- It **lifts cheap open embedders** (bge-m3 +0.058, arctic +0.047) up to ~0.40.
- It **drags down the best embedder** (voyage-4-large 0.470 → 0.424): the 568M reranker is
  *worse* than voyage-4-large's native ordering, so applying it is net-negative.
- Net: **cheap-open + rerank (0.409) ≈ commercial + rerank (0.424)** — the embedder gap
  essentially vanishes once you rerank.

## Implications

1. **For a retrieve→rerank pipeline, the reranker matters more than the embedder.** A cheap,
   durable, self-hosted open embedder + a decent reranker matches a premium embedder + the
   same reranker. Strong support for the open-weights stack (see `woo_eval_results.md`).
2. **A mid-tier reranker caps quality.** bge-reranker-v2-m3 imposes ~0.42 nDCG@10. To go
   higher — and to *exploit* voyage-4-large's better candidate pool (recall@100 0.803, the
   highest) — needs a **stronger reranker** (Qwen3-Reranker-4B, mxbai-rerank-v2, or a
   commercial reranker). **The reranker A/B is the highest-leverage next experiment.**
3. **"Reranking hurt the best embedder" is reranker-specific**, not a general result — it
   only means this reranker is weaker than the best embedders' native ranking.

## Caveats

- **Single-gold scoring** still deflates everything: the reranker may pull an *equally-valid*
  chunk to rank 1 (scored as a miss). So the true rerank benefit is understated — a
  multi-relevant / pooled-judgment eval is needed to measure it properly (see below).
- n=1000 subset (SE ~±1.5pp on recall@10). Not run: voyage-4-lite, voyage-law-2 — closed
  early once the convergence pattern was established (they'd only re-confirm it).
- Reproducible: `python rerank_local.py <base> --n 1000 --topk 100` from saved vectors.
