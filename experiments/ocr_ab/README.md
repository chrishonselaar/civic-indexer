# OCR engine A/B

Implements the evaluation protocol in `data/ocr_testset/OCR_TESTSET.md` against the
11-file hard-OCR test set. Feeds the fleet-hour cost model in `design/ocr-strategy.md`
(pages-per-GPU-hour, Dutch accept rate — both `TODO(probe)` until this experiment says
otherwise).

## Layout

- `common.py` — file manifest + categories, shared paths, render DPI.
- `run_native.py` — PyMuPDF native text (free baseline, "do no harm" control).
- `render_pages.py` — all pages → JPEG @ 200 DPI (`data/ocr_ab/pages/`, input for
  image-based engines and the judge).
- `run_azure_di.py` — Azure DI `prebuilt-read`, 50-page batches, $1 budget gate.
  Credentials come from `wob-search-mcp/.env` at runtime (never committed/printed).
- `run_got_ocr.py` — GOT-OCR2.0 (580M), the only strategy-doc candidate that fits the
  local 4 GB RTX 3050. Larger candidates (olmOCR 7B, PaddleOCR-VL 0.9B*, DeepSeek-OCR,
  dots.ocr) need a rented GPU — see "open arm" in `results/RESULTS.md`.
- `judge_dump.py` — writes side-by-side engine texts for the spot-check pages so a
  VLM/human judge can compare against the page image.
- `make_review_app.py` — generates the local ground-truth review app
  (`data/ocr_ab/review/index.html`): page image left, editable transcript right
  (seeded from Azure + `gt_drafts_overrides.json`), export → `corrections.json`.
- `score.py` — automatic proxies → `results/scores.md`: chars/page, density
  confidence, and redaction-label fidelity (count of `5.1.2e` / `10.2.e`-style Woo
  exemption labels recognised).

## Run order

```bash
AB=~/claude-projects/civic-indexer/data/ocr_ab
WOO=~/claude-projects/wob-search-mcp/.venv/bin/python   # fitz + azure sdk
$WOO run_native.py && $WOO render_pages.py && $WOO run_azure_di.py
$AB/.venv/bin/python run_got_ocr.py                     # torch venv, local GPU
$WOO score.py && $WOO judge_dump.py
```

Raw outputs land in `data/ocr_ab/<engine>/<stem>.json` (gitignored);
committed results live in `results/`.
