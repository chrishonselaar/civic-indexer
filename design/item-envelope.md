# Item Envelope — the normalized record between pipeline stages

**What this is.** The concept draft of the record schema that flows across stage
boundaries (discover → fetch → expand-relations → route-format → extract → enrich →
index). This — not the manifest DSL — is the real universal contract: any novel source
mechanism, declarative or snippet, must emit and consume this shape. Derived from the
decision inventory (`decision-inventory.md`); every design choice below traces to
evidence from the four projects.

---

## 1. Design principles (each earned, not assumed)

1. **Partiality is explicit, never implied.** A record can exist with metadata but no
   document (V's pre-2024 SRU records), with a searchable head but an un-OCR'd tail (W's
   partial OCR), or as a federated stub (W's `woo-idx-` records). The envelope carries
   *what is missing and why* as first-class data — dropping or silently degrading is
   forbidden.
2. **Multi-provenance is preserved.** The same bytes reachable via two routes (G's grid +
   calendar; W's two portals) collapse to one record holding the **union of source URLs**.
   For civic transparency, *which official route published it* is itself signal.
3. **Relationships are typed edges, established by two mechanisms.** Crawl-time link
   discovery (W's sibling-URL regex) *and* index-time grouping keys (S's dossiernummer
   stem, V's derived case key). A parent→child tree is just the common special case.
4. **Status is two-axis, not one flat enum.** W's partial-OCR state had to be marked in
   *four places* — the smell of a flat enum under strain. We separate *pipeline stage
   reached* from *disposition at that stage*.
5. **Blobs are transient by default; the envelope outlives them.** The blob reference
   records validation (magic bytes, size, hash) and a keep/deleted decision with reason
   (W deletes after distill; V keeps because the source evaporates).
6. **Accounting rides on the record and the run.** Cost incurred (OCR pages, EUR, LLM
   tokens) and completeness context (which partition leaf found it, what the source
   claimed) are stored, because reconciliation and budget reporting are engine
   invariants.
7. **Everything derived is rebuildable.** Chunks, embeddings, and the OpenSearch
   projection are *not* envelope state — they are regenerable views. Postgres holds the
   envelope (ground truth); OpenSearch can always be rebuilt from it.

## 2. Two record kinds

The mining surfaced a mismatch: V's bomenbestand (~122 k living geo features) does not
fit a document-shaped record — there is no blob, no extraction, no OCR; there *is* daily
mutation and delta history. So:

- **DocumentItem** — the full envelope below. Woo-besluiten, uitspraken, raadsverslagen,
  vergunning-besluiten, bijlagen.
- **EntityRecord** — a slim sibling for living datasets: shares `identity`,
  `provenance`, `accounting`, `timestamps`, and edge participation; replaces
  `acquisition`/`content` with `attributes` (typed fields incl. geo) + a delta changelog.

Documents link to entities via typed edges (`about_entity`: vergunning → boom), which is
exactly the cross-dataset analysis the project's einddoel wants.

## 3. The envelope (concept sketch, YAML-flavored)

```yaml
identity:
  urn: "src:506941"                 # {source_id}:{natural_key} — global primary key
  natural_key: "506941"             # extracted per manifest EXPR
  cross_source_keys: ["c7e2…-uuid"] # e.g. open.overheid download-UUID
  content_hash: "sha256:…"          # set after fetch; secondary dedup key
  merge_policy: keep_separate       # or merged → merged_into: urn

provenance:
  source_id: "src"
  manifest_version: 14              # which adapter version discovered/processed it
  discovered_via:                   # completeness context
    strategy: paged_form_search
    partition_leaf: "year=2023"
    run_id: "run_2026-07-20_src_daily"
  urls:                             # multi-provenance union, never collapsed
    - {url: "https://…?uitspraakId=506941", role: landing, first_seen: …, last_seen: …}
    - {url: "https://…/download/…", role: file, first_seen: …}

status:
  stage: extracted                  # discovered | fetched | extracted | enriched | indexed
  disposition: partial              # ok | partial | deferred | failed | stub | superseded | gone_from_source
  missing: [ocr_tail]               # machine-readable partiality: fulltext | attachments |
                                    #   ocr_tail | case_route | …
  reason: "ocr_page_limit: 10/2400" # human-readable; deferred/failed carry retryable: bool
  upgradable: true                  # partial results that a later budgeted run can complete

acquisition:                        # DocumentItem only
  blob:
    sniffed_type: pdf               # magic-byte truth, never extension
    bytes: 512_338_112
    pages: 2400                     # cost-predictor input when source provides it
    validated: {magic: true, eof: true, min_size: true}
    kept: true                      # retention decision + reason
    kept_reason: partial_ocr        # ttl_source | partial_ocr | zip_source | deleted_after_distill
    storage_ref: "blobs/…"          # null if deleted; re-fetchable via urls[role=file]
  container:                        # if unpacked from an archive
    parent_urn: "woo:dossier-…"     # archive is provenance parent
    member_path: "bijlage-3.pdf"

relationships:
  edges:
    - {type: attachment_of, target: "woo:besluit-…", established: crawl,  confidence: high}
    - {type: appeal_of,     target: "src:2023_00346", established: index, via: group_key}
  group_keys:                       # index-time linkage material
    dossier: "2023/00346"
    case_id: "9822-…"

content:
  extraction:
    method: ocr_partial             # native | ocr_full | ocr_partial | llm_extract | structured
    engine: azure_prebuilt_read
    confidence: 0.9
    pages_processed: 10
    text_ref: "texts/…/506941.md"   # the durable distillate
    raw_llm_json_ref: null          # kept when PRM extraction ran (reinterpretation w/o re-spend)
  enrichment:
    fields: {normalized_uitkomst: "Aanbeveling", cited_codes: ["NRC"], …}   # manifest facet fields
    enricher_version: 7             # regenerable; version enables bulk re-runs
  summary_ref: null                 # LLM summary (also the doc-level embedding input)

accounting:
  cost: {ocr_pages: 10, ocr_eur: 0.014, llm_calls: 1, llm_tokens: 1840}
  claimed_total_context: {leaf_claimed: 133, leaf_position: 41}

timestamps:
  source_modified: …                # advisory — trust per manifest trust-override
  trusted_published: …              # derived per trust-override EXPR (e.g. URL path date)
  discovered: …
  fetched: …
  processed: …
  indexed: …
```

**EntityRecord** replaces `acquisition`/`content` with:

```yaml
attributes: {soort: "berk", kiemjaar: 1987, geo: {lat: …, lon: …}, …}
deltas:                              # storage scales with change, not corpus size
  - {run_id: …, kind: changed, diff: {kiemjaar: [null, 1987]}}
  - {run_id: …, kind: appeared}
```

## 4. Status vocabulary (two-axis)

**Stage** (monotonic pipeline position): `discovered → fetched → extracted → enriched →
indexed`.

**Disposition** (state at that stage):

| Disposition | Meaning | Evidence | Retry semantics |
|---|---|---|---|
| `ok` | Complete at this stage | — | proceed |
| `partial` | Usable but incomplete; `missing[]` + `upgradable` set | W partial OCR; V no-case-route | separate budgeted upgrade run |
| `deferred` | Deliberately not attempted; reason recorded | W oversize cohort (189 pubs), budget stops | explicit opt-in run |
| `failed` | Attempted and failed; `retryable: bool` | G failure manifest | retry pass if retryable |
| `stub` | Metadata-only by nature (federated pointer, forbidden section) | W `woo-idx-`, G denied modules | re-evaluate when a real adapter exists |
| `superseded` | Newer version/rectificatie replaces it | W aanvullend besluit | keep, link via `supersedes` edge |
| `gone_from_source` | Source removed it; our copy is the archive | V 6-week TTL; V bomen disappeared | never delete; flag |

The Woo ledger enum (`done_native`, `done_ocr_partial`, `truncated`, `stub`, …) maps
losslessly onto (stage × disposition), confirming the two axes suffice.

## 5. Edge types

| Type | Example (project) | Typical establishment |
|---|---|---|
| `attachment_of` | bijlage → besluit (W); doc → meeting/topic (G) | crawl |
| `part_of_dossier` | aanvraag ↔ besluit of one case (V) | index (derived key ladder) |
| `appeal_of` | CVB uitspraak → RCC uitspraak (S) | index (dossiernummer stem + decision-body TBL) |
| `supersedes` / `rectifies` | aanvullend besluit → besluit (W) | crawl or index |
| `responds_to` | antwoord → raadsvraag (G, future) | index |
| `derived_from` | ZIP member → archive; chunk-free provenance parent (W, G) | crawl/unpack |
| `about_entity` | kapvergunning → boom EntityRecord (V) | index (geo/id match) |
| `cites` | uitspraak → advertising-code article; future: ECLI refs (S) | enrichment |

Edges carry `established: crawl|index|enrichment` and `confidence`. Unresolvable targets
(a cited ECLI we don't index yet) are allowed as dangling typed references — same
philosophy as `[[wiki-links]]`: a dangling edge marks future work, not an error.

## 6. Run report (reconciliation record)

Per crawl run — this is what "success" means, and what the monitoring digest reads:

```yaml
run: {id, source_id, manifest_version, mode: incremental|backfill|upgrade, started, finished}
partitions:
  - {leaf: "2023-03", claimed: 41, harvested: 41, cap_hit: false}
  - {leaf: "2023-04", claimed: 512, harvested: 500, cap_hit: true}   # → auto-subdivide next run
reconciliation:
  claimed_total: 6034
  sum_of_leaves: 5980               # gap ⇒ date-hole warning (items outside all partitions)
  harvested_total: 5980
forbidden_sections: ["Raadsadressen", …]
selector_health: [{expr_id: result_item, matches: ok}, {expr_id: total_count, matches: 0}]  # 0 ⇒ alarm
outcomes: {new: 14, updated: 3, unchanged: 5963, deferred: {oversize: 2}, failed_retryable: 1}
cost: {requests: 214, ocr_pages: 380, ocr_eur: 0.52, llm_tokens: 41_000, budget_remaining_eur: 74.48}
verdict: reconciled | reconciled_with_warnings | NOT_reconciled   # engine-computed, not claimable
```

## 7. Postgres mapping (brief)

Ground truth lives in Postgres; OpenSearch is a rebuildable projection. Rough table set:

`sources`, `manifests` (versioned, incl. TBL/PRM payloads and fixtures refs), `items`
(envelope core, stage×disposition indexed), `item_urls`, `edges`, `blobs`, `extractions`,
`enrichments`, `entity_records` + `entity_deltas`, `runs` + `run_partitions`,
`fixtures` (cached HTML/JSON for regression), `deferred_queue`, `cost_ledger`.

Text distillates (`.md`) and kept blobs live on disk/object storage with refs in
Postgres — not as BLOBs in the database.

## 8. Open questions carried forward

1. **Event/delta discovery contract.** ✅ **Resolved** — desk-checked against the real
   KOOP BUS changelog mechanism in `event-feed-desk-check.md`. Verdict: no new hook;
   amendments = discovery roles (backfill/incremental/repair), tombstones in the
   discovery output contract, a stream mode in run accounting (high-water mark +
   contiguity + scheduled reference reconciliation), and an engine transport
   abstraction (SFTP). The envelope itself absorbed everything unchanged.
2. **Envelope versioning.** ✅ **Resolved** in `envelope-desk-checks.md`: three change
   classes — additive (vocabularies as lookup tables, not enums), derived-data
   (regenerate-and-replace, never migrate), core-semantic (rare; expand-and-contract).
   Read-time adaptation for ground truth explicitly rejected.
3. **Dangling-edge resolution.** ✅ **Resolved** in `envelope-desk-checks.md`: edges
   store typed `target_ref` + nullable `resolved_urn`; resolve-on-arrival (both
   directions) plus a batch linker when a new dataset onboards; query-time joins are UI
   convenience only. Dangling counts by scheme go in run reports and double as
   which-dataset-next prioritization signal.
4. **Geo in OpenSearch.** ✅ **Resolved** in `envelope-desk-checks.md`: entities live in
   Postgres (PostGIS only when a manifest needs it); documents carry `geo_point`
   denormalized through `about_entity` edges; entity search indexes are per-manifest
   opt-in projections.
5. **Chunk lineage.** ✅ **Confirmed** in `envelope-desk-checks.md`: chunks stay pure
   projections. One economic caveat: chunk-level embeddings freeze chunker params
   (re-chunking = paid re-embedding), so chunker changes on embedded datasets are
   budget-gated deliberate acts — strengthening the summary-embeddings-first plan.
