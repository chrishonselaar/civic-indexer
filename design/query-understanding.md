# Query understanding — free text → editable filter chips (W4 design, 2026-07-24)

**Status: §8 steps 1–3 BUILT** (step 1 2026-07-24; steps 2–3 2026-07-25) —
`engine/query_parse.py`, `ui/search_api.py`, `ui/app.js` + `index.html` + `styles.css`,
`tests/test_query_parse.py` + `tests/test_search_api_understand.py`, 37 tests.
**Vision-readiness exit criterion #4 is met, with no LLM in the query path.** Steps 4–5
remain deliberately unbuilt (measure-first). What the build taught the design is folded
into §4/§5 below and flagged **[build]**.

> **Scope boundary added 2026-07-28 (W11(a)), and how it composes.** For a query using
> search operators (`"…"`, `AND/EN`, `OR/OF`, `NOT/NIET/-`, `(…)`, `veror*`), chips are
> derived **only from root-conjunctive bare terms** and applied by pruning the operator
> **parse tree** — see [`search-operators.md`](search-operators.md) §4 and §9. Never
> from inside quotes, groups, `OR` branches or a negation: a chip is a *global* filter,
> so it may only come from a globally conjunctive position, and `subsidie OF (onderwijs
> 2026)` must not yield a date filter.
>
> The reason it prunes a tree instead of rewriting text is in **this** document's code:
> `residual()` removes each chip's span *and* strips `STRANDED_WORDS`, which contains
> `en` and `of`, case-folded — so text-level chipping would delete the `EN`/`OF` the
> user typed and search a different boolean than the one on screen, with no error and
> no empty result to notice. In the tree an operator is a *node*, never a leaf, so
> nothing that removes words can reach one. `query_parse.py` itself is untouched: the
> caller masks everything outside the free terms with spaces before calling `parse()`.

This is the short design the roadmap asks for before
W4 is implemented (`product-roadmap.md` row W4: "Short design first … then implement in
the search path"). It closes vision-readiness exit criterion #4: *"Free-text intent
becomes chips + expanded query without the user knowing facet names."*

> **⚠️ Significant finding — half of W4's stated plan is already shipped.** The roadmap
> scoped W4 as "cheap-LLM parse of free text → facet filters **+ expanded/decompounded
> Dutch terms**". Measured against the live corpus (§2), **the decompounding half is
> redundant**: the already-shipped hybrid RRF + rerank stack solves Dutch compound
> queries at full precision. Term expansion survives only as a narrow fallback for the
> vector-less path (§6). W4 is therefore **query→filter understanding**, not query
> expansion. This doc renames the workstream accordingly; `product-roadmap.md` and
> `README.md` are updated to point here.

---

## 1. What the user cannot do today

The search path (`ui/search_api.py`) takes a free-text `q` plus **named facet
parameters** — `publisher`, `file_type`, `disposition`, `month`, `dataset`. Ranking is
`hybrid_rerank` by default (BM25 + dense kNN fused by RRF, top-100 reordered by Voyage
`rerank-2.5`).

The facets work, and the ranking is good. What is missing is the bridge: a resident
typing *"besluiten van OCW over subsidie juni 2026"* has expressed `publisher =
ministerie van Onderwijs, Cultuur en Wetenschap`, `published_month = 2026-06` and the
term `subsidie` — but the engine receives all of it as one opaque text blob. The user
would have to already know that "OCW" is spelled `ministerie van Onderwijs, Cultuur en
Wetenschap` in our `publisher` vocabulary, and that months are `published_month`
`2026-06`, to say it in a way the filters can act on.

## 2. What we measured (live corpus, 2026-07-24)

All numbers from the running dev stack (`civic-tuchtrecht` 47 950 docs,
`civic-haarlem_raad` 1 017, `civic-woo_besluiten` 409).

**(a) Dutch compounds — already solved by hybrid. This is the finding.**

`tandartsklacht` is a natural, well-formed Dutch compound (*tandarts* + *klacht*) that
appears nowhere in the corpus as one token. The Dutch analyzer stems but does **not**
decompound, so BM25 returns nothing — the textbook case for decompounding expansion:

| query (`civic-tuchtrecht`) | `bm25` | `hybrid` | `hybrid_rerank` |
|---|---|---|---|
| `tandartsklacht` | **0** | 100 | 100 |
| `tandarts` (part) | 837 | — | — |
| `klacht` (part) | 10 000 | — | — |

The rescue is not a recall trick that trades away precision: **10 / 10** of the
`hybrid_rerank` top-10 for `tandartsklacht` actually contain the token `tandarts`
(verified per-hit against the index). The dense leg reads the compound as its parts and
the cross-encoder confirms them.

Compounds also behave as subsets rather than misses when they *are* in the corpus —
`omgevingsvergunning` 106 ⊂ `vergunning` 158 (`civic-haarlem_raad`) — so the head-noun
generalization the expansion was meant to add is a *broadening*, not a repair.

**Conclusion: do not build a Dutch decompounder.** It would spend effort re-solving,
worse, what the settled hybrid stack (README "Decisions already taken": RRF-fused BM25 +
dense, pooled recall@100 0.80 each → **0.98 fused**) already does. Recorded as negative
knowledge, per the method.

**(b) Ranking is not filtering. This is the real gap.**

`q = "besluiten van OCW over subsidie juni 2026"`, `hybrid_rerank`, `woo_besluiten`:

- the **ordering** is already right — the top 3 hits are all `ministerie van Onderwijs,
  Cultuur en Wetenschap` / `2026-06` / about subsidie;
- the **result set** is not — `total = 101`, because nothing was filtered.

The same intent expressed as facets the user had to know (`q=subsidie` +
`publisher=ministerie van Onderwijs, Cultuur en Wetenschap`) returns `total = 3`.

So the rerank stack *understands* the query and the filters *cannot act on* it. The user
gets a well-ordered list they cannot narrow, cannot count, cannot page cleanly, cannot
bookmark, and cannot turn into a saved view. **W4's value is countability and
addressability, not relevance.** Facet counts computed over the unfiltered 101 are not
the counts for the user's intent.

**(c) Facet vocabularies are small, real, and enumerable.**

| dataset | `publisher` | `file_type` | `disposition` |
|---|---|---|---|
| `woo_besluiten` | 8 ministries (OCW 113, LVVN 90, EZK 55, …) | `application/zip` 208, `application/pdf` 201 | `ok` 361, `partial` 48 |
| `tuchtrecht` | tens of colleges (Raad van Discipline Amsterdam 5 021, CTG 4 735, Hof van Discipline 4 652, …) | `text/xml` 47 950 | `ok` 47 950 |

This is the design's most useful fact: the target vocabulary is a **closed world of a few
hundred tokens**, already computed by `_facet_aggs()` on every search. A parse step can
be handed the actual vocabulary and asked to *map onto it* — never to invent values.
That structurally removes the dominant failure mode of LLM query parsing (a hallucinated
filter value that silently returns zero results).

## 3. Evidence base

Per the working method, grounded in the four documented projects (W/S/V/G) and settled
decisions rather than invented:

| Claim | Evidence |
|---|---|
| Rich faceting is achievable deterministically, no LLM | **S** — `UitspraakExtractor` derives 10 faceted fields from regex + lookup tables, 10 600 docs in ~30 s (`SRC.md` §7). Tier 1 below is the query-side analogue. |
| Enrichment must be **fail-open**, never a gate | **S** — if `extract_all()` throws, the raw doc is indexed (`enriched_doc = raw_doc`). Query-side: if parsing fails, the raw query runs. |
| Facet vocabularies are enumerable per source | **G** — the crawl partitions by `year × month × category` precisely because facet vocabularies are small and enumerable (`Gemeentes.md` §4). **S** — `keyword` on every facet field (`SRC.md` §9). |
| Never delegate relevance to source-side search | **W/A** — still true, but not for the reason first recorded: open.overheid *does* search (`zoektekst`, relevance-sorted) — see the API-3 correction in `failure-modes.md`. We own the query path because delegating it forfeits hybrid retrieval, not because theirs is absent. |
| A decoupled API in the query path is already house pattern | Voyage rerank: `rerank_available()` gates on an env var, `_hybrid_search()` degrades to `hybrid` then `bm25`, and the response reports `mode_used` / `mode_fallback_reason`. W4 copies this shape exactly. |
| LLM only behind guards, deterministic first | Settled (`extraction-fallback.md`): deterministic extraction first, batched cheap-LLM rescue only behind declared guard conditions. Same stance, query side. |
| Chips are the right surface | `ui-inspiration.md` §1 — query decomposition into editable chips (Linear/GitHub); counts-driven facet disclosure; URL-addressable state; saved views as forkable objects. |

## 4. The design

Three tiers, strictly ordered, each of which may contribute chips. **The raw query
always still runs**; parsing only *adds structure*, and every piece of structure is
visible and removable.

### Tier 0 — deterministic grammar (always on, no dependencies)

Runs in-process against the live facet vocabulary for the selected dataset (one cached
`_facet_aggs()` call — already computed on every search):

- **Vocabulary match.** Case-folded n-gram scan of the query against actual `publisher` /
  `file_type` / `disposition` values, plus a small per-dataset **alias table** (`OCW` →
  `ministerie van Onderwijs, Cultuur en Wetenschap`; `RvD Amsterdam` → `Raad van
  Discipline Amsterdam`). Aliases are a lookup table, exactly as **S** did for
  `normalized_uitkomst` — new aliases are rows, never code.
- **Date expressions.** A closed Dutch grammar → `published_month`: `juni 2026` →
  `2026-06`; `2026` → the twelve months of 2026; `afgelopen jaar` / `vorig kwartaal` →
  a month set. Relative expressions resolve against request time and are **frozen into
  absolute months in the chip**, so a bookmarked URL keeps meaning what it meant.
- **Residue** = the query minus every matched span. That is what goes to `q`.

Tier 0 handles the measured example completely: `OCW` → publisher chip, `juni 2026` →
month chip, residue `besluiten subsidie` → `q`. **No LLM needed for the exit criterion's
own example.** This is the SRC lesson applied: try the lookup table before the model.

**[build] Three things the implementation had to decide:**

- **Aliases resolve against the live vocabulary, they do not carry the value.** The table
  maps `ocw` → the *keywords* `("onderwijs", "cultuur", "wetenschap")`, and the matched
  vocabulary value is whichever live value contains all of them. Matching zero values, or
  more than one, drops the alias and leaves the word in `q` — ambiguity is never guessed
  at. This keeps the closed world closed even if a publisher is spelled differently per
  source, and makes an alias table that is safe to write ahead of the data.
- **A bare month name without a year is deliberately not understood.** `juni` alone would
  need a year picked for it, and picking one is exactly the guess the closed world exists
  to prevent. It stays a search term. (Negative knowledge; revisit only with evidence
  that users type bare months.)
- **Stranded function words are dropped from the residue.** Removing `OCW` from
  `besluiten van OCW over subsidie` leaves `besluiten van over subsidie`, and the BM25 leg
  ANDs its terms, so the connectors the chips stranded would narrow the search for no
  reason. A closed list (`van de het een over voor …`) is dropped — but **only when at
  least one chip was extracted**, so a query we understood nothing in is passed through
  exactly as typed. Not silent: the result is reported as `q_effective`.

### Tier 1 — constrained LLM parse (opt-in, guarded)

Only invoked when Tier 0 left residue that *looks* filter-bearing and the query is not a
plain keyword search. The call is a **closed-world mapping**, not open generation:

- input: the residue + the enumerated live vocabulary (a few hundred tokens, §2c);
- output: a strict JSON list of `{field, value}` where **`value` must be a member of the
  supplied vocabulary** — validated on return; non-members are dropped, not shown;
- budget-gated and cached by `(dataset, normalized query)`. A civic search UI has heavy
  query repetition; the cache should carry most of the traffic.

Guard conditions, mirroring `extraction-fallback.md`: disabled by default per dataset,
per-request budget, and **suppressed at a systematic-failure rate** — if Tier 1's chips
are being rejected by the user (or dropped by validation) above a threshold, that is an
alias-table gap to fix in Tier 0, not something to keep paying an LLM to paper over.

### Tier 2 — term expansion (narrow, see §6)

Not part of the default path. Only for the vector-less fallback case.

### The chip contract

Every chip is `{kind, field, values, label, source, span, text}`:

- `kind`: `filter` (a real facet term) | `term` (stays in `q`) | `expansion` (§6);
- `field`: the **index** field (`published_month`), not the API param (`month`);
- `values`: **[build] a list, not a single value** — one date expression is one chip
  covering N months (`2026` → twelve). Two chips on the same field OR together into one
  `terms` clause;
- `label`: **always absolute** — a relative expression is shown as the range it froze to
  (`afgelopen jaar` → `2025-08 – 2026-07`), so what will be filtered is what is displayed;
- `source`: `vocab` | `alias` | `date_grammar` | `llm` | `user` — **provenance is
  displayed**, so an LLM-derived chip is visibly distinguishable from a matched one;
- `span`: the character range it consumed, so the UI can show what was understood;
- `text`: **[build]** the matched substring verbatim, so removal is a pure text operation;
- `id`: **[build]** `field:matched text`, case-folded — the handle a removed chip is
  remembered by (`drop_chip=publisher:ocw`). Keyed on the text rather than the span, so a
  dismissal survives the same query being re-parsed and **dies as soon as the user edits
  that part of the query**, which is the right lifetime for "I did not mean that as a
  filter";
- `count`: **[build]** how many hits this chip *alone* would yield — added by the search
  API, not by the parser (§5);
- every chip is **removable**, and removing it puts its span back into `q`.

**[build] A repeated value is consumed once.** `OCW besluiten OCW` yields one chip; the
second occurrence stays a search term rather than being swallowed by a chip the user
cannot see (§7: no silent rewriting).

Chips are the URL state (`ui-inspiration.md` §1: URL-addressable state; saved views as
forkable objects). A chip set that a user has edited **is** a saved view, which is the
same object as a thematic scope's deterministic-metadata predicate
(`thematic-scoping.md`) — a client slice defined in the search bar is one step from a
scope definition. That is the payoff for doing this as structured filters rather than
as query rewriting.

### Zero-result guard

Before returning, the parse is validated against the index: a chip set that yields **0
hits** where the raw query yielded > 0 is **not applied**. It is offered instead ("did
you mean to filter by …?") with the raw results shown. Cheap — we already have counts —
and it makes the failure mode of an over-eager parse *visibly nothing* rather than
*silently empty*. This is the query-side form of the fail-open rule (**S**).

**[build] The guard measures the executed result, not a count probe.** The obvious
implementation — count the chip set with a cheap `size: 0` query and decide before
searching — is *wrong for the dense leg*: a chip set with zero BM25 hits can still have
kNN hits, which is hybrid's whole point (§2a). So the search runs with the chips, and
only if it comes back empty is it re-run raw; the chips are then reported with
`chips_applied: false` and the raw results are shown. Cost is one extra search in exactly
the case that was going to disappoint the user anyway. **A zero that the raw query shares
is left alone**: if both are empty the chips stay applied, because an honest empty result
for the user's intent beats an unfiltered empty one.

**[build] Accepting the offer needs no new server surface.** The UI's "toch toepassen"
turns the offered chips into ordinary facet selections — and explicit facets already beat
the parser (§5), so the filtered (empty) result the user asked for is what they get, with
the facets visible in the panel where they can be undone one at a time.

## 5. Where it lands in the search path

Additive; no new pipeline stage (per the method — new mechanics land inside existing
hooks).

- New `engine/query_parse.py`: pure functions, no I/O except the optional Tier-1 call —
  `parse(q, vocab, now) -> (chips, residual_q)`. Testable without OpenSearch, which is
  how the alias/date grammar gets a regression suite.
- `ui/search_api.py`: new `understand: bool` query param (default on) applied **before**
  `filters` are assembled in `search()`. Parsed chips merge with explicitly-passed facet
  params, and **explicit params always win** — a user who clicked a facet is never
  overridden by a parser.
- Response gains `chips: [...]`, `q_effective`, `understanding_used`,
  `understanding_fallback_reason` — the same degradation-reporting shape as
  `mode_used` / `mode_fallback_reason`.
- `ui/app.js`: renders chips in the query box, editable and removable.

`GET /api/search?q=…&understand=0` must reproduce today's behaviour byte for byte. That
is the regression contract, and it is what makes the feature safe to default on.

**[build] How it landed.** `understand: bool = Query(True)`, applied after the explicit
facet params are assembled and before the search. The four new response keys are added
**only when the parse actually ran** — with `understand=0`, or an empty `q`, the response
key set is exactly the pre-W4 one, which is what `test_search_api_understand.py` pins
(alongside: no vocabulary probe is even issued, and the raw `q` reaches BM25 unchanged).
The vocabulary comes from a dedicated size-500 aggregation (wider than the facet panel's
top slice — the parser matches the whole vocabulary), cached per index for 300 s.
Parsing is **fail-open**: if the vocabulary probe or the parse raises, the raw query runs
and the reason is reported in `understanding_fallback_reason`.

Measured on the live stack after the build, `hybrid_rerank`, the §2b example:
**`total` 101 → 19**, all `ministerie van OCW` / `2026-06`, with the top hit unchanged.
The ordering the rerank stack already got right is preserved; what changed is that the
result set is now the user's intent, and is countable and addressable.

**[build] Steps 2–3 added, in the same shape:**

- **`chips_applied`** — a fifth understanding key, and the one the UI keys off. `true`
  normally, `false` when the zero-result guard held the chips back. Kept separate from
  `understanding_used` (which stays "the parse ran"): a suppressed chip set is still a
  parse that succeeded, and the distinction is what lets the UI *offer* it.
- **`drop_chip`** (repeatable, `field:text`) — a chip the user removed. Handled at exactly
  the same point as the explicit-facet override: the chip is dropped and `residual()`
  hands its words back to `q`, so removal is one code path, not two.
- **Chip counts** come from one extra `size: 0` filter-aggregation round trip: each chip
  counted against the *residual* query plus the explicitly-passed facets, so the number is
  that chip's own narrowing power rather than the set's. **Decoration, never a gate** — a
  failed probe leaves the chips countless, not unfiltered. Caveat worth knowing: these are
  exhaustive BM25 counts, so in a hybrid run they are not comparable with `total`, which
  is a pooled number (`max(bm25 total, pool size)`). They are comparable *with each other*,
  which is what disclosure needs — and a `0` on one chip is exactly the explanation of why
  the guard fired.
- **`ui/app.js` renders the chip bar** under the search box: field icon, absolute label,
  provenance word (`gevonden` / `afkorting` / `datum` / `AI`), count, and `×`. Provenance
  is styled as well as labelled, so a future Tier-1 chip is visibly not a matched one. The
  results header reports `q_effective`, so what was actually searched for is never hidden.
- **The URL carries the whole state** — query, dismissed chips, facets, mode, page — read
  on load and rewritten after every search. This is what makes the "relative dates are
  frozen to absolute months" rule (§7) worth anything: a shared link keeps meaning what it
  meant. It is also the first half of saved-views-as-forkable-objects.
- **Not built: editing a chip's *value* in place.** It would need a rewrite param
  (`chip=publisher:ocw=EZK`) because the span must stay consumed rather than returning to
  `q` — and the facet panel plus a removable chip already cover the same ground. Recorded
  as negative knowledge; revisit if the facet panel proves too far from the chip bar.

## 6. Term expansion — what survives, and why

§2a kills expansion for the hybrid path. It survives in exactly one place: **queries
that cannot use the dense leg.**

That set is real today. `_hybrid_search()` returns `None` — falling back to pure BM25 —
when the query embedder is unavailable (missing `--extra embed`, model download failure)
or the index has no vectors. The 208 woo archive members + 17 haarlem office docs from
`3e72fce` are **bm25-searchable but vector-less** (HANDOFF open thread #2), and on those
items `tandartsklacht` really does return nothing.

So Tier 2 is: **when `mode_used == "bm25"` and the query contains an unmatched compound,
add decompounded parts as `should` clauses** (never `must` — broadening, not
re-targeting), surfaced as `kind: expansion` chips so the user sees why extra results
appeared. It is a degradation-path feature, and it should be built **last**, after
measuring whether the vector-less cohort still exists once open thread #2 (pod-embed +
re-project) lands. It may well delete itself.

## 7. What we deliberately do NOT do (negative knowledge)

- **No Dutch decompounder / synonym expansion on the hybrid path.** Measured redundant
  (§2a). Reopening this needs a measurement that beats `hybrid_rerank`, not an intuition.
- **No index-time synonym filter.** It would freeze the vocabulary into the mapping and
  force a reindex per change — the same trap as chunk-embedding params ("a paid
  decision", README). Query-side alias tables are rows.
- **No free LLM generation of filter values.** Values must be members of the live
  vocabulary (§4 Tier 1). A filter value the corpus does not contain is a bug, not a
  suggestion.
- **No silent query rewriting.** Every transformation is a visible, removable chip with
  displayed provenance. The user must be able to see, and undo, everything we inferred.
- **No relevance delegation to source-side search** (`failure-modes.md` API-3).
- **Relative dates are never stored relative.** Frozen to absolute months in the chip.

## 8. Build order

1. ~~**Tier 0 + chip contract + `understand=0` regression contract.**~~ **DONE
   2026-07-24.** `engine/query_parse.py` (pure: `parse()`, `residual()`, `chip_filters()`,
   the alias table, the Dutch date grammar) + the `understand` param in
   `ui/search_api.py`. 29 tests, none needing OpenSearch. Live result in §5.
2. ~~**Zero-result guard + counts-driven chip disclosure.**~~ **DONE 2026-07-25.**
   The guard measured on the executed result (`chips_applied`, one raw re-run), per-chip
   counts from one `size: 0` filter-agg probe, and `drop_chip` for removal. 8 tests.
3. ~~**UI chip rendering** (`ui/app.js`) — editable, removable, provenance-labelled.~~
   **DONE 2026-07-25.** Chip bar + provenance styling + "toch toepassen" for an offered
   set + full URL state. Verified on the live stack in all three states (applied /
   offered / removed).
4. **Tier 1 constrained LLM parse**, off by default, per-dataset opt-in, cached and
   budget-gated. Only if Tier 0's alias table demonstrably cannot cover observed queries
   — measure first.
5. **Tier 2 expansion**, only if a vector-less cohort still exists after open thread #2.

Exit criterion #4 is met at step 3, without any LLM in the query path. That is the
intended outcome, not a compromise: the roadmap assumed a cheap-LLM parse was the
mechanism, and the measurement says a lookup table plus a date grammar gets there.
**Reached 2026-07-25** — steps 4 and 5 are now optional upside, not remaining work.

## 9. Open questions

- **Alias-table provenance.** Ministry abbreviations (OCW, LVVN, EZK, JenV) are stable
  and worth hardcoding, but per-source aliases (college names, gemeente abbreviations)
  ought to be *manifest-declared* — which makes them something the W6 onboarding agent
  should draft during onboarding. Not yet designed; flagged for the W6 UI slice.
- **Cross-dataset search.** Chips are resolved against one dataset's vocabulary; the
  `dataset` param currently selects one index. Multi-source search (the opub-style
  dataset facet, `ui-inspiration.md` §2) needs a vocabulary union and a rule for values
  that exist in one dataset only. Deferred with multi-source search itself.
- **Does the user actually want the filter applied, or just the ordering?** §2b shows
  rerank already orders correctly. It is possible that "narrow to 3" is *worse* for some
  intents than "101 well-ordered". The zero-result guard covers the extreme; the general
  case wants an A/B on the live UI, which is a Stage-C-adjacent measurement.
- **Where Tier 1 runs.** Same open question as W6's runtime hosting — no LLM client
  exists in `engine/` today (only the Voyage rerank HTTP client in `engine/retrieval.py`).
  Tier 1 would be the second such client; worth a shared small-model helper rather than
  two bespoke ones.
