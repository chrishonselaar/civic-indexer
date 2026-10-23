# Pooled multi-relevance eval (fixes single-gold artifact)

250 queries, pooled top-15 across 6 models, Haiku-judged graded relevance (0/1/2). Avg **21.7 relevant chunks/query** (vs 1 under single-gold).

| Model | success@10 | success@100 | nDCG@10 (pooled) | nDCG@10 (single-gold) | recall@100 (pooled) |
|---|---:|---:|---:|---:|---:|
| voyage-4-large | 0.996 | 1.000 | 0.612 | 0.450 | 0.814 |
| voyage-4-lite | 0.968 | 1.000 | 0.559 | 0.394 | 0.801 |
| qwen4b | 0.964 | 1.000 | 0.557 | 0.341 | 0.779 |
| voyage-law-2 | 0.972 | 0.996 | 0.545 | 0.373 | 0.732 |
| bgem3 | 0.972 | 0.992 | 0.532 | 0.298 | 0.733 |
| arctic | 0.948 | 0.996 | 0.436 | 0.285 | 0.673 |
