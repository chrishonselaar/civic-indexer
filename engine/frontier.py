"""Frontier: two-phase discover -> ledger (decision-inventory.md §13).

Discovery writes envelope rows; "already done" is always a ledger question,
never file existence. Everything encountered is recorded — stubs and deferred
items included — or reconciliation arithmetic will not close.
"""

from datetime import datetime, timezone
from typing import Any

from engine import db, manifest as mf, runs
from engine.budget import Budget
from engine.envelope import DiscoveredItem
from engine.reconcile import Reconciliation
from engine.strategies.notubiz_events import NotubizEventsStrategy
from engine.strategies.oai_pmh import OaiPmhStrategy
from engine.strategies.ori_elastic import OriElasticStrategy
from engine.strategies.paged_html_search import PagedHtmlSearchStrategy
from engine.strategies.paged_json_api import PagedJsonApiStrategy
from engine.strategies.sru_records import SruRecordsStrategy

STRATEGY_CLASSES = {
    "paged_json_api": PagedJsonApiStrategy,
    "notubiz_events": NotubizEventsStrategy,
    "paged_html_search": PagedHtmlSearchStrategy,
    "sru_records": SruRecordsStrategy,
    "oai_pmh": OaiPmhStrategy,
    "ori_elastic": OriElasticStrategy,
}


def upsert_discovered(
    cur,
    item: DiscoveredItem,
    *,
    source_id: str,
    manifest_version: int,
    run_id: int,
    strategy_name: str,
    file_url_template: str | None,
) -> str:
    """Insert/refresh one discovered item. Returns 'new' | 'updated' | 'unchanged'."""
    cur.execute("SELECT payload, stage FROM items WHERE urn = %s", (item.urn,))
    existing = cur.fetchone()

    now = datetime.now(timezone.utc)
    if existing is None:
        cur.execute(
            """
            INSERT INTO items (urn, source_id, natural_key, manifest_version,
                discovered_via_strategy, discovered_via_partition_leaf,
                discovered_via_run_id, stage, disposition, reason, payload,
                claimed_total_context, trusted_published, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'discovered', %s, %s, %s, %s, %s, %s)
            """,
            (
                item.urn, source_id, item.natural_key, manifest_version,
                strategy_name, item.partition_leaf, run_id,
                item.disposition, item.reason, db.jsonb(item.payload),
                db.jsonb({"leaf_claimed": None, "leaf_position": item.leaf_position}),
                item.published or None, now,
            ),
        )
        outcome = "new"
    elif existing["payload"] == item.payload:
        outcome = "unchanged"
    else:
        # Refresh source metadata; never touch the monotonic stage axis here.
        cur.execute(
            "UPDATE items SET payload = %s, trusted_published = %s, updated_at = %s "
            "WHERE urn = %s",
            (db.jsonb(item.payload), item.published or None, now, item.urn),
        )
        outcome = "updated"

    urls: list[tuple[str, str]] = []
    if item.landing_url:
        urls.append((item.landing_url, "landing"))
    if item.disposition == "ok":
        if item.file_url:  # strategy-provided (payload) URL wins over templates
            urls.append((item.file_url, "file"))
        elif file_url_template:
            urls.append((file_url_template.format(natural_key=item.natural_key), "file"))
    urls.extend(item.extra_urls)
    for url, role in urls:
        cur.execute(
            "INSERT INTO item_urls (urn, url, role) VALUES (%s, %s, %s) "
            "ON CONFLICT (urn, url) DO UPDATE SET last_seen = now()",
            (item.urn, url, role),
        )
    return outcome


def run_discover(
    m: dict,
    *,
    mode: str = "backfill",
    window_start: str | None = None,
    http_get=None,
    fixture_recorder=None,
) -> tuple[int, dict[str, Any]]:
    """Discover phase: walk the source's listing, ledger every record, reconcile."""
    source_id = mf.get(m, "source.id")
    manifest_version = mf.store(m)
    role_name = "backfill" if mode == "backfill" else "incremental"
    role = mf.resolve_role(m, role_name)
    strategy_name = role["strategy"]
    file_url_template = mf.get(m, "fetch.file_url_template")
    overlap = (
        mf.get(m, "discovery.roles.incremental.cutoff.overlap_pages", 0)
        if window_start
        else 0
    )

    run_id = runs.start(source_id, manifest_version, mode, phases=["discover"])
    budget = Budget(run_id, source_id, mf.get(m, "budgets.eur_per_run_cap"))

    strategy = STRATEGY_CLASSES[strategy_name](
        role,
        m["identity"],
        m.get("politeness", {}),
        http_get=http_get,
    )
    if fixture_recorder is not None:
        strategy._http_get = fixture_recorder(strategy._http_get)

    leaves: dict[str, dict[str, Any]] = {}
    outcomes = {"new": 0, "updated": 0, "unchanged": 0, "stubs": 0}

    with db.tx() as cur:
        for item in strategy.discover(
            until_before=window_start, overlap_pages=overlap
        ):
            outcome = upsert_discovered(
                cur,
                item,
                source_id=source_id,
                manifest_version=manifest_version,
                run_id=run_id,
                strategy_name=strategy_name,
                file_url_template=file_url_template,
            )
            outcomes[outcome] += 1
            if item.disposition == "stub":
                outcomes["stubs"] += 1
            leaf = leaves.setdefault(
                item.partition_leaf, {"claimed": None, "harvested": 0, "cap_hit": False}
            )
            leaf["harvested"] += 1

    # Strategies whose yielded items are CHILDREN of the walk unit (e.g. documents
    # under events) expose their own per-leaf accounting in the walk's native unit;
    # reconciliation must use that, not the yielded-item count.
    leaf_stats = getattr(strategy, "leaf_stats", None)
    if leaf_stats:
        leaves = leaf_stats
    harvested_total = sum(v["harvested"] for v in leaves.values())
    budget.counters["requests"] = strategy.requests_made

    rec = Reconciliation(
        claimed_total_first=strategy.claimed_total_first,
        claimed_total_last=strategy.claimed_total_last,
        harvested_total=harvested_total,
        sum_of_leaves=harvested_total,  # leaves are client-side buckets of the same walk
        scope="window" if window_start else "full",
        cap_hits=["<global sanity cap>"] if strategy.sanity_cap_hit else [],
        all_partitions_empty=(harvested_total == 0 and strategy.raw_results == 0),
        selector_zero_matches=(strategy.requests_made > 0 and strategy.raw_results == 0),
    )
    if strategy.walk_duplicates:
        rec.warnings.append(
            f"{strategy.walk_duplicates} duplicate records mid-walk (page shift)"
        )
    if strategy.unrecordable:
        rec.warnings.append(
            f"{strategy.unrecordable} records had no usable identity (unrecordable)"
        )

    runs.record_partitions(run_id, leaves)
    report = runs.finish(
        run_id,
        source_id,
        rec,
        outcomes=outcomes,
        cost=budget.flush(),
        selector_health={
            "items_path_matches": strategy.raw_results,
            "zero_match_alarm": strategy.raw_results == 0,
        },
    )
    return run_id, report
