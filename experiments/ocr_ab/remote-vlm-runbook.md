> **Update (2026-07-22):** the remote arm ran. Recommended stack, full results, and ops
> lessons are consolidated in **`production-setup.md`** (MinerU2.5-Pro on a 3090, in-process
> vLLM engine, baked `Dockerfile`). Key new op: **PyPI-from-RunPod is a bandwidth lottery** —
> bake the image or gate on a PyPI speed test before installing. `rp.py` = RunPod REST helper.
>
> **Baked image is built & published: `ghcr.io/chrishonselaar/ocr-mineru:v1`** (currently
> PRIVATE on ghcr — flip public or use a pull token). Point a pod's `imageName` at it to
> skip the PyPI install. Threaded-crops result is in: `executor=ThreadPoolExecutor(32)`
> = +15% (~14,000 pages/GPU-h on a 3090). This closes both remaining open items.

# Remote-GPU arm of the OCR A/B (olmOCR, PaddleOCR-VL, DeepSeek-OCR, dots.ocr)

The remaining strategy-doc candidates don't fit the local 4 GB card. This runbook makes
the rented-GPU pass mechanical once a provider account exists (RunPod/Vast class, per
`design/ocr-strategy.md`). Blocked on: provider choice + account (Chris).

## Inputs (already produced locally)

- `data/ocr_ab/pages/` — 396 JPEGs @ 200 DPI (11 files) — for image-input engines.
- `data/ocr_testset/*.pdf` — originals — for olmOCR, which takes PDFs directly.

Ship either as a tarball: `tar czf ocr_ab_inputs.tgz data/ocr_testset/*.pdf data/ocr_ab/pages/`.

## Suggested instance

1× A100 80GB or RTX 4090 24GB, CUDA ≥ 12.1 image with Python 3.11+.
`TODO(probe)`: per-minute price at chosen provider; pages/GPU-hour per engine (that
number is the point of the exercise — it feeds the fleet-hour cost model).

## Engine recipes (verify versions at run time; all support vLLM or HF transformers)

- **olmOCR** (7B, AllenAI): `pip install olmocr` then
  `python -m olmocr.pipeline ./ws --pdfs data/ocr_testset/*.pdf` — native PDF input,
  outputs Dolma-style JSONL; convert to the harness JSON per stem.
- **PaddleOCR-VL** (0.9B): `pip install paddleocr` (or HF `PaddlePaddle/PaddleOCR-VL`
  via vLLM); feed the 200-DPI JPEGs per page.
- **DeepSeek-OCR** (3B): HF `deepseek-ai/DeepSeek-OCR` via vLLM; JPEGs per page.
- **dots.ocr** (3B): HF `rednote-hilab/dots.ocr`; JPEGs per page.

Each engine must write the harness format: `<stem>.json` = `[{"page": N, "text": "..."}]`
— then drop the files into `data/ocr_ab/<engine>/` locally and re-run
`score.py` + `judge_dump.py`.

## Timing discipline

Record per engine: model load time (excluded), pure inference wall time over the 396
pages (included), GPU type. pages/GPU-hour = 396 / hours. Also note failure pages
(empty/garbage output) — they feed the "Dutch accept rate" number.

## Lessons from the live run (2026-07-21)

- **Community-cloud hosts are a bandwidth lottery specifically toward HuggingFace's
  CDN** (hit one at 970 B/s to HF while PyPI ran 5 MB/s). Bandwidth-test HF as the
  first action on any new pod and terminate under ~20 MB/s; automated in
  the deploy script (see scratchpad runpod_redeploy.py pattern).
- **Region**: left unpinned for this experiment (all test data is public WOO
  material). If the production burst tier ever processes non-public material, pin EU
  datacenters (RunPod: mostly Secure Cloud) — record this in the AVG one-pager.
- **Setup amortization**: cold pip install of olmocr[gpu] ≈ 20 min + ~16 GB model
  download. For recurring bursts: bake a Docker image (pull ≈ 2-4 min) + keep weights
  on a network volume (~$0.07/GB/mo) → boot-to-first-page ≈ 1-2 min.
- 4090 community hosts repeatedly failed machine-matching via REST API; 3090 24 GB
  at ~$0.22/h deployed fine and fits every candidate engine.

## Gotcha carried over from the local run

A stale HF token in `~/.cache/huggingface` breaks anonymous downloads with
"OAuth token signature verification failed" — on the rental box either don't copy the
cache, or pass `token=False` / set a fresh `HF_TOKEN`.
