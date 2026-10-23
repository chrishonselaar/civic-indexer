#!/bin/bash
cd /home/chris/claude-projects/civic-indexer/experiments/embeddings_ab
for k in arctic qwen4b voyage-4-lite voyage-law-2; do
  echo "=== miss analysis: $k ==="
  .venv/bin/python miss_analysis.py results_${k}_ranks.jsonl --n 60 2>/dev/null | head -9
done
echo "MISS_ALL_DONE" > miss_all_done.flag
