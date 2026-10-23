"""Does the live projection still have the mapping we declared?

Cross-corpus search unions `civic-*` into one query (design/cross-corpus-search.md),
and a union is only as good as its members' agreement: one index with a different
type for a field makes aggregations and term filters fail *on that shard only*, which
OpenSearch reports as a partial success rather than an error. Silent partial results
are the worst failure shape this system has.

This is how the drift gets in, and it is not hypothetical — it is what the four live
indices looked like on 2026-07-28:

    `os_client.ensure_index` creates an index only when it is absent, so a field
    ADDED to `mappings.py` later never reaches an index that already exists. The
    first document carrying that field is then mapped *dynamically* — a string
    becomes `text`, not `keyword` — and the index quietly disagrees with the schema
    it claims to have. `exposure` was declared `keyword` on 2026-07-27; three of the
    four indices had it as `text`.

Rule 5 of design/failure-modes.md: a prompt-level obligation with no mechanical check
is not a control. This is the mechanical check. It is deliberately NOT the W6.5
projection-liveness probe (that one asks whether the projection reflects the ledger);
this one asks only whether the index agrees with its declared mapping.
"""

from engine.project import mappings


def declared_type(field_def: dict) -> str | None:
    return (field_def or {}).get("type")


def mapping_drift(expected: dict, live: dict) -> dict[str, list]:
    """Compare a declared `mappings` block with a live one.

    Three findings, deliberately distinguished — they have different urgencies:

      wrong_type      declared X, live is Y. Queries on this field misbehave NOW.
      undeclared      live has a field the schema never declared. Something wrote a
                      field nobody designed, and it was typed by guess.
      unmaterialised  declared, absent live. Harmless today (no document has ever
                      carried a value), but it is a wrong_type waiting to happen:
                      the next write types it dynamically.
    """
    exp = (expected or {}).get("properties") or {}
    got = (live or {}).get("properties") or {}
    wrong_type, unmaterialised = [], []
    for name, spec in exp.items():
        if name not in got:
            unmaterialised.append(name)
            continue
        want, have = declared_type(spec), declared_type(got[name])
        if want != have:
            wrong_type.append({"field": name, "declared": want, "live": have})
    undeclared = sorted(set(got) - set(exp))
    return {
        "wrong_type": wrong_type,
        "undeclared": undeclared,
        "unmaterialised": sorted(unmaterialised),
    }


def is_clean(drift: dict) -> bool:
    """Only the findings that mean the index and the schema actually disagree.

    `analysis` is read with .get: mapping_drift() alone does not produce it, and a
    caller comparing two mapping blocks should not have to fake an empty key.
    """
    return not (drift["wrong_type"] or drift["undeclared"] or drift.get("analysis"))


def analysis_drift(live_settings: dict) -> list[dict]:
    """Does the index's live analysis chain still match the one mappings.py declares?

    Added 2026-07-29 because the mapping check above had a hole exactly the size of the
    change that prompted it. Analysis settings, like mappings, are **fixed at index
    creation** — `ensure_index` will not update them — so editing the analyzer in
    `mappings.py` changes what the code says and nothing about what the index does.
    Every query keeps running the old chain, silently, and no test fails: the tokens
    are still Dutch, still stemmed, just stemmed the old way.

    That is worse than a mapping drift, because a wrong-typed field breaks loudly on the
    first aggregation while a stale analyzer only ever returns slightly fewer results
    than it should. The stemmer fix this check was written alongside is precisely a
    "slightly fewer results" defect, and it went unnoticed for the life of the project.

    Compared per analyzer rather than as one blob so the report can name which chain
    moved; filter definitions are folded in because an analyzer is only as declared as
    the filters it names.
    """
    declared = mappings.analysis_settings()
    live = (live_settings or {}).get("analysis") or {}
    out = []
    for kind in ("analyzer", "filter"):
        want, have = declared.get(kind) or {}, live.get(kind) or {}
        for name, spec in want.items():
            if name not in have:
                out.append({"kind": kind, "name": name, "finding": "absent"})
            elif not _same_analysis(spec, have[name]):
                out.append({"kind": kind, "name": name, "finding": "differs",
                            "declared": spec, "live": have[name]})
    return out


def _same_analysis(declared: dict, live: dict) -> bool:
    """Compare ignoring how OpenSearch echoes values back.

    It returns numbers and booleans as strings and may omit a `type` it considers
    implied, so a literal == would report drift on every index forever — a check that
    cries wolf is removed, not obeyed.
    """
    def norm(v):
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        return str(v).lower() if isinstance(v, bool) else str(v)

    d, live_n = norm(declared), norm(live)
    return all(live_n.get(k) == v for k, v in d.items())


def check_indices(osc) -> list[dict]:
    """Every civic-* index, compared against the mapping its shape declares."""
    live = osc.indices.get_mapping(index=f"{mappings.doc_index_name('*')}")
    settings = osc.indices.get_settings(index=f"{mappings.doc_index_name('*')}")
    reports = []
    for name in sorted(live):
        is_chunks = name.endswith("-chunks")
        expected = mappings.chunk_mappings() if is_chunks else mappings.doc_mappings()
        drift = mapping_drift(expected, live[name].get("mappings") or {})
        drift["analysis"] = analysis_drift(
            ((settings.get(name) or {}).get("settings") or {}).get("index") or {}
        )
        reports.append({"index": name, "kind": "chunk" if is_chunks else "doc", **drift})
    return reports


def format_report(reports: list[dict]) -> str:
    lines = []
    for r in reports:
        status = "ok  " if is_clean(r) else "DRIFT"
        lines.append(f"{status} {r['index']}")
        for w in r["wrong_type"]:
            lines.append(
                f"        wrong type: {w['field']} declared {w['declared']}, "
                f"live {w['live']}  -> aggregations/term filters fail on this shard"
            )
        for a in r.get("analysis") or []:
            lines.append(
                f"        analysis {a['kind']} '{a['name']}' {a['finding']}"
                "  -> this index still tokenises the OLD way; queries silently differ"
            )
        if r["undeclared"]:
            lines.append(f"        undeclared (mapped by guess): {', '.join(r['undeclared'])}")
        if r["unmaterialised"]:
            lines.append(
                f"        not yet written: {', '.join(r['unmaterialised'])}"
                "  -> will be typed dynamically unless the index is recreated"
            )
    if not lines:
        return "no civic-* indices found"
    dirty = [r["index"] for r in reports if not is_clean(r)]
    if dirty:
        lines.append("")
        lines.append(
            "Fix: uv run civic project <source> --recreate  (the mapping is only "
            "applied at index creation; Postgres holds the text and the vectors, so a "
            "rebuild costs no crawl and no GPU)"
        )
    return "\n".join(lines)
