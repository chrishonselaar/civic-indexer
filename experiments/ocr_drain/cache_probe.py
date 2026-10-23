#!/usr/bin/env python3
"""2x2 probe: {A/B bench pages, drain pages} x {fresh pass, repeat pass}.

Separates corpus-difficulty from vLLM prefix-cache inflation in the old
bench numbers. Warmup uses a third, disjoint image set so CUDA graphs are
compiled but the test images are not yet in the prefix cache.

usage: cache_probe.py <ab_pages_dir> <drain_pdf> <warm_pdf> [dpi=200]
"""
import glob
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import fitz
from PIL import Image
from vllm import LLM
from mineru_vl_utils import MinerUClient, MinerULogitsProcessor

ab_dir, drain_pdf, warm_pdf = sys.argv[1:4]
dpi = int(sys.argv[4]) if len(sys.argv) > 4 else 200
zoom = fitz.Matrix(dpi / 72, dpi / 72)


def render(pdf, lo, n):
    with fitz.open(pdf) as d:
        return [Image.open(io.BytesIO(
            d[i].get_pixmap(matrix=zoom).tobytes("jpg"))).convert("RGB")
            for i in range(lo, min(lo + n, len(d)))]


ab = [Image.open(p).convert("RGB") for p in sorted(glob.glob(f"{ab_dir}/*.jpg"))]
drain = render(drain_pdf, 40, 48)   # skip cover pages, take a dense stretch
warm = render(warm_pdf, 0, 48)
print(f"ab={len(ab)} drain={len(drain)} warm={len(warm)}", flush=True)

llm = LLM(model="opendatalab/MinerU2.5-Pro-2605-1.2B", trust_remote_code=True,
          gpu_memory_utilization=0.90, max_model_len=8192,
          logits_processors=[MinerULogitsProcessor])
client = MinerUClient(backend="vllm-engine", vllm_llm=llm,
                      executor=ThreadPoolExecutor(max_workers=32))

client.batch_two_step_extract(warm)  # CUDA-graph compile on disjoint images


def timed(label, imgs):
    t0 = time.time()
    r = client.batch_two_step_extract(imgs)
    dt = time.time() - t0
    chars = sum(sum(len((b.get("content") if isinstance(b, dict)
                         else getattr(b, "content", None)) or "")
                    for b in bl) for bl in r)
    print(f"{label:24s} {len(imgs)} pp in {dt:6.1f}s = "
          f"{len(imgs) / (dt / 3600):7.0f} p/GPU-h  ({chars} chars)", flush=True)


timed("ab_fresh", ab)
timed("ab_repeat", ab)
timed("drain_fresh", drain)
timed("drain_repeat", drain)
timed("ab_repeat2", ab)
print("PROBE_DONE", flush=True)
