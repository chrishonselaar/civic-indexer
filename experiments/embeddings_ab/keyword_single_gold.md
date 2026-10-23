# Short-keyword search: BM25 vs dense vs hybrid (single-gold)

1199 keyword queries (1-4 words), pool 42892. Dense = bge-m3 (durable open default). RRF k=60.

| method | recall@10 | recall@100 | nDCG@10 | MRR@100 |
|---|---:|---:|---:|---:|
| BM25 | 0.414 | 0.637 | 0.283 | 0.251 |
| dense(bge-m3) | 0.314 | 0.520 | 0.215 | 0.192 |
| hybrid-RRF | 0.402 | 0.639 | 0.275 | 0.245 |
