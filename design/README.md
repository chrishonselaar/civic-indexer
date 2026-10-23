# Design overview — universal civic-data crawler & indexer

Concept-phase design for the system sketched in [`../idee.md`](../idee.md): a universal,
pluggable crawl → extract → index platform for Dutch civic/government datasets, where an
AI agent onboards new sources interactively and a deterministic engine runs them cheaply.
Everything here is derived empirically from four existing indexers documented in
[`../documented_existing_indexers/`](../documented_existing_indexers/) (Woo-besluiten,
SRC/Reclame Code, Vergunningen Groningen, Gemeentes/Notubiz).

**Status: Stages A and B PASSED (2026-07-23)** — walking skeleton implemented
(`engine/`, `manifests/`, `ui/`; build/test commands in the top-level `README.md`) and the decisive
agent-onboarding test closed with a GO verdict: Haarlem 5, tuchtrecht 2, Vlaardingen
**0** human interventions (`../experiments/stage_b/intervention-log.md`). *Context added
2026-07-25:* Vlaardingen's **0** came from a deliberately *light* pass that inherited five
probes the Notubiz archetype marks non-inheritable; the first SDK-driven live re-onboard
ran them and surfaced three real defects (POL-1 / SURF-3 / PROJ-1 in
[`failure-modes.md`](failure-modes.md), now W6.5). The two numbers measure different
things — the 0 is not withdrawn, but it is not a clean-bill-of-health either. **Stage C
(client value test) is deferred until vision-readiness** — no client contact with the
current app. The active plan is [`product-roadmap.md`](product-roadmap.md).

## The core bets

1. **Engine / manifest split.** Roughly half of all hard-won behavior across the four
   projects is universal (caps, ledgers, budgets, reconciliation, streaming discipline)
   → built once as non-optional engine invariants. The rest is per-source and
   **declarative**: config + strategy selections + restricted expressions + generated
   tables + prompts, with a sandboxed snippet escape hatch that empirically almost
   never fires.
2. **Adapters as data, authored by an agent.** The manifest DSL is narrow enough for an
   LLM agent to author, probe, and later self-heal — with autonomy tiered by blast
   radius, and regression testing against cached fixtures instead of live sites.
3. **The real universal contract is the item envelope**, not the DSL: a normalized
   record (identity, multi-provenance, typed edges, two-axis status, accounting)
   flowing between fixed pipeline stages. Novel source mechanics become new *strategies
   inside hooks*; the stage model itself has survived every desk-check.
4. **Completeness is a reconciliation problem.** Every source examined (5/5) hides a
   silent result cap. "Success" is an engine-computed *reconciled* verdict, never "ran
   without exceptions."
5. **Postgres is ground truth; OpenSearch is a rebuildable projection.** Blobs are
   transient by default (inverted for TTL sources); text distillates and the ledger are
   what persist.
6. **Platform archetypes are the leverage.** The Dutch civic web is a modest set of
   SaaS platforms; one archetype + ~15 lines of parameters covers each new municipality.

## Reading order

| # | Document | What it is |
|---|---|---|
| 1 | [`user-story.md`](user-story.md) | **Start here.** The end-to-end experience in four short acts (add → verify → monitor → use), plus the illustration prompt. |
| 2 | [`decision-inventory.md`](decision-inventory.md) | The empirical foundation: ~100 per-source decisions mined from the four projects, bucketed (engine / config / strategy / expression / table / prompt / snippet). Ends with the derived DSL primitive list and blast-radius tiers. |
| 3 | [`item-envelope.md`](item-envelope.md) | The inter-stage record schema: identity, provenance, typed edges, status vocabulary, run/reconciliation reports, Postgres mapping. Open questions all marked resolved with pointers. |
| 4 | [`src-manifest-example.md`](src-manifest-example.md) | Expressiveness test #1: the full SRC pipeline in the DSL — zero snippets. Friction log of schema refinements it forced. |
| 5 | [`notubiz-archetype.md`](notubiz-archetype.md) | Expressiveness test #2: platform archetype + per-site instantiation (Amsterdam). Adds two-scope probes, override policy, fleet-aware healing tiers. |
| 6 | [`event-feed-desk-check.md`](event-feed-desk-check.md) | Desk-check of the hardest future source shape (KOOP BUS changelog feed), grounded in the official handleiding. Verdict: no new hook; adds discovery roles, tombstones, stream-mode accounting, SFTP transport. |
| 7 | [`envelope-desk-checks.md`](envelope-desk-checks.md) | Resolutions of the remaining envelope questions: versioning policy, dangling-edge linker, geo/entity handling, chunk lineage. |
| 8 | [`onboarding-flow.md`](onboarding-flow.md) | The agent's 8-phase flow from URL to production manifest, centered on the 13-probe stress-test battery (war stories → standing procedure). Intake is minimal and iterative — intent and bounds are agent-drafted proposals, not upfront forms. |
| 9 | [`thematic-scoping.md`](thematic-scoping.md) | Indexing a client-relevant subset first. Scopes are named predicates — theme classifiers *and* deterministic metadata filters (date ranges, gemeente, doc type); scope limits spending, never knowledge; deferred `out_of_scope` cohort enables later expansion without re-crawling; rolling windows never revoke. |
| 10 | [`extraction-fallback.md`](extraction-fallback.md) | Guard-conditioned LLM rescue for incidental extraction failures: batched against stored raw, budget-gated, with a failure-rate threshold that routes *systematic* failures to self-healing instead of masking them. |
| 11 | [`ocr-strategy.md`](ocr-strategy.md) | Two-tier OCR as the default: inline per-page-API head, deferred GPU-burst tail on open-weights OCR VLMs; never blocks indexing. Embeddings ride the same burst pattern. |
| 12 | [`feasibility-plan.md`](feasibility-plan.md) | Honest risk ranking (agent-authoring is the unproven core bet), a 3-stage 4–6-week experiment with go/no-go criteria, and graceful fallback positions. |
| 13 | [`ui-inspiration.md`](ui-inspiration.md) | Reference material (not settled design) for the future search/filter/recommend UI: interaction-pattern catalog pointer + opub.nl as a clean production example with screenshots, and where we can beat it. |
| 14 | [`failure-modes.md`](failure-modes.md) | **The growing failure-modes register**: every observed/hypothesized trap with its detection mechanism (battery probe / engine invariant / harness) and mitigation. New field findings land here first; `DETECTION-GAP` entries are the harness backlog. Live since Stage B. **2026-07-25:** rule 5 added (*a prompt-level obligation is not a control*) plus two new categories from the first live SDK re-onboard — **POL** (access-policy posture; robots `Disallow` unrecorded across 4/4 sources) and **PROJ** (index-side projection/facet liveness, the register's first non-source-side axis) — and **SURF-3** (third-party aggregators are invisible to a host-scoped probe 0). |
| 15 | [`product-roadmap.md`](product-roadmap.md) | **The current plan** (post-Stage-B): six workstreams (OCR drain+backfills, hybrid+rerank, recommend, query understanding, monitoring UI, onboarding-agent UI) plus **W6.5 battery hardening** (added 2026-07-25 from the first live SDK re-onboard), with **seven** vision-readiness exit criteria that gate Stage C — #7 is a recorded access-policy posture, since all four sources harvest hosts serving `Disallow: /`. Supersedes "next = Stage C". **Extended 2026-07-28 with a measured current-state block (4/7 criteria met; the corpus table) and with W7–W15 — the vision items that no exit criterion gates**: self-healing, scheduling + the morning digest, the card-level correction loop, the cheap-LLM enrichment layer, search operators, dossier/CSV, automatically determined facets, scope predicates, the semantic-analysis *nevendoel*. Written down because the seven criteria gate the **client test**, not the vision, and the difference — the autonomy layer of user-story Act 3 — was invisible while only the criteria were tracked. Every "what exists" column was read off the code, which corrected two received claims (fixtures are a regression corpus, not a healing one; no source is HTML-scraped, so the first healer is a JSON-contract healer). |
| 16 | [`bulk-endpoint-discovery.md`](bulk-endpoint-discovery.md) | **Field finding (2026-07-24):** onboarding must actively probe for bulk surfaces (SRU / OAI-PMH / bulk dumps) and prefer them over scraping — tuchtrecht was scraped (13 h @ 1 rps) when SRU gave the whole corpus in ~48 requests. Adds a fail-closed "probe 0", the two-tier content model (bulk-discover → deferred full-content), and the strategy-library backlog (`sru_records` done; OAI-PMH / bulk-archive TODO). Prerequisite for W6. |
| 17 | [`w6-onboarding-agent.md`](w6-onboarding-agent.md) | **W6 flagship architecture (2026-07-24):** productizes the proven onboarding flow (Stage B, 5/2/0 interventions) as three pieces — agent runtime (Claude Agent SDK with the engine's own verbs as tools), bounded read-only probe sandbox, and a UI (reusing the FastAPI+static stack) that renders probe reports, manifest diffs, and gate approvals as first-class objects. Specifies the concrete **surface gate** that wires probe 0 in (honour `recommended` / `raise_if_unenforced()` before any scraper; `frontend_xhr` + content-completeness as owed obligations) and a build order whose first slice is a tested `surface_gate()` helper. **Extended 2026-07-28 with §D and §E** — the two questions the GUI could not be built without: the agent runs **in-process** (one asyncio task per source, polling UI, refused-not-queued), and a verification gate is a **request/verdict pair** where `refused` is stored, blocks acceptance and is legible after a restart — never the absence of an approval. Slices 1–3 are built; the live run is owed. |
| 18 | [`query-understanding.md`](query-understanding.md) | **W4 design + build (design 2026-07-24, BUILT 2026-07-25 — exit criterion #4 met, Tier 0 only)** — free text → editable filter chips. Contains a significant finding: the *expansion* half of W4 is already shipped. Measured on the live corpus, the hybrid RRF + rerank stack resolves Dutch compounds at full precision (`tandartsklacht`: BM25 0 hits → hybrid 100, top-10 precision 10/10), so **no decompounder is being built**. The real gap is that ranking is not filtering (rerank orders "besluiten van OCW over subsidie juni 2026" correctly but returns `total=101` where the filtered truth is 3). Design = deterministic vocabulary/alias/date grammar first (closed-world, facet values are enumerable), constrained LLM parse only as a guarded second tier, every inference a removable provenance-labelled chip. Exit criterion #4 was **reached with no LLM in the query path** (§8 steps 1–3 built; the live example goes `total` 101 → 19, top hit unchanged). |
| 19 | [`aggregator-registry.md`](aggregator-registry.md) | **W6.5 item 2 design + steps 1–3 built (2026-07-25):** probe 0 is host-scoped, so a third-party aggregator republishing the same corpus is structurally invisible (**SURF-3**). Specifies a registry step keyed by **corpus type, not provider host**, a new top rank class `bulk_content` (metadata + full text + bytes), `registries/aggregators.yaml` as reviewable data, and the `ori_elastic` strategy that consumes it. Live-measured: Open Raadsinformatie carries **310 municipalities**, ~100% full-text coverage *of what it holds*, joins on the natural key the manifests already use (URN equality **59/59** on real ledger rows), and proxies the original PDF bytes. **§5 corrected 2026-07-25 — read it before quoting this doc on POL-1:** the route **shrinks** `api.notubiz.nl`'s part in the posture decision by ~85%, it does **not** dissolve it. Coverage is 88.3% per era with **0% before 2014** and a lagging recent window (**TOT-5**), so step 4 is a **three-role split manifest** (provider for the pre-2014 archive *and* the incremental poll, ORI for 2014–2026) and the posture re-ask covers **three** hosts, not two. Records the probe traps as negative knowledge (index-name date ≠ freshness; freshness ≠ archive depth; a sparse head ≠ coverage). |
| 20 | `pol1-posture-decision.md` *(internal, not in the public export)* | **The gate-1 decision memo (2026-07-25), decision input for the admin.** Options with their costs for the three hosts that serve `Disallow: /`. Closed 2026-07-27; the current position is [`access-posture.md`](access-posture.md). |
| 21 | `robots-posture-evidence.md` *(internal, not in the public export)* | **The measured robots.txt evidence under POL-1 (2026-07-26).** Nine hosts probed, with Wayback timelines and reuse channels. **§7 is mandatory reading before quoting any of it outward.** |
| 22 | [`access-posture.md`](access-posture.md) | **The framework that replaces one-off gate rulings (2026-07-27, admin-adopted).** Ambiguity about crawl consent is the **normal** state of a Dutch civic source, not an anomaly — the exception rate is 4/4, KOOP serves four different postures across four of its own hosts, `robots.txt` has no legal force here, and Brave indexes disallowed paths on both `open.overheid.nl` and `api.notubiz.nl` (incl. a `/documents/{id}/file` endpoint, measured 2026-07-27). **"Everyone ignores it" is explicitly NOT our justification** — the admin does not approve of the LLM industry's disregard, and the §1 warning says so in his words. The instrument becomes **two axes**: *acquisition* (fetch or not — expensive to reverse) and *exposure* (`T0` metadata-only → `T3` derived artefacts redistributable — cheap to reverse, because the projection is rebuildable). That split is what makes "we may change our mind" mechanical rather than aspirational, and it makes `acquisition: go` + `exposure: T0` a real position. §8 answers the Notubiz question with the per-era ORI table: **complete for 2014–2025, 0% for 2008–2013, lagging at the recent edge**. **Gate 1 closed 2026-07-27**: all three hosts ruled (`go` / `go` / `go, narrow`), all four manifests carry both axes, tier enforcement lives in the projector (T0) and the API (T1/T2/T3), and `validate_manifest` is **fail-closed** on the posture. A third axis was forced by measurement: **third-party processors are opt-in per source**, because Voyage's own privacy policy takes a *"worldwide, irrevocable, perpetual"* licence to train on submitted content absent an opt-out, with no retention period, no GDPR basis and no SCC/DPF transfer mechanism — so only `woo_besluiten` opts in, and the reranker degrades to `hybrid` with `rerank_blocked` for the rest. |
| 23 | [`cross-corpus-search.md`](cross-corpus-search.md) | **The architectural decision that closed exit criterion #3 (2026-07-28, built same day).** Every source was its own index and `related` ran kNN inside one, so user-story Act 4's cross-corpus journalist and criterion #3 were *structurally impossible*, not merely unbuilt. Settled as a **query-time union** of `civic-*` minus the chunk indices — **not** one shared index — for a reason visible in the code rather than in taste: `doc_mappings()` takes no dataset argument, so every index already shares one schema, one analyzer and one 1024-dim vector space; the shared index's only advantage is a property the system had already, while its cost is re-projecting ~49.4k docs and losing the per-source rebuild the *rebuildable projection* bet is made of. Verified against the live cluster before committing (multi-index BM25 + aggs + kNN + cross-index `_mget`; a pattern matching nothing is an empty set, not an error). Records the three places the union is **not** free — no `get` by `_id` (and never an index guessed from a URN prefix, which names the *provider*), RRF must key on index+id or silently drop a colliding document, and **reranking is governed by the pool's strictest member**, so at the cross-corpus default the flagship mode degrades to `hybrid` until `policy.processors` widens (an admin call). Building it surfaced **PROJ-2**: three of four live indices had silently drifted off the declared mapping because `ensure_index` never touches an index that exists — enforcement unaffected (both fields are read from `_source`), the schema a lie — fixed by re-projection, with `civic check-projection` as the standing control. Measured after: 17 of 100 sampled documents have a cross-source neighbour in their top 8. Leaves **PROJ-3** open on purpose (hybrid facets come from the BM25 leg alone, so they vanish when only the dense channel matches — repairing it changes what every facet count means). |
| 24 | [`search-operators.md`](search-operators.md) | **W11(a), designed and built 2026-07-28.** `idee.md` promises *"fulltext search met operators"* and names operator-less source UIs as the thing to beat; the query path was one `multi_match` with `operator: and`. Adds a real grammar — phrase quotes, `AND/EN`, `OR/OF`, `NOT/NIET/-`, parentheses, trailing wildcard — parsed in-engine (`engine/query_ops.py`) rather than handed to `query_string`, so the compiled clause is inspectable, the failure mode is fail-open, and the response can report **how the query was read** (`parsed`, with grouping made explicit) and **what was repaired**. Two rulings are the content: (a) operators and W4 chips **do not compose**, because chip extraction removes spans and strips stranded connectives — a set containing `en` and `of`, case-folded — so a chipped operator query would silently change the boolean the user typed; an operator query therefore keeps every word and says so. (b) The dense leg cannot express "not": a conjunctive exclusion is handed to kNN as a `must_not` filter (verified: 0/8 leaks against a 1/8 control), and a `NOT` under an `OR` — which no filter expresses — drops the search to BM25 with a stated reason rather than being approximated invisibly. Measured live: `"hoger onderwijs"` 12 vs 120 unquoted; `subsidie -onderwijs` 135 + `subsidie EN onderwijs` 85 = the 220 baseline exactly. **§9/§10 added later the same day:** §10 measured that the default `unified` highlighter picks passages by per-term score and ignores adjacency — only **16 of 50** fragments for `"college van beroep"` contained the phrase — and fixed it by switching to the `plain` highlighter *for phrase queries only* (**48/50**, no re-index, ~20 ms). §9 **composes chips with operators** (built the same day, applied-by-default): a chip may only be derived from a **root-conjunctive bare term** (the same rule §6 applies to exclusions, because a chip is a global filter), and the operation prunes the parse **tree** rather than editing the query string — which is what makes the `en`/`of` collision impossible by construction — an operator is a node, never a leaf, so nothing that removes words can reach one. Live: `"college van beroep" 2026` runs the phrase and filters the year (55 hits), `besluiten -OCW subsidie juni 2026` chips the date but **not** the negated publisher, and `subsidie OF (onderwijs juni 2026)` yields no chip at all. Records two measured caveats: a quoted phrase here matches **stems with a slot per elided stopword** (so `"college van beroep"` also matches *college hoger beroep*), and `max_analyzer_offset` is load-bearing — without it a 3.1M-character document fails the **entire** search on both highlighters. |

## Decisions already taken (with the admin)

- Postgres ledger = pipeline state **and** ground truth; OpenSearch purely rebuildable.
- Self-healing autonomy tiered by blast radius (see inventory §16; fleet dimension in
  the archetype doc).
- opub/open.overheid federation: reference for gap analysis and a stub-source option —
  not a primary data source.
- Single admin for now; multi-tenant self-service deferred.
- Search: BM25 + facets first; summary-level embeddings second; chunk embeddings
  opt-in per dataset (they freeze chunker params — a paid decision). Embedding A/B
  **done** (real WOO corpus) — **hybrid BM25 + dense (RRF) validated even for short
  keyword queries** (BM25 & dense are complementary: pooled recall@100 ~0.80 each →
  **0.98 fused**); use a **self-hosted open embedder (bge-m3)** (commercial/Voyage a
  modest premium + lock-in, ~12–70× costlier). Add a **related-documents/recommend
  mode** (doc-to-doc kNN — the embedding sweet spot). Full synthesis:
  [`../experiments/embeddings_ab/FINDINGS.md`](../experiments/embeddings_ab/FINDINGS.md).
- **Rerank stage (settled): a cross-encoder reorders the hybrid top-100 before display.**
  Reranker A/B **done** (pooled multi-relevance, real corpus): a strong reranker lifts
  bge-m3 top-10 ordering **+0.187 nDCG@10 (0.515 → 0.702)** — the reranker matters *far*
  more than the embedder, and the earlier "reranking is just an equalizer/ceiling" result
  was an artifact of a weak reranker + single-gold scoring. **Default = Voyage `rerank-2.5`
  API** (beats Cohere `rerank-v3.5` 0.702 vs 0.620; beats the mid open bge-reranker-v2-m3).
  Crucially the reranker is **decoupled from the embedder** — self-hosted bge-m3 embeddings
  + a managed rerank API runs on the CPU-only box with **no fixed GPU cost** (~$0.002/search,
  one 100-candidate call), landing within 0.025 nDCG@10 of the best-embedder base.
  See [`../experiments/embeddings_ab/rerank_api_findings.md`](../experiments/embeddings_ab/rerank_api_findings.md).
- **Query-side term expansion is NOT needed on the hybrid path (settled by measurement,
  2026-07-24).** Dutch compounds are the canonical case for a decompounder, and the
  shipped stack already resolves them: `tandartsklacht` returns **0** hits under BM25 but
  **100** under `hybrid`/`hybrid_rerank`, with **10/10** of the reranked top-10 genuinely
  containing `tandarts`. So W4 builds **query→filter understanding** (free text →
  removable, provenance-labelled facet chips), not query expansion; the gap it closes is
  that good *ranking* still leaves an unfiltered, uncountable result set. Deterministic
  vocabulary/alias/date grammar first — facet vocabularies are a closed world of a few
  hundred enumerable values — with a constrained LLM parse only as a guarded second tier.
  See [`query-understanding.md`](query-understanding.md).
- OCR is two-tier and never blocking: inline per-page-API head pages during
  extraction; the tail accumulates and is drained by periodic GPU-container burst runs
  with open-weights OCR VLMs. Engine A/B **done** — MinerU2.5-Pro selected, ≈ $17/M pages
  (~86× cheaper than Azure) at 0.913/0.991 of its fidelity; see
  [`../experiments/ocr_ab/production-setup.md`](../experiments/ocr_ab/production-setup.md).
  Embeddings batch the same way.
- Deterministic extraction first; cheap-LLM rescue only behind declared guard
  conditions, batched and budget-gated — and suppressed when failure rates indicate
  manifest drift (that's self-healing's job, not the LLM's).

## What the concept phase has NOT covered (next candidates)

- **The feasibility experiment** — the concrete next step; see
  [`feasibility-plan.md`](feasibility-plan.md) (walking skeleton → agent-onboards two
  novel sources → one real client slice, with go/no-go criteria).
- Tech selection & component architecture (queueing, scheduling, agent runtime, UI
  stack) and a deployment sketch against the existing ubuntu-srv pattern.
- The manifest DSL's *formal* schema (JSON Schema) — the examples are illustrative.
- Legal/AVG policy layer in depth (per-source retention, takedown workflow) — flagged
  in the feasibility plan as a pre-build one-pager.
