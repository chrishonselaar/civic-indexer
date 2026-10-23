"""Drain-phase pure logic: stems, result evaluation, content measurement.

DB-touching halves (export/ingest) are exercised live against the real queue;
these tests pin the decision logic that guards blob deletion — the one
irreversible step in the drain.
"""

from engine import drain

MD_TWO_PAGES = (
    "<!-- page 1 -->\n"
    "Besluit van de raad over de subsidieverordening.\n"
    "De raad besluit de verordening vast te stellen zoals voorgelegd,\n"
    "met inachtneming van de ingediende zienswijzen en het advies.\n"
    "\n---\n\n"
    "<!-- page 2 -->\n"
    "Aldus vastgesteld in de openbare vergadering, de griffier en de voorzitter.\n"
    "Bijlagen: overzicht van indieners, financiële paragraaf en toelichting.\n"
)


def test_stem_prefixes_source_and_sanitizes():
    s = drain.stem("haarlem_raad", "doc/123 bijlage.pdf")
    assert s == "haarlem_raad__doc_123_bijlage.pdf"
    assert "/" not in s and " " not in s


def test_stems_distinguish_sources_with_same_key():
    assert drain.stem("a", "k1") != drain.stem("b", "k1")


def test_content_bytes_ignores_page_markers_and_separators():
    assert drain.content_bytes("<!-- page 1 -->\n\n---\n") == 0
    assert drain.content_bytes(MD_TWO_PAGES) > 100


def test_evaluate_ok_when_pages_match_and_text_real():
    verdict, found = drain.evaluate_result(MD_TWO_PAGES, expected_pages=2)
    assert (verdict, found) == ("ok", 2)


def test_evaluate_page_mismatch_blocks_upgrade():
    verdict, found = drain.evaluate_result(MD_TWO_PAGES, expected_pages=5)
    assert (verdict, found) == ("page_mismatch", 2)


def test_evaluate_unknown_page_count_skips_mismatch_check():
    verdict, _ = drain.evaluate_result(MD_TWO_PAGES, expected_pages=None)
    assert verdict == "ok"


def test_evaluate_thin_when_markers_but_no_text():
    md = "<!-- page 1 -->\nkort\n\n---\n\n<!-- page 2 -->\n"
    verdict, found = drain.evaluate_result(md, expected_pages=2)
    assert (verdict, found) == ("thin", 2)
