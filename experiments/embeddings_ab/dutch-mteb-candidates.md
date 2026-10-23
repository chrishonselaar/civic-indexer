# Dutch embedding candidates — MTEB(nld, v1) analysis

Evidence base for the eventual embeddings A/B (parallel to `../ocr_ab/`). The design's
embedding decision (`design/ocr-strategy.md` → "Embeddings ride the same pattern") assumed
a **BGE-M3 / multilingual-e5 (~0.5B)** class model. This benchmark lets us pick with data.

**Source:** MTEB Dutch benchmark, <https://huggingface.co/spaces/mteb/leaderboard> →
Benchmarks → Dutch (`MTEB(nld, v1)`: 40 tasks, 7 task types, 236 models). Full CSVs are
checked in here (`MTEB_nld_v1_summary.csv`, `MTEB_nld_v1_per_task.csv`, snapshot
2026-07-22); regenerate the tables below with `python3 analyze_mteb.py`.

## What we actually care about

Our product is **search over Dutch civic/government/legal documents**, so the
**Retrieval** task type — not the overall mean — is the decision metric. Four per-task
retrieval columns are near-exact proxies for our workload; the "Civic" figures below are
their mean:

- `bBSARDNLRetrieval` — Belgian statutory-article legal retrieval (hardest; 10–43 range)
- `LegalQANLRetrieval` — Dutch legal Q&A retrieval
- `OpenTenderRetrieval` — government procurement/tender retrieval
- `DutchNewsArticlesRetrieval` — news retrieval

## How to read these rankings for a legal retrieve→rerank pipeline

The leaderboard is easy to misread for our use case. Five rules:

**1. The scores are nDCG@10 × 100, NOT accuracy or %.** nDCG@10 grades how well the top-10
*ranking* matches the ideal, position-discounted — a right doc at rank 2 already loses
points. So low-to-mid **60s is strong**, not mediocre: leading systems rarely average above
~65–70 nDCG@10 even in English. A "62" is not "62% correct."

**2. The aggregate hides a huge difficulty spread — read the per-task legal columns, not
the mean.** For one model the tasks range from `BelebeleRetrieval` ~90s (easy) to
`bBSARDNL` legal statutes ~24–42 (brutal). The tasks closest to us (legal) sit in the
20s–40s **for everyone** — that's the nature of dense retrieval over dense legal text, not a
bad model. Rank candidates by the **Retrieval task type**, then break ties on **bBSARDNL +
LegalQANL** specifically. **Ignore the headline overall rank / Borda / Mean(Task)** — it
folds in Classification, Clustering, STS, PairClassification tasks we don't serve.

**3. Our embedding model is STAGE ONE — optimize it for recall, not nDCG@10.** In a
retrieve→rerank pipeline the bi-encoder only has to pull relevant docs into a top-k
candidate pool (k≈100–200); a cross-encoder fixes the ordering afterward. **Recall@100 ≫
nDCG@10**, and the model-to-model gap shrinks further at recall@100 — which is *another*
reason the flat 62→67 curve says "a cheap encoder is fine for stage 1." Pick the cheapest
model that clears a recall bar on **our** legal corpus, not the highest nDCG@10.

**4. The `Reranking` column here is NOT your pipeline reranker — don't pick a reranker from
this board.** MTEB "Reranking" measures a *bi-encoder* reordering candidates by cosine
similarity. A real rerank stage is a **cross-encoder** (query+doc scored jointly), a
different model class this leaderboard doesn't rank — e.g. `bge-reranker-v2-m3`,
`Qwen3-Reranker-0.6B/4B`, `jina-reranker-v2-multilingual`, `mxbai-rerank-v2` (candidates to
**verify + A/B separately**, off this board; check the MTEB *reranking* / BEIR reranking
tables). The cross-encoder is where legal-retrieval precision is actually recovered, so
budget it as its own experiment — the embedding score alone does not tell you your final
pipeline quality.

**5. Dense is one channel — hybrid is the multiplier.** BM25 is *strong* exactly where dense
is weak on legal text: exact statute citations, `kenmerk`/dossier numbers, named entities,
verbatim terminology (this is why bBSARDNL dense scores sit in the 20s–40s). The design
already leads with **BM25 + facets**; read the embedding number as **one recall channel in
BM25 + dense → cross-encoder rerank**, never as the whole system.

**Practical checklist when comparing two rows:** sort by Retrieval → compare bBSARDNL +
LegalQANL → prefer the cheap tier unless a model clears a materially higher recall bar on
our corpus → confirm max-token ≥ our chunk size (arctic/bge-m3 8192 ✓, Euler-Legal 1536 ✗)
→ wire the card's query/passage instruction prefixes → pick the reranker in a separate A/B.

## The size ↔ retrieval-quality curve (the burst-tier decision)

Total params · max tokens · Retrieval aggregate · Civic composite · the four civic tasks.
Best-in-tier bolded.

| Model | TotB | maxtok | Retr | Civic | bBSARD | LegalQA | Tender | News |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **— cheap tier (~0.3–0.6B): the burst candidates —** | | | | | | | | |
| **Snowflake/snowflake-arctic-embed-l-v2.0** | 0.57 | 8192 | **62.70** | **59.53** | 28.82 | 82.67 | **48.71** | 77.90 |
| BAAI/bge-m3 *(design baseline)* | 0.57 | 8194 | 60.05 | 56.50 | 24.07 | 81.23 | 39.04 | **81.67** |
| intfloat/multilingual-e5-large | 0.56 | 514 | 59.05 | 53.42 | 23.84 | 77.48 | 37.78 | 74.59 |
| ibm-granite/granite-embedding-311m-r2 | 0.31 | 8192 | 58.66 | 52.04 | 22.23 | 75.78 | 42.60 | 67.55 |
| Qwen/Qwen3-Embedding-0.6B | 0.60 | 32768 | 54.95 | 49.03 | 23.11 | 67.87 | 45.17 | 59.96 |
| **— mid tier (4B) —** | | | | | | | | |
| codefuse-ai/F2LLM-v2-4B | 4.02 | 40960 | **65.41** | **61.31** | 34.19 | **85.45** | 44.98 | 80.62 |
| Octen/Octen-Embedding-4B | 4.02 | 32768 | 65.21 | 60.02 | 38.09 | 79.53 | 47.91 | 74.57 |
| Qwen/Qwen3-Embedding-4B | 4.02 | 32768 | 65.13 | 60.87 | 38.39 | 78.88 | **48.76** | 77.44 |
| **— top tier (8–14B) —** | | | | | | | | |
| codefuse-ai/F2LLM-v2-14B | 13.99 | 40960 | **67.19** | **64.41** | 40.61 | 85.13 | 47.86 | **84.05** |
| nvidia/llama-embed-nemotron-8b | 7.51 | 32768 | 67.00 | 62.22 | **42.86** | **85.86** | 39.98 | 80.17 |
| Qwen/Qwen3-Embedding-8B | 7.57 | 32768 | 66.82 | 61.98 | 41.68 | 78.51 | 47.66 | 80.06 |
| Octen/Octen-Embedding-8B | 7.57 | 32768 | 66.66 | 60.17 | 39.81 | 74.86 | 47.85 | 78.16 |
| Mira190/Euler-Legal-Embedding-V1 | 7.57 | **1536** | 65.52 | 58.54 | 32.17 | 73.67 | 50.87 | 77.45 |

## Findings (full-CSV, corrects the earlier screenshot reads)

1. **The curve is flat at the top.** Best-cheap (arctic-embed 62.70) → best-14B (F2LLM 67.19)
   is **~4.5 Retrieval points for ~25× the params**. 4B→8B is only ~1.7 pts. For a
   cost-sensitive burst fleet that scaling is poor; the value lives in the cheap tier.
2. **Cheap-tier winner is `snowflake-arctic-embed-l-v2.0`, not bge-m3.** At 0.57B/8192-tok
   it beats bge-m3 on Retrieval (62.70 vs 60.05) and Civic (59.53 vs 56.50), and its
   government-**tender** retrieval (48.71) tops nearly every 8B model. It's the standout
   cost/quality pick and the new cheap-tier default to beat. bge-m3 stays a safe,
   well-supported fallback (and still leads on news retrieval).
3. **Euler-Legal is dominated by its own base model — drop it.** It's a legal fine-tune of
   Qwen3-Embedding-8B, but base **Qwen3-8B beats it on Retrieval (66.82 vs 65.52) and even
   on the Belgian-legal `bBSARDNL` task (41.68 vs 32.17)** — the FT *regressed* the metric
   it was meant to help. Plus a crippling **1536-token cap** (vs 32k). The earlier
   "domain-fit-wins" read was a screenshot artifact (that view excluded the 8B giants).
4. **The small end of the LLM-embedder ladder is weak.** `Qwen3-Embedding-0.6B` (54.95) is
   well below same-size encoders (arctic 62.70, bge-m3 60.05). The Qwen3 family only becomes
   competitive at **4B** — so "use a tiny Qwen3" is a trap; for tiny, use an encoder.
5. **`instruct` variants need their prompts or they crater.** `multilingual-e5-large-instruct`
   Civic 38.56 vs plain `multilingual-e5-large` 53.42 — the instruction models tank without
   correct query/passage prefixes. Whatever we pick, wire the prefixes the card specifies.

## Cost implication for the burst tier

A 4–8B embedder is 3–7× the OCR model's 1.2B → materially lower items/GPU-h and 16 GB+ of
bf16 weights. The ~4.5-point quality gap does **not** justify that on burst economics for
bulk chunk embedding. So the burst divisor is **its own `TODO(probe)`** (don't reuse the
~14 000 pages/GPU-h OCR number), and the staged plan holds: **cheap encoder for the
bulk/opt-in chunk tier, reserve a 4–8B model (if at all) for summary-level embeddings**
where volume is low. This also eases the chunker-freeze caveat — arctic-embed/bge-m3 give
8192-token chunks vs Euler-Legal's 1536.

## Recommended A/B shortlist (when it runs)

- **Cheap tier (lead candidate):** `snowflake-arctic-embed-l-v2.0`; **fallback:** `bge-m3`
  (design baseline, best news retrieval, huge ecosystem support).
- **Quality ceiling to measure the gap against:** `Qwen3-Embedding-4B` (best size/quality
  in the mid tier, 32k tokens) — the "is it worth 7× the cost" reference.
- **Reranker (separate stage, separate A/B — NOT from this leaderboard):** evaluate a
  cross-encoder over the top-100 dense+BM25 candidates. Candidates to verify:
  `bge-reranker-v2-m3`, `Qwen3-Reranker-0.6B/4B`, `jina-reranker-v2-multilingual`,
  `mxbai-rerank-v2`. This is where legal-retrieval precision is recovered; the embedding
  A/B and reranker A/B are independent and both needed.
- **Eval as a pipeline, not a model.** Measure **recall@100** for the stage-1 retriever
  (embedding + BM25 hybrid) and **nDCG@10 after rerank** for the full chain, on **our own
  civic corpus** (WOO/tender/legal), not just MTEB. MTEB is the prior; our pipeline eval is
  the decider. Wire the card-specified query/passage prefixes.
- Measure items/GPU-h for the winner on the target card (3090/4090/Salad node), mirroring
  the `ocr_ab` throughput methodology, to fill the burst divisor.

## Open questions

- `TODO(probe)`: does `snowflake-arctic-embed-l-v2.0`'s edge hold on **our** Dutch civic
  documents (MTEB tasks are proxies, and arctic is multilingual-general, not Dutch-tuned)?
- `TODO(probe)`: license check per finalist before shipping (arctic = Apache-2.0 per card;
  verify bge-m3 MIT, Qwen3 Apache-2.0).
- `TODO(probe)`: reranking is a **separate cross-encoder A/B**, NOT read from this board
  (the MTEB `Reranking` task type / `WikipediaRerankingMultilingual` measures a *bi-encoder*
  reordering — see "How to read", rule 4). Source reranker candidates from the MTEB
  reranking / BEIR reranking tables and eval on our top-100 legal candidates.
- A cheap **domain fine-tune** (arctic or Qwen3-0.6B/4B on our WOO/legal pairs) is the ideal
  end state — Euler-Legal shows FT can *hurt* if done wrong, so treat as an experiment, not
  a given.
