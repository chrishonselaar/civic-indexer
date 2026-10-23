"""Concurrent-client throughput benchmark for the OCR fleet cost model.

Fires N concurrent requests at a vLLM OpenAI server so it continuous-batches,
turning the sequential batch-1 FLOOR into a saturated pages/GPU-hour number.
Reports pages/hour and pages/$ (the number that feeds the fleet cost model).

Two modes:
  mineru : mineru-vl-utils two_step_extract (layout+recognition) — the deployment
           target (MinerU2.5-Pro). Multiple server calls per page = the real workload.
  chat   : single whole-page chat call with a prompt (GLM-OCR / PaddleOCR-VL style).

Representative subset: strided sample across ALL doc dirs (keeps the category +
page-size mix), capped to <max_pages>.

usage:
  bench_concurrent.py <mode> <server_url> <model> <pages_root> <concurrency> <max_pages> [price_per_hr] [prompt]
"""
import base64
import glob
import json
import statistics
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

mode, server_url, model, pages_root = sys.argv[1:5]
concurrency, max_pages = int(sys.argv[5]), int(sys.argv[6])
price = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0
prompt = sys.argv[8] if len(sys.argv) > 8 else "Text Recognition:"

all_imgs = sorted(glob.glob(f"{pages_root}/*/p*.jpg"))
if max_pages and len(all_imgs) > max_pages:
    step = len(all_imgs) / max_pages
    imgs = [all_imgs[int(i * step)] for i in range(max_pages)]
else:
    imgs = all_imgs

_local = threading.local()


def mineru_client():
    if not hasattr(_local, "c"):
        from mineru_vl_utils import MinerUClient
        _local.c = MinerUClient(backend="http-client", server_url=server_url)
    return _local.c


def do_mineru(path):
    from PIL import Image
    blocks = mineru_client().two_step_extract(Image.open(path).convert("RGB"))
    return sum(len(getattr(b, "content", None) or "") for b in blocks)


def do_chat(path):
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    body = {"model": model, "temperature": 0, "max_tokens": 4096,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt}]}]}
    req = urllib.request.Request(f"{server_url}/v1/chat/completions",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return len(json.load(r)["choices"][0]["message"]["content"])


runner = do_mineru if mode == "mineru" else do_chat


def work(path):
    t0 = time.time()
    try:
        chars = runner(path)
        return (chars, time.time() - t0, None)
    except Exception as e:
        return (0, time.time() - t0, str(e)[:90])


work(imgs[0])  # warmup (excluded)

t0 = time.time()
lat, errs, okchars = [], 0, 0
with ThreadPoolExecutor(max_workers=concurrency) as ex:
    for chars, dt, err in ex.map(work, imgs):
        lat.append(dt)
        okchars += chars
        if err:
            errs += 1
wall = time.time() - t0
pph = len(imgs) / (wall / 3600)
print(f"[{mode}] concurrency={concurrency} pages={len(imgs)} wall={wall:.1f}s errors={errs} chars={okchars}")
print(f"  throughput : {pph:.0f} pages/GPU-hour")
if price:
    print(f"  pages/$    : {pph/price:.0f}   (at ${price}/hr)")
lat.sort()
print(f"  latency p50/p90/max: {statistics.median(lat):.1f}/{lat[int(len(lat)*0.9)]:.1f}/{lat[-1]:.1f}s")
