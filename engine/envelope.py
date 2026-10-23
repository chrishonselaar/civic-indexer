"""The item envelope as it flows between stages (design/item-envelope.md §3).

Stage A carries the DocumentItem core; the Postgres `items` row is the durable form.
"""

from dataclasses import dataclass, field
from typing import Any

STAGE_RANK = {"discovered": 1, "fetched": 2, "extracted": 3, "enriched": 4, "indexed": 5}


@dataclass
class DiscoveredItem:
    """What a discovery strategy yields per source record."""

    natural_key: str
    urn: str
    partition_leaf: str          # e.g. "2026-06"; "?" when the source gives no date
    disposition: str             # ok | stub (federated metadata-only records)
    reason: str | None
    title: str
    publisher: str
    published: str | None        # YYYY-MM-DD (source-claimed; trusted_published derivation later)
    landing_url: str | None
    file_url: str | None
    pages: int | None            # aantalPaginas — OCR cost knowable pre-download
    file_type: str | None
    payload: dict[str, Any] = field(default_factory=dict)  # raw source record
    leaf_position: int | None = None                       # position within the walk
    # Extra (url, role) rows beyond landing/file — multi-provenance is preserved, never
    # collapsed (item-envelope.md §1 bet 2). Used by aggregator routes to keep the
    # provider's own URL beside the aggregator's. Roles must NOT be 'file': the fetch
    # candidate query joins on role='file' and a second such row would double-fetch.
    extra_urls: list[tuple[str, str]] = field(default_factory=list)
