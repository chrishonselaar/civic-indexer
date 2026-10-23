"""Fetch + extract phases: per-item pipeline over ledger candidates.

Candidates come from the ledger, never from file existence (decision-inventory.md
§7/§12). Every candidate ends in a recorded terminal state — fetched+extracted,
deferred(with reason), or failed(retryable flag) — so the run's internal
accounting closes; the run report verdict checks exactly that.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import requests

from engine import blobstore, config, db, manifest as mf, runs
from engine.budget import Budget, BudgetStop
from engine.extract import native_pdf, xml_text
from engine.fetch import fetch_file
from engine.reconcile import Reconciliation

MIN_TEXT_BYTES = 200  # below this, native text is a stub, not a distillate
PAYLOAD_ENGINE = "sru-abstract"          # extract-from-payload (fetch.transport: none)
PAYLOAD_ENGINE_VERSION = "1"


def _bump(lock: threading.Lock, outcomes: dict, key: str, nested: str | None = None) -> None:
    """Thread-safe increment of the shared outcomes counters (dict += is not atomic
    under the GIL). Only these tiny mutations are locked; the network fetch and each
    item's independent DB transaction run outside it."""
    with lock:
        if nested is None:
            outcomes[key] += 1
        else:
            outcomes[key][nested] = outcomes[key].get(nested, 0) + 1


def select_candidates(
    cur, source_id: str, window: str | None, limit: int | None,
    *, mode: str = "backfill", upgrade_reason: str = "fulltext",
) -> list[dict]:
    """Candidates for a fetch/extract pass, from the ledger — never file existence.

    Two disjoint cohorts, chosen by `mode`:

    - **backfill/incremental** — items the pipeline has not carried yet: freshly
      discovered, or a retryable failure to re-attempt.
    - **upgrade** — the *deferred* cohort: items that already reached a terminal
      state carrying `missing: [<reason>]` and `upgradable`, whose better version
      a later budgeted pass can still fetch (item-envelope.md §partial). These are
      `extracted`/`indexed`, so the backfill predicate can never see them — which
      is why a deferred upgrade needs its own selection rather than a stage reset.
      Resetting the stage would also be a lie in the ledger: the item *is* indexed.
    """
    if mode == "upgrade":
        sql = """
            SELECT i.urn, i.natural_key, i.payload,
                   i.discovered_via_partition_leaf AS leaf,
                   i.disposition, u.url AS file_url
            FROM items i
            JOIN item_urls u ON u.urn = i.urn AND u.role = 'file'
            WHERE i.source_id = %s
              AND i.upgradable
              AND %s = ANY(i.missing)
              AND i.disposition IN ('ok', 'partial')
        """
        params: list[Any] = [source_id, upgrade_reason]
    else:
        sql = """
            SELECT i.urn, i.natural_key, i.payload,
                   i.discovered_via_partition_leaf AS leaf,
                   i.disposition, u.url AS file_url
            FROM items i
            JOIN item_urls u ON u.urn = i.urn AND u.role = 'file'
            WHERE i.source_id = %s
              AND (i.stage = 'discovered' AND i.disposition = 'ok'
                   OR i.disposition = 'failed' AND i.retryable)
        """
        params = [source_id]
    if window:
        sql += " AND i.discovered_via_partition_leaf = %s"
        params.append(window)
    sql += " ORDER BY i.trusted_published DESC NULLS LAST"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    cur.execute(sql, params)
    return cur.fetchall()


def apply_upgrade_tier(m: dict) -> dict:
    """Overlay `fetch.upgrade` onto the manifest for an upgrade run.

    A source can have two extraction tiers: an ACTIVE one that every ordinary run
    uses, and a DEFERRED one that costs more and only a budgeted upgrade pass takes.
    tuchtrecht is the worked example — SRU discovery carries an abstract inline
    (`fetch.transport: none`), while the real body text sits one FRBR fetch away on
    a ~1 rps host. Declaring the deferred tier as config rather than prose means the
    upgrade is reproducible and the active tier is not disturbed by taking it: a
    normal run of this manifest still costs nothing.

    Returns a shallow-copied overlay; the caller's manifest is not mutated.
    """
    upgrade = mf.get(m, "fetch.upgrade") or {}
    if not upgrade:
        return m
    out = {**m, "fetch": {**m.get("fetch", {})}, "extraction": {**m.get("extraction", {})}}
    if "transport" in upgrade:
        out["fetch"]["transport"] = upgrade["transport"]
    if "ladder" in upgrade:
        out["extraction"]["ladder"] = upgrade["ladder"]
    return out


def run_fetch_extract(
    m: dict,
    *,
    mode: str = "backfill",
    window: str | None = None,
    limit: int | None = None,
    budget_eur: float | None = None,
) -> tuple[int, dict[str, Any]]:
    if mode == "upgrade":
        m = apply_upgrade_tier(m)
    source_id = mf.get(m, "source.id")
    manifest_version = mf.store(m)
    politeness = m.get("politeness", {})
    fetch_cfg = m.get("fetch", {})
    # Fetch-host politeness may differ from the discovery host: the fetch surface is
    # often a static/bulk file repository (e.g. KOOP's FRBR repository) built for
    # harvesting, whereas discovery hits a rate-sensitive search app. A per-source
    # `fetch.politeness` block overrides the top-level politeness for this phase only,
    # so the discovery-probed ceiling is never silently applied to a bulk file host.
    fetch_pol = {**politeness, **fetch_cfg.get("politeness", {})}
    concurrency = max(1, int(fetch_pol.get("concurrency", 1)))
    max_bytes = fetch_cfg.get("max_bytes", 500 * 1024 * 1024)
    min_size = mf.get(m, "fetch.validation.min_size_bytes", 1024)
    conf_threshold = mf.get(m, "extraction.native_confidence_threshold", 0.5)
    eur_per_gb = mf.get(m, "budgets.eur_per_gb_downloaded", 0.0)
    delay = fetch_pol.get("delay_seconds", 0.25)

    run_id = runs.start(source_id, manifest_version, mode, phases=["fetch", "extract"])
    cap = budget_eur if budget_eur is not None else mf.get(m, "budgets.eur_per_run_cap")
    budget = Budget(run_id, source_id, cap)

    with db.tx() as cur:
        candidates = select_candidates(cur, source_id, window, limit, mode=mode)

    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})

    outcomes = {
        "fetched": 0, "extracted_ok": 0, "extracted_partial": 0,
        "deferred": {}, "failed_retryable": 0, "failed": 0,
    }
    leaves: dict[str, dict[str, Any]] = {}
    accounted = 0
    budget_stopped = False
    # Guards the shared in-memory accounting (outcomes/budget) so _process_one is safe
    # under the ThreadPoolExecutor; the slow network fetch stays outside the lock, and
    # each item's DB work is an independent short transaction (fresh connection).
    lock = threading.Lock()

    def _run_one(cand: dict) -> None:
        _process_one(
            cand, m, session, budget,
            source_id=source_id, run_id=run_id,
            max_bytes=max_bytes, min_size=min_size,
            conf_threshold=conf_threshold, eur_per_gb=eur_per_gb,
            outcomes=outcomes, lock=lock, mode=mode,
        )
        if delay:
            time.sleep(delay)

    transport = mf.get(m, "fetch.transport", "http")
    if transport == "none":
        # Extract-from-payload: discovery already carries the distillate inline (e.g.
        # sru_records abstracts), so there is nothing to fetch. Full body text, where a
        # per-doc file exists, is a deferred upgrade — items are left upgradable with
        # missing=['fulltext'] so a later fetch pass can distil the real body.
        for cand in candidates:
            leaf = leaves.setdefault(
                cand["leaf"] or "?", {"claimed": 0, "harvested": 0, "cap_hit": False}
            )
            leaf["claimed"] += 1
            _extract_from_payload(cand, m, source_id=source_id, outcomes=outcomes, lock=lock)
            accounted += 1
            leaf["harvested"] += 1
    elif concurrency == 1:
        for cand in candidates:
            leaf = leaves.setdefault(
                cand["leaf"] or "?", {"claimed": 0, "harvested": 0, "cap_hit": False}
            )
            leaf["claimed"] += 1
            try:
                _run_one(cand)
            except BudgetStop:
                budget_stopped = True
                break
            accounted += 1
            leaf["harvested"] += 1
    else:
        # Claimed counts are known up-front (one per candidate); harvested is tallied
        # as futures complete on this (single) thread, so leaves/accounted need no lock.
        for cand in candidates:
            leaves.setdefault(
                cand["leaf"] or "?", {"claimed": 0, "harvested": 0, "cap_hit": False}
            )["claimed"] += 1
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_run_one, c): c for c in candidates}
            for fut in as_completed(futures):
                cand = futures[fut]
                try:
                    fut.result()
                except BudgetStop:
                    budget_stopped = True
                    continue
                accounted += 1
                leaves[cand["leaf"] or "?"]["harvested"] += 1

    rec = Reconciliation(
        claimed_total_first=len(candidates),
        claimed_total_last=len(candidates),
        harvested_total=accounted,
        sum_of_leaves=sum(v["harvested"] for v in leaves.values()),
        scope="full",  # internal accounting must close exactly, window or not
        all_partitions_empty=(len(candidates) > 0 and accounted == 0 and not budget_stopped),
    )
    if budget_stopped:
        rec.warnings.append(
            f"BUDGET-STOP after {accounted}/{len(candidates)} items; remainder stays queued"
        )
        # Deliberate stop, not missing data: exclude unprocessed from the claim.
        rec.claimed_total_first = rec.claimed_total_last = accounted
        for leaf in leaves.values():
            leaf["claimed"] = leaf["harvested"]

    runs.record_partitions(run_id, leaves)
    report = runs.finish(
        run_id, source_id, rec,
        outcomes=outcomes,
        cost=budget.flush(),
        selector_health={"candidates": len(candidates)},
    )
    return run_id, report


def _process_one(
    cand: dict, m: dict, session: requests.Session, budget: Budget,
    *, source_id: str, run_id: int, max_bytes: int, min_size: int,
    conf_threshold: float, eur_per_gb: float, outcomes: dict,
    lock: threading.Lock, mode: str = "backfill",
) -> None:
    urn, key = cand["urn"], cand["natural_key"]
    now = datetime.now(timezone.utc)
    dest = blobstore.blob_path(source_id, key)
    politeness = m.get("politeness", {})
    retry = politeness.get("retry", {})

    result = fetch_file(
        session, cand["file_url"], dest,
        max_bytes=max_bytes, min_size_bytes=min_size,
        timeout=politeness.get("timeout_seconds", 300),
        retry_attempts=retry.get("attempts", 3),
        retry_backoff=retry.get("backoff", 1.5),
    )
    budget.add("fetch_requests", 1)
    budget.add("bytes_downloaded", result.bytes, eur=(result.bytes / 1e9) * eur_per_gb)

    with db.tx() as cur:
        db.upsert(
            cur, "blobs",
            {
                "urn": urn, "url": cand["file_url"],
                "sniffed_type": result.sniffed_type, "bytes": result.bytes,
                "validated_magic": result.validated_magic,
                "validated_eof": result.validated_eof,
                "validated_min_size": result.validated_min_size,
                "content_hash": result.content_hash,
                "kept": False, "kept_reason": None, "storage_ref": None,
            },
            conflict="urn, url",
        )

        if not result.ok:
            if mode == "upgrade":
                _upgrade_noop(cur, urn, f"upgrade fetch failed: {result.error}")
            else:
                _set_status(
                    cur, urn, stage="discovered",
                    disposition="failed", reason=result.error,
                    retryable=result.retryable,
                )
            _bump(lock, outcomes, "failed_retryable" if result.retryable else "failed")
            return

        cur.execute(
            "UPDATE items SET stage = 'fetched', content_hash = %s, fetched_at = %s, "
            "updated_at = %s WHERE urn = %s",
            (result.content_hash, now, now, urn),
        )
        _bump(lock, outcomes, "fetched")

        if result.sniffed_type == "zip":
            # ZIP dossier: container kept, member extraction deferred (format
            # router beyond pdf/native is post-Stage-A).
            _keep_blob(cur, urn, cand["file_url"], dest, "zip_source")
            _defer(cur, urn, "zip_dossier")
            _set_status(cur, urn, stage="fetched", disposition="deferred",
                        reason="zip dossier: member extraction deferred", upgradable=True)
            _bump(lock, outcomes, "deferred", "zip_dossier")
            return

        ladder = mf.get(m, "extraction.ladder", ["native_pdf_text"])

        if result.sniffed_type == "xml" and "xml_text" in ladder:
            # Born-digital XML: no OCR tail exists for this format.
            try:
                xml_extraction = xml_text.extract(dest)
            except Exception as e:
                if mode == "upgrade":
                    _upgrade_noop(cur, urn, f"upgrade xml extraction error: {e}")
                else:
                    _set_status(cur, urn, stage="fetched", disposition="failed",
                                reason=f"xml extraction error: {e}", retryable=False)
                _bump(lock, outcomes, "failed")
                return
            if mode == "upgrade" and xml_extraction.confidence <= 0:
                # Bail BEFORE the write. An upgrade overwrites the shared text file and
                # supersedes the old extraction row, so falling through here would trade
                # a real abstract for an empty body.
                dest.unlink(missing_ok=True)
                _upgrade_exhausted(
                    cur, urn, "upgrade found no body text in the source XML"
                )
                _bump(lock, outcomes, "failed")
                return
            title_path = mf.get(m, "extraction.title_field", "document.titel")
            title = mf.get(cand["payload"] or {}, title_path) or key
            text_file = blobstore.text_path(source_id, cand["leaf"], key)
            text_file.write_text(
                native_pdf.to_markdown(title, xml_extraction.text), encoding="utf-8"
            )
            db.upsert(
                cur, "extractions",
                {
                    "urn": urn, "method": "native", "engine": xml_text.ENGINE,
                    "engine_version": xml_text.ENGINE_VERSION,
                    "confidence": xml_extraction.confidence,
                    "pages_processed": xml_extraction.pages_processed,
                    "text_ref": blobstore.ref(text_file),
                },
                conflict="urn, method, engine",
            )
            _supersede_extractions(cur, urn, "native", xml_text.ENGINE)
            if xml_extraction.confidence > 0:
                dest.unlink(missing_ok=True)
                cur.execute(
                    "UPDATE blobs SET kept = false, kept_reason = 'deleted_after_distill', "
                    "storage_ref = NULL WHERE urn = %s AND url = %s",
                    (urn, cand["file_url"]),
                )
                _set_status(cur, urn, stage="extracted", disposition="ok",
                            missing=[], upgradable=False)
                _bump(lock, outcomes, "extracted_ok")
            else:
                _set_status(cur, urn, stage="extracted", disposition="failed",
                            reason="xml contained no body text", retryable=False)
                _bump(lock, outcomes, "failed")
            cur.execute(
                "UPDATE items SET processed_at = %s, updated_at = %s WHERE urn = %s",
                (now, now, urn),
            )
            return

        if result.sniffed_type != "pdf":
            dest.unlink(missing_ok=True)
            _set_status(
                cur, urn, stage="fetched", disposition="failed",
                reason=f"non-PDF payload ({result.sniffed_type})", retryable=False,
            )
            _bump(lock, outcomes, "failed")
            return

        # ---- native extraction (free tier) ----
        try:
            extraction = native_pdf.extract(dest)
        except Exception as e:
            _keep_blob(cur, urn, cand["file_url"], dest, "partial_ocr")
            _defer(cur, urn, "ocr_tail")
            _set_status(cur, urn, stage="fetched", disposition="partial",
                        missing=["fulltext", "ocr_tail"], upgradable=True,
                        reason=f"native extraction error: {e}")
            _bump(lock, outcomes, "deferred", "ocr_tail")
            return

        title_path = mf.get(m, "extraction.title_field", "document.titel")
        title = mf.get(cand["payload"] or {}, title_path) or key
        text_file = blobstore.text_path(source_id, cand["leaf"], key)
        text_file.write_text(
            native_pdf.to_markdown(title, extraction.text), encoding="utf-8"
        )
        db.upsert(
            cur, "extractions",
            {
                "urn": urn, "method": "native", "engine": native_pdf.ENGINE,
                "engine_version": native_pdf.ENGINE_VERSION,
                "confidence": round(extraction.confidence, 4),
                "pages_processed": extraction.pages_processed,
                "text_ref": blobstore.ref(text_file),
            },
            conflict="urn, method, engine",
        )
        _supersede_extractions(cur, urn, "native", native_pdf.ENGINE)
        cur.execute(
            "UPDATE blobs SET pages = %s WHERE urn = %s AND url = %s",
            (extraction.total_pages, urn, cand["file_url"]),
        )

        native_ok = (
            extraction.confidence >= conf_threshold
            and len(extraction.text) >= MIN_TEXT_BYTES
        )
        if native_ok:
            dest.unlink(missing_ok=True)  # delete-after-distill (default retention)
            cur.execute(
                "UPDATE blobs SET kept = false, kept_reason = 'deleted_after_distill', "
                "storage_ref = NULL WHERE urn = %s AND url = %s",
                (urn, cand["file_url"]),
            )
            _set_status(cur, urn, stage="extracted", disposition="ok", missing=[], upgradable=False)
            _bump(lock, outcomes, "extracted_ok")
        else:
            # Scanned/low-text: keep the blob for the deferred OCR burst.
            _keep_blob(cur, urn, cand["file_url"], dest, "partial_ocr")
            _defer(cur, urn, "ocr_tail",
                   payload={"pages": extraction.total_pages,
                            "native_confidence": round(extraction.confidence, 4)})
            _set_status(cur, urn, stage="extracted", disposition="partial",
                        missing=["ocr_tail"], upgradable=True,
                        reason=f"native confidence {extraction.confidence:.2f} "
                               f"< {conf_threshold} (likely scanned)")
            _bump(lock, outcomes, "extracted_partial")
        cur.execute(
            "UPDATE items SET processed_at = %s, updated_at = %s WHERE urn = %s",
            (now, now, urn),
        )


def _extract_from_payload(
    cand: dict, m: dict, *, source_id: str, outcomes: dict, lock: threading.Lock,
) -> None:
    """Distil the searchable text directly from the discovery payload — no fetch.

    Used when fetch.transport == 'none' (e.g. an SRU bulk harvest whose records carry
    a title + abstract inline, or an aggregator route whose records carry the full page
    text). Writes a native-style text distillate and an extraction row, then advances the
    item to 'extracted'. If a per-document file URL exists AND the payload carried no page
    text, the item stays upgradable with missing=['fulltext'] so the full body text can be
    backfilled later via a normal fetch pass.

    The extraction row is manifest-declared via `extraction.payload_extraction`; the
    defaults reproduce the original SRU behaviour exactly."""
    urn, key = cand["urn"], cand["natural_key"]
    now = datetime.now(timezone.utc)
    payload = cand["payload"] or {}
    title = mf.get(payload, mf.get(m, "extraction.title_field", "title")) or key
    parts = [
        str(v)
        for f in mf.get(m, "extraction.payload_text_fields", [])
        if (v := mf.get(payload, f))
    ]
    body = "\n\n".join(parts)
    text_file = blobstore.text_path(source_id, cand["leaf"], key)
    text_file.write_text(native_pdf.to_markdown(title, body), encoding="utf-8")

    pe = mf.get(m, "extraction.payload_extraction") or {}
    method = pe.get("method", "sru")
    engine = pe.get("engine", PAYLOAD_ENGINE)
    ver_field = pe.get("engine_version_field")
    engine_version = (
        (mf.get(payload, ver_field) if ver_field else None)
        or pe.get("engine_version")
        or PAYLOAD_ENGINE_VERSION
    )
    pages_field = pe.get("pages_field")
    pages_processed = int(mf.get(payload, pages_field) or 0) if pages_field else 0
    confidence = pe.get("confidence", 1.0)
    if method == "aggregator":
        # Second-hand text: someone else's OCR of a scan, of unmeasured quality. 1.0 is
        # reserved for a source's OWN authored text (design/aggregator-registry.md §9b).
        # Enforced here rather than trusted to the manifest — it must not be editable away.
        confidence = None

    # Page text present => the body is NOT deferred, whatever file URL exists.
    has_file = bool(cand.get("file_url")) and pages_processed == 0
    with lock, db.tx() as cur:
        db.upsert(
            cur, "extractions",
            {
                "urn": urn, "method": method, "engine": engine,
                "engine_version": engine_version,
                "confidence": confidence, "pages_processed": pages_processed,
                "text_ref": blobstore.ref(text_file),
            },
            conflict="urn, method, engine",
        )
        empty_reason = (
            "sru abstract empty; title/metadata only" if method == "sru"
            else f"{method} payload carried no text; title/metadata only"
        )
        _set_status(
            cur, urn, stage="extracted", disposition="ok",
            missing=["fulltext"] if has_file else [], upgradable=has_file,
            reason=None if body.strip() else empty_reason,
        )
        cur.execute(
            "UPDATE items SET processed_at = %s, updated_at = %s WHERE urn = %s",
            (now, now, urn),
        )
    _bump(lock, outcomes, "extracted_ok")


def _set_status(
    cur, urn: str, *, stage: str, disposition: str, reason: str | None = None,
    retryable: bool | None = None, missing: list[str] | None = None,
    upgradable: bool | None = None,
) -> None:
    """Write an item's status. **Omitting `missing`/`upgradable` PRESERVES them.**

    That default was inverted on 2026-07-29, after it silently destroyed 92 items.

    It used to write all six columns every call, defaulting `missing` to `[]` and
    `upgradable` to `False` — so a caller who only wanted to say "this attempt failed"
    also, invisibly, *retracted the item's claim that a better version exists*. Those
    two columns are the entire deferred-upgrade cohort
    (`select_candidates(mode="upgrade")`), so the items became unreachable by the only
    pass that could have retried them. Nothing failed, nothing logged, and the run
    reported `reconciled ✓` because reconciliation counts claimed-vs-harvested and
    never reads these columns.

    The fix is the direction of the default, not a rule for callers to remember. Now
    forgetting **preserves**, so the worst outcome of an oversight is a stale mark —
    which costs one wasted retry, is visible in `civic check-ledger`, and is repairable.
    Callers that genuinely mean "there is nothing left to upgrade" say so:
    `missing=[], upgradable=False`. A destructive write is now something you type.
    """
    sets = ["stage = %s", "disposition = %s", "reason = %s", "retryable = %s"]
    params: list[Any] = [stage, disposition, reason, retryable]
    if missing is not None:
        sets.append("missing = %s")
        params.append(missing)
    if upgradable is not None:
        sets.append("upgradable = %s")
        params.append(upgradable)
    params.append(urn)
    cur.execute(
        f"UPDATE items SET {', '.join(sets)}, updated_at = now() WHERE urn = %s",
        params,
    )


def _upgrade_noop(cur, urn: str, reason: str) -> None:
    """Record a failed upgrade WITHOUT changing anything the ledger claims.

    An upgrade that fails is a no-op, not a regression: the item still holds the text
    it already had and is still indexed. The ordinary failure path would rewind it to
    discovered/failed — and _set_status also clears `missing`/`upgradable`, which is
    the only record that a better version is still out there. That silently evicts the
    item from the upgrade cohort, so the next pass cannot even retry it. Measured: the
    2025 tuchtrecht backfill lost 92 items exactly this way before this existed.
    """
    cur.execute(
        "UPDATE items SET reason = %s, updated_at = now() WHERE urn = %s", (reason, urn)
    )


def _upgrade_exhausted(cur, urn: str, reason: str) -> None:
    """The deferred upgrade turned out not to exist. Stop offering it, keep the item.

    Distinct from _upgrade_noop: there is nothing to retry. The marks come off so the
    cohort shrinks, but stage and disposition are untouched — the item is still indexed
    with the text it had, and marking it `failed` would drop an already-published
    document out of the projection (_select_projectable takes only ok/partial).
    """
    cur.execute(
        "UPDATE items SET upgradable = false, missing = '{}', reason = %s, "
        "updated_at = now() WHERE urn = %s",
        (reason, urn),
    )


def _supersede_extractions(cur, urn: str, method: str, engine: str) -> int:
    """Drop every OTHER extraction row for this URN after a real re-extraction.

    All extractors for one item write the SAME `blobstore.text_path(...)` file, but
    `extractions` is keyed `(urn, method, engine)` — so a deferred upgrade that
    re-extracts by a different route (payload abstract -> fetched FRBR XML) UPSERTs a
    second row beside the first instead of replacing it. Both rows then point at one
    file, and only one of them produced its contents. That is the same contract
    drain.py states for the OCR tail ("a surviving row would point at text it did not
    produce"), and it has to hold here too: `projector._select_projectable` joins
    `extractions` with no dedup, so a duplicate row projects the URN twice.

    Called only where the shared text file is actually overwritten. Returns the count
    so a caller can log it; today only the upgrade path ever finds anything to drop.
    """
    cur.execute(
        "DELETE FROM extractions WHERE urn = %s AND NOT (method = %s AND engine = %s)",
        (urn, method, engine),
    )
    return cur.rowcount


def _keep_blob(cur, urn: str, url: str, path, kept_reason: str) -> None:
    cur.execute(
        "UPDATE blobs SET kept = true, kept_reason = %s, storage_ref = %s "
        "WHERE urn = %s AND url = %s",
        (kept_reason, blobstore.ref(path), urn, url),
    )


def _defer(cur, urn: str, reason: str, payload: dict | None = None) -> None:
    cur.execute(
        "INSERT INTO deferred_queue (urn, reason, payload) VALUES (%s, %s, %s) "
        "ON CONFLICT (urn, reason) DO UPDATE SET payload = EXCLUDED.payload, "
        "resolved_at = NULL",
        (urn, reason, db.jsonb(payload or {})),
    )
