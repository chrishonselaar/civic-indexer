"""ori_elastic strategy — the aggregator route (design/aggregator-registry.md step 3).

Self-contained: an injected http_get returns canned Elasticsearch pages whose field
shapes are trimmed from a live sample of `ori_vlaardingen_*` (2026-07-25). Nothing here
touches a live endpoint or the database.

The two properties that make the route worth having are pinned here rather than described:
identity is IDENTICAL to the Notubiz harvest (so switching routes re-keys nothing), and
the imported text is recorded as second-hand (`method: aggregator`, `confidence: NULL`).
"""

import threading

from engine import drain, manifest, process
from engine.strategies.notubiz_events import NotubizEventsStrategy
from engine.strategies.ori_elastic import OriElasticStrategy

VLAARDINGEN = manifest.load("manifests/vlaardingen_raad.yaml")
IDENTITY = VLAARDINGEN["identity"]          # notubiz:262:{natural_key}, key ^[0-9]+$

ROLE = {
    "strategy": "ori_elastic",
    "request": {
        "url": "https://api.openraadsinformatie.nl/v1/elastic",
        "index": "ori_vlaardingen_*",
    },
    "item": {"type_field": "@type", "type_value": "MediaObject"},
    "pagination": {"model": "search_after", "sort_field": "@id", "size": 2},
    "record": {
        # api.notubiz.nl/document/{id}/{version} -> {id}: the version suffix is dropped,
        # which is exactly why identity.rejected_candidates rejected the bare URL as a key.
        "key_field": "original_url",
        "key_pattern": r"^https?://api\.notubiz\.nl/document/(\d+)(?:/\d+)?$",
        "published_field": "last_discussed_at",
        "title_field": "name",
        "bytes_url_field": "url",
        "pages_field": "text_pages",
        "engine_version_field": "was_generated_by.app_semver",
        "fields": {
            "mime_type": "content_type",
            "file_name": "file_name",
            "file_size": "size_in_bytes",
            "meeting_ref": "is_referenced_by",
            "organization_ref": "has_organization_name",
        },
        "source_includes": [
            "@id", "name", "file_name", "content_type", "size_in_bytes",
            "original_url", "url", "last_discussed_at", "is_referenced_by",
            "has_organization_name", "was_generated_by.app_semver", "text_pages",
        ],
    },
    "sanity_cap": {"records": 100000},
    "delay_seconds": 0,
}

BODY = ("Doorontwikkeling Steunpunt Mantelzorg Vlaardingen. Sinds 1 januari 2013 is het "
        "Steunpunt onderdeel van Stichting Maatschappelijke Dienstverlening Nieuwe "
        "Waterweg. Onderbouwing van de extra subsidieaanvraag voor het jaar 2014.")


def _hit(es_id, original_url, *, name, pages, semver="1.7.4", doc_id=None):
    doc_id = doc_id or es_id
    return {
        "_id": es_id,
        "sort": [es_id],
        "_source": {
            "@id": es_id,
            "@type": "MediaObject",
            "name": name,
            "file_name": "document.pdf",
            "content_type": "application/pdf",
            "size_in_bytes": 383825,
            "original_url": original_url,
            "url": f"https://api.openraadsinformatie.nl/v1/resolve/api.notubiz.nl/document/{doc_id}",
            "last_discussed_at": "2014-06-05T20:00:00+02:00",
            "is_referenced_by": "4722995",
            "has_organization_name": "4721010",
            "was_generated_by": {"app_semver": semver},
            "text_pages": [
                {"page_number": n + 1, "text": f"{BODY} (pagina {n + 1})"}
                for n in range(pages)
            ],
        },
    }


PAGE_1 = [
    _hit("4723001", "https://api.notubiz.nl/document/1341681/1",
         name="Doorontwikkeling Steunpunt Mantelzorg", pages=3, doc_id="1341681"),
    # ~0.7% of MediaObjects are links to meeting pages, not provider documents (probed).
    _hit("4723002", "https://vlaardingen.raadsinformatie.nl/vergadering/118190",
         name="vergadering", pages=0),
]
PAGE_2 = [
    _hit("4723003", "https://api.notubiz.nl/document/1341999/2",
         name="Bijlage zonder tekstlaag", pages=0, doc_id="1341999"),
]


def _response(hits):
    return {"hits": {"total": {"value": 3, "relation": "eq"}, "hits": hits}}


class Recorder:
    """Captures every (url, params) the strategy issues, so the walk's own mechanics
    (search_after, _source filtering, the _count/_cat traps) are assertable."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, params):
        import json as _json

        body = _json.loads(params["source"])
        self.calls.append((url, body))
        return _response(PAGE_1 if "search_after" not in body else PAGE_2)


def _strategy(http_get=None):
    return OriElasticStrategy(
        ROLE, IDENTITY, {"delay_seconds": 0}, http_get=http_get or Recorder()
    )


# ------------------------------------------------------------------ the walk


def test_ori_walk_maps_media_objects_to_envelope():
    s = _strategy()
    items = list(s.discover())

    assert s.claimed_total_first == s.claimed_total_last == 3
    assert s.raw_results == 3
    assert [i.natural_key for i in items] == ["1341681", "1341999"]

    a = items[0]
    assert a.partition_leaf == "2014"                 # year from last_discussed_at
    assert a.published == "2014-06-05"
    assert a.disposition == "ok"
    assert a.title == "Doorontwikkeling Steunpunt Mantelzorg"
    assert a.file_type == "application/pdf"
    assert a.pages == 3
    # The bytes come from ORI's resolve proxy — the pipeline never contacts the provider.
    assert a.file_url == (
        "https://api.openraadsinformatie.nl/v1/resolve/api.notubiz.nl/document/1341681"
    )
    assert a.payload["file_size"] == 383825
    assert a.payload["meeting_ref"] == "4722995"
    assert a.payload["organization_ref"] == "4721010"
    assert a.payload["text_engine_version"] == "1.7.4"
    assert a.payload["page_count"] == 3


def test_urn_is_identical_to_the_notubiz_harvest():
    """The whole point of deriving the key from original_url: a document harvested via ORI
    and the same document harvested from the provider are the SAME ledger item."""
    ori_item = next(i for i in _strategy().discover() if i.natural_key == "1341681")

    notubiz_role = manifest.resolve_role(VLAARDINGEN, "backfill")
    events = {
        "pagination": {"total_items": 1, "has_more_pages": False},
        "events": [{
            "id": 100, "type": "meeting", "confidential": 0, "gremium": {"id": 94},
            "attributes": [{"id": 1, "value": "Raadsvergadering"}],
            "plannings": [{"start_date": "2014-06-05 20:00:00"}],
        }],
    }
    meeting = {"meeting": {"id": 100, "documents": [{
        "id": 1341681, "title": "Doorontwikkeling Steunpunt Mantelzorg",
        "url": "https://api.notubiz.nl/document/1341681/1", "confidential": 0,
        "versions": [{"mime_type": "application/pdf", "file_size": 383825}],
    }], "agenda_items": []}}

    def notubiz_http_get(url, params):
        if "meetings/100" in url:
            return meeting
        return events

    notubiz_item = next(iter(NotubizEventsStrategy(
        notubiz_role, IDENTITY, {"delay_seconds": 0}, http_get=notubiz_http_get
    ).discover()))

    assert notubiz_item.natural_key == ori_item.natural_key == "1341681"
    assert notubiz_item.urn == ori_item.urn == "notubiz:262:1341681"


def test_provider_url_is_preserved_under_a_non_file_role():
    """Multi-provenance is kept (item-envelope §1 bet 2) — but NOT as a second role='file'
    row, which the fetch candidate query would join twice and double-fetch."""
    a = next(i for i in _strategy().discover() if i.natural_key == "1341681")
    assert a.extra_urls == [("https://api.notubiz.nl/document/1341681/1", "provider_file")]
    assert all(role != "file" for _, role in a.extra_urls)
    assert a.payload["provider_url"] == "https://api.notubiz.nl/document/1341681/1"


def test_text_pages_map_to_the_engine_page_model():
    """The imported text must be indistinguishable downstream from our own OCR output —
    same `<!-- page N -->` blocks, so drain's own page counter agrees."""
    a = next(i for i in _strategy().discover() if i.natural_key == "1341681")
    md = a.payload["pages_markdown"]
    assert md.startswith("<!-- page 1 -->\n")
    assert "\n\n---\n\n<!-- page 2 -->\n" in md
    assert drain.evaluate_result(md, a.payload["page_count"]) == ("ok", 3)


def test_meeting_links_are_unrecordable_never_silently_dropped():
    s = _strategy()
    keys = [i.natural_key for i in s.discover()]
    assert "118190" not in keys
    assert s.unrecordable == 1               # reported, so reconciliation can see it
    assert s.raw_results == 3 and len(keys) == 2


def test_walk_uses_search_after_and_never_requests_the_text_triplicate():
    rec = Recorder()
    list(_strategy(rec).discover())

    urls = [u for u, _ in rec.calls]
    assert all(u.endswith("/_search") for u in urls), "never _count (body 400s through the proxy)"
    assert not any("_cat" in u for u in urls), "never _cat/indices (it counts nested docs)"

    first, second = (b for _, b in rec.calls)
    assert first["track_total_hits"] is True and first["sort"] == [{"@id": "asc"}]
    assert "search_after" not in first
    # Page 2 resumes from the LAST hit's sort value, not from a from/size offset.
    assert second["search_after"] == ["4723002"]

    includes = first["_source"]["includes"]
    assert "text_pages" in includes
    assert "text" not in includes and "md_text" not in includes, (
        "text/md_text each duplicate the whole body; a full record averages ~125 KB"
    )


def test_incremental_walk_filters_on_the_freshness_field():
    rec = Recorder()
    list(_strategy(rec).discover(until_before="2020-01-01"))
    must = rec.calls[0][1]["query"]["bool"]["must"]
    assert {"range": {"last_discussed_at": {"gte": "2020-01-01"}}} in must
    assert {"term": {"@type": "MediaObject"}} in must


# ------------------------------------------------- the extraction row (§9b)


class _FakeCursor:
    def execute(self, *a, **kw):
        pass


class _FakeTx:
    def __enter__(self):
        return _FakeCursor()

    def __exit__(self, *a):
        return False


def _run_payload_extraction(monkeypatch, tmp_path, m, item):
    rows = []
    monkeypatch.setattr(process.db, "tx", lambda: _FakeTx())
    monkeypatch.setattr(
        process.db, "upsert",
        lambda cur, table, row, conflict, **kw: rows.append((table, row)),
    )
    monkeypatch.setattr(
        process.blobstore, "text_path",
        lambda source_id, leaf, key: tmp_path / f"{key}.md",
    )
    monkeypatch.setattr(process.blobstore, "ref", lambda p: p.name)
    status = {}
    monkeypatch.setattr(
        process, "_set_status",
        lambda cur, urn, **kw: status.update(kw),
    )
    process._extract_from_payload(
        item, m, source_id="vlaardingen_raad",
        outcomes={"extracted_ok": 0}, lock=threading.Lock(),
    )
    return dict(rows)["extractions"], status


ORI_EXTRACTION = {
    "extraction": {
        "title_field": "name",
        "payload_text_fields": ["pages_markdown"],
        "payload_extraction": {
            "method": "aggregator",
            "engine": "ori",
            "engine_version_field": "text_engine_version",
            "pages_field": "page_count",
        },
    }
}


def _candidate(item):
    return {
        "urn": item.urn, "natural_key": item.natural_key, "leaf": item.partition_leaf,
        "payload": {**item.payload, "name": item.title}, "file_url": item.file_url,
    }


def test_aggregator_extraction_row_never_claims_confidence(monkeypatch, tmp_path):
    a = next(i for i in _strategy().discover() if i.natural_key == "1341681")
    row, status = _run_payload_extraction(
        monkeypatch, tmp_path, ORI_EXTRACTION, _candidate(a)
    )
    assert row["method"] == "aggregator"
    assert row["engine"] == "ori"
    assert row["engine_version"] == "1.7.4"        # per-document, from was_generated_by
    assert row["confidence"] is None               # NEVER 1.0 — someone else's OCR
    assert row["pages_processed"] == 3
    # Full text present => not deferred, even though a fetchable file URL exists.
    # `[]`, not `None`: since 2026-07-29 omitting `missing` PRESERVES it, so "there is
    # nothing missing" has to be said rather than implied. Same intent, stated.
    assert status["missing"] == [] and status["upgradable"] is False
    assert status["reason"] is None
    assert (tmp_path / "1341681.md").read_text().startswith(
        "# Doorontwikkeling Steunpunt Mantelzorg"
    )


def test_a_manifest_cannot_launder_aggregator_text_into_a_certainty(monkeypatch, tmp_path):
    a = next(i for i in _strategy().discover() if i.natural_key == "1341681")
    m = {"extraction": {**ORI_EXTRACTION["extraction"]}}
    m["extraction"]["payload_extraction"] = {
        **ORI_EXTRACTION["extraction"]["payload_extraction"], "confidence": 1.0,
    }
    row, _ = _run_payload_extraction(monkeypatch, tmp_path, m, _candidate(a))
    assert row["confidence"] is None


def test_a_pageless_aggregator_item_stays_upgradable(monkeypatch, tmp_path):
    b = next(i for i in _strategy().discover() if i.natural_key == "1341999")
    row, status = _run_payload_extraction(
        monkeypatch, tmp_path, ORI_EXTRACTION, _candidate(b)
    )
    assert row["pages_processed"] == 0
    assert status["missing"] == ["fulltext"] and status["upgradable"] is True
    assert "carried no text" in status["reason"]


def test_payload_extraction_defaults_still_reproduce_the_sru_row(monkeypatch, tmp_path):
    """Regression: tuchtrecht declares no payload_extraction block and must be untouched."""
    m = manifest.load("manifests/tuchtrecht.yaml")
    cand = {
        "urn": "tuchtrecht:ECLI:NL:TADRARL:2016:108",
        "natural_key": "ECLI:NL:TADRARL:2016:108", "leaf": "2016",
        "payload": {"title": "Klacht", "description": "Klacht tegen eigen advocaat."},
        "file_url": "https://repository.overheid.nl/frbr/tuchtrecht/x.xml",
    }
    row, status = _run_payload_extraction(monkeypatch, tmp_path, m, cand)
    assert row["method"] == "sru" and row["engine"] == "sru-abstract"
    assert row["engine_version"] == "1" and row["confidence"] == 1.0
    assert row["pages_processed"] == 0
    assert status["missing"] == ["fulltext"] and status["upgradable"] is True
