# Extraction fallback stack — LLM rescue behind guard conditions

**Motivation.** Deterministic extractors (selectors, regexes, precedence ladders) are
nearly free — and they fail on *some* documents while working on the rest: a template
variant, a missing label, a malformed page. Today that surfaces as a silently absent
field. This doc answers: when the deterministic toolbox comes up empty on a field the
dataset *needs*, can the pipeline spend cents on a cheap LLM to recover that one
document — without the LLM becoming a crutch that hides real breakage?

## Mostly assembled from existing parts

The inventory already contains most of the stack:

- **Per-field precedence ladders** (inventory §10) — ordered expression attempts,
  structured beats fuzzy (W, V).
- **An extraction chain with an LLM rung** — V ran structured → LLM → regex fallback,
  raw always kept (§9). The PRM bucket is defined as prompt + schema + fallback +
  offline mode.
- **Batched cheap-LLM calls** — V's title classification, ~50 per call.
- **Fail-open enrichment** (S): a failed enricher never blocks indexing.
- **Budget gates and budgeted cohort runs** (W): the OCR-upgrade-run machinery.

Three pieces are genuinely new. All land as manifest surface plus one vocabulary
`INSERT` — no new stage, no envelope change.

## 1. Guard conditions: when the fallback engages

A ladder today is just ordered attempts; nothing declares which fields the dataset
cannot do without. The manifest gains a per-field **requirement declaration**:
`required: true` (or a coverage expectation), plus the field's type. The guard fires
when the ladder is exhausted *and* the requirement is unmet — that, not any exception,
is the LLM-rescue trigger. Optional fields keep failing silently-but-recorded, exactly
as cheap as today.

## 2. The rescue cohort: batched, deferred, budget-gated

V's LLM extraction was per-document and inline. Rescue instead **accumulates**: a guard
failure marks the item `pending_llm_rescue` (a status-vocabulary `INSERT`, per the Q2
lookup-table decision) and the item proceeds through the pipeline — indexed with the
field absent, `missing` set, upgradable. A periodic budgeted run drains the cohort in
batches (~50 docs per call, against **stored raw** HTML/text — V's raw-retention lesson
pays again: no re-fetch, no re-spend), writes recovered fields with provenance
`method: llm_rescue`, and re-projects. Same mechanism as OCR-upgrade runs (and the same
never-blocking shape as the OCR tail in `ocr-strategy.md`).

## 3. Incidental vs systematic: rescue must not mask drift

The most important rule. If 2 % of a run's items trip a guard, that is incidental —
rescue them. If 60 % trip the *same* guard, the selector has drifted; per-document LLM
rescue would paper over it at per-document cost, forever, and the health signal that
should wake self-healing (inventory §16) would never fire. So the engine applies a
**per-field failure-rate threshold** (CFG, default ~10 % per run): below → items queue
for rescue; above → rescue is *suppressed* for that field, the run report flags
probable manifest drift, and the ordinary blast-radius healing path takes over. After a
healed selector, the still-pending cohort re-runs the deterministic ladder first — most
of it should drain for free.

## Manifest sketch

```yaml
extraction:
  fields:
    besluit_datum:
      ladder: [{label_anchor: "Datum besluit"}, {selector: "TODO(probe)"}, {regex: "TODO(probe)"}]
      required: true
      type: date
  rescue:
    llm: {model: cheap, batch: 50, budget_eur_per_run: 2}
    systematic_threshold: 0.10     # per field per run; above → suppress rescue, flag drift
    input: stored_raw              # never re-fetch
```

## What this cost the design

One manifest subsection, one status-vocabulary value (`pending_llm_rescue` — an
`INSERT`), one engine threshold with a CFG default, and a cross-link to the §16 healing
tiers. The cohort, budget-gate, and upgradable-status machinery existed already. Per
the working method: this landed inside existing hooks; no new stage was needed.
