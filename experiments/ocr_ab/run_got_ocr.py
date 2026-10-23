"""GOT-OCR2.0 (580M, stepfun-ai/GOT-OCR-2.0-hf) pass over the rendered page images.

The only candidate from design/ocr-strategy.md that fits the local 4 GB RTX 3050;
also yields a first pages-per-GPU-hour datapoint. Resumable per file.

Output: data/ocr_ab/got_ocr2/<stem>.json = [{"page": 1, "text": "..."}, ...]
Run with data/ocr_ab/.venv/bin/python (torch + transformers >= 4.48).
"""
import json
import time

import torch
from transformers import AutoModelForImageTextToText, AutoProcessor

from common import MANIFEST, OUT, engine_dir

MODEL = "stepfun-ai/GOT-OCR-2.0-hf"

# token=False: a stale local HF token otherwise fails signature checks on public repos
processor = AutoProcessor.from_pretrained(MODEL, token=False)
model = AutoModelForImageTextToText.from_pretrained(
    MODEL, torch_dtype=torch.float16, device_map="cuda", token=False
)
model.eval()

out_dir = engine_dir("got_ocr2")
grand_pages = grand_secs = 0
for stem in MANIFEST:
    out_f = out_dir / f"{stem}.json"
    if out_f.exists():
        continue
    imgs = sorted((OUT / "pages" / stem).glob("p*.jpg"))
    pages_out = []
    t0 = time.time()
    for i, img in enumerate(imgs):
        inputs = processor(str(img), return_tensors="pt").to("cuda", torch.float16)
        with torch.no_grad():
            ids = model.generate(
                **inputs,
                do_sample=False,
                tokenizer=processor.tokenizer,
                stop_strings="<|im_end|>",
                max_new_tokens=2048,
            )
        text = processor.decode(ids[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        pages_out.append({"page": i + 1, "text": text})
    secs = time.time() - t0
    out_f.write_text(json.dumps(pages_out, ensure_ascii=False))
    grand_pages += len(imgs)
    grand_secs += secs
    chars = sum(len(p["text"]) for p in pages_out)
    print(f"{stem:55s} {len(imgs):4d} p {chars:8d} chars  {secs:6.1f}s ({secs / max(len(imgs),1):.1f}s/p)", flush=True)

if grand_secs:
    print(f"throughput: {grand_pages / (grand_secs / 3600):.0f} pages/GPU-hour on RTX 3050 4GB")
