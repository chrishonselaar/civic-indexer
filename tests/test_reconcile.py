"""Reconciliation verdict rules — engine-computed, never claimable."""

from engine.reconcile import (
    NOT_RECONCILED,
    RECONCILED,
    WARNINGS,
    Reconciliation,
)


def rec(**kw) -> Reconciliation:
    base = dict(
        claimed_total_first=1000,
        claimed_total_last=1000,
        harvested_total=1000,
        sum_of_leaves=1000,
        scope="full",
    )
    base.update(kw)
    return Reconciliation(**base)


def test_exact_match_is_reconciled():
    assert rec().verdict() == RECONCILED


def test_doctored_claimed_total_gap_is_not_reconciled():
    assert rec(claimed_total_last=1200, claimed_total_first=1200).verdict() == NOT_RECONCILED


def test_churn_within_tolerance_is_warning():
    assert rec(claimed_total_last=1001, claimed_total_first=1001).verdict() == WARNINGS


def test_claimed_total_moved_but_final_matches_is_warning():
    v = rec(claimed_total_first=995)
    assert v.verdict() == WARNINGS
    assert any("moved during walk" in w for w in v.warnings)


def test_window_scope_caps_at_warnings():
    assert rec(scope="window", harvested_total=50, sum_of_leaves=50).verdict() == WARNINGS


def test_all_partitions_empty_is_alarm():
    assert (
        rec(harvested_total=0, sum_of_leaves=0, all_partitions_empty=True).verdict()
        == NOT_RECONCILED
    )


def test_zero_selector_matches_is_alarm():
    assert rec(selector_zero_matches=True).verdict() == NOT_RECONCILED


def test_unresolved_cap_hit_is_not_reconciled():
    assert rec(cap_hits=["2026-06"]).verdict() == NOT_RECONCILED


def test_internal_leaf_mismatch_is_engine_bug_alarm():
    assert rec(sum_of_leaves=999).verdict() == NOT_RECONCILED
