# Short-keyword search, POOLED multi-relevance (BM25 vs dense vs hybrid)

250 keyword queries, pooled top-15 across 3 methods, Haiku-graded. Avg **21.8 relevant chunks/query**.

| method | success@10 | success@100 | nDCG@10 | recall@100 |
|---|---:|---:|---:|---:|
| BM25 | 0.992 | 0.992 | 0.765 | 0.799 |
| dense | 0.996 | 1.000 | 0.762 | 0.767 |
| hybrid | 1.000 | 1.000 | 0.822 | 0.981 |
