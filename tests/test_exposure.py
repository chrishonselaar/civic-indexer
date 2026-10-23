"""Exposure tiers — the second axis of design/access-posture.md.

The framework's claim is that changing our mind about a publisher is cheap: a
re-projection, not a re-harvest. That is only true if the tier is enforced mechanically,
so these tests pin the two boundaries where enforcement actually lives:

* **T0 is a projection boundary.** Text that is never indexed cannot leak from any
  endpoint, present or future — including endpoints not written yet, which is the point.
* **T1/T2/T3 are API boundaries.** The text is indexed because retrieval needs it; the
  question is what a response may contain.

Pure: no Postgres, no OpenSearch.
"""

import pathlib

import pytest
from fastapi.testclient import TestClient

from engine import exposure, manifest
from engine.project import mappings
from ui.search_api import app

client = TestClient(app)


# ------------------------------------------------------------------- the vocabulary


def test_the_tiers_are_ordered():
    assert exposure.rank("T0") < exposure.rank("T1") < exposure.rank("T2") < exposure.rank("T3")


@pytest.mark.parametrize(
    "tier,text,full,derived",
    [
        ("T0", False, False, False),
        ("T1", True, False, False),
        ("T2", True, True, False),
        ("T3", True, True, True),
    ],
)
def test_each_tier_grants_exactly_what_it_says(tier, text, full, derived):
    assert exposure.indexes_text(tier) is text
    assert exposure.serves_full_text(tier) is full
    assert exposure.serves_derived_artefacts(tier) is derived


def test_an_undeclared_tier_falls_back_to_the_documented_default():
    """The default is permissive on purpose — it preserves pre-2026-07-27 behaviour
    rather than silently blanking four live projections — so it must be the *documented*
    one and not whatever a typo happens to sort as."""
    assert exposure.UNDECLARED_DEFAULT in exposure.TIERS
    assert exposure.rank(None) == exposure.rank(exposure.UNDECLARED_DEFAULT)
    assert exposure.rank("T7") == exposure.rank(exposure.UNDECLARED_DEFAULT)


def test_of_manifest_does_not_substitute_the_default():
    """A caller that needs to warn about an undeclared tier has to be able to tell the
    difference between 'declared T2' and 'nothing declared'."""
    assert exposure.of_manifest({"policy": {"exposure": "T0"}}) == "T0"
    assert exposure.of_manifest({"policy": {}}) is None
    assert exposure.of_manifest({}) is None
    assert exposure.of_manifest({"policy": {"exposure": "T7"}}) is None


# -------------------------------------------------------------------- validation


def test_a_typo_in_a_declared_tier_is_rejected():
    """rank() falls back to the permissive default for unknown values, so an unvalidated
    typo would silently over-serve. Validation is the only place that can catch it."""
    m = manifest.load("manifests/vlaardingen_raad.yaml")
    m.setdefault("policy", {})["exposure"] = "T2 (metadata only)"
    assert any("policy.exposure" in p for p in manifest.validate(m))


def test_both_axes_are_required_fail_closed():
    """W6.5 item 1's second half, on since 2026-07-27.

    It could not be switched on earlier: with no posture recorded anywhere it would have
    wedged all four sources at once. It is what makes "the agent may not resolve the gate"
    a control rather than an instruction — rule 5, the same reason policy.takedown and the
    User-Agent both got code.
    """
    m = manifest.load("manifests/vlaardingen_raad.yaml")
    assert not manifest.validate(m), "the shipped manifest must pass"

    m["policy"].pop("exposure")
    assert any("policy.exposure is required" in p for p in manifest.validate(m))

    m = manifest.load("manifests/vlaardingen_raad.yaml")
    m["policy"]["robots"]["admin_decision"] = "pending_gate_1"
    assert any("pending_gate_1" in p for p in manifest.validate(m)), (
        "an unruled source must not validate"
    )

    m = manifest.load("manifests/vlaardingen_raad.yaml")
    del m["policy"]["robots"]["admin_decision"]
    assert any("policy.admin_decision is required" in p for p in manifest.validate(m))


def test_an_unknown_processor_is_rejected():
    """The permission is opt-in and per-name; a typo must not read as an opt-in for
    something else, and must not silently read as nothing either."""
    m = manifest.load("manifests/woo_besluiten.yaml")
    m["policy"]["processors"] = ["voyage-rerank"]  # hyphen, not underscore
    assert any("policy.processors" in p for p in manifest.validate(m))


# ------------------------------------------------------- the projection boundary


def test_the_index_can_hold_the_tier_it_was_projected_under():
    """Enforcement reads the tier off the document, not off the manifest, so a stale
    index cannot over-serve after a manifest edit. That needs a mapped field."""
    assert mappings.doc_mappings()["properties"]["exposure"] == {"type": "keyword"}


def test_t0_omits_text_vectors_and_chunks(monkeypatch):
    from engine.project import projector

    docs, chunks, deleted = _project_with(monkeypatch, projector, tier="T0")
    assert docs, "T0 still projects the metadata document — that is what T0 is for"
    assert all("content" not in d for d in docs), "T0 indexed text"
    assert all("embedding" not in d for d in docs), "T0 indexed a vector"
    assert all(d["exposure"] == "T0" for d in docs)
    assert chunks == [], "T0 wrote chunks"
    assert deleted == ["civic-src-chunks"], "lowering to T0 must withdraw existing chunks"


def test_t1_and_up_index_text_chunks_and_vectors(monkeypatch):
    from engine.project import projector

    docs, chunks, deleted = _project_with(monkeypatch, projector, tier="T1")
    assert all(d["content"] for d in docs), "T1 must index text — retrieval needs it"
    assert all("embedding" in d for d in docs)
    assert chunks, "T1 must write chunks"
    assert deleted == [], "nothing to withdraw when text stays indexed"


def _project_with(monkeypatch, projector, *, tier: str):
    """Run project() against fakes, returning (docs, chunks, indices_cleared)."""
    row = {
        "urn": "src:1", "natural_key": "1", "source_id": "src", "payload": {},
        "disposition": "ok", "missing": [], "trusted_published": None,
        "extraction_method": "native_pdf", "extraction_confidence": 1.0,
        "pages_processed": 1, "text_ref": "blob:fake",
        "landing_url": "https://example.org/1", "file_url": None,
    }
    monkeypatch.setattr(projector, "_select_projectable", lambda cur, s: [row])
    monkeypatch.setattr(
        projector, "_load_manifest", lambda cur, s: {"policy": {"exposure": tier}}
    )
    monkeypatch.setattr(projector.suppress, "urns_for", lambda cur, s: [])
    monkeypatch.setattr(
        projector.suppress, "purge_from_projection",
        lambda *a, **k: {"docs_deleted": 0, "chunks_deleted": 0},
    )

    class FakeBlob:
        def exists(self):
            return True

        def read_text(self, encoding=None):
            return "Vergadering van de gemeenteraad. " * 40

    monkeypatch.setattr(projector.blobstore, "from_ref", lambda ref: FakeBlob())

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            # The only unpatched query in project() is the embedding cache. A vector must
            # actually be available, or "T0 indexed no vector" would pass vacuously.
            return [{"kind": "doc", "key": "src:1", "vector": [0.1, 0.2, 0.3]}]

    class FakeTx:
        def __enter__(self):
            return FakeCur()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(projector.db, "tx", lambda: FakeTx())

    captured: dict[str, list] = {"docs": [], "chunks": [], "cleared": []}

    class FakeIndices:
        def exists(self, index):
            return True

        def refresh(self, index):
            pass

    class FakeOS:
        indices = FakeIndices()

    monkeypatch.setattr(projector.os_client, "client", lambda: FakeOS())
    monkeypatch.setattr(projector.os_client, "ensure_index", lambda *a, **k: None)

    def fake_bulk(osc, index, actions):
        got = list(actions)
        captured["chunks" if index.endswith("-chunks") else "docs"].extend(got)
        return len(got), 0

    monkeypatch.setattr(projector.os_client, "bulk_index", fake_bulk)
    monkeypatch.setattr(
        projector.os_client, "delete_by_source",
        lambda osc, index, source_id: captured["cleared"].append(index) or 0,
    )
    projector.project("src")
    return captured["docs"], captured["chunks"], captured["cleared"]


# --------------------------------------------------------------- the API boundary


def _serves(monkeypatch, doc):
    """Make /api/doc find exactly this document.

    The endpoint looks documents up by a `urn` term across every corpus index rather
    than by `_id` in one (design/cross-corpus-search.md), so the stub is a search.
    """
    import ui.search_api as api

    monkeypatch.setattr(
        api.osc,
        "search",
        lambda **k: {"hits": {"hits": [{"_index": "civic-src", "_id": doc["urn"],
                                        "_source": dict(doc)}]}},
    )


def test_below_t2_the_document_endpoint_withholds_the_text(monkeypatch):
    doc = {"urn": "src:1", "title": "Raadsvoorstel", "content": "de volledige tekst",
           "exposure": "T1", "landing_url": "https://example.org/1"}
    _serves(monkeypatch, doc)

    body = client.get("/api/doc/src:1").json()
    assert "content" not in body
    # Explicit, not silently truncated: a caller must be able to tell "no text" from
    # "text not served", or it will read the first as a bug and retry forever.
    assert body["withheld"]["exposure"] == "T1"
    assert body["withheld"]["landing_url"] == "https://example.org/1"
    assert body["title"] == "Raadsvoorstel", "metadata is exactly what T0/T1 do serve"


def test_at_t2_the_text_is_served(monkeypatch):
    _serves(monkeypatch, {"urn": "src:1", "content": "de volledige tekst", "exposure": "T2"})
    body = client.get("/api/doc/src:1").json()
    assert body["content"] == "de volledige tekst"
    assert "withheld" not in body


def test_a_document_with_no_tier_follows_the_documented_default(monkeypatch):
    """Pre-framework documents carry no `exposure` field at all. They must behave as the
    default says, not fail open in some third way."""
    _serves(monkeypatch, {"urn": "src:1", "content": "de volledige tekst"})
    body = client.get("/api/doc/src:1").json()
    served = exposure.serves_full_text(exposure.UNDECLARED_DEFAULT)
    assert ("content" in body) is served


# ------------------------------------------------- the third-party processor boundary


def test_no_source_sends_text_to_a_third_party_unless_it_opts_in():
    """Voyage's own privacy policy, read 2026-07-27: absent an opt-out, submitting content
    grants "a worldwide, irrevocable, perpetual, royalty-free ... license to use ... the
    Customer Content ... to train, improve, and otherwise further develop the Service" —
    with no stated retention period, no GDPR basis, no controller/processor role and no
    SCC/DPF transfer mechanism. Opt-in per source is the only safe default."""
    assert exposure.processors_of_manifest({}) == []
    assert exposure.processors_of_manifest({"policy": {}}) == []
    assert exposure.processors_of_manifest({"policy": {"processors": ["nope"]}}) == []
    assert exposure.processors_of_manifest(
        {"policy": {"processors": ["voyage_rerank"]}}
    ) == ["voyage_rerank"]


def test_a_document_without_the_permission_blocks_reranking():
    assert not exposure.permits_processor({}, "voyage_rerank")
    assert not exposure.permits_processor({"processors": []}, "voyage_rerank")
    assert exposure.permits_processor({"processors": ["voyage_rerank"]}, "voyage_rerank")


def test_the_shipped_manifests_opt_in_only_where_the_corpus_allows_it():
    """A standing check on the fleet, not on one file: any source whose corpus is known to
    name private individuals must not be shipping that text to a third party."""
    for path in sorted(pathlib.Path("manifests").glob("*.yaml")):
        m = manifest.load(str(path))
        if exposure.processors_of_manifest(m):
            smell = ((m.get("policy") or {}).get("personal_data") or {}).get("smell")
            assert smell != "present", f"{path.name} opts in with personal_data present"
