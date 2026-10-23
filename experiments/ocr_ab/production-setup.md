# OCR production setup — recommended stack + everything the A/B taught us

Distilled from the round-1/round-2 OCR A/B (see `results/RESULTS.md` for the raw
scoreboard). This is the "if we ship the OCR tail, here's what we know" document.

## TL;DR — recommended stack (best known as of 2026-07-22)

| Dimension | Choice | Why |
|---|---|---|
| **Engine** | **MinerU2.5-Pro** (`opendatalab/MinerU2.5-Pro-2605-1.2B`) | 0.913 token-F1 vs human GT — closest open engine to Azure DI's 0.991; stock Qwen2-VL (no custom remote code); no looping on any category |
| **Serving** | vLLM **0.11.2** (pinned) | serves MinerU correctly; 0.25.x emits `!!!!` garbage for Qwen2-VL |
| **Backend** | in-process **`vllm-engine`** + `batch_two_step_extract`, with `executor=ThreadPoolExecutor(max_workers=32)` | ~15% faster than an HTTP server + concurrent client, and no server to babysit; the executor parallelizes the region crops for a further **+15% (measured)** — stacks to ~14,000 p/GPU-h on a 3090 |
| **Hardware** | **RTX 3090**, community ~$0.22/hr, concurrency ~24 | best pages/$ by far — workload isn't compute-bound, so cheap bandwidth+price beats raw FLOPs |
| **Image** | baked `Dockerfile` → **published: `ghcr.io/chrishonselaar/ocr-mineru:v1`** | dodges the PyPI-lottery boot install; ~$0/mo on ghcr. **Currently PRIVATE** — flip to public (or give pods a ghcr pull token) before burst pods can pull it |
| **Prompt/flow** | MinerU two-phase (`two_step_extract`), NOT whole-page prompting | naive whole-page prompting makes small OCR VLMs loop |

**Cost:** ~12,600 pages/GPU-hour steady-state on a 3090 = **~$0.017 per 1,000 pages** at
the $0.22 community rate — **~86× cheaper than Azure DI** ($0.0015/page = $1.50/1,000),
at 0.913/0.991 of its content fidelity. Keep Azure as the trusted reference / fallback.

## Quality — token-F1 vs 19-page human GT (content, markup-stripped)

| engine | F1 | note |
|---|---|---|
| azure_di (reference) | 0.991 | trusted baseline |
| claude_vision | 0.983 | Claude Fable 5, plain "transcribe" prompt, 1 page/call; NOT open/local — see below |
| claude_vision_sonnet5 | 0.978 | Sonnet 5, same protocol; skipped illegible handwriting rather than guessing |
| claude_vision_opus48 | 0.968 | Opus 4.8, same protocol; loss is mostly skipped printed redaction labels (prompt-fixable), not misreads |
| **MinerU2.5-Pro** | **0.913** | winner (open engines); structured HTML/MD out |
| olmOCR | 0.841 | uniform, no catastrophic class |
| PaddleOCR-VL-1.5 | 0.837 | ↑ from 1.0's 0.797; 1.0 looping fixed; usable now |
| GLM-OCR | 0.816* | *partial (8/19 pp) — ELIMINATED: loops on faint scans (koeweit p1 F1=0.09) |
| PaddleOCR-VL-1.0 | 0.797 | round 1 |
| native / GOT-OCR2 | 0.561 / 0.549 | baselines |

`sim`-to-Azure is *lower* for MinerU (0.63) than plaintext engines despite higher F1 —
because MinerU emits HTML tables that tank char-level similarity while nailing content.
For BM25/search that's a feature; for exact plaintext parity you'd flatten its tables.

### Frontier-VLM check (claude_vision, 2026-07-22)

Prompted by the client's observation that "AI vision" (uploading PDFs to Claude under a
Max subscription) beats OCR engines. Replicated: one Claude vision agent per GT page,
plain "transcribe all visible text" prompt, no OCR-specific tooling. **F1 0.983 — the
client's perception is correct**: frontier vision is statistically at Azure DI level
(0.991) and clearly above MinerU (0.913). Crucially it has **no catastrophic class**:
on MinerU's three worst pages (dashboard p11 0.48, seawatch p43 0.60, letterpress p13
0.86) Claude scores 1.00/1.00/1.00; its own worst page is the faint koeweit photocopy
at 0.88 (Azure 0.97). Caveats: (a) not a pipeline option at bulk price — API cost
roughly $10–15/1,000 pages ≈ 600–900× the MinerU 3090 setup; a flat-rate consumer
subscription is neither licensed nor rate-limited for archive-scale ingestion; (b) the
risk profile is hallucination (plausible wrong text) rather than visible garbage, so
spot-checks must compare against the image, not just read the output. **Hallucination
audit (token-precision + image adjudication of every disputed token):** 16/19 pages had
precision 1.000 — zero invented tokens — and the two near-empty pages got no invented
content (the classic VLM failure did not occur). All real disputes trace to ONE page of
barely-legible pencil marginalia (koeweit p1), where Claude rendered scrawl as fluent
Dutch ("ondertekening" where the strokes more likely read "instemming") with no
uncertainty marking; several of its other guesses there beat the human GT (GT's
"enjeroend" is a non-word; the strokes read "ingevoerd"). Two other flagged tokens were
GT errors, not hallucinations (Claude correctly read a half-redacted "DGG" and "za 9
nov" that GT recorded as "nº y"). Net: ~0.7% of GT tokens disputed, all confined to
content a human would also have to guess at; mitigation for the rescue tier = prompt for
explicit `[?]` uncertainty markers on illegible readings. **Implication:
frontier VLM is the right rescue tier** — route only the worst few % of pages (low
layout confidence / engine disagreement) to it and the blended cost stays ~pennies per
1,000 pages while closing most of the gap to Azure on exactly the pages where MinerU
loses. Outputs in `data/ocr_ab/claude_vision/`; per-page rows in `results/gt_scores.md`.

Model-tier check (same protocol, 2026-07-22): Fable 5 → 0.983, Sonnet 5 → 0.978,
Opus 4.8 → 0.968; all three ≥0.84 on every page (no catastrophic class in any tier).
The Opus/Sonnet deltas are NOT vision misreads: Opus interpreted "skip redacted content"
as also skipping the *printed* Woo ground labels (10.2.e / 5.1.2.e) that the GT counts as
page text (telefax 1.00→0.88, chat p1 0.96→0.91, bzk p69), and Sonnet declined to guess
the koeweit handwriting at all (safer than Fable's fluent guesses, costs recall 0.88→0.84).
Conclusion: any current frontier Claude tier lands in the 0.97–0.98+ band — comfortably
above every open engine — and the residual spread is prompt-design, not model vision.
Rescue-tier candidate = cheapest tier that clears the bar (Sonnet), with the redaction-label
clause fixed in the prompt.

## Throughput — saturated concurrent client, MinerU2.5-Pro, 48-page sample

**Concurrency is the dominant lever: 5–8× over the batch-1 floor.** All cards peak ~24.

| GPU | $/hr | peak p/GPU-h | peak pages/$ | notes |
|---|---|---:|---:|---|
| **RTX 3090** | 0.22c/0.50s | 10,953 (http) / 12,051 (engine) / **~14,000 (engine+threads)** | **~50k–64k** | cost-optimal |
| RTX 5090 | 0.99 | 12,095 | 12,217 | fastest + lowest latency; +43% price → only +20% throughput |
| RTX 4090 | 0.69 | 10,053 | 14,570 | |
| A40 | 0.44 | 5,220 | 11,864 | 48 GB wasted (never OOM on a 1.2 B model) |

Key facts:
- **Not compute-bound at the knee.** FP8 on the 4090 gave NO gain (bf16 peak 10,053 >
  FP8 9,776); the 5090's ~2× compute bought only +20%. So don't pay for FLOPs.
- **In-process engine backend is ~15% over HTTP** (~12,600 vs 10,953 on the 3090). Modest
  because the real cost is shared: client-side region crop/preprocess + the two-phase
  layout→recognition barrier. Removing HTTP/base64 only recovers that overhead.
- **Cross-card numbers are pod comparisons, not pure-GPU** — image preprocessing is partly
  CPU-bound, and pods bundle wildly different vCPU counts (5090 pod: 12 vCPU; 3090 pods:
  32–256). Prefer high-vCPU pods for this workload.

## The bottleneck and how to attack it

`batch_two_step_extract` runs strictly serial: **layout(all pages) → [single-threaded
crop/preprocess of ~13 regions/page → ~619 crops for 48 pages, GPU idle] → recognition**.
Two independent problems: crops aren't parallel, and the two GPU phases don't overlap.

1. **Thread the crops (free, built-in).** `MinerUClient(backend="vllm-engine",
   vllm_llm=llm, executor=ThreadPoolExecutor(max_workers=32))` → parallelizes
   `prepare_for_extract` across pages (verified in mineru_vl_utils source; serial without
   an executor). **Do this.** **MEASURED (2026-07-22, 3090 community, 28 vCPU, 48-page
   sample):** no-executor 12,051 p/GPU-h → threads-32 **13,816 (+15%)** → threads-96
   **13,994 (+16%)**. So the executor reclaims most of the serial crop stall for free; the
   gain saturates by ~32 workers (96 adds <2%), and 32 is a safe default. Note this was
   only a 28-vCPU pod — a 128–256 vCPU pod likely widens the gain, since the crop stage is
   CPU-bound. `bench_engine_threaded.py` reproduces it.
2. **Downscale pages to native vision resolution** before layout — fewer vision tokens,
   smaller crops; shrinks both CPU and GPU sides.
3. **Pipeline layout→recognition (the hard part).** The executor does NOT remove the phase
   barrier — that needs a custom `AsyncLLMEngine` driver that submits recognition for a
   page as soon as its layout returns, overlapping with later pages' layout. Only build
   this if a profiler shows GPU-idle > ~15% *after* #1 — likely true on fast cards (4090/
   5090), less so on the cost-balanced 3090. The concurrent HTTP client already gets #1+#3
   implicitly via its threads, which is why it reaches 87% of the engine batch's throughput
   despite the HTTP tax.

## Ops lessons / traps (each cost real time)

- **PyPI bandwidth is a lottery from RunPod** — measured 20 KB/s–5 MB/s across pods; a bad
  draw makes the 2.5 GB torch+vLLM install multi-hour. On 2026-07-22 it was broadly choked
  (4/4 pods < 150 KB/s) while HF stayed fast (48 MB/s). **Fix: bake the Dockerfile** (this
  dir) so pods never install from PyPI. If installing live, GATE first: test PyPI speed and
  discard the pod (~30 s) if slow, rather than waiting 8 min to find out.
- **vLLM 0.11.2 pin.** 0.25.x → `!!!!` garbage for Qwen2-VL (MinerU). Stable 0.11.2 is
  correct AND runs on Blackwell sm_120 (5090 verified). `--enforce-eager` NOT needed on
  0.11.2 (it was a red herring on 0.25.x).
- **MinerU needs `ninja`** at load (JIT) and, in-process, **`MinerULogitsProcessor`**
  (`LLM(logits_processors=[MinerULogitsProcessor])`, vllm>=0.10.1).
- **Benchmark trap: warm the SAME batch shape, then time.** The first 48-page engine batch
  clocked 4,492 p/GPU-h — pure first-batch CUDA-graph compile; steady-state was ~12,600.
- **Availability:** community had ZERO A40/4090/5090 stock (secure only); 4090 community
  matchmaking chronically fails. 3090 community is available on retry. Secure ≈ 2.3× the
  community price for the 3090 ($0.50 vs $0.22); A40/4090/5090 secure = the deploy-UI price.
- **Disk:** HF cache lives on the root overlay on RunPod pytorch images — delete unused
  model weights there, not just `/workspace`, when space is tight.
- **`pkill -f "vllm serve"` matches your own ssh command line** if the launch string is in
  the same command — kills your shell (exit 255). Use the `"vllm [s]erve"` bracket trick,
  and keep kill and launch in SEPARATE ssh calls.
- **`ssh + nohup … &` hangs** unless stdin is redirected: `< /dev/null` or `( … &)`.

## Tooling in this dir

- `Dockerfile` — the baked burst image. **Built and pushed 2026-07-22 to
  `ghcr.io/chrishonselaar/ocr-mineru:v1`** (23.9 GB, digest `sha256:d3535b46…`; vLLM 0.11.2
  + mineru-vl-utils + pillow + ninja pre-installed; model weights NOT baked — pulled from
  HF at first boot, ~45 s). Pod usage: set the RunPod/Salad/Vast `imageName` to it and the
  boot skips the PyPI install entirely. **Visibility is PRIVATE right now** — either flip it
  public at <https://github.com/users/chrishonselaar/packages/container/ocr-mineru/settings>
  (→ Change visibility → Public) for credential-free pulls, or hand the pod a ghcr pull
  token (`docker login ghcr.io`). Rebuild+push only needs a host with healthy PyPI + Docker.
- `bench_concurrent.py` — saturated HTTP throughput harness (concurrency sweep).
- `bench_engine.py` / `bench_engine_threaded.py` — in-process engine throughput (+ executor).
- `mineru_pages.py` — MinerU two-phase page runner (harness JSON out).
- `rp.py` — RunPod REST helper (create/get/rm/cost). Reads `~/.config/runpod/api_key`.
- `score.py` / `gt_score.py` — scoreboard (sim vs Azure; F1 vs human GT).

## Open items

- ~~Measure the threaded-crops gain~~ **DONE (2026-07-22):** +15% (threads-32) / +16%
  (threads-96) over the no-executor engine backend on a 28-vCPU 3090 → ~14,000 p/GPU-h.
  `executor=ThreadPoolExecutor(max_workers=32)` is now the recommended default.
- Optional: the async layout→recognition pipeline, only if profiling shows residual idle.
  The +16% ceiling from threading alone suggests a phase-barrier still remains, but on the
  cost-balanced 3090 it's likely not worth the custom `AsyncLLMEngine` driver.
- Optional: FP8 is a dead end for this workload (no throughput gain) — skip.
- Optional: bake weights into the image / a network volume ($0.07/GB/mo) only for a
  multi-model zoo — not worth it for MinerU alone (HF re-download is ~45 s).
