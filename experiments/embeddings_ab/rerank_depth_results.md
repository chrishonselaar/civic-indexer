# How deep does reranking have to go?

Simulated from the existing rerank outputs — a cross-encoder scores each
pair independently, so its ranking over the stage-1 top-K is its ranking
over the top-100 restricted to those K. No new inference.

| base | reranker | depth | nDCG@10 | Δ vs depth-100 | success@10 | recall@10 |
|---|---|---:|---:|---:|---:|---:|
| bgem3 | bge-v2-m3 | 5 | 0.541 | -0.045 | 0.972 | 0.307 |
| bgem3 | bge-v2-m3 | 10 | 0.557 | -0.029 | 0.972 | 0.307 |
| bgem3 | bge-v2-m3 | 20 | 0.587 | +0.001 | 0.980 | 0.338 |
| bgem3 | bge-v2-m3 | 30 | 0.576 | -0.010 | 0.980 | 0.330 |
| bgem3 | bge-v2-m3 | 50 | 0.578 | -0.008 | 0.980 | 0.333 |
| bgem3 | bge-v2-m3 | 100 | 0.586 | +0.000 | 0.980 | 0.342 |
| bgem3 | — (stage-1) | — | 0.515 |  | 0.972 | 0.307 |
| bgem3 | cohere | 5 | 0.543 | -0.077 | 0.972 | 0.307 |
| bgem3 | cohere | 10 | 0.563 | -0.057 | 0.972 | 0.307 |
| bgem3 | cohere | 20 | 0.594 | -0.026 | 0.980 | 0.339 |
| bgem3 | cohere | 30 | 0.588 | -0.032 | 0.980 | 0.338 |
| bgem3 | cohere | 50 | 0.599 | -0.021 | 0.984 | 0.348 |
| bgem3 | cohere | 100 | 0.620 | +0.000 | 0.980 | 0.362 |
| bgem3 | — (stage-1) | — | 0.515 |  | 0.972 | 0.307 |
| bgem3 | voyage | 5 | 0.551 | -0.151 | 0.972 | 0.307 |
| bgem3 | voyage | 10 | 0.577 | -0.125 | 0.972 | 0.307 |
| bgem3 | voyage | 20 | 0.632 | -0.070 | 0.984 | 0.353 |
| bgem3 | voyage | 30 | 0.641 | -0.061 | 0.980 | 0.358 |
| bgem3 | voyage | 50 | 0.664 | -0.038 | 0.980 | 0.375 |
| bgem3 | voyage | 100 | 0.702 | +0.000 | 0.992 | 0.401 |
| bgem3 | — (stage-1) | — | 0.515 |  | 0.972 | 0.307 |
| voyage-4-large | voyage | 5 | 0.610 | -0.117 | 0.996 | 0.353 |
| voyage-4-large | voyage | 10 | 0.626 | -0.101 | 0.996 | 0.353 |
| voyage-4-large | voyage | 20 | 0.667 | -0.060 | 0.992 | 0.379 |
| voyage-4-large | voyage | 30 | 0.673 | -0.054 | 0.996 | 0.385 |
| voyage-4-large | voyage | 50 | 0.690 | -0.037 | 0.992 | 0.398 |
| voyage-4-large | voyage | 100 | 0.727 | +0.000 | 0.996 | 0.426 |
| voyage-4-large | — (stage-1) | — | 0.591 |  | 0.996 | 0.353 |
