# Embeddings & retrieval — full findings (session 2026-07-22/23)

Master synthesis of the embedding / reranking / hybrid-search investigation on the **real WOO
corpus** (42,892 chunks from `wob-search-mcp/es_export/woo-besluiten-chunks.jsonl`). Detail
docs linked per section. All results reproducible from checked-in scripts + saved vectors.

**TL;DR for the product:**
- **Hybrid BM25 + dense (RRF) is the search backbone** — validated even for short keyword
  queries: BM25 and dense are complementary (pooled recall@100 0.80 each alone → **0.98
  together**). Keep both channels.
- **Use a cheap, durable, self-hosted OPEN embedder (bge-m3)** — commercial (Voyage) is only a
  modest premium and brings vendor lock-in. ~12–70× cheaper self-hosted.
- **Reranker matters more than the embedder** for final top-10 ordering, but a *mid* reranker
  caps quality; a stronger one is the next experiment.
- **"Related documents / recommend" (doc-to-doc kNN) is the embedding sweet spot** — add it as a
  second mode alongside search.
- **Single-gold metrics lie on this corpus** (~22 valid answers/query); use pooled
  multi-relevance judging.

---

## 1. Embedding retrieval quality — 6-model board

Known-item retrieval, 3,000 LLM-generated Dutch questions, all re-scored through one code path
(`score_local.py`) from saved vectors. Detail: [`woo_eval_results.md`](woo_eval_results.md),
[`unified_scores.md`](unified_scores.md).

| Model | type | recall@100 | nDCG@10 | corrected recall@100* |
|---|---|---:|---:|---:|
| voyage-4-large | commercial | 0.783 | 0.448 | ~0.96 |
| voyage-4-lite | commercial | 0.750 | 0.404 | ~0.92 |
| voyage-law-2 | commercial (legal) | 0.728 | 0.378 | ~0.94 |
| Qwen3-Embedding-4B | open, 2025 | 0.720 | 0.373 | ~0.90 |
| bge-m3 | open, 2024, 0.6B | 0.674 | 0.318 | ~0.91 |
| arctic-embed-l-v2.0 | open, 2024, 0.6B | 0.659 | 0.317 | ~0.86 |

*artifact-corrected (see §2). Findings:
- **Open weights competitive at 4B** — Qwen3-4B within 0.03 of voyage-4-lite. The large
  open↔commercial gap was mostly a 2024-0.6B-vs-2025-frontier mismatch.
- **Domain model lost to recency** — voyage-**law**-2 (legal-tuned) beaten by general
  voyage-4-lite. Same pattern as Euler-Legal on MTEB (see
  [`dutch-mteb-candidates.md`](dutch-mteb-candidates.md)).
- **Cheap open pair is a tie** (bge-m3 ≈ arctic; bge-m3 slightly ahead).

## 2. The single-gold artifact — raw metrics massively understate truth

Detail: [`miss_analysis_*.md`](.), [`pooled_eval_results.md`](pooled_eval_results.md).

Known-item scoring labels ONE gold chunk/query, but WOO documents repeat boilerplate and most
queries have many valid answers. **Pooled multi-relevance judging (Haiku, TREC-style) found avg
21.7 relevant chunks/query, not 1.** Two independent measurements:

- **Miss-analysis** (judge each top-100 miss): 58–80% of "misses" are equally-valid chunks →
  artifact-corrected recall@100 **~0.86–0.96** (converges; arctic weakest at ~0.86,
  voyage-4-large best ~0.96).
- **Pooled eval** (250 q, judge pooled candidates, graded 0/1/2): artifact-free
  **success@10 ≈ 0.95–1.0 for ALL models** (bge-m3 0.972); nDCG@10 ~doubles vs single-gold.

| Model | success@10 | nDCG@10 pooled | recall@100 pooled |
|---|---:|---:|---:|
| voyage-4-large | 0.996 | 0.612 | 0.814 |
| voyage-4-lite | 0.968 | 0.559 | 0.801 |
| qwen4b | 0.964 | 0.557 | 0.779 |
| voyage-law-2 | 0.972 | 0.545 | 0.732 |
| bge-m3 | 0.972 | 0.532 | 0.733 |
| arctic | 0.948 | 0.436 | 0.673 |

**Net:** a cheap open embedder surfaces a relevant answer in the top-10 ~97% of the time;
commercial adds a modest premium (~0.08 nDCG@10, ~0.08 recall@100, ~2.4pp success@10).

## 3. Cost — self-hosted open vs Voyage

Measured (bge-m3 = 37K tok/s on a $0.22/hr RTX 3090):

| | $/1M tokens | $ per 1M chunks (~970 tok) |
|---|---:|---:|
| bge-m3 (self-hosted 3090) | $0.0017 | ~$1.60 |
| voyage-4-lite | $0.02 | ~$19 |
| voyage-law-2 / voyage-4-large | $0.12 | ~$116 |

Self-hosted open is **~12× cheaper than voyage-4-lite, ~70× than voyage-4-large**. Voyage's
200M-token free tier makes it $0 at small scale; the gap bites at bulk scale. Self-host wins
clearest for bulk document embedding; Voyage's pay-per-query can win at very low query volume.

## 4. Durability / vendor lock-in — why open is the default

A proprietary embedding index is only queryable by the same model; if Voyage deprecates it, new
queries can't be embedded into that space and **the whole index is unqueryable until a full
re-embed**, on the vendor's schedule. The project principle (Postgres = truth, vector index =
rebuildable projection) covers data safety, but **open weights are the only option you control on
your own schedule** — free escrow/pinning/support-window. At scale, big players mitigate with
contracts/pinning/on-prem/escrow (leverage a small civic project lacks) → open weights are that
leverage, for free. **Commercial = benchmark ceiling + optional per-dataset accelerator, never
the sole retrievability dependency.**

## 5. Reranking — the reranker matters more than the embedder

Detail: [`rerank_results.md`](rerank_results.md). Dense top-100 reranked by open cross-encoder
bge-reranker-v2-m3, n=1000. nDCG@10 before→after:

| base embedder | stage-1 | +rerank | Δ |
|---|---:|---:|---:|
| voyage-4-large | 0.470 | 0.424 | −0.046 ⬇ |
| qwen4b | 0.404 | 0.416 | +0.012 |
| bge-m3 | 0.351 | 0.409 | +0.058 ⬆ |
| arctic | 0.342 | 0.388 | +0.047 ⬆ |

- **All embedders converge to ~0.39–0.42 nDCG@10** — the reranker equalizes AND caps ranking
  quality. It lifts cheap open embedders and *drags down* voyage-4-large (its 568M ordering is
  worse than voyage's native).
- **Two distinct gaps:** reranking closes the **nDCG@10/recall@10** gap but NEVER the
  **recall@100** gap (set by the embedder — voyage-4-large keeps its 0.803 pool-coverage lead;
  answers only it retrieved stay lost to cheaper embedders).
- **Consequence:** for a retrieve→rerank pipeline the reranker dominates final ordering — strong
  support for the cheap durable open embedder + a good reranker. The mid reranker caps nDCG@10 at
  ~0.42; **a stronger reranker (Qwen3-Reranker-4B, mxbai-rerank-v2) is the highest-leverage next
  experiment** and could exploit voyage-4-large's higher recall@100 pool.

## 6. Short-keyword search — BM25 vs dense vs hybrid (the product-realistic eval)

Detail: [`keyword_search_findings.md`](keyword_search_findings.md). The evals above used
natural-language *questions*; real users type *short keywords* (1–4 words). 1,199 keyword queries
(mean 3.2 words), scored across all three retrieval methods.

**Pooled multi-relevance (250 q, artifact-free):**

| method | success@10 | nDCG@10 | recall@100 |
|---|---:|---:|---:|
| BM25 | 0.992 | 0.765 | 0.799 |
| dense (bge-m3) | 0.996 | 0.762 | 0.767 |
| **hybrid-RRF** | **1.000** | **0.822** | **0.981** |

- **BM25 ≈ dense on keywords** under a fair metric (single-gold wrongly made BM25 look far
  better — it couldn't credit the *different* relevant docs dense finds).
- **Hybrid decisively wins** — BM25 (lexical) and dense (semantic) are **complementary**, each
  retrieving ~0.80 of the relevant set but different subsets, so fusion reaches ~0.98.
- **Dense earns its place in SEARCH, not just recommend** — dropping it (as single-gold implied)
  would materially hurt recall on the real query distribution.

## 7. Hybrid's actual use case (why "always use hybrid" is right here)

Hybrid isn't for a special query type — it's for the large slice of *normal* searching where the
user's words don't literally appear in the relevant document:
- **Exact-term** queries (kenmerk, cites, proper nouns) → BM25 wins, dense adds nothing.
- **Vocabulary-mismatch** queries → semantic wins: synonyms (`mondkapjes`↔`gezichtsmaskers`),
  lay-vs-bureaucratic (`uitkering`↔`bijstand`↔`Participatiewet`), concept-vs-keyword
  (`steun boeren stikstof`↔`PAS-melders`/`stikstofdepositie`), Dutch compounds, typos.

BM25 **silently** misses vocabulary-mismatch docs (you never see what you didn't retrieve). Civic
search has an unusually large vocabulary gap (plain-language users, governmentese documents, many
official names) and a **recall-oriented UX** ("show all matching"), so hybrid's value is high.
Pure-navigational lookups (known kenmerk/title) are the only place BM25 alone suffices.

## 8. Recommend / related-documents — the embedding sweet spot (proposed second mode)

Everything that made embeddings awkward for keyword search makes them ideal for **doc-to-doc
similarity** ("related documents", "similar cases", "other decisions on this topic"):
- Query is a full document (dense's strength), doc↔doc is symmetric (native), BM25 can't do
  semantic doc-similarity, and the multi-relevant "cluster" that hurt the search metric IS the
  product. **The saved doc vectors already ARE the recommendation index** (kNN = recommend).
- Maps to the settled design decision "summary-level embeddings second; chunk embeddings opt-in":
  **document-level vector → related documents; chunk-level → related passages / see-also.**
- Arguably higher value than keyword search for the journalist/researcher persona (following a
  thread across dossiers, ministries, time). Open weights still the pick; doc-to-doc is where
  "cheap open is adequate" is most likely to hold.
- Cheap to validate (no new embedding) — a nearest-neighbor demo + relatedness judging is the
  suggested next step.

## 9. Design recommendations for the search tool

1. **Search = hybrid BM25 + dense (RRF), + facets.** BM25 for exact terms/kenmerk/entities;
   dense (bge-m3) for semantic/paraphrase recall; RRF fuse; facets (ministry/date/type) for
   narrowing. `wob-search-mcp` already does BM25+kNN+RRF — swap in bge-m3.
2. **Embedder = self-hosted open bge-m3** (durable, cheap, adequate). Not commercial.
3. **Reranker = optional page-1 sort** for the show-all UX; if added, invest in a strong one
   (separate A/B). Not gating for launch.
4. **LLM query interpretation/expansion** is the highest-value smarts for short queries (expand a
   keyword into a richer semantic query for the dense channel; detect kenmerk → exact lookup).
5. **Add a related-documents/recommend mode** (doc-to-doc kNN on the same vectors) — the
   embedding sweet spot, high value for research users.

## 10. Methodology & process notes

- **Pooled multi-relevance judging is essential on this corpus.** Single-gold would have driven
  ≥2 wrong conclusions (cheap embedders "fail" at 0.66; "drop dense for keyword search"). Reuse
  `miss_analysis.py` / `pooled_eval.py` / `keyword_pooled.py` (Haiku judge) for any future eval.
- **Caveats on the pooled numbers:** relevants pooled from methods' own top-K mildly favor
  hybrid's recall@100 (0.98 optimistic; complementarity robust); ~22 rel/query reflects lenient
  grade-1 judging (success@10 is the robust metric); Haiku judge has its own error (uniform).
- **Process lessons (cost real time this session):** `pkill -f` silently failed twice (orphan
  processes burned CPU/GPU) → **kill by explicit PID and verify with `ps`**; heredoc-over-ssh
  rewrites silently didn't persist → **scp scripts instead**; **verify scp downloads BEFORE
  tearing down a pod** (a glob-quoting bug + premature teardown nearly lost rerank results —
  recovered from run output + saved vectors).

## 11. Artifacts (all local + committed; vectors local-only, gitignored)

- **Vectors (6 models, doc+query):** `embeddings/<key>.{doc,q}.f16.npy` (~700MB, LOCAL ONLY).
  Re-score/rerank/fuse locally with ZERO re-embedding.
- **Scripts:** `build_eval_set.py`, `gen_queries.py`, `gen_keyword_queries.py`, `embed_eval.py`
  (GPU), `voyage_eval.py` (API), `score_local.py` (unified scorer), `rerank_local.py`,
  `miss_analysis.py`, `pooled_eval.py`, `keyword_retrieval.py`, `keyword_pooled.py`.
- **Results:** `woo_eval_results.md`, `unified_scores.{md,json}`, `rerank_results.md`,
  `pooled_eval_results.{md,json}`, `keyword_search_findings.md`,
  `keyword_pooled_results.{md,json}`, `miss_analysis_*.md`, reusable `pooled_qrels.json`.
- **Priors:** `dutch-mteb-candidates.md` (MTEB(nld) analysis) + CSVs.

## 12. Next experiments (ordered)

1. **Stronger cross-encoder reranker A/B** (Qwen3-Reranker-4B, mxbai-rerank-v2) — highest
   leverage; the mid reranker caps nDCG@10 at ~0.42.
2. **Related-documents/recommend** demo + eval (doc-to-doc kNN; relatedness judging).
3. **Weighted / learned hybrid fusion** (equal-weight RRF is untuned).
4. **LLM query-expansion** for short queries (lift the dense channel).
5. Cost/throughput at scale for the chosen embedder on the target burst GPU (fill the burst
   divisor `TODO(probe)`).
