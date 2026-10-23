"""probe 0 — the fail-closed bulk-surface probe (engine/probe.py).

Self-contained: an injected http_get routes canned responses by URL path (real protocol
shape, trimmed), so no live host is touched. Guards the three properties the design
(design/bulk-endpoint-discovery.md) depends on:
  - found vs verified-absent knowledge is kept per surface;
  - the recommendation ranks metadata-bearing bulk (SRU/OAI/CKAN) ABOVE a bare sitemap;
  - a transport/5xx failure makes the verdict `incomplete` and fails CLOSED
    (raise_if_unenforced raises), so a partial probe can never justify a scraper;
  - robots Disallow/Crawl-delay is PARSED and RECORDED per host (POL-1) — including the
    group-selection trap a grep gets wrong — but does NOT gate the verdict, because the
    posture is an admin decision.
"""

import json

import pytest

from engine import probe

HOST = "https://repository.example"

# --- canned protocol bodies (trimmed but real-shaped) -----------------------------------

SRU_EXPLAIN = (
    '<?xml version="1.0"?>'
    '<sru:explainResponse xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse">'
    "<sru:version>2.0</sru:version>"
    "<sru:record><sru:recordData><zr:explain xmlns:zr='http://explain.z3950.org/dtd/2.0/'>"
    "<zr:configInfo><zr:default type='maximumRecords'>1000</zr:default></zr:configInfo>"
    "</zr:explain></sru:recordData></sru:record>"
    "</sru:explainResponse>"
)

OAI_IDENTIFY = (
    '<?xml version="1.0"?>'
    '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><Identify>'
    "<repositoryName>Example Repository</repositoryName>"
    "<granularity>YYYY-MM-DDThh:mm:ssZ</granularity>"
    "</Identify></OAI-PMH>"
)
OAI_FORMATS = (
    '<?xml version="1.0"?>'
    '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListMetadataFormats>'
    "<metadataFormat><metadataPrefix>oai_dc</metadataPrefix></metadataFormat>"
    "<metadataFormat><metadataPrefix>owms</metadataPrefix></metadataFormat>"
    "</ListMetadataFormats></OAI-PMH>"
)

ROBOTS_WITH_SITEMAP = "User-agent: *\nDisallow:\nSitemap: https://x.example/sitemap.xml\n"
SITEMAP_XML = (
    '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://x.example/a</loc></url>"
    "<url><loc>https://x.example/b</loc></url></urlset>"
)
CKAN_OK = '{"success": true, "result": {"count": 4242, "results": []}}'

# open.overheid.nl, verbatim shape as fetched 2026-07-25: a permissive Googlebot group
# sits ABOVE the `*` group that blanket-disallows. A grep for "Allow" reads this backwards.
ROBOTS_GOOGLEBOT_THEN_STAR = (
    "User-agent: Googlebot\n"
    "Allow: /home\n"
    "Allow: /details/*\n"
    "User-agent: *\n"
    "Disallow: /\n"
)
ROBOTS_BLANKET_DISALLOW = "User-agent: *\nDisallow: /\n"


def router(routes: dict, *, default=(404, "")):
    """Build an http_get that dispatches on URL path + (for OAI) the verb param.

    routes maps a matcher key -> (status, text). Keys: "sru", "oai:Identify",
    "oai:ListMetadataFormats", "robots", "sitemap", "ckan". A key absent from `routes`
    falls through to `default` (a clean 404 = verified-absent).
    """
    def http_get(url, params):
        if url.endswith("/sru"):
            key = "sru"
        elif url.endswith("/oai"):
            key = f"oai:{params.get('verb')}"
        elif url.endswith("/robots.txt"):
            key = "robots"
        elif url.endswith("/sitemap.xml"):
            key = "sitemap"
        elif url.endswith("/api/3/action/package_search"):
            key = "ckan"
        else:
            key = None
        if key == "__raise__":  # sentinel handled below
            raise ConnectionError("boom")
        status, text = routes.get(key, default)
        if text == "__raise__":
            raise ConnectionError("boom")
        return {"status": status, "text": text}

    return http_get


# --- tests ------------------------------------------------------------------------------


def test_all_absent_is_clean_no_bulk_surface():
    report = probe.probe_bulk_surfaces([HOST], http_get=router({}))
    assert report.verdict == "no_bulk_surface"
    assert report.enforceable is True
    assert report.recommended is None
    # verified-absent is knowledge, recorded per automated surface (4 of them)
    absents = [s for s in report.surfaces if s.status == "absent"]
    assert {s.surface for s in absents} == {"sru", "oai_pmh", "ckan", "sitemap"}
    # frontend_xhr is a manual boundary, never "absent"
    fx = [s for s in report.surfaces if s.surface == "frontend_xhr"][0]
    assert fx.status == "manual"
    report.raise_if_unenforced()  # must not raise


def test_sru_found_and_recommended_over_sitemap():
    report = probe.probe_bulk_surfaces(
        [HOST],
        http_get=router({
            "sru": (200, SRU_EXPLAIN),
            "robots": (200, ROBOTS_WITH_SITEMAP),  # also present, but ranks lower
        }),
    )
    assert report.verdict == "bulk_surface_available"
    rec = report.recommended
    assert rec is not None
    # metadata-bearing SRU (bulk_metadata, rank 1) beats the bare sitemap (bulk_index, rank 2)
    assert rec.surface == "sru"
    assert rec.rank_class == "bulk_metadata"
    assert rec.evidence.get("maximum_records") == 1000
    # and the sitemap is still recorded as found (negative knowledge is not discarded)
    sm = [s for s in report.surfaces if s.surface == "sitemap"][0]
    assert sm.status == "found" and sm.rank_class == "bulk_index"


def test_oai_identify_pulls_metadata_prefixes():
    report = probe.probe_bulk_surfaces(
        [HOST],
        http_get=router({
            "oai:Identify": (200, OAI_IDENTIFY),
            "oai:ListMetadataFormats": (200, OAI_FORMATS),
        }),
    )
    oai = [s for s in report.surfaces if s.surface == "oai_pmh"][0]
    assert oai.status == "found"
    assert oai.evidence["repository_name"] == "Example Repository"
    assert oai.evidence["metadata_prefixes"] == ["oai_dc", "owms"]


def test_ckan_found_with_count():
    report = probe.probe_bulk_surfaces([HOST], http_get=router({"ckan": (200, CKAN_OK)}))
    ckan = [s for s in report.surfaces if s.surface == "ckan"][0]
    assert ckan.status == "found"
    assert ckan.evidence["dataset_count"] == 4242
    assert report.recommended.surface == "ckan"


def test_sitemap_via_sitemap_xml_when_robots_silent():
    report = probe.probe_bulk_surfaces(
        [HOST],
        http_get=router({"robots": (200, "User-agent: *\n"), "sitemap": (200, SITEMAP_XML)}),
    )
    sm = [s for s in report.surfaces if s.surface == "sitemap"][0]
    assert sm.status == "found"
    assert sm.evidence["loc_count"] == 2 and sm.evidence["kind"] == "urlset"


def test_transport_error_fails_closed():
    # SRU raises → undetermined → verdict incomplete, enforceable False, gate raises.
    report = probe.probe_bulk_surfaces([HOST], http_get=router({"sru": (0, "__raise__")}))
    assert report.verdict == "incomplete"
    assert report.enforceable is False
    with pytest.raises(probe.BulkProbeIncomplete):
        report.raise_if_unenforced()


def test_5xx_is_undetermined_not_absent():
    report = probe.probe_bulk_surfaces([HOST], http_get=router({"sru": (503, "")}))
    sru = [s for s in report.surfaces if s.surface == "sru"][0]
    assert sru.status == "error"
    assert report.enforceable is False


# --- POL-1: robots policy is parsed and recorded (but does not gate) ---------------------


def test_blanket_disallow_is_recorded_in_policy_flags():
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=router({"robots": (200, ROBOTS_BLANKET_DISALLOW)})
    )
    flags = report.to_dict()["policy_flags"]
    assert flags["blanket_disallow"] == [HOST]
    assert flags["requires_admin_decision"] is True
    host_flags = flags["hosts"][0]
    assert host_flags["disallow"] == ["/"]
    assert host_flags["user_agent_group"] == "*"
    assert "ADMIN GATE" in host_flags["note"]


def test_disallow_does_not_gate_the_surface_verdict():
    """The posture is an admin decision — recording it must not change enforceability."""
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=router({"robots": (200, ROBOTS_BLANKET_DISALLOW),
                                 "sru": (200, SRU_EXPLAIN)})
    )
    assert report.verdict == "bulk_surface_available"
    assert report.enforceable is True
    report.raise_if_unenforced()  # must not raise on a Disallow alone
    assert report.disallowing_hosts == [HOST]


def test_a_more_specific_group_above_ours_does_not_grant_permission():
    """POL-1's grep trap: Googlebot's Allow lines are not ours (RFC 9309 group selection)."""
    p = probe.parse_robots(ROBOTS_GOOGLEBOT_THEN_STAR)
    assert p["user_agent_group"] == "*"
    assert p["disallow"] == ["/"]
    assert p["allow"] == []          # the Allows belong to Googlebot's group, not ours


def test_our_own_product_token_group_wins_over_star():
    text = (
        "User-agent: *\nDisallow: /\n\n"
        f"User-agent: {probe.ROBOTS_UA_TOKEN}\nDisallow: /private\nCrawl-delay: 2.5\n"
    )
    p = probe.parse_robots(text)
    assert p["user_agent_group"] == probe.ROBOTS_UA_TOKEN
    assert p["disallow"] == ["/private"]
    assert p["crawl_delay"] == 2.5


def test_crawl_delay_and_empty_disallow_are_read_correctly():
    report = probe.probe_bulk_surfaces(
        [HOST],
        http_get=router({"robots": (200, "User-agent: *\nDisallow:\nCrawl-delay: 10\n")}),
    )
    pol = report.policies[0]
    assert pol.status == "found"
    assert pol.disallow == []          # empty Disallow means nothing is disallowed
    assert pol.blanket_disallow is False
    assert pol.crawl_delay == 10.0
    assert report.to_dict()["policy_flags"]["crawl_delay"] == {HOST: 10.0}
    assert report.to_dict()["policy_flags"]["requires_admin_decision"] is False


def test_missing_robots_is_verified_absent_but_a_5xx_is_undetermined():
    absent = probe.probe_robots(HOST, router({}))                      # default 404
    assert absent.status == "absent" and absent.blanket_disallow is False
    undet = probe.probe_robots(HOST, router({"robots": (503, "")}))
    assert undet.status == "error"     # a 5xx on robots.txt is NOT permission
    assert undet.blanket_disallow is False
    assert HOST in probe.probe_bulk_surfaces(
        [HOST], http_get=router({"robots": (503, "")})
    ).to_dict()["policy_flags"]["undetermined"]


def test_comments_are_stripped_and_repeated_star_groups_merge():
    text = (
        "User-agent: *   # everyone\n"
        "Disallow: /a\n"
        "User-agent: *\n"
        "Disallow: /b\n"
    )
    assert probe.parse_robots(text)["disallow"] == ["/a", "/b"]


def test_policy_flags_round_trip_through_from_dict():
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=router({"robots": (200, ROBOTS_BLANKET_DISALLOW)})
    )
    again = probe.BulkProbeReport.from_dict(report.to_dict())
    assert again.disallowing_hosts == [HOST]
    assert again.policies[0].summary == "User-agent: * / Disallow: /"


def test_sitemap_still_found_via_robots_when_the_host_also_disallows():
    """Sharing the single robots fetch must not lose the Sitemap: advertisement."""
    text = ROBOTS_BLANKET_DISALLOW + "Sitemap: https://x.example/sitemap.xml\n"
    report = probe.probe_bulk_surfaces([HOST], http_get=router({"robots": (200, text)}))
    sm = [s for s in report.surfaces if s.surface == "sitemap"][0]
    assert sm.status == "found" and sm.evidence["via"] == "robots.txt"
    assert report.policies[0].blanket_disallow is True


def test_200_without_protocol_signal_is_absent():
    # An SRU path that returns an HTML error page (200 but not an explainResponse) is
    # verified-absent, not found and not undetermined.
    report = probe.probe_bulk_surfaces([HOST], http_get=router({"sru": (200, "<html>nope</html>")}))
    sru = [s for s in report.surfaces if s.surface == "sru"][0]
    assert sru.status == "absent"
    assert report.enforceable is True


# --- the aggregator-registry step (SURF-3) ----------------------------------------------
#
# Corpus-scoped, not host-scoped. Each test below pins a trap that would otherwise produce a
# silently WRONG probe result rather than a loud failure — see design/aggregator-registry.md
# §2 for how each was found live.

ORI_BASE = "https://agg.example/v1/elastic"

REGISTRY = [{
    "id": "open_raadsinformatie",
    "name": "Open Raadsinformatie",
    "corpus_types": ["council_information"],
    "probe": {
        "kind": "elasticsearch",
        "base_url": ORI_BASE,
        # The date in the index NAME is a reindex stamp, not a data cutoff — the pattern is
        # a wildcard precisely so nothing reads it.
        "index_pattern": "ori_{slug}_*",
        "item_type_field": "@type",
        "item_type_value": "MediaObject",
        "freshness_field": "last_discussed_at",
    },
    "join": {
        "natural_key_field": "original_url",
        "matches_provider_hosts": ["api.notubiz.nl", "*.ibabs.eu"],
    },
    "carries": ["metadata", "full_text", "page_text", "original_bytes"],
    "consent_posture": "republished_for_reuse",
    "license": "not_assessed",
}]

HINTS = probe.AggregatorHints(
    corpus_type="council_information", organisation="Vlaardingen",
    provider_hosts=["https://api.notubiz.nl"],
)


def es_router(hits: int, *, newest: str | None = "2026-07-09T17:00:00.000000",
              oldest: str | None = "2013-01-08T20:00:00.000000",
              per_year: dict | None = None,
              status: int = 200, body: str | None = None, host_routes: dict | None = None):
    """http_get that answers the ES _search and delegates everything else to `router`."""
    base = router(host_routes or {})

    def http_get(url, params):
        if "/_search" not in url:
            return base(url, params)
        if body == "__raise__":
            raise ConnectionError("boom")
        if body is not None:
            return {"status": status, "text": body}
        doc = {"hits": {"total": {"value": hits, "relation": "eq"}, "hits": []}}
        aggs = {}
        if newest is not None:
            aggs["newest"] = {"value_as_string": newest}
        if oldest is not None:
            aggs["oldest"] = {"value_as_string": oldest}
        if per_year is not None:
            aggs["by_year"] = {"buckets": [
                {"key_as_string": y, "doc_count": n} for y, n in per_year.items()
            ]}
        if aggs:
            doc["aggregations"] = aggs
        return {"status": status, "text": json.dumps(doc)}

    return http_get


def test_aggregator_found_outranks_a_metadata_bulk_surface():
    """bulk_content (metadata + full text + bytes) beats bulk_metadata (SRU)."""
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=es_router(16583, host_routes={"sru": (200, SRU_EXPLAIN)}),
        hints=HINTS, registry=REGISTRY,
    )
    rec = report.recommended
    assert rec.surface == "aggregator:open_raadsinformatie"
    assert rec.rank_class == "bulk_content"
    assert rec.evidence["item_count"] == 16583
    assert rec.evidence["index"] == "ori_vlaardingen_*"
    # the SRU surface is still recorded as found — negative knowledge is not discarded
    assert [s for s in report.surfaces if s.surface == "sru"][0].status == "found"


def test_freshness_is_measured_not_read_off_the_index_name():
    """TRAP: ori_vlaardingen_20250514... looks 14 months stale and was current to 2 weeks.

    The probe must report the AGGREGATED max(freshness_field); nothing may parse the name.
    """
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=es_router(16583, newest="2026-07-09T17:00:00.000000"),
        hints=HINTS, registry=REGISTRY,
    )
    ev = report.recommended.evidence
    assert ev["newest_item"].startswith("2026-07-09")
    assert ev["freshness_field"] == "last_discussed_at"


def test_archive_depth_is_measured_too_not_only_freshness():
    """TOT-5: an aggregator can be CURRENT and still start years after the publisher.

    Measured on Vlaardingen 2026-07-25: ORI is current to 2026-07-09 and carries nothing
    before 2010 (0/28 sampled 2008 documents, 0/58 sampled 2012) while the manifest declares
    `range.start: 2008-01-01`. A probe that reports only `newest` recommends that route as if
    it covered the whole corpus, so BOTH ends of the freshness field are aggregated.
    """
    report = probe.probe_bulk_surfaces(
        [HOST],
        http_get=es_router(16877, newest="2026-07-09T17:00:00.000000",
                           oldest="2013-01-08T20:00:00.000000"),
        hints=HINTS, registry=REGISTRY,
    )
    rec = report.recommended
    assert rec.evidence["oldest_item"].startswith("2013-01-08")
    # and it must be visible in the human-readable note, not only in the evidence dict
    assert "oldest 2013-01-08" in rec.note


def test_a_sparse_archive_head_is_visible_per_year_not_hidden_behind_a_start_date():
    """The start date alone lies: ORI's oldest Vlaardingen item is 2010-01-07, yet 2010 holds
    133 items, 2011-2012 hold ZERO and the corpus is dense only from 2014. Reading "2010" as
    an archive start claims six years the aggregator does not have, which is exactly how the
    route came to be described as removing the provider from the POL-1 decision."""
    live_shape = {"2010": 133, "2013": 599, "2014": 1373, "2015": 1310, "2026": 430}
    report = probe.probe_bulk_surfaces(
        [HOST],
        http_get=es_router(16877, oldest="2010-01-07T19:00:00.000000", per_year=live_shape),
        hints=HINTS, registry=REGISTRY,
    )
    ev = report.recommended.evidence
    assert ev["items_per_year"] == live_shape
    # the gap years are ABSENT rather than zero-filled — min_doc_count:1, no invention
    assert "2011" not in ev["items_per_year"] and "2012" not in ev["items_per_year"]


def test_a_missing_oldest_aggregation_is_not_faked():
    """No archive-start measurement must never silently read as "starts at the beginning"."""
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=es_router(16877, oldest=None), hints=HINTS, registry=REGISTRY,
    )
    assert "oldest_item" not in report.recommended.evidence


def test_probe_never_calls_count_or_cat_indices():
    """TRAP x2: `_count` with a body returns non-JSON through the proxy (would read as a
    false absent), and `_cat/indices` doc counts include nested docs (903,693 vs 112,792)."""
    seen: list[str] = []

    def spy(url, params):
        seen.append(url)
        return es_router(16583)(url, params)

    probe.probe_bulk_surfaces([HOST], http_get=spy, hints=HINTS, registry=REGISTRY)
    agg_calls = [u for u in seen if u.startswith(ORI_BASE)]
    assert agg_calls, "the aggregator was never probed"
    assert all("/_count" not in u and "_cat" not in u for u in agg_calls)
    assert all(u.endswith("/_search") for u in agg_calls)


def test_zero_hits_is_verified_absent_not_error():
    """A clean 200 with no hits means the aggregator does not carry this corpus — the
    EXPECTED outcome for most pairings, and knowledge worth keeping."""
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=es_router(0), hints=HINTS, registry=REGISTRY,
    )
    agg = [s for s in report.surfaces if s.surface.startswith("aggregator:")][0]
    assert agg.status == "absent"
    assert report.enforceable is True
    assert report.verdict == "no_bulk_surface"


def test_aggregator_transport_error_fails_closed():
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=es_router(0, body="__raise__"), hints=HINTS, registry=REGISTRY,
    )
    assert report.enforceable is False and report.verdict == "incomplete"
    with pytest.raises(probe.BulkProbeIncomplete):
        report.raise_if_unenforced()


def test_non_json_200_is_undetermined_not_absent():
    """The `_count`-with-body failure shape: HTTP 200 carrying something unparseable must
    never be read as 'no data'."""
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=es_router(0, body="<html>proxy error</html>"),
        hints=HINTS, registry=REGISTRY,
    )
    agg = [s for s in report.surfaces if s.surface.startswith("aggregator:")][0]
    assert agg.status == "error"
    assert report.enforceable is False


def test_missing_hints_records_a_manual_obligation_never_absent():
    """'We did not look' must never read as 'we looked and found nothing' — and must not
    gate, or every undeclared source wedges at once."""
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=es_router(16583), hints=None, registry=REGISTRY,
    )
    agg = [s for s in report.surfaces if s.surface == "aggregator_registry"][0]
    assert agg.status == "manual"
    assert "corpus_type" in agg.note
    assert report.enforceable is True


def test_corpus_type_mismatch_is_absent_not_a_probe():
    """The registry is keyed by corpus type; a non-matching entry is not fetched at all."""
    seen: list[str] = []

    def spy(url, params):
        seen.append(url)
        return es_router(16583)(url, params)

    hints = probe.AggregatorHints(corpus_type="official_publications", organisation="X")
    report = probe.probe_bulk_surfaces([HOST], http_get=spy, hints=hints, registry=REGISTRY)
    agg = [s for s in report.surfaces if s.surface.startswith("aggregator:")][0]
    assert agg.status == "absent" and "corpus_type" in agg.note
    assert not [u for u in seen if u.startswith(ORI_BASE)]


def test_slug_normalises_multiword_organisations():
    assert probe._slug("Den Haag") == "den_haag"
    assert probe._slug("'s-Hertogenbosch") == "s_hertogenbosch"


def test_disallowing_host_is_flagged_avoidable_when_an_aggregator_republishes_it():
    """The SURF-3 x POL-1 link: the posture question for a republished host can be AVOIDED
    rather than answered, because the pipeline need never contact it."""
    report = probe.probe_bulk_surfaces(
        ["https://api.notubiz.nl"],
        http_get=es_router(16583, host_routes={"robots": (200, ROBOTS_BLANKET_DISALLOW)}),
        hints=HINTS, registry=REGISTRY,
    )
    flags = report.policy_flags
    assert flags["blanket_disallow"] == ["https://api.notubiz.nl"]
    assert flags["requires_admin_decision"] is True          # still the admin's call
    assert flags["disallow_avoidable_via"] == {
        "https://api.notubiz.nl": ["open_raadsinformatie"]
    }


def test_a_found_aggregator_carries_the_licence_question_forward():
    """An aggregator is second-hand: consent posture and licence travel with the hit so the
    gate cannot adopt the route without seeing that the licence is still owed."""
    report = probe.probe_bulk_surfaces(
        [HOST], http_get=es_router(16583), hints=HINTS, registry=REGISTRY,
    )
    ev = report.recommended.evidence
    assert ev["consent_posture"] == "republished_for_reuse"
    assert ev["license"] == "not_assessed"
    assert ev["natural_key_field"] == "original_url"
    assert "original_bytes" in ev["carries"]


def test_registry_file_on_disk_loads_and_is_shaped_right():
    entries = probe.load_aggregator_registry()
    assert entries, "registries/aggregators.yaml should ship at least the ORI entry"
    ori = [e for e in entries if e["id"] == "open_raadsinformatie"][0]
    assert "council_information" in ori["corpus_types"]
    # pinned: the freshness field must be the ITEM-type one. start_date lives on
    # Meeting/AgendaItem and aggregates to null over MediaObject.
    assert ori["probe"]["freshness_field"] == "last_discussed_at"
    assert ori["license"] == "not_assessed"       # owed; MIT covers the software only


def test_missing_registry_file_is_empty_not_an_error():
    assert probe.load_aggregator_registry("registries/does-not-exist.yaml") == []
