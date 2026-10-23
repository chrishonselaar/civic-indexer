#!/bin/bash
cd /home/chris/claude-projects/civic-indexer/experiments/embeddings_ab
for m in voyage-4-lite voyage-law-2 voyage-4-large; do
  echo "=== $m $(date +%H:%M:%S) ==="
  .venv/bin/python voyage_eval.py $m
done
echo "=== VOYAGE ALL DONE $(date +%H:%M:%S) ===" > voyage_done.flag
