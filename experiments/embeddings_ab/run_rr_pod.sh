#!/bin/bash
cd /root
for base in bgem3 qwen4b voyage-4-large arctic voyage-4-lite voyage-law-2; do
  echo "=== rr $base $(date +%H:%M:%S) ==="
  python3 rerank_local.py $base --n 1000 --topk 100 > rr_$base.log 2>&1
done
echo DONE > /root/rr_done.flag
