"""Engine backend + threaded crops: does passing a ThreadPoolExecutor to MinerUClient
reclaim the serial crop stall? Compares no-executor vs threads-32 vs threads-96 on one
shared LLM. Warms the 48-batch shape once (compile cached engine-side), times best-of-2.

usage: bench_engine_threaded.py <pages_root> <max_pages> [price_per_hr]
"""
import glob
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
from vllm import LLM
from mineru_vl_utils import MinerUClient

MODEL = "opendatalab/MinerU2.5-Pro-2605-1.2B"
pages_root, max_pages = sys.argv[1], int(sys.argv[2])
price = float(sys.argv[3]) if len(sys.argv) > 3 else 0.22

all_imgs = sorted(glob.glob(f"{pages_root}/*/p*.jpg"))
if max_pages and len(all_imgs) > max_pages:
    step = len(all_imgs) / max_pages
    paths = [all_imgs[int(i * step)] for i in range(max_pages)]
else:
    paths = all_imgs
images = [Image.open(p).convert("RGB") for p in paths]

kw = dict(model=MODEL, trust_remote_code=True, gpu_memory_utilization=0.90, max_model_len=8192)
try:
    from mineru_vl_utils import MinerULogitsProcessor
    kw["logits_processors"] = [MinerULogitsProcessor]
except Exception as e:
    print("no MinerULogitsProcessor:", str(e)[:60])
llm = LLM(**kw)


def bench(label, executor):
    client = MinerUClient(backend="vllm-engine", vllm_llm=llm, executor=executor)
    client.batch_two_step_extract(images)  # warm (shape compile cached engine-side)
    ts = []
    for _ in range(2):
        t0 = time.time()
        r = client.batch_two_step_extract(images)
        ts.append(time.time() - t0)
    best = min(ts)
    chars = sum(sum(len(getattr(b, "content", None) or "") for b in bl) for bl in r)
    pph = len(images) / (best / 3600)
    print(f"[{label}] wall(best/2)={best:.1f}s chars={chars} "
          f"throughput={pph:.0f} p/GPU-h pages/$={pph/price:.0f}", flush=True)


bench("no-executor", None)
bench("threads-32", ThreadPoolExecutor(max_workers=32))
bench("threads-96", ThreadPoolExecutor(max_workers=96))
print("DONE_THREADED")
