"""Manifest validation rules — the three rejections the design mandates, plus baseline."""

import copy

from engine import manifest

VALID = manifest.load("manifests/woo_besluiten.yaml")


def test_woo_manifest_is_valid():
    assert manifest.validate(copy.deepcopy(VALID)) == []


def test_missing_budget_is_invalid():
    m = copy.deepcopy(VALID)
    m["budgets"].pop("eur_per_run_cap")
    problems = manifest.validate(m)
    assert any("budget" in p for p in problems)

    m = copy.deepcopy(VALID)
    m.pop("budgets")
    problems = manifest.validate(m)
    assert any("budgets" in p for p in problems)


def test_open_probe_marker_blocks_production():
    m = copy.deepcopy(VALID)
    m["discovery"]["roles"]["backfill"]["sanity_cap"] = "TODO(probe): find real cap"
    problems = manifest.validate(m)
    assert any("TODO(probe)" in p for p in problems)
    # ...but test mode may proceed
    assert manifest.validate(m, allow_probes=True) == []


def test_claimed_total_none_requires_compensating_controls():
    m = copy.deepcopy(VALID)
    m["discovery"]["roles"]["backfill"]["claimed_total"] = {"declared": "none"}
    problems = manifest.validate(m)
    assert any("compensating_controls" in p for p in problems)

    m["discovery"]["roles"]["backfill"]["claimed_total"] = {
        "declared": "none",
        "compensating_controls": ["cutoff_walk"],
    }
    assert manifest.validate(m) == []


def test_unknown_strategy_rejected():
    m = copy.deepcopy(VALID)
    m["discovery"]["roles"]["backfill"]["strategy"] = "browser_automation"
    problems = manifest.validate(m)
    assert any("unknown strategy" in p for p in problems)


def test_role_extends_flattens():
    role = manifest.resolve_role(VALID, "incremental")
    assert role["strategy"] == "paged_json_api"
    assert role["cutoff"]["overlap_pages"] == 5
