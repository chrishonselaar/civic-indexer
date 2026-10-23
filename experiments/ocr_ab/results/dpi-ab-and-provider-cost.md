# DPI quality A/B + provider speed/cost (RunPod / Vast / Salad)

Session 2026-07-24. Follow-up to the OCR throughput re-benchmark (README §"DONE
2026-07-24"). Two questions: (1) does dropping render DPI 200→150 buy throughput
without hurting quality? (2) what does per-page OCR actually cost on RunPod vs
Vast.ai vs Salad?

Engine throughout: MinerU2.5-Pro (`opendatalab/MinerU2.5-Pro-2605-1.2B`), in-process
vLLM 0.11.2, `batch_two_step_extract`, baked image `ghcr.io/chrishonselaar/ocr-mineru:v1`.

## 1. Render DPI A/B — verdict: keep 200 DPI (150 is a false economy)

Method: OCR'd the exact 19 human-corrected GT pages (`results/corrections.json`) at
150 and 200 DPI on one RunPod 3090, scored with `gt_score.py`. Ran with
`enable_prefix_caching=False` so every page is a fresh forward pass — the cache
artifact that inflated the throughput numbers (README §2026-07-24) cannot affect a
quality run anyway (a cache hit returns identical tokens), but this removes all doubt.
150 and 200 DPI produce different images, so the arms never share a cache entry.

**Control passed:** a fresh 200-DPI arm (`mineru25_pro_200fresh`, vllm-engine backend,
cache off) scored 0.623/0.912 vs the historical `mineru25_pro` (http-client backend)
0.627/0.913 — reproduced to within noise, so backend and cache-off setup are sound.

| arm | mean sim | mean F1 |
|---|---:|---:|
| 200 DPI (fresh) | 0.623 | 0.912 |
| 150 DPI | 0.596 | 0.893 |
| **Δ (150 − 200)** | **−0.027 (−4.3%)** | **−0.019 (−2.1%)** |

The mean understates the risk. Per page, 150 DPI is **flat (Δsim≈0)** on born-digital,
tables, photocopy, and letterpress, but the loss concentrates on small-font raster:

| page | category | Δsim (200 − 150) |
|---|---|---:|
| vuurwerkramp_email_scan p1 | email-scan | −0.22 |
| chat_screenshots p12 | chat-screenshot | −0.18 |
| vuurwerkramp_typemachine p1 | typewriter | −0.08 |

Those are exactly the hard scans the GPU OCR tail exists to handle (born-digital docs
get native text extraction, never OCR). So 150 DPI takes its quality hit from the pages
that most need the resolution, to save money on an already-negligible bill
(§2 — whole WOO backfill is $36–72). **Not worth it as a blanket setting.** If
throughput ever becomes the true constraint, *adaptive* DPI (150 for clean/born-digital,
200 for detected small-font scans) captures most of the speed with little quality loss —
deferred as complexity, not done.

Harness: `dpi_ab.py` (session scratchpad) renders the GT pages at each DPI and writes
per-engine JSON dirs that `gt_score.py` picks up. Full per-engine table in `gt_scores.md`
(now includes `mineru25_pro_150` / `mineru25_pro_200fresh`).

## 2. Provider speed/cost

**Throughput is card-bound, not provider-bound.** Same 3090 silicon + same worker +
same image ⇒ same pages/GPU-h. Provider choice changes $/h and operational friction
(pull speed, host quality, reliability), *not* GPU throughput. Verified directly: on a
Vast 3090 the card was healthy — full PCIe gen4 ×16, 250 W limit, 1695 MHz boost,
unshared (only our process on the card).

Measured, production worker (`ocr_drain/ocr_worker.py`), fresh single pass over the
396-page OCR test set @ 200 DPI (this corpus is deliberately *harder* than the civic
drain mix, so it is a floor, not the drain rate):

| provider / host | 3090 $/h | pages/GPU-h | $/1k pages | note |
|---|---:|---:|---:|---|
| **Vast**, FR 16-vCPU verified (MEASURED) | 0.202 | **4,472 e2e / 5,046 steady** | **0.045** | slim image; render wait 0.0s; *faster* than RunPod |
| **RunPod** community (measured) | 0.22 | 3,176 e2e / 3,535 steady | **0.069** | 25 vCPU; drain mix ~4,014 ⇒ ~$0.055 |
| **Vast**, 5-vCPU host (measured) | 0.12 | **355** | 0.68 ✗ | GPU starved at 4% util — CPU-bound |
| Salad (reference, not run) | ~0.10–0.12 | — | ~0.03 | container-only; no SSH; diff. workflow |
| *Azure DI (bulk reference)* | — | — | *1.50* | free tier F0 = 500 pp/mo, 2 pp/doc — useless for backfill |

**The measured Vast number came out CHEAPER per page than RunPod ($0.045 vs $0.069)** —
but from higher throughput, not lower $/h. The reliable Vast host was **EU (France) at
$0.202/h ≈ RunPod's price**; its 16 vCPU fed the 3090 harder than RunPod's community pod
did (4,472 vs 3,176 e2e, render fully hidden). The theoretical ~2× cheaper needs the
sub-$0.14 US/Asia listings — but those are exactly the ones that failed (§gotchas). Net:
Vast wins on price/page *when it works*, RunPod wins on reliability.

**Azure free tier** (asked): F0 gives 500 pages/month and caps at the first 2 pages per
document. Against a 1.3M-page backfill that's ~2,600 months and the per-doc cap makes it
useless for long scans. It does not change the comparison — Azure is only the cost
reference baseline, not a pipeline component. Bulk $0.0015/page is the right number.

### Two Vast gotchas the price tag hides (both cost you can't see in $/h)

1. **Hardware roulette (vCPU starvation).** MinerU's per-page crop/preprocess is
   CPU-bound. The cheap 3090 listings are cheap partly because they ship with tiny vCPU
   counts. A $0.12/h 3090 with **5 vCPU** ran the GPU at **4% util → 355 p/h → $0.68/1k**,
   ~10× *worse* than RunPod. **Filter `cpu_cores_effective>=16`** — that lands ~$0.11–0.14/h
   (still half RunPod), but the sub-$0.10 listings are a trap for this workload.
2. **Pull-time roulette — it's DATACENTER PEERING, not image size.** The 23.9 GB image
   took **6–23 min** to cold-pull across hosts; the US `137.175.76.x` datacenter hit a
   **layer retry-loop on BOTH the 23.9 GB and the 12.9 GB slim image** (so slimming did not
   fix that host), while EU hosts (NL, FR) pulled both fine. Advertised `inet_down` (a
   speedtest number) does not predict GHCR-to-host throughput, and **authenticating the
   pull with a GitHub token did NOT help** — auth raises rate-limit quotas, not raw
   transfer speed. The slim image *did* cut pull time on a good host (**FR: ~4.5 min for
   12.9 GB**), but **pick an EU host for reliable GHCR peering** — that's the bigger lever
   than image size for the pull itself.

Six Vast hosts were tried before one worked (2× stall/retry-loop on the US datacenter,
1× CPU-starved at 5 vCPU, then the FR 16-vCPU host succeeded). Host/peering roulette is
real — but a verified EU ≥16-vCPU host gives a clean, RunPod-beating run.

### Recommendation

- **Cost was never the constraint** — the whole 1.3M-page WOO backfill is ~$36 (Vast) to
  ~$72 (RunPod). Wall-clock is the constraint, and N-pod parallelism (skip-marker split,
  README §two-pod) solves it for the same total $.
- **RunPod stays the burst default** — fast pull, predictable specs, already automated
  (`ocr_drain/pod.py`). **A verified EU ≥16-vCPU Vast host is cheaper per page and faster**
  (measured $0.045/1k @ 4,472 p/h), but you pay in host-selection effort and pull-reliability
  variance — best for a *standing* pipeline, not one-shot bursts. **Salad** only pays off
  for a permanent stateless service (needs re-engineering off SSH).
- **Filter Vast hard:** `cpu_cores_effective>=16` AND `verified=true` AND an **EU
  geolocation** (GHCR peering). The cheap sub-$0.14 US/Asia listings are the trap.
  These filters + disposable-host reroll + an optional throughput gate are encoded in
  **`experiments/ocr_drain/vast.py`** (pod.py's ergonomics for Vast): `vast.py up
  [--gate data/ocr_testset]` rents the cheapest matching host, waits for the pull,
  auto-destroys + rerolls on a stall/retry-loop or low throughput, and prints the direct
  ssh. That closes most of the RunPod-vs-Vast friction gap.

## 3. Image slim — BUILT, VALIDATED, PUSHED (`ghcr.io/chrishonselaar/ocr-mineru:slim`)

`Dockerfile.slim` (this dir) is a multi-stage rebuild: drop the `runpod/pytorch:...-devel`
base (full CUDA *toolkit* + a duplicate torch — v1 shipped torch 2.4.0 from the base AND
vLLM's own torch 2.9.0) for a `nvidia/cuda:12.4.1-runtime` base carrying only vLLM's
self-contained CUDA pip wheels, copied in as one venv.

**Result: 23.9 GB → 13.1 GB (45% smaller).** Validated on the FR Vast run:
- Functional — vllm + mineru + pymupdf import, model loads, worker runs at 100% GPU.
- **Output parity** — scored 0.614 sim / 0.909 F1 on the 19 GT pages (`vast_slim` column
  in `gt_scores.md`), within noise of RunPod's fresh 200-DPI run (0.623/0.912) and the v1
  baseline (0.627/0.913). No "!!!!" regression.
- Throughput identical-to-better: 4,472 p/GPU-h e2e (§2).

**Two runtime deps the slim base was missing (both now in the Dockerfile, found the hard
way on the FR run):**
1. `python3-distutils` — the copied venv's setuptools imports `distutils.core` from the
   base python stdlib; Ubuntu ships it in a separate package the `python3` metapackage
   skips. Without it: `ModuleNotFoundError: No module named 'distutils.core'` at LLM() init.
2. `gcc` + `python3-dev` — vLLM's Triton backend JIT-compiles CUDA kernels at runtime and
   needs a C compiler + Python.h (build-essential in the builder stage does NOT carry over).
   Without it: `RuntimeError: Failed to find C compiler` at engine init.

**Two published variants** (GHCR, both validated):
- **`:slim`** (13.1 GB) — lean default; the worker pulls the 2.2 GB weights from HF at boot
  (~45 s). Best for one-shot bursts where the smaller image = faster cold pull.
- **`:slim-weights`** (15.4 GB) — MinerU2.5-Pro weights baked into the HF cache => **ZERO-
  network boot** (no HF pull, no HF rate-limit when fanning out N pods). Trade: +2.3 GB pull.
  Wins for N-pod bursts / flaky-HF / repeated use on a host that caches the image. Built via
  `--build-arg BAKE_WEIGHTS=1` (last layer, so it shares every layer with `:slim`).

**Layer order fixed:** `COPY --from=builder /opt/venv` now precedes the runtime `apt` layer,
so editing runtime deps re-pushes only the small apt layer, not the ~9 GB venv. (This session
hit that: the `distutils`+`gcc` fixes forced a full venv re-push because apt came first.)

Build/validate/push checklist is in the `Dockerfile.slim` header; the vLLM-0.11.2 pin is
load-bearing (README "Traps": newer vLLM emits "!!!!" for Qwen2-VL — re-validate page output
before bumping).
