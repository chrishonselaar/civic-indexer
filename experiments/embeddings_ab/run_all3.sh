#!/bin/bash
cd /root
for m in arctic bgem3 qwen4b; do
  echo "=== $m $(date +%H:%M:%S) ==="
  python3 embed_eval.py $m > ${m}.log 2>&1
done
echo "=== ALL3 DONE $(date +%H:%M:%S) ===" > all3_done.flag
