"""Adopt text a PRIOR project already extracted, instead of re-fetching it.

`wob-search-mcp` (2025-2026, `~/claude-projects/wob-search-mcp`) is one of the four
documented prior indexers. It downloaded and distilled **12,335** open.overheid
documents — 10,651 by native PDF text, 1,847 through Azure Document Intelligence OCR
that somebody already paid for. Our `woo_besluiten` ledger holds **3,977 of those same
documents as stubs**: discovered, never fetched, no text. Re-acquiring them means
~3,900 requests at the measured ~1 rps ceiling plus OCR spend, to end up with text
sitting on this disk already.

So this adopts it. Read-only on the prior project: nothing here writes to
`wob-search-mcp`, and the text is **copied into our own blobstore** rather than
referenced in place, because `blobstore.ref` is relative to `DATA_DIR` and the ledger
has to stay relocatable.

## What makes the join safe

The prior ledger keys on `doc_uuid`, parsed from `pdf_url`
(`open.overheid.nl/documenten/<uuid>/file`). Our `natural_key` is `document.id` from the
same API. They differ in exactly one way: the API's `document.id` sometimes carries a
`_N` suffix, and `identity.natural_key_pattern` is a bare-UUID regex, so those records
were rejected into `disposition: stub` (see `WOO_IDX_IS_NOT_THIS` below for the *other*
stub class, which is unrelated). Measured before writing anything:

- every base UUID in that cohort appears **exactly once** (0 bases with >1 suffix), and
- **no** base UUID collides with a bare-UUID item.

So `split_part(natural_key, '_', 1)` is a unique key on both sides and the join cannot
pair the wrong documents. That matters because the obvious alternative — matching on
title — was tried and **abandoned as unreliable**: Woo titles are boilerplate
("beslissing bezwaar besluit verzoek") and even a strict variant happily pairs
"5e deelbesluit" with "6e deelbesluit".

Title similarity is useless for *finding* matches, but it is a good **falsifier** for a
join already made — if the key were wrong, our payload title and the prior project's
filename slug would be unrelated wholesale. Run over 300 imported documents: Jaccard of
significant words p50 **1.00**, p10 **0.80**, and **0 of 300** below 0.15. The join is
confirmed by a signal it does not depend on.

## WOO_IDX_IS_NOT_THIS

`woo_besluiten` holds a second, larger stub class — 20,712 `woo-idx-*` keys — and they
are **not** in this join and cannot be. They are federated index records
(`aanbieder: Harvester`) whose file lives on the *publisher's own* host, not on
open.overheid: 20,102 on `open.rijkswaterstaat.nl`, 446 `media.gelderland.nl`, 164
`es-connector.zeeland.nl`, 12 on a CloudFront name that no longer resolves. The prior
project fetched only `open.overheid.nl/documenten/<uuid>/file`, so it has 0 rows whose
`pdf_url` contains `woo-idx` — verified, not assumed. Those 20,712 are a separate
access-posture question (four new hosts), filed for the admin, not resolvable here.

## Provenance is recorded honestly, including what is unknowable

- `method` is ours (`native` / `ocr_full`), `engine` names the **import**, not a local
  engine, so no audit can mistake this text for something this pipeline produced.
- The prior ledger records `extraction_method` as `native` / `ocr` and nothing finer. Its
  README names PyMuPDF+pdfplumber for native and Azure Document Intelligence for OCR with
  a Tesseract fallback — but **which** ran for a given document is not recorded anywhere
  (checked: not in the ledger, not in the per-document JSON, not in the markdown header).
  `engine_version` says so rather than picking the likely one.
- `confidence` and `pages_processed` are carried across; `content_hash` and `fetched_at`
  stay NULL, because we never held the bytes. An item that reads `extracted` with no
  `fetched_at` is telling the truth about how it got there.
- No `role='file'` URL is written. `process.py` selects fetch candidates by joining
  `item_urls` on that role, and inventing one would enrol adopted items in a fetch they
  do not need.

## Text that is imported, and text that is not

Statuses map to dispositions rather than all landing as `ok`:

| prior status | ours | why |
|---|---|---|
| `done_native`, `done_ocr` | `extracted`/`ok` | complete distillate |
| `done_ocr_partial`, `truncated` | `extracted`/`partial` + `missing: [ocr_tail]` | real text, known incomplete — stays in the drain cohort |
| `stub`, `skipped`, `unprocessed` | **not imported** | measured: all 107 with a file are under 200 chars (p50 137 and 102), i.e. a placeholder, not a distillate |

The `MIN_TEXT_BYTES` guard runs anyway, on the stripped body, so a status that lies
cannot smuggle an empty document in.

The prior project's markdown carries its own presentation layer — an `# <url-slug>`
heading and an optional `## Document Information` / `**Redactions detected:** N` block.
That is stripped: the slug is a filename, not a title, and indexing it would put
hyphenated URL fragments into the searchable text. Our own title comes from the payload
the API gave us, through the same `native_pdf.to_markdown` every other distillate uses.

One convention difference is **kept** rather than smoothed over: the prior project
inserts inline `[REDACTED: page 1, ~4 words, middle]` markers where it detected a
blackout, and `native_pdf.to_markdown` drops such markers on our own OCR tier. Measured
over a 500-document sample: 28% of adopted documents carry markers, p50 5 per document,
and they are **0.32% of all characters**. Deleting information the prior project paid to
detect, to buy a cosmetic consistency worth a third of a percent, is the worse trade —
so they stay, and this paragraph is why a grep for `REDACTED` finds hits in
`woo_besluiten` and not in the other three corpora.
"""

import csv
import re
import sqlite3
from pathlib import Path

from engine import blobstore, db
from engine.extract import native_pdf
from engine.process import MIN_TEXT_BYTES, _set_status, _supersede_extractions

WOB_ROOT = Path.home() / "claude-projects" / "wob-search-mcp"
WOB_LEDGER = WOB_ROOT / "data" / "recovery_ledger.db"
SOURCE_ID = "woo_besluiten"

ENGINE_NATIVE = "wob-search-mcp:pymupdf"
ENGINE_OCR = "wob-search-mcp:ocr"
# Deliberately not "azure-di": the prior ledger records used_ocr as a boolean and names
# no engine per document, so claiming Azure for a row Tesseract may have produced would
# be a guess wearing provenance's clothes.
VERSION_NATIVE = "imported from wob-search-mcp; pymupdf/pdfplumber, not distinguishable per document"
VERSION_OCR = "imported from wob-search-mcp; Azure Document Intelligence with Tesseract fallback, not distinguishable per document"

# prior status -> (our disposition, missing[], upgradable)
STATUS_MAP = {
    "done_native": ("ok", [], False),
    "done_ocr": ("ok", [], False),
    "done_ocr_partial": ("partial", ["ocr_tail"], True),
    "truncated": ("partial", ["ocr_tail"], True),
}

_H1 = re.compile(r"\A#[^\n]*\n")
_DOCINFO = re.compile(r"\A##\s*Document Information\s*\n+(\*\*[^\n]*\n+)*")
_REDACTIONS = re.compile(r"\*\*Redactions detected:\*\*\s*(\d+)")


def strip_prior_header(md: str) -> tuple[str, int | None]:
    """Drop the prior project's heading/info block. Returns (body, redaction_count)."""
    redactions = None
    if m := _REDACTIONS.search(md[:400]):
        redactions = int(m.group(1))
    body = _H1.sub("", md.lstrip(), count=1).lstrip()
    body = _DOCINFO.sub("", body, count=1)
    return body.strip(), redactions


def read_prior_ledger(path: Path = WOB_LEDGER) -> dict[str, dict]:
    """doc_uuid -> row, for rows carrying a real UUID. Read-only, own connection."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT doc_uuid, doc_role, status, extraction_method, used_ocr,"
            " is_truncated, page_count, processed_pages, text_confidence,"
            " md_path, md_exists, md_bytes FROM ledger"
            " WHERE doc_uuid IS NOT NULL AND length(doc_uuid) = 36"
        ).fetchall()
    finally:
        con.close()
    return {r["doc_uuid"]: dict(r) for r in rows}


def candidates(cur) -> list[dict]:
    """Our stub items whose base UUID could match, newest first for a useful --limit."""
    cur.execute(
        """
        SELECT urn, natural_key, split_part(natural_key, '_', 1) AS base,
               discovered_via_partition_leaf AS leaf, payload, disposition
        FROM items
        WHERE source_id = %s
          AND natural_key ~ '^[0-9a-f-]{36}(_[0-9]+)?$'
          AND disposition = 'stub'
          AND NOT EXISTS (SELECT 1 FROM extractions e WHERE e.urn = items.urn)
        ORDER BY trusted_published DESC NULLS LAST
        """,
        (SOURCE_ID,),
    )
    return cur.fetchall()


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def import_texts(
    *, dry_run: bool = True, limit: int | None = None, report_path: Path | None = None
) -> dict:
    """Adopt prior text for every matching stub. Returns a counted outcome dict."""
    prior = read_prior_ledger()
    stats: dict[str, int] = {}

    def bump(k: str) -> None:
        stats[k] = stats.get(k, 0) + 1

    written: list[dict] = []
    with db.tx() as cur:
        cands = candidates(cur)
        stats["stub_candidates"] = len(cands)
        stats["prior_rows"] = len(prior)
        for cand in cands:
            if limit is not None and stats.get("imported", 0) >= limit:
                bump("skipped_limit")
                continue
            row = prior.get(cand["base"])
            if row is None:
                bump("no_prior_row")
                continue
            mapped = STATUS_MAP.get(row["status"] or "")
            if mapped is None:
                bump(f"rejected_status:{row['status']}")
                continue
            md_path = Path(row["md_path"]) if row["md_path"] else None
            if not md_path or row["md_exists"] != "1" or not md_path.exists():
                bump("prior_file_missing")
                continue
            body, redactions = strip_prior_header(
                md_path.read_text(encoding="utf-8", errors="replace")
            )
            if len(body.encode("utf-8")) < MIN_TEXT_BYTES:
                bump("too_short_after_strip")
                continue

            disposition, missing, upgradable = mapped
            is_ocr = (row["extraction_method"] or "") == "ocr"
            method = "ocr_full" if is_ocr else "native"
            engine = ENGINE_OCR if is_ocr else ENGINE_NATIVE
            title = (
                ((cand["payload"] or {}).get("document") or {}).get("titel")
                or cand["natural_key"]
            )
            written.append({
                "urn": cand["urn"], "natural_key": cand["natural_key"],
                "prior_status": row["status"], "method": method, "engine": engine,
                "disposition": disposition, "chars": len(body),
                "redactions": redactions, "prior_md": str(md_path),
            })
            bump("imported")
            bump(f"as:{disposition}")
            bump(f"via:{method}")
            if dry_run:
                continue

            text_file = blobstore.text_path(SOURCE_ID, cand["leaf"], cand["natural_key"])
            text_file.write_text(native_pdf.to_markdown(title, body), encoding="utf-8")
            db.upsert(
                cur, "extractions",
                {
                    "urn": cand["urn"], "method": method, "engine": engine,
                    "engine_version": VERSION_OCR if is_ocr else VERSION_NATIVE,
                    "confidence": _float(row["text_confidence"]),
                    "pages_processed": _int(row["processed_pages"])
                    or _int(row["page_count"]),
                    "text_ref": blobstore.ref(text_file),
                },
                conflict="urn, method, engine",
            )
            _supersede_extractions(cur, cand["urn"], method, engine)
            _set_status(
                cur, cand["urn"], stage="extracted", disposition=disposition,
                reason=f"text adopted from wob-search-mcp ({row['status']})",
                retryable=None, missing=missing, upgradable=upgradable,
            )

    if report_path:
        with report_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(written[0].keys()) if written else
                               ["urn"], extrasaction="ignore")
            w.writeheader()
            w.writerows(written)
    return stats
