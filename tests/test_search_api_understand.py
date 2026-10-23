"""The `understand=0` regression contract (W4, design/query-understanding.md §5).

`GET /api/search?q=…&understand=0` must reproduce the pre-W4 behaviour byte for
byte — that is what makes query understanding safe to default on. These tests
drive the endpoint against a fake OpenSearch client so the contract is pinned on
the *request we build* and the *response shape we return*, without a cluster and
without depending on what happens to be indexed.
"""

import pytest
from fastapi.testclient import TestClient

from ui import search_api

OCW = "ministerie van Onderwijs, Cultuur en Wetenschap"

VOCAB = {
    "publisher": [OCW, "ministerie van Economische Zaken en Klimaat"],
    "file_type": ["application/pdf", "application/zip"],
    "disposition": ["ok", "partial"],
}

# Exactly the keys the endpoint returned before W4. The bm25 set is the load-bearing
# one — `understand=0` must reproduce it byte for byte — and it has not moved.
PRE_W4_BM25_KEYS = {"total", "results", "facets", "mode_used", "mode_fallback_reason"}
# The hybrid set gained `facets_scope` on 2026-07-28 (PROJ-3): hybrid facets are now
# counted over the fused pool, and the response says so rather than leaving the caller
# to infer a count's meaning from the ranking mode. Not a W4 key — `understand=0`
# returns it too, which is the point of listing it here and not in UNDERSTANDING_KEYS.
PRE_W4_HYBRID_KEYS = {"total", "pooled", "results", "facets", "facets_scope", "mode_used"}
UNDERSTANDING_KEYS = {
    "chips",
    "q_effective",
    "chips_applied",
    "understanding_used",
    "understanding_fallback_reason",
}


def _buckets(values):
    return {"buckets": [{"key": v, "doc_count": 1} for v in values]}


class FakeOS:
    """Records every body sent. `fail_vocab` exercises the fail-open path.

    `totals` maps a per-search index (0-based, counting only ranked searches) to
    a hit total, so the zero-result guard's two-search dance can be driven.
    """

    def __init__(self, fail_vocab=False, totals=None, chip_counts=None):
        self.bodies = []
        self.fail_vocab = fail_vocab
        self.totals = totals or {}
        self.chip_counts = chip_counts or {}

    def search(self, index=None, body=None):
        self.bodies.append(body)
        if "query" not in body:  # the vocabulary probe
            if self.fail_vocab:
                raise RuntimeError("index_not_found_exception")
            return {"aggregations": {f: _buckets(v) for f, v in VOCAB.items()}}
        if self._is_chip_probe(body):
            return {
                "aggregations": {
                    name: {"doc_count": self.chip_counts.get(name, 7)}
                    for name in body["aggs"]
                }
            }
        n = self.totals.get(len(self.searches) - 1, 2)
        hits = [{"_id": "urn:a", "_source": {"urn": "urn:a", "title": "a"}, "_score": 1.0}]
        hits = hits[:n]
        if "knn" in body["query"]:
            return {"hits": {"total": {"value": n}, "hits": hits}}
        return {
            "hits": {"total": {"value": n}, "hits": hits},
            "aggregations": {
                **{f: _buckets(v) for f, v in VOCAB.items()},
                "published_month": _buckets(["2026-06"]),
            },
        }

    @staticmethod
    def _is_chip_probe(body):
        return any(k.startswith("chip_") for k in (body.get("aggs") or {}))

    @property
    def searches(self):
        """Bodies of ranked searches (the vocabulary and chip-count probes aren't)."""
        return [
            b for b in self.bodies if "query" in b and not self._is_chip_probe(b)
        ]

    @property
    def chip_probes(self):
        return [b for b in self.bodies if "query" in b and self._is_chip_probe(b)]


def _client(monkeypatch, **kw):
    fake = FakeOS(**kw)
    monkeypatch.setattr(search_api, "osc", fake)
    search_api._vocab_cache.clear()
    c = TestClient(search_api.app)
    c.fake = fake
    return c


@pytest.fixture
def client(monkeypatch):
    return _client(monkeypatch)


def _get(client, **params):
    params.setdefault("mode", "bm25")
    r = client.get("/api/search", params=params)
    assert r.status_code == 200
    return r.json()


def test_understand_0_returns_the_pre_w4_response_shape(client):
    body = _get(client, q="besluiten van OCW over subsidie juni 2026", understand=0)
    assert set(body) == PRE_W4_BM25_KEYS


def test_understand_0_leaves_the_query_and_filters_untouched(client):
    q = "besluiten van OCW over subsidie juni 2026"
    _get(client, q=q, understand=0)
    (sent,) = client.fake.searches
    assert sent["query"] == search_api._bm25_query(q, [])
    assert sent["query"]["bool"]["filter"] == []


def test_understand_0_does_not_even_probe_the_vocabulary(client):
    _get(client, q="OCW juni 2026", understand=0)
    assert len(client.fake.bodies) == 1  # the search, and nothing else


def test_understanding_is_on_by_default_and_filters_the_search(client):
    q = "besluiten van OCW over subsidie juni 2026"
    body = _get(client, q=q)
    assert set(body) == PRE_W4_BM25_KEYS | UNDERSTANDING_KEYS
    assert body["understanding_used"] is True
    assert body["understanding_fallback_reason"] is None
    assert body["q_effective"] == "besluiten subsidie"
    assert {c["field"] for c in body["chips"]} == {"publisher", "published_month"}

    (sent,) = client.fake.searches
    assert sent["query"]["bool"]["filter"] == [
        {"terms": {"publisher": [OCW]}},
        {"terms": {"published_month": ["2026-06"]}},
    ]
    # the residue, not the raw query, is what BM25 sees
    assert sent["query"]["bool"]["must"]["multi_match"]["query"] == "besluiten subsidie"


def test_an_explicit_facet_beats_the_parser_and_gives_its_words_back(client):
    body = _get(
        client,
        q="besluiten van OCW over subsidie juni 2026",
        publisher="ministerie van Economische Zaken en Klimaat",
    )
    assert {c["field"] for c in body["chips"]} == {"published_month"}
    # the publisher chip's span returns to the query rather than vanishing
    assert body["q_effective"] == "besluiten OCW subsidie"
    (sent,) = client.fake.searches
    assert sent["query"]["bool"]["filter"] == [
        {"terms": {"publisher": ["ministerie van Economische Zaken en Klimaat"]}},
        {"terms": {"published_month": ["2026-06"]}},
    ]


def test_understanding_fails_open(monkeypatch):
    client = _client(monkeypatch, fail_vocab=True)
    fake = client.fake
    body = _get(client, q="besluiten van OCW over subsidie juni 2026")

    assert body["understanding_used"] is False
    assert "index_not_found_exception" in body["understanding_fallback_reason"]
    assert body["chips"] == []
    # the raw query still ran, unfiltered — parsing never gates search
    (sent,) = [b for b in fake.bodies if "query" in b]
    assert sent["query"]["bool"]["must"]["multi_match"]["query"].startswith("besluiten van OCW")
    assert sent["query"]["bool"]["filter"] == []


def test_the_vocabulary_probe_is_cached_across_requests(client):
    for _ in range(3):
        _get(client, q="OCW subsidie")
    assert len([b for b in client.fake.bodies if "query" not in b]) == 1


def test_an_empty_query_keeps_the_pre_w4_shape(client):
    assert set(_get(client, q="")) == PRE_W4_BM25_KEYS


# --- step 2: zero-result guard, chip counts, chip removal -------------------


def test_a_chip_set_that_returns_nothing_is_offered_not_applied(monkeypatch):
    # first (chipped) search finds nothing, the raw retry finds something
    client = _client(monkeypatch, totals={0: 0, 1: 5})
    body = _get(client, q="besluiten van OCW over subsidie juni 2026")

    assert body["chips_applied"] is False
    assert body["understanding_fallback_reason"] == "chips would return 0 results"
    assert body["total"] == 5
    # the chips are still reported, so the UI can offer them
    assert {c["field"] for c in body["chips"]} == {"publisher", "published_month"}
    # ... and the results shown are the raw query's, unfiltered
    chipped, raw = client.fake.searches
    assert chipped["query"]["bool"]["filter"]
    assert raw["query"]["bool"]["filter"] == []
    assert raw["query"]["bool"]["must"]["multi_match"]["query"].startswith("besluiten van OCW")
    assert body["q_effective"] == "besluiten van OCW over subsidie juni 2026"


def test_the_guard_leaves_the_chips_applied_when_the_raw_query_is_empty_too(monkeypatch):
    client = _client(monkeypatch, totals={0: 0, 1: 0})
    body = _get(client, q="besluiten van OCW over subsidie juni 2026")

    # nothing to fall back to: an honest zero beats an unfiltered zero
    assert body["chips_applied"] is True
    assert body["total"] == 0
    assert body["q_effective"] == "besluiten subsidie"


def test_the_guard_does_not_retry_when_the_chipped_search_found_something(client):
    _get(client, q="besluiten van OCW over subsidie juni 2026")
    assert len(client.fake.searches) == 1


def test_every_chip_carries_the_count_it_alone_would_yield(monkeypatch):
    client = _client(monkeypatch, chip_counts={"chip_0": 113, "chip_1": 42})
    body = _get(client, q="besluiten van OCW over subsidie juni 2026")

    assert [c["count"] for c in body["chips"]] == [113, 42]
    # one probe, and it counts each chip against the residue + explicit facets
    (probe,) = client.fake.chip_probes
    assert probe["size"] == 0
    counted = [a["filter"]["bool"]["filter"] for a in probe["aggs"].values()]
    assert counted == [
        [{"terms": {"publisher": [OCW]}}],
        [{"terms": {"published_month": ["2026-06"]}}],
    ]
    for agg in probe["aggs"].values():
        assert agg["filter"]["bool"]["must"]["multi_match"]["query"] == "besluiten subsidie"


def test_chip_counts_are_decoration_and_never_gate_the_search(monkeypatch):
    class Grumpy(FakeOS):
        def search(self, index=None, body=None):
            if self._is_chip_probe(body or {}):
                raise RuntimeError("agg blew up")
            return super().search(index=index, body=body)

    fake = Grumpy()
    monkeypatch.setattr(search_api, "osc", fake)
    search_api._vocab_cache.clear()
    body = _get(TestClient(search_api.app), q="besluiten van OCW over subsidie juni 2026")

    assert body["chips_applied"] is True
    assert all("count" not in c for c in body["chips"])
    assert body["total"] == 2  # the search itself ran, filtered, as normal


def test_a_removed_chip_stops_filtering_and_gives_its_words_back(client):
    body = _get(
        client, q="besluiten van OCW over subsidie juni 2026", drop_chip="publisher:ocw"
    )
    assert {c["field"] for c in body["chips"]} == {"published_month"}
    assert body["q_effective"] == "besluiten OCW subsidie"
    (sent,) = client.fake.searches
    assert sent["query"]["bool"]["filter"] == [{"terms": {"published_month": ["2026-06"]}}]


def test_dropping_an_unknown_chip_changes_nothing(client):
    body = _get(
        client, q="besluiten van OCW over subsidie juni 2026", drop_chip="publisher:ezk"
    )
    assert {c["field"] for c in body["chips"]} == {"publisher", "published_month"}


def test_the_hybrid_payload_carries_the_understanding_too(client, monkeypatch):
    monkeypatch.setattr(search_api, "_embed_query", lambda q: [0.1, 0.2, 0.3])
    body = _get(client, q="besluiten van OCW over subsidie juni 2026", mode="hybrid")
    assert set(body) == PRE_W4_HYBRID_KEYS | UNDERSTANDING_KEYS
    assert body["mode_used"] == "hybrid"
    assert body["q_effective"] == "besluiten subsidie"
    # the dense leg is filtered by the chips as well
    knn = [b for b in client.fake.searches if "knn" in b["query"]][0]
    assert knn["query"]["knn"]["embedding"]["filter"]["bool"]["filter"] == [
        {"terms": {"publisher": [OCW]}},
        {"terms": {"published_month": ["2026-06"]}},
    ]
