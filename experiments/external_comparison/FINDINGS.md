# Exit criterion #2, external half — measured 2026-07-29

> **Criterion #2:** "Hybrid+rerank demonstrably beats BM25-only on the live corpus
> (side-by-side toggle; W2) **and** beats the equivalent source-site/opub search on
> the same query."

The internal half was measured in W2. The external half had never been taken — it
was the one criterion carried as an assertion. This is that measurement, on
`tuchtrecht` against `tuchtrecht.overheid.nl`'s own search.

**Verdict: the external half holds for `hybrid`. The rerank clause is satisfied only
in a weak, technical sense — see the warning immediately below.**
nDCG@10 **0.698** (hybrid + open-weights rerank) and **0.656** (hybrid) against
**0.383** (theirs) — +0.315 and +0.273, i.e. +82% and +71% relative. BM25-only
scores **0.485**: ahead, but not by a margin worth defending. The margin comes from
the dense channel and the reranker, not from our lexical matching, which is *worse*
than theirs (§3).

> ⚠️ **The reranker used here is one this project already REJECTED.** `rerank_open_vs_api_findings.md`
> (2026-07-27) benchmarked rerankers and concluded that only **Voyage `rerank-2.5`**
> (+0.187 nDCG@10 over stage-1) and *potentially* **Cohere `rerank-v3.5`** (+0.105,
> **EU region available**) are good enough; `bge-reranker-v2-m3` (+0.071) failed on both
> quality and servable latency. Using it here measures something the product does not
> ship. The inference "Voyage ≥ bge everywhere, so Voyage would also beat them" is sound,
> but it is an argument, not a measurement, and calling the clause "met" on that basis
> invites the reader to think the production rerank path was tested. **It was not.**
>
> The option that should have been raised first is **Cohere-EU**: the reason
> `tuchtrecht` sets `policy.processors: []` is *Voyage's terms specifically* (perpetual
> training licence, no DPA, no EU transfer mechanism). A processor with an EU region and
> a real DPA is a materially different proposition, and whether it clears the bar is an
> **admin decision** — which should have been surfaced instead of quietly substituting a
> model the project had already ruled out.
>
> What this run does legitimately add: it **independently corroborates** the earlier
> rejection. bge-reranker-v2-m3 gained +0.042 nDCG@10 on average here (against +0.071
> measured before) while making **5 of 12 queries worse**, range -0.189 to +0.404. That
> is a re-measurement of a settled negative, not a new capability.

> **Three runs, and only within-run numbers are comparable.** Run 1 (pre-stemmer-fix)
> scored hybrid 0.707 / bm25 0.478 / theirs 0.419. Run 2 (post-fix) 0.723 / 0.442 /
> 0.426. Run 3 (this one) adds the reranked system to the **pool**, which brought in
> documents from deeper in the hybrid pool that no other system had surfaced — so the
> ideal-DCG denominator grew and *every* system's nDCG fell, theirs included. That is
> ordinary TREC pooling behaviour, not a regression: a bigger pool is a harder and
> fairer yardstick. Compare systems inside one row of this table, never across runs.

## 1. What was measured, and what was controlled

| | |
|---|---|
| Corpus | tuchtrecht, uitspraken **dated 2025** — 2,504 documents on both sides |
| Queries | 12, plain text, no operator syntax (`queries.jsonl`): 3 lexical, 4 concept, 5 natural-language |
| Ours | `/api/search?dataset=tuchtrecht&mode=bm25\|hybrid`, `month=2025-01..12` |
| Theirs | `/zoeken/resultaat?text=…&datumtype=uitspraak&datumvan=01-01-2025&datumtot=31-12-2025&ftsscope=uitspraak` |
| Judging | pooled top-10 across all three systems, blind, Haiku-graded 0/1/2 |
| Metrics | nDCG@10 (gains 2^g−1), P@10 (grade 2 only) |

Three controls, each of which the measurement would be worthless without:

- **Coverage, not quality, is what a naive run would have measured.** We held only
  SRU *abstracts* for tuchtrecht — median 589 characters — because the full FRBR
  body text was a deferred upgrade nobody had taken. Against that corpus our search
  would have lost on documents it simply did not contain. So 2025 was backfilled
  first (`--mode upgrade`, ~1 rps, 59 min): median document is now **26,250
  characters**, a 45× jump, and the window holds **2,504 documents on both sides —
  the same number they report**. Coverage is not a variable in this table.
- **Their strongest setting.** `ftsscope=uitspraak` (full decision text), their
  default, not the summary-only `indicatie` alternative.
- **One text source for judging.** Every pooled document is graded from *our*
  extracted text, including the ones only their search found. Grading must not
  depend on which system surfaced a document.

**The reranker here is open-weights, and that is a substitution, not a design.**
`tuchtrecht` declares `policy.processors: []` with a stated reason: these decisions
concern named professionals, and Voyage's default terms (perpetual training licence,
no retention period, no GDPR basis) are not a defensible place to send them. So the
production reranker cannot be used on this corpus. Rather than stop there, the run
used self-hosted `BAAI/bge-reranker-v2-m3` — a model this project **benchmarked and
rejected** for production. That measures *a* reranker, not *the* reranker, and it
should have been raised as the policy question it is (Cohere-EU) before being
silently substituted. See §5.

## 2. Result

| System | nDCG@10 | P@10 | hits matched (median) |
|---|---:|---:|---:|
| ours, bm25 | 0.485 | 0.367 | 14 |
| ours, hybrid | 0.656 | 0.450 | pool-capped |
| ours, hybrid + rerank (**rejected** open-weights model, §5) | 0.698 | 0.517 | pool-capped |
| theirs | 0.383 | 0.317 | 34 |

**The load-bearing row is `hybrid`: 0.656 vs 0.383.** That is the shipped stack minus
the reranker, and it is what the external half of criterion #2 rests on.

The rerank row adds **+0.042 nDCG@10 and +0.067 P@10 over hybrid** on average —
consistent with the +0.071-over-stage-1 this model scored in the embedder A/B — but
per query it ranges **-0.189 to +0.404 and is *worse* on 5 of 12** (§5). A component
that hurts 40% of queries is not a floor under the shipped stack; it is a different,
noisier system that happens to average higher on twelve queries.

**Per-query, because a mean over 12 queries hides too much.** hybrid+rerank against
theirs, counting a gap within +/-0.05 as a tie:

| | count | |
|---|---:|---|
| wins | **10** | biggest: `onvoldoende dossiervoering` +0.505 (6 relevant in top 10 vs 2), `medicatiefout` +0.494 (3 vs 0), `doorhaling` +0.473 (10 vs 6) |
| losses | **1** | `seksueel grensoverschrijdend gedrag`, -0.098 (7 vs 8) |
| ties | 1 | `declaratiegeschil advocaat`, +0.029 (5 vs 5) |

Two figures that say more than nDCG about what a user gets: **5.2 clearly-relevant
decisions in the top 10 against their 3.2**, and on **3 of 12 queries their first page
contained nothing relevant at all** (`medicatiefout`, `dierenarts ... laten inslapen`,
`mag een huisarts weigeren een verwijzing te geven`) against **0 of 12** for us.

The win is broad rather than carried by an outlier — but see the deflators in §3 and §4:
they have no ranker to lose with, and twelve queries is twelve queries.

By query kind — this is where the result actually lives:

| kind | bm25 | hybrid | hybrid+rerank | theirs |
|---|---:|---:|---:|---:|
| lexical (`tandarts`) | 0.636 | 0.754 | 0.831 | 0.467 |
| concept (`onvoldoende dossiervoering`) | 0.701 | 0.730 | 0.727 | 0.516 |
| **natural** (`klacht over te late diagnose van kanker`) | **0.223** | **0.538** | **0.595** | **0.226** |

On natural-language queries our BM25 and their search are indistinguishable (0.223
vs 0.226) and both are close to useless. Hybrid more than doubles that, and the
reranker adds again on top. Note the reranker helps *least* on concept queries
(-0.003, a wash) and most on lexical ones — a cross-encoder reorders a pool, so it
cannot help where the pool was already right. Per-query table in `results.md`.

## 3. The mechanism — they do not lose on matching

The tempting story is that we match Dutch better. **That story is false**, and the
morphology probe (`morphology.md`, same 2025 window on both sides) says so:

| concept | query form | theirs | ours (bm25) |
|---|---|---:|---:|
| tandarts | `tandarts` | 66 | 66 |
| tandarts | `tandartsen` | **66** | **20 -> 66 (fixed)** |
| dossiervoering | `dossiervoering` | 77 | 77 |
| dossiervoering | `bijhouden van het dossier` | **50** | **9** |
| doorhaling | `doorhaling` | 78 | 86 |

On single terms the two are near-identical (66/66, 77/77, 1/1, 0/0, 55/55) — their
`text=` and our BM25 select nearly the same documents. Where they differ, **they
match more**, and their median match count is 34 against our 14.

The `tandartsen` row is a defect on our side with an exact cause:

```
tandarts    -> ['tandart']      # dutch_analyzer, snowball
tandartsen  -> ['tandarts']
```

The Dutch snowball stemmer strips the `-s` off the singular (reading it as a plural
marker) and the `-en` off the plural — singular and plural land on **different
stems and never meet**. Searching `tandartsen` found 20 of the 66 documents that
`tandarts` finds. Their search unifies both forms.

**Fixed the same day.** `dutch_analyzer` now runs `keyword_repeat` +
`remove_duplicates` around the stemmers, so both surface form and stem are indexed
and the two token sets overlap on `tandarts`. Re-measured: `tandartsen` returns
**66**, matching theirs exactly, with every other row of the probe unchanged — a
targeted fix, not a general loosening. See `engine/project/mappings.py`. The §2
numbers are from after this shipped, and the fix did **not** rescue our BM25: the
gap was never mostly morphological.

So the entire margin in §2 comes from ordering, not selection. Which is the finding:

**Their `text=` is a filter, not a ranker.** Their sort control offers exactly two
options, Datum and Titel, and the default (no `sort` parameter) is
**publication-date descending** — verified monotone across all 20 hits of a probe
query, and distinct from both offered sorts. A user who searches `onvoldoende
dossiervoering` gets 67 matching decisions in reverse chronological order and has to
read them. This is not one ranker losing to another; it is ranked retrieval measured
against a chronological listing, and the report would be dishonest if it implied
otherwise.

## 4. What this does and does not license

Holds:
- Our hybrid search beats the source site's own search on the same query, in the
  same window, on a blind pooled judgment. Criterion #2's external half is **met for
  `hybrid`**.
- The margin is largest exactly where a civic search is most useful — someone who
  describes a problem in their own words rather than guessing the term of art.

Does not hold, and should not be claimed:
- **The rerank clause is NOT measured on the shipped stack** — and the substitute used
  is a model this project rejected for production. Treat 0.698 as an argument that the
  clause is reachable, not as evidence it was tested. The measurement that would settle
  it needs either Cohere-EU (an admin policy call) or a completed `woo_besluiten` window
  with Voyage. See the warning at the top and §5.
- **One corpus, one year, twelve queries, one judge model.** `tuchtrecht` 2025 is
  the only window where coverage is controlled. The other three sources are
  unmeasured against their origins.
- **Our lexical channel is not the win it looks like** (§3). BM25-only scores 0.485
  against their 0.383, but on natural-language queries it ties a system that has no
  ranking at all (0.223 vs 0.226) — even after the stemmer fix.

## 5. The rerank half — what was actually measured, and why it is not the claim

Criterion #2 asks for hybrid+**rerank**. What ran was `BAAI/bge-reranker-v2-m3`,
self-hosted, over the top-100 hybrid pool — the same depth production uses
(`HYBRID_POOL`), and a depth this model can be trusted at because
`rerank_depth_findings.md` measured it *flat* between depth 20 and 100. It scores
**0.698 vs their 0.383**. That is a real number about a model the product does not
ship and has already rejected.

**Why not Voyage.** `tuchtrecht` declares `policy.processors: []` for a stated reason
(named professionals; Voyage's terms take a perpetual training licence with no
retention period and no GDPR basis). Reranking is the one place document text would
leave the system, so the gate bites here. Changing it is the admin's call and the
agent does not answer it (POL-1). Nothing was sent anywhere: the pod is our own,
open weights, destroyed after the run.

**Why the "it's a floor" argument does not survive the per-query split.** The
tempting reading is: bge-reranker-v2-m3 is the *weakest* reranker the project measured
(+0.071 nDCG@10 over stage-1 against Voyage's +0.187,
`rerank_open_vs_api_findings.md`), so if even the weakest one clears the source site,
any shipped reranker would too. Two things break that:

1. **The mean hides a sign flip.** Per query the rerank delta over `hybrid` runs
   **-0.189 to +0.404, negative on 5 of 12**. q05 is the clearest case: hybrid
   returned a *perfect* page (grades `[2,2,2,2,2,2,2,2,2,2]`, nDCG 1.000) and the
   reranker demoted two relevant decisions to positions where they scored 0 and 1,
   landing at 0.811 — below their 0.909, and the single query where we lose. A
   monotone-improvement assumption is exactly what a weak model violates.
2. **"Weakest of the set" bounds nothing about a model outside the set.** Voyage and
   Cohere score higher *on the A/B corpus*; that is a different corpus, different
   language mix, and different document lengths from 26k-char tuchtrecht decisions.

So the honest statement is the narrow one: **hybrid beats them, 0.656 vs 0.383. A
rejected reranker also beats them, 0.698 vs 0.383. The shipped rerank path is
unmeasured on any corpus where an external comparison exists.**

**Correction to an earlier version of this section.** It asserted the clause was
unmeasurable anywhere, on two observations that were real and one inference that was
wrong. `open.overheid.nl/zoekresultaten` does return 401 — but the site is a React
SPA, so that path is a client-side route and its status says nothing about search. And
the v0 API does search: the parameter is **`zoektekst`**, not `zoekterm`.
`zoektekst=subsidie` returns 96,786 of 699,981, **relevance-sorted by default**, with
`publicatiedatumVan`/`Tot`, `documentsoort` and `informatiecategorie` filters. Found by
reading the SPA's own bundle instead of guessing more parameter names. This also
corrects a recorded project finding (`failure-modes.md` API-3): the endpoint ignores
*unknown* params rather than rejecting them, which is what makes a wrong guess look
like a missing feature.

So a **second, independent** comparison is now available and worth taking:
`woo_besluiten` permits Voyage *and* has a reachable relevance-ranked surface with the
filters needed to control coverage. That would measure the production stack against a
second source. It waits on coverage (408 of 26,628 — finish a window, W1), which is
backfill work, not a policy decision.

## 6. Actionable, in priority order

1. ~~**Fix the stemmer split.**~~ **DONE 2026-07-29**, see §3. `tandartsen` 20 -> 66.
   It did not move the headline: our BM25 is still a coin-flip against theirs, which
   is itself the useful finding — the lexical gap was never mostly morphological.
2. **Complete a `woo_besluiten` window and re-run this against open.overheid.nl**
   (§5). That is the one measurement that would put the **production** reranker and a
   **second** source into the claim at the same time. Ordinary backfill work (W1).
3. **Do not ship BM25-only.** 0.485 vs 0.383 looks like a win and is not one: it is
   carried entirely by lexical and concept queries, and on natural language BM25 ties
   with a system that has no ranking at all (0.223 vs 0.226).
4. **Put Cohere-EU to the admin, do not ship bge-reranker-v2-m3.** This run reproduced
   why the model was rejected: +0.042 mean, but **worse on 5 of 12 queries** and a range
   of -0.189 to +0.404. A component that hurts 40% of queries is not a fallback, it is a
   coin toss. The real question for processor-refusing corpora is whether a processor
   with an EU region and a DPA (Cohere, +0.105) clears a bar Voyage's terms do not —
   and that is the admin's to answer (POL-1), not something to route around.

## Reproducing

```bash
uv run python experiments/external_comparison/fetch_sides.py       # both sides, ~1 rps
uv run python experiments/external_comparison/morphology_probe.py

# rerank: export the pool, score it on a burst pod, import the scores
uv run python experiments/external_comparison/rerank_pool.py export
scp -P PORT rerank_input.jsonl pod_rerank.py root@POD:/root/
ssh -p PORT root@POD 'pip install -q "transformers==4.49.0" && python3 pod_rerank.py'
scp -P PORT root@POD:/root/rerank_scores.jsonl experiments/external_comparison/
python3 experiments/ocr_drain/pod.py rm POD_ID          # ALWAYS, then verify with `pods`
uv run python experiments/external_comparison/rerank_pool.py import

uv run --with anthropic python experiments/external_comparison/judge.py   # judges LAST
```

The pod is not optional at this size. Reranking 12x100 pairs took **15 s** on a 3090
against a measured **~49 min** on this laptop's 8 CPU cores (~234 s/query) — ~200x, for
~$0.04. An earlier version of `rerank_pool.py` ran on CPU arguing that "renting a
machine to do 13 seconds of compute is worse than waiting"; that guessed at the waiting
and was wrong by two orders of magnitude. `judge.py` runs last because the reranked
top-10 joins the judged **pool**, which changes the qrels for every system.

`runs.json` and `qrels.json` hold ECLIs, ranks and grades — **never hit text**.
tuchtrecht is exposure T2 and concerns named professionals; judged text stays in the
session. Their host was queried ~40 times total at ~1 rps, well inside the ~140
requests at 1 rps the manifest records as probed clean.
