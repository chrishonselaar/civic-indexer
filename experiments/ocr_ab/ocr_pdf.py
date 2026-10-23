"""Turnkey PDF -> text/markdown OCR with MinerU2.5-Pro (in-process vLLM engine).

Unlike mineru_pages.py / bench_engine.py (which expect pre-rendered page JPEGs and are
wired to the A/B test-set), this is a self-contained one-shot for arbitrary PDFs: it
renders each page with PyMuPDF at 200 DPI, runs MinerU's two-phase extract in-process,
and writes one .md per input PDF (blocks joined in reading order, page separators).

Meant to run INSIDE ghcr.io/chrishonselaar/ocr-mineru:v1, which bakes vLLM 0.11.2 +
mineru-vl-utils. PyMuPDF is NOT baked -> the docker run installs it first (see below).

usage:  python ocr_pdf.py <input.pdf | input_dir> <outdir> [dpi]

Needs an NVIDIA GPU visible in the container (docker run --gpus all). The model
(~2.2 GB) downloads from HuggingFace on first run (~45 s), then is cached.
"""
import glob
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF
from PIL import Image
from vllm import LLM
from mineru_vl_utils import MinerUClient

MODEL = "opendatalab/MinerU2.5-Pro-2605-1.2B"

inp, outdir = sys.argv[1], sys.argv[2]
dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 200
os.makedirs(outdir, exist_ok=True)

pdfs = [inp] if inp.lower().endswith(".pdf") else sorted(glob.glob(f"{inp}/*.pdf"))
if not pdfs:
    sys.exit(f"no PDFs found at {inp}")
print(f"{len(pdfs)} PDF(s) to process @ {dpi} DPI", flush=True)

# --- load the model once (in-process; no HTTP server to babysit) --------------------
# MinerULogitsProcessor constrains generation to valid layout tokens (needs vllm>=0.10.1).
llm_kwargs = dict(model=MODEL, trust_remote_code=True,
                  gpu_memory_utilization=0.90, max_model_len=8192)
try:
    from mineru_vl_utils import MinerULogitsProcessor
    llm_kwargs["logits_processors"] = [MinerULogitsProcessor]
except Exception as e:
    print("no MinerULogitsProcessor:", str(e)[:80], flush=True)

llm = LLM(**llm_kwargs)
# executor threads the per-page region crops -> ~+15% throughput (see production-setup.md)
client = MinerUClient(backend="vllm-engine", vllm_llm=llm,
                      executor=ThreadPoolExecutor(max_workers=32))


def render(pdf_path):
    """PDF -> list[PIL.Image], one per page, at the target DPI."""
    zoom = dpi / 72
    doc = fitz.open(pdf_path)
    imgs = []
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        imgs.append(Image.open(io.BytesIO(pix.tobytes("jpg"))).convert("RGB"))
    return imgs


def page_text(blocks):
    parts = []
    for b in blocks:
        c = getattr(b, "content", None) if not isinstance(b, dict) else b.get("content")
        if c:
            parts.append(c)
    return "\n".join(parts)


grand_p = grand_s = 0
for pdf in pdfs:
    stem = os.path.splitext(os.path.basename(pdf))[0]
    outf = os.path.join(outdir, f"{stem}.md")
    if os.path.exists(outf):
        print(f"{stem:50s} skip (exists)", flush=True)
        continue
    imgs = render(pdf)
    t0 = time.time()
    # batch_two_step_extract lets vLLM batch layout+recognition across all pages at once
    results = client.batch_two_step_extract(imgs)
    secs = time.time() - t0
    md = "\n\n---\n\n".join(
        f"<!-- page {i + 1} -->\n{page_text(blocks)}" for i, blocks in enumerate(results)
    )
    with open(outf, "w", encoding="utf-8") as f:
        f.write(md)
    grand_p += len(imgs)
    grand_s += secs
    print(f"{stem:50s} {len(imgs):4d} p {len(md):8d} chars {secs:7.1f}s -> {outf}",
          flush=True)

if grand_s:
    print(f"\ntotal {grand_p} pages in {grand_s:.1f}s "
          f"({grand_p / (grand_s / 3600):.0f} pages/GPU-hour)", flush=True)
