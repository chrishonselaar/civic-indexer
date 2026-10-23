# Onboarding flow — how the agent takes a source from URL to production manifest

**What this is.** The concept draft of the admin-facing flow from `idee.md` ("gebruiker
geeft een site door … AI agent stelt een plan op, legt uit, testrun, tweede verificatie,
schema + limieten"), made concrete against the manifest DSL (`decision-inventory.md`,
`src-manifest-example.md`) and the envelope/run model (`item-envelope.md`). The
centerpiece is the **stress-test battery** (§3): the four projects' war stories converted
into standing probes that run *before* a manifest can be accepted.

Design stance: the *agent* is expensive and rare (onboarding, healing); the *engine* is
deterministic and cheap (every run). Everything the agent learns is written into the
manifest — including negative results — so nothing has to be rediscovered.

Single-admin for now (per earlier decision); gates are lightweight approvals, not
multi-tenant workflow.

---

## Phase 0 — Intake (minimal and iterative)

The only hard requirement: **a URL and one sentence of purpose.** Everything else is a
*draft the agent produces and the admin corrects*, not a form to fill in upfront —
because most intake answers can't realistically be known before the data has been
explored:

- **`admin_intent`** starts as that one sentence. After reconnaissance (Phase 1) the
  agent expands it into a structured draft — "here's what this source contains, here's
  what I think you want findable and how you'd search it" — which the admin edits at
  gate 1 and again at gate 2 when real extracted records make the intent concrete. It is
  a **living field**, revisable whenever purpose shifts, and it remains the standing
  instruction future healing runs re-read to stay aligned with purpose, not mechanics.
- **Bounds are outputs, not inputs.** Budget ceilings, archive depth, and scope are
  proposed *by the agent with evidence attached* (probe 8's cost projection, probe 9's
  volatility, the volume estimate) — asking an admin for a budget before anyone knows
  the scan rate is asking them to guess. The admin's job at the gates is to say yes/no/
  adjust to concrete, priced proposals.
- **Initial scope** — including *thematic* scope ("start with everything touching
  animal welfare") — is elicited at gate 1 with the agent's corpus summary in hand:
  "~14k items; your theme matches an estimated ~900 of them; indexing theme-first costs
  €2 and is live tomorrow, the rest can follow later." See `thematic-scoping.md`.

Known quirks volunteered upfront ("the old PDFs are scans", "there's an API somewhere")
are welcome hints, stored and checked — never required.

## Phase 1 — Fingerprint & reconnaissance (read-only, polite)

Goal: classify the source before proposing anything.

1. **Cheap surface scan:** `robots.txt`, sitemaps, RSS/Atom feeds, `noindex` patterns,
   visible bulk-download or open-data links.
2. **Platform signature matching** against the archetype library (Notubiz, iBabs,
   GO/OpenGov raadsinformatie, Grexx Atalanta, CKAN, SRU/KOOP, open.overheid repository
   API, ASP.NET/Umbraco form search, ArcGIS/WFS, …). A match instantiates a template
   manifest with only per-site parameters left to probe — the 100×-leverage path.
3. **Watch the site's own frontend:** load the search UI, capture the XHR/fetch calls it
   makes. *The site's own frontend always knows the best API* (the rijksoverheid
   HTML→SPA→JSON-API lesson). A discovered private JSON API outranks HTML scraping.
4. **Rank candidate discovery surfaces** (best-first): bulk/feed (BUS-style) →
   structured API (SRU/CKAN/JSON) → paged search → grid enumeration → link-walk →
   headless browser (declared last resort; all nine sources examined so far never
   needed it). **This ranking is enforced by probe 0 (below), not left to judgement** —
   tuchtrecht was scraped when an SRU bulk endpoint existed on the same provider because
   no active probe tested for it (`bulk-endpoint-discovery.md`).
5. **Policy notes:** robots directives, ToS pointers, personal-data smell (names, BSNs,
   medical/juridical content), source's own takedown/rectification mechanism. Recorded
   in a manifest `policy:` block for the admin to see at the plan gate — the AVG
   question is surfaced *per source, at onboarding*, not discovered in production.

Output: a **source dossier** — classification, candidate strategies ranked, risks. If
fingerprinting already fails (login wall, aggressive anti-bot), the flow stops here with
a clear "what would be needed" report instead of a doomed manifest.

## Phase 2 — Interactive scoping (first conversation)

The agent proposes, the admin decides — only genuine trade-offs are asked:

- discovery surface choice (if two rank closely: "official API is cleaner but misses
  attachments; HTML route sees everything — which matters more?"),
- archive depth and scope filters,
- freshness vs completeness (BUS-style one-day-lag questions),
- budget order-of-magnitude before any projection exists ("is €50 backfill acceptable
  if OCR turns out to be needed?").

## Phase 3 — The stress-test battery

Every probe is a small, polite, bounded interaction with the live source. Results are
written into `onboarding_probes:` in the manifest, **dated**, with rejected alternatives
recorded. A manifest with open `TODO(probe)` markers cannot leave test mode (engine
validation rule — `src-manifest-example.md` friction #7).

| # | Probe | Method | Pass condition / output | Earned by |
|---|---|---|---|---|
| 0 | **Bulk surface** | Actively test the provider host(s): SRU `?operation=explain`; OAI-PMH `?verb=Identify`; sitemap / `robots.txt` Sitemap: / `.tar.gz`/`.zip` open-data links; CKAN `/api/3/`; KOOP BUS feed; frontend XHR. For any hit, check whether it carries full content or metadata-only. | A ranked surface list with the best-first winner **and** verified-absent results recorded; if a bulk surface answers, a lower surface may be chosen only with a recorded reason. **Fail-closed:** skipping probe 0 blocks leaving test mode. | tuchtrecht — SRU (48 req, <1 min) missed; scraped instead (13 h @ 1 rps). See `bulk-endpoint-discovery.md` |
| 1 | **Result cap** | Request page `cap+1` on a wide query; request an oversized date range; compare behavior | Cap found → partition spec sized under it; no cap → recorded as verified-absent | rijksoverheid 500, Notubiz 200, RCC depth, SRU 4200 — **5/5 sources had one** |
| 2 | **Claimed total** | Locate a total (rendered count, `total_items`, `numberOfRecords`); cross-check against a hand-countable slice | A trusted total extractor, **or** `claimed_total: none` + mandatory compensating controls | W's Dutch-locale count string; S's meaningless header |
| 3 | **Partition holes** | Σ(per-month claimed counts) vs overall claimed count on a sample year | Match, or a catch-all partition declared | W's dateless documents invisible to any date bracket |
| 4 | **Key uniqueness & stability** | Sample ≥ 500 items; test each candidate natural key for uniqueness; re-fetch a sample later — key unchanged? | One accepted key; **rejected candidates recorded with reasons** | S's dossiernummer reused by CVB appeals |
| 5 | **Filter vocabulary** | For each filter param the manifest will use: verify it returns non-zero *and different* results | Working param set; broken vocab documented | W's TOOI-URI filters silently returning 0 |
| 6 | **Pagination truth** | Walk 3+ pages twice: duplicates across pages? ordering stable? sentinel reliable? last-page behavior | Termination condition proven on live data | V's cursor-vs-offset lesson; G's `has_more_pages` warning-bug |
| 7 | **Content type & size** | Download ~20 payloads: magic bytes vs extension vs Content-Type; `Content-Length` present? size distribution | Format-router config; streaming-cap setting; hidden-ZIP flag | W's extensionless ZIPs; G's opaque `/download?id=` endpoints |
| 8 | **Cost prediction** | Page counts available pre-download? Sample native-text confidence on ~20 PDFs → OCR-need rate | Backfill + daily cost projection (EUR, storage) with stated confidence | W's `aantalPaginas` luxury vs offline predictor |
| 9 | **Volatility** | Probe old items' URLs (1, 5, 15 years back); look for stated retention windows | Volatility class: persistent / TTL(window) / living | V's 6-week evaporation — miss = permanent loss |
| 10 | **Access coverage** | Enumerate the full module/category space; record forbidden or empty sections | `known_exclusions:` list; coverage % estimate | G's "indexed Amsterdam" = a third of Amsterdam |
| 11 | **Politeness ceiling** | Brief probe at modest concurrency; watch for 429s / refusals / degradation | delay + concurrency settings with headroom | small public-sector boxes everywhere; BUS "server busy" refusals |
| 12 | **Schema drift** | Sample items across the archive's full age range; field-presence matrix over time | Per-era field availability; partial-indexing plan for old eras | V's `terinzageleggingBG` existing only since ~2024 |
| 13 | **Relationships** | Sample detail pages: attachments, siblings, appeal/version markers; test candidate grouping keys on samples | Proposed edge types + child-expansion strategy + grouping-key ladder | W's sibling-URL bijlagen; S's CVB stems; V's aanvraag/besluit pairs |

Battery output feeds directly into manifest sections: probe 1→`partition`, 2→
`claimed_total`, 4→`identity`, 5→request params, 6→`termination`, 7→format router +
caps, 8→`budgets`, 9→`volatility`, 10→`known_exclusions`, 12→per-era notes, 13→
`relationships`.

## Phase 4 — Draft manifest + plan explanation (verification gate 1)

The agent presents a **human-readable plan**, not YAML: what the source is, which route
was chosen and why (including rejected alternatives), identity choice, partition plan,
proposed edges, cost projection (backfill and daily, with confidence), storage
projection, coverage caveats, policy flags. The full manifest is attached for
inspection.

Admin approves, adjusts, or redirects. Nothing has been crawled at scale yet; the total
spend so far is the battery's few hundred polite requests.

## Phase 5 — Limited test run

- **N ≈ 20–50 items end-to-end** through the *real engine* into a test index — same
  code path as production, no special test logic (idee.md's "snelle beperkte testrun").
- Every fetched page/response is captured as a **fixture** — these seed the permanent
  regression suite that later self-healing runs test patches against.
- **Facet discovery:** the agent samples ~200 discovered records (metadata + extracted
  text where cheap), proposes facets with coverage % and cardinality, and generates the
  deterministic TBL rules (the SRC-enricher pattern: LLM writes the rules once, runtime
  stays free). Weak facets (<30 % coverage, unbounded cardinality) are proposed as
  *excluded*, with reasons.
- A **mini run-report** is produced with the same schema as production: claimed vs
  harvested on the test slice, selector health, per-item status, cost actuals vs
  projection.

## Phase 6 — Result verification (verification gate 2)

The admin sees the test slice **as search results** — rendered records with extracted
fields, proposed facets populated, provenance links back to the live source — plus the
mini run-report. This is deliberately the *user's* view, not the engineer's: wrong
extractions are obvious when you see "Datum: Aanbeveling" in a result card.

Corrections loop **against cached fixtures** (no re-crawling): admin flags a bad field →
agent revises the EXPR/TBL → regression-runs against fixtures → re-renders. Iterate
until approved. Cost of iteration ≈ zero live requests — the raw-artifact-as-ground-truth
principle doing its onboarding job.

## Phase 7 — Production configuration (verification gate 3)

Interactive, per idee.md: schedule (cadence per volatility class), budget caps (EUR per
run / per month, page caps, storage cap), backfill plan — possibly **phased** (newest
year first, then budgeted tranches backward; the deferred bucket holds the rest with
reasons), alert thresholds for the monitoring digest.

Engine acceptance validation (hard gates, not agent judgment):
- manifest schema-valid; **zero open `TODO(probe)`**;
- `claimed_total: none` ⇒ compensating controls present;
- fixtures registered; health checks defined;
- budgets set (absent budget ≠ unlimited budget — absent budget is invalid).

## Phase 8 — Backfill & handover to operations

- Staged backfill with **reconciliation checkpoints** per tranche; a `NOT_reconciled`
  verdict pauses the backfill rather than burying the gap.
- Final backfill report: coverage vs source-claimed totals, deferred cohort (with
  reasons and projected cost to clear), dangling-edge counts by scheme, actual vs
  projected cost.
- Steady state: incremental runs per schedule; the **monitoring digest** (cheap LLM over
  run reports) watches for `NOT_reconciled`, zero-match selectors, budget-burn
  anomalies, deferred-bucket growth, and drift in new-items-per-day — and hands
  failures to the **self-healing loop**, whose patch autonomy follows the blast-radius
  tiers (`decision-inventory.md` §16).

---

## The through-line

Each verification gate consumes exactly what the previous phase produced, and everything
flows into two artifacts: the **manifest** (all decisions + probe results + negative
knowledge) and the **fixture set** (ground truth for zero-cost iteration and future
self-healing). The battery is the heart of it: five sources in, every single one hid a
silent cap, and most hid a second trap besides — the flow's job is to make those
*findings by procedure* instead of *discoveries by outage*.
