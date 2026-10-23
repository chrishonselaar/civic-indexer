# Cross-corpus search — one query over every source

**Decision (2026-07-28): a query-time union of per-source indices. Not one shared
index.** Built the same day; exit criterion #3 ("recommend mode surfaces a related doc
across *sources*") is met by it.

This was the one architectural question the 2026-07-28 handoff left open, and it was
flagged as getting more expensive with every document indexed. It did not need to be
expensive, for a reason that is visible in the code rather than a matter of taste.

## The question

Every source was its own pair of indices (`civic-<source>`, `civic-<source>-chunks`),
`/api/search?dataset=` picked exactly one, and `/api/doc/{urn}/related` ran kNN inside
one. So two things the vision states plainly were **structurally impossible**, not
merely unbuilt: user-story Act 4's journalist searching across corpora, and exit
criterion #3.

Two ways out:

| | multi-index union | one shared index |
|---|---|---|
| how | search `civic-*`, minus the chunk indices | one `civic-docs`, filtered by `source_id` |
| cost to adopt | query-layer only | re-project every document (≈49.4k docs + ≈72k chunks) |
| per-source rebuild | `civic project <s> --recreate` stays a **drop and rebuild** | becomes a `delete_by_query` over a shared index |
| mapping drift | a query-time problem | impossible by construction |
| new source | searchable the moment it is projected, nothing to register | same |

## Why the union

**Because `doc_mappings()` takes no dataset argument.** The module docstring says
"parametrized by dataset", but the mapping functions are not — every `civic-*` index
already has identical fields, one Dutch analyzer, and the same 1024-dim bge-m3 vector
space. So the shared index's only real advantage — uniformity — is a property the
system already has, while its cost is a full re-projection and the loss of the
per-source rebuild that the *rebuildable projection* bet is made of.

Verified against the live cluster before committing to it, because "OpenSearch
probably supports that" is not evidence:

- multi-index BM25 + `terms` aggregations: 4/4 shards, correct per-source buckets;
- multi-index kNN: 4/4 shards, and a `woo_besluiten` seed returned a `haarlem_raad`
  neighbour at 0.9345 — criterion #3, before a line was written;
- the exclusion pattern `civic-*,-civic-*-chunks` resolves as intended;
- a pattern matching **nothing** returns an empty result set, not an error (so a fresh
  install has no special case);
- cross-index `_mget` via `{"docs": [{"_index", "_id"}]}`.

`dataset=` still narrows to a single index, so nothing that wanted one corpus lost it.
Omitting it now means *everything*, because searching everything is the product
default.

## The three places the union is not free

**1. Document lookup can no longer be a `get`.** `osc.get` needs one index. The
replacement is a search on the `urn` keyword across the target — deliberately *not* an
index name derived from the URN prefix, which names the **provider**, not the source
(`notubiz:262:…` backs `vlaardingen_raad`). That rule already cost this project a
takedown that reported success while the document stayed searchable;
`tests/test_suppression.py` pins it for suppression and `tests/test_cross_corpus.py`
now pins it here.

**2. Fusion has to key on index + id, not id.** Two corpora from the same provider can
in principle carry the same `_id`. Keyed on the id alone, an RRF pool silently drops
one of the two. Pinned by a test that would have passed before the union existed and
fails without the fix.

**3. Reranking is governed by the pool's strictest member — and mixed pools are now
the normal case.** Only `woo_besluiten` declares `policy.processors:
[voyage_rerank]`; the other three declare `[]` (see `access-posture.md` for why the
default is "nobody"). So a cross-corpus pool does **not** rerank, and the API reports
`rerank_blocked` naming the sources that withheld consent, which the UI shows.

That last one is a real product consequence, and it is left visible rather than
engineered around: **at the cross-corpus default, the flagship ranking mode degrades
to `hybrid`.** Interleaving a reranked permitted subset with an RRF-ordered remainder
would mix two score spaces and is not worth having. Widening it is an **admin
decision** on `policy.processors` per source — the same standing rule as the posture
itself — not something the code should route around. Exit criterion #2 is measured
per corpus until then.

## What this surfaced: the mapping had already drifted

The first cross-corpus aggregation failed on three of the four indices:

```
"Text fields are not optimised for operations that require per-document field data
 like aggregations and sorting" — exposure, on civic-{woo_besluiten,tuchtrecht,haarlem_raad}
```

`exposure` was declared `keyword` on 2026-07-27. `ensure_index` creates an index only
when it is **absent**, so a field added to `mappings.py` never reaches indices that
already exist; the first document carrying it was then mapped **dynamically**, and a
string becomes `text`. `processors` was missing from three indices entirely.

Three things follow, and the order matters:

- **Enforcement was never affected.** Both fields are read from `_source`
  (`engine/exposure.py`), which is unaffected by the mapping. No document was ever
  over-served. This is worth stating precisely, because "the exposure field was
  broken" would be a false alarm about a safety property.
- **But the index was lying about its schema**, and anything that filtered or
  aggregated on those fields failed — on those shards only, inside an HTTP 200.
- **Fixed by re-projection** (`civic project <source> --recreate`), which costs no
  crawl and no GPU: Postgres holds the text and the vectors. `woo_besluiten`'s
  suppressed URN stayed suppressed through the rebuild, as designed.

This is the multi-index option's named downside arriving on day one — which is an
argument for the mechanical check, not against the union. A shared index would have
made *this* instance impossible while making every other mapping change a
re-projection of everything.

### Two controls, because rule 5

*A prompt-level obligation with no mechanical check is not a control*
(`failure-modes.md` rule 5).

- **`uv run civic check-projection`** — compares every live `civic-*` mapping against
  the declared one and distinguishes `wrong_type` (queries misbehave now) from
  `undeclared` (a field typed by guess) from `unmaterialised` (declared, never
  written — harmless today, a `wrong_type` waiting for its first write). Exits
  non-zero on the first two and names the fix. This is **not** the W6.5
  projection-liveness probe, which asks a different question (does the projection
  reflect the ledger?); this one asks only whether an index agrees with its schema.
  **Given a trigger 2026-07-28**: `/api/monitor/projection` runs it and `/monitor`
  renders it as an *Index-schema* panel plus an alert-strip entry, because a control
  that has to be *chosen* is only half a control. It is a separate endpoint, fetched
  independently, so the rest of the dashboard stays Postgres-only and survives a dead
  cluster.
- **`incomplete` in the API response** — a partial shard failure is an HTTP 200 with
  fewer results. Inside a union that means a whole source can vanish from an answer
  that looks entirely plausible. `/api/search`, `/api/datasets` now report it and the
  UI shows it. It is a *conditional* key, so a healthy search still returns exactly
  the pre-W4 response shape (the `understand=0` regression contract).

## What was built

| | |
|---|---|
| `mappings.doc_index_target()` | `None`/`"all"` → `civic-*,-civic-*-chunks`; a name → that one index |
| `/api/search` | cross-corpus by default; `source` is a new multi-select facet/filter; `source_id` is aggregated |
| `/api/doc/{urn}` | found by URN term across corpora; exposure enforcement unchanged |
| `/api/doc/{urn}/related` | cross-corpus kNN by default — **criterion #3** — `dataset=` narrows it back |
| `/api/datasets` | what is searchable right now, counted off the indices (not off `manifests/`: a manifest that never ran would otherwise show as an empty corpus) |
| UI | corpus is a facet, not a dropdown; a result says which corpus it came from; a related document from a *different* corpus is called out; both degradations (`incomplete`, `rerank_blocked`) are visible |
| `civic check-projection` | the drift control above |

## Negative knowledge

- **Do not filter the corpus by switching the index.** Selecting two of four sources
  is a `source_id` term filter against the full union, not a narrowed target. Narrowing
  the target would silently change what the facet counts are "out of", which is the
  one thing a facet has to mean consistently. (What they are counted out of is now
  *stated* rather than assumed — see the facet-scope ruling below.)
- **Do not label corpora from a table in the UI.** A hardcoded `{source_id: name}` map
  goes stale the moment the onboarding agent adds a source nobody edited the UI for —
  the label is derived from the id instead. If human names are wanted they belong in
  the manifest and must ride the projection.
- **`processors: []` writes no mapping at all** (an empty array creates no field), so
  its absence from an index mapping is not drift. A term filter on an absent field
  matches nothing, which for a consent field is the correct answer — but the check
  reports it, because the next non-empty write types it by guess.

## What a facet count is counted over (PROJ-3, ruled 2026-07-28)

Found while verifying this work and pre-dating it: hybrid mode reported the **BM25
leg's** aggregations, so `q=tandartsklacht` — a query only the dense channel can
answer — returned 100 results with all five facets empty. The corpus switcher vanished
exactly when cross-corpus retrieval was doing the most work.

**Ruling: pool-scoped, labelled.** Hybrid facets are tallied over the fused pool
(`_pool_facets`), the response carries `facets_scope: "pool"`, and the panel says so in
words. Two reasons this is the *more* honest option rather than merely the convenient
one:

- hybrid pagination was **already** pool-bounded (`min(total, pooled)`), so a global
  count was promising a narrowing the result list could never deliver;
- a count now describes documents the reader can actually reach.

The cost is real and deliberately visible: these are "within the top N" counts, and an
unexplained smaller number reads as a bug. **BM25 mode is untouched** — its
aggregations are exhaustive over the whole result set, `facets_scope` is absent (its
absence *is* the index-wide meaning), and the `understand=0` response shape stays
byte-identical. One `FACET_FIELDS` declaration now feeds both the aggregation and the
pool tally, because two hand-maintained lists drift and a facet present in one ranking
mode but not the other is this same defect in a new costume.

## Measured after the build (2026-07-28, live corpus)

- **Criterion #3 holds, and it is not a lucky anecdote.** 100 sampled documents (25 per
  source), top-8 neighbours each: **17 had at least one neighbour in another corpus.**
  The pairings are sensible — a Vlaardingen committee transcript → a Haarlem committee
  transcript (0.8335); a Woo bundle of environmental annexes → Haarlem's
  "Bijlage 2 Onderzoeken" (0.9345).
- **The rate is a function of corpus size, not of quality**: `vlaardingen_raad` (24
  docs) finds cross-source neighbours 13/24 of the time because its own corpus is
  tiny; `tuchtrecht` (47,950 docs, a distinct genre) never does. Expected, and worth
  recording before someone reads the low overall rate as a defect.
- **The degradation notice is actionable, not decorative**: the same query narrowed to
  `source=woo_besluiten` — the UI's own advice — returns `mode_used: hybrid_rerank`.

## Still owed

- **Chunk indices are not in the union.** `/api/search` searches doc indices only, so
  this changes nothing today, but any future chunk-level retrieval needs the same
  treatment (`civic-*-chunks` is the target, and the same drift check already covers
  it).
- **Cross-corpus relevance is untested.** RRF fuses two channels; it does not
  normalise across corpora with very different document lengths (a 400-page OCR'd Woo
  bundle vs a two-page raadsvoorstel). Whether one corpus crowds out another at the
  top of a mixed result set is a **measurement**, not a known problem — and it is
  distinct from criterion #2's external comparison, which is still untaken.
- **Widening `policy.processors`** so the cross-corpus default can rerank: admin
  decision, per source.
