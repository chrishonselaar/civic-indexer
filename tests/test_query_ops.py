"""The operator grammar (W11(a), design/search-operators.md).

Pure parser tests: no OpenSearch, no endpoint. The load-bearing one is the first —
a query without operator syntax must come back untouched, because that is what makes
the feature safe to default on (same contract W4 gave itself with `understand=0`).
"""

import pytest

from engine import query_ops


def _p(q: str) -> query_ops.Parsed:
    return query_ops.compile_query(q)


# --- detection: the no-change contract --------------------------------------


@pytest.mark.parametrize(
    "q",
    [
        "",
        "   ",
        "subsidie onderwijs",
        "besluiten van OCW over subsidie juni 2026",
        "COVID-19 maatregelen",  # an internal hyphen is not an exclusion
        "Wob - besluit",  # nor is a spaced dash
        "wat is dit?",  # `?` is deliberately not a wildcard
        "e-mailadres",
    ],
)
def test_a_query_without_operators_is_left_completely_alone(q):
    assert query_ops.has_operators(q) is False
    assert _p(q) is query_ops.NO_OPERATORS


@pytest.mark.parametrize(
    "q",
    [
        '"exacte zin"',
        "subsidie AND onderwijs",
        "subsidie EN onderwijs",
        "subsidie OF onderwijs",
        "subsidie NIET onderwijs",
        "subsidie -onderwijs",
        "veror*",
        "(a OR b) c",
    ],
)
def test_operator_syntax_is_detected(q):
    assert query_ops.has_operators(q) is True


def test_lowercase_dutch_connectives_are_words_not_operators():
    # "en"/"of" are ordinary Dutch (and stranded words in query_parse); treating them
    # as operators would turn half the corpus's natural phrasing into syntax.
    assert query_ops.has_operators("besluiten en vergunningen of nota's") is False


# --- phrases -----------------------------------------------------------------


def test_a_quoted_phrase_becomes_a_phrase_match():
    p = _p('"college van burgemeester en wethouders"')
    assert p.used is True
    assert p.clause == {
        "multi_match": {
            "query": "college van burgemeester en wethouders",
            "fields": ["title^2", "content"],
            "type": "phrase",
        }
    }
    assert p.canonical == '"college van burgemeester en wethouders"'
    assert p.dense_text == "college van burgemeester en wethouders"


def test_a_phrase_next_to_a_term_ands_them():
    p = _p('"vaste lasten" subsidie')
    assert p.clause["bool"]["must"] == [
        {
            "multi_match": {
                "query": "vaste lasten",
                "fields": ["title^2", "content"],
                "type": "phrase",
            }
        },
        {
            "multi_match": {
                "query": "subsidie",
                "fields": ["title^2", "content"],
                "operator": "and",
                "type": "cross_fields",
            }
        },
    ]
    assert p.canonical == '"vaste lasten" AND subsidie'


def test_an_unclosed_quote_is_repaired_and_reported():
    p = _p('"vaste lasten subsidie')
    assert p.used is True
    assert p.clause["multi_match"]["type"] == "phrase"
    assert any("afgesloten" in w for w in p.warnings)


def test_a_wildcard_inside_a_phrase_stays_literal():
    p = _p('"artikel 5*"')
    assert p.clause["multi_match"]["type"] == "phrase"
    assert p.clause["multi_match"]["query"] == "artikel 5*"


# --- boolean structure -------------------------------------------------------


def test_or_compiles_to_a_should_with_one_required_match():
    p = _p("subsidie OF subsidieverlening")
    assert p.clause["bool"]["minimum_should_match"] == 1
    assert [c["multi_match"]["query"] for c in p.clause["bool"]["should"]] == [
        "subsidie",
        "subsidieverlening",
    ]
    assert p.canonical == "subsidie OR subsidieverlening"


def test_dutch_and_english_keywords_are_the_same_operator():
    assert _p("a EN b").clause == _p("a AND b").clause
    assert _p("a OF b").clause == _p("a OR b").clause
    assert _p("a NIET b").clause == _p("a NOT b").clause


def test_and_binds_tighter_than_or():
    p = _p("a AND b OR c")
    should = p.clause["bool"]["should"]
    assert len(should) == 2
    assert [c["multi_match"]["query"] for c in should[0]["bool"]["must"]] == ["a", "b"]
    assert p.canonical == "(a AND b) OR c"


def test_parentheses_override_precedence():
    p = _p("(a OR b) AND c")
    must = p.clause["bool"]["must"]
    assert must[0]["bool"]["minimum_should_match"] == 1
    assert must[1]["multi_match"]["query"] == "c"
    assert p.canonical == "(a OR b) AND c"


def test_juxtaposition_still_means_and():
    # the pre-operator default was `operator: and`; adding operators must not
    # quietly turn a two-word query into an OR
    assert _p("(a) b").canonical == "a AND b"


# --- negation ----------------------------------------------------------------


@pytest.mark.parametrize("q", ["subsidie -onderwijs", "subsidie NOT onderwijs"])
def test_exclusion_lands_in_must_not(q):
    p = _p(q)
    assert p.clause["bool"]["must"][0]["multi_match"]["query"] == "subsidie"
    assert p.clause["bool"]["must_not"][0]["multi_match"]["query"] == "onderwijs"
    assert p.canonical == "subsidie AND NOT onderwijs"


def test_a_dash_glued_to_a_quoted_phrase_negates_it():
    p = _p('subsidie -"hoger onderwijs"')
    assert p.clause["bool"]["must_not"][0]["multi_match"]["type"] == "phrase"


def test_a_conjunctive_exclusion_is_reusable_as_a_filter():
    p = _p("subsidie -onderwijs")
    assert p.nested_negation is False
    assert p.exclusions == (
        {
            "multi_match": {
                "query": "onderwijs",
                "fields": ["title^2", "content"],
                "operator": "and",
                "type": "cross_fields",
            }
        },
    )
    # the excluded word is not embedded — the dense leg would retrieve exactly what
    # the user ruled out
    assert p.dense_text == "subsidie"


def test_a_negation_under_an_or_is_flagged_as_inexpressible():
    p = _p("subsidie OR NOT onderwijs")
    assert p.used is True  # BM25 can still answer it exactly
    assert p.nested_negation is True
    assert p.exclusions == ()


def test_a_query_that_is_only_an_exclusion_still_compiles():
    p = _p("-onderwijs")
    assert p.used is True
    assert p.clause == {"bool": {"must_not": [_p("onderwijs OR x").clause["bool"]["should"][0]]}}
    assert p.dense_text == ""  # nothing positive to embed


# --- wildcards ---------------------------------------------------------------


def test_a_trailing_wildcard_becomes_an_analyzed_query_string():
    p = _p("veror*")
    assert p.clause == {
        "query_string": {
            "query": "veror*",
            "fields": ["title^2", "content"],
            "analyze_wildcard": True,
            "default_operator": "and",
        }
    }
    assert p.dense_text == "veror"  # the star is not a word


def test_query_string_metacharacters_are_escaped():
    p = _p("beslui*t^2")
    assert p.clause["query_string"]["query"] == "beslui*t\\^2"


def test_a_leading_wildcard_is_stripped_and_reported():
    p = _p("*wet")
    assert p.clause["query_string"]["query"] == "wet"
    assert any("begin" in w for w in p.warnings)


# --- degenerate input: fail open, never raise --------------------------------


@pytest.mark.parametrize(
    "q", ["AND", "OR OR OR", "NOT", "()", ")))", '"', "( ( (", "-", "*", "- -", "*)-("]
)
def test_degenerate_operator_input_never_raises_and_never_lies(q):
    p = _p(q)
    # Either it compiled to something searchable, or it stepped aside entirely and the
    # caller searches the text literally. There is no third state.
    assert (p.used and p.clause is not None) or (not p.used and p.clause is None)


def test_a_lone_dash_or_star_is_not_operator_syntax_at_all():
    # Nothing to negate and nothing to complete: these are punctuation, and treating
    # them as syntax would make "-" in a title a search operator.
    for q in ("-", "*", "- -"):
        assert _p(q) is query_ops.NO_OPERATORS


def test_an_unmatched_open_paren_is_closed_at_the_end():
    p = _p("(subsidie OR onderwijs")
    assert p.used is True
    assert p.clause["bool"]["minimum_should_match"] == 1
    assert any("haakje" in w for w in p.warnings)


def test_a_dangling_operator_is_dropped_not_fatal():
    p = _p("subsidie AND")
    assert p.used is True
    assert p.clause["multi_match"]["query"] == "subsidie"


def test_nothing_searchable_leaves_used_false_so_the_caller_falls_back():
    p = _p("()")
    assert p.detected is True and p.used is False and p.clause is None
    assert p.warnings
