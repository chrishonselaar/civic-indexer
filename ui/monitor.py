"""Read-only monitoring surface over the ledger (roadmap W5).

`/overview` reads Postgres only — no OpenSearch dependency — so the pipeline stays
legible even when the projection/search stack is down. The ledger already holds
every fact the dashboard needs (`product-roadmap.md` W5): runs + reconciliation
verdicts, per-source item stage/disposition counts, the deferred_queue lifecycle,
cost_ledger spend, and blob retention (the VOL-3 kept-bytes DETECTION-GAP, surfaced
here as a UI concern).

`/projection` is the one exception and is deliberately a *separate* endpoint rather
than a section of the overview: it is the only thing here that opens an OpenSearch
client, and keeping it separate is what preserves the property above. The dashboard
fetches the two independently, so a dead cluster costs one card, not the page.

Management actions (trigger run/drain, retry, resolve queue) are deliberately
out of scope for this first cut — read-only first, per the roadmap.

Mounted by ui/search_api.py under /api/monitor.
"""

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from engine import db, ledger_check

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

# stage axis is monotonic (vocab_stage.rank); render the funnel in this order
STAGE_ORDER = ["discovered", "fetched", "extracted", "enriched", "indexed"]
DISPOSITIONS = [
    "ok", "partial", "deferred", "failed", "stub",
    "superseded", "gone_from_source", "out_of_scope",
]


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()


# --------------------------------------------------------------- building blocks

def _items_by_source(cur):
    """{source_id: {stage: {disposition: count}}} plus flat rollups."""
    rows = _rows(cur, """
        SELECT source_id, stage, disposition, count(*) AS n
        FROM items GROUP BY 1, 2, 3
    """)
    by_source: dict[str, dict] = {}
    for r in rows:
        s = by_source.setdefault(r["source_id"], {})
        s.setdefault(r["stage"], {})[r["disposition"]] = r["n"]
    return by_source


def _missing_by_source(cur):
    """Coverage gaps: {source_id: {missing_reason: item_count}} (missing[] unnested)."""
    rows = _rows(cur, """
        SELECT source_id, unnest(missing) AS reason, count(*) AS n
        FROM items WHERE array_length(missing, 1) > 0
        GROUP BY 1, 2
    """)
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["source_id"], {})[r["reason"]] = r["n"]
    return out


def _last_run_by_source(cur):
    rows = _rows(cur, """
        SELECT DISTINCT ON (source_id)
               source_id, id, mode, verdict, started_at, finished_at
        FROM runs ORDER BY source_id, id DESC
    """)
    return {
        r["source_id"]: {
            "id": r["id"], "mode": r["mode"], "verdict": r["verdict"],
            "started_at": _iso(r["started_at"]),
            "finished_at": _iso(r["finished_at"]),
            "unfinished": r["finished_at"] is None,
        }
        for r in rows
    }


def _open_queue_by_source(cur):
    rows = _rows(cur, """
        SELECT i.source_id, q.reason, count(*) AS n
        FROM deferred_queue q JOIN items i ON i.urn = q.urn
        WHERE q.resolved_at IS NULL
        GROUP BY 1, 2
    """)
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["source_id"], {})[r["reason"]] = r["n"]
    return out


def _cost_by_source(cur):
    rows = _rows(cur, """
        SELECT source_id, round(sum(eur), 4)::float8 AS eur
        FROM cost_ledger GROUP BY 1
    """)
    return {r["source_id"]: r["eur"] or 0.0 for r in rows}


def _funnel(stage_map: dict) -> dict:
    """Per-stage totals (summed over dispositions) for the source's items."""
    return {st: sum((stage_map.get(st) or {}).values()) for st in STAGE_ORDER}


def _dispositions(stage_map: dict) -> dict:
    out: dict[str, int] = {}
    for disp_map in stage_map.values():
        for disp, n in disp_map.items():
            out[disp] = out.get(disp, 0) + n
    return out


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


# --------------------------------------------------------------------- overview

@router.get("/overview")
def overview():
    """One round trip: source health cards + global rollups for the dashboard."""
    with db.tx() as cur:
        sources = _rows(cur, """
            SELECT id, name, base_url, paused, paused_reason, created_at
            FROM sources ORDER BY id
        """)
        items = _items_by_source(cur)
        missing = _missing_by_source(cur)
        last_run = _last_run_by_source(cur)
        open_q = _open_queue_by_source(cur)
        cost = _cost_by_source(cur)

        source_cards = []
        for s in sources:
            stage_map = items.get(s["id"], {})
            disp = _dispositions(stage_map)
            total = sum(disp.values())
            source_cards.append({
                "id": s["id"],
                "name": s["name"],
                "base_url": s["base_url"],
                "paused": s["paused"],
                "paused_reason": s["paused_reason"],
                "items_total": total,
                "funnel": _funnel(stage_map),
                "dispositions": disp,
                "indexed": _funnel(stage_map)["indexed"],
                "missing": missing.get(s["id"], {}),
                "open_queue": open_q.get(s["id"], {}),
                "last_run": last_run.get(s["id"]),
                "cost_eur": cost.get(s["id"], 0.0),
                "health": _health(s, disp, last_run.get(s["id"]), open_q.get(s["id"], {})),
            })

        # Postgres-only, like everything else on /overview: these invariants read the
        # ledger against itself, so the panel still works with the search stack down.
        ledger = ledger_check.check()
        return {
            "generated_at": _now(),
            "totals": _totals(cur, source_cards),
            "sources": source_cards,
            "recent_runs": _recent_runs(cur),
            "queues": _queue_summary(cur),
            "blobs": _blob_summary(cur),
            "costs": _cost_summary(cur),
            "ledger": ledger,
            "alerts": _alerts(cur, source_cards, ledger),
        }


def _health(source, disp, last_run, open_q) -> str:
    """red = needs attention, amber = watch, green = healthy."""
    if source["paused"]:
        return "red"
    if last_run and last_run["verdict"] == "NOT_reconciled":
        return "red"
    if last_run and last_run["unfinished"]:
        return "amber"
    if disp.get("failed"):
        return "amber"
    if last_run and last_run["verdict"] == "reconciled_with_warnings":
        return "amber"
    if sum(open_q.values()):
        return "amber"
    return "green"


def _totals(cur, cards) -> dict:
    disk = _one(cur, """
        SELECT coalesce(sum(bytes), 0)::bigint AS on_disk,
               count(*) AS on_disk_count
        FROM blobs WHERE storage_ref IS NOT NULL
    """)
    reclaimed = _one(cur, """
        SELECT coalesce(sum(bytes), 0)::bigint AS b
        FROM blobs WHERE storage_ref IS NULL
    """)
    runs = _one(cur, """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE finished_at IS NULL) AS unfinished,
               count(*) FILTER (WHERE verdict = 'NOT_reconciled') AS not_reconciled
        FROM runs
    """)
    spend = _one(cur, "SELECT coalesce(sum(eur), 0)::float8 AS eur FROM cost_ledger")
    return {
        "sources": len(cards),
        "paused_sources": sum(1 for c in cards if c["paused"]),
        "items": sum(c["items_total"] for c in cards),
        "indexed": sum(c["indexed"] for c in cards),
        "open_queue": sum(sum(c["open_queue"].values()) for c in cards),
        "disk_bytes": disk["on_disk"],
        "disk_blobs": disk["on_disk_count"],
        "reclaimed_bytes": reclaimed["b"],
        "runs": runs["n"],
        "unfinished_runs": runs["unfinished"],
        "not_reconciled_runs": runs["not_reconciled"],
        "spend_eur": round(spend["eur"], 4),
    }


def _recent_runs(cur, limit: int = 25) -> list:
    rows = _rows(cur, """
        SELECT r.id, r.source_id, s.name AS source_name, r.mode, r.phases,
               r.started_at, r.finished_at, r.verdict,
               r.claimed_total_last, r.harvested_total, r.sum_of_leaves
        FROM runs r JOIN sources s ON s.id = r.source_id
        ORDER BY r.id DESC LIMIT %s
    """, (limit,))
    return [{
        "id": r["id"],
        "source_id": r["source_id"],
        "source_name": r["source_name"],
        "mode": r["mode"],
        "phases": r["phases"],
        "started_at": _iso(r["started_at"]),
        "finished_at": _iso(r["finished_at"]),
        "duration_s": (
            (r["finished_at"] - r["started_at"]).total_seconds()
            if r["finished_at"] else None
        ),
        "verdict": r["verdict"],
        "claimed": r["claimed_total_last"],
        "harvested": r["harvested_total"],
    } for r in rows]


def _queue_summary(cur) -> list:
    rows = _rows(cur, """
        SELECT reason,
               count(*) FILTER (WHERE resolved_at IS NULL) AS open,
               count(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved,
               min(queued_at) FILTER (WHERE resolved_at IS NULL) AS oldest_open
        FROM deferred_queue GROUP BY reason ORDER BY open DESC, reason
    """)
    return [{
        "reason": r["reason"], "open": r["open"], "resolved": r["resolved"],
        "oldest_open": _iso(r["oldest_open"]),
    } for r in rows]


def _blob_summary(cur) -> dict:
    """Retention picture — the VOL-3 kept-bytes gap as a UI concern.

    on_disk = storage_ref still set; reclaimed = deleted after distillation.
    """
    rows = _rows(cur, """
        SELECT coalesce(kept_reason, kept::text) AS bucket,
               kept, kept_reason,
               count(*) AS n,
               coalesce(sum(bytes), 0)::bigint AS bytes,
               count(*) FILTER (WHERE storage_ref IS NOT NULL) AS on_disk_n,
               coalesce(sum(bytes) FILTER (WHERE storage_ref IS NOT NULL), 0)::bigint
                   AS on_disk_bytes
        FROM blobs GROUP BY 1, 2, 3 ORDER BY bytes DESC
    """)
    return {
        "by_reason": [{
            "kept": r["kept"], "kept_reason": r["kept_reason"],
            "count": r["n"], "bytes": r["bytes"],
            "on_disk_count": r["on_disk_n"], "on_disk_bytes": r["on_disk_bytes"],
        } for r in rows],
    }


def _cost_summary(cur) -> dict:
    by_cat = _rows(cur, """
        SELECT category, count(*) AS events,
               round(sum(amount), 2)::float8 AS amount,
               round(sum(eur), 4)::float8 AS eur
        FROM cost_ledger GROUP BY category ORDER BY eur DESC, category
    """)
    stops = _rows(cur, """
        SELECT run_id, source_id, eur::float8, note, created_at
        FROM cost_ledger WHERE category = 'budget_stop'
        ORDER BY created_at DESC LIMIT 20
    """)
    return {
        "by_category": [{
            "category": r["category"], "events": r["events"],
            "amount": r["amount"], "eur": r["eur"],
        } for r in by_cat],
        "budget_stops": [{
            "run_id": r["run_id"], "source_id": r["source_id"],
            "eur": r["eur"], "note": r["note"], "at": _iso(r["created_at"]),
        } for r in stops],
    }


def _alerts(cur, cards, ledger=None) -> list:
    """Things a human should look at, most urgent first."""
    alerts = []
    # First, because it is the only class here that is INVISIBLE everywhere else: a run
    # can report `reconciled` while the ledger contradicts itself, and the stage funnel
    # renders the damage as ordinary pending work. See engine/ledger_check.py.
    for r in ledger or []:
        if r["violations"]:
            fix = " (fixable: civic check-ledger --repair)" if r["repairable"] else ""
            alerts.append({"level": "red", "source": None,
                           "msg": f"ledger invariant broken — {r['claim']}: "
                                  f"{r['violations']} item(s){fix}"})
    for c in cards:
        if c["paused"]:
            alerts.append({"level": "red", "source": c["id"],
                           "msg": f"source paused: {c['paused_reason']}"})
    for c in cards:
        lr = c["last_run"]
        if lr and lr["verdict"] == "NOT_reconciled":
            alerts.append({"level": "red", "source": c["id"],
                           "msg": f"last run #{lr['id']} NOT_reconciled"})
    unfinished = _rows(cur, """
        SELECT id, source_id, mode, started_at FROM runs
        WHERE finished_at IS NULL ORDER BY id
    """)
    for r in unfinished:
        alerts.append({"level": "amber", "source": r["source_id"],
                       "msg": f"run #{r['id']} ({r['mode']}) never finished "
                              f"(started {_iso(r['started_at'])})"})
    for q in _queue_summary(cur):
        if q["open"]:
            alerts.append({"level": "amber", "source": None,
                           "msg": f"{q['open']} item(s) queued for {q['reason']}"})
    return alerts


# ----------------------------------------------------------------- source detail

@router.get("/source/{source_id}")
def source_detail(source_id: str):
    with db.tx() as cur:
        s = _one(cur, """
            SELECT id, name, base_url, paused, paused_reason, created_at
            FROM sources WHERE id = %s
        """, (source_id,))
        if s is None:
            return JSONResponse({"error": "unknown source"}, status_code=404)

        matrix = _rows(cur, """
            SELECT stage, disposition, count(*) AS n
            FROM items WHERE source_id = %s GROUP BY 1, 2
        """, (source_id,))
        stage_map: dict[str, dict] = {}
        for r in matrix:
            stage_map.setdefault(r["stage"], {})[r["disposition"]] = r["n"]

        missing = _rows(cur, """
            SELECT unnest(missing) AS reason, count(*) AS n
            FROM items WHERE source_id = %s AND array_length(missing, 1) > 0
            GROUP BY 1 ORDER BY 2 DESC
        """, (source_id,))

        runs = _rows(cur, """
            SELECT id, mode, phases, started_at, finished_at, verdict,
                   claimed_total_first, claimed_total_last, harvested_total,
                   sum_of_leaves
            FROM runs WHERE source_id = %s ORDER BY id DESC
        """, (source_id,))

        queue = _rows(cur, """
            SELECT q.reason,
                   count(*) FILTER (WHERE q.resolved_at IS NULL) AS open,
                   count(*) FILTER (WHERE q.resolved_at IS NOT NULL) AS resolved
            FROM deferred_queue q JOIN items i ON i.urn = q.urn
            WHERE i.source_id = %s GROUP BY 1 ORDER BY open DESC, reason
        """, (source_id,))

        cost = _rows(cur, """
            SELECT category, round(sum(amount), 2)::float8 AS amount,
                   round(sum(eur), 4)::float8 AS eur
            FROM cost_ledger WHERE source_id = %s GROUP BY 1 ORDER BY eur DESC
        """, (source_id,))

        blobs = _rows(cur, """
            SELECT coalesce(kept_reason, kept::text) AS bucket, count(*) AS n,
                   coalesce(sum(bytes), 0)::bigint AS bytes,
                   count(*) FILTER (WHERE storage_ref IS NOT NULL) AS on_disk_n
            FROM blobs b JOIN items i ON i.urn = b.urn
            WHERE i.source_id = %s GROUP BY 1 ORDER BY bytes DESC
        """, (source_id,))

        failed = _rows(cur, """
            SELECT urn, stage, reason, retryable, updated_at
            FROM items WHERE source_id = %s AND disposition = 'failed'
            ORDER BY updated_at DESC LIMIT 50
        """, (source_id,))

    return {
        "source": {
            "id": s["id"], "name": s["name"], "base_url": s["base_url"],
            "paused": s["paused"], "paused_reason": s["paused_reason"],
            "created_at": _iso(s["created_at"]),
        },
        "funnel": _funnel(stage_map),
        "matrix": stage_map,
        "dispositions": _dispositions(stage_map),
        "missing": [{"reason": r["reason"], "count": r["n"]} for r in missing],
        "runs": [{
            "id": r["id"], "mode": r["mode"], "phases": r["phases"],
            "started_at": _iso(r["started_at"]), "finished_at": _iso(r["finished_at"]),
            "duration_s": ((r["finished_at"] - r["started_at"]).total_seconds()
                           if r["finished_at"] else None),
            "verdict": r["verdict"],
            "claimed_first": r["claimed_total_first"],
            "claimed_last": r["claimed_total_last"],
            "harvested": r["harvested_total"],
            "sum_of_leaves": r["sum_of_leaves"],
        } for r in runs],
        "queue": [{"reason": r["reason"], "open": r["open"], "resolved": r["resolved"]}
                  for r in queue],
        "cost": [{"category": r["category"], "amount": r["amount"], "eur": r["eur"]}
                 for r in cost],
        "blobs": [{"bucket": r["bucket"], "count": r["n"], "bytes": r["bytes"],
                   "on_disk_count": r["on_disk_n"]} for r in blobs],
        "failed_items": [{
            "urn": r["urn"], "stage": r["stage"], "reason": r["reason"],
            "retryable": r["retryable"], "updated_at": _iso(r["updated_at"]),
        } for r in failed],
    }


# -------------------------------------------------------------------- run report

@router.get("/run/{run_id}")
def run_report(run_id: int):
    with db.tx() as cur:
        r = _one(cur, """
            SELECT r.*, s.name AS source_name
            FROM runs r JOIN sources s ON s.id = r.source_id WHERE r.id = %s
        """, (run_id,))
        if r is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        parts = _rows(cur, """
            SELECT leaf, claimed, harvested, cap_hit, subdivided, notes
            FROM run_partitions WHERE run_id = %s ORDER BY leaf
        """, (run_id,))
    return {
        "id": r["id"], "source_id": r["source_id"], "source_name": r["source_name"],
        "mode": r["mode"], "phases": r["phases"],
        "started_at": _iso(r["started_at"]), "finished_at": _iso(r["finished_at"]),
        "duration_s": ((r["finished_at"] - r["started_at"]).total_seconds()
                       if r["finished_at"] else None),
        "verdict": r["verdict"],
        "claimed_total_first": r["claimed_total_first"],
        "claimed_total_last": r["claimed_total_last"],
        "harvested_total": r["harvested_total"],
        "sum_of_leaves": r["sum_of_leaves"],
        "report": r["report"],
        "partitions": [{
            "leaf": p["leaf"], "claimed": p["claimed"], "harvested": p["harvested"],
            "cap_hit": p["cap_hit"], "subdivided": p["subdivided"], "notes": p["notes"],
        } for p in parts],
    }


# -------------------------------------------------------- projection mapping drift

@router.get("/projection")
def projection():
    """Do the live `civic-*` mappings still match the declared schema? (PROJ-2)

    `uv run civic check-projection` has existed since the drift was found, and that
    was the gap: a control that must be *chosen* is not a control. Rule 5 of
    `design/failure-modes.md` — a prompt-level obligation with no mechanical check is
    not a control — applies to the human running the command too. This gives the
    check a place where it is looked at without being asked for.

    Drift is not cosmetic. A field typed `text` where the schema says `keyword` makes
    aggregations and term filters fail *on that shard only*, which OpenSearch reports
    as a partial success inside an HTTP 200 — inside the cross-corpus union, a whole
    source silently vanishes from a plausible-looking answer.

    Never raises: an unreachable cluster is reported as an unreachable cluster
    (`available: false`), because this endpoint's whole purpose is that silence is
    the failure mode. It is separate from `/overview` so that the rest of the
    dashboard keeps working without OpenSearch at all.
    """
    try:
        from engine.project import os_client, verify

        reports = verify.check_indices(os_client.client())
    except Exception as e:
        return {
            "available": False,
            "error": f"{type(e).__name__}: {e}",
            "indices": [],
            "drifted": 0,
            "checked_at": _now(),
        }
    indices = [{**r, "clean": verify.is_clean(r)} for r in reports]
    return {
        "available": True,
        "indices": indices,
        "drifted": sum(1 for i in indices if not i["clean"]),
        # The fix is named here rather than in the UI: the endpoint knows why the
        # rebuild is cheap (Postgres holds text and vectors), the dashboard doesn't.
        "fix": "uv run civic project <source> --recreate",
        "checked_at": _now(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
