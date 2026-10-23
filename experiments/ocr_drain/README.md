# OCR-tail drain runbook (roadmap W1)

Drains `deferred_queue` reason `ocr_tail` through a RunPod 3090 burst pod running
MinerU2.5-Pro (engine selection + throughput economics: `../ocr_ab/production-setup.md`).
The engine half lives in `engine/drain.py` (`civic drain ocr_tail`); this dir holds
the pod half.

```bash
# 0. before-measurement + queue summary
du -sb data/blobs/ && uv run civic drain ocr_tail

# 1. stage payload (symlinks + manifest.json — no local copy)
uv run civic drain ocr_tail --export /tmp/ocr_export

# 2. pod up (image ghcr.io/chrishonselaar/ocr-mineru:v1 is PUBLIC; boot pulls the
#    23.9 GB image — pick EU country codes: faster payload upload AND a fast-link
#    machine; a US 91 Mbps draw meant a ~35 min image pull, the FR 2.5 Gbps one ~2 min)
python3 experiments/ocr_drain/pod.py create "NVIDIA GeForce RTX 3090" ocr-drain COMMUNITY \
    ghcr.io/chrishonselaar/ocr-mineru:v1 NL,DE,BE,FR,GB,SE,AT,CZ,PL,ES,IT,PT,DK,FI,NO,CH
python3 experiments/ocr_drain/pod.py get <id>       # poll for ip= and ports={'22': NNNNN}

# 3. ship + run (tar over ssh: the image has NO rsync; -h resolves export symlinks)
tar -chf - -C /tmp/ocr_export . | ssh -p PORT root@IP 'mkdir -p /root/pdfs && tar -xf - -C /root/pdfs'
scp -P PORT experiments/ocr_drain/ocr_worker.py root@IP:/root/
ssh -p PORT root@IP 'pip install -q pymupdf'
ssh -p PORT root@IP 'cd /root && (nohup python3 ocr_worker.py pdfs out > ocr.log 2>&1 &) '
ssh -p PORT root@IP 'tail -5 /root/ocr.log; ls /root/out | wc -l'   # poll

# 4. results back + ingest (per-source upgrade runs; deletes blobs, resolves queue)
ssh -p PORT root@IP 'cd /root/out && find . -name "*.md" -size +0c | tar -cf - -T -' | tar -xf - -C /tmp/ocr_results
cp /tmp/ocr_export/manifest.json /tmp/ocr_results/
uv run civic drain ocr_tail --ingest /tmp/ocr_results

# 5. re-embed upgraded docs on the SAME pod (embed_pod workflow; hash cache
#    means only upgraded docs export). Pin transformers — see traps.
uv run python experiments/embed_pod/export_chunks.py haarlem_raad woo_besluiten vlaardingen_raad > /tmp/chunks.jsonl
scp -P PORT /tmp/chunks.jsonl experiments/embed_pod/pod_embed.py root@IP:/root/
ssh -p PORT root@IP 'pip install -q "sentence-transformers==3.4.1" "transformers==4.49.0" && cd /root && python3 pod_embed.py'
scp -P PORT root@IP:/root/vectors.jsonl.gz /tmp/
uv run python experiments/embed_pod/import_vectors.py /tmp/vectors.jsonl.gz

# 6. TEAR DOWN (always), then doc vectors + projection + after-measurement
python3 experiments/ocr_drain/pod.py rm <id> && python3 experiments/ocr_drain/pod.py pods
for s in haarlem_raad woo_besluiten vlaardingen_raad; do uv run civic embed $s && uv run civic project $s; done
du -sb data/blobs/ && uv run civic drain ocr_tail    # expect ocr_tail queued: 0
```

## Safety contract (why ingest can delete blobs)

`drain.evaluate_result` gates the irreversible step: a blob is deleted only when
the result `.md` exists, its `<!-- page N -->` count equals the page count recorded
at defer time, and it carries ≥200 B of real text. Missing/mismatched results stay
queued for the next burst; thin-text results resolve but keep the blob for audit.
The worker writes outputs atomically (`.tmp` + rename) and skips existing `.md`s,
so a crashed pod run is rerunnable and never hands ingest a truncated file.

## Learned in the first production drain (2026-07-23, 166 docs / 6,458 pages, ~€0.55)

- **End-to-end throughput is ~3.4–3.9k pages/GPU-h**, not the 12.6k benchmark number.
  (The explanation we believed that night — serial render + small-doc batching — was
  only ~15% of it; the real story is that the 12.6k itself was a prefix-cache artifact.
  See the 2026-07-24 section below.) Budget ~$0.06/1k pages — still ~25× cheaper than
  Azure DI.
- **The container OOMs on giant scans without chunking** (a 1,472-page PDF vs the
  50 GB cgroup limit) — PAGE_BATCH=48 in the worker fixes it. After an OOM kill, the
  zombie vLLM engine can keep ~24 GB VRAM allocated: `kill -9` the leftover PID that
  `nvidia-smi --query-compute-apps` shows, then relaunch (the worker resumes).
- **RunPod's "container is unhealthy (OOM)" console banner is sticky** — it repeats
  until the *container* restarts even when current usage is far under the limit; read
  `/sys/fs/cgroup/memory.current` for the truth.
- **Two pods split by pages halve wall-clock for ~nothing** ($0.22/h): balance docs by
  page count, `touch out/<stem>.md` skip-markers on pod 1 for pod 2's share, and pull
  pod-1 results with `find -size +0c` so the empty markers never reach ingest.
- Thin-text keeps are real and fine: 8/166 docs (1-page stamp/drawing scans) produced
  <200 B of text; their blobs stay `kept=true` for audit, everything else deleted —
  ledger and disk stayed byte-exact through the whole drain.

## DONE 2026-07-24: worker restructured + re-benchmarked — and the 12.6k bench number was a cache artifact

The §PARKED plan (items 1–4) was executed in the 2026-07-24 benchmark session.
Worker restructured (`ocr_worker.py`): page renders run in a thread pool that stays
`PREFETCH=3` super-batches ahead of the GPU; pages from consecutive docs are packed
into cross-document 48-page super-batches; per-doc `.md`s are reassembled and written
atomically; a failing batch retries per-doc so one poison doc can't sink batchmates.
Output proven byte-identical to the old worker (stubbed-OCR diff at PAGE_BATCH 48/7/5),
and a full re-run of the W1 production corpus gave 166/166 docs with marker counts
exactly matching the ledger (both 1.4k-page giants included; memory stayed bounded).
`pod.py create` gained a `minVcpu` arg (`minVCPUPerGPU`) — the benchmark pod drew
128 vCPU at the same $0.22/h.

**Measured (3090 community, FR, 128 vCPU, 200 DPI, render included):**

| run | corpus | old worker | new worker |
|---|---|---:|---:|
| same-pod A/B | 497-page stratified sample | 4,631 p/GPU-h | 6,397 p/GPU-h |
| full re-run | exact W1 drain corpus, 166 docs / 6,458 pp | 3.4–3.9k (production 2026-07-23) | **4,014 e2e / 4,056 steady** |

Render is now fully hidden: over the full run, 99.7% of wall time was inside
`batch_two_step_extract`, 0.2% render wait. **Worker-side optimization is finished** —
the worker runs at ~95% of the true GPU ceiling.

**Why this is far below the ≥10k target — the target was a phantom.** A 2×2 probe
({A/B bench pages, drain pages} × {fresh, repeat}) on the same pod:

```
ab_fresh      4,704 p/GPU-h        drain_fresh    4,870 p/GPU-h
ab_repeat    12,884 p/GPU-h        drain_repeat  12,189 p/GPU-h   (ab_repeat2 13,141)
```

Fresh pages from BOTH corpora run ~4.7–4.9k; repeats of already-seen images run
~12–13k. The old `bench_engine*` harnesses warmed on the same 48 images they then
timed (best-of-2 repeats), and vLLM v1 (0.11.2) has prefix caching ON by default —
including multimodal prefills keyed by image hash. So the "12.6–14k GPU-side proven"
rate was largely cached-prefill replay, and the A/B corpus was never easier than the
drain mix. True 3090 ceiling for MinerU2.5-Pro on fresh 200-DPI civic pages:
**~4.3k p/GPU-h sustained** (per-batch median over 135 real batches; p10 2,959 /
p90 5,158 — content-density spread). The +15% executor measurement in
production-setup.md is cache-contaminated too; its true value is unknown. The
cross-card pages/$ ranking (3090 wins) likely survives — every card was benched with
the same flawed method — but all absolute numbers there are inflated ~2.6×.

**Economics (corrected):** ~$0.055/1k pages end-to-end (~27× cheaper than Azure DI,
not 86×). WOO backfill >1.3M pages ≈ ~320 GPU-h ≈ ~$72 — the money is fine; the
wall-clock needs parallelism: the worker + skip-marker split already supports N pods
(§two-pod trap above), so an 8-pod burst does the WOO set in ~1.7 days for the same $.

**Remaining levers if p/GPU-h itself must rise (all GPU-side, none worker-side):**
1. DPI/downscale A/B — GPU tokens scale with pixels; test 150 DPI vs 200 against the
   19-page GT set before trusting it (quality gate, not just speed).
2. Async layout→recognition overlap inside the extract call (production-setup §3) —
   value unknown now that the executor gain is known to be contaminated; profile GPU
   idle within a fresh batch first.
3. Different engine/quantization — re-run the engine A/B with the corrected protocol.

**Benchmark protocol (use this for all future OCR benchmarks — decision Chris
2026-07-24: keep them short):** 48-page fixed slice of the drain corpus, warm the
batch shape on 48 *disjoint* images (CUDA-graph compile without polluting the prefix
cache), then time a SINGLE fresh pass. Never time repeat passes over the same images
while prefix caching is on. `cache_probe.py` in this dir is the harness; ~6 min of GPU.

## Traps inherited from the A/B sessions (see `../ocr_ab/production-setup.md`)

- 3090 COMMUNITY (~$0.22/h) is the pages/$ winner; other cards are often secure-only.
- Install sentence-transformers only AFTER OCR finishes — transformers 4.49 vs
  vLLM 0.11.2 requirements may conflict; the pod is disposable, order sidesteps it.
- `ssh + nohup … &` hangs without stdin redirect — use `( … &)` or `< /dev/null`.
- First batch includes CUDA-graph compile; judge throughput from steady state.
