# Sample manifest: SRC / Reclame Code Commissie — in the derived DSL

**Purpose.** An expressiveness test: can the DSL derived in `decision-inventory.md`
express everything the hand-written SRC pipeline actually does, with **zero snippet
code**? The syntax below is illustrative, not final — what matters is which *concepts*
the language needed. Values marked `TODO(probe)` are placeholders the onboarding agent
would fill from live probing; everything else is taken from the documented SRC
implementation. The friction log at the bottom records what drafting this revealed.

---

```yaml
manifest_schema: 1
source:
  id: src
  name: "Stichting Reclame Code — uitspraken"
  base_url: https://www.reclamecode.nl
  admin_intent: >
    Alle RCC/CVB-uitspraken doorzoekbaar maken met facetten op uitkomst, product-
    categorie, geciteerde codes/artikelen, medium en klager-type. Dagelijks bijwerken.
  volatility: persistent            # decisions stay online; no TTL pressure
  language: nl

politeness:
  delay_seconds: 1.0
  timeout_seconds: 60
  concurrency: 5
  retry: {attempts: 3, backoff: exponential, retry_on_status: [500, 502, 503, 504]}

budgets:
  ocr: not_applicable               # native HTML source — no PDFs in the primary flow
  llm_runtime: disabled             # all enrichment is generated-table based (TBL)
  eur_per_run_cap: 0

# ── Session ──────────────────────────────────────────────────────────────────
session:
  bootstrap:
    strategy: harvest_hidden_inputs           # the ASP.NET/Umbraco pattern
    page: /uitspraken/uitspraken-zoeken/
    inputs: [__RequestVerificationToken, ufprt]
    apply_to: discovery_requests
    on_failure: abort_run                     # no point continuing without tokens
    refresh_when: {status_in: [403, 419], max_refreshes: 2}   # token lifetime guard

# ── Discovery ────────────────────────────────────────────────────────────────
discovery:
  transport: http
  roles:
    backfill:                                 # one-time historical crawl
      strategy: paged_form_search
      request:
        method: POST
        url: /uitspraken/uitspraken-zoeken/
        params:
          Startdatum: "{partition.start:%Y-%m-%d}"
          Totdatum: "{partition.end:%Y-%m-%d}"
          page_param: "TODO(probe): exact form field name for page number"
      partition:
        key: date
        ladder: [year, month]                 # engine auto-subdivides a leaf that hits the cap
        range: {start: 2000-01-01, end: today}
      pagination: {model: page_number, base: 1}
      termination:
        any_of:
          - sentinel: {selector: "h2.results-title", contains: "Geen resultaten gevonden"}
          - empty: {selector: "div.rl_layout242_item"}
      claimed_total:
        declared: none                        # results-title shows a query-wide number,
        reason: "header count is not per-partition; verified unreliable"
        compensating_controls:                # REQUIRED when claimed_total: none
          - adaptive_subdivision
          - scheduled_full_recrawl: {cadence: yearly, reconcile_against: ledger}
      sanity_cap: {pages_per_leaf: 500}
      items:
        selector: "div.rl_layout242_item"
        fields:                               # sidecar fields live on the LISTING CARD
          dossiernummer:
            ladder:
              - label_anchored: {label: "Dossiernr", take: sibling_text}   # preferred primitive
              - select: {selector: "h3.rl-heading-style-h5"}               # known-good today
            transform: [trim]
          detail_url:
            select: {selector: "a.rl-button-link", attr: href}
            transform: [absolutize]
          datum:
            ladder:
              - label_anchored: {label: "Datum", take: sibling_text}
              - positional: "div:nth-child(2) > div:nth-child(2)"          # legacy; health-checked
            transform: [trim]
            trust: advisory                   # multi-format parse at index; ignore_malformed
          product_dienst: {ladder: [{label_anchored: {label: "Product/dienst"}},
                                    {positional: "TODO(probe)"}]}
          motivatie:      {ladder: [{label_anchored: {label: "Motivatie"}},
                                    {positional: "TODO(probe)"}]}
          uitspraak_meta: {ladder: [{label_anchored: {label: "Uitspraak"}},
                                    {positional: "TODO(probe)"}]}
          medium:         {ladder: [{label_anchored: {label: "Medium"}},
                                    {positional: "TODO(probe)"}]}
    incremental:
      extends: backfill                       # role composition by inheritance
      partition:
        key: date
        leaves: [current_year, next_year]     # the narrow daily window
      schedule: {cadence: daily}

# ── Identity ─────────────────────────────────────────────────────────────────
identity:
  natural_key: {from: detail_url, expr: {query_param: uitspraakId}}
  urn_template: "src:{natural_key}"
  merge_policy: keep_separate
  rejected_candidates:                        # negative knowledge is recorded, not lost
    - key: dossiernummer
      reason: "reused across appeal documents (CVB shares the stem) — fails uniqueness probe"
  onboarding_probes:                          # stress-test battery results, dated
    key_uniqueness: {result: pass, tested: "TODO(probe)", sample: 500}
    cap_probe: {result: "paging depth capped; single unfiltered query cannot reach tail",
                mitigation: partition_by_year}

# ── Fetch ────────────────────────────────────────────────────────────────────
fetch:
  detail_page:
    trim:
      selector: "div.rl-padding-section-large-5"
      on_missing: keep_full_page              # fail-open: never lose data to selector drift
    store: {plain_text: true, html: true}     # dual body for search + modal display
  attachments: none                           # RCC decisions are self-contained HTML

# ── Relationships ────────────────────────────────────────────────────────────
relationships:
  group_keys:
    dossier:
      from: dossiernummer
      transform: [{regex_replace: {pattern: '\s*-\s*CVB$', with: ""}},
                  {replace: {from: "_", to: "/"}}]
  edges:
    - type: appeal_of
      established: index                      # no crawl-time link exists between the two
      when: {field: dossiernummer, matches: '(?P<stem>.+?)\s*-\s*CVB$'}
      target: {group_key: dossier, value: "{stem}"}   # named capture flows into the edge
      note: "CVB decision appeals the RCC decision sharing the dossier stem"

# ── Extraction ───────────────────────────────────────────────────────────────
extraction:
  ladder:
    - structured_html: {source: trimmed_content}
  # no OCR rung, no LLM rung, no pdfplumber — HTML-native source

# ── Enrichment (agent-generated tables; deterministic at runtime) ────────────
enrichment:
  fail_open: true                             # engine default, restated for the reviewer
  tables:
    decision_body:
      from: uitspraak_meta
      rules:
        - {contains: "College van Beroep", value: CVB}
        - {contains: "voorzitter",         value: VT}
        - {default: RCC}
    normalized_uitkomst:
      from: [uitspraak_meta, motivatie]
      rule_ladder_ref: tables/src/uitkomst_ladder.yaml   # Aanbeveling, VAF, dVAF, Afwijzing,
      reject: date_like_values                           # CVB Bevestigd/Vernietigd, …
    product_category:  {from: product_dienst, keyword_map_ref: tables/src/product_map.yaml}
    cited_codes:
      from: full_text
      vocab_regex: {terms: [CDR, RVV, RSM, NRC, KJC, RVK, CBR, CPM, CVA, CAG, MRC],
                    word_boundary: true}
    cited_articles:
      from: full_text
      regex: 'art(?:ikel)?\.?\s*(?P<nr>\d+(?:\.\d+)*)\s*(?:van de\s+)?(?P<code>CDR|RVV|RSM|NRC)'
      emit: "art. {nr} {code}"
    media_types:      {from: [medium, full_text], keyword_map_ref: tables/src/media_map.yaml}
    violation_types:  {from: [full_text, motivatie], keyword_sets_ref: tables/src/violations.yaml}
    complainant_type:
      from: full_text
      rules:
        - {contains_any_ref: tables/src/ngo_list.yaml, value: Consumentenorganisatie}
        - {contains_any: [concurrent, mededinger],     value: Concurrent}
        - {default: Particulier}
    key_terms: {from: full_text, vocab_ref: tables/src/domain_terms.yaml, min_occurrences: 2}
    tags: {union_of: [normalized_uitkomst, product_category, media_types, violation_types],
           cap: 10}

# ── Index projection ─────────────────────────────────────────────────────────
index:
  dataset: src-uitspraken
  analyzer: dutch
  body: {plain: full_text, html: full_text_html}
  dates: {datum: {formats: ["dd-MM-yyyy", "yyyy-MM-dd"], ignore_malformed: true}}
  facets: [normalized_uitkomst, product_category, cited_codes, cited_articles,
           decision_body, media_types, tags, complainant_type, year]
  chunking: engine_default          # ~750 words / 100 overlap; small docs → often 1 chunk

# ── Health & regression ──────────────────────────────────────────────────────
health:
  fixtures:
    - {name: listing_2023_p1, kind: cached_html, expect: {items: ">= 1"}}
    - {name: detail_506941,   kind: cached_html, expect: {trim_selector_matches: true}}
  zero_match_policy: alarm          # any EXPR matching 0 on a known-non-empty fixture
```

---

## Friction log — what drafting this revealed

Ordered by how much they change the DSL spec. None required a snippet; all are additions
to the declarative surface (which is the result we hoped for, now tested).

1. **`claimed_total: none` needs teeth.** Declaring "no trustworthy total" cannot be a
   free pass — the manifest schema must *require* `compensating_controls` (adaptive
   subdivision + a scheduled full re-crawl reconciled against the ledger) whenever the
   total is declared absent. This concept existed in prose in the inventory; drafting
   forced it into the schema as a validation rule.
2. **Role composition needs inheritance.** `incremental: extends: backfill` — without
   it, the daily config duplicates the whole backfill block and the two drift apart.
   Same finding as the BUS desk-check (roles), plus the `extends` mechanism.
3. **Discovery items carry full field-extraction ladders, not just (id, url).** SRC's
   six sidecar fields live on the *listing card*; the detail page doesn't repeat them.
   So the `items:` block needs the complete EXPR toolbox (ladders, label-anchored,
   transforms, trust markers), not a slimmed-down one. The envelope already allowed
   metadata-at-discovery; the DSL surface needed to catch up.
4. **Named regex captures must flow across concepts.** The `appeal_of` edge needs
   `(?P<stem>…)` captured from a field and substituted into the edge's group-key target.
   Expression results are values usable elsewhere in the manifest, not just extractors.
5. **Session bootstrap needed a declarative refresh trigger** (`refresh_when:
   status_in`) — token lifetime was prose in the SRC doc; here it becomes a testable
   declaration.
6. **Onboarding probe results are manifest content.** Key-uniqueness result, cap-probe
   result, and *rejected* identity candidates are recorded in the manifest with dates —
   negative knowledge ("dossiernummer is NOT the id, and here's why") is exactly what a
   future self-healing agent must not have to rediscover.
7. **`TODO(probe)` is a first-class marker.** A drafted manifest is a hypothesis; the
   schema should distinguish "probed and confirmed" from "assumed" per value, and the
   engine should refuse to run a manifest with open probes outside test mode.

**Score for the expressiveness test:** everything the hand-written SRC pipeline does fit
in config + strategies + expressions + tables — zero snippets, zero prompts (SRC never
needed an LLM at runtime, and the manifest preserves that). The seven findings above are
schema refinements, not gaps in the model.
