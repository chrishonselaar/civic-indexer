# Product roadmap — from passed experiment to vision-ready product

**Decision (Chris, 2026-07-23).** The feasibility experiment is over: Stages A and B
passed (engine generalizes; agent onboarding measured at 5/2/**0** interventions —
GO). But **no client contact with the current app**: asking a client's time for
BM25+facets over a partially-OCR'd corpus would test a strawman of the vision, not
the vision. Stage C (the client value test) is **deferred until vision-readiness**,
defined below. Demand conversations may continue in parallel using `user-story.md`
and mockups only — zero client time on the app until it can surface things source
UIs *cannot*.

This supersedes "next = Stage C" in `feasibility-plan.md` §The experiment. The
go/no-go criteria there stay valid; only the timing of the client test moves.

## The gap being closed (state 2026-07-23)

Built and live: ledger/envelope engine, 3 onboarded sources (~39.7k items),
reconciliation verdicts, budget gates, native extraction, OpenSearch projection,
BM25+facets UI. Validated but **not integrated**: hybrid retrieval, reranking,
embeddings, OCR burst engine. Designed but unbuilt: scope predicates, self-healing,
inline OCR head. Never designed (concept-phase "not covered" list): UI stack, agent
runtime, monitoring surface.

## Current state (measured 2026-07-28, off the live ledger and OpenSearch)

**Four and a half of seven exit criteria hold**: #3, #4, #5, #7, and **#2's external
half for `hybrid`** (0.656 vs 0.383, 2026-07-29). Open: **#1, #6, and #2's rerank
clause** — the run substituted a rejected open-weights reranker; the shipped path needs a
reranker decision for `tuchtrecht` (admin call — and the recorded Voyage-vs-Cohere ranking
inverted on 2026-07-30, see `access-posture.md` §9.1) or a `woo_besluiten` window.

| source | discovered | with text | doc vectors |
|---|---:|---:|---:|
| `tuchtrecht` | 47,950 | **47,950** | 47,950 |
| `woo_besluiten` | 26,628 | **4,185** (was 409) | **4,185** (was 201) |
| `haarlem_raad` | 11,622 | 1,017 | 1,000 |
| `vlaardingen_raad` | 89 | 24 | 24 |

Only `tuchtrecht` is finished. `deferred_queue` holds **48 open `ocr_tail`** items (of
214) and 0 open `zip_dossier` (of 27).

**`woo_besluiten` jumped 409 → 4,185 on 2026-07-30 without a single new request**:
`civic import-wob-texts` adopted text the prior `wob-search-mcp` project had already
extracted for documents this ledger held as stubs (`engine/import_wob.py`). What blocks the
remaining 22,443 is **not** crawl rate:

- **~810** are ordinary open.overheid documents rejected by a **regex** —
  `identity.natural_key_pattern` is bare-UUID and the API's `document.id` sometimes carries
  a `_N` suffix. Trivially fetchable; widening the pattern reclassifies ledger rows, so it is
  sequenced by the admin, not slipped into an import.
- **20,712** are four federated hosts, filed as **gate 2** (`access-posture.md` §10). Their
  recorded reason read "needs per-portal scraper", which hid an access question inside an
  engineering one for a year.
- **8,358 more** sit in the prior project's ledger matching *nothing we ever discovered* —
  a discovery blind spot worth understanding before importing around it.

## Workstreams, in dependency order

| # | Workstream | What exists already | What to build | Size |
|---|---|---|---|---|
| W1 | **Corpus truth: OCR tail drain + backfills** | Engine selected (MinerU2.5-Pro), throughput measured (~12.6k pages/GPU-h ≈ $17/M pages), Dockerfile + pinned vLLM stack in `experiments/ocr_ab/production-setup.md`; 166 docs queued; VOL-3 verdict specifies the delete-after-distill contract | A `civic` drain phase: ship queue to burst GPU fleet, ingest OCR text as upgrade (`missing: [ocr_tail]` → resolved), delete blobs per delete-after-distill, stamp `deferred_queue.resolved_at`. Then resume Haarlem (~10.6k), tuchtrecht (47.9k born-digital, no OCR needed), Vlaardingen full | M |
| W2 | **Retrieval stack: embeddings + hybrid + rerank** | All A/Bs done and settled: bge-m3 self-hosted, RRF fusion (pooled recall@100 0.80→**0.98**), Voyage rerank-2.5 (+0.187 nDCG@10), ~$0.002/search, no fixed GPU | Embedding pipeline over text distillates (batched, burst pattern; embed born-digital now, scan-heavy docs after their W1 OCR to avoid re-embeds); kNN field in the projection mapping; hybrid RRF query path + rerank call in `ui/search_api.py`; UI relevance toggle | M |
| W3 | **Recommend mode** | Doc-to-doc kNN identified as "the embedding sweet spot" in `../experiments/embeddings_ab/FINDINGS.md` | "Meer zoals dit" endpoint (kNN on doc vector, filtered by facets) + result-card / detail-page entry point | S (rides W2) |
| W4 | **Query understanding** (was "/ expansion") | **DONE 2026-07-25 — exit criterion #4 met.** Design + build in [`query-understanding.md`](query-understanding.md) (§8 steps 1–3); steps 4–5 deliberately unbuilt (measure-first) | **Scope corrected by measurement.** The *expansion* half is redundant — hybrid RRF + rerank already resolves Dutch compounds at full precision (`tandartsklacht`: BM25 0 → hybrid 100, top-10 precision 10/10), so **no decompounder gets built**. The real gap is ranking ≠ filtering (rerank orders "besluiten van OCW over subsidie juni 2026" correctly but returns `total=101` vs a filtered truth of 3). Build: deterministic vocabulary/alias/date grammar → removable provenance-labelled chips; constrained (closed-world) LLM parse only as a guarded second tier; term expansion only for the vector-less BM25 path, last, if that cohort still exists after W1's re-embed | **S** (down from M — no LLM needed for the exit criterion) |
| W5 | **Monitoring / management UI** | All data already in the ledger: `runs` + reconciliation verdicts, per-source item stage/disposition counts, `deferred_queue`, budgets/cost, blob retention (VOL-3 kept-bytes) | Read-only dashboard first (per-source health, run history + verdicts, queues, spend, coverage/known-exclusions); management actions second (trigger run/drain, retry failed, resolve queue items). Also closes the VOL-3 kept-bytes DETECTION-GAP as a UI concern | M |
| W6.5 | **Battery hardening — the three gaps the live re-onboard found** (added 2026-07-25) | The first SDK-driven live onboarding (`vlaardingen_raad`, 48 turns, $3.40, stopped correctly at verification gate 1) logged 3 DIAGNOSIS interventions, all independently verified. Register entries **POL-1, SURF-3, PROJ-1** in [`failure-modes.md`](failure-modes.md); harness backlog items 7-9; session artifact `../experiments/stage_b/sessions/vlaardingen_raad.json` | Three fixes, **in this order**: (1) **robots/policy** — probe 0 parses `Disallow`/`Crawl-delay`, manifest `policy` block, `validate_manifest` fail-closed. *Mechanism is engine work; the posture is an admin gate decision — and it is fleet-wide: **4/4 sources harvest a host serving `Disallow: /`**, so turning the gate on before the posture exists wedges every source.* **Mechanism half DONE 2026-07-25**: `probe_robots`/`parse_robots` + `policy_flags` in every report, `policy` blocks backfilled into all four manifests at `pending_gate_1`; only the `validate_manifest` gate (and the matching `onboarding-flow.md` Phase-1 wording) is still owed, waiting on the posture decision. (2) **aggregator-registry step** in probe 0, keyed by corpus type — **designed and BUILT 2026-07-25, steps 1-3 of [`aggregator-registry.md`](aggregator-registry.md) §8**: detection (`probe_aggregators` + `registries/aggregators.yaml`), the two surface-gate obligations, and consumption (`engine/strategies/ori_elastic.py`). Owed: step 4, pointing a manifest at the route — an admin call, since it needs `aggregator_provenance` discharged incl. ORI's `not_assessed` data licence. Note **TOT-5**, measured while building step 3 and extended the same day: ORI is weak at **both ends** — 28 of 89 recent ledgered documents absent despite falling inside its covered window, **and 0% coverage of Vlaardingen's sampled 2008/2012 documents** (88.3% per era overall; nothing before 2010, dense only from 2014). So the aggregator is the right route for the *middle* of the archive and the wrong one for the early archive and the recent window: step 4 is a **three-role split manifest**, not a cutover. Re-probing ORI live still made this the *first* item in practice: it carries 310 municipalities, ~100% full-text coverage of what it holds, and proxies the original bytes, so it cuts W1 OCR spend **and shrinks `api.notubiz.nl` — the host where the posture question is hardest — to ~15% of its backfill plus a small recurring poll**. It does **not** remove that host from item (1)'s decision; an earlier version of this cell said it did, and `aggregator-registry.md` §5 records why that was wrong. (3) **projection-liveness probe** — first index-side probe in an otherwise source-side battery; design doc first. The load-bearing lesson, now rule 5 of the register: *a prompt-level obligation with no mechanical check is not a control*. **The PROJ-1 *instance* was fixed 2026-07-25** — `enrichment.lookup_tables` (`from`/`to`/`values`) joined at projection time by `engine/project/projector.py`, `publisher` back in `index.facets` in both Notubiz manifests, measured 0% → 100% non-null on both live indices, `tests/test_project.py` pins it. **The probe itself is still owed**: the defect was that nothing looked, and a regression test for the known case is not that. | S (1) · M (2, 3 — design doc first) |
| W6 | **Onboarding agent UI** | The 8-phase flow is proven (`onboarding-flow.md`; Stage B ran it via Claude Code sessions at 5/2/0 interventions); manifest validation + fixture recording exist in the engine | The flagship. **Slices 1–3 DONE** ([`w6-onboarding-agent.md`](w6-onboarding-agent.md) build order): surface gate + session state, the headless SDK runtime (`civic onboard`), and — **2026-07-28** — the GUI at `/onboard`: intake is URL + one sentence, probe reports / draft-manifest diff / gate cards are first-class objects, the intervention counter is live, and the agent runs in-process (§D) pausing at each verification gate (§E, `approved｜changes_requested｜refused` with a required note — **a refusal is stored, not a cancelled request**). Development and tests run on a `replay` driver at zero cost. **Owed: slice 4** — one *live* run onboarding a source nobody has done, which is what exit criterion #6 actually asks for | L (design doc, then build) |

Suggested execution order: **W1 ∥ W2** (ops vs code, independent), then W3 (days),
then W5 before W6 (the monitoring surface is consumed by the onboarding UI's
verify/monitor acts — user-story Acts 2–3), W4 slots anywhere after W2. W6 last and
largest.

## Workstreams W7–W15 — the vision, beyond the seven criteria

*Added 2026-07-28. W1–W6.5 close the exit criteria and therefore Stage C; they are not
the whole of `idee.md` and `user-story.md`. Everything below is **in the stated vision
and not built**, gated by no criterion — which is exactly how such items go missing.
Each row's "exists" column was read off the code on 2026-07-28, not recalled.*

| # | Workstream | What exists already | What to build | Size |
|---|---|---|---|---|
| W7 | **Self-healing** (user-story Act 3) — *the largest single absence* | Only the **hard half**: a selector/path going to zero halts the run as `NOT_reconciled` (`engine/reconcile.py`) rather than harvesting nothing. Blast-radius tiers designed (`decision-inventory.md`), fleet-aware healing tiers (`notubiz-archetype.md`), and the §E gate model is already the right shape for "escalate instead of auto-apply". **No heal module exists** (`ls engine/` — nothing) | Diff a fresh page against the recorded one, propose an expression patch, run fleet regression, auto-apply at low blast radius, escalate to a gate otherwise. **Two corrections to the received story before anyone scopes this:** (a) fixtures today are **two `page_N.json` pages per source** with no history — a regression fixture, not a healing corpus; healing needs a stored *before* to diff the *after* against. (b) all four sources are **API-based** (`notubiz_events` ×2, `paged_json_api`, `sru_records`) and **no fixture is HTML**, so the first healer is a JSON-contract healer (field rename, path move, endpoint change), *not* the Act-3 narrative's HTML selector. An earlier handoff said the fixtures it needs are already recorded; that is only true for regression | **L** |
| W8 | **Scheduling + the morning digest** (Act 3) | **Nothing.** No scheduler, no cron, no digest, and **no `schedule:` block in any manifest** (grep hits are prose about meeting dates). Onboarding phase 8 prints the word "scheduled" and nothing executes. Runs are manual `civic run`. The digest's *data* already exists in the ledger and is rendered by `/monitor` | A manifest `schedule:` block (the agent already proposes a cadence in prose — make it a field), an executor, and the one-line-per-source digest: *"tuchtcollege: 3 nieuw, reconciled ✓, €0,004."* Act 3's "~1 minute/day" is the acceptance test | **M** |
| W9 | **Card-level correction loop** (Act 2) — *the moment the user story is built around* | The gate model (§E request/verdict with a required note), the manifest diff view, and fixtures that allow re-extraction **without re-crawling**. What is missing is the affordance: you can refuse a gate in prose, you cannot click a wrong field on a result card | Per-field correction on a result card → agent patches the extraction expression → re-run against already-downloaded pages (~seconds) → the card re-renders. Cheap now that gates and diffs exist | **M** |
| W10 | **The cheap-LLM enrichment layer** (`idee.md`) — summaries, filter-option distillation, claim extraction, LLM pipeline monitoring | A real, **unused hook**: `enriched` is a stage rank (`envelope.py:9`) that the projector already accepts (`projector.py:88`) and **nothing populates**. Budget gates exist; `extraction-fallback.md` governs guard-conditioned LLM use. **None of the four enrichments attempted** | All four, behind declared guards and budget gates, at the Gemini-Flash-Lite price point `idee.md` names. Note W4 measured the *query-expansion* half of LLM use redundant against hybrid+rerank — **that verdict says nothing about this half** | **L** |
| W11 | ~~**Query surface gaps**~~ **DONE 2026-07-28** | W4 chips; hybrid+rerank; **and now a real query grammar** | (a) ~~**Search operators**~~ **BUILT 2026-07-28** — [`search-operators.md`](search-operators.md): phrase quotes, `AND/EN`, `OR/OF`, `NOT/NIET/-`, parentheses, trailing wildcard, parsed in-engine (`engine/query_ops.py`, 65 tests). Fail-open, `operators=0` reproduces the literal pre-W11 query, and a query without operator syntax is untouched down to the response keys. Two rulings carry the weight: operators and W4 chips **do not compose** (chip extraction strips stranded connectives — `en`/`of`, case-folded — so it would silently rewrite the boolean), and the dense leg cannot express "not", so a conjunctive exclusion becomes a kNN `must_not` filter (0/8 leaks vs a 1/8 control) while a `NOT` under an `OR` drops to BM25 **with a stated reason** instead of being approximated. Its own two open threads closed the same day: **§9** composes W4 chips with operators (a chip may be derived only from a **root-conjunctive bare term**, applied by pruning the parse **tree** rather than editing the query text) and **§10** fixes phrase highlighting (the `unified` highlighter ignores adjacency — 16/50 fragments contained `"college van beroep"`; `plain` gets 48/50). (b) ~~**PROJ-3**~~ **DECIDED AND BUILT 2026-07-28** — hybrid facet counts are **pool-scoped and labelled** (`facets_scope: "pool"`, stated in the panel); bm25 keeps its exhaustive counts and its exact pre-W4 response shape. Rationale in `failure-modes.md` PROJ-3: hybrid pagination was already pool-bounded, so global counts were promising a narrowing the result list could not deliver | **done** |
| W12 | **Result-set utility** (Act 4) | Typed edges in the envelope (`item-envelope.md`), so an appeal chain is *expressible* | Dossier / appeal-chain grouping in one view, and CSV export. Act 4 names both as part of "four minutes from question to three verifiable primary sources" | **M** |
| W13 | **Automatically determined facets** (`idee.md`: *"automatisch bepaalde facet filters"*) | Facets are **manifest-declared** by the agent at onboarding, not derived from the data. PROJ-1 is the standing warning: a declared facet can be permanently null and nobody notices | Derive facet candidates from the projected data (cardinality, coverage, discriminative power), propose them at onboarding, and verify with the projection-liveness probe (W6.5 item 3) | **M** |
| W14 | **Scope predicates** | Designed in full (`thematic-scoping.md`); **absent from the engine** — no `scope` in `engine/manifest.py` | The predicate primitive (classifier scopes *and* deterministic metadata filters), the deferred `out_of_scope` cohort, rolling windows that never revoke. **Stage C needs this**: the animal-rights slice is defined as a scope | **S–M** |
| W15 | **Semantic vector analysis** (`idee.md` *nevendoel*) — information gaps, influence networks, error/misleading analysis, corruption signals | The corpus and vectors are the precondition, and they are being built. Nothing else | Explicitly a **later** goal in `idee.md` and research-shaped rather than build-shaped. Listed so it stops being invisible, not to schedule it | — |

### Controls owed (from the 2026-07-28 findings)

- ~~**PROJ-2's check has no trigger.**~~ **DONE 2026-07-28.** `/api/monitor/projection`
  calls `verify.check_indices()` and `/monitor` renders it as an *Index-schema* panel,
  with drift also raised in the alert strip — so "geen problemen" can no longer be
  printed while an index disagrees with its schema. Deliberately a **separate endpoint**
  fetched independently by the dashboard: it is the only OpenSearch call on the page, and
  that separation is what keeps `/overview` Postgres-only (the monitor still works with
  the search stack down; a dead cluster costs one card, not the page). It never raises —
  an unreachable cluster renders as "niet te controleren", because silence is the failure
  mode being guarded against. Self-healing is still not attempted: this reports, the fix
  is `--recreate`. Down-payment on W6.5 item 3, not a substitute for it.
- ~~**PROJ-3 needs a product decision.**~~ **RULED 2026-07-28: pool-scoped, labelled** —
  see W11(b).
- ~~**Criterion #2's external comparison has never been taken.**~~ **TAKEN 2026-07-29**,
  both clauses — see criterion 2 below and
  [`experiments/external_comparison/FINDINGS.md`](../experiments/external_comparison/FINDINGS.md).
  What it cost to make the measurement *mean* anything was a coverage control: our
  tuchtrecht corpus held SRU abstracts (median 589 chars), so the naive run would have
  measured our missing text, not our ranking.
- **Widening `policy.processors`** so the cross-corpus default can rerank is an admin
  decision per source, not a UI setting (`cross-corpus-search.md`, `access-posture.md`).

## Vision-readiness = exit criteria (gates Stage C)

1. A query on scanned-only content returns it (OCR tail drained; W1).
2. Hybrid+rerank demonstrably beats BM25-only on the live corpus (side-by-side
   toggle; W2) and beats the equivalent source-site/opub search on the same query.
   **EXTERNAL HALF MET FOR `hybrid`, 2026-07-29** — **0.656** against
   tuchtrecht.overheid.nl's own search (**0.383**): 12 queries over uitspraken dated
   2025, blind pooled judging, 2,504 documents on both sides
   ([`FINDINGS.md`](../experiments/external_comparison/FINDINGS.md)). Three things the
   number alone would hide:
   - **The rerank clause is STILL OPEN.** tuchtrecht forbids Voyage
     (`policy.processors: []`, named professionals), so the run substituted self-hosted
     `bge-reranker-v2-m3` — a model this project **benchmarked and rejected** for
     production (+0.071 over stage-1 vs Voyage's +0.187,
     `rerank_open_vs_api_findings.md`). It scores 0.698, but per query it ranges
     **-0.189 to +0.404 and is worse on 5 of 12**, so it is not a floor under the
     shipped stack — it is a different, noisier system. Closing the clause needs either
     (a) **Cohere-EU** (+0.105, EU region + DPA) approved for `tuchtrecht` — an admin
     policy call (POL-1), never ours — or (b) a completed `woo_besluiten` window, which
     *does* permit Voyage, measured against `open.overheid.nl?zoektekst=`.
   - **BM25-only looks like a win and is not one** (0.485 vs 0.383): the lead is carried
     by lexical/concept queries, and on natural language it ties a system with no ranking
     at all (0.223 vs 0.226). The margin is the dense channel plus the reranker.
   - **They do not lose on matching — they lose on ordering.** Their `text=` selects
     nearly the same documents ours does (66/66, 77/77 on single terms) and *more* on
     multi-word queries; their sort control offers only Datum and Titel and the default
     is publication-date descending. Meanwhile our Dutch snowball stemmer sends
     `tandarts` -> `tandart` but `tandartsen` -> `tandarts`, so a plural query finds 20
     of 66 documents where theirs finds all 66. Fixing that split is the cheapest real
     quality win the experiment turned up.
3. **MET 2026-07-28.** Recommend mode surfaces a related doc across *sources* (W3).
   The blocker was architectural, not algorithmic: every source was its own index and
   `related` ran kNN inside one, so "across sources" was structurally impossible.
   Settled in [`cross-corpus-search.md`](cross-corpus-search.md) as a **query-time union**
   of `civic-*` rather than a shared index — legal because the mapping is not
   parametrized by dataset, and it keeps the per-source rebuildable projection intact.
   Measured on the live corpus: 17 of 100 sampled documents have a cross-source
   neighbour in their top 8 (a Vlaardingen committee transcript pairs with a Haarlem
   one; a Woo bundle of environmental annexes with Haarlem's "Bijlage 2 Onderzoeken").
   Two consequences recorded rather than papered over: the union exposed **PROJ-2**
   (three of four indices had silently drifted off the declared mapping — fixed, with
   `civic check-projection` as the standing control), and a cross-corpus pool
   **does not rerank**, because only `woo_besluiten` opts into `policy.processors`.
4. **MET 2026-07-25.** Free-text intent becomes chips + a filtered, *countable* result set
   without the user knowing facet names (W4). Reworded 2026-07-24: "expanded query"
   dropped — measured redundant against hybrid+rerank; the criterion is addressability,
   not recall ([`query-understanding.md`](query-understanding.md) §2). Delivered by Tier 0
   alone — a lookup table plus a date grammar, **no LLM in the query path**: the live
   example goes `total` 101 → 19 with the top hit unchanged, chips are removable and
   provenance-labelled, an empty-set parse is offered rather than applied, and the whole
   state is in the URL.
5. Pipeline health readable at a glance without psql (W5).
6. A new municipality onboarded end-to-end through the UI from URL + intent
   statement, gates included (W6). **Surface built 2026-07-28** (`/onboard`) and exercised
   end-to-end on the replay driver; the criterion itself still needs the *live* run.

7. **✅ MET 2026-07-27.** All three hosts ruled, all four manifests carry both axes
   (`policy.admin_decision` + `policy.exposure`), and `validate_manifest` is fail-closed on
   them — so this criterion cannot silently regress. The one-off ruling generalised into
   [`access-posture.md`](access-posture.md): consent ambiguity is the normal state, the
   posture is acquisition + exposure, and exposure is enforced at projection time (T0) and
   in the API (T1/T2/T3), with third-party processors opt-in per source. *Original text
   below, kept for the reasoning.*

   **Added 2026-07-25 — a recorded access-policy posture (W6.5 item 1).** All four
   current sources harvest a host serving `robots.txt: Disallow: /`
   (`open.overheid.nl`, `repository.overheid.nl`, `api.notubiz.nl` ×2) and no manifest
   recorded it until now. This is a Stage-C gate, not a nice-to-have: the client
   conversation cannot responsibly open while the project's position on harvesting
   those hosts is undocumented. Resolution is an **admin decision** per source (or one
   project-wide stance), landed in each manifest's `policy.admin_decision`. **Since
   2026-07-25 all four manifests carry a `policy` block** (robots per host, verified
   live, mitigating/aggravating context separated) and every probe-0 report emits
   `policy_flags` — so the posture is now *visible and pending* rather than absent. The
   criterion is met when `admin_decision` reads something other than `pending_gate_1`
   everywhere and the `validate_manifest` fail-closed rule is switched on. Note the
   decision is not four independent ones: `api.notubiz.nl` backs two sources and
   `repository.overheid.nl` two, so three hosts carry all four. See POL-1 in
   [`failure-modes.md`](failure-modes.md), and — **written 2026-07-25 to make the ruling
   cheap to take** — the decision memo `pol1-posture-decision.md` (internal)
   plus three drafted, unsent asks in [`../outreach/`](../outreach/). Note what the wording
   above already implies and the memo states outright: **a recorded NO-GO satisfies this
   criterion too.** Stage C is gated on silence, not on a permissive answer.

When all seven hold on the dev box, re-open Stage C: the animal-rights slice
(`thematic-scoping.md` — scope predicates land as part of that slice, i.e. **W14**) with
the prospective client, per the original go/no-go criterion ("finds something the
official search couldn't surface").

**The seven criteria are not the vision.** They gate the *client test* — they are the
bar for "worth a client's time", deliberately set lower than `idee.md`. W7–W15 above are
the difference, and the autonomy layer (W7 self-healing, W8 scheduling + digest, W9 the
correction loop) is where the user story actually lives: Act 3 is a system that runs
itself and reports one line a day, and none of that exists. Vision-readiness ≠
vision-complete, and this document should not be read as claiming otherwise.
