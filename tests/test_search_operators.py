"""Operators in the search path (W11(a), design/search-operators.md §3/§4/§6).

The grammar itself is pinned in `test_query_ops.py`; this file pins the three things
only the endpoint can get wrong: the no-change contract for operator-free queries, the
fact that operators and W4 chips do not both run, and that an exclusion reaches the
*dense* leg as well as the BM25 one.

The fake OpenSearch client is the one the W4 tests already use — one fake, so a change
to the request shape breaks both files rather than drifting between them.
"""

import pytest
from fastapi.testclient import TestClient
from test_search_api_understand import OCW, FakeOS

from engine import query_ops
from ui import search_api

PLAIN = "besluiten van OCW over subsidie juni 2026"


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


def _must(body):
    return body["query"]["bool"]["must"]


# --- the no-change contract --------------------------------------------------


def test_a_query_without_operators_gets_no_operators_key(client):
    body = _get(client, q=PLAIN)
    assert "operators" not in body


def test_a_query_without_operators_sends_the_same_clause_as_before(client):
    _get(client, q="subsidie onderwijs", understand=0)
    (sent,) = client.fake.searches
    assert _must(sent) == {
        "multi_match": {
            "query": "subsidie onderwijs",
            "fields": ["title^2", "content"],
            "operator": "and",
            "type": "cross_fields",
        }
    }


def test_operators_0_searches_the_syntax_literally(client):
    q = 'subsidie -onderwijs "vaste lasten"'
    body = _get(client, q=q, understand=0, operators=0)
    assert "operators" not in body
    (sent,) = client.fake.searches
    assert _must(sent)["multi_match"]["query"] == q  # exactly the pre-W11 behaviour


# --- what the operators do ---------------------------------------------------


def test_a_quoted_phrase_is_searched_as_a_phrase_and_reported(client):
    body = _get(client, q='"vaste lasten"')
    assert body["operators"] == {
        "used": True,
        "parsed": '"vaste lasten"',
        "warnings": [],
        "exclusion_in_or": False,
    }
    (sent,) = client.fake.searches
    assert _must(sent) == {
        "multi_match": {
            "query": "vaste lasten",
            "fields": ["title^2", "content"],
            "type": "phrase",
        }
    }


def test_explicit_facets_still_filter_an_operator_query(client):
    _get(client, q="subsidie OR onderwijs", publisher=OCW)
    (sent,) = client.fake.searches
    assert sent["query"]["bool"]["filter"] == [{"terms": {"publisher": [OCW]}}]
    assert _must(sent)["bool"]["minimum_should_match"] == 1


def test_an_unusable_operator_query_still_searches_and_says_it_stepped_aside(client):
    body = _get(client, q="()")
    assert body["operators"]["used"] is False
    assert body["operators"]["warnings"]  # why, in the reader's language
    (sent,) = client.fake.searches
    assert _must(sent)["multi_match"]["query"] == "()"  # searched literally


def test_a_repaired_query_reports_the_repair(client):
    body = _get(client, q='"vaste lasten')
    assert body["operators"]["used"] is True
    assert any("afgesloten" in w for w in body["operators"]["warnings"])


# --- §9: chips compose with operators, from safe positions only --------------


def test_a_fully_quoted_query_yields_no_chips_and_keeps_every_word(client):
    body = _get(client, q=f'"{PLAIN}"')
    assert body["chips"] == []
    assert body["chips_applied"] is False
    assert body["understanding_used"] is False
    assert (
        body["understanding_fallback_reason"]
        == "operator query with no free terms to read as filters"
    )
    # the phrase reaches OpenSearch whole — no chip took "OCW" or "juni 2026" out of it
    (sent,) = client.fake.searches
    assert _must(sent)["multi_match"]["query"] == PLAIN
    assert sent["query"]["bool"]["filter"] == []


def test_the_vocabulary_is_not_probed_when_nothing_is_chip_able(client):
    _get(client, q=f'"{PLAIN}"')
    assert len(client.fake.bodies) == 1  # the search, and nothing else


def test_a_phrase_survives_while_the_free_terms_become_chips(client):
    body = _get(client, q=f'"vaste lasten" {PLAIN}')
    assert body["chips_applied"] is True
    assert {c["field"] for c in body["chips"]} == {"publisher", "published_month"}
    # the phrase is untouched and the chipped words are gone from the text
    assert body["q_effective"] == '"vaste lasten" AND besluiten AND subsidie'
    assert body["operators"]["parsed"] == body["q_effective"]
    (sent,) = client.fake.searches
    assert sent["query"]["bool"]["filter"] == [
        {"terms": {"publisher": [OCW]}},
        {"terms": {"published_month": ["2026-06"]}},
    ]
    clauses = _must(sent)["bool"]["must"]
    assert clauses[0]["multi_match"]["query"] == "vaste lasten"
    assert clauses[0]["multi_match"]["type"] == "phrase"
    assert [c["multi_match"]["query"] for c in clauses[1:]] == ["besluiten", "subsidie"]


def test_no_chip_is_taken_from_inside_an_or_branch(client):
    # `subsidie OR (onderwijs juni 2026)` must not yield a global date filter: it would
    # drop every `subsidie` document from another year (§9.1)
    body = _get(client, q="subsidie OF (onderwijs juni 2026)")
    assert body["chips"] == []
    (sent,) = client.fake.searches
    assert sent["query"]["bool"]["filter"] == []
    assert _must(sent)["bool"]["minimum_should_match"] == 1


def test_no_chip_is_taken_from_inside_a_phrase(client):
    body = _get(client, q='"OCW juni 2026" subsidie')
    assert body["chips"] == []
    (sent,) = client.fake.searches
    assert sent["query"]["bool"]["filter"] == []


def test_a_negated_term_is_never_read_as_a_filter(client):
    body = _get(client, q="besluiten -OCW subsidie")
    assert body["chips"] == []  # the user excluded it; a filter would include it
    (sent,) = client.fake.searches
    assert sent["query"]["bool"]["filter"] == []
    assert _must(sent)["bool"]["must_not"][0]["multi_match"]["query"] == "OCW"


def test_stranded_connectives_go_with_the_chips_but_operators_never_do(client):
    body = _get(client, q="besluiten van OCW EN subsidie juni 2026")
    # "van" is dropped as a stranded word; the EN is structure, so it cannot be touched
    assert body["q_effective"] == "besluiten AND subsidie"
    assert {c["text"] for c in body["chips"]} == {"OCW", "juni 2026"}


def test_a_query_entirely_consumed_by_chips_searches_the_filters_alone(client):
    body = _get(client, q="(juni 2026)")
    assert body["chips_applied"] is True
    assert body["q_effective"] == ""
    # said out loud: `used: false` on its own would read as "the operators broke"
    assert body["operators"]["consumed_by_chips"] is True
    (sent,) = client.fake.searches
    assert _must(sent) == {"match_all": {}}
    assert sent["query"]["bool"]["filter"] == [{"terms": {"published_month": ["2026-06"]}}]


def test_a_removed_chip_puts_its_words_back_into_the_operator_query(client):
    body = _get(client, q=f'"vaste lasten" {PLAIN}', drop_chip="publisher:ocw")
    assert {c["field"] for c in body["chips"]} == {"published_month"}
    assert body["q_effective"] == '"vaste lasten" AND besluiten AND OCW AND subsidie'


def test_an_explicit_facet_still_beats_the_parser_here_too(client):
    body = _get(client, q=f'"vaste lasten" {PLAIN}', publisher="ministerie van Economische Zaken en Klimaat")
    assert {c["field"] for c in body["chips"]} == {"published_month"}
    assert "OCW" in body["q_effective"]


def test_chip_counts_are_taken_against_the_pruned_query(client):
    _get(client, q=f'"vaste lasten" {PLAIN}')
    (probe,) = client.fake.chip_probes
    for agg in probe["aggs"].values():
        clauses = agg["filter"]["bool"]["must"]["bool"]["must"]
        assert clauses[0]["multi_match"]["type"] == "phrase"


def test_the_zero_result_guard_reverts_to_the_raw_operator_query(monkeypatch):
    client = _client(monkeypatch, totals={0: 0, 1: 5})
    body = _get(client, q=f'"vaste lasten" {PLAIN}')
    assert body["chips_applied"] is False
    assert body["understanding_fallback_reason"] == "chips would return 0 results"
    # the operator block describes the query that actually ran, not the pruned one
    assert "OCW" in body["operators"]["parsed"]
    chipped, raw = client.fake.searches
    assert chipped["query"]["bool"]["filter"]
    assert raw["query"]["bool"]["filter"] == []


def test_chips_still_work_when_the_query_has_no_operators(client):
    body = _get(client, q=PLAIN)
    assert body["understanding_used"] is True
    assert {c["field"] for c in body["chips"]} == {"publisher", "published_month"}


# --- the dense leg -----------------------------------------------------------


@pytest.fixture
def embedded(monkeypatch):
    """Records what the dense leg was asked to embed."""
    seen: list[str] = []

    def fake_embed(text):
        seen.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(search_api, "_embed_query", fake_embed)
    return seen


def test_the_embedder_gets_the_words_not_the_syntax(client, embedded):
    _get(client, q='"vaste lasten" OR subsidie', mode="hybrid")
    assert embedded == ["vaste lasten subsidie"]


def test_an_excluded_word_is_neither_embedded_nor_reachable_by_knn(client, embedded):
    body = _get(client, q="subsidie -onderwijs", mode="hybrid")
    assert body["mode_used"] == "hybrid"
    assert embedded == ["subsidie"]
    knn = [b for b in client.fake.searches if "knn" in b["query"]][0]
    assert knn["query"]["knn"]["embedding"]["filter"]["bool"]["filter"] == [
        {"bool": {"must_not": list(query_ops.compile_query("subsidie -onderwijs").exclusions)}}
    ]


def test_a_negation_under_an_or_drops_to_bm25_and_says_why(client, embedded):
    body = _get(client, q="subsidie OR NOT onderwijs", mode="hybrid_rerank")
    assert body["mode_used"] == "bm25"
    assert "NOT inside an OR" in body["mode_fallback_reason"]
    # the UI phrases this itself, in Dutch, off the parse fact rather than the string
    assert body["operators"]["exclusion_in_or"] is True
    assert embedded == []  # the dense leg was never asked
    # BM25 still answers the query exactly, operators and all
    (sent,) = client.fake.searches
    assert _must(sent)["bool"]["minimum_should_match"] == 1


def test_a_query_that_is_only_an_exclusion_falls_back_rather_than_embedding_nothing(
    client, embedded
):
    body = _get(client, q="-onderwijs", mode="hybrid")
    assert body["mode_used"] == "bm25"
    assert embedded == []
    (sent,) = client.fake.searches
    assert _must(sent) == {"bool": {"must_not": [
        {
            "multi_match": {
                "query": "onderwijs",
                "fields": ["title^2", "content"],
                "operator": "and",
                "type": "cross_fields",
            }
        }
    ]}}


def test_the_hybrid_response_carries_the_operator_report_too(client, embedded):
    body = _get(client, q='"vaste lasten" subsidie', mode="hybrid")
    assert body["mode_used"] == "hybrid"
    assert body["operators"]["parsed"] == '"vaste lasten" AND subsidie'


# --- phrase-aware highlighting (§10) -----------------------------------------


def test_a_phrase_query_switches_to_the_plain_highlighter(client):
    _get(client, q='"vaste lasten"')
    (sent,) = client.fake.searches
    assert sent["highlight"]["fields"]["content"]["type"] == "plain"
    # the cap stays: without it a 3.1M-character document fails the whole search
    assert sent["highlight"]["fields"]["content"]["max_analyzer_offset"] == 999_999


@pytest.mark.parametrize("q", ["subsidie onderwijs", "subsidie -onderwijs", "veror*"])
def test_every_other_query_keeps_the_highlighter_it_had(client, q):
    _get(client, q=q, understand=0)
    (sent,) = client.fake.searches
    assert "type" not in sent["highlight"]["fields"]["content"]


def test_a_phrase_anywhere_in_the_query_counts(client):
    # the highlighter picks a fragment for the document, not per boolean branch
    _get(client, q='subsidie OF "vaste lasten"')
    (sent,) = client.fake.searches
    assert sent["highlight"]["fields"]["content"]["type"] == "plain"
