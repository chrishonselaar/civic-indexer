# Bulk-endpoint discovery — SRU / OAI-PMH / bulk dumps (field finding, 2026-07-24)

**Status: significant finding + fix landed (capability complete; W6 wiring remains).** A live backfill exposed a gap between the
onboarding *design* (which ranks bulk surfaces first) and the *engine + probe execution*
(which had no bulk-protocol strategy and never probed for one). The gap cost a chosen
strategy that was ~800× slower than an available alternative on the same provider. One of
the three protocols (SRU) is now implemented; OAI-PMH and bulk-archive remain backlog.
**All three bulk protocols and the fail-closed probe 0 are now built (updates below); what
remains is wiring probe 0 into the W6 onboarding agent's ranking step.** This doc records the finding (method: negative knowledge is kept), the corrected model of
"optimal", and the probe-battery amendment that makes the design intent enforceable.

**Update 2026-07-24 (same day):** `oai_pmh` is now implemented too
(`engine/strategies/oai_pmh.py` + `tests/test_strategy_oai.py`), mirroring
`sru_records`: `ListRecords` with `resumptionToken` paging, `completeListSize` as the
claimed total, deleted-record skip (counted, never silent), and manifest-driven
record→envelope map with `key_strip_prefix` to recover a bare natural key from a
namespaced OAI identifier. Two of the three bulk protocols are now capable; only the
bulk-archive router and the fail-closed **probe 0** remain (the latter still belongs in
W6). Built against the OAI-PMH 2.0 protocol spec — no live civic OAI source is wired yet,
so no manifest consumes it until probe 0 discovers one.

**Update 2026-07-24 (probe 0 DONE):** the fail-closed **probe 0** is now built —
`engine/probe.py` + `civic probe-bulk [<host>…] [--source <id>]` + `tests/test_probe.py`
(8 tests). It actively tests each provider host for SRU (`operation=explain`), OAI-PMH
(`verb=Identify` → `ListMetadataFormats`), sitemap (`robots.txt` `Sitemap:` / `sitemap.xml`),
and CKAN (`package_search`), and records each surface as `found` / `absent` (verified-absent
— a positive fact) / `error` / `manual`. **Fail-closed** is real: any transport failure or
5xx makes a surface `error`, which sets the report verdict to `incomplete` and makes
`raise_if_unenforced()` raise — an undetermined probe can never be read as "no bulk surface
exists" and can never silently justify a scraper. Verified **live**: `civic probe-bulk
--source tuchtrecht` returns `bulk_surface_available` and recommends
`sru @ repository.overheid.nl` — the exact surface that was missed — while proving the
public host (`tuchtrecht.overheid.nl`) carries none (artifact
`../experiments/stage_b/probes/tuchtrecht/probe0_bulk_surfaces.json`). All three bulk
protocols + probe 0 now exist; what remains for W6 is wiring probe 0 *into* the onboarding
agent's surface-ranking step (below).

**Ranking refinement found while building probe 0.** The Phase-1 prose ranked "bulk/feed"
above "structured API", but building the recommender exposed that a bare **sitemap/robots**
carries only URLs — no metadata, no body — so it still needs a per-URL fetch, whereas
**SRU/OAI/CKAN** carry rich metadata inline (the two-tier "searchable now" tier). So probe 0
recommends **metadata-bearing bulk (`bulk_metadata`, rank 1) ABOVE a bare URL index
(`bulk_index`, rank 2)**. This is not a contradiction of the design ranking but a
sharpening of it with the two-tier-content insight (SURF-2): "bulk" is only best when it
carries content or metadata, not when it is a plain link list. This matches the empirical
tuchtrecht win — SRU, not a sitemap, was the surface worth preferring.

## The evidence (tuchtrecht)

Stage B onboarded `tuchtrecht` (47,966 rulings) by **inventing `paged_html_search`** to
scrape the search UI at `tuchtrecht.overheid.nl` (logged as an ENGINE-GAP intervention in
`../experiments/stage_b/intervention-log.md`), with per-document full-text pulled from the
FRBR file repository `repository.overheid.nl/frbr/...`. Two costs surfaced on the real
backfill (2026-07-24):

1. **The FRBR file host caps at ~1 rps** (measured: 1 rps → 0% HTTP 429; ~3.6 rps → 47%;
   ~3.7 rps → 69%; not connection-bound). Full-text for 48k docs = **~13 h**, unavoidable
   at that surface. Recorded in `../manifests/tuchtrecht.yaml` `onboarding_probes.fetch_rate_ceiling`.
2. **The same provider exposes SRU 2.0** at `repository.overheid.nl/sru`, which was
   **never probed**. `?query=c.product-area==tuchtrecht` returns `numberOfRecords: 47966`
   — the exact corpus — with full OWMS metadata + a human-written `dcterms:description`
   abstract (median 378, p90 1065, max 14k chars) + rich facets (9 domains, uitspraakdatum,
   verdict, zaaknummer, subject), **1000 records/request, no rate limit**. Whole corpus in
   **~48 requests (<1 min)** vs 13 h.

SRU was not considered-and-rejected. It was **missed** — because the engine had no strategy
that could consume it, so the agent took the path of least resistance (HTML scrape) among
the strategies that existed.

## What the design already said (and where it broke down)

`onboarding-flow.md` Phase 1 is explicit and correct:
- cheap surface scan includes "visible **bulk-download or open-data links**";
- archetype matching names "**CKAN, SRU/KOOP, open.overheid repository API**";
- discovery surfaces are ranked **best-first: `bulk/feed → structured API (SRU/CKAN/JSON)
  → paged search → grid enumeration → link-walk → headless browser`**.

The break was in **execution + capability**, not intent:
- **Capability:** the strategy library had *zero* bulk-protocol strategies
  (`paged_json_api`, `notubiz_events`, `paged_html_search` only). A ranking that prefers a
  surface the engine cannot run is unenforceable.
- **Execution:** the 13-probe battery has no *active* "is there a bulk endpoint?" probe.
  The ranking lived in prose, not in a runtime step that fails closed when skipped.

## "Optimal" is not "always use the bulk endpoint"

The tuchtrecht SRU record's `gzd:url` points **back at the 1-rps FRBR file host** — SRU
carries discovery + metadata + abstract, **not** full body text. So the optimal pattern is
not "bulk beats fetch"; it is a **two-tier content model**:

> **bulk-discover → assess content completeness → fetch full content only where it is
> missing *and* wanted.**

This is what was built for tuchtrecht: SRU discovery carries the abstract inline; the
extract phase distils it with **no per-document fetch** (`fetch.transport: none`); each item
is left **upgradable with `missing:['fulltext']`** so full body text is a *deferred, optional*
backfill (drain-style), never a blocker. Result: 47,950 items searchable (abstract + facets)
in minutes; full-text depth available later for the fraction that warrants it. This mirrors
the two-tier OCR strategy (`ocr-strategy.md`) — a general shape: **cheap complete tier now,
expensive depth tier deferred.**

## What "aware AND able to exploit optimally" requires

1. **A bulk-surface probe (probe 0), fail-closed.** — **DONE** (2026-07-24;
   `engine/probe.py`, `civic probe-bulk`, `tests/test_probe.py`; live-verified on
   tuchtrecht). Before ranking discovery surfaces, the agent actively tests, on the
   provider's host(s):
   - **SRU**: `?operation=explain` → parse `numberOfRecords`, `maximumRecords` cap, record
     schema; decide metadata-only vs full-content.
   - **OAI-PMH**: `?verb=Identify` then `?verb=ListMetadataFormats` → resumptionToken paging,
     `metadataPrefix` richness.
   - **Bulk dump / feed**: sitemap.xml, `robots.txt` Sitemap:, `.tar.gz`/`.zip` open-data
     links, **CKAN** `/api/3/action/package_search`, KOOP **BUS** changelog feed
     (`event-feed-desk-check.md`).
   - **Frontend XHR**: the site's own fetch/XHR calls (existing lesson: private JSON API
     outranks scraping).
   The probe records *found* **and** *verified-absent* (both are knowledge). If a bulk
   surface answers, the best-first ranking **must** prefer it over scraping — the manifest
   records why a lower surface was chosen when it is.

2. **Fill the strategy library** (the capability half):
   - `sru_records` — **DONE** (2026-07-24; `engine/strategies/sru_records.py`, manifest-driven
     record→envelope map, extract-from-payload via `fetch.transport: none`).
   - `oai_pmh` — **DONE** (2026-07-24; `engine/strategies/oai_pmh.py`, ListRecords +
     resumptionToken paging, `completeListSize` total, deleted-record skip, DC/rich
     metadataPrefix via the manifest field map; registered in `frontier.py` +
     `manifest.py`, `tests/test_strategy_oai.py`). No live source consumes it yet — it
     waits on probe 0 to surface an OAI endpoint the way SRU was surfaced for tuchtrecht.
   - bulk-archive fetch/format-router — **DONE** (2026-07-24; `engine/extract/archive.py`
     + `engine/extract/office.py` + `engine/archive_resolve.py`, CLI `civic
     resolve-archives`, `tests/test_extract_archive.py`). Local synchronous resolver over
     the `zip_dossier` deferred queue (no burst pod — pure CPU, unlike the OCR drain). The
     27 parked archives drained cleanly; details below.

### What the bulk-archive router found (2026-07-24)

Draining the 27 `zip_dossier` items exposed that **one magic signature (ZIP) hides two
completely different things**, and the split is a member fingerprint, not the bytes:

- **OOXML office documents mis-sniffed as archives (17/27).** All 17 Haarlem items were
  single Office files (1 DOCX + 16 XLSX) — an OOXML file *is* a zip. `archive.classify()`
  routes on the `[Content_Types].xml` + `word/`|`xl/`|`ppt/` fingerprint and `office.py`
  distils the one document (born-digital, confidence 1.0, no OCR tail). This is a
  general fix: any source can now emit `.docx/.xlsx/.pptx` and have it indexed, not parked.
- **True document archives (10/27).** The Woo besluiten ZIPs held 3–93 released PDFs each
  (198 total). Each routable member is fanned out to a **child item** `{parent}#NNNN`,
  linked to the parent by an `attachment_of` edge, and routed through the *same* native
  ladder the fetch phase uses — 150 extracted inline, **48 scanned members deferred to the
  existing `ocr_tail` queue** (the two-tier model applies recursively: cheap tier now,
  OCR depth deferred). The parent besluit is kept as a searchable **container**: a
  distillate listing its member titles, so the dossier still answers a query while every
  released document is independently findable. Verified live: all 198 members + 17 office
  docs + 10 containers project and return under bm25.

**Negative knowledge (cost real debugging time):** a fanned-out member is *unpacked*, never
*fetched*, so it has **no `blobs` row** — but the OCR-tail drain joins `deferred_queue` to a
*kept blob*. Deferring a scanned member without synthesising a kept `blobs` row silently
strands its pages (undrainable). The resolver now writes a synthetic `archive-member:<urn>`
blob row (`_keep_member_blob`) for exactly this reason. Any future "fan-out then defer"
path must register a blob row or the deferred work is invisible to its drain.

3. **Generalize the two-tier content model** beyond tuchtrecht: `fetch.transport: none` +
   `extraction: payload_text` + `missing:['fulltext']` upgrade path is currently a one-off
   config; promote it to a documented archetype capability so any bulk-metadata source can
   declare "searchable now, full content deferred".

## Where this lands

- **W6 (onboarding UI)** is the natural home: probe 0 must run *before* the flagship agent
  onboards new municipalities, or it will keep choosing fragile scrapers over bulk feeds.
  This is a prerequisite finding for that workstream, not a nice-to-have. **The probe itself
  is now built (`civic probe-bulk`); the remaining W6 work is the *gate*** — the onboarding
  agent must run it and honour `report.recommended` / `raise_if_unenforced()` before falling
  back to a scraper. Two things probe 0 leaves to the agent: the `frontend_xhr` surface (a
  `manual` result needing a DevTools/HAR pass), and the content-completeness classification
  of a found surface (SURF-2: metadata-only vs full-body, which decides the two-tier config).
  **This gate is now built** (`engine/onboarding.py::surface_gate()`, `tests/test_onboarding.py`;
  specified in `w6-onboarding-agent.md` §C) — it makes honouring the ranking
  enforceable-not-prose, exactly as probe 0 made the ranking itself enforceable: a scraper
  chosen over a metadata-bearing bulk surface is blocked (override only with a recorded
  justification), and the two boundaries above become owed obligations
  (`frontend_xhr_pass`, `content_completeness`) that the acceptance check
  (`GateDecision.raise_if_unresolved()`) blocks on.
- **`failure-modes.md`**: SURF-1 is now **closed** (was DETECTION-GAP) — probe 0 is the
  detection; "bulk endpoint exists but unprobed → slow/fragile surface chosen" now fails
  closed.
- **Archetype library** (`decision-inventory.md` / `notubiz-archetype.md`): SRU/KOOP and
  OAI-PMH become first-class archetypes with template manifests, not just names in a list.

## Reusability note

The overheid.nl / KOOP family is broad: `repository.overheid.nl/sru` serves many
collections beyond tuchtrecht (product-area is a query parameter), and OAI-PMH/CKAN are
common across Dutch open-data portals. A single well-built SRU archetype + the probe-0 step
likely converts several future sources from "13 h scrape" to "1 min bulk" — the same
100×-leverage argument as platform archetypes, applied to access protocols.
