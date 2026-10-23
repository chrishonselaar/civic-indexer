"""Reconciliation: the engine-computed completeness verdict.

"Success" is a reconciled verdict, never "ran without exceptions"
(design/decision-inventory.md §3, item-envelope.md §6). The verdict is computed
here from counters the run recorded — it is not claimable by strategy code.
"""

from dataclasses import dataclass, field

RECONCILED = "reconciled"
WARNINGS = "reconciled_with_warnings"
NOT_RECONCILED = "NOT_reconciled"

CHURN_TOLERANCE = 0.001  # ±0.1% claimed-total drift tolerated as mutation churn


@dataclass
class Reconciliation:
    claimed_total_first: int | None
    claimed_total_last: int | None
    harvested_total: int
    sum_of_leaves: int
    scope: str                     # "full" | "window"
    cap_hits: list[str] = field(default_factory=list)   # leaves that hit a cap
    all_partitions_empty: bool = False
    selector_zero_matches: bool = False
    warnings: list[str] = field(default_factory=list)

    def verdict(self) -> str:
        w = list(self.warnings)

        # Hard alarms first — these are never warnings.
        if self.selector_zero_matches:
            return NOT_RECONCILED
        if self.all_partitions_empty:
            return NOT_RECONCILED
        if self.cap_hits:
            # A cap left unresolved means unknown missing data.
            return NOT_RECONCILED

        if self.sum_of_leaves != self.harvested_total:
            # Internal accounting must always close; this is an engine bug or
            # dropped-item leak, not source churn.
            return NOT_RECONCILED

        claimed = self.claimed_total_last
        if self.scope == "window" or claimed is None:
            # A windowed walk cannot close the loop against a global claimed
            # total; compensating controls (cutoff walk, facet cross-check,
            # sanity cap) cap the verdict at reconciled_with_warnings.
            w.append(
                "window-scoped walk: global claimed_total not applicable; "
                "compensating controls applied"
            )
            self.warnings = w
            return WARNINGS

        gap = claimed - self.harvested_total
        if gap == 0:
            if (
                self.claimed_total_first is not None
                and self.claimed_total_first != claimed
            ):
                w.append(
                    f"claimed_total moved during walk "
                    f"({self.claimed_total_first} -> {claimed}); final count matched"
                )
                self.warnings = w
                return WARNINGS
            self.warnings = w
            return RECONCILED

        if abs(gap) <= max(1, round(claimed * CHURN_TOLERANCE)):
            w.append(
                f"gap of {gap} vs claimed_total {claimed} within ±{CHURN_TOLERANCE:.1%} "
                f"churn tolerance"
            )
            self.warnings = w
            return WARNINGS

        self.warnings = w
        return NOT_RECONCILED
