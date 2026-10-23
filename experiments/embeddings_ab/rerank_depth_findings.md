# How deep does reranking have to go? (2026-07-27)

Asked because rerank depth is the one lever that scales *every* option at once: API price,
GPU seconds, cold-start budget, latency. The production path reranks the fused top-100. If
depth 20 scored the same, the whole vendor question would get ~5× cheaper before anyone was
chosen.

**It does not. The lever does not exist.**

## Method — no new inference, no vendor

A cross-encoder scores each (query, document) pair independently, so the ranking it would
have produced over the stage-1 top-K *is* its ranking over the top-100 restricted to those
K. Depth K is therefore a pure re-ordering of artefacts already on disk:

```
rerank the stage-1 top-K  ->  those K in reranker order
leave the tail alone      ->  ranks K+1.. in stage-1 order
```

`rerank_depth.py`, scored with the same `pooled_qrels_v2.json` and the same nDCG formula as
`rerank_score.py`, n=250 judged queries. Costs nothing and is exactly reproducible.

## Result

| depth | bge-m3 + **Voyage** | Δ vs 100 | bge-m3 + Cohere | Δ | bge-m3 + **bge-reranker-v2-m3** | Δ |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | **0.702** | — | 0.620 | — | 0.586 | — |
| 50 | 0.664 | −0.038 | 0.599 | −0.021 | 0.578 | −0.008 |
| 30 | 0.641 | −0.061 | 0.588 | −0.032 | 0.576 | −0.010 |
| 20 | 0.632 | −0.070 | 0.594 | −0.026 | **0.587** | **+0.001** |
| 10 | 0.577 | −0.125 | 0.563 | −0.057 | 0.557 | −0.029 |
| 5 | 0.551 | −0.151 | 0.543 | −0.077 | 0.541 | −0.045 |
| — (stage-1) | 0.515 | | 0.515 | | 0.515 | |

## Findings

1. **Depth is worth real quality, and it does not saturate before 100.** Cutting the
   production stack to depth 20 costs **−0.070 nDCG@10** — more than a third of the entire
   +0.187 that reranking buys over stage-1. Even depth 50 costs −0.038. Whatever reranker is
   chosen, it has to see ~100 candidates.

2. **How much depth pays scales with how strong the reranker is.** Voyage loses 0.070 going
   from 100 to 20; Cohere loses 0.026; `bge-reranker-v2-m3` loses *nothing* — it is already
   flat at depth 20. A weak reranker cannot exploit a deep pool, so its depth curve is flat
   for the same reason its ceiling is low. This is the cleanest single explanation of the
   quality gap between the three, and it means "just rerank fewer candidates with a cheaper
   model" fails twice over rather than trading one axis against the other.

3. **Consequence for costing.** Every per-search price quoted for these APIs already assumes
   ~100 documents, so nothing gets cheaper; and a self-hosted option needs GPU time for 100
   pairs per query, not 20. The cost estimates stand as they were — which, at this project's
   query volume, still means well under €1/month for every option. Depth was never going to
   be where the decision lived.

## Negative result worth keeping

The hypothesis "rerank depth 20 is nearly free" was mine, offered as the one step that could
be taken without spending anything. It was wrong, and it was wrong in the direction that
would have quietly cost quality if it had been adopted on plausibility rather than measured.
The measurement cost nothing precisely because the artefacts of the earlier A/B were kept.
