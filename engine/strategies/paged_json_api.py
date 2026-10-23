"""paged_json_api — record-offset paging over a public JSON search API.

Discovery loop ported from wob-search-mcp/scripts/scrape_open_overheid.py
(discover() + UUID/federated-stub classification), generalized to manifest
config: CSV/ES-dedup dropped (the ledger replaces both), claimed-total capture
and cutoff-overlap added.
"""

import re
import time
from typing import Any, Callable, Iterator

import requests

from engine import config
from engine.envelope import DiscoveredItem
from engine.manifest import get
from engine.partition import month_leaf

HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "application/json",
}

# http_get(url, params) -> parsed JSON dict; injectable for fixture replay.
HttpGet = Callable[[str, dict[str, Any]], dict]


class PagedJsonApiStrategy:
    def __init__(
        self,
        role: dict,
        identity: dict,
        politeness: dict,
        http_get: HttpGet | None = None,
    ):
        self.role = role
        self.identity = identity
        self.politeness = politeness
        self._http_get = http_get or self._real_http_get
        self._session: requests.Session | None = None

        self.claimed_total_first: int | None = None
        self.claimed_total_last: int | None = None
        self.requests_made = 0
        self.raw_results = 0
        self.walk_duplicates = 0
        self.unrecordable = 0
        self.sanity_cap_hit = False
        self.facets: dict = {}

        self._key_pattern = (
            re.compile(identity["natural_key_pattern"])
            if identity.get("natural_key_pattern")
            else None
        )

    # ------------------------------------------------------------------ http

    def _real_http_get(self, url: str, params: dict[str, Any]) -> dict:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        retry = self.politeness.get("retry", {})
        attempts = retry.get("attempts", 3)
        backoff = retry.get("backoff", 1.5)
        retry_on = set(retry.get("retry_on_status", [429, 500, 502, 503, 504]))
        timeout = self.politeness.get("timeout_seconds", 30)
        for attempt in range(attempts):
            try:
                resp = self._session.get(url, params=params, timeout=timeout)
                if resp.status_code in retry_on:
                    raise requests.HTTPError(f"status {resp.status_code}")
                resp.raise_for_status()
                return resp.json()
            except Exception:
                if attempt == attempts - 1:
                    raise
                time.sleep(backoff * (attempt + 1))
        raise RuntimeError("unreachable")

    # ------------------------------------------------------------------ walk

    def discover(
        self, *, until_before: str | None = None, overlap_pages: int = 0
    ) -> Iterator[DiscoveredItem]:
        req = self.role["request"]
        pag = self.role["pagination"]
        page_size = pag.get("size", 20)
        ct_field = get(self.role, "claimed_total.field")
        sanity_cap = get(self.role, "sanity_cap.records")
        items_path = get(self.role, "items.path", "resultaten")
        delay = self.politeness.get("delay_seconds", 0.25)

        start = 0
        position = 0
        seen: set[str] = set()
        pages_past_cutoff = 0

        while True:
            params = dict(req.get("params", {}))
            params[pag["param_start"]] = start
            params[pag["param_size"]] = page_size
            page = self._http_get(req["url"], params)
            self.requests_made += 1

            if ct_field and ct_field in page:
                if self.claimed_total_first is None:
                    self.claimed_total_first = page[ct_field]
                    self.facets = page.get("filters", {}) or {}
                self.claimed_total_last = page[ct_field]

            results = get(page, items_path) if "." in items_path else page.get(items_path)
            if not results:
                return

            page_crossed = False
            for result in results:
                self.raw_results += 1
                item = self._classify(result, position)
                position += 1
                if item is None:
                    continue
                if (
                    until_before
                    and item.published
                    and item.published < until_before
                ):
                    page_crossed = True
                    continue  # below cutoff: not recorded; overlap pages catch sort anomalies
                if item.natural_key in seen:
                    self.walk_duplicates += 1
                    continue
                seen.add(item.natural_key)
                yield item

            if page_crossed or pages_past_cutoff:
                pages_past_cutoff += 1
                if pages_past_cutoff > overlap_pages:
                    return

            start += page_size
            if sanity_cap and start > sanity_cap:
                self.sanity_cap_hit = True
                return
            if delay:
                time.sleep(delay)

    # ------------------------------------------------------------------ classify

    def _classify(self, result: dict, position: int) -> DiscoveredItem | None:
        fields = get(self.role, "items.fields", {})

        def fval(name: str) -> Any:
            path = fields.get(name)
            return get(result, path) if path else None

        raw_id = get(result, get(self.role, "items.id_field", "document.id")) or ""
        published = fval("published")
        stub_cfg = self.identity.get("federated_stub") or {}

        is_real_key = bool(self._key_pattern.fullmatch(raw_id)) if self._key_pattern else bool(raw_id)
        disposition, reason = "ok", None
        if not is_real_key:
            is_federated = (
                fval("aanbieder") == stub_cfg.get("aanbieder")
                or raw_id.startswith(stub_cfg.get("id_prefix", "\x00"))
            )
            if is_federated and raw_id:
                disposition = stub_cfg.get("disposition", "stub")
                reason = stub_cfg.get("reason")
            elif raw_id:
                disposition, reason = "stub", "id does not match natural_key_pattern"
            else:
                self.unrecordable += 1  # no identity at all; reported, never silent
                return None

        urn = self.identity["urn_template"].format(natural_key=raw_id)
        return DiscoveredItem(
            natural_key=raw_id,
            urn=urn,
            partition_leaf=month_leaf(published),
            disposition=disposition,
            reason=reason,
            title=fval("title") or "",
            publisher=fval("publisher") or "",
            published=published,
            landing_url=fval("landing_url") or fval("weblocatie"),
            file_url=None,  # filled by the frontier from manifest fetch config
            pages=fval("pages"),
            file_type=fval("file_type"),
            payload=result,
            leaf_position=position,
        )
