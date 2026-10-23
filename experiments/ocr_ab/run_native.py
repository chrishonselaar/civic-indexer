"""Native (no-OCR) PyMuPDF extraction pass — the free baseline and 'do no harm' control.

Output: data/ocr_ab/native/<stem>.json  = [{"page": 1, "text": "..."}, ...]
Run with a python that has PyMuPDF, e.g.:
    ~/claude-projects/wob-search-mcp/.venv/bin/python run_native.py
"""
import json

import fitz

from common import MANIFEST, engine_dir, pdf_path

out_dir = engine_dir("native")
for stem in MANIFEST:
    doc = fitz.open(pdf_path(stem))
    pages = [{"page": i + 1, "text": p.get_text()} for i, p in enumerate(doc)]
    (out_dir / f"{stem}.json").write_text(json.dumps(pages, ensure_ascii=False))
    chars = sum(len(p["text"]) for p in pages)
    print(f"{stem:55s} {len(pages):4d} p {chars:8d} chars ({chars / max(len(pages),1):.0f}/p)")
