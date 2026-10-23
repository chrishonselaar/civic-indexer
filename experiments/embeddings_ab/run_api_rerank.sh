#!/bin/bash
cd /home/chris/claude-projects/civic-indexer/experiments/embeddings_ab
for base in bgem3 voyage-4-large; do
  for prov in voyage cohere; do
    echo "=== $base / $prov $(date +%T) ===" >> api_rerank.log
    .venv/bin/python rerank_api.py $base $prov >> api_rerank.log 2>&1
  done
done
echo "ALL DONE $(date +%T)" >> api_rerank.log
