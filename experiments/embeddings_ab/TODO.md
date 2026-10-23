# TODO — embeddings/search next experiments

Next-session work, deferred from the 2026-07-22/23 session. Full context:
[`FINDINGS.md`](FINDINGS.md). All reproducible from checked-in scripts + **local saved vectors**
(`embeddings/<key>.{doc,q}.f16.npy`, ~700MB, LOCAL ONLY / gitignored — on this machine, not in
git; regenerate with `embed_eval.py` / `voyage_eval.py` if missing). Ordered by leverage.

## 1. Stronger reranker A/B  — ✅ DONE (2026-07-23), decision: Voyage `rerank-2.5`
**Result:** a strong reranker lifts bge-m3 top-10 ordering **+0.187 nDCG@10 (0.515 → 0.702)**,
pooled scoring. The old "~0.42 ceiling / equalizer" was an artifact of a weak 568M reranker +
single-gold scoring. **Voyage `rerank-2.5` beats Cohere `rerank-v3.5`** (0.702 vs 0.620 over
bge-m3) and lands within 0.025 nDCG@10 of the best-embedder base — so **self-hosted bge-m3 +
Voyage rerank API** is the production stack (CPU-only box, no fixed GPU, ~$0.002/search). Went
with **managed APIs, not a GPU-served open model** (Chris: a small always-on rerank GPU is a fixed
cost we can barely afford; reranker is decoupled from the embedder so an API is fine). Full writeup:
`rerank_api_findings.md`. Harnesses: `rerank_prep.py` → `rerank_api.py` → `rerank_repool.py` →
`rerank_score.py`; `rerank_pod.py` (open-model GPU runner) is ready but **not run** — the ceiling
check (Qwen3-Reranker-4B / mxbai-v2, "could a zero-API self-hosted reranker beat Voyage") was
deferred as low-value once the APIs cleared 0.70.

## 2. Related-documents / recommend demo + eval  (the embedding sweet spot)
**Why:** doc-to-doc kNN is where dense genuinely beats BM25; the saved doc vectors ARE the
recommendation index (zero new embedding). See FINDINGS.md §8. High value for journalist persona.
**How:** (a) demo — pick ~10 real besluiten, cosine-NN their `bgem3.doc` vectors, eyeball the
neighbours; (b) eval — sample source docs, take top-k neighbours, Haiku-judge relatedness
(reuse the pooled-judge pattern in `pooled_eval.py`). Compare doc-level (mean-pooled chunks or a
summary embedding) vs chunk-level. Also worth: does commercial (voyage-4-large) cluster better
than bge-m3 for recommend? (vectors already saved for both).

## 3. Weighted / learned hybrid fusion  (RRF is untuned)
**Why:** current hybrid uses equal-weight RRF (k=60). On keywords dense is weaker, so weighting
BM25 higher (or a learned combiner) may lift precision. See `keyword_search_findings.md`.
**How:** in `keyword_retrieval.py`, parameterize the RRF weights / try weighted-sum of normalized
scores; sweep weights; re-score with `keyword_pooled.py` (pooled). Also try convex combo of
min-max-normalized BM25 + cosine.

## 4. LLM query-expansion for short queries  (lift the dense channel)
**Why:** bare 1-4 word queries embed poorly (dense's weakness on keywords). An LLM can expand a
keyword into a richer semantic query for the dense side (BM25 keeps raw terms), and detect
intent (kenmerk → exact lookup). See FINDINGS.md §9.
**How:** add an expansion step before dense encoding in `keyword_retrieval.py` (Haiku: keyword →
1-2 sentence semantic query); re-run dense + hybrid; compare pooled metrics vs the no-expansion
baseline. Watch cost/latency (per-query LLM call).

---

## Notes / gotchas for whoever picks this up
- **SCORE WITH POOLED MULTI-RELEVANCE, not single-gold** — single-gold understates badly
  (~22 valid answers/query) and would drive wrong conclusions (it already nearly did twice).
  Reuse `pooled_eval.py` / `keyword_pooled.py` (Haiku judge; `pooled_qrels.json` reusable for the
  question set). Robust metric = **success@10**; recall@100-pooled is mildly optimistic (relevants
  pooled from methods' own top-K).
- **API key:** Haiku + Voyage keys are in `civic-indexer/.env` (in-project; the classifier blocks
  reading other projects' .env). Load in-script, never print.
- **Process hygiene (bit us):** `pkill -f` silently failed twice → kill by explicit PID + verify
  with `ps`. Heredoc-over-ssh rewrites didn't persist → scp scripts. Verify scp downloads BEFORE
  tearing down a pod. RunPod: 3090 COMMUNITY works; kill+verify pod gone with `rp.py pods`.
- **HF stale-token trap:** local HF downloads 401'd on a stale token → run with
  `env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN -u HUGGINGFACE_HUB_TOKEN HF_HUB_DISABLE_IMPLICIT_TOKEN=1`.
- **Deferred earlier, still open:** should `design/README.md` add an explicit "rerank stage" to
  the settled search decisions? (Chris was asked in the OCR-era handoff, never answered.)
