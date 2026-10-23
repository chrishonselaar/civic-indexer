"""In-process vllm-engine backend throughput bench for MinerU2.5-Pro.

Contrasts with the http-client sweep: processes the same strided page sample via
batch_two_step_extract, so vLLM batches layout+recognition natively with NO HTTP /
base64 / JSON round-trips. Isolates the fan-out+preprocessing bottleneck.

usage: bench_engine.py <pages_root> <max_pages> [price_per_hr]
"""
import glob
import sys
import time

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

# MinerULogitsProcessor constrains generation to valid layout tokens (needed vllm>=0.10.1)
llm_kwargs = dict(model=MODEL, trust_remote_code=True,
                  gpu_memory_utilization=0.90, max_model_len=8192)
try:
    from mineru_vl_utils import MinerULogitsProcessor
    llm_kwargs["logits_processors"] = [MinerULogitsProcessor]
    print("using MinerULogitsProcessor")
except Exception as e:
    print("no MinerULogitsProcessor:", str(e)[:80])

llm = LLM(**llm_kwargs)
client = MinerUClient(backend="vllm-engine", vllm_llm=llm)

# warmup with the SAME batch shape so per-shape compile is excluded from timing
_ = client.batch_two_step_extract(images)

for it in range(2):
    t0 = time.time()
    results = client.batch_two_step_extract(images)
    wall = time.time() - t0
    chars = sum(sum(len(getattr(b, "content", None) or "") for b in blocks) for blocks in results)
    pph = len(images) / (wall / 3600)
    print(f"[engine iter{it}] pages={len(images)} wall={wall:.1f}s chars={chars} "
          f"throughput={pph:.0f} p/GPU-h  pages/$={pph/price:.0f}")
first = "".join((getattr(b, "content", None) or "") for b in results[0])
print("  sample:", repr(first[:120]))
