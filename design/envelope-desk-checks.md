# Desk-checks: remaining envelope open questions (Q2–Q5)

Companion to `item-envelope.md` (which carries the questions) and
`event-feed-desk-check.md` (which resolved Q1). Same method: test each question against
the evidence in the four documented projects, decide, and record what the decision
changes. All four resolve without touching the stage model.

---

## Q2 — Envelope versioning & migration policy

**Question.** Manifests are versioned; the envelope schema itself will evolve. What is
the migration policy for millions of rows — expand-and-backfill or read-time adaptation?

**Evidence consulted.** SRC's 1,940 `.OLD` sidecar files (the anti-pattern: parallel
schema versions accumulating as junk); Vergunningen's idempotent
`ALTER TABLE … ADD COLUMN` in try/except (the cheapest evolution that worked for years);
Woo's regenerable derivations keyed by processing version.

**Resolution: three change classes, three policies.**

1. **Additive changes** (new optional field, new edge type, new disposition value, new
   `missing[]` reason) — expected to be the overwhelming majority. Policy: plain
   additive migration, nullable columns. Crucially, the *vocabularies* (edge types,
   dispositions, missing-reasons, blob keep-reasons) are **lookup tables, not database
   enums** — extending a vocabulary is an `INSERT`, not a schema migration. This single
   choice removes most future migration pressure.
2. **Derived-data changes** (new enrichment scheme, different chunking, better summary
   prompt) — **not envelope migrations at all.** Ground truth (raw artifact + `text_ref`)
   is untouched; the derivation re-runs as a versioned batch job (`enricher_version`,
   `chunker_version`) that *replaces* its output. This is the SRC lesson made policy:
   reprocess = replace, never accumulate `.OLD` shadows.
3. **Core semantic changes** (URN scheme change, splitting a field, redefining
   identity) — rare and expensive by nature. Policy: classic **expand-and-contract**
   (add new alongside old → backfill in batches → cut readers over → drop old), executed
   as a deliberate maintenance event.

**Explicitly rejected: read-time adaptation for ground truth.** Two code paths reading
two row shapes is the multi-machine-divergence bug in new clothes — the whole ledger
philosophy is *one authoritative representation*. `schema_version` lives on the database
(migration-tool managed), not per row; per-row versions exist only on derived artifacts,
where they schedule regeneration rather than complicate reads.

---

## Q3 — Dangling-edge resolution

**Question.** When a new dataset arrives (e.g. rechtspraak), do existing dangling `cites`
edges get resolved lazily at query time or by a batch linker?

**Evidence consulted.** SRC's appeal chains (a CVB decision can be indexed *before* its
RCC original — dangling is normal even within one source); Vergunningen's post-hoc
normalization (a separate, idempotent, repeatable consolidation step — the shape a linker
wants); the wiki-link philosophy already adopted ("a dangling edge marks future work,
not an error").

**Resolution: store typed refs; resolve incrementally + in batch; never query-time-only.**

- An edge is stored with a **typed `target_ref`** — `{scheme: urn|group_key|ecli|op_id|…,
  value}` — plus a nullable `resolved_urn`. Unresolvable at write time is a normal state,
  not an error. Edges are unique on `(type, source_urn, target_ref)`.
- **Resolve-on-arrival (incremental):** when an item is indexed, one cheap lookup
  resolves in *both directions* — pending edges pointing at any of the newcomer's keys
  get `resolved_urn` filled, and the newcomer's own edges are resolved against existing
  keys. This handles the CVB-before-RCC ordering within a source automatically.
- **Batch linker (event-driven):** when a whole new dataset onboards, a scheduled linker
  run sweeps dangling refs whose scheme the newcomer can satisfy (all `ecli:*` refs, the
  day rechtspraak arrives). Idempotent, re-runnable, same stage shape as V's normalizer.
- **Query-time joins are a UI convenience only** (exploratory "show possible matches"),
  never the mechanism of record — facet counts, dossier views, and network analysis all
  need materialized resolutions.
- Run reports include **dangling-edge counts by scheme** — a standing, quantified
  work-list that doubles as a prioritization signal for *which dataset to onboard next*
  (many dangling `ecli:` refs = rechtspraak is valuable).

---

## Q4 — Geo / EntityRecords and OpenSearch

**Question.** Are EntityRecords (living datasets like the 122k-tree bomenbestand) indexed
in OpenSearch at all, or queried only via Postgres/PostGIS?

**Evidence consulted.** Vergunningen's actual usage: the dashboard reads SQLite directly;
trees serve delta monitoring and joins against permits — nobody full-text-searches trees.
The Woo/SRC pattern of denormalizing parent facets onto chunks (copy what filtering
needs to where the query runs).

**Resolution: entities live in Postgres; documents carry denormalized geo; entity search
is opt-in.**

- **Ground truth + deltas + spatial joins: Postgres.** At current scale (~10⁵ point
  features) plain lat/lon columns with distance math suffice; PostGIS is an upgrade to
  take *when a manifest actually needs* polygon/containment queries, not before.
- **The search UI's geo needs are document-shaped:** "besluiten about this
  neighborhood" is a filter on *documents*. So documents gain `geo_point` fields —
  populated from their own metadata or **denormalized through `about_entity` edges at
  index time**, exactly the chunk-facet denormalization pattern reapplied. Map filtering
  = OpenSearch geo queries on the document index; entities never need to be there for it.
- **Entity search as a per-manifest opt-in:** if a future dataset's primary value *is*
  entity lookup (a register, a facility list), the manifest may declare an entity →
  OpenSearch projection — governed by the same rule as everything else in OpenSearch:
  a rebuildable projection, never ground truth.

---

## Q5 — Chunk lineage

**Question (tentatively confirmed earlier).** Are chunks purely derived projections, with
nothing needing chunk-level ground truth?

**Resolution: confirmed — with one economic caveat.**

- Chunks are deterministic functions of `(text_ref, chunker_version, params)`; page
  markers for citations live inside the text distillate itself, so citation fidelity
  survives any re-chunk. Nothing in the four projects needs chunk-level ground truth.
  Chunks stay out of Postgres and out of the envelope.
- **The caveat appears the day chunk-level embeddings exist:** embeddings are derived
  from chunk text, so changing chunker params invalidates every chunk embedding — a
  *paid* regeneration, unlike free re-chunking. Policy: embeddings record
  `chunker_version`; chunker-param changes on datasets with chunk embeddings are a
  **budget-gated deliberate act** (same machinery as OCR-upgrade runs), not a config
  tweak. This quietly strengthens the staged-embeddings recommendation: summary-level
  doc embeddings (immune to chunking) first; chunk embeddings only where their value
  justifies freezing the chunker.

---

## Net effect on the model

All four questions resolved as **policies and small schema choices** — vocabulary lookup
tables (Q2), `target_ref`/`resolved_urn` on edges plus a linker stage (Q3), `geo_point`
denormalization on documents (Q4), `chunker_version` on embeddings (Q5). The stage model
and the envelope survive intact; with Q1 (event feeds) previously resolved, **the open
list is now empty** — the concept phase has no known unresolved structural questions.
