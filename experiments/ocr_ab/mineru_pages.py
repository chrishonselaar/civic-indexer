"""Run page JPEGs through MinerU2.5 via mineru-vl-utils http-client backend.

MinerU2.5 needs its two-phase (layout -> recognition) client, NOT naive whole-page
prompting (that loops). two_step_extract returns list[ContentBlock]; we join block
content in returned (reading) order into one page text.

usage: mineru_pages.py <server_url> <pages_root> <outdir>
Output JSON per stem: [{"page":n,"text":...}], resumable per stem.
"""
import glob
import json
import os
import sys
import time

from PIL import Image
from mineru_vl_utils import MinerUClient

server_url, pages_root, outdir = sys.argv[1:4]
os.makedirs(outdir, exist_ok=True)
client = MinerUClient(backend="http-client", server_url=server_url)


def extract(img_path):
    img = Image.open(img_path).convert("RGB")
    blocks = client.two_step_extract(img)
    parts = []
    for b in blocks:
        c = getattr(b, "content", None) if not isinstance(b, dict) else b.get("content")
        if c:
            parts.append(c)
    return "\n".join(parts)


grand_p = grand_s = 0
for stemdir in sorted(glob.glob(f"{pages_root}/*/")):
    stem = os.path.basename(stemdir.rstrip("/"))
    outf = f"{outdir}/{stem}.json"
    if os.path.exists(outf):
        continue
    imgs = sorted(glob.glob(f"{stemdir}/p*.jpg"))
    t0 = time.time()
    pages = []
    for i, img in enumerate(imgs):
        try:
            text = extract(img)
        except Exception as e:
            text = ""
            print(f"  page {i+1} ERROR {e}", flush=True)
        pages.append({"page": i + 1, "text": text})
    secs = time.time() - t0
    json.dump(pages, open(outf, "w"), ensure_ascii=False)
    grand_p += len(imgs)
    grand_s += secs
    chars = sum(len(p["text"]) for p in pages)
    print(f"{stem:55s} {len(imgs):4d} p {chars:8d} chars {secs:7.1f}s", flush=True)
if grand_s:
    print(f"throughput: {grand_p/(grand_s/3600):.0f} pages/GPU-hour")
