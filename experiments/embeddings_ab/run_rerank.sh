#!/bin/bash
cd /home/chris/claude-projects/civic-indexer/experiments/embeddings_ab
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
for base in bgem3 voyage-4-large arctic; do
  echo "=== rerank $base $(date +%H:%M:%S) ==="
  env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN -u HUGGINGFACE_HUB_TOKEN \
    .venv/bin/python rerank_local.py $base --n 500 --topk 100
done
echo "RERANK_DONE" > rerank_done.flag
