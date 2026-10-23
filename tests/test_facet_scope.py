"""PROJ-3: what a facet count is counted over, and that it says so.

The defect these pin is not "facets are wrong" but "facets are *absent* exactly when
they matter most". Hybrid mode used to report the BM25 leg's aggregations, so a query
only the dense channel could answer (`tandartsklacht`: BM25 0 hits, hybrid 100)
returned a hundred results with all five facets empty — including the source facet,
i.e. the corpus switcher vanished at the moment cross-corpus retrieval was doing the
most work.

The ruling (2026-07-28): hybrid facets are tallied over the fused pool and the
response labels them `facets_scope: "pool"`. BM25 mode is untouched — its
aggregations are exhaustive over the whole result set, and its response shape is the
`understand=0` regression contract.
"""

import pytest
from fastapi.testclient import TestClient

from ui import search_api


def _doc(urn, source, publisher, ftype="application/pdf", disp="ok", month="2026-06"):
    return {
        "urn": urn, "title": urn, "source_id": source, "publisher": publisher,
        "file_type": ftype, "disposition": disp, "published_month": month,
    }


# --------------------------------------------------------------- the tally itself

def test_pool_facets_counts_every_declared_facet_over_the_pool():
    pool = [
        _doc("a", "woo_besluiten", "OCW"),
        _doc("b", "woo_besluiten", "OCW"),
        _doc("c", "haarlem_raad", "Haarlem", month="2026-05"),
    ]
    facets = search_api._pool_facets(pool)
    assert set(facets) == set(search_api.FACET_FIELDS)
    assert facets["source_id"] == [
        {"value": "woo_besluiten", "count": 2},
        {"value": "haarlem_raad", "count": 1},
    ]
    assert facets["file_type"] == [{"value": "application/pdf", "count": 3}]


def test_months_are_ordered_by_key_descending_not_by_count():
    """A month list reads as a timeline; the terms agg orders it `_key: desc` and the
    pool tally has to make the same choice or the panel reorders itself per mode."""
    pool = [_doc("a", "s", "p", month="2025-01"),
            _doc("b", "s", "p", month="2025-01"),
            _doc("c", "s", "p", month="2026-06")]
    months = search_api._pool_facets(pool)["published_month"]
    assert [m["value"] for m in months] == ["2026-06", "2025-01"]


def test_ties_break_on_key_ascending_like_the_terms_agg():
    pool = [_doc("a", "s", "beta"), _doc("b", "s", "alfa")]
    assert [f["value"] for f in search_api._pool_facets(pool)["publisher"]] == [
        "alfa", "beta"
    ]


def test_a_multi_valued_field_counts_once_per_value():
    pool = [{"publisher": ["Haarlem", "Vlaardingen"]}, {"publisher": "Haarlem"}]
    assert search_api._pool_facets(pool)["publisher"] == [
        {"value": "Haarlem", "count": 2},
        {"value": "Vlaardingen", "count": 1},
    ]


def test_missing_and_empty_values_are_not_facet_values():
    """`(leeg)` as a clickable filter option is a dead end — the term filter it builds
    matches nothing."""
    pool = [{"publisher": None}, {"publisher": ""}, {}, {"publisher": "OCW"}]
    assert search_api._pool_facets(pool)["publisher"] == [{"value": "OCW", "count": 1}]


def test_the_declared_size_caps_each_facet():
    pool = [_doc(str(i), "s", f"pub-{i:03d}") for i in range(80)]
    facets = search_api._pool_facets(pool)
    assert len(facets["publisher"]) == search_api.FACET_FIELDS["publisher"]["size"]


def test_the_aggregation_and_the_tally_come_from_one_declaration():
    """Two hand-maintained lists drift, and a facet present in one ranking mode but
    not the other is PROJ-3 again in a new costume."""
    aggs = search_api._facet_aggs()
    assert set(aggs) == set(search_api.FACET_FIELDS)
    for name, spec in search_api.FACET_FIELDS.items():
        assert aggs[name]["terms"]["field"] == name
        assert aggs[name]["terms"]["size"] == spec["size"]
    assert aggs["published_month"]["terms"]["order"] == {"_key": "desc"}


# ------------------------------------------------------------------ the endpoint

class FakeOS:
    """BM25 returns nothing, kNN returns a mixed-corpus pool — the PROJ-3 query shape."""

    def __init__(self, bm25_hits, knn_hits):
        self.bm25_hits, self.knn_hits = bm25_hits, knn_hits
        self.bodies = []

    def search(self, index=None, body=None):
        self.bodies.append(body)
        if "query" not in body:  # vocabulary probe
            return {"aggregations": {}}
        if "knn" in body["query"]:
            hits = [{"_id": d["urn"], "_index": "civic-x", "_source": d, "_score": 1.0}
                    for d in self.knn_hits]
            return {"hits": {"total": {"value": len(hits)}, "hits": hits}}
        hits = [{"_id": d["urn"], "_index": "civic-x", "_source": d, "_score": 1.0}
                for d in self.bm25_hits]
        return {
            "hits": {"total": {"value": len(hits)}, "hits": hits},
            # Left in deliberately: the endpoint must ignore these in hybrid mode.
            "aggregations": {"source_id": {"buckets": []}, "publisher": {"buckets": []},
                             "file_type": {"buckets": []}, "disposition": {"buckets": []},
                             "published_month": {"buckets": []}},
        }


@pytest.fixture
def client(monkeypatch):
    fake = FakeOS(
        bm25_hits=[],
        knn_hits=[_doc("urn:a", "woo_besluiten", "OCW"),
                  _doc("urn:b", "haarlem_raad", "Haarlem"),
                  _doc("urn:c", "haarlem_raad", "Haarlem")],
    )
    monkeypatch.setattr(search_api, "osc", fake)
    monkeypatch.setattr(search_api, "_embed_query", lambda q: [0.1, 0.2, 0.3])
    search_api._vocab_cache.clear()
    c = TestClient(search_api.app)
    c.fake = fake
    return c


def _hybrid(client, **kw):
    r = client.get("/api/search", params={"q": "tandartsklacht", "mode": "hybrid",
                                          "understand": 0, **kw})
    assert r.status_code == 200
    return r.json()


def test_a_dense_only_answer_still_has_facets(client):
    """The regression itself: BM25 total 0, results non-empty, facets non-empty."""
    body = _hybrid(client)
    assert body["total"] == 3 and len(body["results"]) == 3
    assert body["facets"]["source_id"] == [
        {"value": "haarlem_raad", "count": 2},
        {"value": "woo_besluiten", "count": 1},
    ], "the corpus switcher must survive a query only the dense channel answers"


def test_hybrid_labels_its_facet_scope(client):
    assert _hybrid(client)["facets_scope"] == "pool"


def test_hybrid_no_longer_asks_the_cluster_for_counts_nobody_reads(client):
    _hybrid(client)
    ranked = [b for b in client.fake.bodies if "query" in b]
    assert all("aggs" not in b for b in ranked), "the BM25 leg's aggs are dead work now"


def test_bm25_keeps_exhaustive_counts_and_says_nothing_about_scope(client):
    """Absence of `facets_scope` means the index-wide meaning that was always there —
    which is what keeps the pre-W4 bm25 response shape byte-identical."""
    r = client.get("/api/search", params={"q": "tandartsklacht", "mode": "bm25",
                                          "understand": 0})
    body = r.json()
    assert "facets_scope" not in body
    assert [b for b in client.fake.bodies if "query" in b][-1]["aggs"] == \
        search_api._facet_aggs()


def test_the_pool_not_the_page_is_what_gets_counted(client):
    """Page 1 of size 1 still describes all three pooled documents — otherwise the
    facet panel would reshuffle itself as you paged."""
    body = _hybrid(client, size=1)
    assert len(body["results"]) == 1
    assert sum(f["count"] for f in body["facets"]["source_id"]) == 3
