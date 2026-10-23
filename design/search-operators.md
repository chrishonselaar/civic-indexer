# Search operators — the boolean half of the query surface (W11(a), 2026-07-28)

**Status: BUILT** — `engine/query_ops.py`, `ui/search_api.py`, `ui/app.js` +
`index.html` + `styles.css`, `tests/test_query_ops.py` (50) +
`tests/test_search_operators.py` (30). Verified against the live cluster
(49 399 documents, four corpora) and rendered in headless Chrome in six states.
**§9 (chips × operators) and §10 (phrase-aware highlighting) were built the same day**,
§9 applied-by-default per the admin's call on §9.3.

`idee.md` promises *"fulltext OpenSearch search met operators etc"*, and names the
alternative it exists to beat: source UIs that *"ondersteunen geen geavanceerde
operators"*. Until today the query path was one `multi_match` with `operator: and` —
so the promise was unkept and the named weakness was shared. This is the smaller,
older half of the query surface; W4 built the newer half (free text → facet chips)
first, on purpose, because that is the one a resident cannot work around. An
archivist, a journalist or a griffier *can* work around a missing phrase search, and
hates having to.

---

## 1. The grammar

Precedence `NOT > AND > OR`; juxtaposition means AND, which is what the engine already
did. Keywords are **uppercase only**, Dutch and English:

| syntax | means | example |
|---|---|---|
| `"…"` | phrase — these words, in this order | `"hoger onderwijs"` |
| `AND` · `EN` · (a space) | both | `subsidie EN onderwijs` |
| `OR` · `OF` | either | `subsidie OF vergunning` |
| `NOT x` · `NIET x` · `-x` | exclude | `subsidie -onderwijs` |
| `( … )` | grouping | `(subsidie OF vergunning) EN gemeente` |
| `veror*` | wildcard at the end of a word | `veror*` |

**Why uppercase-only.** `en` and `of` are ordinary Dutch words — so ordinary that
`query_parse.STRANDED_WORDS` already lists both, because W4 has to strip them out of a
residual query. Accepting them lowercase would turn the corpus's natural phrasing into
syntax. The residual risk is an all-caps query where `EN` was meant literally, and it is
mild: `EN`/`AND` is the conjunction the engine applies to a bare two-word query anyway,
so only an all-caps `OF`/`NIET` can surprise — and §5 shows the reader exactly how their
query was read.

**`-` is negation only when it is glued to its operand.** `Wob - besluit` is
punctuation and stays punctuation; `COVID-19` and `e-mailadres` are words. A dash is
detected on a whitespace boundary followed by a non-space (`engine/query_ops.py:_DASH`),
which is the difference between a search operator and a hyphen.

## 2. Three things deliberately not built

* **`?` is not a single-character wildcard.** Dutch queries end in a question mark far
  more often than anyone wants a one-character wildcard, and quietly turning
  `wat is dit?` into a pattern search is worse than not having the feature at all.
* **No leading wildcard.** `*wet` is a full scan over a content field holding OCR'd
  PDFs. The leading star is stripped and the strip is reported, rather than accepted
  and paid for.
* **No `field:value` syntax.** W4's chips already do that, and do it better: they
  resolve against the *live* facet vocabulary, so they cannot name a value the corpus
  lacks (`query-understanding.md` §4, closed world). A second, hallucination-prone way
  to say the same thing is not a feature.

Also absent, for now: fuzzy (`~`), proximity (`"a b"~5`), field boosts. None is
promised anywhere and each needs its own answer to "what does the dense leg do with
it?" (§6).

## 3. The no-change contract

`query_ops.has_operators(q)` is a cheap pure test, and **false means the query path
does not change at all**: same clause, same fields, same weights, same response keys.
The `operators` key is *absent* from the response for such a query, exactly as
`facets_scope` is absent in bm25 mode — the same convention, for the same reason.

For a query that *does* contain operator syntax, `&operators=0` reproduces the
pre-W11 literal behaviour, the way `&understand=0` reproduces the pre-W4 one. Both
switches exist so that "the new thing broke my search" is a five-second experiment
rather than an argument.

Everything the parser cannot make sense of is **fail-open**: `used=False`, the caller
searches the text literally, and the response says so with a reason. `()`, `)))`,
`NOT` on its own, `"`, `( ( (` are all pinned as tests. Parsing never gates search.

## 4. Why chips may never be applied by editing the query text

*(Written when chips were switched off entirely for operator queries; **§9 now composes
them**, and this section is why it does so the way it does. Kept in full, because the
trap it describes is one line of `query_parse.py` away at all times.)*

The obvious way to combine the two features is to let W4 extract its chips and hand the
leftover text to the operator parser. That is **actively unsafe**, and the proof is two
lines of existing code: chip extraction removes the chip's span from the query
(`query_parse.residual`) and then strips the stranded connectives — a set that contains
**`en` and `of`**, compared case-folded. So a chipped `subsidie EN onderwijs 2026` would
have its year lifted out as a filter *and its `EN` deleted as a stray connective*, and
the user would get a silently different boolean than the one they typed. No error, no
empty result, nothing to notice.

The rule that follows is: **an operator query keeps every word it was typed with, and
chips are applied by pruning the parse tree — never by editing the text.** In the tree,
an operator is a *node* and never a leaf, so nothing that removes words can reach one.

Until §9 was built (the same day), the conservative form of this rule was in force:
chips simply did not run for an operator query, and the response said so. That state is
still reachable and still correct — it is what happens when a query has no
root-conjunctive term to read as a filter (`"…" alles tussen aanhalingstekens`), and the
response reports
`understanding_fallback_reason: "operator query with no free terms to read as filters"`.

## 5. What the response says

```json
"operators": {
  "used": true,
  "parsed": "(subsidie OR vergunning) AND gemeente AND NOT onderwijs",
  "warnings": ["aanhalingsteken niet afgesloten — de rest is als één zin gezocht"],
  "exclusion_in_or": false
}
```

…plus `"consumed_by_chips": true` in the one case §9 can produce where every search term
became a filter and there is no text left to search.

`parsed` is the query rendered back from the parse tree, with **grouping made
explicit**: `a AND b OR c` renders as `(a AND b) OR c` even though the precedence rule
already determines it. The string exists so a reader can check that the engine read
their query the way they meant it, and "you can infer the grouping from the documented
precedence" is not that.

`warnings` are repairs, in Dutch, because a repair changes the result set: an
auto-closed quote is a different search than the one that was typed.

`exclusion_in_or` is a property of the *parse*, reported separately from
`mode_fallback_reason` (§6) on purpose. That field is a machine reason in English, like
every other machine reason in this API; the UI phrases its own Dutch sentence off the
boolean instead of pattern-matching English prose that a later edit would reword.

## 6. The dense leg — where operators and hybrid retrieval actually collide

Hybrid search fuses a BM25 leg with a kNN leg (`retrieval.rrf_fuse`). BM25 honours
boolean structure exactly. A vector does not: there is no embedding of "not".

Three rulings, in increasing order of how much they mattered:

1. **The embedder gets the words, not the syntax.** `subsidie -onderwijs` is embedded
   as `subsidie`. Embedding the raw string asks the vector space for the excluded word
   as loudly as for the wanted one. The reranker gets the same cleaned text, for the
   same reason — a cross-encoder reads a query as natural language.
2. **A conjunctive exclusion becomes a filter on the dense leg too.** Every `NOT`
   reachable through `AND` nodes only holds for the whole query, so it is handed to
   kNN as a `must_not` filter. Without this the kNN channel quietly returns exactly
   what the user excluded — the one failure mode a reader would (rightly) call a bug
   the moment they saw it.
3. **A `NOT` under an `OR` drops the whole search to BM25, and says why.**
   `a OR NOT b` admits documents containing `b`, so no filter expresses it. The
   available options were to approximate it (over-exclude on the dense leg, i.e. lie
   in the safer direction), or to step aside. BM25 answers such a query *exactly*, so
   stepping aside costs recall and buys correctness, and the response reports
   `mode_used: "bm25"` with the reason. Approximating would have been invisible.

## 7. Measured on the live corpus (2026-07-28, all four sources, `mode=bm25`)

| query | hits | what it shows |
|---|---:|---|
| `subsidie` | 220 | the baseline |
| `hoger onderwijs` | 120 | today's AND-of-terms |
| `"hoger onderwijs"` | **12** | the phrase is a real 10× narrowing, not a re-ranking |
| `subsidie OF onderwijs` | 474 | union, > either side |
| `subsidie EN onderwijs` | 85 | — |
| `subsidie -onderwijs` | **135** | and 135 + 85 = 220 exactly: the exclusion partitions the baseline |
| `veror*` | 825 | wildcard reaches `verordening`, `verordeningen`, … |
| `(subsidie OF vergunning) EN gemeente` | 315 | grouping |

**What a quoted phrase actually means here** (measured while designing §10, and worth
stating because users read quotes as "literal"): the `content` field runs the Dutch
analyzer, which **stems and drops stopwords with a position increment**. `"college van
beroep"` analyses to `colleg _ beroep` — two stems with one position between them — so it
also matches *"college **hoger** beroep"* and *"college **het** beroep"*, both verified in
`civic-tuchtrecht`. A phrase is therefore "these stems, in this order, with one slot per
elided stopword", not a string match. It is still a real narrowing (12 vs 120 above);
it is just looser than the quotes suggest. Making it literal would need an unstemmed
sub-field — a mapping change and a full re-projection — and nobody has asked.

The dense-leg ruling (§6.2) was verified end to end rather than asserted: for
`subsidie -onderwijs` in `mode=hybrid`, **0 of the top 8 documents contain "onderwijs"**
in their full text, against **1 of 8** in the unfiltered `subsidie` control. A small
control number, honestly reported: the filter is doing real work, and the work it does
is not large. What matters is that it is not *zero*, because an exclusion that leaks
even once is not an exclusion.

## 8. Open threads this leaves

1. ~~**Composing chips with operators**~~ — **designed and built, §9** (2026-07-28),
   applied-by-default.
2. ~~**Phrase-aware highlighting**~~ — **measured and fixed, §10** (2026-07-28).
3. **Operators on the `related` and chunk paths** — neither takes free text today, so
   there is nothing to parse yet.
4. **`?`, fuzzy and proximity** (§2) — none is promised; each needs a §6 answer first.
5. **A literal phrase mode** (§7): quotes match stems with stopword slots. An
   unstemmed sub-field would make them literal, at the cost of a mapping change and a
   full re-projection. Nobody has asked; recorded so the looseness is not a surprise.

---

## 9. Composing chips with operators — **BUILT 2026-07-28**

> **Status: built, applied-by-default** (the §9.3 choice, taken by the admin).
> `engine/query_ops.py` (`free_spans`, `mask_outside`, `prune`), `ui/search_api.py`
> (`_understand_operators`, `_chip_is_clean`), `ui/app.js`, 10 endpoint tests. Two
> things the build corrected in this design are marked **[build]** below.

**The case that motivates it.** `"college van B&W" 2026`: the phrase must survive
untouched, and the year should still become a `published_month` chip, exactly as it
would in a query without quotes. Before this, such a query kept every word and got no
chip at all (§4) — safe, but poorer than it needs to be, and asymmetric: chips applied
themselves right up until the user added one quote.

### 9.1 The rule

> **A chip may only be derived from a root-conjunctive bare term.**

That is: a `term` leaf that is a direct child of the root `AND` (or the whole query,
when the query is one bare run) — never a phrase, never a wildcard, never a negated
term, and never anything inside a group or an `OR` branch.

It is the *same* rule §6.2 already applies to exclusions, for the same reason, and that
is the argument for it: **a chip is a global filter, so it may only come from a globally
conjunctive position.** `subsidie OF (onderwijs 2026)` must not yield a `2026` filter —
that would silently drop every `subsidie` document published in another year, which is
precisely the class of error a filter derived from ranking-only structure produces.

Two consequences worth stating plainly, because they are what "composition" does *not*
mean here: text inside quotes is never mined for chips, and neither is text inside a
group. A user who wants those as filters clicks the facet.

**The rule is not ours, and that is the good news: it is predicate pushdown.** A query
optimiser may push a conjunct through, but never a predicate out from under a
disjunction. We arrived at it twice in two days without naming it — once for handing
exclusions to the kNN leg (§6.2), once for chips — and a rule that turns up
independently in two places, with a fifty-year-old correctness argument already behind
it in someone else's field, is much likelier to be right than one we invented. Anyone
extending this surface (fuzzy, proximity, `field:value`, or the mixed composition §9.1
still refuses) should reach for the same test first: *would a database be allowed to
push this predicate out of here?*

Nothing else in this document is novel, and it is worth saying so: removable filter
pills, lifting attributes out of prose, and a boolean grammar over a text field are all
standard practice. What is uncommon is having the inferred chips and a **user-authored**
boolean expression share one input, which is the situation that makes the pushdown
question load-bearing rather than academic.

### 9.2 The mechanism — prune a tree, never edit a string

The reason §4's naive version was unsafe is that it works on the query *text*, where
`residual()` deletes stranded words and `en`/`of` case-fold onto the operators. Working
on the parse tree removes that whole class of bug **by construction**: an operator is a
*node*, never a leaf, so nothing that removes words can reach one.

1. **Spans on leaves.** The tokenizer already computes offsets; carry them onto the
   `term`/`phrase`/`wild` nodes. (~15 lines, no behaviour change.)
2. **`Parsed.free_spans`** — the spans of the root-conjunctive bare terms, per §9.1.
3. **Mask, then reuse the existing parser unchanged.** Build a same-length copy of `q`
   with every character outside `free_spans` replaced by a space, and hand *that* to
   `query_parse.parse()`. Spaces are word boundaries, so the vocabulary scan and the
   date grammar simply cannot see protected text, and every chip's `span` still indexes
   into the original query. **Verified 2026-07-28** on
   `besluiten van OCW over "vaste lasten" juni 2026 -concept`: chips came back for
   `OCW` and `juni 2026` only, with spans that map back exactly, and the operator parse
   was untouched. `query_parse.py` needs **no change at all**.
4. **Drop chips that do not align with a free term's boundaries.** A vocabulary value
   can start mid-token (`query_parse._W` treats `-` as a boundary, so `hoger-onderwijs`
   could yield a chip covering half a token). **[build]** The first implementation
   tested this against *whitespace* in `q` and was wrong: a term can sit flush against
   a parenthesis, so `(juni 2026)` — a perfectly good date — was silently rejected. The
   test is against the leaf spans, which is the thing actually being pruned.
5. **Prune the tree**: remove the leaves covered by a chip span, plus the leftover
   `STRANDED_WORDS` leaves of the same free run (`van`, `over` — what `residual()` does
   today, but only ever to leaves). Recompile the clause from the pruned tree.
6. **An empty tree is not an error.** If every root leaf was a chip, the search becomes
   empty text + filters — which is exactly what W4 does today for a fully-understood
   query, and is the right answer for `"…" juni 2026` reduced to its date.
7. `q_effective` for display is the **canonical rendering of the pruned tree**, so what
   the reader is told was searched is generated from the thing that was searched.

**[build] A chip covers leaves, not a leaf.** `juni 2026` is *one* chip over *two*
leaves, and a publisher name is one chip over four or five. The first `prune()` matched
leaf spans against chip spans by equality and therefore removed nothing; it must be
containment. The visible symptom was mild and misleading — the search still worked, the
filter still applied, and only the leftover words in `q_effective` said anything was
wrong — which is a good argument for the test that now pins it.

The chip-count probe and the zero-result guard then take the pruned and unpruned parses
respectively — the same two-search dance as today, with a `Parsed` instead of a string.
The guard needs one extra line: when it reverts to the raw query, the `operators` block
must revert with it, or the panel would describe a pruned query nobody executed.

**When every term becomes a filter** (`(juni 2026)`), the search is empty text + filters
— legitimate, and exactly what W4 does for a fully-understood plain query. But the
response then has `used: false` with no clause, which a UI would reasonably render as
*"operators broken"*. So it carries `consumed_by_chips: true` and the panel says
*"Alle zoektermen zijn als filter gelezen"*. **[build]** This state was not in the
design; it fell out of the first live run.

### 9.2b Measured on the live corpus (2026-07-28, `mode=bm25`)

| query | chips lifted out | what actually ran | hits |
|---|---|---|---:|
| `"college van beroep" 2026` | `published_month 2026` | `"college van beroep"` | 55 |
| `"vaste lasten" besluiten van OCW over subsidie juni 2026` | `publisher OCW`, `published_month juni 2026` | `"vaste lasten" AND besluiten AND subsidie` | 0 |
| `besluiten -OCW subsidie juni 2026` | `published_month juni 2026` only | `besluiten AND NOT OCW AND subsidie` | 70 |
| `subsidie OF (onderwijs juni 2026)` | **none** — §9.1 | unchanged | 280 |
| `(juni 2026)` | `published_month juni 2026` | *(filters only)* | 1 148 |

Row 3 is the rule earning its keep twice over: the negated `OCW` is **not** turned into
a publisher filter (that would include exactly what the user excluded), while the date
in the same query still is. Row 4 is the rule refusing a filter that would have dropped
every `subsidie` document from another year.

### 9.3 The one product choice

Applying chips rewrites what runs, and for an operator query the user was being
deliberate. Two defensible defaults:

* **Applied** (recommended) — consistent with every other query; each chip carries its
  `×` to put the words back, the canonical shows the result, and nothing about the
  operator structure can change. The precedent is W4's own path, which has removed
  chip words from `q` since 2026-07-25 without complaint.
* **Offered** (`chips_applied: false`) — the chips appear with *"Toch toepassen"* and
  nothing happens until clicked. Safer-feeling, but it costs an extra click on every
  such query and the machinery is identical, so the only thing it really buys is a
  deferral of this decision.

Recommendation: **applied**, because the asymmetry — chips auto-apply for a plain query
but not for one with a quote in it — is itself a surprise, and §9.1 makes the operation
provably structure-preserving. **Decided 2026-07-28 (admin): applied.**

### 9.4 What it cost, and what §4 now reads as

Built in one sitting: ~90 lines in `query_ops` (leaf spans, `free_spans`,
`mask_outside`, `prune`, `_finish`), ~70 in `search_api`, 10 endpoint tests. No mapping
change, no re-projection, no new dependency, and **`query_parse.py` was not touched** —
the masking step was the whole point of that.

§4 of this document still stands as written, and should be read as the reason §9 works
the way it does rather than as a superseded position: chip extraction must never edit
the query *text*, because `residual()` strips `en`/`of` and would delete a user's
operators. §9 does not solve that problem — it **avoids** it, by never producing a
string to strip. What changed is not the risk assessment; it is that the tree gives us
a representation where the risk cannot arise.

Three things remain deliberately not composed, and all three are §9.1 doing its job:
text inside quotes, inside groups, and inside `OR` branches or negations. A user who
wants those as filters clicks the facet.

---

## 10. Phrase-aware highlighting — measured, and fixed (2026-07-28)

**Built.** `_highlight()` switches the `content` field to the **`plain`** highlighter
when, and only when, the parse contains a phrase.

### 10.1 What was wrong

OpenSearch's default `unified` highlighter selects passages by **per-term score and
ignores adjacency**, so for a phrase query it happily returns a fragment containing one
common word of the phrase and none of the others. The reader gets three separately
marked words and no idea why the document is a hit.

Measured across all four corpora, same query, only the highlighter differing —
*fragments that actually contain the phrase*:

| phrase | `unified` (before) | `plain` (after) | latency before/after |
|---|---:|---:|---:|
| `"college van beroep"` | **16 / 50** | **48 / 50** | 75 ms → 93 ms |
| `"hoger onderwijs"` | 8 / 12 | **12 / 12** | 60 ms → 80 ms |
| `"vaste lasten"` | 17 / 17 | 17 / 17 | 399 ms → 599 ms |
| `"raad van discipline"` | 50 / 50 | 50 / 50 | 20 ms → 21 ms |
| `"hoger beroep"` | 50 / 50 | 50 / 50 | 20 ms → 21 ms |

So the defect is real but **conditional**: it appears when the phrase's words are
individually common in the document, and is invisible otherwise — which is exactly why
it survived. The cost is ~20 ms typically and ~200 ms on an already-slow query, paid
only by phrase queries.

**The residual 2/50 is not a highlighter failure.** Both documents genuinely lack the
literal phrase; they matched because the analyzer elides stopwords (§7) — they contain
*"college het beroep"* and *"college hoger beroep"*. The highlighter was right and the
query was loose.

### 10.2 Why not the other two options

* **`fvh`** (fast vector highlighter) is the textbook phrase-accurate choice, and it
  needs `term_vector: with_positions_offsets` in the mapping — a mapping change, a full
  re-projection of ~49 400 documents, and a materially larger index. `plain` reaches
  48/50 for zero of that. Revisit only if 48/50 ever proves insufficient.
* **Post-processing the marks** in the API cannot work: the *passage selection* is what
  goes wrong, not just the marking. Dropping non-phrase marks would leave fragments
  with no highlight at all.

### 10.3 The load-bearing detail

`max_analyzer_offset: 999_999` stays on both paths. Verified on a **3 158 061-character**
document: without it, *both* highlighters fail the **entire search**, not just that
document's excerpt. The comment in `_highlight()` says so, because a future reader
tidying up an "unnecessary" cap would take the search down for the largest documents in
the corpus — the ones the OCR drain exists to produce more of.
