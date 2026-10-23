"""Cross-corpus search: one query over every source (design/cross-corpus-search.md).

Each source keeps its own OpenSearch index; searching across them is a query-time
union of `civic-*` minus the chunk indices. These tests pin the *target* the API
builds and the two places the union is not free — document lookup (no `_id` `get`
across indices, and no guessing the index from the URN) and reranking (a mixed pool
is governed by its strictest member).

Driven against a fake client that records the index it was asked for, so the
contract holds without a cluster.
"""

import pytest
from fastapi.testclient import TestClient

from engine.project import mappings
from ui import search_api

ALL = mappings.ALL_DOC_INDICES


class FakeOS:
    """Records (index, body) per call and answers plausibly for each shape."""

    def __init__(self, hits=None, mget_docs=None):
        self.calls = []
        self.mgets = []
        self.hits = hits if hits is not None else [
            {"_index": "civic-woo_besluiten", "_id": "urn:a",
             "_source": {"urn": "urn:a", "title": "a", "source_id": "woo_besluiten"},
             "_score": 1.0},
        ]
        self.mget_docs = mget_docs

    def search(self, index=None, body=None):
        self.calls.append((index, body))
        if "query" not in body:  # vocabulary probe
            return {"aggregations": {}}
        return {
            "hits": {"total": {"value": len(self.hits)}, "hits": self.hits},
            "aggregations": {
                "source_id": {"buckets": [{"key": "woo_besluiten", "doc_count": 3},
                                          {"key": "tuchtrecht", "doc_count": 2}]},
            },
        }

    def mget(self, body=None, **kw):
        self.mgets.append(body)
        return {"docs": self.mget_docs or []}

    @property
    def indices_searched(self):
        return [index for index, _ in self.calls]

    @property
    def ranked(self):
        return [(i, b) for i, b in self.calls if "query" in b]


@pytest.fixture
def fake(monkeypatch):
    f = FakeOS()
    monkeypatch.setattr(search_api, "osc", f)
    search_api._vocab_cache.clear()
    return f


@pytest.fixture
def client(fake):
    return TestClient(search_api.app)


# ------------------------------------------------------------------ the target


def test_the_default_target_is_every_corpus_and_excludes_the_chunk_indices():
    assert mappings.doc_index_target(None) == ALL
    assert mappings.doc_index_target("") == ALL
    assert mappings.doc_index_target("all") == ALL
    # the exclusion is what keeps chunks — a different document shape — out
    assert "-civic-*-chunks" in ALL
    assert mappings.chunk_index_name("woo_besluiten") == "civic-woo_besluiten-chunks"


def test_a_named_dataset_still_targets_exactly_one_index():
    assert mappings.doc_index_target("woo_besluiten") == "civic-woo_besluiten"


def test_search_without_a_dataset_searches_every_corpus(client, fake):
    client.get("/api/search", params={"q": "subsidie", "mode": "bm25"})
    assert fake.indices_searched and set(fake.indices_searched) == {ALL}


def test_search_with_a_dataset_narrows_to_that_index(client, fake):
    client.get("/api/search", params={"q": "subsidie", "mode": "bm25",
                                      "dataset": "tuchtrecht"})
    assert set(fake.indices_searched) == {"civic-tuchtrecht"}


# ------------------------------------------------------- source as a facet/filter


def test_the_source_facet_is_aggregated_so_a_search_shows_what_it_found_where(client):
    body = client.get("/api/search", params={"q": "subsidie", "mode": "bm25"}).json()
    assert body["facets"]["source_id"] == [
        {"value": "woo_besluiten", "count": 3},
        {"value": "tuchtrecht", "count": 2},
    ]


def test_narrowing_to_sources_is_a_filter_not_a_different_index(client, fake):
    """Two of four corpora is a facet selection, so it has to be expressible — and it
    must not silently become an index switch, or the facet counts would stop meaning
    'out of everything searched'."""
    client.get("/api/search", params={"q": "subsidie", "mode": "bm25",
                                      "source": ["tuchtrecht", "haarlem_raad"]})
    (index, body), = fake.ranked
    assert index == ALL
    assert {"terms": {"source_id": ["tuchtrecht", "haarlem_raad"]}} in \
        body["query"]["bool"]["filter"]


# ------------------------------------------------------------ document lookup


def test_a_document_is_found_by_urn_across_corpora_not_by_id_in_one(client, fake):
    """`get` needs one index; the union has four. And the URN prefix names the
    *provider*, never the source, so it may not be turned into an index name — the
    same rule takedowns are pinned to (tests/test_suppression.py)."""
    fake.hits = [{"_index": "civic-tuchtrecht", "_id": "notubiz:262:1",
                  "_source": {"urn": "notubiz:262:1", "title": "t", "exposure": "T2"}}]
    body = client.get("/api/doc/notubiz:262:1").json()
    assert body["title"] == "t"
    (index, sent), = fake.ranked
    assert index == ALL
    assert sent["query"] == {"term": {"urn": "notubiz:262:1"}}


def test_a_missing_document_is_a_404_not_an_empty_record(client, fake):
    fake.hits = []
    assert client.get("/api/doc/urn:nope").status_code == 404


def test_related_is_cross_corpus_by_default(client, fake):
    fake.hits = [
        {"_index": "civic-woo_besluiten", "_id": "urn:a",
         "_source": {"urn": "urn:a", "embedding": [0.1, 0.2]}, "_score": 1.0},
    ]
    body = client.get("/api/doc/urn:a/related").json()
    # the seed itself is dropped; it is the only hit the fake returns
    assert body["related"] == []
    assert set(fake.indices_searched) == {ALL}


def test_related_can_be_narrowed_back_to_one_corpus(client, fake):
    fake.hits = [
        {"_index": "civic-woo_besluiten", "_id": "urn:a",
         "_source": {"urn": "urn:a", "embedding": [0.1]}, "_score": 1.0},
    ]
    client.get("/api/doc/urn:a/related", params={"dataset": "woo_besluiten"})
    assert set(fake.indices_searched) == {"civic-woo_besluiten"}


def test_a_neighbour_in_another_corpus_is_returned_and_says_which(client, fake):
    """Exit criterion #3: a related document across *sources*."""
    fake.hits = [
        {"_index": "civic-woo_besluiten", "_id": "urn:a",
         "_source": {"urn": "urn:a", "embedding": [0.1]}, "_score": 1.0},
        {"_index": "civic-haarlem_raad", "_id": "urn:b",
         "_source": {"urn": "urn:b", "title": "b", "source_id": "haarlem_raad"},
         "_score": 0.93},
    ]
    body = client.get("/api/doc/urn:a/related").json()
    assert [d["urn"] for d in body["related"]] == ["urn:b"]
    assert body["related"][0]["source_id"] == "haarlem_raad"


# ------------------------------------------------------------------- reranking


def _hybrid(monkeypatch, fake, mget_docs):
    monkeypatch.setattr(search_api, "_embed_query", lambda q: [0.1, 0.2])
    monkeypatch.setattr(search_api.retrieval, "rerank_available", lambda: True)
    fake.mget_docs = mget_docs
    return TestClient(search_api.app).get(
        "/api/search", params={"q": "subsidie", "mode": "hybrid_rerank",
                               "understand": 0}
    ).json()


def test_a_mixed_pool_reranks_only_if_every_source_in_it_opted_in(monkeypatch, fake):
    """The pool is governed by its strictest member. Across corpora that is a live
    constraint, not a theoretical one: three of four sources declare no processors."""
    body = _hybrid(monkeypatch, fake, [
        {"_source": {"title": "a", "content": "x", "source_id": "woo_besluiten",
                     "processors": ["voyage_rerank"]}},
        {"_source": {"title": "b", "content": "y", "source_id": "haarlem_raad",
                     "processors": []}},
    ])
    assert body["mode_used"] == "hybrid"
    # Named, so the answer is "these manifests", not "reranking stopped working".
    assert "haarlem_raad" in body["rerank_blocked"]
    assert "woo_besluiten" not in body["rerank_blocked"]


def test_the_rerank_lookup_addresses_documents_per_index(monkeypatch, fake):
    """`mget` by bare id needs one index; a union needs {_index, _id} pairs."""
    fake.hits = [
        {"_index": "civic-woo_besluiten", "_id": "urn:a",
         "_source": {"urn": "urn:a", "title": "a"}, "_score": 1.0},
        {"_index": "civic-tuchtrecht", "_id": "urn:b",
         "_source": {"urn": "urn:b", "title": "b"}, "_score": 0.9},
    ]
    _hybrid(monkeypatch, fake, [])
    (sent,) = fake.mgets
    assert sent["docs"] == [
        {"_index": "civic-woo_besluiten", "_id": "urn:a"},
        {"_index": "civic-tuchtrecht", "_id": "urn:b"},
    ]


def test_two_corpora_sharing_a_document_id_are_not_merged(monkeypatch, fake):
    """Fusion keys on index+id. Keyed on the id alone, a collision would silently
    drop one of the two documents from the pool."""
    fake.hits = [
        {"_index": "civic-haarlem_raad", "_id": "notubiz:1",
         "_source": {"urn": "notubiz:1", "title": "haarlem"}, "_score": 1.0},
        {"_index": "civic-vlaardingen_raad", "_id": "notubiz:1",
         "_source": {"urn": "notubiz:1", "title": "vlaardingen"}, "_score": 0.9},
    ]
    monkeypatch.setattr(search_api, "_embed_query", lambda q: [0.1, 0.2])
    body = TestClient(search_api.app).get(
        "/api/search", params={"q": "raad", "mode": "hybrid", "understand": 0}
    ).json()
    assert [r["title"] for r in body["results"]] == ["haarlem", "vlaardingen"]


# -------------------------------------------------- a union that lost a member


SHARD_FAILURE = {
    "_shards": {"total": 4, "successful": 3, "failed": 1, "failures": [
        {"index": "civic-tuchtrecht", "reason": {
            "type": "illegal_argument_exception",
            "reason": "Text fields are not optimised for aggregations"}}]},
}


def test_a_union_that_silently_lost_a_source_says_so(monkeypatch):
    """OpenSearch answers from the shards that worked and reports the rest inside a
    200. Across corpora that removes a whole source from the results, leaving a
    smaller, entirely plausible answer — so it has to be surfaced."""

    class Partial(FakeOS):
        def search(self, index=None, body=None):
            return {**super().search(index=index, body=body), **SHARD_FAILURE}

    monkeypatch.setattr(search_api, "osc", Partial())
    search_api._vocab_cache.clear()
    body = TestClient(search_api.app).get(
        "/api/search", params={"q": "subsidie", "mode": "bm25", "understand": 0}
    ).json()
    assert "civic-tuchtrecht" in body["incomplete"]
    assert "aggregations" in body["incomplete"]


def test_a_healthy_search_keeps_the_pre_w4_response_shape(client):
    """The warning is a conditional key: adding it unconditionally would break the
    `understand=0` regression contract that W4 is safe to default on."""
    body = client.get("/api/search",
                      params={"q": "subsidie", "mode": "bm25", "understand": 0}).json()
    assert "incomplete" not in body


# ---------------------------------------------------------------- the corpora list


def test_the_datasets_endpoint_reports_what_is_searchable_with_its_tier(monkeypatch):
    class Counting(FakeOS):
        def search(self, index=None, body=None):
            self.calls.append((index, body))
            return {"aggregations": {"source_id": {"buckets": [
                {"key": "tuchtrecht", "doc_count": 47950,
                 "exposure": {"buckets": [{"key": "T2"}]}},
                {"key": "haarlem_raad", "doc_count": 1017,
                 "exposure": {"buckets": [{"key": "T1"}]}},
            ]}}}

    f = Counting()
    monkeypatch.setattr(search_api, "osc", f)
    body = TestClient(search_api.app).get("/api/datasets").json()
    assert body["datasets"] == [
        {"source_id": "tuchtrecht", "docs": 47950, "exposure": ["T2"]},
        {"source_id": "haarlem_raad", "docs": 1017, "exposure": ["T1"]},
    ]
    assert f.indices_searched == [ALL]
