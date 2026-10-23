# Reranker A/B — pooled multi-relevance scoring

n=250 judged queries. nDCG@10 & recall@10 use pooled graded relevance (0/1/2); success@10 = any grade>=1 in top-10. Sorted by pooled nDCG@10.

| method | success@10 | nDCG@10 (pooled) | recall@10 | nDCG@10 (single-gold) |
|---|---:|---:|---:|---:|
| voyage-4-large + voyage | 0.996 | 0.727 | 0.426 | 0.470 |
| bgem3 + voyage | 0.992 | 0.702 | 0.401 | 0.434 |
| bgem3 + cohere | 0.980 | 0.620 | 0.362 | 0.417 |
| voyage-4-large · stage-1 (no rerank) | 0.996 | 0.591 | 0.353 | 0.450 |
| bgem3 + bge-v2-m3 | 0.980 | 0.586 | 0.342 | 0.377 |
| bgem3 + mxbai-v2 | 0.972 | 0.545 | 0.315 | 0.386 |
| bgem3 · stage-1 (no rerank) | 0.972 | 0.515 | 0.307 | 0.298 |
