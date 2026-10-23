"""URN-level suppression — keep a named document out of the public projection.

Backs the promise in `manifests/vlaardingen_raad.yaml` `policy.takedown` and POL-1 §5.
Two halves, and both are needed for the promise to be true:

1. **Durable.** The URN is recorded in Postgres, so every later `civic project` run
   leaves it out — including a `--recreate` rebuild and including a re-discovery of an
   item that had been deleted from the ledger.
2. **Immediate.** Suppressing also deletes the document (and its chunks) from the
   OpenSearch indices right away, because a projection run only *adds*: without an
   explicit delete, an already-indexed document would stay searchable until someone
   remembered to rebuild.

**The dataset is looked up, never parsed out of the URN.** Measured 2026-07-27: the URN
prefix is the *provider* namespace, so `notubiz:262:…` backs both `vlaardingen_raad` and
`haarlem_raad` while `woo_besluiten:…` happens to match its dataset. Deriving the dataset
from the prefix therefore purges the wrong OpenSearch index — silently, since deleting
from an index that does not exist is a legal no-op. The authority is `items.source_id`.

**Scope, stated plainly because a takedown promise lives or dies on its boundary.**
Suppression removes a document from the OpenSearch projection — the only public read
path — and keeps it out. It does **not** delete the ledger row or the fetched bytes.
Postgres is ground truth (`design/README.md`), and a record that we harvested something,
plus who asked for it to be removed and why, is what makes the removal auditable at all.
If a request requires the bytes gone too, that is a separate manual operation on the
blobstore; it is not automated here and `ui/bot.html` says so ("met de hand").
"""

from typing import Iterable

from engine import db
from engine.project import mappings, os_client


def check_urn(urn: str) -> str:
    """Reject an obviously malformed URN before it reaches the database.

    Shape only — `{namespace}:{natural_key}`. This deliberately does NOT return a
    source id: see the module docstring, the prefix is the provider, not the dataset.
    """
    urn = (urn or "").strip()
    if ":" not in urn:
        raise ValueError(f"not a urn ({{namespace}}:{{natural_key}} expected): {urn!r}")
    namespace, natural_key = urn.split(":", 1)
    if not namespace or not natural_key:
        raise ValueError(f"urn has an empty half: {urn!r}")
    return urn


def dataset_of(cur, urn: str) -> str | None:
    """The dataset that ledgered this URN, or None if it has not been discovered yet."""
    cur.execute("SELECT source_id FROM items WHERE urn = %s", (urn,))
    row = cur.fetchone()
    return row["source_id"] if row else None


# ------------------------------------------------------------------ the ledger side


def add(urn: str, *, reason: str, requested_by: str | None = None) -> dict:
    """Record a suppression. Idempotent: re-suppressing updates the reason."""
    urn = check_urn(urn)
    if not (reason or "").strip():
        raise ValueError("a suppression needs a reason — an unexplained one is unreviewable")
    with db.tx() as cur:
        source_id = dataset_of(cur, urn)
        cur.execute(
            """
            INSERT INTO suppressions (urn, source_id, reason, requested_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (urn) DO UPDATE
               SET source_id = COALESCE(EXCLUDED.source_id, suppressions.source_id),
                   reason = EXCLUDED.reason,
                   requested_by = EXCLUDED.requested_by,
                   updated_at = now()
            RETURNING urn, source_id, reason, requested_by, created_at, updated_at
            """,
            (urn, source_id, reason.strip(), requested_by),
        )
        return cur.fetchone()


def remove(urn: str) -> bool:
    """Lift a suppression. Returns False if it was not suppressed.

    The document does not come back on its own — it reappears at the next
    `civic project` run for its source, which is the same path everything else takes
    into the index.
    """
    with db.tx() as cur:
        cur.execute("DELETE FROM suppressions WHERE urn = %s RETURNING urn", (urn,))
        return cur.fetchone() is not None


def listing(source_id: str | None = None) -> list[dict]:
    with db.tx() as cur:
        if source_id:
            cur.execute(
                """
                SELECT s.* FROM suppressions s
                LEFT JOIN items i ON i.urn = s.urn
                WHERE i.source_id = %s OR s.source_id = %s
                ORDER BY s.created_at
                """,
                (source_id, source_id),
            )
        else:
            cur.execute("SELECT * FROM suppressions ORDER BY source_id NULLS LAST, created_at")
        return cur.fetchall()


def urns_for(cur, source_id: str) -> list[str]:
    """Suppressed URNs belonging to one dataset.

    Joins items rather than trusting the cached column: a suppression recorded before
    the item was discovered carries source_id NULL, and it must still take effect the
    moment that item shows up. The cached column covers the inverse case — an item
    that has since been deleted from the ledger.
    """
    cur.execute(
        """
        SELECT s.urn FROM suppressions s
        LEFT JOIN items i ON i.urn = s.urn
        WHERE i.source_id = %s OR s.source_id = %s
        """,
        (source_id, source_id),
    )
    return [r["urn"] for r in cur.fetchall()]


# -------------------------------------------------------------- the projection side


def _delete_by_terms(osc, index: str, field: str, urns: list[str]) -> int:
    if not osc.indices.exists(index=index):
        return 0
    result = osc.delete_by_query(
        index=index,
        body={"query": {"terms": {field: urns}}},
        refresh=True,
        conflicts="proceed",
    )
    return int(result.get("deleted", 0))


def purge_from_projection(
    source_id: str, urns: Iterable[str], *, osc=None
) -> dict[str, int]:
    """Delete suppressed documents and their chunks from OpenSearch.

    Safe to call when nothing is suppressed, when the indices do not exist yet, and
    repeatedly — a delete of an absent document deletes 0, not an error.
    """
    urns = list(urns)
    if not urns:
        return {"docs_deleted": 0, "chunks_deleted": 0}
    osc = osc or os_client.client()
    return {
        "docs_deleted": _delete_by_terms(
            osc, mappings.doc_index_name(source_id), "urn", urns
        ),
        "chunks_deleted": _delete_by_terms(
            osc, mappings.chunk_index_name(source_id), "parent_urn", urns
        ),
    }


def suppress(urn: str, *, reason: str, requested_by: str | None = None) -> dict:
    """Record + purge in one step: what the CLI and any takedown handler should call.

    An undiscovered URN records fine and skips the purge — there is nothing indexed to
    delete, and the durable half keeps it out when the item does arrive.
    """
    row = add(urn, reason=reason, requested_by=requested_by)
    if not row["source_id"]:
        return {**row, "docs_deleted": 0, "chunks_deleted": 0, "not_yet_ledgered": True}
    return {**row, **purge_from_projection(row["source_id"], [urn])}
