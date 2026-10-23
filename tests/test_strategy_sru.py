"""sru_records strategy — parses a KOOP SRU 2.0 response into DiscoveredItems.

Self-contained: an injected http_get returns a canned SRU page (real element shape,
trimmed), so no live endpoint is touched. Guards the record→envelope mapping the
tuchtrecht manifest depends on: ECLI identity, inline abstract in payload, FRBR file
URL carried for the deferred full-text backfill, and year-leaf partitioning.
"""

from engine import manifest
from engine.strategies.sru_records import SruRecordsStrategy

M = manifest.load("manifests/tuchtrecht.yaml")

# Two records; the second's description carries an &amp; entity to check unescaping.
SRU_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<sru:searchRetrieveResponse xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse"
  xmlns:gzd="http://standaarden.overheid.nl/sru"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:overheidwetgeving="http://standaarden.overheid.nl/wetgeving/">
<sru:numberOfRecords>2</sru:numberOfRecords><sru:records>
<sru:record><sru:recordData><gzd:gzd><gzd:originalData>
  <dcterms:identifier>ECLI:NL:TADRARL:2016:108</dcterms:identifier>
  <dcterms:title>ECLI:NL:TADRARL:2016:108 Raad van Discipline 22-02-2016 15-242</dcterms:title>
  <dcterms:type>Raad van Discipline</dcterms:type>
  <dcterms:creator>Raad van Discipline Arnhem-Leeuwarden</dcterms:creator>
  <dcterms:description>Klacht tegen eigen advocaat.</dcterms:description>
  <dcterms:subject>Zorg voor de client</dcterms:subject>
  <overheidwetgeving:uitspraakdatum>2016-02-22</overheidwetgeving:uitspraakdatum>
  <overheidwetgeving:instantieDomein>Advocaten</overheidwetgeving:instantieDomein>
  <overheidwetgeving:beslissing>Schrapping</overheidwetgeving:beslissing>
</gzd:originalData><gzd:enrichedData>
  <gzd:preferredUrl>https://tuchtrecht.overheid.nl/ECLI:NL:TADRARL:2016:108</gzd:preferredUrl>
  <gzd:url>https://repository.overheid.nl/frbr/tuchtrecht/2016/ECLI:NL:TADRARL:2016:108/1/xml/ECLI_NL_TADRARL_2016_108.xml</gzd:url>
</gzd:enrichedData></gzd:gzd></sru:recordData></sru:record>
<sru:record><sru:recordData><gzd:gzd><gzd:originalData>
  <dcterms:identifier>ECLI:NL:TGZRSGR:2009:YG0001</dcterms:identifier>
  <dcterms:title>ECLI:NL:TGZRSGR:2009:YG0001</dcterms:title>
  <dcterms:creator>Regionaal Tuchtcollege Den Haag</dcterms:creator>
  <dcterms:description>Onzorgvuldig handelen &amp; communicatie.</dcterms:description>
  <overheidwetgeving:uitspraakdatum>2009-05-12</overheidwetgeving:uitspraakdatum>
  <overheidwetgeving:instantieDomein>Gezondheidszorg</overheidwetgeving:instantieDomein>
</gzd:originalData><gzd:enrichedData>
  <gzd:url>https://repository.overheid.nl/frbr/tuchtrecht/2009/ECLI:NL:TGZRSGR:2009:YG0001/1/xml/ECLI_NL_TGZRSGR_2009_YG0001.xml</gzd:url>
</gzd:enrichedData></gzd:gzd></sru:recordData></sru:record>
</sru:records></sru:searchRetrieveResponse>"""


def _strategy():
    role = manifest.resolve_role(M, "backfill")
    politeness = {**M.get("politeness", {}), "delay_seconds": 0}

    def http_get(url, params):
        # numberOfRecords=2 < size, so a single page terminates the walk.
        return {"html": SRU_PAGE}

    return SruRecordsStrategy(role, M["identity"], politeness, http_get=http_get)


def test_sru_walk_maps_records_to_envelope():
    s = _strategy()
    items = list(s.discover())
    assert s.claimed_total_first == 2
    assert s.raw_results == 2
    assert len(items) == 2

    a = items[0]
    assert a.natural_key == "ECLI:NL:TADRARL:2016:108"
    assert a.urn == "tuchtrecht:ECLI:NL:TADRARL:2016:108"
    assert a.partition_leaf == "2016"          # year from uitspraakdatum
    assert a.published == "2016-02-22"
    assert a.disposition == "ok"
    assert a.publisher == "Raad van Discipline Arnhem-Leeuwarden"
    # abstract + facets carried inline for extract-from-payload
    assert a.payload["description"] == "Klacht tegen eigen advocaat."
    assert a.payload["instantieDomein"] == "Advocaten"
    assert a.payload["beslissing"] == "Schrapping"
    assert a.payload["mime_type"] == "text/xml"
    # FRBR file URL kept for the deferred full-text backfill
    assert a.file_url.endswith("ECLI_NL_TADRARL_2016_108.xml")
    assert a.landing_url == "https://tuchtrecht.overheid.nl/ECLI:NL:TADRARL:2016:108"


def test_sru_unescapes_entities_and_handles_year_leaf():
    s = _strategy()
    items = list(s.discover())
    b = items[1]
    assert b.partition_leaf == "2009"
    assert b.payload["description"] == "Onzorgvuldig handelen & communicatie."  # &amp; -> &
    assert b.landing_url is None                                                # absent tag -> None


def test_sru_rejects_non_ecli_identity():
    role = manifest.resolve_role(M, "backfill")
    bad = SRU_PAGE.replace("ECLI:NL:TADRARL:2016:108", "not-an-ecli", 1)
    s = SruRecordsStrategy(
        role, M["identity"], {"delay_seconds": 0},
        http_get=lambda u, p: {"html": bad},
    )
    items = list(s.discover())
    assert s.unrecordable == 1                  # the malformed key is dropped, never silent
    assert all(i.natural_key.startswith("ECLI:") for i in items)
