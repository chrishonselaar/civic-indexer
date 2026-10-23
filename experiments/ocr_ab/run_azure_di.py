"""Azure Document Intelligence (prebuilt-read) pass over the full test set.

- Credentials: AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / _KEY, loaded from the prior WOO
  project's .env (never printed).
- Batches of 50 pages per request (per-word polygons make big responses; same guardrail
  as wob-search-mcp/shared/pdf_processor.py).
- Hard budget gate: aborts if projected cost exceeds MAX_USD.

Output: data/ocr_ab/azure_di/<stem>.json = [{"page": 1, "text": "..."}, ...]
Run with the wob-search-mcp venv python (has azure-ai-formrecognizer + dotenv).
"""
import json
import os
import sys
import time

import fitz
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

from common import MANIFEST, engine_dir, pdf_path

COST_PER_PAGE_USD = 0.0015
MAX_USD = 1.00
BATCH = 50

load_dotenv("/home/chris/claude-projects/wob-search-mcp/.env")
endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "")
key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")
if not endpoint or not key:
    sys.exit("Azure DI credentials not found in wob-search-mcp/.env — aborting (no spend).")

out_dir = engine_dir("azure_di")
todo = [s for s in MANIFEST if not (out_dir / f"{s}.json").exists()]
total_pages = sum(fitz.open(pdf_path(s)).page_count for s in todo)
cost = total_pages * COST_PER_PAGE_USD
print(f"{len(todo)} files, {total_pages} pages -> projected ${cost:.2f}")
if cost > MAX_USD:
    sys.exit(f"Projected cost ${cost:.2f} exceeds budget gate ${MAX_USD:.2f} — aborting.")

client = DocumentAnalysisClient(endpoint, AzureKeyCredential(key))
for stem in todo:
    path = pdf_path(stem)
    n = fitz.open(path).page_count
    data = path.read_bytes()
    pages_out = []
    t0 = time.time()
    for start in range(1, n + 1, BATCH):
        rng = f"{start}-{min(start + BATCH - 1, n)}"
        poller = client.begin_analyze_document("prebuilt-read", data, pages=rng)
        result = poller.result()
        for p in result.pages:
            text = "\n".join(line.content for line in p.lines)
            pages_out.append({"page": p.page_number, "text": text})
    (out_dir / f"{stem}.json").write_text(json.dumps(pages_out, ensure_ascii=False))
    chars = sum(len(p["text"]) for p in pages_out)
    print(f"{stem:55s} {n:4d} p {chars:8d} chars  {time.time() - t0:5.1f}s")
print("done")
