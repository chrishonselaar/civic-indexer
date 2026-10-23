# What public benchmarks say about Cohere Rerank 4 vs Voyage rerank-2.5

Desk research, 2026-07-28. **No new measurement** — this is a survey of published evidence,
gathered because the two blocked A/B runs (`rerank-v4.0-fast`, `rerank-v4.0-pro`) need a
Cohere *production* account and the admin does not want to spend more there.

Our own measured baseline, for reference (n=250 judged queries, `pooled_qrels_v2.json`):
Voyage `rerank-2.5` **0.702**, Cohere `rerank-v3.5` **0.620**, no rerank 0.515.

## 0. Naming correction

There is **no Voyage "rerank 4"**. Voyage's newest reranker is `rerank-2.5` / `rerank-2.5-lite`,
released 2025-08-11 and still current as of today. `voyage-4-large` / `voyage-4-lite` are
*embedders* (already in our A/B). So the open question is exactly:

> Cohere `rerank-v4.0-fast` (and `-pro`), released 2025-12-11 — vs Voyage `rerank-2.5`.

## 1. Vendor claims — mutually circular, both self-reported

| source | claim | what it is worth |
|---|---|---|
| Voyage, 2025-08-11 | `rerank-2.5` is **+7.94%** over Cohere `rerank-v3.5` across 93 datasets / 9 domains (51 of them multilingual, 31 languages); **+12.70%** on MAIR. nDCG@10, top-100 candidates, four first-stage retrievers. | Compares to **v3.5**, i.e. the generation we already measured. Our own number is +13.2% relative (0.620 → 0.702), so on our corpus Voyage's claim is if anything conservative. Nothing about v4. |
| Cohere, 2025-12-11 | "Rerank 4 … outperform[s] the likes of MongoDB's Voyage models and ElasticSearch's Jina rerankers in overall search relevance." | The one direct claim, and it is unfalsifiable from the page: every nDCG@10 chart is a raster image with no table and no per-model figures. The prose consistently sells **Pro**; **Fast** is described only as "same latency as Rerank 3.5, with significantly higher accuracy". |

Cohere's benchmark set is BEIR plus internal suites (Long Context, Parsed PDFs across 6
languages, Semi-Structured) and domain suites (finance, healthcare, manufacturing, workplace
productivity). Named comparison models: Qwen Reranker 8B, Jina Rerank v3, Voyage Rerank 2.5.

## 2. The only genuinely third-party numbers: the Agentset leaderboard

Snapshot 2026-02-15. Methodology: FAISS top-50 candidates held identical across models,
GPT-5 judges pairs of ranked lists head to head → ELO; separately a label-based nDCG@10 over
three sets (FiQA, SciFact, long-form "PG"), plus latency percentiles.

| model | ELO | their avg nDCG@10 | latency | licence |
|---|---:|---:|---:|---|
| zerank-2 | 1638 | 0.079 | 265 ms | cc-by-nc-4.0 |
| **Cohere Rerank 4 Pro** | **1629** | 0.095 | 614 ms | proprietary |
| zerank-1 | 1573 | 0.082 | 266 ms | cc-by-nc-4.0 |
| **Voyage rerank-2.5** | **1544** | **0.110** | 613 ms | proprietary |
| zerank-1-small | 1539 | 0.083 | 248 ms | Apache-2.0 |
| Voyage rerank-2.5-lite | 1520 | 0.103 | 616 ms | proprietary |
| **Cohere Rerank 4 Fast** | **1510** | 0.094 | **447 ms** | proprietary |
| Qwen3 Reranker 8B | 1473 | 0.106 | 4687 ms | Apache-2.0 |
| Contextual AI Rerank v2 Instruct | 1469 | 0.114 | 3333 ms | cc-by-nc-4.0 |
| Cohere Rerank 3.5 | 1451 | 0.080 | 392 ms | proprietary |

**The two columns disagree**, and that is the most important thing on this page. By ELO,
Pro > Voyage > Fast. By their own label-based nDCG@10, Voyage (0.110) beats *both* v4 models
(0.095 / 0.094). Per dataset the split is domain-shaped: on Arguana Voyage is far ahead
(nDCG@10 0.543 vs 0.425 Fast / 0.439 Pro; recall@5 0.96 vs 0.66), on FiQA Cohere is slightly
ahead (0.138 Fast / 0.129 Pro vs 0.119 Voyage). Three eval sets is a thin base and an
LLM-as-judge ELO is not our metric, so treat the ordering as weak.

Agentset's own write-up adds that **Fast regressed against v3.5** on argumentation-style and
web-style queries, gaining mainly on business (~+300 ELO) and finance (~+140 ELO) documents.
Pro gains on all six of their domains (~+400 ELO on business).

## 3. Reading it onto our scale

Only ELO deltas against v3.5 are available, and ELO→nDCG is not linear, so this is an
*ordering* argument, not an arithmetic one:

- v3.5 → **Fast** is +59 ELO; v3.5 → **Voyage** is +93 ELO. Fast moves in the right direction
  but, on the one independent ranking that exists, **does not reach Voyage** — and on that
  source's label-based metric it does not beat v3.5's *relative* position against Voyage either.
- v3.5 → **Pro** is +178 ELO, comfortably past Voyage.

So the public evidence says: **Fast probably does not close our 0.082 gap; Pro is the model
that plausibly does.** If we ever spend on this question again, spend it on `rerank-v4.0-pro`
first — measuring Fast alone would most likely just confirm a loss.

## 4. Dutch: nobody has measured it

This is the honest gap, and it applies to *every* source above.

- Cohere claims 100+ languages and "state-of-the-art retrieval in 10 major business languages:
  Arabic, Chinese, French, German, Hindi, Japanese, Korean, Portuguese, Russian, Spanish."
  **Dutch is not in that list.** Their multilingual suite is 18 languages, reported as a single
  pooled nDCG@10 — no per-language breakdown.
- Voyage's multilingual set is 51 datasets / 31 languages, also pooled, also no per-language
  numbers.
- No academic or leaderboard evaluation found that reports Dutch reranking for either API model.
  (MTEB/MMTEB reranking leaderboards carry open-weights models; commercial rerankers are absent.)

Secondary blogs asserting things like "Voyage degrades materially outside English while Cohere
holds" are SEO content with no measurements behind them — **do not cite them**. Recorded here
only so the claim is not rediscovered and believed later.

Our corpus is Dutch civic text with heavy legal/administrative register. That is exactly the
setting where pooled multilingual averages transfer worst, which is why the local A/B existed
in the first place.

## 5. The one materially new fact

**Both v4 variants are already on Microsoft Foundry as direct-from-Azure models**, GA'd
2025-12-11 alongside the Cohere launch (Global Standard, $2.50/1K search units for Pro,
$2.00/1K for Fast), plus Amazon SageMaker AI for Fast and Pro, plus VPC/on-prem.

That matters more than the benchmark question: the whole reason Cohere v4 was interesting is
the **EU-residency + Art. 28 paperwork** route (Azure/Bedrock DPA + SCCs, Cohere never sees the
text) from `design/access-posture.md` §4. That route is confirmed to exist for **Pro**, not just
Fast — and Pro is the variant the public evidence favours. Caveat: "Global Standard" is a
routing tier, **not** an EU-residency guarantee; a data-residency deployment has to be selected
explicitly and priced separately, and that is a paperwork question, not a benchmark question.

Secondary consequence: an Azure Foundry deployment would run the blocked A/B **without a Cohere
production key at all**.

## 6. Conclusion

1. Public benchmarks do **not** support the hypothesis that `rerank-v4.0-fast` closes the gap to
   Voyage. One independent leaderboard puts it below Voyage on both of its metrics.
2. `rerank-v4.0-pro` is the variant with a real case, and it has the same GDPR route.
3. Nobody has published Dutch reranking numbers for either vendor, so no public benchmark can
   settle our question — it can only tell us which run is worth paying for.
4. Therefore: **do not buy a Cohere production key to measure Fast.** Either park the question
   (Voyage stays, `woo_besluiten` remains the only source opted into `voyage_rerank`, exposure
   T2), or, if the EU-residency argument becomes binding, measure **Pro** — ideally via Azure
   Foundry rather than a Cohere account.

## Sources

- Cohere, *Introducing Rerank 4* (2025-12-11) — https://cohere.com/blog/rerank-4
- Cohere changelog, *Rerank v4.0* — https://docs.cohere.com/changelog/rerank-v4.0
- Cohere docs, *Rerank overview* (100+ languages) — https://docs.cohere.com/docs/rerank-overview
- Microsoft Foundry blog, *Introducing Cohere Rerank 4.0 in Microsoft Foundry* (2025-12-11) —
  https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-cohere-rerank-4-0-in-microsoft-foundry/4477076
- Voyage AI, *rerank-2.5 and rerank-2.5-lite* (2025-08-11) — https://blog.voyageai.com/2025/08/11/rerank-2-5/
- Agentset reranker leaderboard (snapshot 2026-02-15) — https://agentset.ai/rerankers
- Agentset, *Cohere Rerank 4: a real upgrade over 3.5* — https://agentset.ai/blog/cohere-reranker-v4
- Agentset pairwise pages — /compare/voyage-ai-rerank-25-vs-cohere-rerank-4-fast,
  /compare/cohere-rerank-4-pro-vs-voyage-ai-rerank-25
