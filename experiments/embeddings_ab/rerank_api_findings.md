# Reranker A/B — managed APIs (Voyage / Cohere) over stage-1, pooled scoring (2026-07-23)

Follow-up to `rerank_results.md` (which used a mid open reranker + single-gold scoring and
concluded "the reranker is just an equalizer capped at ~0.42 nDCG@10"). This run tests the
**production-relevant** setup — self-hosted `bge-m3` stage-1 → **managed rerank API** — and
scores with **pooled multi-relevance** (the fair metric), re-judging every doc the rerankers
promote into their top-10.

## Setup
- n=250 judged queries; stage-1 = top-100 dense candidates from saved fp16 vectors.
- Rerankers: **Voyage `rerank-2.5`**, **Cohere `rerank-v3.5`** (both multilingual, API — no GPU).
- Bases: `bgem3` (self-hosted, the production embedder) + `voyage-4-large` (best stage-1 pool).
- **Fairness fix:** `pooled_qrels_v2.json` re-judged 1,689 reranker-promoted docs → avg
  **25.7 relevant chunks/query** (was 21.7). Without this, rerankers eat unfair grade-0s.
- Scripts: `rerank_prep.py` → `rerank_api.py` → `rerank_repool.py` → `rerank_score.py`.

## Results (pooled)

| method | success@10 | **nDCG@10 (pooled)** | recall@10 | nDCG@10 (single-gold) |
|---|---:|---:|---:|---:|
| voyage-4-large + **Voyage** | 0.996 | **0.727** | 0.426 | 0.470 |
| **bgem3 + Voyage**  *(production stack)* | 0.992 | **0.702** | 0.401 | 0.434 |
| bgem3 + Cohere | 0.980 | 0.620 | 0.362 | 0.417 |
| voyage-4-large · stage-1 (no rerank) | 0.996 | 0.591 | 0.353 | 0.450 |
| bgem3 · stage-1 (no rerank) | 0.972 | 0.515 | 0.307 | 0.298 |

## Findings

1. **Strong rerankers lift quality a lot — the old "equalizer/ceiling" was an artifact.**
   `bge-m3` stage-1 goes **0.515 → 0.702** with Voyage (**+0.187 nDCG@10**), +0.105 with
   Cohere. The earlier "+rerank barely moves / even hurts" result was caused by (a) a weak
   568M reranker and (b) single-gold scoring — *not* a real ceiling. Reranking is decisively
   worth it.

2. **Voyage `rerank-2.5` clearly beats Cohere `rerank-v3.5`** on this Dutch corpus, same base:
   **0.702 vs 0.620** over `bge-m3`. Voyage is the reranker to deploy.

3. **The production stack is validated.** Self-hosted `bge-m3` embeddings + Voyage rerank API
   = **0.702**, within **0.025** of the best-embedder base (voyage-4-large + Voyage, 0.727).
   The reranker matters far more than the embedder — a cheap, self-hosted, durable embedder +
   a managed reranker gets ~97% of the way to a premium embedder base. Runs entirely on the
   CPU-only VPS + an API call; **no fixed GPU cost** (~$0.002/search).

4. **A better stage-1 pool gives a modest edge** (voyage-4-large base +0.025 over bge-m3 post
   rerank) — the embedder still owns the recall ceiling, but the gap is small and not worth
   the vendor lock-in / cost for the production default.

5. **success@10 ≈ 1.0 everywhere** (a relevant chunk is almost always in the top-10); the real
   differentiation is *ordering*, which is exactly what nDCG@10 captures and where rerank wins.

## Caveats
- n=250 (SE on nDCG@10 ~±1.5pp) — the Voyage>Cohere and rerank>>no-rerank gaps are well
  outside noise; the bge-m3 vs voyage-4-large base gap (0.025) is near it.
- Pooled scoring re-judges rerankers' promoted docs (fair), but relevants are still pooled
  from methods' own top-K → recall@10 is pool-limited (~25.7 relevant/query can't fit in 10).
- Dropped `voyage-4-large + Cohere` (redundant secondary; Cohere trial-key throttled to ~10
  req/min). Not run: open self-hosted rerankers (Qwen3-Reranker-4B, mxbai-v2) as a ceiling
  anchor — `rerank_pod.py` is ready if we want to check whether an open model beats the APIs.
