# OCR A/B — results log

Status (2026-07-21): **round 1 complete** — Azure DI (reference), native, and GOT-OCR2.0
scored, incl. against 19 pages of human-corrected ground truth. **Verdict: GOT-OCR2.0 is
eliminated** (0.51 vs GT, against Azure's 0.915; systematic repetition loops on
table/form/sparse pages — dot leaders, fax forms, landscape tables). Killed during the
meitellingen file after 9/11 files; measured **368 pages/GPU-hour** on the local RTX
3050 4GB (batch-1 HF generate — a floor, not a ceiling; loops burn 3-10× tokens/page).
Per-category sim-to-Azure: photocopy 0.87, letterpress 0.74, email-scan 0.70,
redacted-scan 0.55/0.27, chat 0.41, dashboard 0.28, typewriter 0.17, telefax 0.01.
Next round: olmOCR / PaddleOCR-VL / DeepSeek-OCR on a rented GPU
(`remote-vlm-runbook.md`), scored with the same harness against the same GT.

**Success criterion (Chris, 2026-07-21): Azure DI is the reference. An open engine wins
by being close enough to Azure (per-category `sim` in scores.md + spot-check) at orders
of magnitude lower cost.** Ground truth exists only to sanity-check the reference
itself: `make_review_app.py` builds `data/ocr_ab/review/index.html` (image left,
editable transcript right, seeded with Azure + judged corrections; export drops
`corrections.json` into `results/`).

## Runs completed

| Engine | Coverage | Cost | Wall time |
|---|---|---|---|
| native (PyMuPDF) | 11/11 files, 396 p | free | seconds |
| azure_di (prebuilt-read) | 11/11 files, 396 p | $0.59 ($0.0015/p) | ~93 s API time |
| got_ocr2 (580M, local RTX 3050 4GB) | running | electricity | — |

Automatic proxies per engine/file: see `scores.md` (chars/page, density confidence,
exemption-label counts). Side-by-side spot-check dumps: `data/ocr_ab/judge/`.

## VLM-judge spot-checks (page image vs engine text)

Judged so far (Fable 5 as judge, page JPEG as reference):

- **chat_screenshots p12 (dark-mode WhatsApp)** — Azure near-perfect: the one visible
  message verbatim, all 4 redaction labels (`5.1.2.e`×2, `5.2.1`×2), timestamps; one
  glyph error ("wodafone"). **No hallucination across redaction stickers.** Native: empty.
- **seawatch p43 (~95% black boxes)** — Azure emits exactly the 4 visible fragments,
  nothing invented. The doc's own text layer behaves the same (redactions are whitespace
  in the native layer).
- **vuurwerkramp typewriter p1 (1989 photocopy)** — Azure complete and correct incl.
  yellow `10.2.e` label; reading order slightly interleaved (stamp column); "ln"→"In".
- **zeemansgraf p13 (19th-c. letterpress)** — both Azure and the publisher's embedded
  OCR layer are good. Weak spot for both: dot-leader budget tables — Azure detaches two
  amounts from their `Artikel` lines; native has glyph noise (`I^osten`, soft hyphens).
- **bzk_scanprint p69 (2022 scan-of-print, reference case)** — Azure essentially
  perfect: all mail headers, black-box `5.1.2.e` labels read off the boxes, "valt buiten
  reikwijdte" markers, full body text. Native layer: empty (8.7× char gain from OCR).
- **onderwijsinspectie p11 (media-monitoring dashboard)** — Azure captures every stat,
  label and redaction chip; row/column association is lost (values stream as separate
  lines — inherent to plain-text output). The native layer is far worse: scrambled order
  plus junk glyphs (`■`, `)`, `C`). For BM25 findability Azure is fine; structured reuse
  of dashboards would need layout-aware output.
- **koeweit p20 (washed-out photocopy, ghost-faint text)** — Azure reads only the three
  dark stamps and skips the ghost text (conservative, no hallucination); the publisher's
  embedded layer emits pure glyph noise (`I ^`, `fc`, `IB`...). Faint-copy recall is a
  real axis to score the open engines on.
- **bestuurskosten p6 (born-digital control)** — Azure output ≡ native content. Needless
  OCR is a pure cost leak here, not a quality risk ("do no harm" holds).

## Early findings

1. **Azure DI is a strong baseline on every category**, including the two categories
   flagged as production pain points (dark-mode chat screenshots, redacted scan-print).
   The interesting A/B question is whether open engines can *match* it at lower cost,
   not whether it suffices.
2. **Redaction fidelity (Azure): excellent.** It reads exemption labels printed on/next
   to boxes (1,312 labels found in bzk, 264 in seawatch, 63 in chat screenshots — see
   scores.md) and does not hallucinate across boxes on any judged page.
3. **Several "scan" files carry a publisher OCR text layer** (zeemansgraf 1221 cpp,
   koeweit 1119 cpp, seawatch 721 cpp native). A naive "no text → OCR" gate underrates
   these; the density heuristic would OCR them anyway — often duplicating acceptable
   text. Cost-model input: the *marginal* OCR gain varies hugely per file (bzk +8.7×,
   zeemansgraf ≈ 1.0×).
4. **The false-trigger control works as designed**: bestuurskosten native conf = 0.10
   → gate would fire → $0.02 wasted per such doc, output unchanged. At corpus scale this
   argues for the pattern-check guard, not just density.
5. Table structure (meitellingen, zeemansgraf dot-leaders) is the weakest axis for
   plain-text OCR output across engines — matches the strategy doc's expectation that
   layout-aware output formats matter for the table categories.

## Open arm — status

- **GOT-OCR2.0** (580M): only strategy-doc candidate that fits the local 4 GB RTX 3050;
  run in progress, will yield pages/GPU-hour on consumer hardware.
- **olmOCR (7B), PaddleOCR-VL (0.9B), DeepSeek-OCR, dots.ocr**: need a rented GPU
  (RunPod/Vast class, per design/ocr-strategy.md). Decision + account needed from Chris.
- Stale local HF token note: `~/.cache/huggingface` token fails signature checks and
  breaks even anonymous downloads; runners pass `token=False` as a workaround.

## Round 2 (2026-07-21) — MinerU2.5-Pro / GLM-OCR / PaddleOCR-VL-1.5 + throughput curve

Rented RunPod GPUs (3090 community + A40/4090/5090 secure). Scored on the same 19-page
human GT (F1 = content token-overlap, markup stripped; the metric that matters since the
document-parsers emit HTML tables that tank char-`sim`).

### Quality — mean token-F1 vs human GT

| engine | F1 | verdict |
|---|---|---|
| azure_di (reference) | 0.991 | trusted baseline |
| **mineru25_pro** | **0.913** | **WINNER** — closest open engine to Azure; structured HTML/MD out; no looping on any category |
| olmocr | 0.841 | uniform, no catastrophic class |
| paddleocr_vl_15 | 0.837 | ↑ from 1.0's 0.797; **1.0 looping fixed** (koeweit 1.4 s/p not 16.9) |
| glm_ocr | 0.816 | PARTIAL (8/19 pp) — **eliminated**: loops catastrophically on faint scans (koeweit p1 F1=0.09, 3.6k chars of repetition); great on clean docs only |
| paddleocr_vl | 0.797 | round-1 |
| native / got_ocr2 | 0.561 / 0.549 | baselines |

MinerU2.5-Pro is stock Qwen2-VL (no custom remote code; runs on stable vLLM 0.11.2 — the
bleeding-edge 0.25.1 emits `!!!!` garbage for Qwen2-VL). Two-phase layout→recognition via
`mineru-vl-utils` http-client; naive whole-page prompting loops (same trap as Paddle/GLM).

### Throughput — saturated concurrent client (MinerU2.5-Pro, 48-page sample)

Harness `bench_concurrent.py` fires N concurrent `two_step_extract` calls. **Concurrency is
the dominant lever: 5–8× over the batch-1 floor.** All cards peak at concurrency ~24–48.

| GPU | $/hr | peak p/GPU-h | peak pages/$ | vs Azure ($0.0015/p) |
|---|---|---|---|---|
| **RTX 3090** | 0.22c / 0.50s | 10953 | **49.8k c / 21.9k s** | **~33–75× cheaper — cost-optimal** |
| RTX 5090 | 0.99 | 12095 | 12.2k | fastest per-GPU + lowest latency |
| RTX 4090 | 0.69 | 10053 | 14.6k | — |
| A40 | 0.44 | 5220 | 11.9k | 48 GB wasted (never OOM) |

Findings: (1) **not compute-bound** at the knee — FP8 on the 4090 gave NO gain (bf16 peak
10053 > FP8 9776), and the 5090's 2× compute bought only +20%. Bottleneck is the two-phase
request fan-out (1 layout + N recognition HTTP calls/page) + CPU image preprocessing.
(2) **3090 is cost-optimal** — high bandwidth (936 GB/s) + rock-bottom price beats everything
on pages/$, even at secure rates. (3) Mitigation levers for the fan-out: in-process
`vllm-engine` backend (kill HTTP/base64), batch the recognition crops, GPU-side JPEG decode,
prefix-cache the identical layout prompt. CAVEAT: cross-card = pod comparison (vCPU differs:
5090 pod 12 vCPU vs 3090 32) since preprocessing is partly CPU-bound.

Ops: community had ZERO A40/4090/5090 availability (secure only); 4090 community matchmaking
fails per prior runbook note. RunPod helper `rp.py` (create/get/rm/cost) — session scratchpad.

### In-process engine backend (`vllm-engine`) — ~15% over http, and where the real gains are

`MinerUClient(backend="vllm-engine")` + `batch_two_step_extract` on the 3090, steady-state:
**~12,600 p/GPU-h vs the http-client's 10,953 = ~15% faster** (pages/$ ~57k vs ~50k; ~86×
cheaper than Azure). Needs `MinerULogitsProcessor` (vllm>=0.10.1). **Benchmark trap:** the
first 48-page batch clocked 4,492 p/GPU-h — pure first-batch CUDA-graph compile for that
shape; warm with the SAME batch shape, then time iter2+.

Only ~15% because removing HTTP/base64/JSON recovers just that overhead — the **dominant cost
is shared by both backends: single-threaded client-side region preprocessing (crop+resize the
~13 regions/page → ~619 crops for 48 pages, the "Adding requests ~10s" phase, GPU idle) plus
the hard layout→recognition barrier.**

**Next optimization (backend-agnostic, the real lever):** overlap CPU preprocessing with GPU
compute and pipeline the two phases. Concretely: (1) thread/parallelize the region crops
(ThreadPool/Process pool across the 32-128 vCPUs, or GPU-side tensor-slice crops via nvjpeg/DALI
— kills the ~10s serial phase); (2) stream layout→recognition per page via `AsyncLLMEngine` so
recognition of early pages overlaps layout of later pages (no barrier, GPU never drains); (3)
downscale pages to native vision resolution first (fewer vision tokens). The concurrent http
client already does (1)+(2) implicitly via its 48 threads — which is why it reaches 87% of the
engine batch's throughput despite the HTTP tax. Best-of-both = in-process AsyncLLMEngine + a
threaded crop pool + streaming submission → expected to beat both current numbers.
