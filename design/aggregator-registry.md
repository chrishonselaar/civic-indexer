# Aggregator registry — probe 0's blind spot (SURF-3), design doc

**Status: design + steps 1–3 BUILT, 2026-07-25. W6.5 item 2. §5 and §7 item 3 CORRECTED the
same day — if you are quoting this doc on POL-1, read §5 first.** Probe 0 is **host-scoped**: it tests
SRU/OAI/CKAN/sitemap/XHR on the *provider's own* hosts. A third-party aggregator that
republishes the same corpus is therefore **structurally invisible** to it — the verdict
`no_bulk_surface` comes back correct about the hosts probed and wrong in substance. This
doc specifies the fix (an aggregator-registry step keyed by **corpus type**, not provider
host), and records the live measurements that motivate it.

Two things this doc is *not*. It is not a proposal to make probe 0 crawl the open web
looking for copies — the registry is a curated, hand-maintained list, and each entry is
probed and recorded verified-absent exactly like any other surface (rule: negative
knowledge is kept). And it is not only a cost optimisation: for the two Notubiz sources it
is the route that **shrinks the disallowing host's part in the POL-1 posture decision by
roughly 85%** — measured, §5. An earlier version of this sentence said *removes … altogether*;
that was wrong in a way that mattered, and §5 records why.

## 1. The evidence (B3 Vlaardingen re-onboard, 2026-07-25)

The first SDK-driven live onboarding ran probe 0 against `vlaardingen.raadsinformatie.nl`
and `api.notubiz.nl`, got `no_bulk_surface, recommended=null` — and then the *agent*, not
the probe, found Open Raadsinformatie by reading around the problem. That gap is SURF-1
("a bulk endpoint exists but is unprobed, so a slow/fragile surface gets chosen")
recurring one level up: aggregator instead of provider endpoint.

## 2. What ORI actually carries — measured live 2026-07-25

Endpoint `https://api.openraadsinformatie.nl/v1/elastic` (Elasticsearch read API). Run by
**Open State Foundation + VNG Realisatie + Ontola**; **Notubiz is one of six council-
information suppliers that actively feed it**, so this is the vendor's own reuse channel,
not a scrape of it.

| Measurement | Vlaardingen | Haarlem |
|---|---|---|
| `MediaObject` (documents) | 16,877 | 71,062 |
| …with extracted `text` | **16,583** | **70,160** |
| PDFs | 16,583 | 70,163 |
| Full-text coverage of PDFs | **100%** | **99.996%** |
| Newest meeting | **2026-07-09** | **2026-07-02** |
| Other types | AgendaItem 7,065 · Meeting 926 · Organization 34 | AgendaItem 39,262 · Meeting 2,432 · Organization 36 |

Fleet-wide: **310 `ori_*` indices** — Amsterdam 2.68M, Haarlem 904k, Utrecht 682k,
Tilburg 575k, Den Haag 574k … i.e. the registry entry is reusable across essentially every
future Dutch municipality onboarding, not just our two. That is the 100×-leverage argument
from `bulk-endpoint-discovery.md` §Reusability, applied to aggregators.

**The `MediaObject` record shape** (sampled live):

```
@id                4723700
@type              MediaObject
content_type       application/pdf
file_name          Agenda_Gemeenteraad_20141028.pdf
name               printversie agenda
size_in_bytes      25160
last_discussed_at  2014-10-28T20:00:00+01:00
is_referenced_by   4723699                → AgendaItem
has_organization_name 4721010             → Organization (the gremium — see §6)
original_url       https://api.notubiz.nl/document/1680571/1     ← THE JOIN KEY
url                https://api.openraadsinformatie.nl/v1/resolve/api.notubiz.nl/document/1680571
text               ["…", …]   ONE ENTRY PER PAGE — a list, not a flat string (corrected)
md_text            ["…", …]   likewise; markdown flavour of the same pages
text_pages         [{page_number: 1, text: "…"}, …]
was_generated_by   {app_semver, ended_at_time, reference_identifier, …}   ← provenance
```

Three consequences, each load-bearing:

1. **`original_url` is exactly the natural key `manifests/vlaardingen_raad.yaml` already
   uses** (`api.notubiz.nl/document/{id}/{version}`). The join is free; no identity
   re-derivation, no new key rejected-list entry.
2. **`text_pages[{page_number, text}]` is the shape our OCR tail emits.** ORI has already
   paid the OCR cost — 100% of Vlaardingen's PDFs carry text. This is where the W1 saving
   comes from, and it is larger than "cut some OCR spend": it is the whole tail for these
   two sources.
3. **`/v1/resolve/…` serves the original bytes.** Verified: `HTTP 200`,
   `content_type: application/pdf`, 27,590 bytes. So discovery, metadata, full text **and**
   the source PDF all come from ORI — **for the items ORI carries.** That qualifier is the
   whole of §5: this doc first said "the pipeline never contacts `api.notubiz.nl`", which is
   true of a walk over ORI's corpus and false of a source that must cover 2008-2013 and the
   current fortnight.

**Archive depth, measured 2026-07-25 (and the counts above do not show it):**

| | Vlaardingen | Haarlem |
|---|---|---|
| ORI's oldest item | **2010-01-07** | 2014-01-09 |
| shape of the head | 2010: **133** · 2011-12: **0** · 2013: 606 · then ~1,000-1,650/yr | dense at once (2014: 4,613) |
| provider has pre-2014 documents? | **yes** (verified substantive: 2008 = 23 PDFs + 5 msword, max 1.77 MB; 2012 = 58 PDFs, max 13.1 MB) | **no** — `haarlem_raad.yaml` already records DRIFT-2: 2008/2012 meetings carry 0 documents |
| cost of the cutoff | **~2,000-3,000 documents ORI cannot serve** | ~nil |

The same aggregator therefore has a *materially different* coverage cost per municipality,
which is why archive depth is probed **per source** rather than declared once in the registry.
Note also that a start date alone still lies: read "2010" as Vlaardingen's archive start and
you claim six years ORI does not have. `items_per_year` is what exposes a sparse head, so the
probe reports the whole histogram (`engine/probe.py::probe_aggregator`).

`https://api.openraadsinformatie.nl/robots.txt` → **HTTP 404**: a verified absence,
nothing disallowed (same vocabulary as `probe_robots` already records for
`tuchtrecht.overheid.nl`).

### Negative knowledge — traps found while probing

- **The date in the index NAME is a reindex stamp, not a data cutoff.**
  `ori_vlaardingen_20250514092617` looks 14 months stale; its newest meeting is 2026-07-09.
  Reading the name as freshness would wrongly disqualify the whole route. Freshness must be
  measured by aggregating over the item type, and the aggregator probe does exactly that.
- **The freshness field is `last_discussed_at`, not `start_date`.** Found while building
  step 1: `start_date` lives on `Meeting`/`AgendaItem` and aggregates to **null** over
  `MediaObject` — a probe using it would report "no freshness signal" on a corpus current to
  within two weeks. (`date_modified` is ORI's harvest recency, a different question:
  2026-07-10 vs the civic-time 2026-07-09.) This doc said `start_date` when first written;
  corrected against the live index.
- **Freshness is not archive depth, and an archive START is not coverage either.** The probe
  first measured only `max(freshness_field)`, which recommended ORI for a source whose
  declared range begins in 2008 while ORI holds nothing before 2010 and effectively nothing
  before 2014. Worse, the start date *alone* still misleads: 2010 holds 133 items and
  2011–2012 hold **zero**, so "oldest: 2010-01-07" claims six years that are not there. Both
  ends **and** `items_per_year` are now aggregated in the one probe request.
- **"N/N carry text" is not a coverage measurement.** A walk over the aggregator can only
  tell you about items the aggregator *has*; asking it whether it is missing anything is
  circular. This trap cost a wrong conclusion in this very doc (§7 item 3): a 401/401
  text-presence figure was read as archival reassurance for two days. Coverage requires a
  **provider-side** reference set — and one is usually free, because the ledger and the
  onboarding battery's own samples are provider harvests already on disk.
- **ES queries go through the existing GET-only `HttpGet` contract** via `source=` +
  `source_content_type=` (verified live). No contract widening was needed — worth knowing
  before anyone reaches for a POST-capable second transport.
- **`_count` with a request body fails through the ORI proxy** (returns non-JSON). Use
  `_search` with `size: 0, track_total_hits: true`. A probe that treats the failure as
  "no data" would record a false absent.
- **`_cat/indices` doc counts are not document counts.** `ori_haarlem_*` reports 903,693
  there but 112,792 via search — `_cat` counts nested docs. Rank registry hits on searched
  totals, never on `_cat`.
- **`text` and `md_text` are per-page LISTS, not flat strings** — this doc said "flat
  extracted text" when first written; corrected against the live index while building
  step 3. All three text fields carry the whole body once each, so a full `_source` is a
  **triplicate**: ~125 KB mean, 1.6 MB max, and a 1000-hit page weighs 23 MB. Request
  `text_pages` only via `_source.includes` (dotted paths work) and store **one** mapped
  copy — the page model — or the ledger takes ~3× the corpus in `items.payload` jsonb.
- **A `MediaObject` is not always a document.** ~0.7% (7/1000 sampled) are links to meeting
  pages (`*.raadsinformatie.nl/vergadering/…`, including *other* municipalities'). They
  cannot yield a provider document key, so `16,877 MediaObjects` ≠ `16,877 documents`; the
  strategy counts them `unrecordable` rather than dropping them silently.
- **`search_after` on `@id` is the walk, not scroll or PIT.** `@id` is a unique sortable
  keyword, so it gives a stable total order — and unlike scroll/PIT it needs no POST, so
  the walk stays inside the existing GET-only `HttpGet` contract.
- The **MIT licence** on the ORI repo covers the *software*, not the data. The per-
  municipality data licence is still owed (§7).

## 3. Where the step goes in probe 0

A new surface prober, run **after** the per-host battery and **once per source** rather
than once per host — this is the first probe in the battery whose subject is the *corpus*,
not a host:

```
probe_bulk_surfaces(hosts)            # unchanged, host-scoped
  └── probe_aggregators(source_hints) # NEW: registry-driven, corpus-scoped
```

`source_hints` is what the registry matches on — deliberately **not** the provider host,
because matching on `api.notubiz.nl` would have missed the point (ORI covers iBabs, GO,
Parlaeus and Tweede Kamer sources too). The hints are: corpus type
(`council_information`, `open_data`, `official_publications`), the municipality/organisation
name, and optionally a CBS/BAG gemeentecode.

**Rank class.** ORI carries metadata *and* full body text inline, which is strictly richer
than the existing `bulk_metadata` (SRU carries metadata + abstract, body deferred). Rather
than inventing a rank tier per source, add **one** class above the current two:

| rank | class | carries | example |
|---|---|---|---|
| 0 | `bulk_content` | metadata + full text + bytes | ORI MediaObject |
| 1 | `bulk_metadata` | metadata (± abstract), body deferred | SRU, OAI-PMH, CKAN |
| 2 | `bulk_index` | URLs only | sitemap, robots `Sitemap:` |

This is the SURF-2 two-tier content insight made rankable: `bulk_content` is the case where
the two-tier model collapses into one tier because someone else already paid for tier 2.

**Fail-closed behaviour is inherited unchanged.** A registry entry that errors makes the
report `incomplete`; a registry entry that answers cleanly with zero hits is recorded
`absent` (verified: "ORI does not carry this municipality" is knowledge worth keeping, and
it is the *expected* outcome for e.g. `woo_besluiten`).

**A found aggregator must not silently win.** It goes through the same
`engine/onboarding.py::surface_gate()` that already blocks a scraper chosen over a
metadata-bearing bulk surface — with one added owed obligation, `aggregator_provenance`
(§7): an aggregator is a *second-hand* source, and the manifest must record that it is one.

## 4. Registry format

A checked-in YAML file, `registries/aggregators.yaml` — data, not code, so adding an
aggregator is a data change reviewable in a diff (same principle as the vocab lookup
tables: new values are INSERTs, never migrations).

```yaml
- id: open_raadsinformatie
  name: Open Raadsinformatie (Open State Foundation / VNG Realisatie / Ontola)
  corpus_types: [council_information]
  coverage: dutch_municipalities            # 310 indices, fleet-wide
  probe:
    kind: elasticsearch
    base_url: https://api.openraadsinformatie.nl/v1/elastic
    index_pattern: "ori_{slug}_*"           # slug derived from the organisation name
    freshness_field: last_discussed_at      # §2 trap: NEVER the index-name date, and NOT
                                            # start_date (Meeting/AgendaItem only)
    content_type_field: content_type
    item_type_field: "@type"
    item_type_value: MediaObject
  join:
    natural_key_field: original_url         # api.notubiz.nl/document/{id}/{version}
    matches_provider_hosts: [api.notubiz.nl, "*.ibabs.eu", "*.gemeenteoplossingen.nl"]
  carries: [metadata, full_text, page_text, original_bytes]
  bytes_url_field: url                      # /v1/resolve/… proxy
  robots: "no robots.txt (HTTP 404, fetched 2026-07-25) — nothing disallowed"
  license: not_assessed                     # owed — see §7. MIT covers the SOFTWARE only
  consent_posture: republished_for_reuse    # explicit, not inferred
```

`matches_provider_hosts` is a *hint for the report*, not the lookup key: it lets the probe
say "you were about to harvest `api.notubiz.nl`; this aggregator republishes that host's
corpus", which is the sentence the onboarding agent needs. Lookup itself is by
`corpus_types` + name slug.

## 5. What this does for POL-1 — it SHRINKS the question, it does not dissolve it

> **CORRECTED 2026-07-25 (measured, twice). This section said the ORI route *dissolves* the
> posture question for two of four sources. It does not.** The claim rested on §2 point 3 —
> ORI serves discovery, metadata, text and bytes, so "the pipeline never contacts
> `api.notubiz.nl`" — which is true of a walk over *ORI's corpus* and false of a source
> obliged to cover the publisher's. Two measurements closed it: **TOT-5** (recent-window lag,
> §7 item 3) and the **per-era coverage probe** (`ori_era_coverage.json`, §2). The
> overstatement mattered because it was the argument that made this route look like a way
> *around* the admin gate rather than a way to make the gate smaller. Do not let it stand,
> and do not cite the old framing when re-asking.

Why `api.notubiz.nl` is still the hardest host in the decision, unchanged: blanket
`Disallow: /`, no ToS, no documented API, no licence statement, largest source by volume,
**and** a commercial vendor rather than the public body that owns the documents. Every
mitigating argument available for the KOOP hosts is weaker there.

What the route actually buys, for Vlaardingen (the source with both measurements):

| what must come from `api.notubiz.nl` | without ORI | with ORI |
|---|---|---|
| 2014-2026 backfill | ~16,800 documents | **0** — 95.6-100% covered, with text |
| 2008-2013 backfill | ~2,000-3,000 documents | **~2,000-3,000** — ORI carries 0% of this era |
| recent window / incremental | every poll | **every poll** — 28 of 30 recent absentees sat inside ORI's covered window |
| scattered misses in the covered era | — | a small residue (4/91 in 2016, 1/136 in 2018, 1/297 in 2021) |

So: **~85% of the traffic goes away, and the host does not.** That is still a large win and a
real change in kind for the *backfill* — the 40k-document harvest of a disallowing host was
the sharpest edge of the POL-1 question and it is gone. But three things survive, and each is
a reason the gate still has to rule on this host:

1. **A pre-2014 backfill of a few thousand documents.** In scope by the admin's own words:
   `source.admin_intent` says *"archief vanaf 2008"*.
2. **A recurring recent-window poll.** Small (~0.25 events and ~4 documents/day on 2025
   actuals) but ongoing, which is a different posture question from a one-off backfill:
   continuous access is harder to characterise as incidental.
3. **Discovery, not just fetching.** An ORI-only route cannot *see* pre-2014 items at all, so
   the project's own scoping rule — *discover all, process scoped, defer the rest as metadata
   stubs* — cannot be honoured without touching the provider. The gap is a **discovery** gap,
   and that is the one kind of gap this project has decided not to accept.

What is genuinely better, and survives the correction: for the ~85% the route *does* cover, an
**inferred** consent is replaced by a **declared** one, asked of an organisation whose stated
purpose is enabling this reuse and which has an obvious contact route. That is a better shape
than any "conditional GO" over the same material. It is a smaller question, asked of a more
answerable party — not the absence of a question.

## 6. Second-order win — ~~it may also close PROJ-1~~ OVERTAKEN 2026-07-25

PROJ-1 was the dead `publisher` facet in both Notubiz manifests
(`engine/strategies/notubiz_events.py` hard-coded `"gremium_name": None`, and the
enrichment stage its comment deferred to does not exist). ORI's MediaObject carries
`has_organization_name` → an `Organization` document (34 for Vlaardingen, 36 for Haarlem —
the right order of magnitude for a gremia list), so if the ORI route landed the join would
come **inline with discovery**.

**The manual fix was done first and the facet is live** — `enrichment.lookup_tables` +
a projection-time join in `engine/project/projector.py`, measured 0% → 100% non-null on
both indices. That was the right order: it was cheap, it was independent, and it did not
have to wait on an admin decision about someone else's licence. What survives here is a
*simplification* to consider **if and when** the ORI route lands — the hand-maintained
`values:` table could be replaced by ORI's organisation names, retiring a table that must
be refreshed whenever a municipality adds a gremium. Not a reason to touch anything now.

## 7. What this does NOT settle (owed, and named so it is not forgotten)

1. **ORI's data licence.** MIT covers the software. The data licence per municipality is
   `not_assessed`. **Drafted as a sendable ask 2026-07-25 —
   [`../outreach/01-ori-datalicentie-openstate-vng.md`](../outreach/01-ori-datalicentie-openstate-vng.md);
   not sent.** It asks about three separable things (metadata, ORI's *own* extracted text,
   and the proxied original bytes), because they need not have the same answer. This is now
   an *askable* question — there is a named organisation with a
   contact route, which `api.notubiz.nl` never offered.
2. ~~**Aggregator provenance in the envelope.**~~ **SETTLED 2026-07-25 — see §9. It fits;
   no envelope change, no migration.** One semantic consequence does need recording, and it
   is in §9 rather than here because it changes what a `reconciled` verdict *means*.
3. **Completeness reconciliation against the provider.** ORI's coverage of a municipality
   is *its* harvest, not necessarily the provider's full corpus. TOT-1..4 apply: the
   manifest needs a claimed-total and a reconciliation verdict for the ORI walk, and the
   honest posture is "ORI-complete", not "Vlaardingen-complete", until a sampled
   cross-check against the provider says otherwise. **Note the tension:** that cross-check
   is itself a reason to touch `api.notubiz.nl`. A sampled check (hundreds of HEADs) is a
   different order of exposure from a 40k-document backfill, but it is not zero and must be
   ruled on explicitly rather than assumed in.

   **MEASURED 2026-07-25 while building step 3 — the warning is now a number, and it is
   worse than "not necessarily".** Cross-checking the 89 already-ledgered Vlaardingen
   documents (harvested from Notubiz) against ORI, **for free and without touching the
   provider** — the ledger is the sample:

   | | |
   |---|---|
   | carried by ORI | 59 / 89 |
   | **URN equality on those 59** | **59 / 59 exact** — the §9(a) claim, proven against real ledger rows rather than fixtures |
   | absent from ORI | 30 |
   | …of which dated **inside** ORI's covered window (≤ its newest meeting, 2026-07-09) | **28** |

   The 28 are ordinary PDFs — besluitenlijsten, raadsbesluiten, ingekomen stukken — not
   exotic formats. Two mechanisms fit and this probe does **not** distinguish them: ORI
   harvested those meetings before the documents were attached and has not reindexed, or a
   genuine harvest gap. **Read the rate with care:** the ledger sample is the Stage-B recent
   slice (2026-06-02 … 2026-07-14), i.e. precisely the window where aggregator lag is most
   likely.

   > **The reassurance that used to sit here was not a measurement — corrected 2026-07-25.**
   > This paragraph closed with *"a separate 400-item spot walk over 2010–2013 found 401/401
   > carrying page text. So this is evidence of weak recent-window coverage, not of a broken
   > archive."* That walk measured **text presence among the items ORI carries**. It never
   > compared against the provider, so it was not a coverage measurement and could not support
   > the conclusion drawn from it. Archival coverage was, at that point, **unmeasured**.

   **Now measured, and the deficit has TWO ends** (`../experiments/stage_b/probes/
   vlaardingen_raad/ori_era_coverage.json` — free, and again without one request to the
   disallowing host: the provider side is the 785 era-stratified documents the onboarding
   battery already harvested, the aggregator side is one `_source`-filtered walk):

   | era sampled | in ORI | coverage |
   |---|---|---|
   | 2008 | 0 / 28 | **0%** |
   | 2012 | 0 / 58 | **0%** |
   | 2014 | 39 / 39 | 100% |
   | 2016 | 87 / 91 | 95.6% |
   | 2018 | 135 / 136 | 99.3% |
   | 2020 | 127 / 127 | 100% |
   | 2021 | 296 / 297 | 99.7% |
   | 2024 / 2026 | 4 / 4 · 5 / 5 | 100% |
   | **overall** | **693 / 785** | **88.3%** |

   ORI's coverage is **excellent in the middle and absent at both ends**: a hard archival
   cutoff (nothing before 2010, effectively nothing before 2014 — §2), a lagging recent
   window, and a small scattered residue in between. The uncovered eras are real documents,
   not placeholders (2008: 23 PDFs + 5 msword, max 1.77 MB; 2012: 58 PDFs, max 13.1 MB).

   Three consequences. `discovery.completeness_claim: aggregator` (§9) is a **substantive**
   declaration, not a formality. The aggregator is a poor fit for the *incremental* role even
   where it is the right one for the backfill — a fresh-material pipeline reading only ORI
   would have missed 28 of 30 recent documents. And it is a poor fit for the **early archive**
   too, which is the part nobody had measured: a backfill reading only ORI would silently
   start in 2014 on a source whose declared range and stated intent both begin in 2008. Both
   are manifest decisions for step 4, not things this strategy should assume.
   Artifacts: `../experiments/stage_b/probes/vlaardingen_raad/ori_coverage_crosscheck.json`
   (recent window) and `ori_era_coverage.json` (per era).

   **The mechanical control this earned** (rule 5: an obligation with no check is not a
   control). Probe 0's aggregator step measured only `max(freshness_field)` — so it answered
   *"is this current?"* and never *"how deep does it go?"*, and recommended ORI for
   Vlaardingen without noticing the six missing years. It now aggregates **both** ends plus
   `items_per_year`, and `aggregator_provenance` owes a fourth item: the aggregator's archive
   span compared against the source's declared range. A start date alone is not enough — ORI's
   oldest Vlaardingen item is 2010-01-07 while 2011–2012 hold zero, so a sparse head makes an
   archive look years deeper than it is. Pinned by
   `test_archive_depth_is_measured_too_not_only_freshness` and
   `test_a_sparse_archive_head_is_visible_per_year_not_hidden_behind_a_start_date`.
4. **Whether the extracted text is good enough.** 100% coverage says text exists, not that
   it is accurate on scanned pages. Sample against our own OCR before dropping the tail.

## 8. Build order

1. ~~`registries/aggregators.yaml` + loader + `probe_aggregators()` in `engine/probe.py`,
   with the §2 traps pinned as tests.~~ **DONE 2026-07-25.** 13 tests in
   `tests/test_probe.py` (192 total). Verified live on the motivating case: probe 0 on
   `vlaardingen_raad` went from `no_bulk_surface, recommended=null` to
   `bulk_surface_available, recommended=aggregator:open_raadsinformatie` (16,877 items);
   Haarlem 71,062, newest 2026-07-02. `tuchtrecht` — which declares no corpus hints —
   correctly still recommends SRU and records the registry step as a `manual` obligation
   rather than a false absent. Artifacts: `../experiments/stage_b/probes/*/probe0_with_aggregator.json`
   (the pre-aggregator `probe0_bulk_surfaces.json` is kept as the before-record).
   Also landed: `policy_flags.disallow_avoidable_via` — the SURF-3 × POL-1 link, which on
   both Notubiz sources now reads `{"https://api.notubiz.nl": ["open_raadsinformatie"]}`.
2. ~~`rank_class: bulk_content` + the surface-gate obligation `aggregator_provenance`.~~
   **DONE 2026-07-25** (7 tests, 199 total). Landed as *two* obligations, not one — the
   second was not in the original plan and is the more useful half:
   - `aggregator_provenance` — owed when the agent CHOOSES an aggregator; names all three
     §9 items (the `completeness_claim` declaration, `confidence: NULL`, the unresolved
     licence, read from the registry entry rather than hard-coded).
   - `aggregator_considered` — owed when an aggregator was FOUND and something else was
     chosen. It does **not** force the route; it forbids *silence* about not taking it.
     Where the aggregator republishes a disallowing host, the detail says so.

   Also fixed a gap step 1 opened: the gate's block condition tested
   `rank_class == "bulk_metadata"` literally, so a scraper chosen while an *aggregator* was
   recommended came back `allowed`. Now `BLOCKING_RANK_CLASSES = {bulk_content,
   bulk_metadata}` (`bulk_index` still deliberately absent — a bare sitemap still needs
   per-URL fetching). Pinned by `test_scraper_is_blocked_over_an_aggregator_too`.

   Checked against the real stored artifact, not only fixtures:
   `surface_gate(<vlaardingen probe 0>, "notubiz_events")` — what the manifest does
   **today** — returns `allowed` but **not `acceptable`**, with the open
   `aggregator_considered` obligation naming the 16,877-item route and the disallowing host
   it would avoid. The finding now binds on the live manifest instead of sitting in a doc.
3. ~~An `ori_elastic` discovery strategy (the ES scroll/PIT walk), mapping MediaObject →
   envelope, `original_url` as the natural key, `text_pages` → our page model.~~
   **DONE 2026-07-25** — `engine/strategies/ori_elastic.py`, 11 tests in
   `tests/test_strategy_ori.py` (210 total). No scroll or PIT: **`search_after` on `@id`**,
   which is unique and sortable, so the whole walk stays inside the existing GET-only
   `HttpGet` contract (both scroll and PIT need POST). Registered in `engine/frontier.py`
   and `engine/manifest.py` like any other strategy — **no new pipeline stage, no
   migration, no envelope change** (§9 held).

   Landed alongside it, and neither was in the plan:
   - **`DiscoveredItem.extra_urls`** — an additive in-flight field (no schema change) so the
     provider's `original_url` is preserved beside ORI's resolve URL, per §9(a). It is
     written under role **`provider_file`, deliberately NOT `file`**: the fetch candidate
     query in `engine/process.py:47` joins `item_urls` on `role = 'file'`, so a second
     `file` row would silently double-fetch every document.
   - **`extraction.payload_extraction`** — the extract-from-payload row is now
     manifest-declared (`method`/`engine`/`engine_version_field`/`pages_field`), with
     defaults that reproduce tuchtrecht's `sru`/`sru-abstract`/`1.0` row byte for byte.
     One rule is enforced in **code, not manifest**: `method: aggregator` forces
     `confidence: NULL` whatever the YAML says, so §9(b) cannot be edited away. An item
     whose payload carried **zero** pages still gets `missing: ['fulltext'], upgradable`,
     so ORI's ~2% text-less MediaObjects stay OCR-able through the normal route.

   Verified live, not only on fixtures: a bounded 400-item walk of `ori_vlaardingen_*`
   returned `claimed_total 16,877`, 401 keyed / 5 unrecordable, **401/401 carrying page
   text**, every URN in the `notubiz:262:<docid>` shape.
4. **Point `vlaardingen_raad` at the route — as a THREE-role split, not a cutover.** Blocked
   on the admin (it needs `aggregator_provenance` discharged, including ORI's
   `license: not_assessed`), so it is a decision to put in front of the gate, not engine work
   to land. **Reshaped twice by measurement:** TOT-5 ruled out an ORI-only *incremental* role,
   and the per-era probe (§7 item 3) ruled out an ORI-only *backfill* as well. What is left is
   a role split, which `discovery.roles` already supports natively — `engine/frontier.py:105`
   resolves `role["strategy"]` per role, so **no engine change is needed**, and §9(a)'s
   URN equality (proven 59/59 on real ledger rows) is what makes the roles interchangeable
   item-for-item:

   ```yaml
   discovery:
     completeness_claim: aggregator        # §9 — per role; see the note below
     roles:
       archive_backfill:                   # 2008-2013: ORI carries 0% of this era
         strategy: notubiz_events          # provider — the ONLY route to these items
         partition: {range: {start: "2008-01-01", end: "2013-12-31"}}
         # ~2,000-3,000 documents. This is the residual POL-1 exposure; it does not
         # disappear, and an ORI-only manifest could not even DISCOVER these items.
       backfill:                           # 2014-2026: ORI's strength, 95.6-100% + full text
         strategy: ori_elastic
         completeness_claim: aggregator    # ORI-complete, NOT Vlaardingen-complete
       incremental:                        # recent window: ORI lags (28 of 30 absentees)
         strategy: notubiz_events          # provider, small ongoing poll
         cutoff: {field: "plannings[0].start_date", overlap_pages: 1}
   ```

   Four things to settle with the shape, not after it:
   - **`extends: backfill` must go** on `incremental` — it currently inherits the backfill
     role, which stops being coherent the moment the two use different strategies. Spell the
     provider role out.
   - **Reconciliation units differ per role** (ORI reconciles in MediaObjects, the Notubiz
     walk in *events* — `claimed_total.unit: events`, TOT-4). That is already per-role jsonb,
     so it works, but a single fleet-level "reconciled" tick now aggregates two different
     claims and must not be read as one.
   - **`completeness_claim` is per role, not per source.** Only the ORI role is
     aggregator-complete; the provider roles are publisher-complete. A source-level
     declaration would understate two roles and overstate none — still wrong.
   - **The handover boundary is a decision, not a measurement.** 2013/2014 is where ORI's
     density starts, but ORI's *oldest* item is 2010-01-07 with 2011-2012 empty. Pick the
     boundary from `items_per_year`, declare it, and let the pre-2014 role own everything
     below it — overlap is cheap (identical URNs), a hole is not.

   Haarlem needs only the **two**-role split: its provider corpus has no pre-2014 documents
   (DRIFT-2, already recorded in its manifest), so ORI's 2014 start costs it nothing.

   **This costs one live re-onboard (~$3.40) and is the point at which
   `design/onboarding-flow.md` §Phase-1 should already carry both the robots wording (W6.5
   item 1) and the aggregator step — batch them, since the flow doc *is* the agent's system
   prompt.**
5. Only then: re-ask the POL-1 posture question — **scoped to three hosts, not two.** The
   earlier plan said "the two KOOP hosts only", on the §5 claim that ORI removed
   `api.notubiz.nl` from the decision. It does not (§5, corrected): what changes is that the
   Notubiz ask shrinks from a ~19k-document backfill to **~2-3k pre-2014 documents plus a
   small recurring poll**. State the shrink and the residue together; a re-ask that repeats
   "two hosts" is asking about a corpus that does not exist.

## 9. Does aggregator provenance fit the envelope? — SETTLED, it does

Asked before writing the `ori_elastic` strategy, because working-method rule 2 says new
source mechanics land as strategies inside existing hooks, and a case that genuinely needs
an envelope change is a **significant finding** to flag rather than an edit to make quietly.

**Verdict: no envelope change, no migration, no new stage.** Second-hand content decomposes
into three facets and each already has a home — two of them with a working precedent in the
tree.

**(a) Two routes to the same bytes → `item_urls`, unchanged.** This is not a new case, it is
the case the envelope was built for: *"Multi-provenance is preserved. The same bytes
reachable via two routes … never collapsed"* (`item-envelope.md` §1, bet 2). `item_urls` is
`(urn, url, role, first_seen, last_seen)` with `role` free text (`landing | file |
api_detail | …`), so ORI's `/v1/resolve/…` is another `file`-role row beside the provider's
`original_url`. **The identity consequence is the load-bearing one:** the natural key still
derives from `original_url`, so the URN is *identical* whether an item was harvested via
Notubiz or via ORI. A source can switch routes — or run both — without re-keying a single
item, and items already in the ledger from the Notubiz path are the same items. No
`cross_source_keys` entry, no merge policy, no rejected-key note.

**(b) Text extracted by someone else → an `extractions` row, precedent already in the tree.**
`extractions` is `(urn, method, engine, engine_version, confidence, pages_processed,
text_ref)` with `UNIQUE (urn, method, engine)`. Critically, **`method` is a plain `text`
column, NOT a FK to a vocab table** — unlike `stage`, `disposition`, `missing`,
`kept_reason` and `deferred_reason`, which all reference `vocab_*`. So a new method value
costs nothing at all: not a migration, not even a lookup INSERT.

The precedent is tuchtrecht: `engine/process.py:404` already writes
`method: "sru", engine: PAYLOAD_ENGINE` for text that arrived inline in a discovery payload
rather than from our own fetch+extract. ORI is the same shape one step further out:

```
method:          "aggregator"
engine:          "ori"
engine_version:  was_generated_by.app_semver     # ORI records it per document
confidence:      NULL                            # see below — deliberately not 1.0
pages_processed: len(text_pages)
```

**`confidence` must be NULL, not 1.0.** tuchtrecht's payload path writes `1.0` because the
abstract *is* the source's own authored text — nothing was inferred. ORI's text is someone
else's OCR of a scan, of unmeasured quality (§7 item 4). Writing 1.0 would launder an
unverified extraction into a certainty. NULL is already an accepted state — `engine/
drain.py:225` writes `confidence: None` for our own OCR output and the projector carries it
through as null — so this needs no accommodation anywhere.

Checked for a trap and there isn't one: `extraction.native_confidence_threshold` (default
0.5) gates `ocr_tail` deferral at `process.py:349`, but that lives in the **native-ladder**
branch, which a payload-style extraction never enters. A NULL confidence therefore cannot
spuriously re-defer 16,583 documents whose text we already have.

**(c) "This content is second-hand" as a queryable fact → already recorded twice.**
`extractions.engine = 'ori'` and `items.discovered_via_strategy = 'ori_elastic'` each carry
it, joinable without a new column. The UI/projection can surface provenance from either.

### The one thing that DOES change — and it is semantic, not structural

**A reconciled verdict on an ORI walk means *ORI-complete*, not *Vlaardingen-complete*.**
The structure copes fine (`claimed_total_context` is per-leaf jsonb and the claimed total is
simply ORI's count), but the *meaning* of the project's central quality signal shifts, and
completeness is bet 4 — "success is an engine-computed reconciled verdict". An aggregator
inserts a party between us and the publisher whose own harvest may be incomplete or lagging,
and no amount of internal reconciliation can see that.

So the manifest must say which claim it is reconciling against. Proposal for the build:
`discovery.completeness_claim: aggregator` (vs the implicit `publisher` everywhere else),
rendered in the monitoring UI (W5) as *"ORI-complete — publisher coverage unverified"*
rather than a bare green tick. This is a **declaration**, not a probe: it makes the weaker
claim visible instead of letting a green verdict overstate itself.

The honest cross-check is a sampled comparison against the provider — which, per §7 item 3,
is itself a reason to touch `api.notubiz.nl`. Hundreds of requests is a different order from
a 40k-document backfill, but it is not zero, and it stays an explicit gate-1 decision rather
than something the route quietly assumes away.

**Register note:** this is the first case of *content provenance ≠ discovery provenance* in
the project. It fits, but it is worth a line in `failure-modes.md` so the next aggregator is
not re-derived from scratch.

## Sources

- Live probes, 2026-07-25: `api.openraadsinformatie.nl/v1/elastic` (counts, aggregations,
  MediaObject sample, `/v1/resolve/` byte fetch, `robots.txt`); `repository.overheid.nl/sru`
  (product-area counts); `robots.txt` on all five KOOP/Notubiz hosts.
- Coverage measurements, both **free and with zero requests to the disallowing host** — each
  reuses provider data the pipeline had *already* harvested as the provider side of the
  comparison: `../experiments/stage_b/probes/vlaardingen_raad/ori_coverage_crosscheck.json`
  (ledger vs ORI, recent window) and `ori_era_coverage.json` (the battery's 785 era-stratified
  documents vs a full `_source`-filtered walk of ORI's 16,877 MediaObjects, per era).
- [Open Raadsinformatie (openstate/open-raadsinformatie)](https://github.com/openstate/open-raadsinformatie)
- [NotuBiz on ORI support](https://www.notubiz.nl/nieuws/koppelen-notubiz-helpt-je-graag-verder)
- Register: `failure-modes.md` SURF-3 (evidence), SURF-1/SURF-2 (the pattern one level down).
