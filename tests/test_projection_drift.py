"""Mapping drift between the declared schema and the live indices.

The failure this pins is not hypothetical — it is what the four live indices looked
like on 2026-07-28, three days after `exposure` was declared `keyword`:
`ensure_index` only creates an index that is absent, so a field added to
`mappings.py` never reaches indices that already exist, and the first document
carrying it gets mapped dynamically as `text`. Aggregations and term filters then
fail on those shards — inside a cross-corpus union, as a partial success with an
HTTP 200.
"""

from engine.project import mappings, verify

DECLARED = {"properties": {"urn": {"type": "keyword"},
                           "exposure": {"type": "keyword"},
                           "content": {"type": "text"}}}


def test_a_field_declared_keyword_but_live_as_text_is_drift():
    live = {"properties": {"urn": {"type": "keyword"},
                           "exposure": {"type": "text"},
                           "content": {"type": "text"}}}
    drift = verify.mapping_drift(DECLARED, live)
    assert drift["wrong_type"] == [
        {"field": "exposure", "declared": "keyword", "live": "text"}
    ]
    assert not verify.is_clean(drift)


def test_an_index_that_matches_its_schema_is_clean():
    drift = verify.mapping_drift(DECLARED, DECLARED)
    assert verify.is_clean(drift)
    assert drift == {"wrong_type": [], "undeclared": [], "unmaterialised": []}


def test_a_declared_field_no_document_has_written_is_reported_but_not_a_failure():
    """`processors: []` writes no mapping at all. Harmless now — a term filter on an
    absent field correctly matches nothing — but it is a wrong_type waiting to
    happen, because the next write types it dynamically."""
    live = {"properties": {"urn": {"type": "keyword"}, "content": {"type": "text"}}}
    drift = verify.mapping_drift(DECLARED, live)
    assert drift["unmaterialised"] == ["exposure"]
    assert verify.is_clean(drift), "not yet written is a warning, not a disagreement"


def test_a_field_nobody_declared_is_drift_because_it_was_typed_by_guess():
    live = {"properties": {**DECLARED["properties"], "sneaky": {"type": "text"}}}
    drift = verify.mapping_drift(DECLARED, live)
    assert drift["undeclared"] == ["sneaky"]
    assert not verify.is_clean(drift)


def test_the_real_schema_is_what_gets_compared(monkeypatch):
    """Guards the wiring, not the comparison: doc indices against doc_mappings(),
    chunk indices against chunk_mappings() — swapping them would report every field
    of both as drift."""

    class FakeIndices:
        def get_mapping(self, index=None):
            return {
                "civic-woo_besluiten": {"mappings": mappings.doc_mappings()},
                "civic-woo_besluiten-chunks": {"mappings": mappings.chunk_mappings()},
            }

        def get_settings(self, index=None):
            live = {"settings": {"index": {"analysis": mappings.analysis_settings()}}}
            return {"civic-woo_besluiten": live, "civic-woo_besluiten-chunks": live}

    class FakeOS:
        indices = FakeIndices()

    reports = verify.check_indices(FakeOS())
    assert [r["kind"] for r in reports] == ["doc", "chunk"]
    assert all(verify.is_clean(r) for r in reports)
    assert "DRIFT" not in verify.format_report(reports)


def test_the_report_names_the_fix():
    reports = [{"index": "civic-x", "kind": "doc", "unmaterialised": [],
                "undeclared": [], "wrong_type": [
                    {"field": "exposure", "declared": "keyword", "live": "text"}]}]
    out = verify.format_report(reports)
    assert "DRIFT civic-x" in out
    assert "--recreate" in out, "a check that does not say what to do gets ignored"


# ── analysis drift (2026-07-29) ───────────────────────────────────────────────
#
# The mapping check above had a hole exactly the size of the change that prompted
# these tests. Analysis settings are fixed at index creation too, so editing the
# analyzer in mappings.py changes what the code says and nothing about what the
# index does — every query keeps running the old chain, and no test fails, because
# the tokens are still Dutch and still stemmed, just stemmed the old way.


def test_the_old_builtin_dutch_analyzer_is_reported_as_drift():
    """What all eight live indices looked like before the stemmer fix."""
    live = {"analysis": {"analyzer": {
        "dutch_analyzer": {"type": "dutch", "stopwords": "_dutch_"},
        "dutch_exact": {"type": "custom", "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding"]},
    }}}
    found = verify.analysis_drift(live)
    names = {(d["kind"], d["name"]) for d in found}
    assert ("analyzer", "dutch_analyzer") in names
    assert ("filter", "dutch_stemmer") in names       # the whole chain is missing
    assert ("analyzer", "dutch_exact") not in names   # unchanged, so not flagged
    assert not verify.is_clean({"wrong_type": [], "undeclared": [], "analysis": found})


def test_an_index_carrying_the_declared_chain_is_clean():
    assert verify.analysis_drift({"analysis": mappings.analysis_settings()}) == []


def test_opensearch_echoing_values_as_strings_is_not_drift():
    """A check that cries wolf gets removed, not obeyed: OpenSearch returns numbers
    and booleans as strings, so a literal == would report drift forever."""
    declared = mappings.analysis_settings()
    live = {"analysis": {
        "analyzer": declared["analyzer"],
        "filter": {**declared["filter"],
                   "dutch_stop": {"type": "stop", "stopwords": "_dutch_",
                                  "ignore_case": "true"}},  # extra key, string bool
    }}
    assert verify.analysis_drift(live) == []


def test_the_declared_chain_keeps_singular_and_plural_reachable():
    """The defect this fix exists for, pinned at the level of the declaration.

    Snowball alone sends `tandarts` -> 'tandart' and `tandartsen` -> 'tandarts', so
    the two forms never meet and a plural query found 20 of 66 documents. The fix is
    keyword_repeat + remove_duplicates around the stemmers, which keeps the surface
    form beside the stem so the two token sets overlap on 'tandarts'.
    """
    chain = mappings.analysis_settings()["analyzer"]["dutch_analyzer"]["filter"]
    assert "keyword_repeat" in chain and "remove_duplicates" in chain
    assert chain.index("keyword_repeat") < chain.index("dutch_stemmer")
    assert chain.index("remove_duplicates") > chain.index("dutch_stemmer")
    # dropping the built-in's irregular rules would quietly un-fix kind/kinderen
    rules = mappings.analysis_settings()["filter"]["dutch_override"]["rules"]
    assert "kind=>kinder" in rules
