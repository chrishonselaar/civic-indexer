# MinerU2.5-Pro OCR-burst image — vLLM baked in so rented pods never run the multi-GB
# PyPI install at boot (which is a bandwidth lottery: we measured 20 KB/s–5 MB/s across
# pods, a ~250x spread; a bad draw = a multi-hour install). Build where PyPI is healthy
# (laptop/CI), push to a registry, then point the pod's imageName at it.
#
# Base = the RunPod pytorch image we validated all session. It carries RunPod's SSH /
# startup wiring, so direct `ssh -p <port> root@<ip>` keeps working unchanged — layering
# our own CUDA base would mean reimplementing that, so don't.
FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

# vLLM 0.11.2 is the PINNED, VALIDATED version: it serves MinerU2.5-Pro (stock Qwen2-VL)
# and GLM-OCR correctly, and its bundled torch supports Ampere→Blackwell (sm_120 works —
# the 5090 ran fine on it). vLLM 0.25.x emits "!!!!" garbage for Qwen2-VL. Do NOT bump
# this pin without re-validating real page output (see production-setup.md, "Traps").
RUN pip install --no-cache-dir \
      vllm==0.11.2 \
      mineru-vl-utils \
      pillow \
      ninja

# Client/harness scripts (batch-of-one HTTP, MinerU two-phase client, in-process engine).
COPY bench_concurrent.py mineru_pages.py bench_engine.py bench_engine_threaded.py /root/

# Model weights are NOT baked: ~2.2 GB, pulled from HF at first boot. HF peering is
# reliable (~48 MB/s measured even when PyPI was choked) → ~45 s. Bake them for a
# zero-network boot by uncommenting:
#   RUN python3 -c "from huggingface_hub import snapshot_download; \
#       snapshot_download('opendatalab/MinerU2.5-Pro-2605-1.2B')"

# PUBLISHED 2026-07-22: ghcr.io/chrishonselaar/ocr-mineru:v1 (23.9 GB, digest sha256:d3535b46…).
# Currently PRIVATE on ghcr — flip to public (package settings) or give pods a pull token.
#
# Rebuild + push (from a host with healthy PyPI + Docker):
#   docker build -t ghcr.io/chrishonselaar/ocr-mineru:v1 experiments/ocr_ab
#   gh auth token | docker login ghcr.io -u chrishonselaar --password-stdin   # needs write:packages scope
#   docker push ghcr.io/chrishonselaar/ocr-mineru:v1
# Then set the pod imageName to ghcr.io/chrishonselaar/ocr-mineru:v1 (rp.py create's imageName).
