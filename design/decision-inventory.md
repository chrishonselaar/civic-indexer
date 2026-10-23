# Decision Inventory — mined from the four existing indexers

**What this is.** Every per-source decision actually made in the four documented projects
(Woo, SRC, Vergunningen, Gemeentes), consolidated and sorted by *shape*. This empirical
list — not an a-priori design — is the specification for the manifest DSL: what must be
expressible declaratively, what the engine owns universally, and what (if anything) is
left as snippet code.

**Method.** One row per decision that existed *because a particular source demanded it*.
Rows that turned out to vary across **zero** sources are engine invariants, not manifest
surface. Similar decisions across sources are consolidated into one row with multi-source
evidence — convergence is the point.

**Bucket legend**

| Code | Bucket | Meaning |
|---|---|---|
| `ENG` | Engine invariant | Universal behavior; never in a manifest |
| `CFG` | Config value | Plain scalar/list data |
| `STRAT` | Strategy selection | Choice from a fixed enum, each choice with its own param schema |
| `EXPR` | Restricted expression | Selector, JSONPath, regex, URL template, transform pipeline, precedence ladder |
| `TBL` | Generated table | Vocab/classification rules authored by the agent at onboarding, deterministic at runtime |
| `PRM` | Prompt + schema | Declarative LLM call: prompt text + JSON output schema + fallback |
| `SNIP` | Snippet | Sandboxed code escape hatch |

**Evidence key:** W = Woo, S = SRC/RCC, V = Vergunningen Groningen, G = Gemeentes.

---

## 1. Source-level declarations

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Volatility class: persistent / TTL-window / living-mutating (+ window length) | STRAT+CFG | V, W, S | Drives cadence, retry aggression, and *inverts* blob retention (V: the download IS the archive) |
| Crawl schedule; incremental window (current+next year; lookback 60 d) | CFG | S, V, W | |
| Budgets: EUR cap, page caps, safety multipliers | CFG | W | Enforced by engine gates, values per source |
| Politeness: delay, timeout, concurrency cap | CFG | all | Engine enforces per-host |
| Archive bounds: start year; don't-crawl-future clock | CFG | G, S | Clock guard itself is ENG |
| Scope filters: publisher/category include-exclude substrings | CFG | W | |
| Cross-source exclusion ("this data arrives via source Y, skip here") | CFG | V | ArcGIS excludes bomenbestand already fetched via WFS |

## 2. Discovery strategies & pagination

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Discovery strategy: paged-HTML-list / paged-JSON-API / form-POST search / server-cursor (SRU) / grid-enumeration / link-walk / bulk-fetch (WFS) / offset-paging (ArcGIS) / enumerate-classify-exclude | STRAT | all — **9 observed variants** | The largest enum in the DSL; each variant has its own param schema |
| URL / query template with **typed format specifiers** | EXPR | all | G's *unpadded month* (`/2023/3` not `/03`) proves padding must be explicit template syntax |
| JSON request-body template (SPA's own search API) | EXPR | W | From the HTML→SPA→private-API pivot |
| Result-item selector / JSONPath | EXPR | all | |
| Link extraction with fallback tier (result containers → all anchors) | EXPR + CFG | W | Fail-open tiering is a declarable option |
| Pagination params: page size, base index, cursor field (`nextRecordPosition`), offset field, `has_more` field | CFG/EXPR | all | Cursor rule: *follow the server's cursor, never compute offsets* is ENG guidance inside the strategy |
| Termination condition: sentinel-selector / zero-items / cursor-absent / shorter-than-pagesize / count-reached | STRAT | S, V, W, G | Combinable; sentinel text itself is EXPR |
| Sanity cap on paging (100 k features, 8 000 records) | CFG | V, W | Cap enforcement is ENG ("servers lie about counts") |
| Enumeration-dimension discovery (scrape category list from nav) | STRAT+EXPR | G | A mini-crawl that yields the grid's axes |
| Sub-source discovery: enumerate org services → classify by regex → exclude | STRAT+EXPR | V | New yearly ArcGIS layers auto-included with zero code change |
| Sort-order choice (ascending for stable historical walks, descending for incremental) | STRAT | V | |
| 404-as-empty-cell for grid enumeration | STRAT param | G | |
| **All-cells-empty alarm** (grid harvests 0 everywhere → fail loudly, never "success") | ENG | G | Antidote to the silent wrong-URL-scheme failure |

## 3. Completeness accounting & partitioning

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Claimed-total extractor (rendered string with locale separators; JSON `total_items` / `meta.page.total_results`) | EXPR | W, G | **Manifest is invalid without either this or an explicit "no trustworthy total" declaration** |
| "No trustworthy total" declaration + acknowledged coverage risk | STRAT | S | RCC's results header is meaningless; termination is sentinel-driven |
| Partition spec: key (date/category), granularity ladder (year→month→day), adaptive subdivision | STRAT+CFG | W, S, G | Partition-then-exhaust is the universal cap escape |
| Cap-boundary instrumentation (probe page cap+1; warn at page ≥ cap) | ENG | W | War-story diagnostic promoted to standing invariant |
| Σ(partition counts) vs unpartitioned total (date-hole detection) | ENG | W | Catches items with missing facet values that fall between all brackets |
| Harvested vs claimed reconciliation, per partition leaf and per run | ENG | W, G | "Success" = reconciled, not "ran without exceptions" |
| Forbidden-section enumeration + coverage reporting | ENG + CFG | G | "We indexed Amsterdam" must not silently mean a third of it |
| Selector health checks against a known-non-empty fixture | ENG + EXPR | G | Fixture reference is per-source; machinery is ENG |
| Partial-record indexing (metadata without documents) with explicit missing markers | ENG | V | Pre-2024 SRU records have no `case_url`; index the *knowledge that it exists* |

## 4. Session & access

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Session bootstrap: fetch page X, harvest named hidden inputs, thread into subsequent requests, abort-on-fail, re-bootstrap on expiry | STRAT | S | Looked like bespoke code; is a parameterized pattern covering ASP.NET/Umbraco/Drupal |
| Auth (API keys, login) | future STRAT | — | Not observed in the four; declared gap |
| Filter-vocabulary probing (friendly param works; TOOI URIs return 0) | onboarding-time agent task | W | Not runtime manifest content — the *result* (chosen params) is CFG |

## 5. Fetch & blob handling

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Streaming download, chunked write; never whole-file-in-memory | ENG | G, W | |
| HTTP Range resume (206 / 416) | ENG | G | |
| Retry/backoff with status list; per-task failure isolation | ENG + CFG | G, S, V | |
| Content-Length pre-check + mid-stream byte-cap abort + partial cleanup | ENG + CFG | W | Only way to stop a 900 MB file with hidden size |
| Magic-byte validation (`%PDF`, `PK`), `%%EOF` truncation check, min size | ENG | W, G | Placeholders must never pass as done |
| Type detection by magic bytes / Content-Type, never URL extension | ENG | G | `.pdf`-suffix heuristic misses opaque `/download?id=` endpoints |
| Blob retention: delete-after-distill (default) vs keep (TTL sources, partials, ZIP sources) | ENG + volatility override | W, V | "Deletable iff distilled artifact durably complete" |
| Content trimming before store (main-content selector; fail-open to full page) | EXPR + ENG | S | |
| Filename recovery as transform pipeline (`unquote ∘ unquote`, strip-query, hash fallback) | EXPR | V, G | Pipelines composed from a primitive library |
| Asset filtering: extension whitelist + name blacklist | EXPR | V | Documents, not chrome |
| Filename/path sanitization | ENG | V, G, S | |

## 6. Relationships & child expansion

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Child-expansion strategy: sibling-URL regex (W) / one-level-recurse-collect (G) / structured resource list, CKAN (S-ACM) / independent-discovery-plus-grouping-key (S-CVB) | STRAT+EXPR | all | Four distinct mechanisms observed |
| Crawl-time link edges **and** index-time grouping-key edges — both required | ENG (envelope) + EXPR | W vs S, V | The generalization that forced typed edges — see `item-envelope.md` |
| Derived stable entity-key ladder (`case_id` → title-regex `\d{9,}` → record id) | EXPR | V | One dossier appears as aanvraag + besluit records |
| Relationship-type classification from a field (RCC / VT / CVB) | TBL | S | |
| Child inherits parent date / provenance / grouping | ENG | G | |
| "A child is just another item" — recurse through identical machinery | ENG | W | Keeps fan-out logic small |
| Within-page link dedup (seen-set) | ENG | V, G | |

## 7. Identity & dedup

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Natural-key extractor (`uitspraakId` query param; SRU identifier; UUID in URL) | EXPR | all | |
| Negative knowledge: which plausible key is **not** identity (dossiernummer — reused by appeals) | manifest note | S | → onboarding must *test* key uniqueness, not assume it |
| Cross-source key (open.overheid UUID embedded in every source's `pdf_url`) | EXPR | W | |
| Content hash (SHA-256) secondary key; URL→hash manifest; multi-provenance union | ENG | W, G | Skip re-fetch by URL (cheap), dedup store by hash (correct), keep all provenance URLs |
| Merge policy: keep-separate (namespaced `src:506941`) vs merge-on-equivalence | STRAT | S | |
| Idempotent upsert by `_id` / `ON CONFLICT` | ENG | all | |
| "Already done" from ledger status — never file existence; chunks-exist, not parent-exists | ENG | W, V, S | The single most expensive lesson across all four |
| Fail-open on dedup-check errors (treat as new; index-time backstop) | ENG | W | |
| Identity classifier: downloadable vs federated-stub records (UUID regex vs `woo-idx-`) | EXPR + STRAT | W | Stubs routed to metadata-only, never dropped silently |

> **Follow-up for Stage B — temporal source authority (flagged 2026-07-23).** The
> rijksoverheid → open.overheid transition (verified empirically during the Stage A
> skeleton: rijksoverheid archive-only since 2026-04-28; open.overheid's zoek API lists
> native docs-with-files only from 2026, everything earlier = federated stubs) shows
> that a *dataset* can be fed by **multiple overlapping source manifests whose
> authority is time-bounded**. The identity machinery above (cross-source keys, content
> hash, merge policy, multi-provenance union) handles *record equivalence*, but "which
> source is canonical for which period" is today only implicit in per-source date
> bounds plus the federation-as-gap-analysis decision. Stage B should decide: (a) a
> per-dataset declaration of source authority windows (likely CFG on the dataset, not
> the source), (b) how reconciliation reports coverage when one logical dataset spans
> sources with disjoint/overlapping windows, and (c) whether the merge-on-equivalence
> linker prefers the authoritative-source record as primary. Concrete first exercise:
> importing the historical wob-search-mcp corpus (both portals) next to the live
> `woo_besluiten` manifest.

## 8. Format routing & containers

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Format router by sniffed type: html / pdf / zip / csv / xlsx / json / geojson | ENG | S, W, G | |
| ZIP: temp extract → member-wise processing through same ladder → immediate member cleanup → keep source | ENG | W | Members recorded with archive as provenance parent |
| Zip-bomb ceilings (member count, total uncompressed size) | ENG + CFG | G | |
| Non-text containers (CSV/Excel scans-of-scans) → metadata-only outcome | ENG | W | |
| Mislabeled containers (ZIP served without extension) | ENG | W | Solved by sniffing, above |
| Archive-level + member-level dedup keys | ENG | V | Repeated bundle must not reprocess every member |

## 9. Text extraction & OCR

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Extraction ladder ordering: native → OCR (azure\|tesseract\|auto) → pdfplumber; accept OCR only if longer | STRAT | W | Rungs are engine-provided; ordering/selection is manifest |
| OCR gate: confidence formula + content-pattern guard + min-chars | ENG + CFG + TBL | W | Thresholds CFG; the Dutch letter-token pattern list is a per-corpus TBL |
| Free cost predictor (`aantalPaginas` field; offline chars/page heuristic) | EXPR + ENG | W | Field mapping per source; predictor machinery ENG |
| Pre-flight budget gate; degrade-not-fail; `BUDGET-STOP` recorded | ENG | W | "Never a surprise bill" |
| Partial OCR with upgradable status | ENG + CFG | W | Searchable head now, tail later, PDF kept |
| OCR batching + hard page cap | ENG + CFG | W | |
| Two-tier OCR: inline per-page-API head (first N pages) + tail deferred to periodic GPU-burst cohort runs | STRAT + CFG | W (ext) | Generalizes partial-OCR-upgradable from budget exception to default; never blocks indexing — see `ocr-strategy.md` |
| Self-hosted OCR-VLM engine rungs (olmOCR, DeepSeek-OCR, PaddleOCR-VL, …); fleet-hour cost model + burst trigger | STRAT + CFG | W (ext) | Engine slot was already pluggable; per-item engine provenance already in envelope; A/B via the existing OCR gate |
| LLM document extraction: bytes → model, strict field schema, raw JSON retained | PRM + ENG | V | Raw retention enables reinterpretation without re-spend |
| LLM batch classification (titles ×50) with regex fallback + offline mode | PRM + EXPR | V | Includes false-positive filtering ("kap" = roof construction) |
| Extraction chain per source: structured → LLM → regex fallback, raw always kept | STRAT | V | |
| Required-field guard conditions → batched LLM rescue (deferred cohort, budget-gated) | CFG + PRM | V, S (ext) | Ladder exhausted + required unmet ⇒ `pending_llm_rescue`; drains against stored raw — see `extraction-fallback.md` |
| Incidental-vs-systematic routing: per-field failure-rate threshold (rescue items vs suppress + flag manifest drift → §16) | ENG + CFG | — (ext) | Rescue must never mask selector drift |

## 10. Metadata & enrichment

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Per-field precedence ladders (meta tag → breadcrumb → body scan; `LAT/LON` → geometry) | EXPR | W, V | Ordered list of expression attempts; structured beats fuzzy |
| Positional `nth-child` selectors — fragile → **label-anchored extraction** primitive instead | EXPR | S | The DSL should offer a *better* primitive than any codebase used: find label "Datum", take sibling |
| Date parsing: ISO-first ordering, locale formats, `dayfirst` pitfall | ENG + CFG | W, S | Load-bearing parser ordering is engine; locale is config |
| Untrusted-field policy: derive freshness from URL path / `crawled_date`, not the source's date field | EXPR | W | A declarable "trust override" |
| Field type coercion (int-or-None) | EXPR | V | Primitive-library member |
| Closed-vocab tables: outcome ladder, product categories, cited codes/articles regexes, media types, violation keywords, NGO list, ministries, doc types with default | TBL | S, W, V | The showcase for agent-generated deterministic enrichers — ~10.6 k docs in 30 s, no LLM |
| Key-term frequency filter (≥2), tag cap (10) | CFG | S | |
| Enrichment fail-open (index raw doc if enricher throws) | ENG | S | Enrichment is additive, never a gate |
| Post-hoc normalization as separate idempotent step with stored mapping | ENG + TBL | V | berk/berken/berkenboom → canonical; extraction stays literal |
| Singleton-vs-list JSON normalization | ENG | V | REST sources are not type-stable at n=1 |
| Redaction detection: optional size-gated toggle | STRAT + CFG | W | Cosmetic enrichers must be toggles, not stages |

## 11. Incremental, delta & living data

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Newest-first paging until `--since` cutoff | STRAT | W | Daily run reads a handful of pages, never the repo |
| Modified-since query with lookback overlap | STRAT + CFG | V | Overlap catches late publications |
| Cheap modified-timestamp check before expensive fetch | STRAT + EXPR | V | Where the timestamp lives is per-source |
| Delta detection vs stored state: compare-field list → changelog of deltas → replace current | STRAT + CFG | V | Storage scales with change, not corpus size |
| Once-per-day idempotence (snapshot log; `--force`) | ENG | V | |
| Incremental mode vs one-time historical mode, per source | STRAT/CFG | S, V | Different sort, page size, delay |
| Event/mutation feeds (KOOP BUS changelog) | STRAT (desk-checked) | V (ref) | Consume-a-stream, not enumerate-a-window; grounded against the real KOOP handleiding in `event-feed-desk-check.md` — fits as a strategy, no new hook |

## 12. Indexing & search projection

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Dual index: parents + overlapping chunks with denormalized facets | ENG | W | Chunks are pure projections, rebuildable |
| Chunking params (≈750 words, 100 overlap, min/max, page markers) | CFG | W | Engine defaults, rarely overridden |
| Analyzer / language | CFG | W, S | |
| Facet field list | manifest (from facet design) | S, W | Output of onboarding facet discovery |
| Multi-format `ignore_malformed` dates | ENG | S | One bad date never rejects a document |
| Dual body: plain (search) + html (display) | ENG | S | |
| Streaming bulk, lazy generators, refresh once | ENG | S | |
| Skip already-indexed via ledger | ENG | S, W | |
| Reprocess = replace (no `.OLD` accumulation) | ENG | S | |

## 13. Operational discipline

| Decision | Bucket | Ev | Note |
|---|---|---|---|
| Two-phase: discover → frontier/manifest → fetch | ENG | G, S | The single most reusable structural idea per G |
| Batched, locked frontier writes | ENG | G | |
| Batched DB writes; WAL mode | ENG | V | |
| Deferred bucket with explicit reason (oversize, budget, federated, forbidden) | ENG | W | Recorded, never silently dropped |
| Failure manifest → retry pass | ENG | G | |
| Temp-space: stream everything; scratch dirs deleted in `finally`; corpus never fully on disk | ENG | all | |
| Frontier retention cleanup | ENG + CFG | S | |
| Multi-machine divergence → one authoritative ledger | ENG | W | Now: the Postgres ledger |

---

## 14. Distribution — the punchlines

Approximate consolidated-row counts (many rows carry two buckets, e.g. a strategy plus its
expressions):

| Bucket | ~Rows | Share of non-engine surface |
|---|---|---|
| ENG (engine invariant) | ~48 | — (roughly **half of everything**) |
| STRAT | ~26 | ~40 % |
| EXPR | ~30 | ~45 % |
| CFG | ~22 | (parameters riding on the above) |
| TBL | ~8 | ~10 % |
| PRM | ~3 | ~5 % |
| **SNIP** | **~0** | **≈ 0 %** |

1. **Half the scar tissue is engine, not adapter.** The famous war stories (caps,
   placeholders, hidden sizes, ledger) are *universal invariants*. This validates the
   engine/manifest split empirically: build them once, make them non-optional.
2. **Snippet residue for these four sources is approximately zero** — *given the right
   primitives*. Everything that looked like bespoke code dissolved into a declarable
   pattern once named. Expect genuine residue from future sources; the escape hatch stays,
   but it is the exception path, not a load-bearing one.
3. **Some primitives are only visible by juxtaposition** — no single codebase contains
   them, the comparison does: label-anchored extraction (from S's `nth-child` fragility),
   typed format specifiers in URL templates (from G's unpadded month), trust overrides for
   unreliable fields (from W's garbage dates), derived-key ladders (from V's dossier
   variants), dimension-discovery mini-crawls (from G's category list).
4. **Three decisions are onboarding-time *agent tasks*, not manifest entries:** probing
   the filter vocabulary (W's TOOI trap), testing candidate-key uniqueness (S's
   dossiernummer trap), and probing for result caps (W's page-51 probe). These belong in a
   standard **source stress-test battery** the agent runs before a manifest is accepted.

## 15. The derived manifest DSL — primitive inventory

What the mining says the manifest language must contain, grouped:

- **Declarations:** volatility class, schedule, bounds, budgets, politeness, scope
  filters, cross-source exclusions, coverage-risk acknowledgments.
- **Discovery:** strategy enum (9 observed + event-feed + federated-stub + headless-last-resort
  reserved), URL/body templates with typed format specifiers, pagination params,
  termination conditions, sanity caps, dimension-discovery, sub-source
  enumerate-classify-exclude, sort-order, session bootstrap.
- **Completeness:** claimed-total extractor *or* explicit absence declaration, partition
  spec with adaptive ladder, forbidden-section lists, health-check fixtures.
- **Expressions** (the restricted-expression toolbox): CSS selector, label-anchored
  extractor, JSONPath, regex, URL template, transform pipeline over a primitive library
  (unquote, strip-query, basename, coerce-int, …), precedence ladder, derived-key ladder,
  trust override.
- **Identity:** natural-key extractor, cross-source keys, merge policy, stub classifier.
- **Relationships:** typed edge extractors (crawl-time) + grouping-key definitions
  (index-time) — see `item-envelope.md`.
- **Extraction:** ladder ordering, cost-predictor field mapping, OCR thresholds,
  partial-OCR limits, OCR tier split (inline head pages / burst tail), required-field
  guards with LLM-rescue engagement.
- **Tables:** closed-vocab classification/normalization rules (agent-generated,
  deterministic at runtime).
- **Prompts:** prompt + JSON output schema + fallback expression + offline mode.
- **Incremental:** since-cutoff, lookback, modified-check location, delta compare-fields.
- **Index projection:** facet field list, language, chunking overrides.
- **Escape hatch:** sandboxed snippet per hook, typed I/O, fixture-testable.

## 16. Blast-radius tiers (self-healing autonomy mapping)

The buckets *are* the autonomy tiers for agent-authored patches:

| Patch touches | Autonomy |
|---|---|
| CFG value | Auto-apply on regression green |
| EXPR | Auto-apply on regression green against cached fixtures |
| STRAT selection / params, TBL | Apply + notify admin (revertible) |
| PRM | Apply + notify; sample-output diff attached |
| SNIP (new or changed) | Always human review |
| Engine behavior | Never patchable per-source, by construction |
