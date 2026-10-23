# Crawl / Scrape / Index approaches for gemeente sites

Field notes distilled from the prototype scripts and plans in this directory
(`examples/scrape_gemeenten/`). These were one-off harvests of Dutch municipal
council material — *raadsverslagen*, *raadsvoorstellen*, *moties*,
*schriftelijke vragen*, *politiek vragenuur* and their **bijlagen** (attachments)
— from Groningen and Amsterdam.

The purpose of this write-up is **not** to bless these scripts as reference
implementations. They are quick, per-site harvesters. The purpose is to capture,
in one place, *every distinct pattern and every gotcha we hit*, so a future
**universal, pluggable crawl → scrape → index system** knows what shapes of
source it has to adapt to, and which "simple" assumptions break in the field.

---

## 1. The sources we actually dealt with

Two municipalities, but effectively **three different platform archetypes**, and
the differences between them are the whole story.

| Source | Platform | Discovery model | Payload | Files |
|--------|----------|-----------------|---------|-------|
| Groningen — `gemeenteraad.groningen.nl/Documenten/...` | Custom CMS, static HTML | **URL enumeration** by `categorie/year/month`, then nested topic pages | PDF links in static HTML | `download-raadsverslagen.py`, `download-raadsvoorstellen.py`, `download_documents.py`, `download-politiek-vragenuur.py`, `plan-raadsvoorstellen*.txt`, `plan-politiek-vragenuur.txt` |
| Groningen — `gemeenteraad.groningen.nl/Vergaderingen/...` | Same CMS, calendar view | **Link-following** year → meeting → documents | PDF links in static HTML | `download_raadsverslagen.py`, `plan-raadsverslagen.txt` |
| Amsterdam — `notubiz.nl` / `raadsinformatie.nl` | **Notubiz** (a commercial council-information SaaS used by many NL municipalities) | **JSON API with pagination** + HTML "module" list views | JSON events, HTML tables of PDFs, HTML meeting pages | `scrape_amsterdam.py`, `download-politiek-vragenuur.py`(notubiz variant not here), `plan-notubiz.txt`, `plan-kalender-raadsverslagen.txt` |

The single most important architectural takeaway: **the same *kind* of document
(a raadsverslag) is reachable through completely different mechanics depending on
which platform the municipality runs.** A universal system cannot key off "what
the document is"; it has to key off "what platform/route serves it". That is the
core argument for a **per-platform adapter/plugin** design.

---

## 2. The three discovery models (crawl layer)

### 2a. URL enumeration ("guess the grid")
*Groningen `/Documenten`, most scripts.*

The archive is addressable as a predictable grid:

```
https://gemeenteraad.groningen.nl/Documenten/{category}/{year}/{month}
# month is an UNPADDED integer (1..12), not 01..12 — see plan-raadsvoorstellen.txt
```

So the crawler just iterates `category × year × month` and requests every cell.
Empty/nonexistent cells return **HTTP 404 → skip and continue** (see
`download_documents.py:63`, `scrape_groningen.py:115`). No index page needs to be
parsed for the time dimension at all.

- **Pro:** trivially parallelizable, no pagination, embarrassingly simple.
- **Con:** you must *know* the category list and the date range up front. Groningen's
  category list itself is scraped from `#subnavlist > li > a` on `/Documenten`
  (`download_documents.py:43`), so category discovery is a mini-crawl of its own.
- **Con:** you are guessing the URL scheme. If the site changes its routing or uses
  padded months, the whole grid silently returns 404s and you harvest *nothing* —
  a dangerous **silent-failure mode**. A universal system needs a "this grid
  returned 0 hits everywhere → alarm, don't declare success" guard.

### 2b. Link-following ("walk the calendar")
*Groningen `/Vergaderingen`, `download_raadsverslagen.py` + `plan-raadsverslagen.txt`.*

A classic 3-level hierarchical crawl:

```
year page  ──selector──▶  meeting pages  ──selector──▶  document (PDF) links
#vergaderingen > li > a     #meeting-container > section.general-documents.documents.active
                            > div.fold-out > ul > li > div > a
```

Each level is a CSS/JS selector applied to static HTML. You follow links instead
of guessing URLs, so you never harvest a dead cell — but you pay N+1 requests
(one per meeting) and you are tightly coupled to the DOM structure.

### 2c. API + pagination ("ask the backend")
*Amsterdam / Notubiz, `plan-kalender-raadsverslagen.txt`, `download_raadsverslagen.py`(amsterdam).*

Notubiz exposes a JSON events API:

```
https://api.notubiz.nl/events?...&organisation_id=281
   &date_from={year}-01-01 00:00:00&date_to={year}-12-31 00:00:00
   &page=1&per_page=200&sort_field=start_date&sort_order=ASC
```

You parse `events[]`, pull `self` (→ event ID), `plannings[0].start_date`,
`attributes[0].value` (title), then fetch a per-event HTML page
(`raadsinformatie.nl/vergadering/{id}`). This is the **cleanest** source — structured,
sortable, filterable by date — **but it introduces the pagination-cap problem
(section 4), which is the single biggest completeness risk in the whole project.**

**Design implication:** a universal crawler needs at least these three
pluggable "discovery strategies" — *grid-enumeration*, *link-walk*, and
*paged-API* — and probably a fourth for JS-rendered SPAs (not encountered here;
notably, **all of these sources served the links in static HTML**, so `bs4` was
enough and no headless browser was ever required — every plan explicitly notes
"geen javascript nodig").

---

## 3. Scrape layer (extraction)

Extraction was uniformly boring, which is good:

- **Selectors live in the plan, code applies them via BeautifulSoup.** The `.txt`
  plans record the exact `document.querySelectorAll(...)` path used in DevTools;
  the Python translates it to `soup.select(...)`. Keeping the selector as
  externalized config (not buried in code) is exactly what a pluggable system
  wants — selectors are the per-site config.
- **PDF detection two ways:** either a specific class (`a.document_pdf` on Notubiz,
  `scrape_amsterdam.py:42`) or the brute heuristic `href.lower().endswith('.pdf')`
  (`get_pdf_links`, everywhere). The extension heuristic is fragile — it misses
  PDFs served via query-string/streaming endpoints and misses non-PDF payloads
  (docx, zip) entirely. See section 6.
- **URL normalization:** `urljoin(base, href)` to resolve relative links;
  `unquote`/`urlparse` to recover a filename from an encoded URL
  (`scrape_amsterdam.py:48`).
- **Filename sanitization:** `re.sub(r'[<>:"/\\|?*]', '_', name)` is repeated in
  almost every script — Windows-hostile characters in Dutch titles (colons in
  meeting times like `16:30`, slashes) had to be scrubbed before they became paths.

---

## 4. The completeness traps (the part that actually matters)

This is where "just scrape it" stops being simple. Every one of these is a way to
*think you got everything when you didn't*.

### 4a. Paged search results with a hard cap
Notubiz enforces **`per_page` ≤ 200** (`plan-kalender-raadsverslagen.txt:3`). The
API returns a `pagination` object with `has_more_pages`, `total_items`,
`total_pages`. Our scripts request `per_page=200, page=1` **and only page 1**, then
merely *print a warning* if `has_more_pages` is true
(`download_raadsverslagen.py:64-65`):

```python
if data['pagination']['has_more_pages']:
    print(f"WARNING: Year {year} has more pages than can be retrieved ...")
```

That warning is a **silent data-loss bug in waiting**: if any single year has >200
events, everything past #200 is dropped. It happened not to trip for Amsterdam's
per-year council event counts, but it is pure luck.

**The mitigation we actually used — partition the query space below the cap.**
Rather than page through, we sliced the query by **date (per-year, sometimes
per-month) and per-category** so each individual request returns well under 200
rows. That is why every script loops `year × month × category`: it is not only for
URL enumeration, it is also the trick that keeps each **paged/API result set under
its cap**. Date/category partitioning is the universal escape hatch for capped
search UIs.

**Design implications for a universal system:**
- Never trust page 1. Read `total_items` / `has_more_pages` / result-count-in-DOM
  and **loop pages OR sub-partition until the reported total is fully covered.**
- Treat any "results capped at N" UI (very common in council search front-ends —
  they'll show "1000+ results" and refuse to paginate past, say, page 50) as a
  signal to **recursively narrow the query** (by date range, then category, then
  free-text facet) until each leaf query is under the cap, then union the leaves.
- **Reconcile counts:** compare `#harvested` against the source's own
  `total_items`. A mismatch is the only reliable "you missed something" signal.

### 4b. Access-denied sections
Half the Notubiz modules for Amsterdam return *"De toegang tot 'X' is geweigerd"*
(`plan-notubiz.txt:37-58`): Raadsadressen, Toezeggingen, Publieke documenten,
Bestuursdocumenten, Beleidsdocumenten, Verordeningen, Besluitenlijsten, … Only 4
of ~12 modules were public. A universal crawler must **enumerate the full module/
category space, record which are forbidden, and report coverage** — otherwise
"we indexed Amsterdam" quietly means "we indexed a third of Amsterdam".

### 4c. Boundary / future cells
Enumeration overshoots the present: scripts skip future months explicitly
(`download-raadsvoorstellen.py:71`, "Skip future months") and cap the year range.
A generic enumerator needs a "don't crawl past today" clock and a defined
start-of-archive.

### 4d. DOM-coupling fragility
Every selector like `#wrapper > div > div > article > section:nth-child(3) > ul >
li > div > a` is a redesign away from breaking. `nth-child(3)` especially. This is
why selectors must be per-site config with a **health check** (did this selector
match ≥1 element on a page we know is non-empty?).

### 4e. Two access routes to the same documents
Groningen exposes the same PDFs through both `/Documenten` (grid) and
`/Vergaderingen` (calendar). Harvesting both **double-counts** unless deduped
(section 7). Good for completeness (one route may expose what the other hides),
bad for storage/index hygiene.

---

## 5. Bijlage (attachment) discovery

"How do we get the attachments hanging off a hit" was handled structurally, not
by chasing "bijlage" labels:

- **Groningen grid:** a category/month page lists **topic links**
  (`section:nth-child(3) > ul > li > div > a`). Each topic page is then fetched and
  *its* PDFs harvested into a per-topic subfolder
  (`download-raadsvoorstellen.py:80-98`, `download_documents.py:81-99`). So the
  main document and its bijlagen are simply "all PDFs found one level below the
  topic". The folder layout `category/year/month/topic_name/filename.pdf` preserves
  the topic→attachment grouping.
- **Groningen calendar:** the meeting page's document section lists the verslag
  *and* its annexes together; all are pulled as a flat set for that meeting.
- **Notubiz:** each table row (`td.item_details > dl > dd a.document_pdf`) can carry
  multiple `document_pdf` anchors — the primary doc plus attachments — and all are
  taken.

**Generalization:** bijlage discovery = "recurse one level from the hit's detail
page and collect every payload link, grouping them under the parent's identity."
The parent (topic / meeting / agenda item) is the unit of grouping; attachments
inherit its date and folder. A universal system should model this explicitly as a
**parent record → child assets** relationship rather than a flat file list, so the
index can show "this motie + its 4 bijlagen" as one hit.

---

## 6. Zip files and non-PDF payloads

**Honest status: the current scripts do NOT handle zip files or non-PDF payloads.**
Extraction is hard-wired to `.pdf` (extension heuristic or `document_pdf` class).
Any `.zip`, `.docx`, `.xlsx` bundle — which councils *do* use to ship a dossier of
bijlagen as one download — is silently skipped. This is a **known gap** and a
first-class requirement for the universal system. What it needs to do:

- **Detect by Content-Type and magic bytes, not by URL extension.** URLs like
  `.../download?id=123` hide the real type; council CMSes love opaque download
  endpoints.
- **Unpack archives in-stream and index members individually**, while recording the
  archive as the provenance parent (so a zip of 20 bijlagen becomes 20 indexed docs
  that all point back to the same source archive + the same council item).
- **Stream the unpack** (see section 8) — do not extract a 200 MB zip to a temp dir
  and then read it; iterate members and pipe each into the extract/index step,
  discarding bytes as you go.
- **Guard against zip bombs / nested archives** with a member-count and
  total-uncompressed-size ceiling.

---

## 7. Deduplication decisions

Dedup happened at several layers, with increasing sophistication needed as sources
overlap:

1. **Within a page** — `pdf_links = set()` so a PDF linked twice on one page is
   fetched once (`scrape_groningen.py:58`, `download-politiek-vragenuur.py:40`).
2. **Across runs (idempotency)** — *skip if the file already exists and is
   non-empty*: `if os.path.exists(path) and os.path.getsize(path) > 0: skip`
   (`download_documents.py:109`, and every downloader). This makes re-runs cheap and
   resumable and is the primary "don't re-fetch" mechanism.
3. **Filename/path identity** — the on-disk path *is* the dedup key. Files are keyed
   by `basename(url)` within a `category/year/month[/topic]` folder. **Weakness:**
   two different documents that happen to share a basename collide/overwrite; and the
   *same* document under two URLs (e.g. Groningen's grid vs calendar route, 4e)
   produces two copies because the paths differ.
4. **Cross-source / content dedup — NOT implemented.** Nothing hashes content. The
   same verslag reachable via Notubiz *and* a municipal mirror, or via two routes on
   one site, is stored twice.

**Design implications for a universal system:**
- Key documents by a **content hash (e.g. SHA-256 of bytes)** in addition to source
  URL, so identical bytes from different URLs collapse to one indexed document with
  *multiple provenance URLs* recorded.
- Keep a **URL→hash and hash→doc manifest** so re-crawls skip by URL (cheap) but the
  store dedupes by hash (correct).
- For near-duplicates (same PDF re-exported, differing bytes) consider a secondary
  text-similarity check before showing them as separate hits.
- **Preserve, don't discard, the multiple provenance** — for civic transparency it
  matters *which* official route published a document, so dedup should merge records
  while keeping the union of source URLs.

---

## 8. Keeping the pipeline optimized (and low on temp space)

Several deliberate choices keep these harvests from ballooning memory/disk, and
they generalize well:

- **Two-phase: discover URLs → manifest file → download separately.** Several
  scripts don't download during the crawl at all; they append `path|||url` lines to
  a manifest (`document_urls*.txt`) and a *separate* downloader
  (`download_documents.py`) consumes it. Benefits: the discovery pass holds almost
  nothing in memory, the manifest is a resumable checkpoint, and the two phases can
  run at different times / parallelism. This crawl/fetch split is the single most
  reusable structural idea here.
- **Batched, locked manifest writes** — URLs are buffered and flushed in batches of
  50 under a `FileLock` (`scrape_amsterdam.py:BATCH_SIZE=50`, `scrape_groningen.py
  _write_batch_to_file`), so concurrent workers append safely without holding the
  whole list in RAM.
- **Streaming downloads, never whole-file-in-memory** — `stream=True` +
  `iter_content(chunk_size=8192)` writes chunks straight to the final file
  (`download_documents.py:80`, all downloaders). No temp staging directory; bytes go
  to their destination path directly.
- **Resume via HTTP Range** — the most complete downloader records the existing
  partial size and sends `Range: bytes={initial_pos}-`, handling `206 Partial
  Content` and `416 Range Not Satisfiable` ("already complete")
  (`download_documents.py:48-62`). Interrupted multi-GB harvests resume instead of
  restarting — no wasted bandwidth *or* temp space.
- **Skip-if-exists** (section 7.2) means re-runs cost near-zero I/O.
- **Connection reuse + retry/backoff** — thread-local `requests.Session` with an
  `HTTPAdapter(Retry(total=3, backoff_factor=0.5, status_forcelist=[500,502,503,
  504]))` (`download_documents.py:24-35`); pooled connections, automatic retry on
  transient 5xx.
- **Bounded concurrency + politeness** — `ThreadPoolExecutor(max_workers=3..5)` (or
  `asyncio.gather` in chunks of 10 for the async Amsterdam scraper), plus
  `time.sleep(...)` between requests/pages to avoid hammering the council servers.
  Concurrency is capped low deliberately: these are small public-sector boxes.
- **Failure manifest for retry** — failed downloads are written to
  `failed_downloads.txt` in the same `path|||url` format so a retry pass reuses the
  same downloader (`download_documents.py:157`).

**What's missing for a universal system:** the manifest → download split should be
extended so that *unpack (zip) → extract text → index* are also streaming stages of
the same pipeline, with each document's bytes discarded once its text + hash are
committed. The goal: **at no point does the whole corpus (or even a whole large
archive) need to exist on disk at once** — you stream each item through
fetch→hash→dedup-check→unpack→extract→index and keep only what the index needs.

---

## 9. What a universal pluggable crawl/scrape/index system needs

Rolling the above into requirements. The system should be a pipeline of swappable
stages, configured per source:

1. **Source adapter (plugin) per platform.** At minimum: a *Notubiz* adapter, a
   *generic-grid-enumeration* adapter, a *calendar-link-walk* adapter, and a
   *paged-search* adapter. Each adapter declares its discovery strategy, URL
   templates, selectors, pagination model, and auth/forbidden behavior. Groningen
   and Amsterdam alone required three of these.

2. **Discovery strategies as first-class, composable pieces:**
   grid-enumeration (`cat × year × month`, 404-skip), link-walk (N-level selector
   chains), and paged-API/paged-search — the last with **mandatory cap handling**:
   read the source's own total, and either page through or recursively partition the
   query (by date → category → facet) until every leaf is under the cap, then union.

3. **Completeness accounting.** Every crawl reports: cells/queries attempted,
   forbidden sections, source-reported totals vs harvested counts, and selectors
   that matched zero elements. "Success" must mean *reconciled*, not *ran without
   exceptions*. (This directly answers the 4a warning-and-continue bug and the 4b
   silent-partial-coverage problem.)

4. **Payload-type detection beyond extension** — Content-Type + magic bytes; and a
   **streaming archive unpacker** that indexes zip/dossier members individually
   while recording the archive as provenance parent (section 6).

5. **Parent→child asset model** so a hit (motie, meeting, agenda item) carries its
   bijlagen as grouped children inheriting date/provenance (section 5).

6. **Content-hash dedup with multi-provenance** — dedupe by SHA-256, keep the union
   of source URLs, skip re-fetch by URL (section 7).

7. **Streaming, checkpointed pipeline** — crawl→manifest→fetch→unpack→extract→index
   as stages that pass items through and discard bytes early; resumable via manifest
   + Range; idempotent via skip-if-exists / hash-seen; bounded concurrency + per-host
   politeness (section 8).

8. **Selectors and templates as external config**, with health checks, so a site
   redesign degrades loudly (zero-match alarm) instead of silently harvesting
   nothing (section 4d).

---

## 10. File-by-file index

| File | Role | Notable technique |
|------|------|-------------------|
| `plan-raadsverslagen.txt` | Plan: Groningen calendar walk | 3-level selector chain |
| `plan-raadsvoorstellen.txt` / `plan-raadsvoorstellen-like.txt` | Plan: Groningen grid + topic pages | `cat/year/month` (unpadded) enumeration |
| `plan-politiek-vragenuur.txt` | Plan: Groningen single-category grid | simplest enumeration |
| `plan-kalender-raadsverslagen.txt` | Plan: Notubiz events API | `per_page ≤ 200`, `has_more_pages` cap |
| `plan-notubiz.txt` | Plan: Notubiz module list views | forbidden modules, `document_pdf` selector |
| `download-politiek-vragenuur.py` | Groningen `.pdf`-link harvest | `set()` dedup, per-request sleep |
| `download-raadsverslagen.py` | Groningen calendar → docs | selector-chain, ThreadPool(3) |
| `download_raadsverslagen.py` | Amsterdam/Notubiz API → per-event HTML | pagination warning, ThreadPool(5) |
| `download-raadsvoorstellen.py` | Groningen grid → topic → PDF | retry+backoff, skip-future-months |
| `download-raadsvoorstellen-like-backup.py` | Groningen full-category variant | writes URL manifest instead of downloading |
| `download_documents.py` | Standalone downloader | **Range-resume**, retry-session, failed-manifest, streaming |
| `scrape_amsterdam.py` | Async Notubiz module scraper | `aiohttp`, batched+locked manifest writes |
| `scrape_groningen.py` | Groningen all-categories → manifest | batched `FileLock` writes, tqdm progress |
```
