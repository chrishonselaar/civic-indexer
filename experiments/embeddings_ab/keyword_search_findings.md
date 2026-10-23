# Short-keyword search eval — BM25 vs dense vs hybrid (2026-07-23)

The embedding/rerank evals used LLM-generated **natural-language questions**. Real users type
**short keywords** (1-4 words). This eval uses the realistic query distribution AND scores
**BM25 + dense + hybrid together** (not dense alone), to de-risk the actual search product.

- Queries: 1,199 keyword queries (mean 3.2 words) generated from 400 gold chunks
  (`gen_keyword_queries.py`), e.g. `zandwinput Ravenswaarden`, `IPCEI groene waterstof`,
  `RWS-2023/21861`, `mondkapjes NVWA`.
- BM25: `rank_bm25` over 42,892 chunks, Dutch analyzer (lowercase + stopwords + snowball stem).
- Dense: bge-m3 (durable open default); doc vectors reused, keyword queries embedded fresh.
- Hybrid: Reciprocal Rank Fusion (RRF, k=60) of BM25 + dense.

## Single-gold (misleading — kept as a lesson)

| method | recall@10 | recall@100 | nDCG@10 |
|---|---:|---:|---:|
| BM25 | 0.414 | 0.637 | 0.283 |
| dense (bge-m3) | 0.314 | 0.520 | 0.215 |
| hybrid-RRF | 0.402 | 0.639 | 0.275 |

Read naively: "BM25 beats dense on keywords; hybrid doesn't help." **Both conclusions are
wrong** — single-gold only credits the one labelled chunk, so it can't see that dense retrieves
*different* relevant docs (semantic variants), nor that BM25+dense are complementary.

## Pooled multi-relevance (the trustworthy result) — 250 queries, Haiku-judged

Avg **21.8 relevant chunks/query**.

| method | success@10 | success@100 | nDCG@10 | recall@100 |
|---|---:|---:|---:|---:|
| BM25 | 0.992 | 0.992 | 0.765 | 0.799 |
| dense (bge-m3) | 0.996 | 1.000 | 0.762 | 0.767 |
| **hybrid-RRF** | **1.000** | **1.000** | **0.822** | **0.981** |

## Findings — this REVERSES the single-gold story

1. **BM25 ≈ dense on keywords under a fair metric** (nDCG 0.765 vs 0.762). Single-gold's
   "BM25 wins big" was an artifact — dense finds different relevant chunks it wasn't credited for.
2. **Hybrid decisively wins** (nDCG 0.822, recall@100 0.981 vs ~0.77-0.80 alone). BM25 (lexical)
   and dense (semantic) are **complementary** — they retrieve largely different relevant docs, so
   fusion captures nearly the whole relevant set. Opposite of the single-gold read.
3. **The embedding channel earns its place in SEARCH, not just recommend.** Dense is a genuine
   complementary recall channel on keyword traffic — dropping it (as single-gold would suggest)
   would materially hurt recall. Validates the settled hybrid BM25+dense (RRF) architecture even
   for the short-query product.
4. **Methodology matters:** single-gold would have driven a *wrong architectural decision*. The
   pooled multi-relevance eval (reusing the Haiku judge) is the honest way to compare retrieval
   on this corpus, where every query has ~20+ valid answers.

## Caveats

- Pooled relevants are drawn from the methods' own top-15 → mildly favors hybrid's recall@100
  (0.981 is optimistic); the complementarity direction is robust.
- Equal-weight RRF, untuned; weighting/learned fusion could shift precision.
- 21.8 rel/query reflects lenient grade-1 judging; success@10 is the robust metric. Haiku judge
  has its own error, applied uniformly across methods.

## Design implications

- **Hybrid BM25 + dense (RRF) is validated for keyword search** — keep both channels. BM25 for
  exact terms/kenmerk/entities, dense for semantic/paraphrase recall; together ~0.98 pooled
  recall@100.
- **Embedding investment pays off in BOTH modes:** complementary recall channel in *search*, and
  primary signal in *related-documents/recommend* (doc-to-doc kNN — the embedding sweet spot).
- Reproducible: `gen_keyword_queries.py` -> `keyword_retrieval.py` -> `keyword_pooled.py`.
