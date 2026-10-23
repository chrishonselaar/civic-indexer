"""Search API for the walking-skeleton UI.

Lean adaptation of src-uitspraken-mcp/search-ui/backend/search_api.py to the
civic envelope indices. Ranking modes (design/README.md, settled):
  bm25          — BM25 + facets (always available)
  hybrid        — BM25 + dense bge-m3, RRF-fused top pool
  hybrid_rerank — hybrid pool reordered by Voyage rerank-2.5 (default)
Hybrid modes degrade gracefully to bm25 when the embed extra / vectors /
API key are missing; the response reports `mode_used`.

Run:  uv run uvicorn ui.search_api:app --port 8080
"""

import os
import time
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from engine import exposure, query_ops, query_parse, retrieval
from engine.project import mappings, os_client
from ui import monitor, onboard

HYBRID_POOL = 100  # candidates per channel; fused pool is reranked/paginated
VOCAB_TTL_S = 300  # facet vocabulary changes at crawl speed, not query speed

_embedder_error: str | None = None


def _embed_query(q: str) -> list[float] | None:
    """Lazy bge-m3 query embedding; remembers a hard failure and stops retrying."""
    global _embedder_error
    if _embedder_error:
        return None
    try:
        from engine import embed

        return embed.embed_query(q)
    except Exception as e:  # missing extra, model download failure, ...
        _embedder_error = str(e)
        return None


def _index(dataset: str | None) -> str:
    """Search target for a `dataset=` parameter.

    Omitted means *every* corpus (design/cross-corpus-search.md): the sources are
    separate indices with one shared mapping, so searching across them is a
    query-time union. `dataset=<id>` still narrows to a single index, which is what
    the projection's own tests and any single-source caller want.
    """
    return mappings.doc_index_target(dataset)


UI_DIR = Path(__file__).parent

app = FastAPI(title="civic-indexer search")

# Search-engine posture of the UI itself (admin decision 2026-09-08, alongside the
# corpus choice in funding/demo-bouw-ontwerp.md §6.2): the demo host carries T2 full
# text (tuchtrecht concerns named professionals), so the *app* must not be indexed by
# search engines even though a reader may search it. Two mechanisms, because crawlers
# honour different ones: `/robots.txt` disallows everything, and every response carries
# `X-Robots-Tag: noindex, nofollow`. `/bot` stays reachable — the User-Agent promises
# it — a disallow only stops indexing, not fetching. Set CIVIC_ROBOTS_INDEX=1 to lift
# it (a non-demo deployment with T0/T1 corpora only, for instance).
ROBOTS_NOINDEX = os.environ.get("CIVIC_ROBOTS_INDEX", "0") != "1"


@app.middleware("http")
async def _robots_header(request, call_next):
    response = await call_next(request)
    if ROBOTS_NOINDEX:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get("/robots.txt")
def robots_txt():
    body = "User-agent: *\nDisallow: /\n" if ROBOTS_NOINDEX else "User-agent: *\nAllow: /\n"
    return PlainTextResponse(body)
osc = os_client.client()
app.include_router(monitor.router)  # read-only pipeline dashboard (roadmap W5)
app.include_router(onboard.router)  # agent onboarding surface (roadmap W6)


@app.get("/")
def root():
    return FileResponse(UI_DIR / "index.html")


@app.get("/styles.css")
def styles():
    return FileResponse(UI_DIR / "styles.css", media_type="text/css")


@app.get("/app.js")
def appjs():
    return FileResponse(UI_DIR / "app.js", media_type="text/javascript")


@app.get("/bot")
def bot_page():
    """Operator-facing crawler info page — the `+URL` in engine.config.USER_AGENT.

    Keep this route reachable wherever the UI is deployed: the User-Agent promises it.
    """
    return FileResponse(UI_DIR / "bot.html")


@app.get("/bot.css")
def bot_css():
    return FileResponse(UI_DIR / "bot.css", media_type="text/css")


@app.get("/vertrouwen")
def vertrouwen_page():
    """Demo screen 4: models, data location, exposure tiers, takedown, harvest posture.

    Prose only — every claim on it is enforced elsewhere (engine/exposure.py,
    engine/suppress.py, the manifests' policy blocks); the page states, it does not
    decide. Its open-questions block is load-bearing: an accountability page that
    carries only the good news is not one.
    """
    return FileResponse(UI_DIR / "vertrouwen.html")


@app.get("/bewijs")
def bewijs_page():
    """Demo screen 2 (funding/demo-bouw-ontwerp.md): the external comparison, readable.

    Static prose assembled from experiments/external_comparison/ — no new measurement,
    and deliberately no rerank figure (the measured one came from a rejected model).
    """
    return FileResponse(UI_DIR / "bewijs.html")


@app.get("/monitor")
def monitor_page():
    return FileResponse(UI_DIR / "monitor.html")


@app.get("/monitor.css")
def monitor_css():
    return FileResponse(UI_DIR / "monitor.css", media_type="text/css")


@app.get("/monitor.js")
def monitor_js():
    return FileResponse(UI_DIR / "monitor.js", media_type="text/javascript")


@app.get("/onboard")
def onboard_page():
    return FileResponse(UI_DIR / "onboard.html")


@app.get("/onboard.css")
def onboard_css():
    return FileResponse(UI_DIR / "onboard.css", media_type="text/css")


@app.get("/onboard.js")
def onboard_js():
    return FileResponse(UI_DIR / "onboard.js", media_type="text/javascript")


@app.get("/health")
def health():
    return {"ok": osc.ping(), "index": mappings.ALL_DOC_INDICES}


@app.get("/api/stats")
def stats():
    return {
        "total_docs": osc.count(index=mappings.ALL_DOC_INDICES)["count"],
        # Whether THIS deployment can rerank at all — a key, not a policy. The policy
        # half (which sources opted in) is per query and comes back as `rerank_blocked`.
        # The UI offers a reranked mode in a dropdown, so a deployment without a key
        # would otherwise name a mode it cannot run; on the demo host that absence is
        # deliberate (funding/demo-bouw-ontwerp.md §2).
        "rerank_available": retrieval.rerank_available(),
    }


@app.get("/api/datasets")
def datasets():
    """The corpora searchable right now, counted off the indices themselves.

    Read from the projection rather than from `manifests/`, deliberately: what a
    reader can search is what has been projected, and a manifest that exists but was
    never run would otherwise show up as an empty corpus with no way to tell.
    """
    body = {
        "size": 0,
        "aggs": {
            "source_id": {
                "terms": {"field": "source_id", "size": 100},
                "aggs": {"exposure": {"terms": {"field": "exposure", "size": 5}}},
            }
        },
    }
    try:
        resp = osc.search(index=mappings.ALL_DOC_INDICES, body=body)
    except Exception as e:
        return JSONResponse({"error": str(e), "datasets": []}, status_code=503)
    incomplete = _shard_failure(resp)
    return {
        "datasets": [
            {
                "source_id": b["key"],
                "docs": b["doc_count"],
                # One tier per source in practice; a list because the index is the
                # authority and a mid-re-projection index can legitimately hold two.
                "exposure": [t["key"] for t in (b.get("exposure") or {}).get("buckets", [])],
            }
            for b in resp["aggregations"]["source_id"]["buckets"]
        ],
        # A corpus missing from this list because its shard failed would look
        # exactly like a corpus that was never onboarded.
        **({"incomplete": incomplete} if incomplete else {}),
    }


def _highlight(parsed: query_ops.Parsed = query_ops.NO_OPERATORS) -> dict:
    """The excerpt spec. Phrase queries switch highlighter — measured, see §10.

    The default `unified` highlighter picks passages by per-term score and ignores
    adjacency, so for `"college van beroep"` only **16 of 50** returned fragments
    actually contained the phrase: the reader is shown three separately marked common
    words and left to wonder why the document is a hit. The `plain` highlighter re-runs
    the real query against each hit and gets **48 of 50** — at no re-index and no
    measurable latency cost, because `max_analyzer_offset` caps the work either way.

    It is switched on *only* for a query that contains a phrase. Every other query keeps
    the highlighter, the fragment choice and the excerpts it had before — same no-change
    contract the operators themselves keep (§3).
    """
    spec: dict = {
        "fragment_size": 240,
        "number_of_fragments": 1,
        # Huge OCR'd docs exceed the index's 1M analyzed-offset cap; truncate
        # highlighting instead of failing the whole search (OpenSearch spells this
        # max_analyZER_offset, unlike ES). Verified 2026-07-28 that `plain` honours it
        # too: without it, a 3.1M-character document fails the *entire* search on both
        # highlighters, so this is load-bearing, not defensive.
        "max_analyzer_offset": 999_999,
    }
    if parsed.has_phrase:
        spec["type"] = "plain"
    return {
        "fields": {"content": spec},
        "pre_tags": ["<mark>"],
        "post_tags": ["</mark>"],
    }


# One declaration, two consumers — the aggregation OpenSearch computes over a BM25
# result set, and the tally we compute in Python over a fused hybrid pool. Kept as a
# single spec deliberately: two hand-maintained lists drift, and a facet present in
# one ranking mode but not the other is PROJ-3 all over again.
#
# The corpus switcher is a facet, not a dropdown: a cross-corpus search should show
# what it found where, and narrowing to two of four sources is the same gesture as
# narrowing to two publishers.
FACET_FIELDS: dict[str, dict] = {
    "source_id": {"size": 50},
    "publisher": {"size": 30},
    "file_type": {"size": 10},
    "disposition": {"size": 10},
    # Months read as a timeline, so they are ordered by key, not by count.
    "published_month": {"size": 24, "order": "key_desc"},
}


def _facet_aggs() -> dict:
    aggs = {}
    for name, spec in FACET_FIELDS.items():
        terms = {"field": name, "size": spec["size"]}
        if spec.get("order") == "key_desc":
            terms["order"] = {"_key": "desc"}
        aggs[name] = {"terms": terms}
    return aggs


def _bm25_query(q: str, filters: list, parsed: query_ops.Parsed | None = None) -> dict:
    """The text half of a search. Operators (W11(a)) replace the `must`, nothing else.

    `parsed` is passed in by the caller that already parsed the query, so the clause we
    search with is the same object the response describes; omitted, it is derived here.
    A query with no operator syntax takes the pre-W11 branch untouched — that is the
    whole no-change contract, and `design/search-operators.md` §3 states it.
    """
    if parsed is None:
        parsed = query_ops.compile_query(q)
    if parsed.used:
        return {"bool": {"must": parsed.clause, "filter": filters}}
    if q.strip():
        return {
            "bool": {
                "must": {
                    "multi_match": {
                        "query": q,
                        "fields": ["title^2", "content"],
                        "operator": "and",
                        "type": "cross_fields",
                    }
                },
                "filter": filters,
            }
        }
    return {"bool": {"must": {"match_all": {}}, "filter": filters}}


def _shard_failure(resp: dict) -> str | None:
    """One index failing inside a union is a 200, not an error.

    OpenSearch answers a multi-index search from whichever shards succeeded and
    reports the rest in `_shards.failures`. Across a union of corpora that means a
    single drifted or unhealthy index quietly removes a whole source from the
    results — the reader sees a smaller, plausible answer with nothing wrong on the
    face of it. So it is reported. `uv run civic check-projection` names the cause
    for the common case (mapping drift).
    """
    shards = resp.get("_shards") or {}
    if not shards.get("failed"):
        return None
    failures = shards.get("failures") or []
    where = sorted({f.get("index", "?") for f in failures})
    why = (failures[0].get("reason") or {}).get("reason", "") if failures else ""
    return f"{shards['failed']} shard(s) failed on {', '.join(where) or '?'}: {why[:200]}"


def _hit_result(hit: dict) -> dict:
    highlight = (hit.get("highlight", {}).get("content") or [None])[0]
    return {**hit["_source"], "highlight": highlight, "score": hit.get("_score")}


def _facets(resp: dict) -> dict:
    return {
        name: [{"value": b["key"], "count": b["doc_count"]} for b in agg["buckets"]]
        for name, agg in resp["aggregations"].items()
    }


def _pool_facets(docs: list[dict]) -> dict:
    """Facet counts over a fused hybrid pool, tallied here rather than by the index.

    PROJ-3, decided 2026-07-28. Hybrid used to report the BM25 leg's aggregations,
    which count the *whole* BM25 result set — global, exhaustive, and empty exactly
    when the dense channel is doing all the work. `q=tandartsklacht` returned 100
    results with all five facets `[]`: the corpus switcher disappeared at the moment
    cross-corpus retrieval was most useful.

    The ruling is pool-scoped counts, labelled in the UI. Two reasons it is the more
    honest of the two, not merely the more convenient:

      * hybrid pagination is *already* pool-bounded (`min(total, pooled)`), so a
        global facet count promised a narrowing the result list could never deliver;
      * a count now describes the documents you can actually reach.

    The cost is real and must stay visible: these are "within the top N" counts, so
    the response labels them (`facets_scope`) and the UI says so. BM25 mode is
    untouched — its aggregations are exhaustive and it keeps the pre-W4 shape.
    """
    facets = {}
    for name, spec in FACET_FIELDS.items():
        counts: Counter = Counter()
        for doc in docs:
            value = doc.get(name)
            if value is None or value == "":
                continue
            # Match the terms agg: a multi-valued field counts once per value.
            for one in value if isinstance(value, list) else [value]:
                counts[one] += 1
        if spec.get("order") == "key_desc":
            ordered = sorted(counts.items(), key=lambda kv: kv[0], reverse=True)
        else:
            # count desc, key asc on ties — the terms agg's own ordering.
            ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
        facets[name] = [
            {"value": k, "count": c} for k, c in ordered[: spec["size"]]
        ]
    return facets


def _vocab_aggs() -> dict:
    """Wider than _facet_aggs(): the parser matches against the whole closed
    vocabulary, not just the slice the facet panel displays."""
    return {
        "publisher": {"terms": {"field": "publisher", "size": 500}},
        "file_type": {"terms": {"field": "file_type", "size": 50}},
        "disposition": {"terms": {"field": "disposition", "size": 50}},
    }


_vocab_cache: dict[str, tuple[float, dict]] = {}


def _vocab(index: str) -> dict[str, list[str]]:
    cached = _vocab_cache.get(index)
    now = time.monotonic()
    if cached and now - cached[0] < VOCAB_TTL_S:
        return cached[1]
    resp = osc.search(index=index, body={"size": 0, "aggs": _vocab_aggs()})
    vocab = {
        name: [b["key"] for b in agg["buckets"]]
        for name, agg in resp["aggregations"].items()
    }
    _vocab_cache[index] = (now, vocab)
    return vocab


def _understand(
    index: str, q: str, explicit: set[str], dropped: set[str]
) -> tuple[list[dict], str, dict]:
    """Tier 0 parse (W4, design/query-understanding.md §4/§5).

    Fail-open, like extraction-side enrichment: any trouble and the raw query
    runs unchanged, with the reason reported. A facet the caller passed
    explicitly always wins — its chips are dropped and their spans go back into
    `q`, so a user who clicked a facet is never overridden by the parser. A chip
    the user removed (`drop_chip`) goes the same way: gone as a filter, its words
    back in the query.
    """
    try:
        chips, _ = query_parse.parse(q, _vocab(index))
        chips = [
            c
            for c in chips
            if c["field"] not in explicit and query_parse.chip_id(c) not in dropped
        ]
        q_effective = query_parse.residual(q, chips)
        return chips, q_effective, {
            "chips": chips,
            "q_effective": q_effective,
            "chips_applied": True,
            "understanding_used": True,
            "understanding_fallback_reason": None,
        }
    except Exception as e:
        return [], q, {
            "chips": [],
            "q_effective": q,
            "chips_applied": False,
            "understanding_used": False,
            "understanding_fallback_reason": str(e),
        }


def _chip_is_clean(q: str, masked: str, chip: dict, free_spans) -> bool:
    """Is this chip safe to lift out of an operator query? (§9.2 steps 3–4)

    Two guards, both cheap and both real:

      * the chip must not reach **across a masked region** — a multi-word vocabulary
        value could otherwise match text half of which sits inside a quoted phrase;
      * it must begin where a free term begins and end where one ends. Pruning removes
        whole leaves, so a chip covering *part* of one would take a word the user never
        named — and a vocabulary value can legitimately start mid-token, because
        `query_parse._W` treats `-` as a word boundary (`hoger-onderwijs`).

    The second test is against the leaf spans rather than against whitespace: a term
    can sit flush against a parenthesis, and `(juni 2026)` is a perfectly good date.
    """
    start, end = chip["span"]
    if masked[start:end] != q[start:end]:
        return False
    return any(start == a for a, _ in free_spans) and any(end == b for _, b in free_spans)


def _understand_operators(
    index: str, q: str, parsed: query_ops.Parsed, explicit: set[str], dropped: set[str]
) -> tuple[list[dict], str, query_ops.Parsed, dict, bool]:
    """W4 chips out of an operator query (§9). Returns (chips, q_effective, parsed,
    understanding, consumed) — `consumed` meaning every search term became a filter.

    Fail-open like everything else on this path: any trouble and the operator query
    runs exactly as typed, with no chips.
    """
    silent = {
        "chips": [],
        "q_effective": q,
        "chips_applied": False,
        "understanding_used": False,
        # Precise, because "operators are on" is not the reason — the reason is that
        # this particular query has no globally-conjunctive term to read as a filter.
        "understanding_fallback_reason": "operator query with no free terms to read as filters",
    }
    if not parsed.free_spans:
        return [], q, parsed, silent, False
    try:
        masked = query_ops.mask_outside(q, parsed.free_spans)
        chips, _ = query_parse.parse(masked, _vocab(index))
        chips = [
            c
            for c in chips
            if c["field"] not in explicit
            and query_parse.chip_id(c) not in dropped
            and _chip_is_clean(q, masked, c, parsed.free_spans)
        ]
    except Exception as e:
        return [], q, parsed, {**silent, "understanding_fallback_reason": str(e)}, False

    understood = {
        "chips": chips,
        "chips_applied": True,
        "understanding_used": True,
        "understanding_fallback_reason": None,
    }
    if not chips:
        # Nothing recognised — the same non-event as a plain query nobody's vocabulary
        # matched, so it is reported the same way rather than as a fallback.
        return [], q, parsed, {**understood, "q_effective": q}, False

    pruned = query_ops.prune(
        parsed, [tuple(c["span"]) for c in chips], query_parse.STRANDED_WORDS
    )
    if pruned is None:
        # Every term became a filter. Empty text + filters is a legitimate search (it
        # is what W4 does for a fully-understood plain query), so it runs — but the
        # response has to say so, or `used: false` would read as "operators broken".
        return chips, "", query_ops.NO_OPERATORS, {**understood, "q_effective": ""}, True
    return chips, pruned.canonical, pruned, {**understood, "q_effective": pruned.canonical}, False


def _annotate_chip_counts(
    index: str,
    q: str,
    filters: list,
    chips: list[dict],
    parsed: query_ops.Parsed = query_ops.NO_OPERATORS,
) -> None:
    """Give every chip the hit count it alone would produce (§8 step 2).

    One `size: 0` filter-aggregation round trip: each chip counted against the
    *residual* query plus the facets the caller passed explicitly, so the number
    shown is that chip's own narrowing power rather than the whole set's. Purely
    decorative — a failed probe leaves the chips countless, never unfiltered.
    """
    if not chips:
        return
    # The caller's parse, never a fresh one: the count has to be taken against the same
    # clause the search runs, which for an operator query is the *pruned* one.
    aggs = {
        f"chip_{i}": {
            "filter": _bm25_query(
                q, filters + [{"terms": {c["field"]: c["values"]}}], parsed
            )
        }
        for i, c in enumerate(chips)
    }
    try:
        resp = osc.search(
            index=index, body={"size": 0, "query": {"match_all": {}}, "aggs": aggs}
        )
        for i, chip in enumerate(chips):
            bucket = (resp.get("aggregations") or {}).get(f"chip_{i}")
            if bucket is not None:
                chip["count"] = bucket["doc_count"]
    except Exception:
        pass


@app.get("/api/search")
def search(
    q: str = Query(""),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    sort: str = Query("relevance"),
    mode: str = Query("hybrid_rerank"),
    publisher: list[str] = Query(None),
    file_type: list[str] = Query(None),
    disposition: list[str] = Query(None),
    month: list[str] = Query(None),
    source: list[str] = Query(None),
    dataset: str = Query(None),
    understand: bool = Query(True),
    operators: bool = Query(True),
    drop_chip: list[str] = Query(None),
):
    filters = []
    explicit = set()
    for field, values in [
        ("source_id", source),
        ("publisher", publisher),
        ("file_type", file_type),
        ("disposition", disposition),
        ("published_month", month),
    ]:
        if values:
            filters.append({"terms": {field: values}})
            explicit.add(field)

    index = _index(dataset)

    # W11(a) search operators. Parsed once, here, so the clause the search runs is the
    # clause the response describes. `operators=0` is the same kind of switch as
    # `understand=0`: it reproduces the literal pre-W11 query for a text that contains
    # operator syntax (for a text that does not, nothing changes either way).
    parsed = query_ops.compile_query(q) if operators else query_ops.NO_OPERATORS

    # W4 Tier 0: free text -> filter chips. `understand=0` must reproduce the
    # pre-W4 response byte for byte — that is the regression contract, and it is
    # what makes the feature safe to default on.
    understanding: dict = {}
    chips: list[dict] = []
    q_raw = q
    parsed_raw = parsed  # kept for the zero-result guard, which re-runs the raw query
    consumed = False
    if understand and q.strip():
        if parsed.used:
            # §9. Chips are derived only from the root-conjunctive bare terms, and are
            # applied by pruning the parse *tree* — never by editing the query string,
            # which is what makes §4's `en`/`of` collision impossible rather than
            # merely avoided.
            chips, q, parsed, understanding, consumed = _understand_operators(
                index, q, parsed, explicit, set(drop_chip or [])
            )
            _annotate_chip_counts(index, q, filters, chips, parsed)
        else:
            chips, q, understanding = _understand(
                index, q, explicit, set(drop_chip or [])
            )
            _annotate_chip_counts(index, q, filters, chips)

    result = _execute(
        index, q, filters + query_parse.chip_filters(chips),
        page=page, size=size, sort=sort, mode=mode, parsed=parsed,
    )

    # Zero-result guard (§4): a chip set that yields nothing where the raw query
    # yields something is *offered*, not applied — an over-eager parse must fail
    # visibly rather than silently empty. Measured on the executed result rather
    # than on a count probe, so it stays right for the dense leg too (a chip set
    # with no BM25 hits can still have kNN hits).
    if chips and result["total"] == 0:
        raw = _execute(
            index, q_raw, filters, page=page, size=size, sort=sort, mode=mode,
            parsed=parsed_raw,
        )
        if raw["total"] > 0:
            result = raw
            # The raw query is what ran, so it is also what the operator block must
            # describe — otherwise the panel would report a pruned query nobody executed.
            parsed, consumed = parsed_raw, False
            understanding = {
                **understanding,
                "q_effective": q_raw,
                "chips_applied": False,
                "understanding_fallback_reason": "chips would return 0 results",
            }

    # Conditional, like `facets_scope`: a query with no operator syntax keeps the
    # response shape it had before W11 exactly. When there *is* syntax the key is
    # always present, including when we failed to use it — a silent absence would
    # read as "the operators were applied".
    # `parsed_raw`, because §9 can prune the parse away entirely (every term became a
    # filter) — and a query that *had* operators must still report them.
    if parsed_raw.detected:
        result = {
            **result,
            "operators": {
                "used": parsed.used,
                "parsed": parsed.canonical or None,
                "warnings": list(parsed.warnings or parsed_raw.warnings),
                # Every search term was read as a filter, so there is no text left to
                # search — a legitimate outcome that `used: false` alone would misreport
                # as "the operators broke".
                **({"consumed_by_chips": True} if consumed else {}),
                # A property of the parse, not of this run — which is exactly why it
                # is reported here rather than left for the UI to recognise inside
                # `mode_fallback_reason`. That string is a machine reason in English;
                # matching Dutch UI copy against it would break on a reworded sentence.
                "exclusion_in_or": parsed.nested_negation,
            },
        }
    return {**result, **understanding}


def _execute(
    index: str, q: str, filters: list, *, page: int, size: int, sort: str, mode: str,
    parsed: query_ops.Parsed = query_ops.NO_OPERATORS,
) -> dict:
    """One ranked search — the whole pre-W4 endpoint body, minus understanding.

    Split out so the zero-result guard can run the same search twice (with the
    chips, then without) instead of duplicating the mode-fallback ladder.
    """
    # Hybrid only applies to relevance-sorted, non-empty queries.
    want_hybrid = mode in ("hybrid", "hybrid_rerank") and q.strip() and sort == "relevance"
    # ...and never to a query whose exclusion cannot be handed to the dense leg
    # (design/search-operators.md §6). `a OR NOT b` admits documents containing `b`,
    # so there is no filter that expresses it: the kNN channel would return exactly
    # the documents the user ruled out, mixed in with the ones they asked for. BM25
    # answers it exactly, so we drop to BM25 and say why rather than approximate.
    hybrid_blocked = (
        "operator query with NOT inside an OR — the dense leg cannot honour it"
        if want_hybrid and parsed.nested_negation
        else None
    )
    if want_hybrid and not hybrid_blocked:
        result = _hybrid_search(
            index, q, filters, page, size,
            rerank=(mode == "hybrid_rerank"), parsed=parsed,
        )
        if result is not None:
            return result

    sort_clause = {
        "relevance": ["_score"],
        "date_desc": [{"published": {"order": "desc", "missing": "_last"}}],
        "date_asc": [{"published": {"order": "asc", "missing": "_last"}}],
    }.get(sort, ["_score"])

    body = {
        "query": _bm25_query(q, filters, parsed),
        "from": (page - 1) * size,
        "size": size,
        "sort": sort_clause,
        "_source": {"excludes": ["content", "embedding"]},
        "highlight": _highlight(parsed),
        "aggs": _facet_aggs(),
    }
    resp = osc.search(index=index, body=body)
    incomplete = _shard_failure(resp)
    return {
        "total": resp["hits"]["total"]["value"],
        "results": [_hit_result(h) for h in resp["hits"]["hits"]],
        "facets": _facets(resp),
        "mode_used": "bm25",
        "mode_fallback_reason": hybrid_blocked or (_embedder_error if want_hybrid else None),
        # Conditional, so a healthy search keeps the pre-W4 response shape exactly.
        **({"incomplete": incomplete} if incomplete else {}),
    }


def _hybrid_search(
    index: str, q: str, filters: list, page: int, size: int, *, rerank: bool,
    parsed: query_ops.Parsed = query_ops.NO_OPERATORS,
) -> dict | None:
    """RRF-fused BM25 + kNN pool, optionally reranked. None -> caller falls back."""
    # Operators are syntax, not meaning: embedding `subsidie -onderwijs` verbatim asks
    # the vector space for the excluded word as loudly as for the wanted one. The
    # positive text is embedded instead, and the exclusions come back as a kNN filter.
    dense_q = parsed.dense_text if parsed.used else q
    if not dense_q.strip():
        return None  # nothing positive to search for semantically (e.g. `-onderwijs`)
    qvec = _embed_query(dense_q)
    if qvec is None:
        return None

    # No aggs on the BM25 leg: since PROJ-3 the facets are counted over the fused
    # pool, and asking the cluster for counts nobody reads is pure work.
    bm25_body = {
        "query": _bm25_query(q, filters, parsed),
        "size": HYBRID_POOL,
        "_source": {"excludes": ["content", "embedding"]},
        "highlight": _highlight(parsed),
    }
    # A conjunctive NOT holds for the whole query, so the dense leg gets it as a
    # filter — the BM25 clause already carries it. Without this the kNN channel is
    # free to reintroduce exactly what the operator excluded, which is the one
    # failure any reader would immediately (and rightly) call a bug.
    knn_filters = filters + (
        [{"bool": {"must_not": list(parsed.exclusions)}}] if parsed.exclusions else []
    )
    knn_clause: dict = {"vector": qvec, "k": HYBRID_POOL}
    if knn_filters:
        knn_clause["filter"] = {"bool": {"filter": knn_filters}}
    knn_body = {
        "size": HYBRID_POOL,
        "query": {"knn": {"embedding": knn_clause}},
        "_source": {"excludes": ["content", "embedding"]},
    }
    bm25_resp = osc.search(index=index, body=bm25_body)
    knn_resp = osc.search(index=index, body=knn_body)

    # Keyed by index+id, not id alone: across a multi-index target two corpora could
    # in principle carry the same `_id`, and a bare-id dict would silently merge them.
    def _ref(hit: dict) -> str:
        return f"{hit.get('_index', index)}/{hit['_id']}"

    bm25_hits = {_ref(h): h for h in bm25_resp["hits"]["hits"]}
    knn_hits = {_ref(h): h for h in knn_resp["hits"]["hits"]}
    fused = retrieval.rrf_fuse([list(bm25_hits), list(knn_hits)])

    hit_of = {ref: bm25_hits.get(ref) or knn_hits[ref] for ref, _ in fused}
    ordered = [dict(_hit_result(hit_of[ref]), score=score) for ref, score in fused]
    refs = [
        {"_index": hit_of[ref].get("_index", index), "_id": hit_of[ref]["_id"]}
        for ref, _ in fused
    ]

    mode_used = "hybrid"
    rerank_blocked = None
    if rerank and ordered and retrieval.rerank_available():
        try:
            docs = osc.mget(
                body={"docs": refs},
                _source_includes=["title", "content", "processors", "source_id"],
            )["docs"]
            # Reranking ships the document text to a third party. Every document in the
            # pool has to permit that, and by default none does — see engine/exposure.py
            # for what Voyage's own privacy policy says about content submitted to it.
            # The pool is governed by its strictest member, which across corpora is a
            # live constraint rather than a theoretical one: a cross-corpus pool reranks
            # only if EVERY source in it opted in. Name the sources that did not, so the
            # answer is "these two manifests", not "reranking stopped working".
            withheld = sorted(
                {
                    d.get("_source", {}).get("source_id") or "?"
                    for d in docs
                    if not exposure.permits_processor(d.get("_source", {}), "voyage_rerank")
                }
            )
            if withheld:
                raise PermissionError(
                    "no policy.processors opt-in for voyage_rerank on: "
                    + ", ".join(withheld)
                )
            texts = [
                f"{d['_source'].get('title') or ''}\n{d['_source'].get('content') or ''}"
                for d in docs
            ]
            # `dense_q`, not `q`: a cross-encoder reads the query as natural language,
            # so operator syntax is noise to it for the same reason it is to the embedder.
            reranked = retrieval.voyage_rerank(dense_q, texts)
            ordered = [dict(ordered[i], score=rel) for i, rel in reranked]
            mode_used = "hybrid_rerank"
        except PermissionError as e:
            # Reported, never silent: a quality drop that looks like a bug gets "fixed"
            # by someone removing the check.
            rerank_blocked = str(e)
        except Exception:
            pass  # pool order is already good; rerank is an upgrade, not a gate

    start = (page - 1) * size
    return {
        # BM25's exhaustive count can be 0 while the dense channel still hits
        # (vocabulary mismatch — hybrid's whole point); report what's browsable.
        "total": max(bm25_resp["hits"]["total"]["value"], len(ordered)),
        "pooled": len(ordered),
        "results": ordered[start : start + size],
        # PROJ-3: counted over the whole fused pool, not the page and not the BM25
        # leg. `facets_scope` is the label that keeps the smaller numbers honest;
        # its absence (bm25 mode) means the exhaustive index-wide counts that were
        # always the meaning, so the pre-W4 bm25 response shape is untouched.
        "facets": _pool_facets(ordered),
        "facets_scope": "pool",
        "mode_used": mode_used,
        **({"rerank_blocked": rerank_blocked} if rerank_blocked else {}),
        **({"incomplete": _shard_failure(bm25_resp)} if _shard_failure(bm25_resp) else {}),
    }


def _find_doc(
    target: str, urn: str, *, includes: list[str] = None, excludes: list[str] = None
) -> dict | None:
    """One document by URN, from anywhere in the target.

    A search on the `urn` keyword rather than a `get` by `_id`, because across a
    multi-index target there is no single index to `get` from — and the dataset must
    NOT be guessed from the URN prefix, which names the provider, not the source
    (tests/test_suppression.py pins that rule for takedowns; it holds here too).
    """
    body: dict = {"size": 1, "query": {"term": {"urn": urn}}}
    source_spec = {}
    if includes:
        source_spec["includes"] = includes
    if excludes:
        source_spec["excludes"] = excludes
    if source_spec:
        body["_source"] = source_spec
    hits = osc.search(index=target, body=body)["hits"]["hits"]
    return hits[0] if hits else None


@app.get("/api/doc/{urn}")
def get_doc(urn: str, dataset: str = Query(None)):
    """Full document. Serving our own copy of the text is a T2 act.

    Below T2 the document still resolves — metadata, facets and the link back to the
    publisher are what T0/T1 exist to serve — but the text comes out. `withheld` says so
    rather than returning a silently truncated record: a caller that cannot tell the
    difference between "no text" and "text not served" will treat the first as a bug.
    """
    try:
        doc = _find_doc(_index(dataset), urn, excludes=["embedding"])
    except Exception:
        doc = None
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    source = doc["_source"]
    if not exposure.serves_full_text(source.get("exposure")):
        source.pop("content", None)
        source["withheld"] = {
            "content": "exposure tier below T2 — read it at the source",
            "exposure": source.get("exposure"),
            "landing_url": source.get("landing_url"),
        }
    return source


@app.get("/api/doc/{urn}/related")
def related(urn: str, k: int = Query(8, ge=1, le=25), dataset: str = Query(None)):
    """Recommend mode: doc-to-doc kNN on the stored doc vector (embeddings A/B §8).

    Cross-corpus by default — a neighbour in another source is the whole point
    (roadmap exit criterion #3), and every index shares one 1024-dim bge-m3 space,
    so the kNN is over one vector space that happens to be stored in four indices.
    `dataset=` narrows it back to a single corpus.
    """
    target = _index(dataset)
    try:
        doc = _find_doc(target, urn, includes=["embedding"])
    except Exception:
        doc = None
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    vec = doc["_source"].get("embedding")
    if not vec:
        return {"related": [], "reason": "document has no embedding yet"}
    body = {
        "size": k + 1,  # the doc itself ranks first; drop it below
        "query": {"knn": {"embedding": {"vector": vec, "k": k + 1}}},
        "_source": {"excludes": ["content", "embedding"]},
    }
    resp = osc.search(index=target, body=body)
    # Dropped by URN, not by `_id`: identical here, but the URN is the identity the
    # rest of the system agrees on.
    hits = [h for h in resp["hits"]["hits"] if h["_source"].get("urn") != urn][:k]
    return {"related": [{**h["_source"], "score": h["_score"]} for h in hits]}
