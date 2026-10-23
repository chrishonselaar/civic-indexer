"""Ledger invariants: does the ledger still agree with itself?

Three checks already exist and none of them could have caught this class:

- **Reconciliation** (`engine/reconcile.py`) asks whether a run harvested what it
  claimed. It counts items in and items out. The 2026-07-29 backfill reconciled
  perfectly — 2,438 claimed, 2,438 harvested, verdict `reconciled ✓` — while
  destroying 92 items' upgrade marks, because those columns are not part of the
  count.
- **`check-projection`** (`engine/project/verify.py`) asks whether each index agrees
  with its declared mapping and analysis chain. It never reads Postgres.
- The **monitor** shows stage funnels and queue depths. A funnel showing 92 items
  back in `discovered` looks exactly like 92 items legitimately awaiting a fetch.

So there was no check that asks the remaining question: *do the ledger's own columns
still tell a consistent story about the same item?* That is what this is. It is
deliberately about **internal** consistency — no network, no OpenSearch, no file
system — so it is cheap enough to run on every monitor load.

Design notes, because both are load-bearing:

- **Read-only by default.** Same posture as `check-projection`: this reports, a human
  chooses the fix. `repair()` exists for the one finding whose correct value is
  *derivable* rather than guessed, and it is a separate, explicit call.
- **Every finding names the repair and whether it is derivable.** A check that says
  "3 items are inconsistent" and stops gets muted, not fixed.

This is a down-payment on W7 (self-healing), not a substitute: W7 is about a source
whose *shape* changed under us. This is about our own writes contradicting each other.
"""

from engine import db

# Each invariant: a name, the human-readable claim it enforces, the SQL that finds
# violations, and whether repair() can fix it without guessing.
INVARIANTS: list[dict] = [
    {
        "name": "extracted_item_rewound_to_discovered",
        "claim": "an item with an extraction row is past 'discovered'",
        "why": (
            "This is the 92-item signature. A failure path that rewinds `stage` also "
            "clears `missing`/`upgradable`, so the item keeps its text and its index "
            "entry but silently leaves the deferred-upgrade cohort. Nothing else "
            "reports it: the run reconciles, and the funnel just shows more items "
            "waiting to be fetched."
        ),
        "sql": """
            SELECT i.urn, i.source_id, i.stage, i.disposition, i.reason
            FROM items i
            WHERE i.stage = 'discovered'
              AND EXISTS (SELECT 1 FROM extractions e WHERE e.urn = i.urn)
        """,
        "repairable": True,
    },
    {
        "name": "half_set_upgrade_mark",
        "claim": "`upgradable` and `missing` agree — both set, or neither",
        "why": (
            "The two columns are one fact written twice. `upgradable` with no reason "
            "means a pass will re-fetch an item and not know what for; a `missing` "
            "reason with `upgradable` false means the gap is recorded and will never "
            "be filled. Either way the cohort predicate "
            "(`upgradable AND <reason> = ANY(missing)`) silently disagrees with the "
            "human-readable state."
        ),
        "sql": """
            SELECT urn, source_id, stage, disposition, upgradable, missing
            FROM items
            WHERE upgradable <> (missing IS NOT NULL AND array_length(missing, 1) > 0)
        """,
        "repairable": False,
    },
    {
        "name": "duplicate_extraction_rows",
        "claim": "one extraction row per item",
        "why": (
            "Every extractor writes the same text file but `extractions` is keyed "
            "(urn, method, engine), so re-extracting by a new route ADDS a row. "
            "`projector._select_projectable` joins with no dedup, so the URN projects "
            "twice and one row points at text it did not produce."
        ),
        "sql": """
            SELECT e.urn, i.source_id, count(*) AS rows
            FROM extractions e JOIN items i ON i.urn = e.urn
            GROUP BY e.urn, i.source_id HAVING count(*) > 1
        """,
        "repairable": False,
    },
    {
        "name": "open_queue_entry_for_a_finished_item",
        "claim": "an unresolved deferred_queue entry belongs to an upgradable item",
        "why": (
            "The queue and the marks are two records of the same deferral. A queue "
            "entry whose item is no longer upgradable is work that will be picked up "
            "and then decline to do anything — it inflates the monitor's queue depth "
            "forever and trains people to ignore it."
        ),
        "sql": """
            SELECT q.urn, i.source_id, q.reason, i.upgradable
            FROM deferred_queue q JOIN items i ON i.urn = q.urn
            WHERE q.resolved_at IS NULL AND NOT i.upgradable
        """,
        "repairable": False,
    },
]

# The repair for the one derivable finding. Not a guess: an item that has an
# extraction row HAS been extracted, and `indexed_at` records whether it also reached
# the index — so both columns are recoverable from evidence the item already carries.
# `missing`/`upgradable` are deliberately NOT touched: what a lost mark said is not
# derivable, and inventing one would be the same kind of lie that caused the problem.
REPAIR_SQL = """
    UPDATE items i SET
        stage = CASE WHEN i.indexed_at IS NOT NULL THEN 'indexed' ELSE 'extracted' END,
        disposition = CASE WHEN i.disposition = 'failed' THEN 'ok' ELSE i.disposition END,
        retryable = NULL,
        reason = coalesce(i.reason, '') || ' [ledger-repair: stage restored from evidence]',
        updated_at = now()
    WHERE i.stage = 'discovered'
      AND EXISTS (SELECT 1 FROM extractions e WHERE e.urn = i.urn)
      -- ::text on both: Postgres cannot infer a type for an uncast null parameter
      -- (and a placeholder written in this comment would be counted as a real one)
      AND (%s::text IS NULL OR i.source_id = %s::text)
    RETURNING i.urn
"""


def check(source_id: str | None = None) -> list[dict]:
    """Run every invariant. Returns one report per invariant, violations included."""
    reports = []
    with db.tx() as cur:
        for inv in INVARIANTS:
            sql = inv["sql"]
            if source_id:
                # Every invariant selects i.source_id / source_id, so filtering is a
                # wrapper rather than four hand-written variants that can drift apart.
                sql = f"SELECT * FROM ({sql}) f WHERE f.source_id = %s"
                cur.execute(sql, (source_id,))
            else:
                cur.execute(sql)
            rows = cur.fetchall()
            reports.append({
                "name": inv["name"], "claim": inv["claim"], "why": inv["why"],
                "repairable": inv["repairable"],
                "violations": len(rows),
                "sample": [dict(r) for r in rows[:5]],
            })
    return reports


def is_clean(reports: list[dict]) -> bool:
    return not any(r["violations"] for r in reports)


def repair(source_id: str | None = None) -> list[str]:
    """Fix only the derivable finding. Explicit call, never automatic."""
    with db.tx() as cur:
        cur.execute(REPAIR_SQL, (source_id, source_id))
        return [r["urn"] for r in cur.fetchall()]


def format_report(reports: list[dict]) -> str:
    lines = []
    for r in reports:
        if not r["violations"]:
            lines.append(f"ok    {r['name']}")
            continue
        lines.append(f"BROKEN {r['name']}: {r['violations']} item(s)")
        lines.append(f"        claim: {r['claim']}")
        for s in r["sample"]:
            lines.append(f"        e.g. {s}")
        lines.append(
            "        fix: uv run civic check-ledger --repair"
            if r["repairable"]
            else "        fix: not derivable — needs a human decision, see engine/ledger_check.py"
        )
    return "\n".join(lines) or "no invariants defined"
