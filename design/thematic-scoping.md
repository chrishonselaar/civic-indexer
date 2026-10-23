# Scoping — indexing a subset first (thematic and otherwise)

**Motivation.** Often the initial interest is a *subset* of a source: a client working
on animal rights, a journalist's beat, a campaign theme. Scoping lets a new dataset go
operational faster and cheaper — index the ~900 relevant documents tomorrow for €2
instead of the full 14k next month for €30 — while keeping the option to expand later.

## This is not a new feature — one of the four projects already *was* one

The Vergunningen pipeline is, structurally, a **themed crawl**: a kapvergunningen-scope
(CQL `dt.title=vellen OR kappen OR houtopstand OR w.activiteit=="kappen"`) over the full
officielepublicaties corpus. Its lessons transfer directly:

- **Keyword filters leak.** Title-only matching missed records that were correctly
  *tagged* as kap-activity but titled differently — the fix was combining free-text
  terms with structured fields. Any thematic filter has a recall risk that must be
  measured, not assumed.
- **"Kap" also means roof construction.** Theme classifiers need false-positive
  filtering too — which is exactly what the cheap-LLM title-classification pass did.

## The core principle: scope limits *spending*, never *knowledge*

Discovery (metadata enumeration) is cheap; fetching, OCR, and indexing are what cost
money. So the default is: **discover everything, classify cheaply, spend selectively.**
Out-of-scope items are not dropped — they become metadata stubs in the ledger with
disposition `deferred(reason: out_of_scope)`: counted, reconcilable, reportable
("12.400 more documents exist outside your theme"), and instantly expandable later.

This preserves the completeness machinery intact: claimed-total reconciliation still runs
against the *full* corpus, because we still enumerate the full corpus — we just don't
pay to process all of it.

## Three places a scope can be applied

| Where | Cost | Recall risk | Reconciliation | When to use |
|---|---|---|---|---|
| **At the source** (query filter, like Vergunningen's CQL) | cheapest — discovery itself shrinks | **real** — keyword filters leak; measure it | ⚠️ breaks full-corpus reconciliation; needs a periodic *unscoped count-check* + declared caveat | corpus too huge to enumerate, or source offers trustworthy structured theme fields |
| **At discovery** (default) | discover all metadata; classify on title/metadata; fetch+OCR+index only matches | low — classifier is recall-tuned, misses recoverable | ✅ fully preserved | almost always |
| **Post-extraction** | theme only detectable in full text | low | ✅ | last resort; mitigate with partial extraction (first pages) as a cheap classifier, or sample-based scope estimation |

## The theme classifier: recall-first, agent-generated

Same two-tier pattern as everything else: the agent generates a **keyword/rule table**
(TBL) at onboarding from the admin's theme description plus a corpus sample; borderline
cases go through a **cheap LLM batch pass** on titles/metadata (the Vergunningen
title-parsing pattern, ~50 per call). Tuning is deliberately **include-on-doubt** — a
false positive costs cents of processing; a false negative hides a document from the
client. Gate 2 shows the admin a sample of in/out classifications with an estimated
precision/recall, and misses are structurally recoverable: expansion runs process the
deferred cohort anyway.

## Priority, expansion, and multiple scopes

- **Work-queue ordering** generalizes the phased-backfill idea: tranches ordered by
  `(scope-match, recency)` — the client's theme lands first, newest first; the rest
  follows if/when budget allows.
- **Expansion is a budgeted run over the `deferred(out_of_scope)` cohort** — no
  re-discovery, no re-crawl; the ledger already knows every item and its metadata. The
  same mechanism as OCR-upgrade runs.
- **Scopes are named objects** (`scope: dierenwelzijn`), several may be active per
  source (different clients); the processing set is their union, and reporting is
  per-scope ("your theme: 812 docs indexed, +3 this week").

## Scopes generalize beyond themes: two predicate classes

Theme was the motivating example, but nothing above depends on it. A scope is a **named
predicate over enumerated metadata**, and predicates come in two classes:

- **Classifier scopes** (themes): fuzzy, real recall risk → TBL + LLM-borderline pass,
  include-on-doubt, precision/recall shown at Gate 2. Everything described above.
- **Metadata-filter scopes**: deterministic predicates on fields discovery already
  yields — date range ("only documents since 2023"), gemeente/publisher, document type,
  file size. No classifier, no LLM pass, no recall risk — *provided the field is
  trusted*. W's garbage-dates lesson applies: a filter on a field carrying a declared
  trust override must use the derived value (URL-path date), never the source's.

For metadata filters, the `source_filtered` mode's recall caveat mostly evaporates:
structured date/organisation query parameters don't "leak" the way keyword filters do,
so filtering at the source becomes acceptable where the field is trustworthy — keep the
periodic unscoped count-check as a cheap tripwire.

**Rolling windows never revoke.** A time scope like `within: {years: 2}` moves. The
rule: a scope is evaluated **at classification time** and only ever gates *new*
spending; an item already processed stays indexed when the window slides past it.
Scope limits spending, never knowledge — and never retroactively un-spends.

## Manifest sketch

```yaml
scoping:
  mode: discover_all_process_scoped        # default; alternatives: source_filtered, full
  scopes:
    - name: dierenwelzijn
      classifier:
        keyword_table_ref: tables/{source}/scope_dierenwelzijn.yaml
        llm_borderline: {model: cheap, batch: 50, on_doubt: include}
      applies_to: [fetch, extract, ocr, index]   # discovery always runs unscoped
      priority: 1
    - name: recent                               # metadata-filter scope: deterministic,
      filter: {field: publicatiedatum, within: {years: 2}}   # no classifier, no LLM pass
      applies_to: [fetch, extract, ocr, index]   # rolling window gates new spending only
      priority: 2
  out_of_scope: {disposition: deferred, reason: out_of_scope, index_metadata_stub: true}
  expansion: {trigger: manual_budgeted_run}
  # only if mode: source_filtered —
  # recall_caveat: declared, plus scheduled unscoped_count_check for reconciliation
```

## What this cost the design

One vocabulary value (`out_of_scope` — an `INSERT`, thanks to the Q2 lookup-table
decision), one manifest section, and a work-queue ordering rule. The deferred bucket,
budget gates, upgradable statuses, TBL+LLM classification, and phased backfill all
existed already. A feature request that lands as configuration is the design working as
intended.

Generalizing beyond themes cost one distinction (classifier vs metadata-filter
predicates) and one rule (rolling windows never revoke) — again configuration, not
architecture.
