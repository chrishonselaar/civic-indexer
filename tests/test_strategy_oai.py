"""oai_pmh strategy — parses an OAI-PMH 2.0 ListRecords response into DiscoveredItems.

Self-contained: an injected, stateful http_get serves two canned OAI-PMH pages (real
element shape, trimmed), so no live endpoint is touched. Guards the OAI-specific
envelope handling the design (design/bulk-endpoint-discovery.md) depends on:
resumptionToken paging (verb+token only on page 2), completeListSize as claimed total,
deleted-record skip (counted, never silent), namespaced-identifier stripping, and the
record→envelope map. There is no live OAI manifest yet (awaits probe 0 + a real source),
so the role is declared inline here — it doubles as the intended manifest shape.
"""

from engine.strategies.oai_pmh import OaiPmhStrategy

# The role shape a manifest would resolve — mirrors manifests/tuchtrecht.yaml's SRU role.
ROLE = {
    "request": {
        "method": "GET",
        "url": "https://repository.example/oai",
        "params": {"verb": "ListRecords", "metadataPrefix": "oai_dc", "set": "tuchtrecht"},
    },
    "pagination": {"param_token": "resumptionToken"},
    "sanity_cap": {"records": 60000},
    "delay_seconds": 0,
    "record": {
        "key_field": "identifier",  # OAI header <identifier> (namespaced)
        "key_strip_prefix": "oai:repository.overheid.nl:",
        "published_field": "date",  # a payload key, post-map
        "fields": {
            "title": "dc:title",
            "creator": "dc:creator",
            "description": "dc:description",
            "subject": "dc:subject",
            "date": "dc:date",
        },
    },
}

IDENTITY = {
    "natural_key": "ecli",
    "natural_key_pattern": "ECLI:NL:[A-Z0-9]+:[0-9]{4}:[A-Za-z0-9]+",
    "urn_template": "oaitest:{natural_key}",
}

_ENVELOPE_OPEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"'
    ' xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<responseDate>2026-07-24T00:00:00Z</responseDate><ListRecords>"
)
_ENVELOPE_CLOSE = "</ListRecords></OAI-PMH>"

# Page 1: one live record, one DELETED record (no metadata), then a continuation token.
PAGE_1 = _ENVELOPE_OPEN + """
<record><header>
  <identifier>oai:repository.overheid.nl:ECLI:NL:TADRARL:2016:108</identifier>
  <datestamp>2016-02-23</datestamp><setSpec>tuchtrecht</setSpec>
</header><metadata><oai_dc:dc>
  <dc:title>ECLI:NL:TADRARL:2016:108 Raad van Discipline 22-02-2016 15-242</dc:title>
  <dc:creator>Raad van Discipline Arnhem-Leeuwarden</dc:creator>
  <dc:description>Klacht tegen eigen advocaat &amp; kantoor.</dc:description>
  <dc:subject>Zorg voor de client</dc:subject>
  <dc:date>2016-02-22</dc:date>
</oai_dc:dc></metadata></record>
<record><header status="deleted">
  <identifier>oai:repository.overheid.nl:ECLI:NL:TGZRSGR:2009:YG0002</identifier>
  <datestamp>2020-01-01</datestamp>
</header></record>
<resumptionToken completeListSize="3" cursor="0">TOKEN-PAGE-2</resumptionToken>
""" + _ENVELOPE_CLOSE

# Page 2: one live record, then an EMPTY token (end of walk).
PAGE_2 = _ENVELOPE_OPEN + """
<record><header>
  <identifier>oai:repository.overheid.nl:ECLI:NL:TGZRSGR:2009:YG0001</identifier>
  <datestamp>2009-05-13</datestamp>
</header><metadata><oai_dc:dc>
  <dc:title>ECLI:NL:TGZRSGR:2009:YG0001</dc:title>
  <dc:creator>Regionaal Tuchtcollege Den Haag</dc:creator>
  <dc:date>2009-05-12</dc:date>
</oai_dc:dc></metadata></record>
<resumptionToken completeListSize="3" cursor="2"></resumptionToken>
""" + _ENVELOPE_CLOSE


def _make_http_get(pages):
    calls = []

    def http_get(url, params):
        calls.append(dict(params))
        # OAI-PMH 2.0: page 2+ is requested with verb + resumptionToken only.
        return {"html": pages[1] if "resumptionToken" in params else pages[0]}

    http_get.calls = calls
    return http_get


def _strategy(http_get):
    return OaiPmhStrategy(ROLE, IDENTITY, {}, http_get=http_get)


def test_oai_walk_pages_via_resumption_token():
    http_get = _make_http_get([PAGE_1, PAGE_2])
    s = _strategy(http_get)
    items = list(s.discover())

    # Two live records across two pages; one deleted; total claimed = 3.
    assert s.requests_made == 2
    assert s.claimed_total_first == 3
    assert s.raw_results == 3          # 2 live + 1 deleted block seen
    assert s.deleted_records == 1
    assert len(items) == 2

    # Page 2 must be fetched with verb + resumptionToken ONLY (exclusivity rule).
    assert "metadataPrefix" not in http_get.calls[1]
    assert http_get.calls[1]["resumptionToken"] == "TOKEN-PAGE-2"

    a = items[0]
    assert a.natural_key == "ECLI:NL:TADRARL:2016:108"   # oai: prefix stripped
    assert a.urn == "oaitest:ECLI:NL:TADRARL:2016:108"
    assert a.partition_leaf == "2016"                    # year from dc:date
    assert a.published == "2016-02-22"
    assert a.disposition == "ok"
    assert a.publisher == "Raad van Discipline Arnhem-Leeuwarden"
    assert a.payload["description"] == "Klacht tegen eigen advocaat & kantoor."  # &amp; -> &
    assert a.payload["subject"] == "Zorg voor de client"
    assert a.payload["mime_type"] == "text/xml"

    b = items[1]
    assert b.natural_key == "ECLI:NL:TGZRSGR:2009:YG0001"
    assert b.partition_leaf == "2009"


def test_oai_deleted_record_counted_not_silent():
    s = _strategy(_make_http_get([PAGE_1, PAGE_2]))
    items = list(s.discover())
    assert s.deleted_records == 1
    # the deleted ECLI never surfaces as an item
    assert all("YG0002" not in i.natural_key for i in items)


def test_oai_rejects_non_ecli_identity():
    bad = PAGE_1.replace("ECLI:NL:TADRARL:2016:108", "not-an-ecli", 1)
    s = _strategy(_make_http_get([bad, PAGE_2]))
    items = list(s.discover())
    assert s.unrecordable == 1                       # malformed key dropped, never silent
    assert all(i.natural_key.startswith("ECLI:") for i in items)
