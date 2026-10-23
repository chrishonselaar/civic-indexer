"""Projection field building — the PROJ-1 regression suite.

PROJ-1: a declared index.projection path that is permanently null yields a facet
that is empty for 100% of documents, and it is invisible to source-side probing.
The instance was Notubiz `publisher`: the API gives a gremium *id*, the strategy
hard-coded `gremium_name: None`, and the "enrichment stage" both manifests
deferred to never existed. Fixed by a projection-time id->label join declared in
the manifest (enrichment.lookup_tables).

These tests exercise the pure field-building path only — no Postgres, no
OpenSearch — so they run in the normal fixture-based suite.
"""

import pytest

from engine import manifest
from engine.project.projector import _doc_fields, lookups_of, projection_of

VLAARDINGEN = manifest.load("manifests/vlaardingen_raad.yaml")
HAARLEM = manifest.load("manifests/haarlem_raad.yaml")


def _row(payload: dict, **over) -> dict:
    row = {
        "urn": "notubiz:262:1",
        "natural_key": "1",
        "payload": payload,
        "disposition": "ok",
        "trusted_published": None,
        "landing_url": None,
    }
    row.update(over)
    return row


def _fields(m: dict, payload: dict) -> dict:
    return _doc_fields(_row(payload), projection_of(m), lookups_of(m))


# ---------------------------------------------------------------- the join


def test_publisher_is_joined_from_the_gremium_id():
    fields = _fields(VLAARDINGEN, {"meeting_context": {"gremium_id": 94}})
    assert fields["publisher"] == "Gemeenteraad"


def test_the_join_survives_the_yaml_int_to_jsonb_string_round_trip():
    """The lookup keys are YAML ints; the manifest reaches the projector through
    JSONB, where every object key is a string, and the payload may hold either.
    Both sides are normalised to str — if that stops happening, the facet goes
    silently null again, which is exactly PROJ-1."""
    stored_keys = {str(k): v for k, v in VLAARDINGEN["enrichment"]["lookup_tables"]["gremium"]["values"].items()}
    as_stored = {"enrichment": {"lookup_tables": {"gremium": {
        "from": "meeting_context.gremium_id", "to": "publisher", "values": stored_keys}}}}
    for key in (94, "94"):
        fields = _doc_fields(
            _row({"meeting_context": {"gremium_id": key}}),
            projection_of(as_stored),
            lookups_of(as_stored),
        )
        assert fields["publisher"] == "Gemeenteraad", f"lost the join for {key!r}"


def test_an_unmapped_id_projects_null_never_the_raw_id():
    """An opaque number loose in a facet is worse than an empty one."""
    fields = _fields(VLAARDINGEN, {"meeting_context": {"gremium_id": 999999}})
    assert fields["publisher"] is None


def test_a_stub_payload_without_a_gremium_id_projects_null():
    """6 of Haarlem's 11,622 ledgered items are stubs with an empty payload."""
    assert _fields(HAARLEM, {})["publisher"] is None


def test_a_source_with_no_lookup_tables_still_projects_from_its_payload_path():
    """The woo/open.overheid shape must be untouched by the lookup machinery."""
    m = {"index": {"projection": {"publisher": "document.publisher"}}}
    fields = _fields(m, {"document": {"publisher": "Ministerie van BZK"}})
    assert fields["publisher"] == "Ministerie van BZK"


# ------------------------------------------------- both manifests are live


@pytest.mark.parametrize("m", [VLAARDINGEN, HAARLEM], ids=["vlaardingen", "haarlem"])
def test_publisher_is_a_declared_facet_and_actually_populates(m):
    """The two halves of PROJ-1 in one assertion: declared as a facet AND
    non-null for a real payload. Declaring one without the other is the defect."""
    assert "publisher" in m["index"]["facets"]
    gremium = m["enrichment"]["lookup_tables"]["gremium"]
    some_id = next(iter(gremium["values"]))
    assert _fields(m, {"meeting_context": {"gremium_id": some_id}})["publisher"]


@pytest.mark.parametrize("m", [VLAARDINGEN, HAARLEM], ids=["vlaardingen", "haarlem"])
def test_no_manifest_still_projects_publisher_from_the_dead_gremium_name_path(m):
    """The path the strategy no longer populates must not be declared anywhere."""
    assert "gremium_name" not in str(m.get("index", {}).get("projection", {}))


# ------------------------------------------------------------- validation


def test_a_field_cannot_have_two_owners():
    """A payload path AND a lookup for the same field is the ambiguity that
    produced the dead facet — validate() rejects it rather than picking a winner."""
    m = manifest.load("manifests/vlaardingen_raad.yaml")
    m["index"]["projection"]["publisher"] = "meeting_context.gremium_name"
    problems = manifest.validate(m)
    assert any("exactly one owner" in p for p in problems), problems


def test_an_incomplete_lookup_table_is_rejected():
    m = manifest.load("manifests/vlaardingen_raad.yaml")
    del m["enrichment"]["lookup_tables"]["gremium"]["from"]
    problems = manifest.validate(m)
    assert any("gremium.from is required" in p for p in problems), problems


# ------------------------------------------------- the missing-text-blob distinction


def test_a_missing_text_blob_is_none_not_empty_string(tmp_path, monkeypatch):
    """The 2026-09-08 incident: a ledger moved to a new host without `data/texts`
    projected every document as `content: ""` and reported `docs_failed: 0`. Nothing
    downstream can tell "no text in this document" from "no text on this machine"
    unless the reader hands back two different answers."""
    from engine import blobstore
    from engine.project import projector

    monkeypatch.setattr(blobstore, "from_ref", lambda ref: tmp_path / ref)
    assert projector.read_text_blob("texts/absent.md") is None

    (tmp_path / "texts").mkdir()
    (tmp_path / "texts" / "present.md").write_text("besluit", encoding="utf-8")
    assert projector.read_text_blob("texts/present.md") == "besluit"

    (tmp_path / "texts" / "blank.md").write_text("", encoding="utf-8")
    assert projector.read_text_blob("texts/blank.md") == ""
