#!/usr/bin/env python3
"""Runs ON the pod: OCR a directory of PDFs with MinerU2.5-Pro, one .md each.

Restructured per README §PARKED (2026-07-23) to close the render/batching gap
measured in the first production drain (~3.4-3.9k p/GPU-h end-to-end vs the
~12.6-14k GPU-side benchmark):

1. Render/OCR overlap — page renders run in a thread pool that stays PREFETCH
   super-batches ahead of the GPU, so render time hides behind OCR time
   (the old loop was strictly render -> OCR -> render; GPU idle every render).
2. Cross-document super-batches — pages from consecutive docs are accumulated
   into PAGE_BATCH-page batches (48 = the measured concurrency knee), removing
   the 1-20-page-doc penalty (half the civic corpus). Per-doc .mds are
   reassembled after. PAGE_BATCH also still bounds memory on giant scans
   (a 727-page doc held whole OOM-killed the container 2026-07-23).

Output format is byte-identical to the previous worker — one `<!-- page N -->`
marker per page, pages joined with `---` separators — drain.evaluate_result
counts those markers against the queue's expected pages before it allows blob
deletion, so engine/drain.py ingest needs zero changes.

Meant for ghcr.io/chrishonselaar/ocr-mineru:v1 (vLLM 0.11.2 + mineru-vl-utils
baked). PyMuPDF is NOT baked: `pip install pymupdf` first. Resumable: existing
outputs are skipped (including 0-byte two-pod skip markers), so rerun after any
crash. A doc whose batch fails is retried in isolation; if it still fails, no
.md is written and the drain leaves it queued for the next burst.

usage:  python3 ocr_worker.py <pdf_dir> <outdir> [dpi=200]
env:    PAGE_BATCH=48  PREFETCH=3  RENDER_WORKERS=min(16, vCPUs)
"""
import glob
import io
import os
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF
from PIL import Image
from vllm import LLM
from mineru_vl_utils import MinerUClient

MODEL = "opendatalab/MinerU2.5-Pro-2605-1.2B"
PAGE_BATCH = int(os.environ.get("PAGE_BATCH", "48"))
PREFETCH = int(os.environ.get("PREFETCH", "3"))
RENDER_WORKERS = int(os.environ.get("RENDER_WORKERS",
                                    str(min(16, os.cpu_count() or 8))))

pdf_dir, outdir = sys.argv[1], sys.argv[2]
dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 200
os.makedirs(outdir, exist_ok=True)

# ---- build the global page stream (skip docs whose .md already exists) ----
docs = []  # (pdf, stem, n_pages)
for pdf in sorted(glob.glob(f"{pdf_dir}/*.pdf")):
    stem = os.path.splitext(os.path.basename(pdf))[0]
    if os.path.exists(os.path.join(outdir, f"{stem}.md")):
        print(f"{stem:60s} skip (exists)", flush=True)
        continue
    try:
        with fitz.open(pdf) as d:
            docs.append((pdf, stem, len(d)))
    except Exception as e:
        print(f"{stem:60s} FAILED to open: {str(e)[:100]}", flush=True)

if not docs:
    sys.exit(f"no PDFs to do in {pdf_dir}")

pages = [(pdf, stem, i) for pdf, stem, n in docs for i in range(n)]
batches = [pages[i:i + PAGE_BATCH] for i in range(0, len(pages), PAGE_BATCH)]
total_pages = len(pages)
print(f"{len(docs)} PDF(s), {total_pages} pages, {len(batches)} batches of "
      f"<= {PAGE_BATCH} @ {dpi} DPI; prefetch={PREFETCH} "
      f"render_workers={RENDER_WORKERS}", flush=True)

llm_kwargs = dict(model=MODEL, trust_remote_code=True,
                  gpu_memory_utilization=0.90, max_model_len=8192)
try:
    from mineru_vl_utils import MinerULogitsProcessor
    llm_kwargs["logits_processors"] = [MinerULogitsProcessor]
except Exception as e:  # degraded but functional without it
    print("no MinerULogitsProcessor:", str(e)[:80], flush=True)

llm = LLM(**llm_kwargs)
client = MinerUClient(backend="vllm-engine", vllm_llm=llm,
                      executor=ThreadPoolExecutor(max_workers=32))

# ---- renderer: page-level tasks, thread-local fitz handles ----------------
tls = threading.local()
zoom = fitz.Matrix(dpi / 72, dpi / 72)


def render_page(pdf, idx):
    cache = getattr(tls, "docs", None)
    if cache is None:
        cache = tls.docs = {}
    doc = cache.get(pdf)
    if doc is None:
        if len(cache) >= 4:  # a doc is rendered once; don't hoard handles
            for d in cache.values():
                d.close()
            cache.clear()
        doc = cache[pdf] = fitz.open(pdf)
    jpg = doc[idx].get_pixmap(matrix=zoom).tobytes("jpg")
    return Image.open(io.BytesIO(jpg)).convert("RGB")


def page_text(blocks):
    parts = []
    for b in blocks:
        c = b.get("content") if isinstance(b, dict) else getattr(b, "content", None)
        if c:
            parts.append(c)
    return "\n".join(parts)


# ---- consume batches in order, renders running PREFETCH batches ahead -----
pool = ThreadPoolExecutor(max_workers=RENDER_WORKERS)
inflight = deque()  # (batch_index, [futures])


def submit(bi):
    inflight.append((bi, [pool.submit(render_page, p, i)
                          for p, _, i in batches[bi]]))


doc_pages = {stem: {} for _, stem, _ in docs}  # stem -> {page_idx: text}
doc_total = {stem: n for _, stem, n in docs}
failed = set()
done_docs = 0
grand_p = 0
t_start = time.time()
warm_pages = warm_secs = 0  # everything after batch 0 (CUDA-graph compile)

for bi in range(min(PREFETCH, len(batches))):
    submit(bi)
next_submit = min(PREFETCH, len(batches))


def finish_doc(stem):
    global done_docs
    texts = doc_pages.pop(stem)
    md = "\n\n---\n\n".join(
        f"<!-- page {i + 1} -->\n{texts[i]}" for i in sorted(texts))
    outf = os.path.join(outdir, f"{stem}.md")
    tmp = outf + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(md)
    os.replace(tmp, outf)  # never leave a truncated .md for ingest to trust
    done_docs += 1
    print(f"{stem:60s} {doc_total[stem]:4d} p {len(md):8d} chars "
          f"[{done_docs}/{len(docs)} docs]", flush=True)


for _, stem, n in docs:  # 0-page PDFs: old worker wrote an empty .md — keep that
    if n == 0:
        finish_doc(stem)

while inflight:
    bi, futs = inflight.popleft()
    t_wait = time.time()
    imgs = []
    for f, (_, stem, _) in zip(futs, batches[bi]):
        try:
            imgs.append(f.result() if stem not in failed else None)
        except Exception as e:
            if stem not in failed:
                failed.add(stem)
                doc_pages.pop(stem, None)
                print(f"{stem:60s} FAILED render: {str(e)[:100]}", flush=True)
            imgs.append(None)
    render_wait = time.time() - t_wait
    if next_submit < len(batches):  # keep the pool fed before blocking on GPU
        submit(next_submit)
        next_submit += 1

    live = [(img, meta) for img, meta in zip(imgs, batches[bi])
            if img is not None]
    del imgs
    if not live:
        continue
    t0 = time.time()
    try:
        results = client.batch_two_step_extract([img for img, _ in live])
        groups = list(zip((meta for _, meta in live), results))
    except Exception:
        # isolate the poison doc: retry per-doc segments within this batch
        groups = []
        by_doc = {}
        for img, meta in live:
            by_doc.setdefault(meta[1], []).append((img, meta))
        for stem, part in by_doc.items():
            try:
                res = client.batch_two_step_extract([img for img, _ in part])
                groups += list(zip((meta for _, meta in part), res))
            except Exception as e:
                failed.add(stem)
                doc_pages.pop(stem, None)
                print(f"{stem:60s} FAILED OCR: {str(e)[:120]}", flush=True)
    secs = time.time() - t0

    for (_, stem, idx), blocks in groups:
        if stem in failed:
            continue
        texts = doc_pages[stem]
        texts[idx] = page_text(blocks)
        grand_p += 1
        if len(texts) == doc_total[stem]:
            finish_doc(stem)
    if bi > 0:
        warm_pages += len(groups)
        warm_secs += secs + render_wait
    elapsed = time.time() - t_start
    print(f"  batch {bi + 1}/{len(batches)}: {len(groups)} pp "
          f"ocr {secs:.1f}s wait {render_wait:.1f}s | cum {grand_p} pp "
          f"{grand_p / (elapsed / 3600):.0f} p/h", flush=True)

pool.shutdown(wait=False, cancel_futures=True)
elapsed = time.time() - t_start
print(f"\ntotal {grand_p} pages in {elapsed:.1f}s "
      f"({grand_p / (elapsed / 3600):.0f} pages/GPU-hour end-to-end)", flush=True)
if warm_secs:
    print(f"steady-state (excl. first batch): {warm_pages} pages, "
          f"{warm_pages / (warm_secs / 3600):.0f} pages/GPU-hour", flush=True)
if failed:
    print(f"FAILED ({len(failed)}): {', '.join(sorted(failed))}", flush=True)
