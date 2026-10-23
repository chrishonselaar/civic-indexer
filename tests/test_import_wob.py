"""Adopting a prior project's text: header stripping, status mapping, join safety.

The import writes into the ledger, so the risks are the ledger's: text that is really a
placeholder landing as `ok`, an incomplete distillate losing its place in the OCR drain
cohort, and a candidate query that could pair the wrong two documents. These pin all
three, plus the header strip — which is the only text *transformation* in the path and
therefore the only place adopted content can be silently damaged.
"""

from engine import import_wob


class FakeCursor:
    """Captures the SQL/params a selection builds; returns nothing."""

    rowcount = 0

    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return []


# ── header stripping ──────────────────────────────────────────────────────────


def test_strips_the_slug_heading():
    body, red = import_wob.strip_prior_header(
        "# besluit-op-woo-verzoek-over-iets\n\n> Retouradres Postbus 1\nGeachte heer,\n"
    )
    assert body.startswith("> Retouradres")
    assert "besluit-op-woo-verzoek" not in body
    assert red is None


def test_strips_the_document_information_block_and_keeps_the_count():
    body, red = import_wob.strip_prior_header(
        "# een-titel\n\n## Document Information\n\n"
        "**Redactions detected:** 1197\n\nEerste alinea van het besluit.\n"
    )
    assert body == "Eerste alinea van het besluit."
    assert red == 1197


def test_a_file_with_no_heading_is_left_alone():
    body, red = import_wob.strip_prior_header("Ministerie van Financien\nDatum\n")
    assert body == "Ministerie van Financien\nDatum\n".strip()
    assert red is None


def test_only_the_first_heading_goes():
    """A `#` inside the body is content — a document can start a section with one."""
    body, _ = import_wob.strip_prior_header("# slug\n\nAlinea een.\n\n# 5.1.2.e\n\nTwee.\n")
    assert "# 5.1.2.e" in body


def test_inline_redaction_markers_survive():
    """Deliberate: 0.32% of characters, and deleting them loses real information."""
    body, _ = import_wob.strip_prior_header(
        "# slug\n\n[REDACTED: page 1, ~4 words, middle]\n\nDe rest van het besluit.\n"
    )
    assert "[REDACTED: page 1, ~4 words, middle]" in body


# ── status mapping ────────────────────────────────────────────────────────────


def test_complete_extractions_land_as_ok_and_leave_the_upgrade_cohort():
    for status in ("done_native", "done_ocr"):
        disposition, missing, upgradable = import_wob.STATUS_MAP[status]
        assert (disposition, missing, upgradable) == ("ok", [], False)


def test_incomplete_extractions_stay_in_the_ocr_drain_cohort():
    """`partial` alone would drop them: the drain selects on missing/upgradable."""
    for status in ("done_ocr_partial", "truncated"):
        disposition, missing, upgradable = import_wob.STATUS_MAP[status]
        assert disposition == "partial"
        assert missing == ["ocr_tail"]
        assert upgradable is True


def test_placeholder_statuses_are_not_importable_at_all():
    """Measured: every such row with a file is under 200 chars (p50 137 and 102)."""
    for status in ("stub", "skipped", "unprocessed"):
        assert status not in import_wob.STATUS_MAP


def test_engine_names_the_import_not_a_local_engine():
    """No audit may mistake adopted text for something this pipeline produced."""
    assert import_wob.ENGINE_NATIVE.startswith("wob-search-mcp:")
    assert import_wob.ENGINE_OCR.startswith("wob-search-mcp:")
    # and the OCR engine is not guessed: the prior ledger records used_ocr, not which one
    assert "azure" not in import_wob.ENGINE_OCR
    assert "Tesseract" in import_wob.VERSION_OCR


# ── candidate selection ───────────────────────────────────────────────────────


def test_candidates_only_takes_stubs_that_hold_no_extraction():
    cur = FakeCursor()
    import_wob.candidates(cur)
    assert "disposition = 'stub'" in cur.sql
    assert "NOT EXISTS (SELECT 1 FROM extractions" in cur.sql
    assert cur.params == ("woo_besluiten",)


def test_candidates_excludes_the_woo_idx_key_space():
    """The 20,712 federated records are a different corpus; the regex must not admit
    them, or the join would silently pair unrelated documents."""
    cur = FakeCursor()
    import_wob.candidates(cur)
    assert "^[0-9a-f-]{36}(_[0-9]+)?$" in cur.sql
