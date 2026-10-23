# Crawl / Scrape / Index Architecture

**Purpose of this document.** This captures how this project actually crawls, scrapes,
and indexes the Reclame Code Commissie (RCC / SRC) uitspraken site — and, as a second
source, ACM.nl and data.overheid.nl. It is written with a forward-looking lens: what
would a *universal, pluggable* crawl → scrape → index system need to abstract, and which
real-world edge cases forced non-obvious design decisions here. Every claim below is
grounded in the code in this repo; where something is a design consideration for a future
universal system rather than currently-implemented behavior, it is marked
**[design consideration]**.

Key files referenced:
- `docs/old/scraper.py` — original listing crawler (discovery)
- `docs/old/downloader.py` — original detail fetcher (scrape + metadata sidecars)
- `daily/daily_scrape_pipeline.py` — the production, unattended orchestrator (crawl → download → index)
- `shared/uitspraak_extractor.py` — deterministic (no-LLM) metadata/tag enrichment
- `search-ui/backend/process_scraped_data.py` — batch re-processing of the file corpus
- `scripts/acm_search_api_parser.py`, `scripts/test_all_acm_datasets.py` — the ACM / data.overheid.nl second source

---

## 1. The pipeline at a glance

The system is a **three-stage pipeline** with a hard boundary (a file on disk) between
each stage. Stages are independently restartable and each stage is idempotent.

```
  STAGE 1: DISCOVER (crawl)        STAGE 2: FETCH (scrape)          STAGE 3: INDEX
  ─────────────────────────        ───────────────────────         ──────────────────
  paginated search UI       →      per-document detail page   →     Elasticsearch
  → CSV of (year,page,             → {id}.html  (trimmed)           (bulk, streaming)
     dossiernummer, URL)           → {id}.json  (metadata)          + enrichment
```

Why a file boundary between every stage matters:

- **Crash-resumability.** If indexing dies, the HTML/JSON on disk is untouched; re-running
  Stage 3 alone re-indexes without re-crawling. If the crawl dies, the CSV holds progress.
- **Re-processing without re-fetching.** When the extraction schema changes (it has, several
  times — see the 1,940 `.OLD` sidecar files in `scraped_output/`), you re-run only
  Stage 3 against local HTML. You never hit the source site again to change how you *parse*
  what you already downloaded. This is the single biggest lever for both politeness and speed.
- **Auditability.** The raw HTML is the ground truth. Metadata is derived, disposable, and
  regenerable.

**[design consideration]** A universal system should treat these three stages as pluggable
interfaces: `Discoverer` (yields document identities + URLs), `Fetcher` (URL → raw bytes +
sidecar), `Indexer` (raw + sidecar → search backend). The file-on-disk contract between them
is what makes each swappable per source.

---

## 2. Stage 1 — Discovery / crawl: the search UI is the crawl surface

There is no sitemap, no feed, no bulk export, and no public API for RCC uitspraken. The
**only** enumeration surface is the site's own search form
(`https://www.reclamecode.nl/uitspraken/uitspraken-zoeken/`). This drove almost every hard
decision, so the "special circumstances" are worth spelling out.

### 2.1 The site is a stateful ASP.NET/Umbraco form — you cannot just GET pages

The search endpoint rejects naive requests. Each query must carry two **dynamic anti-forgery
tokens** that only exist inside the rendered search form:

- `__RequestVerificationToken`
- `ufprt` (Umbraco form-post round-trip token)

`get_dynamic_tokens()` fetches the search page first, scrapes both hidden `<input>` values,
and threads them into every subsequent query. Tokens are fetched **once per run** and reused
across all pages/years. If token fetching fails, the run aborts immediately — there is no
point continuing.

**[design consideration]** A universal discoverer needs a *session bootstrap* hook: "before
you can enumerate, go render page X and harvest tokens/cookies/CSRF." This is common to
ASP.NET, Drupal, and any form-driven search. The token has a lifetime, so a long crawl may
need to re-bootstrap mid-run.

### 2.2 There is no reliable total count and no "next page" button

The results header (`h2.results-title`, e.g. "133 Resultaten") is **unreliable** — the
original `scraper.py` has extensive comments noting the reported total is often a site-wide
or query-wide number, not the per-year page count, so it cannot be used to compute
"how many pages remain." There is also no rendered "next" control to follow.

Termination is therefore **sentinel-driven, not count-driven**. A page is the last page when
*either*:

1. the explicit `<h2 class="results-title">Geen resultaten gevonden</h2>` sentinel is present, or
2. zero result items (`div.rl_layout242_item`) parse out of the page.

The crawler pages `1, 2, 3, …` until one of those fires, then stops. This is the robust way
to handle any "infinite scroll / opaque pagination" source: **trust the data, not the
metadata.**

### 2.3 The result set is capped — so we shard the query by year to reach 100% completeness

This is the subtle one, and the reason completeness was hard. The search backend caps how
deep you can page into a single result set. A single unfiltered query therefore *cannot*
return the full ~25 years of decisions — the tail is simply unreachable.

The fix is to **partition the query space into slices small enough that each slice fits under
the cap**, then enumerate each slice exhaustively. Here the slice key is the **year**: every
query pins `Startdatum={year}-01-01` and `Totdatum={year}-12-31` and pages that year to
exhaustion (`scrape_year()`), then moves to the next year. The union of per-year crawls is
the complete corpus.

```
for year in years:
    page = 1
    while True:
        results = query(year, page, tokens)   # date-bounded slice
        if not results: break                  # sentinel / empty → year done
        write_csv(results)
        page += 1
```

**[design consideration]** "Partition-then-exhaust" is the general antidote to result caps.
The partition key varies by source: year, month, first letter, category, geographic region,
price bucket. A universal discoverer wants a pluggable **partition strategy** plus a check:
*if any single partition still hits the cap, sub-divide it further* (e.g. drop from year to
month). The current code assumes a year is always small enough — a busy year could in
principle still overflow, so an adaptive sub-division is the natural hardening.

### 2.4 What discovery emits

For each result item the crawler extracts the dossiernummer (`h3.rl-heading-style-h5`) and
the detail-page href (`a.rl-button-link`, made absolute against the base domain), and appends
one row to a CSV: `(Year, Page, Dossiernummer, URL)`. The CSV *is* the crawl frontier / work
queue for Stage 2. The original `scraper.py` could even resume from the last CSV row
(`get_last_scraped()`).

### 2.5 Polite pacing

`time.sleep(1)` between listing pages; the daily pipeline scrapes only the **current + next
year** (not a full historical re-crawl) so the daily load on the source is a handful of
requests. Full historical crawls are a one-time event whose output lives in `scraped_output/`.

---

## 3. Stage 2 — Fetch / scrape: detail pages, sidecars, and stable IDs

`downloader.py` (and its inlined copy in `daily_scrape_pipeline.py`) consumes the CSV and, for
each row, fetches the detail page and writes **two files**.

### 3.1 The stable identity: `uitspraakId` from the URL

The detail URL carries `?uitspraakId=NNNNNN`. This integer is the **canonical document
identity** across the whole system — it becomes the Elasticsearch `_id`, and it is embedded in
the filename. The dossiernummer is *not* used as the identity because it is not unique (see
§5.2 on CVB appeals). Deriving identity from a source-stable key, not from a filename or a
title, is what makes the whole pipeline idempotent.

### 3.2 Filename convention encodes provenance

```
{dossiernummer}_p{page}_uid{uitspraakId}.html   ← trimmed content
{dossiernummer}_p{page}_uid{uitspraakId}.json   ← metadata sidecar
```

Slashes in the dossiernummer are sanitized to `_`. Files are bucketed into per-year
directories (`scraped_output/{year}/`). The filename alone tells you the case number, which
listing page it came from, and its canonical ID — provenance you can reconstruct without
opening the file. `parse_filename()` recovers the `uid` by splitting on `_uid`.

### 3.3 Content trimming — store the decision, not the chrome

Rather than persist the full HTML page, the fetcher extracts only the main content container
(`div.rl-padding-section-large-5`) via `soup.select_one(...)` and saves `str(content_div)`.
If that selector is absent, it **falls back to saving the full raw HTML** (fail-open — never
lose data because a selector drifted). This trimming is a deliberate **temp-space** decision:
it strips site nav/header/footer/scripts, shrinking each stored document substantially while
keeping the formatted decision body for later re-parsing and for modal display in the UI.

### 3.4 The metadata sidecar (and its fragility)

Six fields are pulled from the listing card and written to the `.json` sidecar via **positional
`nth-child` CSS selectors**:

```
dossiernummer, product_dienst, datum, motivatie, uitspraak_meta, medium
```

e.g. `div.rl_layout242_item > div:nth-child(2) > div:nth-child(2)`. This is the **most fragile
part of the system** — it depends on the exact DOM ordering of the card and breaks silently if
the site re-orders columns. It is called out here precisely because a universal system should
prefer semantic/label-anchored extraction (find the label "Datum", take its sibling) over
positional selectors. The sidecar exists so that enrichment/indexing never needs to re-fetch
or re-parse the full page for these fields.

### 3.5 Concurrency and pacing

Async fetch via `aiohttp` + `aiofiles`. Concurrency is capped by a `TCPConnector(limit=...)`
— **1** in the original conservative downloader, **5** in the daily pipeline — with a
per-request delay (`DOWNLOAD_DELAY_SECONDS`) and a 60s timeout. Failures are caught per-task
(`asyncio.gather(..., return_exceptions=True)`) so one bad URL never sinks the batch; the row
is logged and skipped.

---

## 4. Bijlagen / attachments and appeal (CVB) documents

"How are bijlages discovered from besluiten" resolves into two distinct mechanisms in this
codebase, because "attachment" means different things for the two sources.

### 4.1 RCC: appeal decisions (CVB) as linked sibling documents

A single case (dossiernummer) can produce **multiple decision documents**: the first-instance
Commissie decision and, on appeal, a **College van Beroep (CVB)** decision. On RCC these
appear as separate search results whose dossiernummer carries a ` - CVB` suffix
(e.g. `2023_00346 - CVB`). There are **678** such CVB documents in the current corpus.

The system handles these by treating each as a **first-class document with its own
`uitspraakId`**, discovered through the same listing crawl — not as an "attachment" bolted
onto the original. The shared dossiernummer stem (`2023/00346`) is what links the appeal back
to the original decision. `uitspraak_extractor._extract_decision_body()` then classifies each
document as `RCC` / `VT` / `CVB` from its `uitspraak_meta`, so the appeal chain is
reconstructable at query time by grouping on dossiernummer and ordering by decision body.

**Design lesson:** related documents (original + appeal + correction) are discovered as
independent identities and *linked by a shared business key*, rather than one being nested
inside the other. This keeps discovery uniform (everything comes off the same listing) and
avoids a special "go find the attachments of X" crawl phase for this source.

### 4.2 ACM / data.overheid.nl: attachments as enumerated resources

For the ACM besluiten source, attachments are real files (PDF, CSV, XLSX, ZIP). Two discovery
paths exist:

- **CKAN API (`data.overheid.nl`)** — `package_show` returns a dataset's `resources[]` array,
  each with `url`, `format`, `name`, `size`. Attachment discovery here is a **structured list
  walk**, not HTML scraping: iterate `resources`, and classify each by `format` /
  URL extension (`.csv/.json/.xml/.xlsx`) to decide whether it is a real data file versus a
  landing-page link (`scripts/test_all_acm_datasets.py`).
- **ACM.nl detail pages** — `acm_search_api_parser.get_acm_document()` scrapes the publication
  page and can surface linked documents from the content body.

**[design consideration]** A universal fetcher needs an **attachment-enumeration hook** per
source that returns typed `(url, format, size)` tuples, plus a **format router** (see §6) that
dispatches each to the right extractor. RCC's "attachment" is another crawled page; ACM's is a
binary resource. Same abstraction, two plugins.

---

## 5. Deduplication across pages, runs, and sources

Dedup happens at **four** layers, cheapest-first, so expensive work is never repeated.

### 5.1 Filesystem-level skip (avoids re-download)

Before fetching, the downloader checks: *do both `{base}.html` and `{base}.json` already
exist?* If so, skip — no network request. A half-written pair (HTML present, JSON missing) is
detected and only the missing sidecar is regenerated from the local HTML. This makes Stage 2
resumable and makes daily re-runs cheap: the current/next-year crawl re-lists everything, but
only genuinely new documents are actually downloaded.

### 5.2 Identity-level dedup (the `uitspraakId` as `_id`)

Because the Elasticsearch `_id` **is** the `uitspraakId`, indexing the same document twice is a
harmless overwrite, never a duplicate row. This is *content-addressing by source key* and is
why the pipeline can be run repeatedly without a dedup pass. The dossiernummer deliberately is
**not** the identity, precisely because appeals reuse it (§4.1).

### 5.3 Index-level skip (avoids re-enrichment + re-indexing)

Stage 3 first scrolls the entire index for the set of existing `_id`s
(`get_indexed_ids()` — a paged scroll into a Python `set`), then `collect_files_to_index()`
walks the corpus and **skips any file whose `uid` is already indexed**. Only new documents are
enriched and bulk-indexed. Enrichment (regex over full text) is the CPU-heavy step, so
skipping already-indexed docs is the main indexing-time optimization.

### 5.4 Cross-source / cross-query dedup

Within the ACM discoverer, the same dataset surfaces under multiple search terms
("ACM", "Autoriteit Consument Markt"). These are collapsed by ID with the idiom
`{d['id']: d for d in all_datasets}.values()` before any per-dataset work. More broadly,
because each source assigns identity from its own stable key, **cross-source dedup is a policy
decision at index time**: choose whether a document that appears in two sources becomes one
merged record or two source-tagged records.

**[design consideration]** For a universal system, define a `document_key(source, raw)` per
plugin and a global `merge_policy`:
- *keep-separate* (default): namespace IDs by source (`rcc:506941`, `acm:…`) → two records.
- *merge*: define a cross-source equivalence (e.g. same ECLI, same official besluit number,
  or a content hash) and fold sidecar metadata from all sources into one record, tracking
  `sources[]`. A content hash (MD5/SHA of normalized text) is the fallback dedup key when no
  shared business key exists — not yet implemented here, but the natural extension.

---

## 6. Handling ZIP files and mixed binary formats

RCC decisions are HTML, so the primary pipeline is text/HTML only. ZIP handling appears in two
places and is worth documenting for the universal lens:

- **Corpus transport/backup.** The full scrape is shipped as a single archive
  (`docs/old/scrape_output.zip`, ~38 MB) rather than thousands of loose files. Bundling the
  immutable raw corpus into one compressed artifact is the cheap way to snapshot/move it.
- **ACM resources.** `resources[].format` can be `ZIP` (an archive of the actual CSV/XLSX/PDF).
  The current code classifies format but does not yet unpack.

**[design consideration]** A universal fetcher needs a **format router** keyed on
`(Content-Type, extension, format)`:

```
html   → BeautifulSoup content-div extraction  (current RCC path)
pdf    → text-layer extraction (pdfminer/pypdf), OCR fallback for scans
zip    → open in a temp dir, recurse each member back through the router, then discard the temp dir
csv/xlsx/json/xml → structured parse straight to sidecar fields
```

The critical constraint for ZIP (and any archive) is **temp-space discipline**: stream the
archive to a scratch dir, extract, extract-and-index members one at a time, and delete the
scratch dir immediately — never let an unpacked archive accumulate alongside the permanent
corpus. This is the same discipline the pipeline already applies to CSVs (§8).

---

## 7. Metadata extraction and enrichment (deterministic, no-LLM)

`shared/uitspraak_extractor.UitspraakExtractor` turns raw text + the six sidecar fields into a
rich, faceted document — **without any LLM**, using precompiled regex and lookup tables. It
processes ~10,600 documents in ~30 seconds. Two tiers:

**Tier 1 — parsed at scrape time (Stage 2):** the six positional-selector fields
(dossiernummer, product_dienst, datum, motivatie, uitspraak_meta, medium).

**Tier 2 — derived at index time (Stage 3)** by `extract_all()`:

| Derived field | Method |
|---|---|
| `normalized_uitkomst` | Rule ladder mapping messy outcome strings → a fixed vocabulary (Aanbeveling, VT, VAF, dVAF, Afwijzing, CVB Bevestigd/Vernietigd, Niet-ontvankelijk, Ingetrokken, …); rejects date-looking values |
| `product_category` | Keyword→category map over `product_dienst` |
| `cited_codes` | Word-boundary regex for the 11 known advertising codes (CDR, RVV, RSM, NRC, …) |
| `cited_articles` | Regex `art(ikel) N.N CODE` incl. the "van de CODE" variant → normalized `art. 3.1 CDR` |
| `decision_body` | RCC / VT / CVB from `uitspraak_meta` (drives the appeal-chain grouping, §4.1) |
| `media_types` | Keyword map (facebook/instagram→Social Media, tv/televisie→TV, …) |
| `violation_types` | Keyword sets over content+motivatie (misleiding, duurzaamheid, prijzen, …) |
| `complainant_type` | Named NGOs (Wakker Dier, Consumentenbond, Foodwatch…) → Consumentenorganisatie; else Concurrent/Particulier |
| `key_terms` | Frequency filter over a domain term whitelist (must appear ≥2×) |
| `tags` | Human-facing union of the above, capped at 10 |

Design principles worth carrying forward:
- **Fail-open enrichment.** If `extract_all()` throws, the pipeline indexes the *raw* document
  rather than dropping it (`enriched_doc = raw_doc`). Enrichment is additive, never a gate.
- **Idempotent + regenerable.** Enrichment reads only local files, so improving a rule = re-run
  Stage 3 over the corpus (`process_scraped_data.py` does the batch variant). No re-crawl.
- **Deterministic over probabilistic** where the domain vocabulary is closed (codes, outcomes,
  media). Cheap, explainable, auditable, and reproducible.

**[design consideration]** In a universal system, enrichment is a per-source ordered list of
`Enricher` plugins over a common document dict; the closed-vocabulary regex enrichers are
source-specific, while things like language detection, date normalization, and content-hashing
are source-agnostic and shared.

---

## 8. Keeping the workflow optimized / low temp-space

The pipeline is deliberately frugal with disk and redundant work:

- **Trim before store (§3.3):** persist the content div, not the full page.
- **Sidecar metadata (§3.4):** parse the six fields once at fetch time so enrichment/indexing
  never re-parse the full HTML for them.
- **Skip-if-exists at two layers (§5.1, §5.3):** no re-download of existing file pairs, no
  re-enrichment of already-indexed IDs. A daily run touches only genuinely new documents.
- **Streaming bulk indexing:** `helpers.streaming_bulk(..., chunk_size=100, max_retries=3,
  raise_on_error=False)` — documents are *generated lazily* (`generate_documents()` is a
  Python generator) and streamed in chunks, so the full corpus is **never materialized in
  memory** during indexing. One `UitspraakExtractor` is instantiated for the whole batch (its
  regexes precompile once).
- **Narrow daily crawl window:** only current + next year are re-listed daily; history is a
  one-time crawl.
- **CSV retention / cleanup:** `cleanup_old_csvs()` deletes crawl-frontier CSVs older than
  `CSV_RETENTION_DAYS` (default 7). The CSV is transient scaffolding, not corpus.
- **Refresh once, at the end:** `es.indices.refresh()` is called after the bulk load, not per
  document.
- **Watch-out — schema-migration bloat:** the 1,940 `.OLD` sidecar files are leftovers from
  reprocessing under an old schema. They are the concrete reminder that re-processing should
  **overwrite in place or clean up**, not accumulate parallel versions. A universal system
  wants an explicit "reprocess = replace" policy so scratch never becomes permanent.

**[design consideration]** ZIP/PDF/archive extraction (§6) must follow the same rule as CSVs:
scratch dir → extract → index members → delete scratch. The permanent corpus should hold only
the canonical raw artifact and its regenerable sidecar; everything else is temporary.

---

## 9. Stage 3 — Indexing target and search model

Documents land in Elasticsearch (`src-decisions-enriched`) with a mapping tuned for the domain:

- **Dutch analyzer** on `full_text` / `content` / `product_dienst` / `key_terms`.
- **`keyword`** on every facet field (normalized_uitkomst, product_category, cited_codes,
  cited_articles, decision_body, media_types, tags, complainant_type, dossiernummer,
  uitspraak_id, year).
- **Multi-format, `ignore_malformed` date** on `datum` (`dd-MM-yyyy || yyyy-MM-dd ||
  epoch_millis`) so a single bad date never rejects a document.
- **Dual body storage:** `full_text` (plain, for search/snippets) + `full_text_html` (formatted,
  for modal display) — extracted together in one `extract_text_from_html()` pass.
- **Hybrid search** (BM25 + dense embeddings via RRF) was layered on later; see
  `search-ui/backend/migrate_to_hybrid.py` and the hybrid-search docs. Embeddings are an
  *additional* enrichment over the same raw corpus — again, no re-crawl.

---

## 10. Summary: what a universal pluggable system abstracts

Distilled from the above, the reusable interface surface is:

1. **`Discoverer`** — bootstrap a session (tokens/cookies), enumerate a source, emit document
   identities + URLs. Must support: (a) sentinel-based termination for opaque pagination,
   (b) a **partition strategy** to beat result caps, with adaptive sub-division, (c) an
   attachment-enumeration hook.
2. **`Fetcher`** — URL → raw artifact + typed sidecar. Content-trim on store, fail-open on
   selector drift, per-source concurrency/politeness, skip-if-exists.
3. **`FormatRouter`** — dispatch by content type: html / pdf / csv / xlsx / zip(recurse), all
   under strict scratch-dir temp-space discipline.
4. **Identity + dedup policy** — `document_key(source, raw)` from a source-stable key
   (with content-hash fallback); index `_id` = key ⇒ idempotency; explicit cross-source
   merge-vs-keep-separate policy.
5. **`Enricher` chain** — ordered, fail-open, deterministic-where-possible, regenerable from
   local corpus with zero re-crawl.
6. **`Indexer`** — lazy generator → streaming bulk → single refresh; skip already-indexed IDs.

The through-line: **discovery is source-specific and hard; everything after the raw artifact is
local, idempotent, and regenerable.** Getting the raw bytes down once — completely and with
stable identity — is the whole game. Parsing, enrichment, dedup, and indexing are all cheap,
repeatable operations over that local ground truth.
```
