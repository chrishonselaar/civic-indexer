# Unified local scores (one code path, from saved vectors)

Pool = 42892 chunks, 3000 queries, known-item single-gold retrieval.

| Model | dim | r@10 | r@20 | r@100 | nDCG@10 | MRR@100 |
|---|---:|---:|---:|---:|---:|---:|
| voyage-4-large | 1024 | 0.598 | 0.661 | 0.783 | 0.448 | 0.408 |
| voyage-4-lite | 1024 | 0.554 | 0.621 | 0.750 | 0.404 | 0.365 |
| voyage-law-2 | 1024 | 0.521 | 0.593 | 0.728 | 0.378 | 0.342 |
| qwen4b | 2560 | 0.505 | 0.576 | 0.720 | 0.372 | 0.339 |
| bgem3 | 1024 | 0.445 | 0.515 | 0.674 | 0.318 | 0.287 |
| arctic | 1024 | 0.449 | 0.515 | 0.659 | 0.317 | 0.283 |
