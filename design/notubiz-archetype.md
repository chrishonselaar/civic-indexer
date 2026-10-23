# Archetype manifest: Notubiz — the template-plus-parameters test

**Purpose.** The SRC manifest (`src-manifest-example.md`) tested whether the DSL can
express one bespoke site. This document tests the *other* leverage claim: that one
**platform archetype** covers the ~100+ Dutch municipalities running Notubiz/
raadsinformatie.nl, with only per-site parameters left to probe. It contains (1) the
archetype, (2) a sample instantiation for Amsterdam, (3) a friction log of what
archetype-drafting revealed — which turned out to be the most interesting part.

All platform facts are from the Gemeentes field notes; `TODO(probe)` marks what the
onboarding battery must confirm per site (or once per platform).

---

## 1. The archetype

```yaml
archetype:
  id: notubiz
  version: 1
  description: >
    Notubiz council-information SaaS (raadsinformatie.nl): JSON events API + per-event
    HTML pages + HTML module list views with document/attachment links.
  platform_signature:                    # what the fingerprinter matches on
    - host_pattern: "{municipality}.raadsinformatie.nl"
    - api_reachable: "https://api.notubiz.nl/events"
    - selector_present: "a.document_pdf"

  # ── Parameters an instantiation must supply ────────────────────────────────
  params_schema:
    organisation_id: {type: int, required: true,
                      hint: "capture from the site's own XHR calls (fingerprint phase)"}
    site_host:       {type: string, required: true, example: "amsterdam.raadsinformatie.nl"}
    archive_start:   {type: year, required: true, probe: site}
    modules:         {type: list, probe: site,
                      hint: "enumerate full module space; record forbidden ones"}

  # ── Probes resolved at ARCHETYPE level (platform behavior, inherit to sites) ──
  archetype_probes:
    cap: {result: "per_page hard max 200", tested: "TODO(probe): re-verify, source is 2024 notes",
          mitigation: partition_by_date}
    claimed_total: {result: "pagination.total_items / total_pages / has_more_pages present and truthful",
                    tested: "TODO(probe)"}
    pagination_truth: {result: "TODO(probe): ordering stability across pages under sort_field=start_date"}
    json_stability: {result: "TODO(probe): attributes[] is positional — check for stable attribute ids",
                     risk: "attributes[0].value as title is the nth-child fragility in JSON form"}

  # ── Probes that MUST be re-run per site (results differ per municipality) ────
  site_probes_required:
    - access_coverage        # forbidden modules vary per municipality (Amsterdam: 4 of ~12 public)
    - volume_and_archive_depth
    - politeness_ceiling
    - content_type_sample    # attachment format mix varies (pdf/docx/zip)
    - schema_drift           # older events may lack fields

  # ── Manifest body (site params interpolate via {param.*}) ──────────────────
  politeness: {delay_seconds: 1.0, timeout_seconds: 60, concurrency: 3, retry: engine_default}
  budgets: {ocr: per_site, llm_runtime: disabled}
  volatility: persistent

  discovery:
    transport: http
    roles:
      backfill:
        strategy: paged_json_api
        request:
          method: GET
          url: "https://api.notubiz.nl/events"
          params:
            organisation_id: "{param.organisation_id}"
            date_from: "{partition.start:%Y-%m-%d} 00:00:00"
            date_to:   "{partition.end:%Y-%m-%d} 00:00:00"
            page: "{page}"
            per_page: 200
            sort_field: start_date
            sort_order: ASC
        partition:
          key: date
          ladder: [year, month]                  # engine subdivides any leaf nearing 200×pages cap
          range: {start: "{param.archive_start}-01-01", end: today}
        pagination: {model: page_number, base: 1, page_size: 200}
        termination:
          any_of:
            - json_flag: {path: "pagination.has_more_pages", equals: false}
            - empty: {path: "events"}
        claimed_total: {json_path: "pagination.total_items"}   # reconcile per leaf — mandatory
        items:
          path: "events[]"
          fields:
            event_id:   {json_path: "self", transform: [last_url_segment]}
            start_date: {ladder: [{json_path: "plannings[0].start_date"}],
                         trust: advisory}
            title:      {ladder: [{json_path: "attributes[?id==TODO(probe)].value"},   # stable-id form, if it exists
                                  {json_path: "attributes[0].value"}]}                 # positional fallback, health-checked
            detail_url: {template: "https://{param.site_host}/vergadering/{event_id}"}
      incremental:
        extends: backfill
        partition: {key: date, leaves: [current_year, next_year]}
        schedule: {cadence: daily}
      modules:                                     # second surface: HTML module list views
        strategy: link_walk
        entry_points: "{param.modules}"            # per-site, minus forbidden ones
        levels:
          - {selector: "td.item_details", emit: item_row}
        known_exclusions: from_site_probe(access_coverage)

  identity:
    natural_key: {from: event_id}                  # document rows: notubiz document id from href
    urn_template: "notubiz:{param.organisation_id}:{natural_key}"
    merge_policy: keep_separate
    onboarding_probes: {key_uniqueness: {scope: archetype, result: "TODO(probe)"}}

  fetch:
    detail_page:
      trim: {selector: "TODO(probe): main content container", on_missing: keep_full_page}
    attachments:
      strategy: collect_payload_links
      selector: "td.item_details > dl > dd a.document_pdf"    # multiple anchors per row
      type_detection: magic_bytes                 # never trust .pdf suffix (engine invariant)
      group_under: parent_item                    # doc + its bijlagen inherit event date/provenance

  relationships:
    edges:
      - {type: attachment_of, established: crawl, from: attachment, to: parent_item}
      - {type: part_of_dossier, established: crawl, from: item, to: meeting_event}

  extraction:
    ladder:
      - native_pdf_text
      - ocr: {engine: auto, gate: engine_default, budget: "{site.budgets.ocr}"}

  enrichment:
    tables:
      document_kind:                               # motie / raadsvoorstel / verslag / vraag …
        from: [title, detail_context]
        rules_ref: tables/notubiz/document_kind.yaml   # shared across the fleet
    site_tables: optional                          # a site MAY add local vocab without forking

  index:
    dataset: "raad-{source.id}"
    analyzer: dutch
    facets: [document_kind, module, year, meeting]
    chunking: engine_default
```

## 2. Sample instantiation — Amsterdam

```yaml
manifest_schema: 1
source:
  id: amsterdam-raad
  name: "Gemeenteraad Amsterdam (Notubiz)"
  instantiates: notubiz@1                # pinned archetype version
  admin_intent: >
    Raadsverslagen, moties, voorstellen en schriftelijke vragen van Amsterdam
    doorzoekbaar, incl. bijlagen; dagelijks bij.
  params:
    organisation_id: 281
    site_host: amsterdam.raadsinformatie.nl
    archive_start: 2010                  # TODO(probe): true archive depth
    modules: [agenda_items, documents, motions, written_questions]   # the 4 public ones
  overrides:                             # CFG-only overrides — see friction log #3
    politeness: {concurrency: 2}         # large municipality, be gentler
    budgets: {ocr: {eur_per_run_cap: 5}}
  onboarding_probes:                     # site-level battery results
    access_coverage:
      public: [agenda_items, documents, motions, written_questions]
      forbidden: [Raadsadressen, Toezeggingen, Publieke documenten, Bestuursdocumenten,
                  Beleidsdocumenten, Verordeningen, Besluitenlijsten]
      note: "coverage caveat reported in every run: ~4 of ~12 modules public"
    volume: {events_per_year_max_observed: "TODO(probe)", cap_margin: "must stay < 200×pages"}
    politeness_ceiling: "TODO(probe)"
    content_type_sample: "TODO(probe): pdf/docx/zip mix"
```

A second instantiation (say, Zaanstad) would be ~15 lines: new `id`, `organisation_id`,
`site_host`, probe results. That is the leverage claim, made concrete.

---

## 3. Friction log — what archetype-drafting revealed

These are new *concepts*, not just schema fields — archetypes stress different parts of
the design than single-site manifests did.

1. **The probe battery splits into two scopes.** Platform behavior (the 200-cap,
   `total_items` truthfulness, JSON shape) is probed **once per archetype** and
   inherited; municipality-specific facts (forbidden modules, volume, archive depth,
   politeness, attachment mix) are probed **per site** and can never be inherited —
   Amsterdam's 4-of-12 public modules says nothing about Zaanstad's. The manifest schema
   needs `archetype_probes` vs `site_probes_required` as distinct, validated sections.
2. **Fleet blast radius changes the autonomy tiers.** A patch to the archetype
   propagates to every instantiation. The self-healing tiers from
   `decision-inventory.md` §16 gain a dimension: an EXPR patch that is auto-apply for a
   single-site manifest becomes **apply-after-fleet-regression** (run against the union
   of all instantiations' fixture sets) once `fleet_size > 1`, and the notification goes
   fleet-wide. Conversely this is also the payoff: one Notubiz redesign, one patch, one
   regression run, a hundred municipalities healed at once.
3. **Override policy needs teeth.** Instantiations may freely override **CFG** (budgets,
   politeness, scope). Overriding **EXPR/STRAT** *detaches* the site from that part of
   the archetype — legal, but it must be explicit (`detached_from_archetype: [section]`),
   because fleet patches silently skip detached sections and the site stops healing with
   the fleet. Silent forks are the archetype version of the `.OLD`-file rot.
4. **Platform version skew is real.** SaaS vendors roll out API changes per customer.
   The archetype therefore versions independently (`notubiz@1`, `notubiz@2`) with
   per-site pinning, and the fingerprinter re-run decides when a site should migrate.
   This is the manifest-versioning story from `envelope-desk-checks.md` Q2, recurring
   one level up.
5. **Fleet expansion is itself an enumerate-classify-exclude discovery.** "Which
   municipalities run Notubiz" is the ArcGIS-service-discovery pattern applied to
   organizations instead of layers: enumerate NL municipalities, fingerprint each,
   classify by archetype, queue matches as one-click onboarding candidates (each still
   passes its own site-probe battery and admin gates).
6. **Positional fragility exists in JSON too.** `attributes[0].value` as the title is
   `nth-child` in API clothing; the archetype records it as a health-checked fallback
   behind a stable-id lookup, same ladder discipline as HTML. The label-anchored
   principle generalizes: *anchor on identity, fall back to position, health-check the
   fallback*.

**Score:** the DSL absorbed the archetype with additions (archetype/instantiation split,
two-scope probes, override policy, fleet-aware tiers) but again **zero snippets**. The
template-plus-parameters claim survives contact — an instantiation is genuinely ~15
lines plus probe results.
