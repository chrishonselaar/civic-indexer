"""notubiz_events strategy: canned-page walk covering the probed behaviors —
two-level expansion, per-leaf event accounting, confidential stubbing (ACC-2),
cross-meeting document dedup, lying publication_date (meeting date wins)."""

from engine.strategies.notubiz_events import NotubizEventsStrategy

ROLE = {
    "request": {
        "url": "https://api.notubiz.nl/events",
        "params": {"organisation_id": 544, "format": "json", "version": "1.21.1"},
    },
    "pagination": {"model": "page_number", "param_page": "page", "param_size": "per_page", "size": 200},
    "partition": {
        "key": "date",
        "assignment": "server_side_window",
        "range": {"start": "2025-01-01"},
        "bracketing": {"max_items": 180, "min_days": 1},
    },
    "claimed_total": {"declared": "per_leaf", "field": "pagination.total_items", "unit": "events"},
    "sanity_cap": {"records": 10000},
    "detail": {
        "url_template": "https://api.notubiz.nl/events/meetings/{event_id}",
        "params": {"format": "json", "version": "1.21.1"},
    },
}

IDENTITY = {"natural_key": "id", "urn_template": "notubiz:544:{natural_key}"}


def _doc(doc_id, title, size=1000):
    return {
        "id": doc_id,
        "title": title,
        "url": f"https://api.notubiz.nl/document/{doc_id}/1",
        "confidential": 0,
        "publication_date": "2026-07-23",  # the lying field: echoes request date
        "versions": [{"type": "file", "mime_type": "application/pdf",
                      "file_name": f"{title}.pdf", "file_size": size, "id": 1}],
        "types": [{"id": 1247, "value": "BenW Nota"}],
    }


EVENTS_PAGE = {
    "pagination": {"page": 1, "per_page": 200, "total_pages": 1, "items_on_page": 2,
                   "total_items": 2, "has_more_pages": False},
    "events": [
        {
            "id": 100, "type": "meeting", "confidential": 0,
            "gremium": {"id": 525},
            "attributes": [{"id": 1, "value": "Raadsvergadering"}],
            "plannings": [{"start_date": "2025-03-06 19:30:00"}],
            "event_type_data": {"self": "api.notubiz.nl/events/meetings/100"},
        },
        {
            "id": 101, "type": "meeting", "confidential": 1,  # ACC-2: stub, no detail call
            "gremium": {"id": 525},
            "attributes": [{"id": 1, "value": "Besloten vergadering"}],
            "plannings": [{"start_date": "2025-03-07 10:00:00"}],
        },
    ],
}

MEETING_100 = {
    "meeting": {
        "id": 100,
        "documents": [_doc(7001, "Agenda")],
        "agenda_items": [
            {"confidential": 0, "documents": [_doc(7002, "Nota A")],
             "agenda_items": [  # nested agenda items must be walked
                 {"confidential": 0, "documents": [_doc(7003, "Bijlage A1"),
                                                   _doc(7002, "Nota A")],  # cross-ref dup
                  "agenda_items": []}]},
            {"confidential": 1,  # docs under confidential agenda item stay unfetched
             "documents": [_doc(7999, "Geheim stuk")], "agenda_items": []},
        ],
    }
}


def fake_http_get(url, params):
    if "events/meetings/100" in url:
        return MEETING_100
    assert params.get("version"), "version param is required (failure-modes API-2)"
    if params.get("per_page") == 1:  # bracketing count probe
        return {"pagination": {"total_items": 2}, "events": []}
    return EVENTS_PAGE


def make_strategy():
    return NotubizEventsStrategy(
        ROLE, IDENTITY, {"delay_seconds": 0}, http_get=fake_http_get
    )


def test_two_level_walk_yields_documents_not_events():
    items = list(make_strategy().discover())
    docs = [i for i in items if i.disposition == "ok"]
    assert sorted(i.natural_key for i in docs) == ["7001", "7002", "7003"]
    assert all(i.urn.startswith("notubiz:544:") for i in docs)
    assert all(i.file_url and i.file_url.startswith("https://api.notubiz.nl/document/") for i in docs)


def test_leaf_stats_account_events_not_documents():
    s = make_strategy()
    list(s.discover())
    (label, stats), = s.leaf_stats.items()
    assert stats["claimed"] == 2 and stats["harvested"] == 2
    assert s.claimed_total_first == s.claimed_total_last == 2
    assert s.facets["events_walked"] == 2


def test_confidential_event_becomes_stub_without_detail_fetch():
    items = list(make_strategy().discover())
    stubs = [i for i in items if i.disposition == "stub"]
    assert len(stubs) == 1 and stubs[0].natural_key == "e101"
    assert "confidential" in stubs[0].reason


def test_confidential_agenda_item_documents_not_yielded():
    items = list(make_strategy().discover())
    assert "7999" not in [i.natural_key for i in items]


def test_cross_meeting_duplicate_documents_deduped():
    s = make_strategy()
    items = list(s.discover())
    keys = [i.natural_key for i in items if i.disposition == "ok"]
    assert len(keys) == len(set(keys))
    assert s.facets["doc_cross_refs"] == 1


def test_published_is_meeting_date_not_lying_publication_date():
    items = list(make_strategy().discover())
    doc = next(i for i in items if i.natural_key == "7001")
    assert doc.published == "2025-03-06"  # planning date, NOT 2026-07-23
    assert doc.payload["meeting_context"]["meeting_id"] == 100
