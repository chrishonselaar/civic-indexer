"""surface gate — the W6 probe-0 wiring as code (engine/onboarding.py).

Guards the properties design/w6-onboarding-agent.md §C depends on:
  - a scraper chosen over a metadata-bearing bulk surface is BLOCKED (unless justified);
  - a bare sitemap (bulk_index) does NOT block a scraper — only bulk_metadata does;
  - the two boundaries probe 0 leaves to the agent (`frontend_xhr_pass`,
    `content_completeness`) surface as owed obligations, not silent skips;
  - an `incomplete` probe fails CLOSED (surface_gate raises BulkProbeIncomplete);
  - the gate is the acceptance dependency: blocked OR open obligation ⇒ not acceptable
    and raise_if_unresolved raises.

Self-contained (synthetic reports) plus one round-trip against the real tuchtrecht
probe-0 artifact.
"""

import json
from pathlib import Path

import pytest

from engine import onboarding, probe

HOST = "https://repository.example"


# --- report builders --------------------------------------------------------------------


def _report(*surfaces, hosts=(HOST,)):
    return probe.BulkProbeReport(hosts=list(hosts), surfaces=list(surfaces))


def _sru_found(host=HOST):
    return probe.SurfaceProbe(
        "sru", host, host + "/sru", "found", 200, "bulk_metadata",
        {"protocol": "SRU", "maximum_records": 100}, "SRU explainResponse present",
    )


def _sitemap_found(host=HOST):
    return probe.SurfaceProbe(
        "sitemap", host, host + "/sitemap.xml", "found", 200, "bulk_index",
        {"via": "/sitemap.xml", "loc_count": 9}, "urlset with 9 <loc> entries",
    )


def _absent(surface, host=HOST):
    return probe.SurfaceProbe(surface, host, f"{host}/{surface}", "absent", 404, note="HTTP 404")


def _errored(surface, host=HOST):
    return probe.SurfaceProbe(surface, host, f"{host}/{surface}", "error", 503,
                              note="HTTP 503 (server error — undetermined)")


# --- block / allow ----------------------------------------------------------------------


def test_scraper_blocked_over_bulk_metadata():
    d = onboarding.surface_gate(_report(_sru_found()), "paged_html_search")
    assert d.blocked and d.status == "blocked"
    assert d.recommended.surface == "sru"
    assert not d.acceptable
    assert "sru" in d.reason
    with pytest.raises(onboarding.SurfaceGateUnresolved):
        d.raise_if_unresolved()


def test_scraper_allowed_with_recorded_justification():
    d = onboarding.surface_gate(
        _report(_sru_found()), "paged_html_search",
        justification="SRU explain lists 0 records for this collection; corpus lives only in HTML",
        resolved_obligations=["frontend_xhr_pass", "content_completeness"],
    )
    assert d.status == "overridden" and not d.blocked
    assert d.justification and "SRU explain" in d.justification
    assert d.acceptable  # override recorded AND obligations resolved
    d.raise_if_unresolved()  # must not raise


def test_choosing_the_recommended_bulk_surface_is_allowed():
    d = onboarding.surface_gate(
        _report(_sru_found()), "sru", resolved_obligations=["content_completeness"],
    )
    assert d.status == "allowed" and not d.blocked
    # picking a bulk surface owes no HAR pass (it already outranks a private API) ...
    assert {o.key for o in d.obligations} == {"content_completeness"}
    assert d.acceptable


def test_bare_sitemap_does_not_block_a_scraper():
    # bulk_index (sitemap) still needs per-URL fetching, so a scraper alongside it is fine.
    d = onboarding.surface_gate(
        _report(_sitemap_found()), "paged_html_search",
        resolved_obligations=["frontend_xhr_pass"],
    )
    assert d.status == "allowed" and not d.blocked
    # no metadata-bulk found ⇒ no content_completeness obligation
    assert {o.key for o in d.obligations} == {"frontend_xhr_pass"}
    assert d.acceptable


def test_scraper_allowed_when_no_bulk_surface_exists():
    rep = _report(*[_absent(s) for s in ("sru", "oai_pmh", "ckan", "sitemap")])
    d = onboarding.surface_gate(rep, "headless")
    assert d.status == "allowed" and not d.blocked
    # frontend_xhr_pass still owed (we are scraping); no content obligation (nothing found)
    assert {o.key for o in d.obligations} == {"frontend_xhr_pass"}
    assert not d.acceptable  # HAR pass not yet recorded
    with pytest.raises(onboarding.SurfaceGateUnresolved):
        d.raise_if_unresolved()


# --- obligations ------------------------------------------------------------------------


def test_obligations_owed_until_resolved():
    d = onboarding.surface_gate(
        _report(_sru_found()), "link_walk",
        justification="bulk surface is a stale mirror, confirmed empty",
    )
    assert {o.key for o in d.obligations} == {"frontend_xhr_pass", "content_completeness"}
    assert {o.key for o in d.open_obligations} == {"frontend_xhr_pass", "content_completeness"}
    assert not d.acceptable

    d2 = onboarding.surface_gate(
        _report(_sru_found()), "link_walk",
        justification="bulk surface is a stale mirror, confirmed empty",
        resolved_obligations=["frontend_xhr_pass", "content_completeness"],
    )
    assert d2.open_obligations == []
    assert d2.acceptable
    d2.raise_if_unresolved()


# --- fail-closed ------------------------------------------------------------------------


def test_incomplete_probe_fails_closed():
    rep = _report(_errored("sru"), _absent("oai_pmh"))
    with pytest.raises(probe.BulkProbeIncomplete):
        onboarding.surface_gate(rep, "paged_html_search")


# --- round-trip against the real tuchtrecht artifact ------------------------------------


def test_tuchtrecht_artifact_blocks_scraper_recommends_sru():
    art = (
        Path(__file__).resolve().parents[1]
        / "experiments/stage_b/probes/tuchtrecht/probe0_bulk_surfaces.json"
    )
    rep = probe.BulkProbeReport.from_dict(json.loads(art.read_text()))
    assert rep.enforceable and rep.recommended.surface == "sru"

    d = onboarding.surface_gate(rep, "paged_html_search")
    assert d.blocked
    assert d.recommended.surface == "sru"
    assert d.recommended.host == "https://repository.overheid.nl"
    # the exact Stage-B miss, now enforceable: an HTML scraper cannot ship over the SRU host.
    with pytest.raises(onboarding.SurfaceGateUnresolved):
        d.raise_if_unresolved()


# --- onboarding session: the intervention counter + acceptance ---------------------------


def test_intervention_counter_excludes_gates():
    s = onboarding.OnboardingSession("tuchtrecht", intent="tuchtcollege decisions")
    # tuchtrecht's real Stage-B cost: 2 ENGINE-GAP interventions, plus a gate (not counted).
    s.log_intervention("ENGINE-GAP", "paged_html_search strategy")
    s.log_intervention("ENGINE-GAP", "xml_text format support")
    s.record_gate_decision("Chris", "start stage B", phase=0)
    assert s.intervention_count == 2          # GATE not counted
    assert len(s.interventions) == 3          # but recorded for the honesty log
    assert s.within_intervention_budget


def test_intervention_budget_is_the_pass_bar():
    s = onboarding.OnboardingSession("haarlem_raad")
    for i in range(5):                        # Haarlem sat AT the ≤5 boundary
        s.log_intervention("ENGINE-GAP", f"gap {i}")
    assert s.intervention_count == 5 and s.within_intervention_budget
    s.log_intervention("DIAGNOSIS", "a trap the battery missed")
    assert s.intervention_count == 6 and not s.within_intervention_budget


def test_unknown_intervention_category_rejected():
    s = onboarding.OnboardingSession("x")
    with pytest.raises(ValueError):
        s.log_intervention("OOPS", "not a category")


def test_acceptance_requires_surface_gate_run():
    s = onboarding.OnboardingSession("x")
    # no surface gate recorded ⇒ not acceptable
    assert "surface gate not run" in "; ".join(s.acceptance_problems())
    with pytest.raises(onboarding.OnboardingNotAcceptable):
        s.raise_if_not_acceptable()


def test_acceptance_blocks_on_unresolved_gate_and_clears_when_resolved():
    s = onboarding.OnboardingSession("x")
    # a blocked scraper choice ⇒ session not acceptable
    s.set_surface_gate(onboarding.surface_gate(_report(_sru_found()), "paged_html_search"))
    assert not s.acceptable
    # choosing the recommended surface with its obligation resolved ⇒ acceptable
    s.set_surface_gate(onboarding.surface_gate(
        _report(_sru_found()), "sru", resolved_obligations=["content_completeness"]))
    assert s.acceptable
    s.raise_if_not_acceptable()               # must not raise


def test_acceptance_blocks_on_cost_overrun():
    s = onboarding.OnboardingSession("x", cost_projected_eur=1.0)
    s.set_surface_gate(onboarding.surface_gate(
        _report(_sru_found()), "sru", resolved_obligations=["content_completeness"]))
    s.add_cost(1.5)
    assert s.acceptable                       # 1.5 ≤ 2× 1.0
    s.add_cost(1.0)                           # now 2.5 > 2.0
    assert not s.acceptable
    assert "exceeds" in "; ".join(s.acceptance_problems())


def test_session_round_trips_through_dict():
    s = onboarding.OnboardingSession("tuchtrecht", intent="decisions", phase=7,
                                     cost_projected_eur=5.0)
    s.log_intervention("ENGINE-GAP", "strategy")
    s.record_gate_decision("Chris", "go")
    s.add_cost(0.8)
    s.set_surface_gate(onboarding.surface_gate(
        _report(_sru_found()), "sru", resolved_obligations=["content_completeness"]))
    restored = onboarding.OnboardingSession.from_dict(s.to_dict())
    assert restored.to_dict() == s.to_dict()  # idempotent round-trip
    assert restored.intervention_count == 1
    assert restored.gate.chosen_surface == "sru"


# --- aggregator obligations (SURF-3, design/aggregator-registry.md §9) -------------------

AGG_HOST = "https://api.openraadsinformatie.nl/v1/elastic"


def _aggregator_found(host=AGG_HOST, license="not_assessed", republishes=("api.notubiz.nl",),
                      oldest="2013-01-08T20:00:00.000000",
                      newest="2026-07-09T17:00:00.000000"):
    ev = {"aggregator": "open_raadsinformatie", "index": "ori_vlaardingen_*",
          "item_count": 16877, "carries": ["metadata", "full_text", "original_bytes"],
          "license": license, "republishes_hosts": list(republishes)}
    if oldest:
        ev["oldest_item"] = oldest
    if newest:
        ev["newest_item"] = newest
    return probe.SurfaceProbe(
        "aggregator:open_raadsinformatie", host, host + "/ori_vlaardingen_*/_search",
        "found", 200, "bulk_content", ev, "16877 items in ori_vlaardingen_*",
    )


def test_scraper_is_blocked_over_an_aggregator_too():
    """bulk_content is strictly richer than bulk_metadata — it must block at least as hard.

    Regression guard for the gap the new rank class opened: before BLOCKING_RANK_CLASSES
    the gate only tested `== "bulk_metadata"`, so a scraper chosen while an aggregator was
    recommended came back `allowed`.
    """
    d = onboarding.surface_gate(_report(_aggregator_found()), "paged_html_search")
    assert d.status == "blocked"
    assert d.recommended.rank_class == "bulk_content"
    assert not d.acceptable


def test_choosing_the_aggregator_owes_provenance():
    d = onboarding.surface_gate(_report(_aggregator_found()), "ori_elastic")
    assert d.status == "allowed"
    ob = [o for o in d.obligations if o.key == "aggregator_provenance"]
    assert ob and ob[0].met is False
    detail = ob[0].detail
    # the two §9 rules and the licence must all be named, or the obligation is decorative
    assert "completeness_claim: aggregator" in detail
    assert "confidence: NULL" in detail
    assert "not_assessed" in detail
    # TOT-5: and the measured archive SPAN, so freshness cannot be mistaken for coverage
    assert "2013-01-08" in detail and "2026-07-09" in detail
    # and it blocks acceptance until recorded
    assert not d.acceptable
    with pytest.raises(onboarding.SurfaceGateUnresolved):
        d.raise_if_unresolved()


def test_unmeasured_archive_start_says_so_rather_than_assuming_coverage():
    """A probe that could not measure the archive start must not read as full coverage."""
    d = onboarding.surface_gate(_report(_aggregator_found(oldest=None)), "ori_elastic")
    detail = [o for o in d.obligations if o.key == "aggregator_provenance"][0].detail
    assert "was not measured" in detail
    assert "do not assume its coverage" in detail


def test_aggregator_provenance_resolves():
    d = onboarding.surface_gate(
        _report(_aggregator_found()), "ori_elastic",
        resolved_obligations=["aggregator_provenance"],
    )
    assert d.acceptable
    d.raise_if_unresolved()


def test_choosing_something_else_owes_a_recorded_reason_not_silence():
    """The gate does NOT force the aggregator route — it forbids silence about not taking it."""
    d = onboarding.surface_gate(_report(_aggregator_found()), "paged_json_api")
    assert d.status == "allowed"           # not a scraper; the choice itself is permitted
    ob = [o for o in d.obligations if o.key == "aggregator_considered"]
    assert ob and ob[0].met is False
    assert "16877" in ob[0].detail and "paged_json_api" in ob[0].detail
    assert not d.acceptable                # ... but acceptance waits on the reason


def test_forgoing_an_aggregator_that_avoids_a_disallowing_host_says_so():
    """The SURF-3 x POL-1 link surfaces in the obligation text, where the agent will read it."""
    policy = probe.RobotsPolicy(
        "https://api.notubiz.nl", "https://api.notubiz.nl/robots.txt", "found", 200,
        user_agent_group="*", disallow=["/"],
    )
    report = probe.BulkProbeReport(
        hosts=["https://api.notubiz.nl"], surfaces=[_aggregator_found()], policies=[policy],
    )
    d = onboarding.surface_gate(report, "paged_json_api")
    detail = [o for o in d.obligations if o.key == "aggregator_considered"][0].detail
    assert "robots.txt" in detail and "avoids that host" in detail


def test_the_two_aggregator_obligations_are_mutually_exclusive():
    keys = lambda d: {o.key for o in d.obligations}
    chose = onboarding.surface_gate(_report(_aggregator_found()), "ori_elastic")
    other = onboarding.surface_gate(_report(_aggregator_found()), "paged_json_api")
    assert "aggregator_provenance" in keys(chose) and "aggregator_considered" not in keys(chose)
    assert "aggregator_considered" in keys(other) and "aggregator_provenance" not in keys(other)


def test_no_aggregator_found_owes_neither():
    d = onboarding.surface_gate(_report(_sru_found()), "sru")
    assert not {o.key for o in d.obligations} & {"aggregator_provenance", "aggregator_considered"}
