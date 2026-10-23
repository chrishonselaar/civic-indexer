"""The deferred-upgrade pass: cohort selection, tier overlay, row supersession.

A source can hold two extraction tiers — an ACTIVE one every run takes, and a
DEFERRED one that costs real requests. tuchtrecht is the worked example: the SRU
payload carries an abstract for free, the body text is one FRBR fetch away on a
~1 rps host. These tests pin the three pieces that make taking the deferred tier
safe: it must select a cohort the backfill predicate cannot see, it must not
disturb the active tier, and it must leave exactly one extraction row behind.
"""

import psycopg

from engine import db, process


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


# ── cohort selection ──────────────────────────────────────────────────────────


def test_backfill_selects_uncarried_items_only():
    cur = FakeCursor()
    process.select_candidates(cur, "tuchtrecht", None, None)
    assert "stage = 'discovered'" in cur.sql
    assert "upgradable" not in cur.sql
    assert cur.params == ["tuchtrecht"]


def test_upgrade_selects_the_deferred_cohort_by_missing_reason():
    cur = FakeCursor()
    process.select_candidates(cur, "tuchtrecht", None, None, mode="upgrade")
    assert "i.upgradable" in cur.sql
    assert "ANY(i.missing)" in cur.sql
    assert cur.params == ["tuchtrecht", "fulltext"]


def test_upgrade_does_not_reset_or_filter_on_stage():
    # The deferred cohort is already 'extracted'/'indexed'. Selecting it must not
    # depend on a stage rewind — the item IS indexed, and a ledger that says
    # otherwise to make a query work is a lie.
    cur = FakeCursor()
    process.select_candidates(cur, "tuchtrecht", None, None, mode="upgrade")
    assert "stage" not in cur.sql


def test_upgrade_reason_is_selectable():
    cur = FakeCursor()
    process.select_candidates(cur, "s", None, None, mode="upgrade",
                              upgrade_reason="ocr_tail")
    assert cur.params == ["s", "ocr_tail"]


def test_window_and_limit_apply_to_both_cohorts():
    for mode in ("backfill", "upgrade"):
        cur = FakeCursor()
        process.select_candidates(cur, "s", "2025", 5, mode=mode)
        assert "discovered_via_partition_leaf = %s" in cur.sql
        assert cur.sql.rstrip().endswith("LIMIT %s")
        assert cur.params[-2:] == ["2025", 5]


# ── tier overlay ──────────────────────────────────────────────────────────────

TUCHT = {
    "fetch": {"transport": "none", "file_url_source": "payload",
              "upgrade": {"transport": "http", "ladder": ["xml_text"]}},
    "extraction": {"ladder": ["payload_text"], "title_field": "title"},
}


def test_overlay_swaps_transport_and_ladder():
    out = process.apply_upgrade_tier(TUCHT)
    assert out["fetch"]["transport"] == "http"
    assert out["extraction"]["ladder"] == ["xml_text"]


def test_overlay_keeps_every_undeclared_key():
    out = process.apply_upgrade_tier(TUCHT)
    assert out["fetch"]["file_url_source"] == "payload"
    assert out["extraction"]["title_field"] == "title"


def test_overlay_does_not_mutate_the_active_manifest():
    # An ordinary run of this manifest must still cost nothing after an upgrade run.
    process.apply_upgrade_tier(TUCHT)
    assert TUCHT["fetch"]["transport"] == "none"
    assert TUCHT["extraction"]["ladder"] == ["payload_text"]


def test_manifest_without_upgrade_block_is_returned_unchanged():
    m = {"fetch": {"transport": "http"}, "extraction": {"ladder": ["native_pdf_text"]}}
    assert process.apply_upgrade_tier(m) is m


def test_the_shipped_tuchtrecht_manifest_declares_the_deferred_tier():
    from engine import manifest as mf

    m = mf.load("manifests/tuchtrecht.yaml")
    assert mf.get(m, "fetch.transport") == "none"          # active tier still free
    up = process.apply_upgrade_tier(m)
    assert mf.get(up, "fetch.transport") == "http"
    assert mf.get(up, "extraction.ladder") == ["xml_text"]


# ── extraction-row supersession ───────────────────────────────────────────────


def test_supersede_deletes_every_other_row_for_the_urn():
    # All extractors write the SAME text file, but `extractions` is keyed
    # (urn, method, engine) — so an upgrade by a different route UPSERTs beside the
    # old row instead of replacing it, and projector._select_projectable (a JOIN with
    # no dedup) would then project the URN twice.
    cur = FakeCursor()
    process._supersede_extractions(cur, "urn:x", "native", "xml-text")
    assert cur.sql.startswith("DELETE FROM extractions")
    assert "NOT (method = %s AND engine = %s)" in cur.sql
    assert cur.params == ("urn:x", "native", "xml-text")


def test_failed_upgrade_keeps_the_item_in_the_cohort():
    # The 92-item lesson: the ordinary failure path rewinds to discovered/failed AND
    # clears missing/upgradable, which evicts the item from the only cohort that could
    # retry it. A failed upgrade must claim nothing.
    cur = FakeCursor()
    process._upgrade_noop(cur, "urn:x", "upgrade fetch failed: 429")
    assert "stage" not in cur.sql and "disposition" not in cur.sql
    assert "upgradable" not in cur.sql and "missing" not in cur.sql
    assert cur.params == ("upgrade fetch failed: 429", "urn:x")


def test_exhausted_upgrade_drops_the_marks_but_not_the_document():
    # No better version exists, so stop offering it — but marking it failed would drop
    # an already-indexed document out of the projection.
    cur = FakeCursor()
    process._upgrade_exhausted(cur, "urn:x", "no body text")
    assert "upgradable = false" in cur.sql and "missing = '{}'" in cur.sql
    assert "stage" not in cur.sql and "disposition" not in cur.sql


def test_live_projection_has_one_extraction_row_per_urn():
    """The invariant the supersession protects, checked against the real ledger."""
    try:
        with db.tx() as cur:
            cur.execute(
                "SELECT count(*) AS dupes FROM ("
                "  SELECT urn FROM extractions GROUP BY urn HAVING count(*) > 1"
                ") d"
            )
            assert cur.fetchone()["dupes"] == 0
    except psycopg.OperationalError:  # no local DB in this environment
        pass
