# Can a self-hosted open reranker replace the Voyage API? (2026-07-27)

Asked because the reranker is the one place where document text leaves the system to a
third party, and Voyage's terms have no Art. 28 DPA and no EU transfer mechanism
(`design/access-posture.md` §4). If an open model self-hosted on a burst GPU matched it,
the question would disappear rather than being traded off.

**It does not, on two independent axes — quality and servable latency — and the two are
anti-correlated.** Same gold set, same metric, same stage-1 pool as the API A/B
(`rerank_api_findings.md`): n=250 judged queries, `pooled_qrels_v2.json`, top-100 dense
candidates from `bge-m3`.

## Quality

> ⚠️ **Two corrections landed 2026-07-30, both from the external comparison. Read them
> before using this table to choose anything.**
>
> 1. **"EU region available" in the Cohere row means Azure/Bedrock, NOT Cohere's own API.**
>    On Cohere's own endpoint — the only Cohere route this project has, on a free trial key —
>    the SaaS Agreement §3(a) permits sharing API Data with third parties "for other
>    purposes", §13(d) states Cohere "will have no obligation to delete or otherwise render
>    inaccessible any API Data", **no** clause distinguishes trial from production keys, and
>    the agreement sets **no** geographic restriction. Voyage *with the opt-out set*
>    (done 2026-07-27) gives zero-day retention and no training licence. So on retention and
>    training this table's implied ranking **inverts**: Voyage-with-opt-out is the stronger
>    position and Cohere-trial the weaker. Cohere only wins on the **paid** EU-cloud route.
>    Voyage's remaining gap is paperwork — no published Art. 28 DPA, no SCCs/DPF for
>    `api.voyageai.com`, US processing — not retention. Detail in `access-posture.md` §9.1.
> 2. **`bge-reranker-v2-m3`'s +0.071 is not a floor.** Used on the external comparison it
>    beat the source site, but per query it was **worse than plain `hybrid` on 5 of 12** and
>    destroyed one perfect result page (nDCG 1.000 → 0.811). A mean gain of this size on this
>    model hides a sign flip; the "if even the weakest still wins" argument does not hold.
>    See `experiments/external_comparison/FINDINGS.md` §5.

| reranker | where it runs | pooled nDCG@10 | gain over stage-1 |
|---|---|---:|---:|
| **Voyage `rerank-2.5`** | API (US; opt-out ⇒ zero retention, no DPA/SCCs) | **0.702** | +0.187 |
| **Cohere `rerank-v3.5`** | API (EU residency **only** via paid Azure/Bedrock — see ⚠️) | 0.620 | +0.105 |
| `BAAI/bge-reranker-v2-m3` (568M) | self-hosted | 0.586 | +0.071 |
| `mixedbread-ai/mxbai-rerank-large-v2` (2B) | self-hosted | 0.545 | +0.030 |
| — no rerank (stage-1 `bge-m3`) | — | 0.515 | — |

**The 2B model scores below the 568M one.** That inversion is the single most important
line in the table, and it is the reason the conclusion below is stated as provisional: it
is either a real property of this Dutch corpus or a defect in how we call these models
(English instruction on a Dutch corpus, 1024-token truncation, the yes/no prompt format).
`mxbai-rerank-large-v2` tops English leaderboards. One inversion is a corpus quirk; a
second would be evidence against our own harness, and the run that would have told us
apart (`Qwen3-Reranker-4B`) was killed for time.

## Servable latency — RTX 3090, 100 candidates per query

| reranker | q/s | per query | servable interactively? |
|---|---:|---:|---|
| `bge-reranker-v2-m3` (568M, cross-encoder) | 0.90 | **1.1 s** | yes |
| `mxbai-rerank-large-v2` (2B, cross-encoder) | 0.14 | 7.1 s | no |
| `jina-reranker-v3` (0.6B, **listwise**) | 0.053 | **18.9 s** | no |
| `Qwen3-Reranker-4B` (causal LM) | ≤0.05 | ≥20 s | no |

**Small does not mean fast when the architecture is listwise.** `jina-reranker-v3` is
0.6B — smaller than everything else here except the winner — and is the *slowest* of the
lot, because listwise scoring puts all 100 candidates (~50k tokens) into one context per
query instead of scoring pairs. Model size is not the cost driver; total context is.

So the only open reranker fast enough to serve is also the weakest one, and every open
model that could plausibly compete on quality costs 7-20 s per query on a rented GPU. That
is not a search interface.

## Depth does not rescue it

See `rerank_depth_findings.md`. Reranking only the top 20 instead of the top 100 — the
obvious way to make the slow models affordable — costs **−0.070 nDCG@10** on the
production stack, more than a third of the entire rerank gain. And the effect is
proportional to reranker strength: Voyage loses 0.070, Cohere 0.026, `bge-reranker-v2-m3`
loses *nothing* (it is already flat at depth 20). A weak reranker cannot exploit a deep
pool. Cheap depth and cheap models fail together, not as a trade.

## Cost is not the deciding factor and never was

At this project's query volume (~500 searches/month) every option costs under €1.50/month:

| option | unit price | ~500 q/month |
|---|---|---:|
| self-hosted `bge-reranker-v2-m3` on serverless GPU | ~$0.0003/query | ~€0.05 |
| Cohere `rerank-v3.5` | $0.001/search | €0.50 |
| Voyage `rerank-2.5` | $0.002/search | €1.00 |
| Cohere `rerank-4-fast` / `-pro` | $0.002 / $0.0025 | €1.00 / €1.25 |

The entire spread is under a euro. The decision is about **who sees the text** and **what
quality is lost**, not about money.

## Where this leaves the choice

Provisionally, and pending the two runs below:

- **Voyage `rerank-2.5`** — best measured quality (0.702), worst legal position (no Art. 28
  DPA, no SCC/DPF for `api.voyageai.com`, US processing; opt-out set 2026-07-27 removes the
  training licence and retention but not the paperwork).
- **Cohere via Azure AI Foundry / AWS Bedrock in an EU region** — 0.620 measured *on the
  previous generation*, strongest legal position (EU residency, the cloud provider's DPA
  and SCCs, Cohere never sees it).
- **`bge-reranker-v2-m3` self-hosted** — 0.586, 1.1 s/query, nobody sees the text at all.
  The only option with no third party in it, at Cohere-minus-0.03.

**Sharpened 2026-07-30, after trying to use this list for a real decision.** The three
bullets above are all about *quality vs who sees the text*, and that framing hid the fact
that the middle option is really **two** options with opposite legal properties:

| route | quality | who sees the text | retention / training | cost |
|---|---:|---|---|---|
| Voyage `rerank-2.5`, opt-out ON | 0.702 | Voyage, US | **zero-day, no training licence** — but no published DPA/SCCs, and the opt-out is a **dashboard toggle**, so it needs a periodic re-check, not a one-time tick | ~€1.00/mo |
| Cohere `rerank-v3.5`, **own API / trial key** | 0.620 | Cohere + "Third Parties…for other purposes" (§3(a)) | **indefinite** (§13(d) disclaims any deletion obligation); no trial/prod distinction; no region commitment | free |
| Cohere `rerank-v3.5`, **Azure AI Foundry / Bedrock, EU region** | 0.620 | the cloud provider only | provider's DPA + SCCs; Cohere never sees it. Azure caveat: residency depends on the **deployment SKU**, not the region name alone | paid Azure; a live subscription exists (`src-ocr.cognitiveservices.azure.com`) |
| `bge-reranker-v2-m3` self-hosted | 0.586 mean, **but −0.189 to +0.404 per query and worse than no-rerank on 5 of 12** | nobody | n/a | ~€0.05/mo |

The practical consequence: "use Cohere because it has an EU option" and "use Cohere because
it's free" are **mutually exclusive**, and picking the free one is strictly worse than
Voyage-with-opt-out on exactly the axis that motivated leaving Voyage.

## What is NOT measured, and why it matters

1. **Cohere Rerank 4 (Pro and Fast).** `rerank_api.py:15` pins `rerank-v3.5`. Rerank 4
   shipped in December 2025, so the A/B used a superseded model — and it is the model of
   the option with the best paperwork. If v4 Pro reaches ~0.68-0.70 the whole trade-off
   between quality and legal cover disappears. **Highest-value next run**, needs only a
   Cohere key; 250 calls per variant fits the free trial tier.
2. **ZeroEntropy `zerank-2`** — a 4B fine-tune of Qwen3-4B trained specifically for
   multilingual reranking and instruction-following, claiming to beat `rerank-v3.5`. Same
   backbone as the killed `qwen3-4b` run, so it would also answer the harness question in
   §Quality. Licence needs checking before use (the model card says Apache-2.0, the HF
   listing says non-commercial). Same ~20 s/query class, so it is a ceiling anchor, not a
   deployable candidate.
3. **`Qwen3-Reranker-4B`** — killed after 85 minutes at ~0.05 q/s.
4. **`jina-reranker-v3`** — killed at 10/250 queries once the ETA read 75 minutes. Its
   latency number above is measured; its quality is not.

Backends for all four are wired in `rerank_pod.py`; the API ones need keys.

## Process note worth keeping

`rerank_pod.py` printed nothing until a run completed. That cost real money twice: a chain
died silently after the first model and was noticed ten minutes later, and the
`Qwen3-Reranker-4B` run burned 85 minutes of GPU with no way to bound its remainder from
above before it was killed. A progress line (`tick()`, added here) turned the *next*
question — is `jina-reranker-v3` fast enough? — into a 3-minute answer instead of a
75-minute one. **Any long-running experiment script must emit progress; an ETA is a
control, not a nicety.**
