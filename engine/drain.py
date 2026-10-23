"""OCR-tail drain (roadmap W1): the deferred_queue's first reader.

Two halves, matching the burst-pod workflow (experiments/ocr_drain/README.md):

  civic drain ocr_tail --export DIR   symlink queued blobs as <stem>.pdf + manifest.json
  civic drain ocr_tail --ingest DIR   read <stem>.md OCR results, apply the upgrade

The upgrade contract (product-roadmap.md W1, failure-modes.md §VOL): the OCR
distillate replaces the native one — the native `extractions` row is deleted
because its text file gets overwritten, so a surviving row would point at text
it did not produce — `missing: [ocr_tail]` resolves, delete-after-distill
applies to the kept blob, and `deferred_queue.resolved_at` is stamped. Runs are
recorded per source (mode=upgrade, phases=[drain]) so the ledger stays the one
account of what happened.

Safety: the blob is only deleted when the result covers every expected page
and carries real text. Anything suspicious (missing result, page mismatch,
near-empty text) leaves the blob — and for the first two, the queue entry —
untouched for the next drain.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from engine import blobstore, db, manifest as mf, runs
from engine.blobstore import _safe
from engine.reconcile import Reconciliation

REASON = "ocr_tail"
OCR_ENGINE = "mineru2.5-pro"
OCR_ENGINE_VERSION = "2605-1.2B"
MIN_OCR_BYTES = 200          # same floor as process.MIN_TEXT_BYTES
PAGE_MARK = "<!-- page"      # emitted once per page by the pod worker
EUR_PER_1K_PAGES = 0.017     # 3090-community rate measured in production-setup.md


def stem(source_id: str, natural_key: str) -> str:
    """Filename stem for the pod round-trip: <stem>.pdf out, <stem>.md back."""
    return f"{_safe(source_id)}__{_safe(natural_key)}"


def evaluate_result(md: str, expected_pages: int | None) -> tuple[str, int]:
    """Classify a pod result: ('ok'|'page_mismatch'|'thin', pages_found)."""
    found = md.count(PAGE_MARK)
    if expected_pages and found != expected_pages:
        return "page_mismatch", found
    if content_bytes(md) < MIN_OCR_BYTES:
        return "thin", found
    return "ok", found


def content_bytes(md: str) -> int:
    """Text volume excluding the worker's page markers and separators."""
    lines = [
        line for line in md.splitlines()
        if not line.startswith(PAGE_MARK) and line.strip() != "---"
    ]
    return len("\n".join(lines).strip().encode("utf-8"))


def select_queue(cur, source: str | None = None) -> list[dict]:
    sql = """
        SELECT q.id AS queue_id, q.urn, q.payload AS queue_payload,
               i.source_id, i.natural_key, i.payload AS item_payload,
               i.discovered_via_partition_leaf AS leaf,
               b.url AS blob_url, b.storage_ref, b.bytes
        FROM deferred_queue q
        JOIN items i ON i.urn = q.urn
        JOIN blobs b ON b.urn = q.urn AND b.kept
        WHERE q.reason = %s AND q.resolved_at IS NULL
    """
    params: list = [REASON]
    if source:
        sql += " AND i.source_id = %s"
        params.append(source)
    sql += " ORDER BY i.source_id, i.natural_key"
    cur.execute(sql, params)
    return cur.fetchall()


def export(export_dir: Path, source: str | None = None) -> dict:
    """Symlink queued blobs into export_dir as <stem>.pdf + write manifest.json.

    Symlinks, not copies: the payload ships with `rsync -L`, so the local disk
    never holds the queue twice. Titles are resolved here (DB + source manifest
    in hand) so ingest needs nothing beyond the directory that comes back.
    """
    with db.tx() as cur:
        rows = select_queue(cur, source)

    export_dir.mkdir(parents=True, exist_ok=True)
    title_fields: dict[str, str] = {}
    entries: list[dict] = []
    missing_blob: list[str] = []
    seen: set[str] = set()

    for r in rows:
        blob = blobstore.from_ref(r["storage_ref"])
        if not blob.exists():
            missing_blob.append(r["urn"])
            continue
        s = stem(r["source_id"], r["natural_key"])
        if s in seen:
            raise ValueError(f"stem collision after sanitization: {s}")
        seen.add(s)

        if r["source_id"] not in title_fields:
            m = mf.load_validated(f"manifests/{r['source_id']}.yaml")
            title_fields[r["source_id"]] = mf.get(
                m, "extraction.title_field", "document.titel"
            )
        title = mf.get(r["item_payload"] or {}, title_fields[r["source_id"]])

        link = export_dir / f"{s}.pdf"
        link.unlink(missing_ok=True)
        link.symlink_to(blob.resolve())
        entries.append({
            "stem": s,
            "urn": r["urn"],
            "queue_id": r["queue_id"],
            "source_id": r["source_id"],
            "natural_key": r["natural_key"],
            "leaf": r["leaf"],
            "title": title or r["natural_key"],
            "pages": (r["queue_payload"] or {}).get("pages"),
            "bytes": r["bytes"],
            "blob_url": r["blob_url"],
            "storage_ref": r["storage_ref"],
        })

    (export_dir / "manifest.json").write_text(
        json.dumps({"reason": REASON, "entries": entries}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return {
        "exported": len(entries),
        "pages": sum(e["pages"] or 0 for e in entries),
        "bytes": sum(e["bytes"] or 0 for e in entries),
        "missing_blob": missing_blob,
    }


def ingest(results_dir: Path, source: str | None = None) -> dict[str, tuple[int, dict]]:
    """Apply pod OCR results as upgrades; one run per source, one tx per doc."""
    data = json.loads((results_dir / "manifest.json").read_text(encoding="utf-8"))
    assert data["reason"] == REASON
    by_source: dict[str, list[dict]] = {}
    for e in data["entries"]:
        if source and e["source_id"] != source:
            continue
        by_source.setdefault(e["source_id"], []).append(e)

    reports: dict[str, tuple[int, dict]] = {}
    for source_id, group in sorted(by_source.items()):
        m = mf.load_validated(f"manifests/{source_id}.yaml")
        run_id = runs.start(source_id, mf.store(m), "upgrade", phases=["drain"])
        counts = {"upgraded": 0, "thin_text_blob_kept": 0,
                  "missing_result": 0, "page_mismatch": 0}
        pages_done = 0
        leaves: dict[str, dict] = {}

        for e in group:
            md_file = results_dir / f"{e['stem']}.md"
            if not md_file.exists():
                counts["missing_result"] += 1
                continue
            md = md_file.read_text(encoding="utf-8")
            verdict, found = evaluate_result(md, e["pages"])
            if verdict == "page_mismatch":
                counts["page_mismatch"] += 1
                continue

            _apply_upgrade(e, md, found, thin=(verdict == "thin"))
            pages_done += found
            counts["upgraded" if verdict == "ok" else "thin_text_blob_kept"] += 1
            leaf = leaves.setdefault(e["leaf"] or "?", {"claimed": 0, "harvested": 0})
            leaf["claimed"] += 1
            leaf["harvested"] += 1

        resolved = counts["upgraded"] + counts["thin_text_blob_kept"]
        rec = Reconciliation(
            claimed_total_first=resolved, claimed_total_last=resolved,
            harvested_total=resolved,
            sum_of_leaves=sum(v["harvested"] for v in leaves.values()),
            scope="full",
        )
        skipped = counts["missing_result"] + counts["page_mismatch"]
        if skipped:
            rec.warnings.append(
                f"{skipped}/{len(group)} queue items left queued "
                f"(missing_result={counts['missing_result']}, "
                f"page_mismatch={counts['page_mismatch']})"
            )
        runs.record_partitions(run_id, leaves)
        eur = round(pages_done * EUR_PER_1K_PAGES / 1000, 4)
        with db.tx() as cur:
            cur.execute(
                "INSERT INTO cost_ledger (run_id, source_id, category, amount, eur, note) "
                "VALUES (%s, %s, 'ocr_pages', %s, %s, %s)",
                (run_id, source_id, pages_done, eur,
                 f"burst-pod drain estimate at ${EUR_PER_1K_PAGES}/1k pages"),
            )
        reports[source_id] = (run_id, runs.finish(
            run_id, source_id, rec,
            outcomes=counts,
            cost={"ocr_pages": pages_done, "eur_estimate": eur},
            selector_health={"queued": len(group)},
        ))
    return reports


def _apply_upgrade(e: dict, md: str, pages_found: int, *, thin: bool) -> None:
    now = datetime.now(timezone.utc)
    urn = e["urn"]
    text_file = blobstore.text_path(e["source_id"], e["leaf"], e["natural_key"])
    text_file.write_text(f"# {e['title']}\n\n{md}", encoding="utf-8")

    with db.tx() as cur:
        cur.execute("DELETE FROM extractions WHERE urn = %s AND method = 'native'", (urn,))
        db.upsert(
            cur, "extractions",
            {
                "urn": urn, "method": "ocr_full", "engine": OCR_ENGINE,
                "engine_version": OCR_ENGINE_VERSION, "confidence": None,
                "pages_processed": pages_found,
                "text_ref": blobstore.ref(text_file),
            },
            conflict="urn, method, engine",
        )
        if thin:
            # Distillate is suspiciously small: keep the blob for a human look,
            # but the OCR itself is done — nothing further is queued.
            reason = (f"ocr_tail drained: <{MIN_OCR_BYTES}B text from "
                      f"{pages_found} pages; blob kept for audit")
        else:
            blobstore.from_ref(e["storage_ref"]).unlink(missing_ok=True)
            cur.execute(
                "UPDATE blobs SET kept = false, kept_reason = 'deleted_after_distill', "
                "storage_ref = NULL WHERE urn = %s AND url = %s",
                (urn, e["blob_url"]),
            )
            reason = None
        cur.execute(
            "UPDATE items SET stage = 'extracted', disposition = 'ok', missing = '{}', "
            "upgradable = false, reason = %s, retryable = NULL, "
            "processed_at = %s, updated_at = %s WHERE urn = %s",
            (reason, now, now, urn),
        )
        cur.execute(
            "UPDATE deferred_queue SET resolved_at = %s WHERE id = %s",
            (now, e["queue_id"]),
        )
