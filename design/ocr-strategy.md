# OCR strategy — inline API head, burst GPU tail

**Decision (admin, 2026-07-21).** All bulk OCR runs in **periodic batches** and never
blocks extraction, enrichment, or indexing. During the regular extraction run, only a
few head pages are OCR'd via a per-page cloud API (Azure Document Intelligence class);
the remaining pages accumulate in a queue drained by **burst runs of GPU containers**
on per-minute IaaS, running open-weights OCR VLMs.

## This generalizes W's partial OCR from exception to default

W already proved "searchable head now, tail later": partial OCR with an upgradable
status, `missing: [ocr_tail]`, the PDF kept, a later budgeted upgrade run
(`item-envelope.md`, inventory §9). That was a *budget-pressure exception*. This
decision makes it the **standard shape of all OCR**:

- **Tier 1 — head, inline.** First N pages (CFG, ~3–10) via per-page API during the
  extraction run. Bounded per-document cost, immediate searchability, and enough text
  to feed the theme classifier, the summary, and facet extraction. Cheap and
  latency-flat, so the extraction run never waits on a GPU.
- **Tier 2 — tail, burst.** Everything else lands in the `ocr_tail` cohort. A periodic
  run spins up a GPU container fleet, drains the whole queue **across all sources** in
  one burst, and tears the fleet down.

The non-blocking invariant is the point: OCR is an *upgrade path*, never a pipeline
gate. Every item indexes on metadata + head text alone.

## Why burst: the economics change shape

Per-page APIs (Azure ≈ $1.50/1k pages — the W cost model) price every page identically,
whenever it is processed. Self-hosted open-weights OCR VLMs on per-minute GPU IaaS
(RunPod/Vast.ai class) price in **fleet-hours amortized over the queue** — reported
orders of magnitude cheaper at volume (olmOCR's own benchmark claimed ≈ $190/M pages vs
Azure's ≈ $1 500/M), but only at high utilization. That makes batching structurally
correct, not merely convenient: accumulate a large queue, burst it, tear down. The
deferred/upgradable machinery *is* that queue — it existed before this decision.

**The A/B (2026-07-21/22) confirmed and beat that hypothesis with measured numbers.**
MinerU2.5-Pro on a community RTX 3090 (~$0.22/hr) at ~12,600 pages/GPU-hour steady-state
= **≈ $17/M pages, ~86× cheaper than Azure DI**, at 0.913/0.991 of its content fidelity
(token-F1 vs human ground truth) — better than the olmOCR vendor claim, on Dutch WOO
scans. So the burst bet holds empirically; keep Azure as the trusted reference/fallback.
Full scoreboard, ops traps, and the recommended production stack (pinned vLLM, in-process
engine backend, baked Dockerfile) live in
[`../experiments/ocr_ab/production-setup.md`](../experiments/ocr_ab/production-setup.md).

Consequences for existing machinery:

- **A second cost model** beside per-page: fleet-hours × hourly rate ÷ measured
  pages-per-GPU-hour. That divisor is now a **measured** value, not a vendor claim:
  ≈ 12,600 pages/GPU-hour for MinerU2.5-Pro on a 3090 (in-process vLLM engine backend,
  concurrency ~24). It becomes CFG **per engine × card** — concurrency, not FLOPs, is the
  dominant lever (5–8× over the batch-1 floor; the workload isn't compute-bound). The cost
  predictor and pre-flight budget gates work unchanged on top.
- **A burst trigger** (CFG, per deployment — not per source): queue threshold in pages
  and/or a schedule (e.g. weekly). One fleet drains all sources' queues together.
- **New engine rungs.** The A/B selected **MinerU2.5-Pro** as the default burst engine
  (0.913 token-F1, structured HTML/MD out, no looping); olmOCR (0.841) and PaddleOCR-VL
  (0.837) are usable alternates; GOT-OCR2 and GLM-OCR were eliminated for repetition loops
  on faint scans and tables. The extraction ladder's engine slot was already pluggable
  (`azure|tesseract|auto` → now `+mineru|olmocr|…`), and the envelope records
  `extraction.engine` per item — so mixed-engine corpora and later re-OCR upgrade runs
  were already representable. Engine choice, vLLM version, and the two-phase prompt flow
  are all CFG, not code (see the production-setup doc).

## Quality: A/B with the instrument we already have

The OCR gate (confidence formula + Dutch content-pattern guard + min-chars +
accept-only-if-longer, inventory §9) is the A/B harness: run candidate engines against
the per-page API baseline on a sample drawn from the **worst** W scans (old stamps,
marginal print, skew), not the clean ones. These models are predominantly
English-trained, so Dutch quality on bad scans was the open question — and the run
answered it: the winner scores markup-stripped token-F1 **0.913 against human-corrected
ground truth** (Azure DI reference 0.991) on exactly that worst-scan Dutch sample, with no
catastrophic-failure class. Measured against human GT rather than the live gate, because
the gate's accept/reject is coarser than the fidelity signal we actually wanted.

## Embeddings ride the same pattern

Semantic embeddings batch and burst the same way, on the same fleets. The staged plan is
unchanged — BM25 + facets first, summary-level embeddings second — but burst economics
cheapen the opt-in chunk-embedding tier, softening (not removing) the chunker-freeze
caveat in `envelope-desk-checks.md`. **Candidate update (2026-07-22, full MTEB(nld) CSV):**
on the retrieval task type (our actual metric) the size↔quality curve is **flat at the
top** — the best cheap encoder (`snowflake-arctic-embed-l-v2.0`, 0.57B) scores 62.7 vs the
best 14B model's 67.2, i.e. **~4.5 points for ~25× the params**. So the bulk/opt-in chunk
tier should stay a **cheap encoder** (arctic-embed leads; bge-m3 the safe fallback — arctic
beats the design's original bge-m3 assumption), reserving a 4–8B model only for the
low-volume summary tier if the gap proves worth it. The burst divisor is its own
`TODO(probe)`, not the OCR number. (The hyped legal FT `Euler-Legal-8B` is dominated by its
own base Qwen3-8B even on legal retrieval — dropped.) Evidence, full table, and A/B
shortlist: [`../experiments/embeddings_ab/dutch-mteb-candidates.md`](../experiments/embeddings_ab/dutch-mteb-candidates.md).

## What this cost the design

A tier split inside the existing extraction ladder (STRAT + CFG), a second cost model
and a burst trigger (CFG on existing ENG machinery), and new engine rungs. No new stage,
no envelope change — `ocr_tail`, upgradable statuses, and budgeted cohort runs all existed
already. The A/B task that this decision spun off for the feasibility phase is **done**
(2026-07-21/22): engine selected, both cost models populated with measured numbers, a
production stack recommended — see
[`../experiments/ocr_ab/production-setup.md`](../experiments/ocr_ab/production-setup.md).
