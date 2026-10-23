# Failure-modes register — the growing list the harnesses are designed against

**What this is.** The single place where every observed or plausible failure mode of a
source/pipeline interaction is recorded, with (a) how we detect it — which battery probe
(`onboarding-flow.md` §3), engine invariant, or regression harness — and (b) how we
mitigate it. The 13-probe battery is the *procedural projection* of this list; this
register is the *evidence base* the battery is derived from, and the place new failure
modes land **first**. When an entry has no detection mechanism yet, that is a to-do on
the battery or the engine, and it is marked `DETECTION-GAP`.

**Rules of the register** (extends the working method in `CLAUDE.md`):

1. Every new trap met in the field gets an entry *when it is met* — including ones we
   dodged by luck. Stage B onboarding sessions must append here (the intervention log
   `../experiments/stage_b/intervention-log.md` records the *cost*; this file records
   the *lesson*).
2. Every entry cites evidence: W/S/V/G (the four documented projects), A (Stage A
   build), B1/B2… (Stage B sources), or `hypothesized` (allowed! — thought-up failure
   scenarios get entries too, marked so, and graduate to evidenced when seen live).
3. An entry is *closed* only when a deterministic detection exists (probe, validation
   rule, or fixture-regression) — "we'll notice" is not detection.
4. Categories are stable so harnesses can be built per category; add categories
   sparingly.
5. **A prompt-level obligation is not a control.** Rule 3 says "we'll notice" is not
   detection; POL-1 proves the stronger form — an instruction written into the agent's
   own flow spec is not detection either. `onboarding-flow.md` §Phase-1 has required
   robots directives be recorded since it was written, and **four sources shipped
   without it** because nothing mechanically checked. Text in the system prompt raises
   the probability of a step; only a verb, a schema field, or a validation rule makes it
   a control. When an entry's mitigation is "the flow doc says to", it is a
   `DETECTION-GAP`, not `covered`. (Evidenced 2026-07-25.)

Legend: **Detect** = probe # from the battery / engine invariant / harness.
Status: `covered` (detection + mitigation exist) · `DETECTION-GAP` · `hypothesized`.

---

## Category CAP — silent result limits

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| CAP-1 | Hard result/paging cap silently truncates wide queries | W (rijksoverheid 500), G (Notubiz 200), S (RCC depth), SRU 4200 — 5/5 sources | Probe 1 | partition ladder sized under cap | covered |
| CAP-2 | Cap rejects loudly (4xx) instead of clamping — walker must treat 400-on-oversize as *config* error, not transient | B1 Haarlem: `per_page=201` → HTTP 400 with explicit message | Probe 1 (record reject style) | retry classifier must not retry 400s | covered |
| CAP-3 | `total_items` **equal to** the cap value is ambiguous (true count vs truncated count) | B1 Haarlem: 2025 total = exactly 200 = cap; turned out genuine (half-years 117+83) — but only sub-cap partition probes can prove it | Probe 2 extension: **if claimed total == any known cap value, mandatory sub-partition cross-sum** | treat `total == cap` as untrusted until cross-summed | covered (new probe rule, added 2026-07-23) |

## Category API — request/response contract quirks

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| API-1 | Endpoint silently ignores parameters (echoes them unparsed) and returns generic "no parameters" error — looks like an empty/broken source | B1: api.notubiz.nl ignores ALL params until `version=` is supplied | Probe 5 (filter vocabulary) — extended: verify params are *parsed* (error text ≠ echo) before concluding anything | required `version` param recorded in archetype | covered |
| API-2 | Undocumented **required** parameter (e.g. `version`) gates the whole API | B1 (same incident) | DETECTION-GAP → battery needs a "minimal-request escalation" step: bare → +format → +version → +dates, recording which unlock | archetype records unlock set | **DETECTION-GAP** (procedure drafted, not yet in battery table) |
| API-3 | Server-side filter exists but LIES / returns unfiltered (W's TOOI-URI filters returning 0; opposite: filters ignored ⇒ full stream) | W; A (open.overheid ignores *unknown* params — see note below table) | Probe 5 (non-zero AND different results per param value) | client-side windowing fallback | covered |
| API-4 | Strict datetime format requirements with unhelpful errors on miss | B1: `date_from` must be `"YYYY-MM-DD HH:MM:SS"` exactly | Probe 5 records accepted formats | manifest stores format template | covered |
| API-5 | A metadata field **lies deterministically**: Notubiz document `publication_date` echoes the *request* date (every doc "published today") | B1 (2015 doc showed 2026-07-23); opub.nl displays the same class of error raw (see `ui-inspiration.md`, date 19/12/2913) | Probe 12/13 sample cross-check: date fields vs known meeting dates | manifest records the lying field + the trusted substitute (meeting planning date); `trusted_published` derivation | covered (new, 2026-07-23) |

**Corrected 2026-07-29:** the parameter is `zoektekst`, not `zoekterm`, and it works — `zoektekst=subsidie` returns 96,786 of 699,981, relevance-sorted, with `publicatiedatumVan/Tot`, `documentsoort` and `informatiecategorie` filters. The v0 API silently ignores *unknown* params instead of rejecting them, which is what made a wrong guess look like a missing feature. Probe 5 asks for "different results per param value" and would have caught it had it been run against the right name; the deeper lesson is that a param a site's own client does not use is not evidence about the API. Found by reading the SPA bundle.

## Category ID — identity & keys

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| ID-1 | Natural key reused across entities (dossiernummer reused by appeals) | S (CVB) | Probe 4 (uniqueness on ≥500 sample) | rejected-candidates list in manifest | covered |
| ID-2 | Key stable but *landing URL* unstable across republication | W (pid) | Probe 4 re-fetch check | key ≠ URL; URN from id | covered |
| ID-3 | Records with no usable identity at all | A (engine counts `unrecordable`) | engine invariant (never silent) | reported per run | covered |

## Category PAGE — pagination truth

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| PAGE-1 | Items shift across pages mid-walk (insertions) → duplicates or misses | W (2 dup records full-corpus), V (cursor-vs-offset) | Probe 6 double-walk; engine `walk_duplicates` counter | dedup by key; reconciliation tolerance | covered |
| PAGE-2 | `has_more_pages`-style sentinel lies | G field notes (warning-bug) | Probe 6 (walk past sentinel once) | prefer count-derived termination + empty-page confirm | covered |
| PAGE-3 | Tail sort anomaly: ordering breaks at stream end → incremental cutoff misses items | A (open.overheid) | Probe 6 on the *last* pages, not just first | `overlap_pages` on cutoff | covered |

## Category PLAT — platform/archetype skew

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| PLAT-1 | Host pattern matches archetype but site runs a **different product tier** — primary API empty while site is alive (looks like "no data") | B1: zaanstad.raadsinformatie.nl matches `{municipality}.raadsinformatie.nl` but events API returns 0 for all years; site is modules-only product | Fingerprint step 2 extension: **archetype match requires a positive data probe** (≥1 item via primary surface), not just signature match | classify as non-instance; reroute or bespoke-onboard | covered (new rule, 2026-07-23) |
| PLAT-2 | SaaS vendor rolls out API versions per customer → archetype drift per site | notubiz-archetype.md friction #4 | fingerprinter re-run cadence | archetype versioning + per-site pin | covered (design; unexercised) |
| PLAT-3 | Same municipality splits its data across **two sites/products** (meetings on portal X, documents on raadsinformatie modules) | B1 Zaanstad (gemeenteraad.zaanstad.nl + modules site) | Probe 10 (access coverage) must enumerate *sibling hosts* linked from the homepage | multi-surface manifest roles | **DETECTION-GAP** (sibling-host enumeration not yet a battery step) |

## Category SURF — discovery-surface selection

| id | failure mode | evidence | detection | mitigation | status |
|---|---|---|---|---|---|
| SURF-1 | A superior **bulk surface** (SRU / OAI-PMH / bulk dump / CKAN) exists but is never probed, so the agent picks a slow/fragile scrape — the best-first ranking is prose, not an enforced step | tuchtrecht: `repository.overheid.nl/sru` (whole 48k corpus in ~48 req, no rate limit) missed; onboarding built an HTML scraper + 1-rps FRBR fetch (~13 h) instead | **Probe 0 (bulk surface)** — active SRU/OAI/sitemap/CKAN/frontend-XHR test, fail-closed; `engine/probe.py` (`civic probe-bulk`), `tests/test_probe.py` | prefer bulk surface; record verified-absent; a transport/5xx `error` ⇒ verdict `incomplete` ⇒ `raise_if_unenforced()` blocks the scraper fallback; `sru_records`/`oai_pmh`/bulk-archive strategies + two-tier content model; W6 **surface gate** holds the agent to the ranking — built: `engine/onboarding.py::surface_gate()` blocks a scraper chosen over a metadata-bulk surface (override only with a recorded justification), `tests/test_onboarding.py` (`w6-onboarding-agent.md` §C) | **covered** (probe 0 built 2026-07-24; surface gate built 2026-07-24; verified live — `civic probe-bulk --source tuchtrecht` recommends the SRU host that was missed, artifact `../experiments/stage_b/probes/tuchtrecht/probe0_bulk_surfaces.json`) |
| SURF-2 | Bulk surface carries **metadata/abstract only**, not full body text — treating "bulk found" as "content complete" ships a shallow index | tuchtrecht SRU `gzd:url` points back at the 1-rps FRBR file host; abstract present, body absent | Probe 0 must classify each hit full-content vs metadata-only | two-tier: distil abstract now (`fetch.transport: none`), defer full text as `missing:['fulltext']` upgrade; W6 surface gate raises content-completeness classification as an owed obligation (`content_completeness`, blocks acceptance until resolved) — built: `engine/onboarding.py` (`w6-onboarding-agent.md` §C) | covered (pattern built for tuchtrecht; surface gate emits the obligation 2026-07-24; generalise per `bulk-endpoint-discovery.md`) |
| SURF-3 | Probe 0 is **host-scoped**: it tests SRU/OAI/CKAN/sitemap/XHR on the *provider's own* hosts only, so a **third-party aggregator republishing the same corpus** is structurally invisible. Verdict `no_bulk_surface` is then correct about the hosts probed and wrong in substance | B3 Vlaardingen re-onboard (2026-07-25): probe 0 returned `no_bulk_surface, recommended=null`; the agent then found Open Raadsinformatie (Open State Foundation/VNG) at `api.openraadsinformatie.nl/v1/elastic/`, index `ori_vlaardingen_*`, **16,583 MediaObjects carrying extracted full text**, joined on `original_url = api.notubiz.nl/document/{id}/{version}` — the exact natural key the manifest already uses. Independently re-verified by direct query. This is SURF-1 recurring one level up (aggregator instead of provider endpoint). **Re-probed live 2026-07-25 (`../design/aggregator-registry.md`): stronger than first recorded** — ORI covers **310 municipalities**, Vlaardingen 16,583/16,583 and Haarlem 70,160/70,163 PDFs carry extracted text, data current to 2026-07-09, and `/v1/resolve/…` **serves the original PDF bytes** — so for the era it covers, the pipeline need not contact `api.notubiz.nl` at all. **Coverage is NOT wholesale, measured 2026-07-25 (TOT-5): 88.3% per era, 0% before 2014, lagging in the recent window.** An earlier version of this cell said "the pipeline need never contact `api.notubiz.nl`" without that qualifier | DETECTION-GAP → probe 0 needs an **aggregator-registry step keyed by corpus type**, not by provider host (ORI / openbesluitvorming, data.overheid.nl, CKAN federations). Designed in [`aggregator-registry.md`](aggregator-registry.md): new rank class `bulk_content` above `bulk_metadata`, registry as reviewable YAML data, four probe traps pinned. **Second gap, found by TOT-5:** the step measured freshness only, so it could not see archive depth — it now aggregates both ends + `items_per_year` | prefer the aggregator when it carries full content the provider withholds — cuts OCR spend (W1) **and shrinks the POL-1 posture question for 2 of 4 sources by ~85%** (declared consent replaces inferred for the era it covers; the residue is a pre-cutoff backfill plus an incremental poll — see TOT-5, and do NOT restate this as "dissolves"); record verified-absent per registry entry | **DETECTION + CONSUMPTION BUILT 2026-07-25** (`probe_aggregators` + `registries/aggregators.yaml`, 13 tests; probe 0 on vlaardingen_raad now returns `bulk_surface_available` → `aggregator:open_raadsinformatie` where it returned `no_bulk_surface, recommended=null`. `engine/strategies/ori_elastic.py` + 11 tests consumes it: `search_after` walk, `original_url`→natural key, `text_pages`→page model, `method: aggregator` extraction rows). Still owed: pointing a manifest at the route — [`aggregator-registry.md`](aggregator-registry.md) §8 step 4, gated on the `aggregator_provenance` obligations incl. the unresolved ORI data licence |

### Note — content provenance ≠ discovery provenance (first seen 2026-07-25, SURF-3)

An aggregator route makes the *content* second-hand while the *identity* stays the
publisher's. Checked against the envelope before building
([`aggregator-registry.md`](aggregator-registry.md) §9): it **fits with no envelope change
and no migration** — `item_urls` already keeps a multi-provenance union (so the URN is
identical whether harvested direct or via the aggregator), and `extractions.method` is plain
`text`, not a `vocab_*` FK, with tuchtrecht's `method: "sru"` payload path as the working
precedent. Two rules fall out, both easy to get wrong:

- **Record imported text with `confidence: NULL`, never `1.0`.** 1.0 is right for a source's
  own authored text (tuchtrecht's abstract) and wrong for someone else's OCR of a scan — it
  launders an unverified extraction into a certainty.
- **A reconciled verdict on an aggregator walk means *aggregator-complete*, not
  *publisher-complete*.** Structure copes; meaning shifts. The manifest must declare which
  claim it reconciles against, or a green tick overstates itself (bet 4). **Measured, not
  assumed** (2026-07-25, see TOT-5): the gap is real, and it sits at **both ends** — a lagging
  recent window *and* a hard archival cutoff (ORI carries 0% of Vlaardingen's sampled 2008 and
  2012 documents). Declare `completeness_claim` **per role**, not per source: in a split
  manifest only the aggregator role is aggregator-complete.

## Category NET — transport pathologies

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| NET-1 | Server accepts TCP then blackholes (curl hangs even with `--max-time` on redirect chains) — probe scripts stall instead of failing | B1: gemeenteraad.zaanstad.nl hung a 2-minute shell timeout | politeness probe must wrap **wall-clock hard kill** around every probe, not per-request timeouts | engine: per-request AND per-walk deadlines | **DETECTION-GAP** (engine has per-request timeout only) |
| NET-2 | Rate-limit responses (429) vs refusals vs silent degradation | BUS "server busy"; small public boxes everywhere | Probe 11 | politeness ceiling in manifest; retry classes | covered |
| NET-3 | Oversize payloads mid-stream (Content-Length absent or lying) | W (extensionless ZIPs, 520 MB file); A (oversize cap + `bytes=0` regression) | Probe 7; engine streaming cap | `max_bytes` + magic/EOF validation; refused downloads charge €0 | covered |

## Category CNT — content & format traps

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| CNT-1 | Extension/Content-Type lies (ZIPs as .pdf, opaque `/download?id=`) | W, G | Probe 7 magic-bytes sample | format router on magic bytes only | covered |
| CNT-2 | Scanned PDFs with no text layer at high rates | W (16.5% in Stage A sample) | Probe 8 native-text confidence sample | two-tier OCR, never blocking | covered |
| CNT-3 | Positional JSON fields (`attributes[0]` as title) = nth-child fragility | G/archetype friction #6; B1 confirms `attributes[]` with stable ids present | Probe 12/13 record stable-id map | anchor on id, fall back positional, health-check | covered |

## Category TOT — claimed totals & reconciliation

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| TOT-1 | No total exists anywhere | S (meaningless header) | Probe 2 | `claimed_total: none` ⇒ compensating controls mandatory | covered |
| TOT-2 | Total exists but drifts during long walks (churn) | W/A (26,432 vs 26,430) | engine records first/last claim | ±0.1% churn tolerance verdict | covered |
| TOT-3 | Partition scheme has holes (dateless items invisible to any date bracket) | W | Probe 3 cross-sum | catch-all partition | covered |
| TOT-4 | **Event count ≠ document count**: reconciling on parent entities says nothing about attachment completeness | B1 (2,749 events; documents under agenda items; list-level `document_count` counts top-level only = lies for planning) | partially covered: frontier `leaf_stats` reconciles the walk in its native unit (events); Σ-docs-vs-detail closes at discovery. Fetch-phase child-completeness control still open | ledger edges + per-parent child counts | partial (frontier seam landed 2026-07-23) |
| TOT-5 | **An aggregator's totals are its own harvest, and it is weak at BOTH ENDS — the recent window and the early archive.** Reconciling an aggregator walk closes perfectly while the corpus is materially incomplete against the publisher: the arithmetic cannot see a party it never queries. Freshness does not imply coverage — an aggregator can be current to within days and still start years after the publisher's archive | Vlaardingen 2026-07-25, two probes, both **free and without one request to the disallowing host**. Recent window (`../experiments/stage_b/probes/vlaardingen_raad/ori_coverage_crosscheck.json`): of 89 already-ledgered Notubiz documents ORI carried **59**, and **28 of the 30 absentees were dated INSIDE ORI's covered window**; URN equality **59/59** on the ones it does carry. Per era (`…/ori_era_coverage.json`, provider side = the 785 era-stratified documents the battery had already harvested): **693/785 = 88.3%**, but **2008 = 0/28 and 2012 = 0/58** against 95.6–100% for 2014–2026. ORI's oldest Vlaardingen item is 2010-01-07 with **2011–2012 empty** and ~1,400/yr only from 2014; the uncovered documents are substantive (max 1.77 MB / 13.1 MB). **A 2010–2013 spot walk finding 401/401 with page text was previously cited here as archival reassurance — it measured text presence among items ORI CARRIES, not coverage, and is withdrawn.** Per-municipality, not universal: Haarlem's ORI archive starts 2014 and its provider corpus has no pre-2014 documents (DRIFT-2), so the same cutoff costs it nothing | cross-check the **ledger** against the aggregator, and where an era-stratified probe sample exists use it for per-era coverage — both are free and provider-free. Mechanically, probe 0's aggregator step aggregates **`min` and `max` of the freshness field plus `items_per_year`**, so a sparse head cannot read as archive depth (`engine/probe.py::probe_aggregator`; a start date alone would have read ORI's 2010-01-07 as six years it does not have). Otherwise a sampled provider check, itself a POL-1 exposure decision (`aggregator-registry.md` §7 item 3) | `discovery.completeness_claim: aggregator` declared **per role**; rendered in W5 as "aggregator-complete — publisher coverage unverified"; **split the manifest by role** — provider for the pre-cutoff archive AND the incremental poll, aggregator for the era it actually covers. Consequence for POL-1: the aggregator route **shrinks** a disallowing host's exposure (~85% for Vlaardingen), it does not remove it, and the pre-cutoff era is a **discovery** gap, so "discover all, process scoped" cannot be honoured without the provider | **OPEN** — measured at both ends; archive-depth detection BUILT 2026-07-25 (3 tests) + a fourth `aggregator_provenance` obligation naming the span; `completeness_claim` and the role split not yet implemented (owed at `aggregator-registry.md` §8 step 4) |

## Category VOL — volatility & retention

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| VOL-1 | Source evaporates items after a window (6-week TTL) — missed = lost forever | V | Probe 9 | volatility class drives cadence; blob retention inverted | covered |
| VOL-2 | Old items silently de-listed while files still served (discovery ≠ availability) | A (0/93 old UUIDs listed; files still at /file) | Probe 9 old-URL sampling | import path; never "fix" discovery to find them | covered |
| VOL-3 | **Test-slice resident-disk ratio doesn't extrapolate**: delete-after-distill keeps exactly the *pathological* blobs (scanned partials, zip dossiers), which are systematically larger than the median doc. Haarlem test slice measured 3% kept; the 1,000-doc scale run measured **12.9% by count but 34% by bytes** (131 kept / 871 MB of 2,572 MB fetched). Slice-derived resident projections can understate ~10× | B1 (scale run 12, 2026-07-23) | report kept-ratio *by bytes* in every run report, not just by count | size resident projections from kept-bytes ratio at ≥1k-doc scale; treat small-slice ratios as lower bounds | DETECTION-GAP (run report lacks kept-bytes line) |

### VOL-3 verdict — does the OCR-tail drain delete blobs after processing? (2026-07-23)

**No, because the drain does not exist yet.** `deferred_queue` has exactly one writer
(`engine/process.py::_defer`) and **zero readers** anywhere in the repo; nothing ever
sets `resolved_at`, and the CLI exposes no drain/OCR-burst phase (`civic run` phases
are discover/fetch/extract only). So today `partial_ocr` blobs are retained
indefinitely — the resident-disk projection for any full backfill is the
**no-reclamation path** until a drain lands.

Measured state (ledger vs disk, 2026-07-23):

- Open `ocr_tail` queue: **166 items, 1,602,501,435 B kept** — haarlem_raad 114
  (913,454,946 B), woo_besluiten 36 (677,343,232 B; the prior handoff's "46 Stage A"
  was stale — actual 36), vlaardingen_raad 16 (11,703,257 B).
- Open `zip_dossier` queue: 27 items, 529,729,608 B kept. **RESOLVED 2026-07-24** via
  `civic resolve-archives` (`engine/archive_resolve.py`): 17 were OOXML office docs
  (mis-sniffed as zip), 10 were true PDF archives fanned out to 198 child members (150
  extracted, 48 moved to the `ocr_tail` queue). Container zips deleted-after-distill.
  DETECTION-GAP recorded there: a fanned-out member has no `blobs` row (never fetched), so
  a scanned member deferred to `ocr_tail` is undrainable unless a synthetic kept-blob row
  is written — the resolver now does so (`_keep_member_blob`).
- Cross-check: ledger `blobs WHERE kept` = **193 blobs / 2,132,231,043 B**; on-disk
  `data/blobs/` = **193 files / 2,132,231,043 B** — byte-for-byte match. The ledger's
  kept-accounting is trustworthy for resident projections.

Consequences:

- **Haarlem resume UNBLOCKED.** No-reclamation resident cost for the remaining ~10.6k
  docs ≈ 871 MB kept per 1k docs (the 34%-by-bytes ratio) ≈ **9–10 GB additional**.
  Host fs re-measured today at **60% used / 228 GiB free** — the 90%-watermark
  condition in the 2026-07-23 handoff has cleared (≈185 GB was freed between
  sessions), and the 92% create-index block is ~189 GB away. Disk does not gate the
  resume anymore, drain or no drain.
- **Drain-implementation task opened** (Stage A leftover, overlaps this entry): the
  burst drain per `ocr-strategy.md` must, on successful tail OCR, apply the standard
  delete-after-distill rule — unlink the blob, set `kept=false,
  kept_reason='deleted_after_distill', storage_ref=NULL`, and stamp
  `deferred_queue.resolved_at`. Until then, expect kept-bytes to grow monotonically
  at the VOL-3 ratio.

## Category ACC — access & coverage

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| ACC-1 | Whole modules/categories invisible without auth ("indexed Amsterdam" = ⅓ of Amsterdam) | G | Probe 10 enumerate + record forbidden | `known_exclusions` + coverage % in every report | covered |
| ACC-2 | Per-item confidentiality flags inside public listings | B1 (`confidential`, `permission_group` fields on every event) | Probe 10 samples flag distribution | ledger the flag; never fetch confidential items | covered (new, 2026-07-23) |

## Category POL — access policy & compliance posture

*New category (2026-07-25). Justified under rule 4 because every other category records a
way the source can mislead the **engine**; this one records a way the project can be wrong
about its **right to harvest** — a different axis, and one that gates Stage C rather than
correctness.*

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| POL-1 | A harvested host serves `robots.txt: Disallow: /` and **nothing records it**. As found, `engine/probe.py:288-301` fetched robots.txt but extracted only `Sitemap:` directives; it never read `Disallow`/`Crawl-delay` (fixed 2026-07-25 — see Status). `onboarding-flow.md` §Phase-1 *does* require "Policy notes: robots directives, ToS pointers" — so the obligation existed in the prompt the whole time and was silently skipped, because no verb carries it, no manifest field holds it, and no validation checks it | B3 Vlaardingen re-onboard (2026-07-25) found it on `api.notubiz.nl`; follow-up check found it is **fleet-wide — 4/4 sources**: `open.overheid.nl` (woo_besluiten), `repository.overheid.nl` (tuchtrecht SRU), `api.notubiz.nl` (haarlem_raad + vlaardingen_raad) all serve `Disallow: /`. No manifest had any `policy` key before this date | DETECTION-GAP → probe 0 parses `Disallow`/`Crawl-delay` per host and emits `policy_flags`; manifest schema gains a `policy` block; `validate_manifest` refuses to leave test mode when a harvested host disallows and no policy is declared | **fail-closed, not advisory** — the posture is an admin gate decision (`policy.admin_decision`), never agent-resolved. First instance drafted in `manifests/vlaardingen_raad.yaml` (`admin_decision: pending_gate_1`) with mitigating/aggravating context separated | **DETECTED, NOT YET GATED** (evidenced 2026-07-25; blocks Stage C). Landed 2026-07-25: `engine.probe.probe_robots`/`parse_robots` do RFC 9309 group selection and every report carries `policy_flags` (per-host `disallow`/`allow`/`crawl_delay`/`blanket_disallow`), and all 4 manifests now hold a `policy` block at `pending_gate_1`. Recording is deliberately **decoupled from `enforceable`** — a Disallow does not make the surface evidence incomplete. **Still owed:** the `validate_manifest` fail-closed rule and the `onboarding-flow.md` Phase-1 wording, which land together **after** the posture decision (turning the gate on first wedges 4/4 sources). **Decision memo written 2026-07-25 — `pol1-posture-decision.md` (internal)** (three hosts with genuinely unequal cases; four options; a NO-GO *also* satisfies exit criterion #7, so Stage C is blocked by silence rather than by a "no"), with three consent-bearing asks drafted and unsent in [`../outreach/`](../outreach/) |

## Category PROJ — projection & facet liveness

*New category (2026-07-25). The whole 13-probe battery tests the **source** side; this
category exists because a manifest can pass every source-side probe and still ship a
search UI with dead facets. Probe 5 tests filter vocabulary **at the source**, which is a
different question from "does the declared projection path populate in the index".*

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| PROJ-1 | A declared `index.projection` path is **permanently null**, so a declared facet is empty for 100% of documents — invisible to source-side probing, and inheritable: it propagated from one manifest to another via the archetype copy | B3 (2026-07-25), confirmed by direct code read: `engine/strategies/notubiz_events.py:218` hard-codes `"gremium_name": None` with the comment *"gremia lookup joined at enrichment time"*, but **no enrichment stage exists** (`enriched` appears only as a stage rank in `envelope.py:9` and a SQL filter in `project/projector.py:47`). Both Notubiz manifests declared `publisher: meeting_context.gremium_name` **and** listed `publisher` in `index.facets` → dead facet in both. Survived a full prior onboarding at 0 logged interventions | DETECTION-GAP → **projection-liveness probe**: assert every `index.projection` path and every `index.facets` field is non-null for a minimum share of the test slice, run against the Phase-5 limited test run | **INSTANCE FIXED 2026-07-25** as predicted — cheap because the id is captured. `enrichment.lookup_tables.<name>` gained `from`/`to`/`values` and `engine/project/projector.py:lookups_of` joins id→label at **projection time** (existing hook, no new stage); the strategy stopped emitting the null `gremium_name` key; `validate()` now rejects a field owned by both a payload path and a lookup — the ambiguity that produced this. Measured before/after on the live index: publisher non-null **0/24 → 24/24** (Vlaardingen) and **0/1017 → 1017/1017** (Haarlem). | **INSTANCE FIXED, GAP OPEN** (2026-07-25) |

| PROJ-2 | **The live index silently stops matching the declared mapping.** `os_client.ensure_index` creates an index only when it is *absent*, so a field ADDED to `mappings.py` never reaches indices that already exist; the first document carrying it is mapped **dynamically**, and a string becomes `text` rather than `keyword`. Aggregations and term filters on that field then fail — and inside a cross-corpus union that failure is a *partial shard success*, i.e. **HTTP 200 with a whole source missing** | Measured 2026-07-28 on all four live indices while building cross-corpus search: `exposure`, declared `keyword` on 2026-07-27, was `text` on `civic-{woo_besluiten,tuchtrecht,haarlem_raad}`; `processors` was absent from three. **Enforcement was never affected** — `engine/exposure.py` reads both from `_source`, so no document was ever over-served — but the schema was a lie and the first cross-corpus aggregation failed on 3/4 shards | **`uv run civic check-projection`** (`engine/project/verify.py`): every live `civic-*` mapping vs the declared one, distinguishing `wrong_type` (queries misbehave now) / `undeclared` (typed by guess) / `unmaterialised` (declared, never written — harmless today, a `wrong_type` awaiting its first write). Non-zero exit on the first two. Plus `incomplete` in the API response, so a union that lost a shard says so | **FIXED 2026-07-28.** All four sources re-projected (`--recreate`; the mapping is applied only at creation). Costs no crawl and no GPU — Postgres holds text and vectors — and `woo_besluiten`'s suppressed URN stayed suppressed through the rebuild. 8/8 indices now clean | **FIXED + CONTROL BUILT** (2026-07-28) |
| PROJ-3 | **Facets vanish exactly when the dense channel is doing the work.** Hybrid mode computed its facet counts from the **BM25 leg alone** (`_facets(bm25_resp)`), so a query the dense channel answers and BM25 does not returned 100 results with *every facet empty* — including the source facet, i.e. the corpus switcher disappeared at the moment cross-corpus retrieval is most useful | Measured 2026-07-28, `q=tandartsklacht`: BM25 `total` 0, hybrid `total` 100, all five facets `[]`. **Pre-dates cross-corpus search** (`ui/search_api.py:454` at `6a02f32`); it became visible because the source facet is new. Same query class as W4's own finding that hybrid resolves Dutch compounds BM25 cannot | `tests/test_facet_scope.py` pins the defect itself — BM25 total 0, results non-empty, facets non-empty — plus the bm25/hybrid split, so a future change cannot quietly empty one mode's panel again. Still worth folding the general form (facets non-empty whenever `results` is) into the W6.5 projection-liveness probe | **RULED AND FIXED 2026-07-28: pool-scoped, labelled.** Hybrid facets are now tallied over the fused pool (`_pool_facets`), not the BM25 leg, and the response carries `facets_scope: "pool"` which the UI states in words. The ruling is the *more* honest of the two, not merely the more convenient: hybrid pagination was **already** pool-bounded (`min(total, pooled)`), so the old global counts promised a narrowing the result list could never deliver. BM25 mode is untouched — its aggregations are exhaustive, `facets_scope` is absent, and the `understand=0` response shape is byte-identical. The cost is real and stays visible: these are "within the top N" counts and the panel says so | **FIXED** (2026-07-28) |

## Category DRIFT — schema drift over archive age

| ID | Failure mode | Evidence | Detect | Mitigation | Status |
|---|---|---|---|---|---|
| DRIFT-1 | Fields exist only since year X (e.g. `terinzageleggingBG` ≥2024) | V | Probe 12 field-presence matrix over eras | per-era notes; partial indexing plan | covered |
| DRIFT-2 | Old-era parents have zero children where new ones have many — looks like extraction failure but is source reality | B1 **evidenced**: 2008+2012 meetings 0 docs (4/4), 2016+ meetings 25–70 docs | Probe 12 extension: child-count-by-era histogram (ran ad-hoc for Haarlem; not yet a standing battery step) | era-aware health thresholds; manifest schema_drift notes | partial (evidenced; battery step pending) |

---

## Harness backlog (derived from DETECTION-GAPs above)

1. **Minimal-request escalation probe** (API-2) — codify bare→+params unlock ladder as
   battery step; output = required-params set for the manifest.
2. **Sibling-host enumeration** (PLAT-3) — fingerprint phase greps outbound same-org
   links from the entry page; each candidate host gets its own mini-fingerprint.
3. **Per-walk wall-clock deadline** (NET-1) — engine invariant next to per-request
   timeout; a probe/walk that exceeds N× expected duration aborts with a distinct
   status (never hangs a run).
4. **Child-completeness control** (TOT-4) — reconciliation extension for parent→child
   sources: Σ fetched children vs Σ declared `document_count`/`agenda_item_count`.
5. **Child-count-by-era histogram** (DRIFT-2) — cheap addition to probe 12.
6. **Kept-bytes line in run report** (VOL-3) — report blob retention by bytes (not just
   count) per run, so resident-disk projections come from the right ratio.
7. **robots/policy parse + fail-closed validation** (POL-1) — probe 0 parses
   `Disallow`/`Crawl-delay` per host → `policy_flags`; manifest `policy` block;
   `validate_manifest` blocks test-mode exit when a harvested host disallows and no
   policy is declared. **Two-part: the mechanism is engine work, the posture is an admin
   decision** — landing the fail-closed half before the posture exists would wedge all
   four sources at once. **Half done 2026-07-25:** parse + `policy_flags` + all four
   `policy` blocks are in; the `validate_manifest` rule waits on the posture decision.
8. **Aggregator-registry step in probe 0** (SURF-3) — keyed by corpus type, not provider
   host; ORI/openbesluitvorming, data.overheid.nl, CKAN federations. Each entry probed
   and recorded verified-absent like any other surface. **Designed 2026-07-25 —
   [`aggregator-registry.md`](aggregator-registry.md); build order in its §8.**
9. **Projection-liveness probe** (PROJ-1) — assert declared `index.projection` paths and
   `index.facets` fields populate for a minimum share of the Phase-5 test slice. Note
   this is the register's first **index-side** probe; the battery is otherwise entirely
   source-side, so it likely needs its own hook rather than a slot in the 13.
   **Still owed after the 2026-07-25 fix.** Repairing the one dead facet does not close
   this: the defect was never the null field, it was that *nothing looked*. The instance
   survived a full onboarding at 0 logged interventions and propagated to a second
   manifest by archetype copy before a human read the code. Two manifests are now pinned
   by `tests/test_project.py`, which is a regression guard for the known case — not a
   probe that would catch the next one. Do not let "publisher works now" retire this item.

Fixture-based regression (the Stage A method) remains the harness *substrate*: every
failure mode that reaches `covered` should ideally have a fixture that reproduces it —
recorded probe responses land in `../experiments/stage_b/probes/` and graduate into
`tests/fixtures/` when a strategy/validation rule consumes them.
