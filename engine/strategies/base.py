"""The strategy seam: discovery mechanics live behind this interface.

Stage B onboards new sources by adding strategies (or parametrizing existing
ones via manifests) — never by adding pipeline stages (design bet #3).
"""

from typing import Iterator, Protocol

from engine.envelope import DiscoveredItem


class DiscoveryStrategy(Protocol):
    """Walks a source's listing mechanism and yields DiscoveredItems.

    After the walk the engine reads the counters below to compute
    reconciliation — a strategy never computes its own verdict.
    """

    claimed_total_first: int | None
    claimed_total_last: int | None
    requests_made: int
    raw_results: int          # results seen before dedup/classification
    walk_duplicates: int      # same natural key seen twice mid-walk (page shift)
    unrecordable: int         # records with no usable identity (reported, never silent)
    sanity_cap_hit: bool
    facets: dict              # source-declared facet counts (compensating control)

    def discover(
        self, *, until_before: str | None = None, overlap_pages: int = 0
    ) -> Iterator[DiscoveredItem]: ...
