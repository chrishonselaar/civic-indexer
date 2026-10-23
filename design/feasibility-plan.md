# Feasibility — what's proven, what isn't, and the cheapest decisive experiment

An honest position paper: do we have something, and what's the shortest path to knowing?

## What is already de-risked (unusually much, for a concept)

- **Pipeline mechanics.** Four working systems prove every stage: capped-search
  discovery, session bootstrap, OCR gating, ZIP recovery, ledgers, reconciliation,
  chunked indexing, faceted search UIs. This design is a *distillation*, not an
  invention.
- **Cost model.** Real numbers exist: OCR at $0.0015/page, Woo backfills under €75
  budgets, SRC enrichment at 10.6k docs in 30 s for free, LLM classification at
  Flash-Lite prices. Hobby-budget operation is demonstrated, not hoped. A further,
  desk-level lever: open-weights OCR VLMs on per-minute GPU fleets promise
  order-of-magnitude reductions below per-page APIs (see `ocr-strategy.md`) — pending
  a Dutch-quality A/B.
- **Search value.** The existing UIs already beat the source sites' own search (OCR'd
  scans findable, facets, operators). The delta vs official portals is proven per
  source.
- **Design coherence.** Two expressiveness tests (SRC, Notubiz archetype) and three
  desk-checks (BUS feed, envelope Q2–Q5, scoping) all landed as strategies/config, zero
  snippets, zero architecture changes. The model has stopped moving when poked — a good
  sign it's the right shape.

## The unproven bets, ranked by risk

1. **Agent-authored onboarding & healing** — the core novel claim. Can an agent run the
   battery, draft a working manifest, and later heal it, with only gate-level human
   input? Nobody has demonstrated this; it is *the* thing to test.
2. **Engine generalization cost.** "Universal" systems classically cost 3–5× a bespoke
   one and die of abstraction. Mitigation exists (walking skeleton, reuse proven code,
   library-before-DSL — see fallbacks), but the effort estimate is unvalidated.
3. **Maintenance economics at fleet scale.** 20+ sources means something breaks weekly.
   Self-healing is designed but unproven; if it underperforms, operations cost grows
   linearly with sources.
4. **Demand beyond the first client.** opub exists and is polished. Our differentiation
   is concrete — OCR-complete corpora, self-serve source addition in a day, thematic
   client subsets, advanced operators, no dependency on parties self-federating — but
   only one prospective client conversation validates it so far.

**Do we have something?** Yes — conditionally. The design is well-grounded and the
differentiators are real and specific. The uncertainty is not *whether the system can
work* (the four projects settle that) but *whether the universal+agentic version is
cheap enough to build and run*. Both conditions are testable in weeks, not months. The
quiet moat is the scar-tissue: the decision inventory, the battery, and the fixture
method are exactly the parts a competitor can't copy from a feature list.

## The experiment (≈4–6 weeks part-time, three stages)

**Stage A — walking skeleton (2–3 weeks).** The smallest real engine: Postgres ledger +
envelope core, *one* discovery strategy (paged JSON API), fetch + native extraction
(ported from Woo/SRC code, not rewritten), OpenSearch projection, run report with
reconciliation verdict. **Deliberately deferred:** the DSL interpreter (manifests are
validated YAML read by hand-written strategy code), self-healing automation, OCR
(port from Woo when a source needs it), UI polish (reuse an existing search-ui).

**Stage B — the decisive test: agent onboarding of two *novel* sources (1–2 weeks).**
Sources never crawled before: one **archetype path** (a Notubiz municipality ≠
Amsterdam) and one **bespoke** (e.g. a tuchtcollege). The agent runs the battery,
drafts manifests, and drives the test-run/verification gates; a human implements
whatever the skeleton lacks — *and records every intervention*, because the
intervention count is the measurement.

**Stage C — the value test: one real client slice.** Deliver a thematically-scoped
dataset (e.g. the animal-rights subset per `thematic-scoping.md`) to the actual
prospective client. Watch them use it. One "I found something I couldn't find before"
is worth more than any architecture document — including these.

## Go / no-go criteria

| Criterion | Pass |
|---|---|
| Battery catches the source's traps (cap, key, vocab, drift) without human diagnosis | caught ≥ what a human retrospective finds |
| Human interventions during onboarding (beyond gate approvals) | ≤ 5 per source |
| Both sources reach a `reconciled ✓` backfill | yes, within stated budgets |
| Cost actuals vs agent projection | within 2× |
| Time from URL to searchable (scoped) dataset | ≤ 1 day wall-clock |
| Client outcome | finds something the official search couldn't surface |

## Graceful fallback positions (why the bet is asymmetric)

- **If agent authoring underperforms:** degrade to *agent-assisted, human-approved*
  onboarding. Even a battery + draft manifest that a human finishes turns weeks of
  bespoke scripting into a day of review — still a step-change over the status quo, at
  worse economics than hoped but far better than today's.
- **If engine generalization balloons:** stop generalizing and ship the invariants as a
  **library** (ledger, reconciliation, budget gates, format router) under thin
  per-source scripts — the four existing projects retrofitted onto shared rails. The
  DSL comes later or never; most of the operational value survives.
- Both fallbacks are useful products. The experiment can *downgrade* the vision but is
  unlikely to strand it — which is what makes the 4–6 weeks a sound bet.

## Before writing more design

Two cheap actions worth doing in parallel with (or before) Stage A: (1) a second and
third **demand conversation** — show `user-story.md` to a journalist and an NGO
researcher, ask what they'd pay in money or attention; (2) a one-page **AVG/takedown
position** (per-source retention, the source's own rectification signals, a removal
workflow) — the one risk category that can't be fixed by engineering later; (3) the
**OCR engine A/B** from `ocr-strategy.md` on the worst already-archived W scans — a
half-day script, no crawling involved, and it prices the burst-fleet cost model with
measured pages-per-GPU-hour instead of vendor claims.
