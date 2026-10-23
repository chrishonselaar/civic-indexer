"""Postgres -> OpenSearch projection. Fully rebuildable, never source of truth.

Deleting an index and re-projecting must yield identical counts
(item-envelope.md §1 principle 7).
"""

import sys
from datetime import datetime, timezone
from typing import Any, Iterator

from engine import blobstore, db, exposure, suppress
from engine.chunker import CHUNKER_VERSION, DocumentChunker
from engine.manifest import get as mget
from engine.project import mappings, os_client

# Payload paths used when the stored manifest declares no index.projection block
# (the original open.overheid shape).
DEFAULT_PROJECTION = {
    "title": "document.titel",
    "publisher": "document.publisher",
    "file_type": "bestandsType",
}


def _load_manifest(cur, source_id: str) -> dict:
    cur.execute(
        "SELECT payload FROM manifests WHERE source_id = %s ORDER BY version DESC LIMIT 1",
        (source_id,),
    )
    row = cur.fetchone()
    return (row["payload"] if row else None) or {}


def projection_of(m: dict) -> dict:
    return {**DEFAULT_PROJECTION, **(mget(m, "index.projection") or {})}


def lookups_of(m: dict) -> dict[str, dict]:
    """Declared enrichment joins, keyed by the projected field each one fills.

    PROJ-1: a projected field whose payload path is permanently null is a dead
    facet. Where the source captures an *id* but never the label (Notubiz
    `gremium_id` vs `gremium_name`), the manifest carries the id->label table and
    the join happens here, at projection time — inside an existing hook, no new
    pipeline stage (CLAUDE.md working method, and the remedy the manifest's own
    known_defects block prescribes).

    Lookup keys are normalised to str on both sides: YAML integer keys come back
    from JSONB as strings, and the payload may hold either.
    """
    out: dict[str, dict] = {}
    for name, table in (mget(m, "enrichment.lookup_tables") or {}).items():
        if not isinstance(table, dict):
            continue
        field, path, values = table.get("to"), table.get("from"), table.get("values")
        if not (field and path and isinstance(values, dict)):
            continue
        out[field] = {
            "name": name,
            "from": path,
            "values": {str(k): v for k, v in values.items()},
        }
    return out


def _resolve_lookup(payload: dict, spec: dict) -> Any:
    key = mget(payload, spec["from"])
    if key is None:
        return None
    # An unmapped id resolves to null, never to the raw id: an opaque number
    # loose in a facet is worse than an empty one.
    return spec["values"].get(str(key))


def _select_projectable(cur, source_id: str) -> list[dict]:
    cur.execute(
        """
        SELECT i.urn, i.natural_key, i.source_id, i.payload, i.disposition, i.missing,
               i.trusted_published,
               e.method AS extraction_method, e.confidence AS extraction_confidence,
               e.pages_processed, e.text_ref,
               lu.url AS landing_url, fu.url AS file_url
        FROM items i
        JOIN extractions e ON e.urn = i.urn
        LEFT JOIN item_urls lu ON lu.urn = i.urn AND lu.role = 'landing'
        LEFT JOIN item_urls fu ON fu.urn = i.urn AND fu.role = 'file'
        WHERE i.source_id = %s
          AND i.stage IN ('extracted', 'enriched', 'indexed')
          AND i.disposition IN ('ok', 'partial')
          AND NOT EXISTS (SELECT 1 FROM suppressions s WHERE s.urn = i.urn)
        ORDER BY i.trusted_published
        """,
        (source_id,),
    )
    return cur.fetchall()


def _doc_fields(
    row: dict, projection: dict, lookups: dict[str, dict] | None = None
) -> dict[str, Any]:
    payload = row["payload"] or {}
    published = (
        row["trusted_published"].date().isoformat() if row["trusted_published"] else None
    )
    fields = {
        "title": mget(payload, projection["title"]) or row["natural_key"],
        "publisher": mget(payload, projection["publisher"]),
        "published": published,
        "published_month": published[:7] if published else None,
        "file_type": mget(payload, projection["file_type"]),
        "disposition": row["disposition"],
        "landing_url": row["landing_url"],
    }
    # A declared lookup OWNS its field — the manifest may not also declare a
    # payload path for it (validate() rejects that contradiction), so this
    # overrides only the DEFAULT_PROJECTION fallback.
    for field, spec in (lookups or {}).items():
        fields[field] = _resolve_lookup(payload, spec)
    return fields


def read_text_blob(text_ref: str) -> str | None:
    """The extracted text behind a ledger row, or **None when the blob is absent**.

    None and "" are different answers and the caller must be able to tell them apart:
    an empty string is a document with no text in it, a missing blob is this machine
    not having the text at all. Collapsing the two is what let a deploy project 47,967
    documents as `content: ""` and report `docs_failed: 0` (2026-09-08).
    """
    path = blobstore.from_ref(text_ref)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def project(dataset_source_id: str, *, recreate: bool = False) -> dict[str, int]:
    osc = os_client.client()
    doc_index = mappings.doc_index_name(dataset_source_id)
    chunk_index = mappings.chunk_index_name(dataset_source_id)
    os_client.ensure_index(osc, doc_index, mappings.doc_index_config(), recreate)
    os_client.ensure_index(osc, chunk_index, mappings.chunk_index_config(), recreate)

    with db.tx() as cur:
        rows = _select_projectable(cur, dataset_source_id)
        suppressed = suppress.urns_for(cur, dataset_source_id)
        stored = _load_manifest(cur, dataset_source_id)
        projection = projection_of(stored)
        lookups = lookups_of(stored)
        # Cached vectors ride along when present (embed phase is optional/lazy;
        # a projection without vectors is still valid — BM25-only).
        cur.execute(
            "SELECT kind, key, vector FROM embeddings WHERE source_id = %s",
            (dataset_source_id,),
        )
        vectors = {(r["kind"], r["key"]): r["vector"] for r in cur.fetchall()}

    # design/access-posture.md: T0 is a PROJECTION boundary, not a response-time one.
    # Text that is never indexed cannot leak from any endpoint, present or future — which
    # is the whole reason the lowest tier is enforced here and the others in the API.
    declared_tier = exposure.of_manifest(stored)
    tier = declared_tier or exposure.UNDECLARED_DEFAULT
    if declared_tier is None:
        print(
            f"WARNING: {dataset_source_id} declares no policy.exposure; projecting at the "
            f"permissive default {tier}. See design/access-posture.md §3.",
            file=sys.stderr,
        )
    with_text = exposure.indexes_text(tier)
    # Empty unless the manifest opts in: no third party sees this text by default.
    processors = exposure.processors_of_manifest(stored)

    chunker = DocumentChunker()
    now = datetime.now(timezone.utc).isoformat()

    # A ledger row that claims a `text_ref` whose blob is not on this machine used to
    # project as `content: ""` and count as a success — the run printed
    # `docs_indexed: 47967, docs_failed: 0` over an index in which nothing but titles
    # was searchable (measured on the demo host 2026-09-08, after a deploy that moved
    # the ledger but not `data/texts`). Empty text is indistinguishable from a document
    # with nothing in it, so it has to be counted and said out loud.
    missing_text = {"n": 0}

    def doc_actions() -> Iterator[dict]:
        for row in rows:
            text = ""
            if with_text and row["text_ref"]:
                blob = read_text_blob(row["text_ref"])
                if blob is None:
                    missing_text["n"] += 1
                else:
                    text = blob
            doc = {
                "_id": row["urn"],
                "urn": row["urn"],
                "source_id": row["source_id"],
                "natural_key": row["natural_key"],
                # Each document carries its own tier so an endpoint can enforce it
                # without a database round-trip, and so a stale index cannot
                # over-serve: a document projected at T0 has no text to give even if
                # the manifest later says T2.
                "exposure": tier,
                "processors": processors,
                "missing": row["missing"] or [],
                "pages": row["pages_processed"],
                "extraction_method": row["extraction_method"],
                "extraction_confidence": row["extraction_confidence"],
                "file_url": row["file_url"],
                "indexed_at": now,
                **_doc_fields(row, projection, lookups),
            }
            # Omitted, not emptied: at T0 the field should be absent from the document
            # so that `exists`-style queries and any future consumer see "there is no
            # text here" rather than "there is a text here and it is blank".
            if with_text:
                doc["content"] = text
                vec = vectors.get(("doc", row["urn"]))
                if vec is not None:
                    doc["embedding"] = vec
            yield doc

    def chunk_actions() -> Iterator[dict]:
        if not with_text:
            return
        for row in rows:
            if not row["text_ref"]:
                continue
            # Counted by doc_actions already — do not double-count the same absence.
            path = blobstore.from_ref(row["text_ref"])
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            fields = _doc_fields(row, projection, lookups)
            for chunk in chunker.chunk_document(text, row["urn"]):
                action = {
                    "_id": chunk.chunk_id,
                    "chunk_id": chunk.chunk_id,
                    "parent_urn": chunk.parent_urn,
                    "chunk_text": chunk.text,
                    "chunk_number": chunk.number,
                    "total_chunks": chunk.total,
                    "chunk_size_words": chunk.size_words,
                    "chunker_version": CHUNKER_VERSION,
                    "source_id": row["source_id"],
                    **fields,
                }
                vec = vectors.get(("chunk", chunk.chunk_id))
                if vec is not None:
                    action["embedding"] = vec
                yield action

    docs_ok, docs_failed = os_client.bulk_index(osc, doc_index, doc_actions())
    chunks_ok, chunks_failed = os_client.bulk_index(osc, chunk_index, chunk_actions())
    osc.indices.refresh(index=f"{doc_index},{chunk_index}")

    # The SELECT above already leaves suppressed items out, but a projection run only
    # ever *adds*: anything indexed before it was suppressed would stay searchable
    # until someone happened to --recreate. Delete it on every run, not once.
    purged = suppress.purge_from_projection(dataset_source_id, suppressed, osc=osc)

    # Same reasoning for a tier that was lowered to T0: the doc bodies are replaced in
    # place by the bulk index (so their text and vectors go), but chunks written under a
    # higher tier would survive untouched and keep the full text searchable. Lowering a
    # tier has to take effect on the next run or it is not a control.
    chunks_withdrawn = 0
    if not with_text:
        chunks_withdrawn = os_client.delete_by_source(osc, chunk_index, dataset_source_id)

    with db.tx() as cur:
        cur.execute(
            # Suppressed items must not be marked 'indexed' — they are not in the
            # index, and a stage that lies is worse than one that lags.
            "UPDATE items SET stage = 'indexed', indexed_at = %s, updated_at = %s "
            "WHERE source_id = %s AND stage = 'extracted' "
            "AND disposition IN ('ok', 'partial') "
            "AND NOT EXISTS (SELECT 1 FROM suppressions s WHERE s.urn = items.urn)",
            (datetime.now(timezone.utc), datetime.now(timezone.utc), dataset_source_id),
        )

    # Loud, and at the end where a person reads it. `docs_failed: 0` is true — the bulk
    # index accepted every document — so the only place this can surface is here.
    if missing_text["n"]:
        print(
            f"WARNING: {missing_text['n']} of {docs_ok} documents were projected with "
            f"EMPTY text: the ledger names a text blob that is not on this machine. "
            f"They are indexed by title only. Check CIVIC_DATA_DIR and whether "
            f"data/texts/ travelled with the ledger."
        )

    return {
        "docs_indexed": docs_ok,
        "docs_failed": docs_failed,
        "chunks_indexed": chunks_ok,
        "chunks_failed": chunks_failed,
        "suppressed": len(suppressed),
        "exposure": tier,
        "chunks_withdrawn": chunks_withdrawn,
        # 0 on a healthy run. Non-zero means the index is quietly title-only for that
        # many documents, which no other counter here can express.
        "text_blobs_missing": missing_text["n"],
        **purged,
    }
