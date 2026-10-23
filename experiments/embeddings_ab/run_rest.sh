#!/bin/bash
cd /root
echo "=== bgem3 $(date) ==="
python3 embed_eval.py bgem3 > bgem3.log 2>&1
echo "=== qwen4b $(date) ==="
python3 embed_eval.py qwen4b > qwen4b.log 2>&1
echo "=== ALL DONE $(date) ===" > rest_done.flag
