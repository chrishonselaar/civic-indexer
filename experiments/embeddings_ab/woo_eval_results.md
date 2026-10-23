# WOO embedding A/B — results on the real corpus (2026-07-22)

Decision-grade retrieval eval of six embedding models on **real WOO-besluit chunks**,
not MTEB proxies. Converts the MTEB priors in `dutch-mteb-candidates.md` into a measured
call on our own Dutch civic/legal corpus.

## Setup

- **Corpus / pool:** 42,892 substantive chunks (≥150 words, redaction-boilerplate filtered)
  from `wob-search-mcp/es_export/woo-besluiten-chunks.jsonl`. Built by `build_eval_set.py`.
- **Eval set:** 1,000 gold chunks × 3 Dutch synthetic queries = **3,000 queries**, generated
  by Claude Haiku (`gen_queries.py`). **Known-item retrieval**: each query's source chunk is
  the single labelled relevant doc.
- **Metric:** primary **recall@100** (stage-1 retriever's job = get the answer into the
  candidate pool for the reranker). Secondary recall@10/20, nDCG@10, MRR@100.
- **Fairness:** each model uses its own registered query/passage prompt (`query:` for arctic,
  `Instruct: …` for Qwen, `input_type` for Voyage; none for bge-m3). All scored at 1024-dim
  where selectable, inputs truncated ~2048 tokens. **All six re-scored through one code path**
  (`score_local.py`) from saved vectors — so the numbers are guaranteed apples-to-apples, not
  an artifact of the GPU arm vs the Voyage arm.

## Results (unified scorer, from saved vectors)

| Rank | Model | Type | dim | r@10 | r@20 | **r@100** | nDCG@10 | MRR |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | voyage-4-large | commercial | 1024 | 0.598 | 0.661 | **0.783** | 0.448 | 0.408 |
| 2 | voyage-4-lite | commercial | 1024 | 0.554 | 0.621 | **0.750** | 0.404 | 0.365 |
| 3 | voyage-law-2 | commercial (legal) | 1024 | 0.521 | 0.593 | **0.728** | 0.378 | 0.342 |
| 4 | **Qwen3-Embedding-4B** | **open, 2025** | 2560 | 0.505 | 0.576 | **0.720** | 0.372 | 0.339 |
| 5 | bge-m3 | open, 2024 | 1024 | 0.445 | 0.515 | **0.674** | 0.318 | 0.287 |
| 6 | arctic-embed-l-v2.0 | open, 2024 | 1024 | 0.449 | 0.515 | **0.659** | 0.317 | 0.283 |

## The raw numbers massively understate true recall (single-gold artifact)

Known-item scoring marks exactly ONE chunk gold per query, but WOO documents repeat
boilerplate (bezwaar clauses, art. 5.1 exceptions, redaction rationales) and many queries
have several equally-valid answers. A Haiku judge (`miss_analysis.py`) categorized each
top-100 miss as *artifact* (an equally-valid chunk was retrieved) vs *true* failure:

| Model | raw r@100 | true-miss share | **artifact-corrected r@100** |
|---|---:|---:|---:|
| voyage-4-large (commercial) | 0.783 | 20% | **~0.96** |
| voyage-law-2 | 0.728 | 23% | **~0.94** |
| voyage-4-lite | 0.749 | 33% | **~0.92** |
| bge-m3 (open cheap) | 0.673 | 27% | **~0.91** |
| qwen4b (open 4B) | 0.720 | 35% | **~0.90** |
| arctic (open cheap) | 0.659 | 42% | **~0.86** |

**~3 of every 4 "misses" are the model finding a correct chunk, just not the labelled one**
(60-miss Haiku-judged sample each). True effective recall@100 is ~0.86–0.96, not 0.66–0.78 —
and the corrected numbers **converge** (five of six in ~0.90–0.96). Two real signals survive
the artifact: arctic is genuinely weakest (42% true misses), voyage-4-large genuinely
strongest (20%); the middle four are a statistical tie (±~6–10pp at n=60).

## Cost: self-hosted open vs Voyage API

From measured throughput (bge-m3 = 37K tok/s on a $0.22/hr RTX 3090):

| | $/1M tokens | $ per 1M chunks (~970 tok) |
|---|---:|---:|
| **bge-m3 (self-hosted 3090)** | **$0.0017** | **~$1.60** |
| voyage-4-lite | $0.02 | ~$19 |
| voyage-law-2 / voyage-4-large | $0.12 | ~$116 |

Self-hosted bge-m3 is ~12× cheaper than voyage-4-lite, ~70× cheaper than voyage-4-large per
token. Caveats: Voyage's 200M-token free tier makes it $0 at small scale (the gap only bites
at scale); self-hosting has an always-on query-side serving cost (Voyage's pay-per-query can
win at low query volume). The self-host advantage is clearest for **bulk document embedding**.

## Reranking changes the picture — the reranker is the real lever

Full detail in `rerank_results.md`. Stage-1 dense top-100 reranked by open cross-encoder
**bge-reranker-v2-m3** (n=1000). nDCG@10 before → after:

| base embedder | stage-1 | +rerank | Δ |
|---|---:|---:|---:|
| voyage-4-large | **0.470** | 0.424 | **−0.046** ⬇ |
| qwen4b | 0.404 | 0.416 | +0.012 |
| bge-m3 | 0.351 | 0.409 | +0.058 ⬆ |
| arctic | 0.342 | 0.388 | +0.047 ⬆ |

**The reranker equalizes AND caps — but only on the ranking metric, not recall.** Two
distinct gaps behave differently:
- **nDCG@10 / recall@10 (ordering within the pool):** all embedders converge to ~0.39–0.42
  after the same reranker — it lifts the cheap open embedders and *drags down* voyage-4-large
  (its 568M ordering is worse than voyage-4-large's native one). Here the gap closes.
- **recall@100 (is the answer in the pool at all):** set by the embedder, **unchanged by
  reranking**. voyage-4-large keeps its lead (0.803 raw / ~0.96 corrected vs bge-m3
  ~0.674 / ~0.91). For queries where the cheap embedder missed the pool, **no reranker
  recovers them** — a residual, embedder-bound advantage.

So precisely: **for queries where both embedders got the answer into the top-100, cheap-open +
rerank ≈ commercial + rerank**; commercial still wins the extra ~5pp (corrected) of queries
where only it reached the pool. The end-to-end gap is much smaller than stage-1 suggested but
NOT zero. Consequence: **for a retrieve→rerank pipeline the reranker dominates final ranking
quality** — strong support for the cheap durable open stack — while the embedder still owns the
recall ceiling. This mid reranker caps nDCG@10 at ~0.42; a stronger reranker (Qwen3-Reranker,
mxbai-rerank-v2, commercial) is the highest-leverage next experiment and could exploit
voyage-4-large's higher recall@100 pool.

## Pooled multi-relevance eval — the artifact-free numbers (250 queries)

Ran the fix below (option 1): pooled top-15 across all 6 models, Haiku graded each candidate
0/1/2, built multi-relevant qrels. **Avg 21.7 relevant chunks/query** (vs 1 under single-gold
— the artifact was even bigger than the 60-miss estimate suggested). `pooled_eval.py`,
`pooled_eval_results.md`, `pooled_qrels.json`.

| Model | success@10 | nDCG@10 pooled | nDCG@10 single-gold | recall@100 pooled |
|---|---:|---:|---:|---:|
| voyage-4-large | 0.996 | **0.612** | 0.450 | 0.814 |
| voyage-4-lite | 0.968 | 0.559 | 0.394 | 0.801 |
| qwen4b | 0.964 | 0.557 | 0.341 | 0.779 |
| voyage-law-2 | 0.972 | 0.545 | 0.373 | 0.732 |
| bge-m3 | 0.972 | 0.532 | 0.298 | 0.733 |
| arctic | 0.948 | 0.436 | 0.285 | 0.673 |

- **"Relevant answer in top-10?" is essentially solved for all — success@10 ≈ 0.95–1.0**,
  cheap open included (bge-m3 0.972). The raw recall@10 ~0.45 was almost pure artifact.
- **nDCG@10 ~doubled** vs single-gold once equally-valid chunks count (bge-m3 0.298→0.532).
- Ranking preserved but compressed: voyage-4-large still leads (0.612 / 0.814); the commercial
  premium over cheap bge-m3 is modest and precise — **~0.08 nDCG@10, ~0.08 recall@100, ~2.4pp
  success@10.**
- Caveats: pooling top-15/model makes recall@100-pooled slightly optimistic (relevants outside
  every model's top-15 go unjudged); 21.7 rel/query partly reflects lenient grade-1 judging —
  success@10 is the robust metric. Haiku judge has its own error, applied uniformly.

**Bottom line:** artifact-free, a cheap durable open embedder gets a relevant answer into the
top-10 ~97% of the time; commercial adds a real-but-modest premium. With reranking (nDCG
converges) + ~12–70× lower cost + no lock-in, the open stack is the justified durable default.

## Fixing the single-gold artifact (the metric's main weakness)

Single-gold scoring credits only the labelled chunk, so equally-valid retrievals score as
misses — deflating BOTH the embedding and reranking numbers. Proper fixes, by rigor:
1. **Pooled multi-relevance judgments (TREC-style)** — pool top-K across all models, LLM-judge
   each for relevance (reuse `miss_analysis.py`'s Haiku judge), build multi-relevant qrels,
   re-score. Dissolves the artifact for embeddings AND reranking at once. *Recommended next.*
2. **De-duplicate boilerplate** (cluster near-identical chunks; treat the cluster as gold).
3. **Uniquely-answerable query generation** (force a distinguishing detail per query).
4. **Real human-judged queries** (gold standard; small calibration set).

## Findings

1. **Open weights are competitive at the 4B tier.** Qwen3-Embedding-4B (0.720) closes most
   of the gap: +0.05 over the 2024 open cheap tier, within 0.03 of voyage-4-lite. The large
   apparent open↔commercial gap is mostly a **2024-0.6B-vs-2025-frontier mismatch**; against a
   fair opponent it's a modest frontier premium.
2. **The commercial edge is real but modest.** In artifact-corrected/true terms, voyage-4-large
   ~halves the true-miss rate (~9%→~4%) vs bge-m3 — a genuine capability edge, but both are
   already high (~0.91 vs ~0.96), and BM25-hybrid + cross-encoder rerank shrink the practical
   difference further.
3. **Domain-specialization lost to recency, again.** voyage-**law**-2 (0.728) is beaten by the
   general voyage-4-lite (0.750) and barely edges open Qwen3-4B (0.720) — the same pattern as
   `Euler-Legal` on MTEB. A general frontier model > an older legal-tuned one on our legal text.
4. **The cheap-tier pair is a statistical tie** (bge-m3 0.674 ≈ arctic 0.659); bge-m3 slightly
   ahead on recall. arctic's small MTEB edge does not carry to our corpus.
5. **Commercial ranks higher, not just present.** The commercial lead widens at r@10 (0.60 vs
   0.45) — they order the answer better, which is exactly what our rerank stage is for.

## Durability caveat — why the winner is not simply "buy voyage-4-large"

A proprietary embedding index is only queryable by the same model; if the vendor deprecates it,
new queries can't be embedded into that space and the **whole stored index becomes unqueryable**
until a full re-embed. For a civic archive meant to persist, that couples queryability to a
vendor roadmap. The project principle already covers this — **Postgres = ground truth, vector
index = rebuildable projection** — so the durable asset is the chunk text; but **open weights are
the only option you control on your own schedule** (your escrow/pinning/support-window for free).
Commercial models are best treated as a **benchmark ceiling** and an optional per-dataset
accelerator, not the durable default.

## Recommendation

- **Durable default: open weights.** bge-m3 or arctic for the bulk/cheap tier (both ~0.91
  effective recall@100 — ample for stage 1 of a BM25-hybrid + rerank pipeline; Apache/MIT).
  **Qwen3-Embedding-4B** where quality matters more and the 4B cost (dim 2560, ~3.3 pass/s/GPU
  vs ~38 for the 0.6B encoders) is acceptable — e.g. summary-level embeddings.
- **Commercial (Voyage-4-large/lite): benchmark + optional accelerator only**, eyes open on
  re-embed/lock-in exposure; never the sole retrievability dependency for the archive.
- **The reranker A/B is the higher-leverage next experiment** — the r@10 gap and the fact that
  ~0.91–0.96 of answers are already in the top-100 means precision is recovered downstream, not
  by paying embedder lock-in.

## Caveats

- Synthetic queries (Haiku) are somewhat optimistic and self-correlated; absolute numbers run
  high after correction. The **model-to-model comparison** is the reliable output (identical
  queries for all).
- Artifact-corrected recall extrapolates a 60-miss Haiku-judged sample to all misses; it's an
  estimate (±), and the judge has its own error. Directionally robust, corroborated by manual
  spot-checks (see conversation log).
- Dense-only, single-vector. Real system is BM25 + dense → cross-encoder rerank with
  multi-relevant judgments; production recall will differ (higher).

## Artifacts (all local, reproducible)

- `embeddings/<key>.doc.f16.npy` + `.q.f16.npy` — **saved vectors for all 6 models** (pool +
  queries). Re-run any ranking/fusion/reranker experiment locally with **zero re-embedding**.
- `score_local.py` — unified scorer (the one code path). `unified_scores.{md,json}` — the board.
- `results_<key>.json` / `results_<key>_ranks.jsonl` — per-run metrics + per-query top-20 dumps.
- `miss_analysis.py` + `miss_analysis_<key>.md` — artifact-corrected recall.
- `build_eval_set.py`, `gen_queries.py` (Haiku), `embed_eval.py` (GPU arm), `voyage_eval.py`
  (API arm). `slim_pool.jsonl` regenerable from the source export.

**Cost:** one RTX 3090 (~6 GPU-h, ~$1.3 — arctic/bge/qwen, re-run once to save vectors) +
Voyage within free tier (~$0). Pod torn down.
