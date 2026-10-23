"""Bulk-archive resolver — the `zip_dossier` deferred queue's reader.

The third bulk protocol from bulk-endpoint-discovery.md (after `sru_records` and
`oai_pmh`). Where those *discover* over a bulk surface, this *resolves* the archives
the fetch phase parked: it downloads nothing (the container blob is already kept on
disk), so unlike the OCR-tail drain it needs no burst pod — it is a local,
synchronous CPU pass. Two shapes, split by `archive.classify()`:

  * **OOXML office document** (docx/xlsx/pptx) — ONE document mis-sniffed as zip.
    Distil its text in place; the item becomes `extracted/ok`. (The 16 XLSX + 1 DOCX
    Haarlem items were stuck here.)
  * **document archive** — fan each routable member out to a **child item**
    (`{parent}#NNNN`), linked to the parent by an `attachment_of` edge, and route it
    through the same native ladder the fetch phase uses (PDF → native text, scanned →
    deferred `ocr_tail`). The parent besluit is left as a searchable *container*: a
    distillate listing its member titles, so the dossier itself still answers a query
    while each released document is independently findable.

Idempotent at the parent grain: a resolved parent stamps `deferred_queue.resolved_at`
and is never selected again; child inserts are `ON CONFLICT DO NOTHING`.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from engine import blobstore, db, manifest as mf, runs
from engine.budget import Budget
from engine.extract import archive, native_pdf, office, xml_text
from engine.process import MIN_TEXT_BYTES, _defer, _set_status
from engine.reconcile import Reconciliation

CHILD_STRATEGY = "archive_member"
ARCHIVE_ENGINE = "archive-manifest"      # parent container distillate
ARCHIVE_ENGINE_VERSION = "1"

_NUM_PREFIX = re.compile(r"^\d+[_-]")
_DUP_EXT = re.compile(r"(\.[A-Za-z0-9]{1,5})\1$")


def _set_dotted(payload: dict, path: str, value) -> dict:
    """Return a shallow-copied payload with `value` set at a dotted path (e.g.
    'document.titel'), so a child item's title lands where the manifest's title_field
    expects it regardless of source shape."""
    keys = path.split(".")
    out = dict(payload)
    node = out
    for k in keys[:-1]:
        node[k] = dict(node.get(k) or {})
        node = node[k]
    node[keys[-1]] = value
    return out


def _display_title(member_name: str) -> str:
    """Human title for a member: drop the archive path + the ordering prefix, and
    collapse the common doubled extension (`name.pdf.pdf` -> `name.pdf`)."""
    base = member_name.rsplit("/", 1)[-1]
    base = _NUM_PREFIX.sub("", base)
    base = _DUP_EXT.sub(r"\1", base)
    return base or member_name


def _select_archives(cur, source_id: str, limit: int | None) -> list[dict]:
    sql = """
        SELECT q.id AS qid, i.urn, i.natural_key, i.payload, i.manifest_version,
               i.trusted_published, i.discovered_via_partition_leaf AS leaf,
               b.storage_ref, u.url AS file_url
        FROM deferred_queue q
        JOIN items i ON i.urn = q.urn
        JOIN blobs b ON b.urn = i.urn AND b.kept AND b.storage_ref IS NOT NULL
        LEFT JOIN item_urls u ON u.urn = i.urn AND u.role = 'file'
        WHERE q.reason = 'zip_dossier' AND q.resolved_at IS NULL
          AND i.source_id = %s
        ORDER BY i.trusted_published DESC NULLS LAST
    """
    params: list[Any] = [source_id]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    cur.execute(sql, params)
    return cur.fetchall()


def resolve_archives(
    m: dict, *, source_id: str | None = None, limit: int | None = None,
) -> tuple[int, dict[str, Any]]:
    source_id = source_id or mf.get(m, "source.id")
    manifest_version = mf.store(m)
    conf_threshold = mf.get(m, "extraction.native_confidence_threshold", 0.5)
    ladder = mf.get(m, "extraction.ladder", ["native_pdf_text"])
    title_field = mf.get(m, "extraction.title_field", "document.titel")

    run_id = runs.start(source_id, manifest_version, "upgrade", phases=["extract"])
    budget = Budget(run_id, source_id, mf.get(m, "budgets.eur_per_run_cap"))

    with db.tx() as cur:
        archives = _select_archives(cur, source_id, limit)

    outcomes = {
        "office_ok": 0, "office_empty": 0, "archives_fanned": 0,
        "members_extracted": 0, "members_ocr_deferred": 0,
        "members_failed": 0, "members_unroutable": 0, "parents_failed": 0,
    }
    leaves: dict[str, dict[str, Any]] = {}
    resolved = 0

    for arc in archives:
        leaf = arc["leaf"] or "?"
        leaves.setdefault(leaf, {"claimed": 0, "harvested": 0, "cap_hit": False})
        leaves[leaf]["claimed"] += 1
        blob = blobstore.from_ref(arc["storage_ref"])
        kind = archive.classify(blob) if blob.exists() else "corrupt"

        if kind in ("docx", "xlsx", "pptx"):
            _resolve_office(arc, blob, kind, source_id=source_id,
                            title_field=title_field, outcomes=outcomes)
        elif kind == "document_archive":
            _resolve_archive(
                arc, blob, source_id=source_id, run_id=run_id,
                manifest_version=manifest_version, ladder=ladder,
                conf_threshold=conf_threshold, title_field=title_field,
                outcomes=outcomes,
            )
        else:  # ooxml (unsupported body) | empty | corrupt | missing blob
            _fail_parent(arc, source_id=source_id, kind=kind, outcomes=outcomes)

        resolved += 1
        leaves[leaf]["harvested"] += 1

    rec = Reconciliation(
        claimed_total_first=len(archives),
        claimed_total_last=len(archives),
        harvested_total=resolved,
        sum_of_leaves=sum(v["harvested"] for v in leaves.values()),
        scope="full",
        all_partitions_empty=(len(archives) > 0 and resolved == 0),
    )
    runs.record_partitions(run_id, leaves)
    report = runs.finish(
        run_id, source_id, rec,
        outcomes=outcomes, cost=budget.flush(),
        selector_health={"archives": len(archives)},
    )
    return run_id, report


def _resolve_office(
    arc: dict, blob, kind: str, *, source_id: str, title_field: str, outcomes: dict,
) -> None:
    """OOXML single document: distil text in place, no fan-out."""
    urn, key = arc["urn"], arc["natural_key"]
    ex = office.extract(blob, kind)
    title = mf.get(arc["payload"] or {}, title_field) or key
    now = datetime.now(timezone.utc)
    with db.tx() as cur:
        if not ex.text:
            _set_status(cur, urn, stage="fetched", disposition="failed",
                        reason=f"{kind} contained no extractable text", retryable=False)
            _resolve_queue(cur, arc["qid"])
            outcomes["office_empty"] += 1
            return
        text_file = blobstore.text_path(source_id, arc["leaf"], key)
        text_file.write_text(native_pdf.to_markdown(title, ex.text), encoding="utf-8")
        db.upsert(
            cur, "extractions",
            {"urn": urn, "method": "native", "engine": office.ENGINE,
             "engine_version": office.ENGINE_VERSION, "confidence": ex.confidence,
             "pages_processed": ex.units, "text_ref": blobstore.ref(text_file)},
            conflict="urn, method, engine",
        )
        _delete_blob(cur, urn, arc["file_url"], blob)
        _set_status(cur, urn, stage="extracted", disposition="ok", missing=[], upgradable=False)
        cur.execute("UPDATE items SET processed_at = %s, updated_at = %s WHERE urn = %s",
                    (now, now, urn))
        _resolve_queue(cur, arc["qid"])
    outcomes["office_ok"] += 1


def _resolve_archive(
    arc: dict, blob, *, source_id: str, run_id: int,
    manifest_version: int, ladder: list, conf_threshold: float,
    title_field: str, outcomes: dict,
) -> None:
    """Document archive: fan routable members out to child items, keep the parent
    as a searchable container listing."""
    parent_urn, parent_key = arc["urn"], arc["natural_key"]
    leaf = arc["leaf"]
    mems = archive.members(blob)
    routable = [mem for mem in mems if mem.routable]
    member_titles: list[str] = []

    for mem in mems:
        title = _display_title(mem.name)
        if not mem.routable:
            outcomes["members_unroutable"] += 1
            continue
        member_titles.append(title)
        child_key = f"{parent_key}#{mem.index:04d}"
        child_urn = f"{source_id}:{child_key}"
        # Child payload inherits the parent's facets (publisher/date/type) with the
        # member filename as its title (set at the manifest's title_field), so it
        # projects like any first-class item.
        child_payload = _set_dotted(arc["payload"] or {}, title_field, title)
        child_payload["_archive"] = {"parent": parent_urn, "member": mem.name}

        with db.tx() as cur:
            cur.execute(
                """
                INSERT INTO items (urn, source_id, natural_key, manifest_version,
                    discovered_via_strategy, discovered_via_partition_leaf,
                    discovered_via_run_id, stage, disposition, payload,
                    trusted_published, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'discovered','ok',%s,%s,now())
                ON CONFLICT (urn) DO NOTHING
                """,
                (child_urn, source_id, child_key, manifest_version, CHILD_STRATEGY,
                 leaf, run_id, db.jsonb(child_payload), arc["trusted_published"]),
            )
            cur.execute(
                """
                INSERT INTO edges (source_urn, type, target_scheme, target_value,
                    resolved_urn, established, via)
                VALUES (%s,'attachment_of','urn',%s,%s,'derived',%s)
                ON CONFLICT (type, source_urn, target_scheme, target_value) DO NOTHING
                """,
                (child_urn, parent_urn, parent_urn, CHILD_STRATEGY),
            )

        child_dest = blobstore.blob_path(source_id, child_key)
        archive.extract_member(blob, mem.name, child_dest)
        _route_member(
            child_urn, child_key, title, mem, child_dest,
            source_id=source_id, leaf=leaf, ladder=ladder,
            conf_threshold=conf_threshold, outcomes=outcomes,
        )

    # Parent -> searchable container: title + member listing distillate.
    now = datetime.now(timezone.utc)
    parent_title = mf.get(arc["payload"] or {}, title_field) or parent_key
    listing = "\n".join(f"- {t}" for t in member_titles)
    body = (f"Dossier met {len(routable)} document(en).\n\n{listing}"
            if routable else "Leeg of niet-routeerbaar archief.")
    with db.tx() as cur:
        text_file = blobstore.text_path(source_id, leaf, parent_key)
        text_file.write_text(native_pdf.to_markdown(parent_title, body), encoding="utf-8")
        db.upsert(
            cur, "extractions",
            {"urn": parent_urn, "method": "archive", "engine": ARCHIVE_ENGINE,
             "engine_version": ARCHIVE_ENGINE_VERSION, "confidence": 1.0,
             "pages_processed": len(routable), "text_ref": blobstore.ref(text_file)},
            conflict="urn, method, engine",
        )
        _delete_blob(cur, parent_urn, arc["file_url"], blob)
        _set_status(cur, parent_urn, stage="extracted", disposition="ok", missing=[], upgradable=False,
                    reason=f"archive container: {len(routable)} member(s) fanned out")
        cur.execute("UPDATE items SET processed_at = %s, updated_at = %s WHERE urn = %s",
                    (now, now, parent_urn))
        _resolve_queue(cur, arc["qid"])
    outcomes["archives_fanned"] += 1


def _route_member(
    urn: str, key: str, title: str, mem, dest, *, source_id: str, leaf,
    ladder: list, conf_threshold: float, outcomes: dict,
) -> None:
    """Route one extracted member file through the native ladder (mirrors the
    fetch phase's PDF/XML handling; scanned PDFs defer to the OCR tail)."""
    now = datetime.now(timezone.utc)
    with db.tx() as cur:
        if mem.ext == "xml" and "xml_text" in ladder:
            try:
                ex = xml_text.extract(dest)
            except Exception as e:
                _set_status(cur, urn, stage="fetched", disposition="failed",
                            reason=f"xml extraction error: {e}", retryable=False)
                outcomes["members_failed"] += 1
                return
            text_file = blobstore.text_path(source_id, leaf, key)
            text_file.write_text(native_pdf.to_markdown(title, ex.text), encoding="utf-8")
            db.upsert(
                cur, "extractions",
                {"urn": urn, "method": "native", "engine": xml_text.ENGINE,
                 "engine_version": xml_text.ENGINE_VERSION, "confidence": ex.confidence,
                 "pages_processed": ex.pages_processed, "text_ref": blobstore.ref(text_file)},
                conflict="urn, method, engine",
            )
            if ex.confidence > 0:
                dest.unlink(missing_ok=True)
                _set_status(cur, urn, stage="extracted", disposition="ok",
                            missing=[], upgradable=False)
                outcomes["members_extracted"] += 1
            else:
                _set_status(cur, urn, stage="extracted", disposition="failed",
                            reason="xml member contained no body text", retryable=False)
                outcomes["members_failed"] += 1
            cur.execute("UPDATE items SET processed_at=%s, updated_at=%s WHERE urn=%s",
                        (now, now, urn))
            return

        if mem.ext != "pdf":
            dest.unlink(missing_ok=True)
            _set_status(cur, urn, stage="fetched", disposition="failed",
                        reason=f"unrouted member type ({mem.ext})", retryable=False)
            outcomes["members_failed"] += 1
            return

        try:
            ex = native_pdf.extract(dest)
        except Exception as e:
            _keep_member_blob(cur, urn, dest, pages=None)
            _defer(cur, urn, "ocr_tail")
            _set_status(cur, urn, stage="fetched", disposition="partial",
                        missing=["fulltext", "ocr_tail"], upgradable=True,
                        reason=f"native extraction error: {e}")
            outcomes["members_ocr_deferred"] += 1
            return

        text_file = blobstore.text_path(source_id, leaf, key)
        text_file.write_text(native_pdf.to_markdown(title, ex.text), encoding="utf-8")
        db.upsert(
            cur, "extractions",
            {"urn": urn, "method": "native", "engine": native_pdf.ENGINE,
             "engine_version": native_pdf.ENGINE_VERSION,
             "confidence": round(ex.confidence, 4), "pages_processed": ex.pages_processed,
             "text_ref": blobstore.ref(text_file)},
            conflict="urn, method, engine",
        )
        native_ok = ex.confidence >= conf_threshold and len(ex.text) >= MIN_TEXT_BYTES
        if native_ok:
            dest.unlink(missing_ok=True)
            _set_status(cur, urn, stage="extracted", disposition="ok", missing=[], upgradable=False)
            outcomes["members_extracted"] += 1
        else:
            _keep_member_blob(cur, urn, dest, pages=ex.total_pages)
            _defer(cur, urn, "ocr_tail",
                   payload={"pages": ex.total_pages,
                            "native_confidence": round(ex.confidence, 4)})
            _set_status(cur, urn, stage="extracted", disposition="partial",
                        missing=["ocr_tail"], upgradable=True,
                        reason=f"native confidence {ex.confidence:.2f} < {conf_threshold} "
                               f"(likely scanned)")
            outcomes["members_ocr_deferred"] += 1
        cur.execute("UPDATE items SET processed_at=%s, updated_at=%s WHERE urn=%s",
                    (now, now, urn))


def _fail_parent(arc: dict, *, source_id: str, kind: str, outcomes: dict) -> None:
    reason = {
        "ooxml": "unsupported OOXML type; no extractor",
        "empty": "empty archive (no members)",
        "corrupt": "corrupt or unreadable zip",
    }.get(kind, f"unresolved archive ({kind})")
    with db.tx() as cur:
        _set_status(cur, arc["urn"], stage="fetched", disposition="failed",
                    reason=reason, retryable=False)
        _resolve_queue(cur, arc["qid"])
    outcomes["parents_failed"] += 1


def _keep_member_blob(cur, urn: str, dest, pages: int | None) -> None:
    """Register a kept blobs row for a fanned-out member the OCR-tail drain must find.

    A member is never *fetched* (it is unpacked from the parent container), so unlike
    a normal item it has no blobs row — and the drain joins deferred_queue to a kept
    blob. We synthesise the row here: without it the scanned member's `ocr_tail`
    deferral would be undrainable (the exact gap that would silently strand pages)."""
    data = dest.read_bytes()
    db.upsert(
        cur, "blobs",
        {
            "urn": urn, "url": f"archive-member:{urn}", "sniffed_type": "pdf",
            "bytes": len(data), "pages": pages,
            "validated_magic": True, "validated_eof": True, "validated_min_size": True,
            "content_hash": hashlib.sha256(data).hexdigest(),
            "kept": True, "kept_reason": "partial_ocr",
            "storage_ref": blobstore.ref(dest),
        },
        conflict="urn, url",
    )


def _delete_blob(cur, urn: str, url: str | None, blob) -> None:
    """Delete-after-distill on the container: text now exists, the blob does not
    need keeping."""
    blob.unlink(missing_ok=True)
    cur.execute(
        "UPDATE blobs SET kept = false, kept_reason = 'deleted_after_distill', "
        "storage_ref = NULL WHERE urn = %s" + (" AND url = %s" if url else ""),
        (urn, url) if url else (urn,),
    )


def _resolve_queue(cur, qid: int) -> None:
    cur.execute("UPDATE deferred_queue SET resolved_at = now() WHERE id = %s", (qid,))
