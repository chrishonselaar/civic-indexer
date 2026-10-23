"""Run lifecycle: start/finish, partition rows, run-report assembly (item-envelope.md §6).

A NOT_reconciled verdict pauses the source: the next backfill refuses to start
until the gap is understood (onboarding-flow.md Phase 8).
"""

from datetime import datetime, timezone
from typing import Any

from engine import db
from engine.reconcile import NOT_RECONCILED, Reconciliation


class SourcePaused(Exception):
    pass


def start(
    source_id: str, manifest_version: int, mode: str, phases: list[str]
) -> int:
    with db.tx() as cur:
        cur.execute("SELECT paused, paused_reason FROM sources WHERE id = %s", (source_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown source: {source_id}")
        if row["paused"] and mode == "backfill":
            raise SourcePaused(
                f"source {source_id} is paused ({row['paused_reason']}); "
                "resolve the reconciliation gap before the next backfill"
            )
        cur.execute(
            "INSERT INTO runs (source_id, manifest_version, mode, phases) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (source_id, manifest_version, mode, phases),
        )
        return cur.fetchone()["id"]


def record_partitions(run_id: int, leaves: dict[str, dict[str, Any]]) -> None:
    """leaves: {leaf: {claimed, harvested, cap_hit, notes}}"""
    with db.tx() as cur:
        for leaf, stats in sorted(leaves.items()):
            db.upsert(
                cur,
                "run_partitions",
                {
                    "run_id": run_id,
                    "leaf": leaf,
                    "claimed": stats.get("claimed"),
                    "harvested": stats.get("harvested", 0),
                    "cap_hit": stats.get("cap_hit", False),
                    "notes": stats.get("notes"),
                },
                conflict="run_id, leaf",
            )


def finish(
    run_id: int,
    source_id: str,
    rec: Reconciliation,
    outcomes: dict[str, Any],
    cost: dict[str, Any],
    selector_health: dict[str, Any],
    forbidden_sections: list[str] | None = None,
) -> dict[str, Any]:
    """Compute the verdict, persist the report, pause the source if NOT_reconciled."""
    verdict = rec.verdict()
    report = {
        "reconciliation": {
            "claimed_total_first": rec.claimed_total_first,
            "claimed_total_last": rec.claimed_total_last,
            "harvested_total": rec.harvested_total,
            "sum_of_leaves": rec.sum_of_leaves,
            "scope": rec.scope,
            "cap_hits": rec.cap_hits,
            "warnings": rec.warnings,
        },
        "selector_health": selector_health,
        "forbidden_sections": forbidden_sections or [],
        "outcomes": outcomes,
        "cost": cost,
        "verdict": verdict,
    }
    with db.tx() as cur:
        cur.execute(
            "UPDATE runs SET finished_at = %s, claimed_total_first = %s, "
            "claimed_total_last = %s, harvested_total = %s, sum_of_leaves = %s, "
            "verdict = %s, report = %s WHERE id = %s",
            (
                datetime.now(timezone.utc),
                rec.claimed_total_first,
                rec.claimed_total_last,
                rec.harvested_total,
                rec.sum_of_leaves,
                verdict,
                db.jsonb(report),
                run_id,
            ),
        )
        if verdict == NOT_RECONCILED:
            cur.execute(
                "UPDATE sources SET paused = true, paused_reason = %s WHERE id = %s",
                (f"run {run_id} NOT_reconciled", source_id),
            )
    return report


def render_report(run_id: int) -> str:
    """Human-readable run report."""
    with db.tx() as cur:
        cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
        run = cur.fetchone()
        cur.execute(
            "SELECT leaf, claimed, harvested, cap_hit, notes FROM run_partitions "
            "WHERE run_id = %s ORDER BY leaf",
            (run_id,),
        )
        parts = cur.fetchall()
    if run is None:
        return f"run {run_id} not found"
    r = run["report"] or {}
    rec = r.get("reconciliation", {})
    lines = [
        f"run {run['id']}  source={run['source_id']}  mode={run['mode']}  "
        f"phases={','.join(run['phases'])}",
        f"  started={run['started_at']:%Y-%m-%d %H:%M:%S}  "
        + (f"finished={run['finished_at']:%Y-%m-%d %H:%M:%S}" if run["finished_at"] else "UNFINISHED"),
        "",
        "  partitions:",
        "    leaf       claimed  harvested  cap_hit",
    ]
    for p in parts:
        claimed = "-" if p["claimed"] is None else str(p["claimed"])
        lines.append(
            f"    {p['leaf']:<10} {claimed:>7}  {p['harvested']:>9}  "
            f"{'YES' if p['cap_hit'] else 'no'}"
            + (f"  ({p['notes']})" if p["notes"] else "")
        )
    lines += [
        "",
        f"  reconciliation: claimed {rec.get('claimed_total_first')} -> "
        f"{rec.get('claimed_total_last')}  harvested={rec.get('harvested_total')}  "
        f"sum_of_leaves={rec.get('sum_of_leaves')}  scope={rec.get('scope')}",
    ]
    for w in rec.get("warnings", []):
        lines.append(f"    warning: {w}")
    outcomes = r.get("outcomes", {})
    lines.append(f"  outcomes: {outcomes}")
    lines.append(f"  cost: {r.get('cost', {})}")
    verdict = r.get("verdict", "?")
    mark = {"reconciled": "✓", "reconciled_with_warnings": "~", "NOT_reconciled": "✗"}.get(
        verdict, "?"
    )
    lines.append(f"\n  verdict: {verdict} {mark}")
    return "\n".join(lines)
