"""Tier 0 query understanding (W4): vocabulary, aliases, Dutch date grammar.

Pure — no OpenSearch, no LLM, no clock. The vocabulary is handed in and `now` is
passed explicitly, which is the whole point of the design's closed world: a chip
can only ever carry a value the corpus actually has, and a relative date is
frozen at parse time.
"""

from datetime import date

from engine import query_parse
from engine.query_parse import chip_filters, parse, residual

NOW = date(2026, 7, 24)

VOCAB = {
    "publisher": [
        "ministerie van Onderwijs, Cultuur en Wetenschap",
        "ministerie van Economische Zaken en Klimaat",
        "Raad van Discipline Amsterdam",
    ],
    "file_type": ["application/pdf", "application/zip"],
    "disposition": ["ok", "partial"],
}

OCW = "ministerie van Onderwijs, Cultuur en Wetenschap"


def _by_field(chips: list[dict]) -> dict:
    return {c["field"]: c for c in chips}


# --- the measured example (design/query-understanding.md §2b/§4) -------------


def test_the_measured_example_parses_completely():
    chips, q = parse("besluiten van OCW over subsidie juni 2026", VOCAB, NOW)
    got = _by_field(chips)
    assert got["publisher"]["values"] == [OCW]
    assert got["publisher"]["source"] == "alias"
    assert got["publisher"]["text"] == "OCW"
    assert got["published_month"]["values"] == ["2026-06"]
    assert got["published_month"]["source"] == "date_grammar"
    assert got["published_month"]["text"] == "juni 2026"
    # residue is what the design promises: the filter-bearing spans and the
    # connectors they stranded are gone, the search terms remain.
    assert q == "besluiten subsidie"


def test_every_chip_spans_the_text_it_claims():
    q = "zip besluiten van OCW juni 2026 partial"
    chips, _ = parse(q, VOCAB, NOW)
    assert chips  # sanity
    for chip in chips:
        start, end = chip["span"]
        assert q[start:end] == chip["text"]
        assert chip["kind"] == "filter"
        assert chip["source"] in ("vocab", "alias", "date_grammar")
    # chips come back in query order
    assert [c["span"][0] for c in chips] == sorted(c["span"][0] for c in chips)


def test_a_chip_id_is_its_field_and_the_text_it_matched():
    chips, _ = parse("besluiten van OCW juni 2026", VOCAB, NOW)
    assert [c["id"] for c in chips] == ["publisher:ocw", "published_month:juni 2026"]
    assert all(query_parse.chip_id(c) == c["id"] for c in chips)


# --- closed world -----------------------------------------------------------


def test_a_literal_vocabulary_value_matches_case_insensitively():
    chips, q = parse(f"besluiten {OCW.upper()}", VOCAB, NOW)
    assert [c["values"] for c in chips] == [[OCW]]
    assert chips[0]["source"] == "vocab"
    assert q == "besluiten"


def test_an_alias_whose_value_is_absent_from_the_vocabulary_stays_in_the_query():
    vocab = {"publisher": ["Raad van Discipline Amsterdam"]}
    chips, q = parse("besluiten OCW subsidie", vocab, NOW)
    assert chips == []
    assert q == "besluiten OCW subsidie"  # untouched, stopwords and all


def test_an_ambiguous_alias_is_dropped_rather_than_guessed():
    vocab = {"publisher": [OCW, OCW + " (archief)"]}
    chips, _ = parse("besluiten OCW", vocab, NOW)
    assert chips == []


def test_short_vocabulary_values_are_not_scanned_for():
    # "ok" is a real disposition value but a false-positive machine in free text
    chips, _ = parse("is dit besluit ok", VOCAB, NOW)
    assert chips == []
    chips, _ = parse("besluiten partial", VOCAB, NOW)
    assert [c["values"] for c in chips] == [["partial"]]


def test_file_type_alias_needs_a_word_boundary():
    chips, _ = parse("pdf besluiten", VOCAB, NOW)
    assert [c["values"] for c in chips] == [["application/pdf"]]
    assert parse("pdfje besluiten", VOCAB, NOW)[0] == []


def test_two_publishers_become_one_terms_clause():
    chips, _ = parse("OCW en EZK subsidie", VOCAB, NOW)
    assert chip_filters(chips) == [
        {"terms": {"publisher": [OCW, "ministerie van Economische Zaken en Klimaat"]}}
    ]


# --- date grammar -----------------------------------------------------------


def test_iso_month_and_named_month():
    assert parse("subsidie 2026-06", VOCAB, NOW)[0][0]["values"] == ["2026-06"]
    assert parse("subsidie juni 2026", VOCAB, NOW)[0][0]["values"] == ["2026-06"]
    assert parse("subsidie jun. 2026", VOCAB, NOW)[0][0]["values"] == ["2026-06"]


def test_a_bare_year_becomes_its_twelve_months():
    chips, q = parse("subsidie 2026", VOCAB, NOW)
    assert chips[0]["values"] == [f"2026-{m:02d}" for m in range(1, 13)]
    assert chips[0]["label"] == "2026-01 – 2026-12"
    assert q == "subsidie"


def test_a_named_month_beats_the_bare_year_inside_it():
    chips, _ = parse("subsidie juni 2026", VOCAB, NOW)
    assert len(chips) == 1
    assert chips[0]["text"] == "juni 2026"


def test_a_bare_month_name_is_not_understood():
    # picking a year for it would be a guess; the word stays a search term
    chips, q = parse("besluiten juni", VOCAB, NOW)
    assert chips == []
    assert q == "besluiten juni"


def test_relative_expressions_are_frozen_to_absolute_months():
    cases = {
        "deze maand": ["2026-07"],
        "vorige maand": ["2026-06"],
        "vorig kwartaal": ["2026-04", "2026-05", "2026-06"],
        "dit kwartaal": ["2026-07"],
        "afgelopen 6 maanden": [f"2026-{m:02d}" for m in range(2, 8)],
    }
    for phrase, expected in cases.items():
        chips, _ = parse(f"subsidie {phrase}", VOCAB, NOW)
        assert [c["values"] for c in chips] == [expected], phrase


def test_afgelopen_jaar_rolls_and_vorig_jaar_is_the_calendar_year():
    rolling, _ = parse("subsidie afgelopen jaar", VOCAB, NOW)
    assert rolling[0]["values"][0] == "2025-08"
    assert rolling[0]["values"][-1] == "2026-07"
    assert len(rolling[0]["values"]) == 12

    calendar, _ = parse("subsidie vorig jaar", VOCAB, NOW)
    assert calendar[0]["values"] == [f"2025-{m:02d}" for m in range(1, 13)]

    # nothing relative survives into the chip itself
    for chip in rolling + calendar:
        assert all(v[:2] == "20" and len(v) == 7 for v in chip["values"])
        assert "afgelopen" not in chip["label"] and "vorig" not in chip["label"]


def test_dit_jaar_stops_at_the_current_month():
    chips, _ = parse("subsidie dit jaar", VOCAB, NOW)
    assert chips[0]["values"] == [f"2026-{m:02d}" for m in range(1, 8)]


# --- residue / filters ------------------------------------------------------


def test_residual_is_the_identity_when_nothing_was_understood():
    q = "een zaak over de zorgplicht van een tandarts"
    assert parse(q, VOCAB, NOW) == ([], q)
    assert residual(q, []) == q


def test_dropping_a_chip_puts_its_text_back_in_the_query():
    q = "besluiten van OCW over subsidie juni 2026"
    chips, _ = parse(q, VOCAB, NOW)
    kept = [c for c in chips if c["field"] != "publisher"]
    assert residual(q, kept) == "besluiten OCW subsidie"


def test_a_repeated_value_is_only_consumed_once():
    # the second span has no chip of its own, so it stays visible as a term
    # rather than being silently deleted
    chips, q = parse("OCW besluiten OCW", VOCAB, NOW)
    assert len(chips) == 1
    assert q == "besluiten OCW"


def test_an_empty_query_parses_to_nothing():
    assert parse("", VOCAB, NOW) == ([], "")
    assert parse("   ", VOCAB, NOW) == ([], "   ")
    assert chip_filters([]) == []


def test_month_field_is_the_index_field_not_the_api_param():
    chips, _ = parse("subsidie juni 2026", VOCAB, NOW)
    assert chips[0]["field"] == query_parse.MONTH_FIELD == "published_month"
