# Crawl / Scrape / Index Architecture — and lessons for a *universal* pluggable pipeline

**What this document is.** A synthesis of how this project actually crawls, scrapes and
indexes Dutch government Woo-besluiten from **two very different sources**
(`rijksoverheid.nl` and `open.overheid.nl`), how it discovers **bijlagen** (attachments)
from **besluiten** (decisions), and — most importantly — the **hard-won edge cases** that
made "scrape it with 100% completeness" far harder than it looks.

It is written with a second audience in mind: someone designing a **generic, pluggable
crawl→extract→index system** who wants to know *which* real-world failure modes such a
system must be adaptable to. Every war story below maps to a plug point in
[§13 — Toward a universal pluggable pipeline](#13-toward-a-universal-pluggable-pipeline).

> Companion docs: [`INDEX_UPDATE_RUNBOOK.md`](INDEX_UPDATE_RUNBOOK.md) is the operational
> "how to run it without wasting money" guide; this doc is the "how and why it is shaped
> this way" guide.

---

## 1. The shape of the problem

We are indexing **Woo-besluiten**: decisions on Freedom-of-Information requests. Each
decision is not one file — it is a small dossier:

```
Woo-besluit (the decision)
  ├── besluit-letter PDF          (the ruling itself)
  ├── lijst-van-documenten PDF    (inventaris: which docs were released)
  ├── bijlage-1 PDF               (the released documents themselves — often scans)
  ├── bijlage-2 PDF
  └── beslisnota / afschrift brief ...
```

The bijlagen are the interesting part (that's the actual released government content), they
are frequently **scanned** (needs OCR), frequently **redacted** (black boxes), sometimes
**enormous** (thousands of page-images, >500 MB), and sometimes bundled inside **ZIP
archives served with no `.zip` extension**. And the source of truth for where these live
**changed underneath us mid-project** (April 2026). Almost every design decision below is a
direct response to one of those facts.

### The 4-stage pipeline

The crawler (`crawlers/woo_besluit_crawler.py`) runs four independently-failing stages:

| Stage | Function | In | Out |
|---|---|---|---|
| 1 Scrape (discover) | `scrape_woo_publications()` | source search API | `data/scraped_woo_*.csv` + `{id}.json` stubs |
| 2 Download | `download_all_publications()` | publication URLs | `{id}.html`, `{id}.json`, (temp) `{id}.pdf` |
| 3 Process | `process_publication_pdfs()` | PDFs | `processed_output/YYYY/{id}.md` |
| 4 Index | `index_to_elasticsearch()` | `.md` + metadata | ES parents + chunks |

Each stage is **incremental / idempotent** by construction — it checks for prior output and
skips. This is what lets us re-run the whole thing daily without re-doing (or re-paying for)
work. The *nature* of the "have I done this already?" check is itself a subtle design
problem — see [§9](#9-completeness-and-the-never-redo-work-guarantee).

---

## 2. Two sources, two fundamentally different access models

The single most important architectural fact: **the canonical source migrated on
2026-04-28.** `rijksoverheid.nl` became **archive-only** (its search returns 0 Woo-besluiten
after that date); all new publications appear only on **`open.overheid.nl`**. We did **not**
throw away the old scraper — we run **both** (migration "Approach A: dual source"), writing
into one shared Elasticsearch index. This forces the pipeline to be *source-pluggable* at
Stage 1 while keeping Stages 2–4 identical.

The two sources could not be more different:

| | `rijksoverheid.nl` | `open.overheid.nl` |
|---|---|---|
| Mental model | **Publication portal** | **Document repository** |
| Unit of indexing | one *publication* (besluit + its attached PDFs) | one *file* (each PDF is its own UUID) |
| Access | HTML search → publication HTML → PDF link (3 round-trips) | JSON search → fetch PDF by UUID (1 round-trip) |
| Scale (Woo) | ~6,900 besluiten | ~50–100k file-entries (repo total ~675k docs) |
| Bundling | explicit — besluit page links its companions | implicit — related files only share title/date/publisher |
| Page count known pre-download? | no (must download + PyMuPDF) | **yes** — `aantalPaginas` is in the search response |

The rest of the doc treats these as two Stage-1 **plugins** behind a shared downstream.

---

## 3. Source A — `rijksoverheid.nl`, and the silent pagination cap

This is the richest source of "completeness is harder than it looks" lessons.

### 3.1 How the search is queried

Base endpoint: `https://www.rijksoverheid.nl/documenten`, a GET with these params:

```python
params = {
    "trefwoord": "",                 # empty keyword = match all
    "dateRange": "custom",
    "startdatum": "01-03-2025",      # Dutch dd-mm-YYYY
    "einddatum":  "31-03-2025",
    "onderdeel":  "Alle ministeries",
    "type":       "Woo-besluit",     # fixed document-type filter
    "pagina":     page,              # 1-based
}
```

Results are 10 per page. Pagination walks `pagina=1,2,3,…`, stopping when a page yields no
document links **or** no `"volgende"` ("next") anchor exists. Link extraction
(`extract_document_links_from_page`) is deliberately two-tier and defensive: first it looks
for result containers (`class` contains `result`/`item`/`document`) and pulls
`/documenten/` hrefs with `href.count('/') >= 3`; if that finds nothing (markup drift) it
falls back to scanning *all* anchors with the same href heuristic. Politeness:
`DOWNLOAD_DELAY_SECONDS=1` between page fetches, 30 s timeout, 3 retries with 5 s backoff.

### 3.2 THE EDGE CASE — a hard 500-result / 50-page silent cap

**The trap:** the search UI silently caps every query at **500 results = 50 pages × 10 per
page**. If you filter by a whole *year* that contains 1,200 besluiten, the UI happily paginates
to page 50, shows you 500 results, and then simply *stops* — no error, no "results
truncated" banner. You walk away believing you scraped the year. You missed 700 documents.
This is exactly how we lost **thousands of pages** in the early yearly-bracket crawls.

**How it was diagnosed** (`scripts/verify_total_count.py`): the UI *does* render a Dutch
total-count string — `"6.034 documenten, gefilterd op Woo-besluit"` (note the `.` thousands
separator). The verifier scrapes that claimed total, then probes the boundary directly by
requesting **`pagina=51`** on a wide date range and checking for any results:

```python
params["pagina"] = 51
...
if results_on_page > 0:
    print("Pagination limit does NOT apply")
else:
    print("Pagination limit IS enforced (500-result max)")
```

Comparing the claimed total (6,034) against the actual scrape (5,842) surfaced the gap.

**The fix — consecutive date-bracket subdivision.** Shrink each query's date window until
its result set fits under the cap. We walk **month by month** from `START_YEAR=2020` to now
(`get_month_ranges()` using `relativedelta(months=1)`, each bracket ending on
`next_month - 1 day`). No single month has ~500 Woo-besluiten, so each monthly query stays
under the cap. Because brackets are consecutive and non-overlapping, and dedup is URL-keyed,
the union reconstructs the full set.

**The cap is not silent to *us* any more** — we instrumented it. If any monthly query still
reaches `page >= 50`, we raise a `max_results_warning`, collect that month into
`months_over_500`, and log *"⚠ these months hit the 500-result limit — may need DAILY
re-scraping."* The subdivision granularity is a manual escalation ladder: **year → month →
day.** (Months have never actually tripped it, so daily brackets stay a documented fallback,
not standing code.)

### 3.3 A *second*, distinct blind spot: date-filter holes

`scripts/count_by_month.py` cross-checks a different failure mode. It sums the website's
*per-month* claimed counts and compares to the website's *overall* claimed count. When the
month-sum falls materially short of the overall total, that means **some documents carry no
valid publication date** and therefore appear in *no* monthly bracket at all — invisible to a
date-partitioned crawl no matter how fine you subdivide. This is a genuinely different bug
from the pagination cap: the cap loses the *tail* of a big bracket; the date-hole loses docs
that fall *between* all brackets. A universal crawler that partitions by any facet must
reconcile Σ(partition counts) against the unpartitioned total to catch this.

### 3.4 When the HTML disappeared: the SPA → JSON-API pivot

In **April 2026** `rijksoverheid.nl/documenten` became a Next.js SPA and the BeautifulSoup
pagination found *zero* links (the results are client-rendered). Rather than drive a headless
browser, we found the API the SPA itself calls: **`POST
https://www.rijksoverheid.nl/api/search`** with an Elastic Search-UI `{requestState,
queryConfig}` body (`scripts/scrape_via_api.py`). This is strictly better than HTML scraping:
structured JSON (no markup fragility), page size up to **50**, and — crucially — a reliable
`meta.page.total_results` / `total_pages` count read straight from the response instead of
regex-scraping a rendered string. The filter becomes
`{field:"information_type", values:["Woo-besluit"]}` plus a `sort_date` range.

**Lesson for a pluggable system:** a "source" is not a URL, it's an *access strategy that can
change under you*. The same logical source went HTML-list → SPA → private JSON API within one
project. The discovery plugin must be swappable without touching Stages 2–4.

---

## 4. Source B — `open.overheid.nl`, the repository API

Implemented in `scripts/scrape_open_overheid.py`. This is a **public JSON API, no auth**:

```
GET https://open.overheid.nl/overheid/openbaarmakingen/api/v0/zoek
      ?informatiecategorie="Woo-verzoeken en -besluiten"&aantal=20&start=N
→ { totaal, resultaten: [ { document:{id,pid,titel,openbaarmakingsdatum,publisher,…},
                            aantalPaginas, bestandsType, bestandsgrootte } , … ] }
```

Key design choices and gotchas:

- **Incremental by newest-first paging.** Results are sorted `openbaarmakingsdatum`
  descending. We page from the newest and **stop as soon as a result is older than the
  `--since` cutoff** — so a daily update reads only a handful of pages, never the whole repo.
  (`--safety-max 8000` records is a runaway backstop.)
- **Cost is knowable before download.** `aantalPaginas` in the search response lets us
  project OCR spend *without downloading a byte* — a luxury `rijksoverheid.nl` never gave us.
- **Filter syntax was a research problem.** The repo's real taxonomy is **TOOI URIs**
  (`https://identifier.overheid.nl/tooi/def/thes/kern/c_05f4a5f3`), and friendly filters like
  `type=Woo-besluit` return **0 results**. The shipped scraper sidesteps this by using the one
  human-readable param that works: `informatiecategorie="Woo-verzoeken en -besluiten"`.
- **Two ID shapes; only one is downloadable.** Real `8-4-4-4-12` **UUIDs** (Aanleverloket /
  direct uploads) resolve to a PDF at `…/documenten/{uuid}/file`. IDs that fail the UUID regex
  — `woo-idx-…` prefixes, `aanbieder == "Harvester"` — are **federated metadata stubs**
  (Rijkswaterstaat / provincial portals) that carry *no file here*, only a `weblocatie` back to
  the source portal. These are written to a separate `*_federated_deferred.csv` "rather than
  dropping silently" — they need a per-portal scraper we haven't built.
- **Scope creep.** The Woo category on open.overheid includes ~20k Rijkswaterstaat/provincial
  docs the legacy ministry-focused corpus never had. `--publishers` / `--exclude` substring
  filters scope it back to legacy coverage when desired.

The scraper emits the same `{id}.json` stub shape (`url`=landing page `pid`, `pdf_url`=the
`/file` UUID URL, `source:"open.overheid.nl"`) and **feeds the existing Stages 2–4
unchanged.**

---

## 5. Bijlage discovery — how one besluit fans out to many PDFs

This is a **`rijksoverheid.nl` mechanism** (open.overheid exposes each file independently; its
dossier-bundling API is 401-gated and unused).

The insight (and the trap): **rijksoverheid hosts a besluit's companions as *separate
publications*, not as attachments on the besluit page.** So the lijst-van-documenten, the
afschrift-brief, the aanvullend besluit each live at their own `/documenten/publicaties/…` or
`/documenten/brieven/…` URL. If you only crawl the besluit page you get the ruling and miss
the actual released content.

`extract_related_publication_urls()` finds them by scanning every anchor against:

```python
RELATED_DOC_PATH_RE = re.compile(
    r"^/documenten/(publicaties|brieven|woo-besluiten|kamerstukken|rapporten)"
    r"/(\d{4})/\d{2}/\d{2}/")
```

Matches are stored on the parent's JSON as `related_urls`. Stage 2 runs a **second download
pass** (`Companions`) that fetches each related URL through the *same*
`download_publication_page()` — so a companion is just another publication, recursively fed
through the identical machinery. Each publication page yields **one** main PDF (first anchor
whose href contains `.pdf` or `open.overheid.nl`).

Two helper scripts drive this outside the full pipeline: `fetch_companion_pages.py` (re-parse
cached HTML, fetch new companions) and `backfill_companion_urls.py` (corpus-wide parallel
backfill, `--concurrency 10`).

**Lesson:** attachment/child discovery is source-specific and must be a **plug point**. Here
"children" are sibling URLs matched by a path regex; on open.overheid they'd be a dossier API;
elsewhere they're `<a>` tags on the page. The pipeline treats a child as "just another item,"
which is what keeps the fan-out logic small.

---

## 6. Deduplication across sources — two keys, two layers

Because the same document can be reached via rijksoverheid *and* open.overheid (and via a
besluit *and* as someone's companion), dedup runs at two layers with two keys:

1. **Discovery-time, keyed on the open.overheid UUID** (`scrape_open_overheid.py`). Before
   indexing a newly-discovered doc it asks ES `_count` with a `wildcard` on `pdf_url` for
   `*{uuid}*`. **Why this is the cross-source key:** a rijksoverheid publication page links its
   PDF to `open.overheid.nl/documenten/{uuid}/file` — so a doc first discovered years ago via
   rijksoverheid *already* carries an open.overheid UUID in its `pdf_url`. The same UUID
   appearing later in the open.overheid search therefore matches and is skipped. The check
   **fails open** (on error, treat as new — index-time dedup is the backstop).
2. **Index-time, keyed on `document_url`** (the publication URL) as the ES `_id`
   (Stage 4). Re-indexing the same URL is an idempotent upsert. A **content hash
   `text_sha256`** is the secondary key to catch identical text under different URLs.

A subtle but load-bearing refinement in Stage 4: "already indexed" is judged **not** by
parent-doc existence but by *whether chunks exist for it* — the skip set is built from a terms
aggregation on the **chunks index** `parent_id`. Reason: a legacy or content-less parent with
no chunks must be **re-processed**, otherwise stub docs stay frozen forever and their chunks
never get created. (This is the same "never trust mere existence" principle as the ledger in
[§9](#9-completeness-and-the-never-redo-work-guarantee).)

**Lesson:** a universal system needs a **stable cross-source identity** distinct from any one
source's URL. Here the download-UUID happened to be embedded in every source's link, which is
what made cross-source dedup tractable. Absent that, a content hash is the fallback.

---

## 7. The disk-frugal PDF lifecycle — download → extract → delete

A first-principles constraint: the bijlage corpus is **terabytes** of scanned PDFs, but the
*value* we keep is the extracted `.md` text (a few KB each). So PDFs are treated as
**transient** — downloaded only long enough to extract, then deleted. The whole design
protects a small disk.

**The normal path (main crawler, `recover_stubs.py`):**

1. **Skip the download entirely if a valid PDF is already on disk** — `is_real_pdf()` checks
   the first 4 bytes are `%PDF`.
2. **Guard oversize *before* committing disk** — two layers, because open.overheid serves big
   files *chunked with no `Content-Length`*:
   - Layer 1: if a `Content-Length` header exists and exceeds `--max-download-mb` (**500 MB**),
     abort before reading the body. `probe_oversize.py` does a header-only GET for triage.
   - Layer 2 (the important one): for downloads *without* `Content-Length`, stream in **64 KB**
     chunks and abort mid-stream the moment cumulative bytes exceed the cap, `unlink()`-ing the
     partial. This is the only way to skip a 900 MB file whose size "only becomes visible
     mid-download."
3. **Process** (native extract / OCR — [§8](#8-the-ocr-decision-and-workflow)).
4. Write `.md`, update the JSON `pdf_processing` block, then **`pdf_path.unlink()`** — the PDF
   is gone. The `pdf_url` stays in the JSON so it can always be re-fetched.

**When PDFs are deliberately KEPT.** Deletion is *wrong* for anything that might need more
work later, so the backfill/zip paths keep their sources:
- **Partial-OCR / truncated** docs (only first N pages were OCR'd — keep the PDF so the rest
  can be done later without re-downloading).
- **ZIP dossiers** (the source of inner PDFs).
Two retroactive cleanup scripts reclaim disk with explicit keep-rules:
`cleanup_processed_pdfs.py` deletes a PDF only if a `.md` ≥ **200 bytes** exists;
`cleanup_backfill_pdfs.py` deletes only if `pdf_processing.success` **and** not
`partial_ocr`/`is_truncated`/`zip_recovered` **and** `.md` ≥ 200 bytes **and** magic bytes are
`%PDF`. Everything else is kept.

**Lesson:** "temp file hygiene" is not a detail — for a scan-heavy corpus it is the
difference between a 20 GB working set and a 2 TB one. The generic rule: **a fetched blob is
deletable iff its distilled artifact is durably complete.** "Complete" needs a real predicate
(non-trivial `.md`, success flag, no partial/zip markers), not "the file exists."

---

## 8. The OCR decision and workflow

### 8.1 Deciding *whether* to OCR (the money-saving gate)

OCR (Azure) costs **$0.0015/page**; native extraction is free. So we OCR only genuine scans.
The gate combines a confidence score with a content-pattern check:

```python
text_is_substantial = len(text) >= 2000
needs_ocr = ( confidence < 0.7                            # PDF_TEXT_CONFIDENCE_THRESHOLD
              or (not pattern_valid and not text_is_substantial) )
```

- **Confidence = extracted_chars / (2000 × pages), capped at 1.0.** 2000 is the "typical chars
  per page" expectation. A text PDF saturates to 1.0; a scan (≈0 extractable chars) tends to 0.
- **Why the pattern check is guarded.** `pattern_valid` looks for formal Dutch-letter tokens
  ("Geachte / Datum / Betreft"). But **bijlagen and inventaris/CSV-style attachments
  legitimately lack those tokens even with perfect native text** — so a failed pattern match
  alone must *not* trigger OCR. It only counts when the text is *also* too short (<2000 chars),
  i.e. extraction genuinely failed. This subtlety was learned the expensive way (OCR'ing
  perfectly-readable attachment lists).

`recover_stubs.py::predict_ocr()` reproduces this gate **offline** (with a `chars_per_page <
200` "thin page" heuristic) so cost can be estimated without any Azure call.

### 8.2 The 3-tier extraction ladder

1. **PyMuPDF** (native) — always first, free.
2. **OCR** — only if `needs_ocr`. Engine by config: **Azure Document Intelligence
   (`prebuilt-read`)** primary; **Tesseract** (`nld+eng`, 300 DPI, `--psm 1`) as local
   fallback (`"auto"` tries Azure then Tesseract). OCR output is accepted only `if
   len(ocr_text) > len(native_text)`; on accept, confidence is set to 0.9.
3. **pdfplumber** — last resort, only if OCR produced nothing better.

### 8.3 Azure guardrails

- **Batching:** `prebuilt-read` returns per-word polygons (~200 bytes JSON/word), so a naive
  500-page call could return hundreds of MB and OOM. Pages are OCR'd in **50-page batches**
  (`AZURE_OCR_BATCH_SIZE`) so each response stays small.
- **Hard page cap:** above `AZURE_OCR_MAX_PAGES = 1500`, Azure OCR is **skipped entirely** and
  the partial PyMuPDF text is kept ("better than nothing").

### 8.4 Partial OCR — "give it a searchable head"

Big scans don't need full OCR to be findable. Above `--ocr-full-limit` (**100**) pages, only
the first `--ocr-partial-pages` (**10**) are OCR'd. Partials are marked **in four places** so
they're never mistaken for complete: (1) a loud banner atop the `.md`, (2) JSON fields
`partial_ocr` + `ocr_page_limit`, (3) ledger status `done_ocr_partial`, (4) a line in
`docs/PARTIAL_OCR_TODO_2026-06.md`. Keeping the PDF (§7) means the tail can be completed later.

### 8.5 Cost controls (defense in depth)

- **`--budget-eur`** hard cap (default 75; 40 for zips) → converted to a page budget
  (`EUR_PER_USD=0.92`) and enforced *before* each OCR; when hit, the doc keeps native text and
  a `BUDGET-STOP` TODO line is written. *"Never a surprise bill."*
- **`OCR_BASELINE_PAGES × OCR_SAFETY_MULTIPLIER`** → a USD ceiling checked before every OCR
  call (`check_azure_cost_limit`), derived from actual historical spend.
- **`--dry-run` / `--estimate`** project pages and EUR/USD with zero spend and zero writes
  (and need no Azure creds).

**Lesson:** for any pay-per-item enrichment, a pluggable pipeline needs (a) a *free predictor*
of the cost, (b) a *pre-flight budget gate* that degrades gracefully instead of failing, and
(c) *idempotent partial results* that can be upgraded later. All three are cheaper to design
in than to retrofit.

---

## 9. Completeness, and the "never re-do work" guarantee

Two bugs dominate this project's history, both about **trusting the wrong signal for "done."**

- **The 0-byte / truncated placeholder bug.** ~1,940 docs were silently missed across four
  crawl rounds because a failed download left a **0-byte `.pdf`** on disk, and the incremental
  check ("skip if file exists") treated the placeholder as done. Fixes: `is_real_pdf()`
  (magic-byte check) and `recover_truncated.py` (a PDF that starts `%PDF` but lacks `%%EOF` in
  its last 1024 bytes is an incomplete download — delete it so `--download` re-fetches).
- **The filesystem-vs-reality split.** Because `.md`/`.json` live on *both* a laptop and the
  server, a doc can be "done" on one machine and absent on the other. A filesystem scan of one
  machine therefore reports thousands of phantom "stubs."

The cure for both is a **content-addressed ledger** (`data/recovery_ledger.db`) keyed on the
open.overheid **UUID**, not on file existence. Status is an explicit enum (`done_native`,
`done_ocr`, `done_ocr_partial`, `truncated`, `unprocessed`, `stub`, `skipped`). Backfills are
driven from a **manifest** derived from the ledger, so the work-list is "what the ledger says
is undone," never "what files happen to be missing here." The completeness predicate is
concrete: **valid `pdf_processing` block AND `.md` ≥ 200 bytes.**

**Lesson:** "have I processed this?" must be answered by a **durable, content-keyed register
with an explicit status**, never by the presence of a file (which lies: 0 bytes, truncated,
half-OCR'd, or simply on another machine).

---

## 10. ZIP archives and oversize monsters

**ZIPs with no `.zip` extension.** Some `…/documenten/{uuid}/file` endpoints serve a **ZIP**
(magic bytes `PK`) bundling N inner bijlage PDFs. The main crawler saw "not a `%PDF`" and
skipped them. `recover_zips.py` cracks them: extract to a `tempfile.mkdtemp()`, run each inner
PDF through the *same* native-first→partial-OCR logic, **`unlink()` each inner PDF immediately**
after extraction (disk), concatenate the inner markdowns into **one `.md` per dossier** with
`## Bijlage: {name}` headers, `rmtree` the temp dir in `finally`, and keep the source ZIP.
The JSON gets `zip_recovered:True` + inner counts (which is exactly the flag that stops cleanup
from deleting the source). Some ZIPs are pure data (CSV/Excel) or scans-of-scans and are
treated as **not text-indexable → metadata-only**.

**Oversize (>500 MB) cohort — deferred *on purpose*.** An audit found **189 publications**
(163 giant scanned PDFs + 26 ZIPs) whose streams exceed 500 MB — multi-thousand-page scanned
dossiers. They are skipped by the `--max-download-mb 500` cap *before download* and thus have
**~zero text indexed** (do not assume "we have their first pages" — partial-OCR never ran on
them). They're deferred because full OCR would cost **hundreds of dollars** and risk OOM/disk
blowups; picking them up is a separate, budgeted, `--concurrency 1`, page-count-probed decision
(see `INDEX_UPDATE_RUNBOOK.md` §6). The 26 ZIPs are metadata-only.

**Lesson:** a universal crawler needs a **content-type/size triage** *before* it commits
resources — magic-byte sniffing (PDF vs ZIP vs HTML-error-page), a streaming size cap for
sources that hide `Content-Length`, and an explicit **"deferred / too-expensive" bucket** that
is recorded, not silently dropped.

---

## 11. Redaction detection, and indexing

**Redaction detection** (OpenCV) renders each page at 2× zoom, inverse-thresholds at grayscale
**30** to isolate near-black regions, finds contours, ignores anything under **1000 px²**
(noise), estimates redacted word-count from area, and tags position (top/middle/bottom third).
Markers `[REDACTED: page N, ~M words, position]` go into the `.md`. It is **disabled or
size-gated** on large scans (`--redaction-max-pages 150`) because rendering every page to an
image is the wall-clock bottleneck on 100–500 MB dossiers — and it only adds cosmetic markers,
never affects searchable text. (Cost/quality tradeoffs like this want to be a **toggle**, not a
hardcoded step.)

**Indexing & chunking (Stage 4).** Every parent doc is also split into **~750-word passage
chunks** (max 1200, min 200, 100-word overlap) by `document_chunker.py`, respecting paragraph
boundaries and preserving `[Page N]` markers for citations. Chunks are indexed to a separate
`woo-besluiten-chunks` index with **denormalized parent metadata** (ministry, date, type) for
filtering. This dual index is what makes MCP search cheap: a query returns a handful of
relevant ~750-word passages (~1.2k tokens) instead of whole 8k-token documents — an ~8×
token reduction and the ability to search across 5–10 docs per query. Both indexes use a Dutch
text analyzer.

---

## 12. Metadata extraction

`shared/document_extractor.py` (`WOODocumentExtractor`) enriches each publication with the
faceted metadata that search and filtering depend on — ministry, document type, date, Woo ID,
and content signals. The recurring theme is **defensiveness about source quirks**: every field
has a precedence ladder that prefers a *structured, trustworthy* signal and only falls back to
fuzzier ones, because each fuzzy signal has a known false-positive that bit us in production.

**Ministry** (`extract_ministry`), in precedence order:
1. `<meta name="DCTERMS.creator">` — the preferred signal, added by rijksoverheid's 2026 SPA.
   Matched (case-insensitively) against a hardcoded 12-entry `MINISTRIES` list, or accepted if
   the content simply starts with `"Ministerie van"`.
2. Breadcrumb (`class~=breadcrumb`) fallback.
3. **Last-resort** full-HTML lowercased substring scan. *Why it's last:* this scan wrongly
   hits "Algemene Zaken" in footer boilerplate present on every page — so it must never win
   over a real structured signal.

`department` is text matching `(Directie|Afdeling|Directoraat)`.

**Document type** (`classify_document_type`): lowercase `title + content[:1000]`, match
`DOCUMENT_TYPES` patterns in dict order (`woo-besluit`, `kamerstuk`, `rapport`, `brief`,
`besluit`). **Default when nothing matches = `"woo-besluit"`** (the corpus is Woo-first).

**Date** (`parse_date`): source is `<meta name="DCTERMS.issued">` (e.g.
`"2026-04-22T13:23:36.489Z"`), else a `<time>` element or `class~=date|datum`. Parsing order is
deliberate: **ISO-8601 regex first**, *then* Dutch `dd-mm-yyyy` (reordered to `yyyy-mm-dd`),
*then* `dateutil` with `dayfirst=True`. The ordering is load-bearing — `dayfirst=True` on ISO
input **flips month and day**, so ISO must be caught before dateutil ever sees it. Separately,
the stored `publication_date` field is known to contain garbage/future values, so downstream
**freshness** is judged not from it but from the `/YYYY/MM/DD/` path segment of the
`document_url` (and, in ES, from `crawled_date`).

**Woo ID** (`extract_woo_id`), in precedence order:
1. A 36-char UUID in the URL (`/([a-f0-9-]{36})/`).
2. Content patterns `WOO[- ](\d{4}[- ]\d+)` / `Woo…` / `besluit…`.
3. Fallback: last URL path segment (≤50 chars), else `"unknown"`.

**Enrichment** (`enrich_document`) folds in content keywords (Woo terms like `openbaarmaking`,
`weigering`, `persoonlijke levenssfeer`, `bedrijfs- en fabricagegegevens`) and the PDF-derived
fields that come back from Stage 3: `has_pdf`, `total_pages`, `processed_pages`, `is_truncated`,
`used_ocr`, `text_confidence`, `has_redactions`, `redaction_count`, `redaction_metadata`
(page / estimated_words / position / bbox), `crawled_date`, and a `processing_status`
(`completed` / `no_pdf`). These are exactly the fields **denormalized onto every chunk** so the
chunks index can filter by ministry/type/date/redactions without touching the parent.

On the **open.overheid** side the search API hands us most of this structured already —
`titel`, `publisher` (→ ministry), `openbaarmakingsdatum`, `omschrijving`, and `aantalPaginas`
as a first-class field — so its extractor path is thinner than the HTML-scraping path.

**Lesson for a pluggable system:** metadata extraction is a **per-field precedence ladder**, not
a single parser. Prefer explicit structured metadata (`<meta>`, API JSON) over full-text
heuristics; order parsers so the unambiguous format wins before a lenient one can mis-parse it;
and treat any field the source fills unreliably (here `publication_date`) as advisory — derive
the value you actually trust (freshness) from a signal you control (the URL path, `crawled_date`).

---

## 13. Toward a universal pluggable pipeline

Everything above generalizes to a small set of **plug points** and a catalogue of **edge cases
the framework must let a plugin declare it handles**. If you were building a generic
crawl→extract→index engine, this is the interface the WOO project's scar tissue argues for.

### 12.1 The plug points

| Plug point | Responsibility | What varied across our two sources |
|---|---|---|
| **Discovery / paging** | enumerate item IDs for a query window | HTML list → SPA → JSON API; record-paging vs page-number paging; newest-first `--since` cutoff |
| **Partitioning strategy** | split the query space to fit source limits | date brackets year→month→day to dodge the 500-cap |
| **Child/attachment expansion** | turn one item into its dossier | rijksoverheid: sibling-URL regex; open.overheid: (unused) dossier API |
| **Identity / dedup key** | stable cross-source ID + content hash | download-UUID in `pdf_url`; `text_sha256` secondary |
| **Fetcher** | acquire the blob, cheaply and safely | streaming size cap, magic-byte sniff, truncation check |
| **Extractor ladder** | blob → text, cheapest-first | PyMuPDF → Azure/Tesseract OCR → pdfplumber |
| **Cost predictor + budget gate** | project & cap paid enrichment | `aantalPaginas` / offline predictor; `--budget-eur` |
| **Completeness register** | durable, content-keyed "done" state | the ledger, status enum, manifest-driven runs |
| **Indexer** | text → searchable units | parent + overlapping chunks, denormalized facets |

### 12.2 The edge-case catalogue (what a plugin must be able to declare)

1. **Silent result caps.** Sources truncate result sets with no error. *Requirement:* always
   fetch the source's *claimed total* and reconcile it against what you retrieved; partition
   the query space until each slice fits under the cap; instrument the boundary
   (`page >= cap → warn`).
2. **Partition blind spots.** Facet-partitioning (by date, ministry, …) loses items with
   missing/invalid facet values. *Requirement:* reconcile Σ(partition counts) vs the
   unpartitioned total; keep a catch-all partition.
3. **Access model changes under you.** A source goes HTML → SPA → private API mid-project.
   *Requirement:* discovery is a swappable plugin; downstream stages never know the source.
4. **Attachments hidden as siblings.** The "document" is a dossier whose parts live at
   unrelated URLs. *Requirement:* a child-expansion hook; treat a child as "just another item."
5. **Placeholders that masquerade as success.** 0-byte, truncated, or wrong-type files pass a
   naive existence check. *Requirement:* validate the artifact (magic bytes, `%%EOF`, min size),
   and key "done" on a content register, not the filesystem.
6. **Multi-machine / multi-worker divergence.** "Done here" ≠ "done everywhere."
   *Requirement:* a single authoritative, content-keyed ledger; drive work from a manifest.
7. **Hidden size (no `Content-Length`).** Oversize blobs whose size only appears mid-stream.
   *Requirement:* a streaming byte cap that aborts and cleans up the partial.
8. **Pay-per-item enrichment.** OCR/LLM/API costs real money. *Requirement:* a free cost
   *predictor*, a pre-flight *budget gate* that degrades (keep cheap output) instead of failing,
   and *upgradable partial results*.
9. **Container formats.** ZIP/tar bundles (sometimes mislabeled). *Requirement:* magic-byte
   detection, recursive extraction into temp space, immediate cleanup, and a "not
   text-indexable → metadata-only" outcome.
10. **The deferred bucket.** Some items are legitimately too expensive/risky *right now*.
    *Requirement:* record them explicitly with a reason, never silently drop; make picking them
    up a separate budgeted run.
11. **Transient disk discipline.** For scan-heavy corpora the blobs dwarf the outputs.
    *Requirement:* fetch→extract→delete by default; keep a blob **iff** its distilled artifact is
    durably complete; retroactive cleanup with explicit keep-rules.
12. **Quality/cost toggles.** Expensive cosmetic steps (redaction detection) shouldn't block
    throughput. *Requirement:* make them size-gated toggles, not hardcoded stages.
13. **Metadata source quirks.** Boilerplate false-matches, ISO-vs-locale date ambiguity,
    "unreliable" fields. *Requirement:* prefer structured metadata (`<meta>`) over full-text
    scans; try unambiguous parsers first; derive freshness from a trustworthy field.

The through-line: **completeness and cost-safety are not features you add at the end — they are
properties of how "done," "too big," and "already paid for" are represented.** Get those three
representations right (a content-keyed status register, a pre-commit size/type triage, and a
free-predictor + budget gate) and the rest of the pipeline can stay small and source-agnostic.

---

*Sources for this document: `crawlers/woo_besluit_crawler.py`, `scripts/scrape_open_overheid.py`,
`scripts/scrape_via_api.py`, `scripts/scrape_with_monthly_filters.py`,
`scripts/verify_total_count.py`, `scripts/count_by_month.py`, `scripts/recover_stubs.py`,
`scripts/recover_zips.py`, `scripts/recover_truncated.py`, `scripts/run_backfill_from_ledger.py`,
`scripts/cleanup_processed_pdfs.py`, `scripts/cleanup_backfill_pdfs.py`, `scripts/probe_oversize.py`,
`shared/pdf_processor.py`, `shared/document_extractor.py`, `shared/document_chunker.py`, and the
`docs/` runbooks (INDEX_UPDATE_RUNBOOK, OPEN_OVERHEID_NL_CRAWLER_SCOPING, OVERSIZE_PDFS_2026-05,
MISSING_PDFS_20260429).*
