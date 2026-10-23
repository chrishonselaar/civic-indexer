"""paged_html_search — server-side windowed, page-number-paged HTML search walk.

The bespoke-source strategy for Stage B source 2 (tuchtrecht.overheid.nl), kept
mechanism-generic on purpose: everything site-specific — the claimed-total count
string, the result-row block, per-field regexes, the file-URL template — comes
from the manifest (label-anchored EXPRs, decision-inventory.md §6). The KOOP
"cb-common" frontend serves several overheid.nl family sites, so this is likely
an archetype in waiting, not a one-off.

The injectable http_get returns {"html": <page text>} so fixture record/replay
works unchanged.
"""

import re
import time
from datetime import datetime
from typing import Any, Callable, Iterator

import requests

from engine import config
from engine.envelope import DiscoveredItem
from engine.manifest import get

HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "text/html",
}

HttpGet = Callable[[str, dict[str, Any]], dict]


def _url_vars(natural_key: str) -> dict[str, str]:
    """Template variables derivable from the natural key (e.g. an ECLI)."""
    vars: dict[str, str] = {
        "natural_key": natural_key,
        "natural_key_underscored": natural_key.replace(":", "_"),
    }
    for i, part in enumerate(natural_key.split(":")):
        vars[f"key_part_{i}"] = part
    return vars


class PagedHtmlSearchStrategy:
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
        self.leaf_stats: dict[str, dict[str, Any]] = {}

        ex = role["extract"]
        self._re_total = re.compile(ex["claimed_total_regex"])
        self._re_block = re.compile(ex["item_block_regex"], re.S)
        self._re_fields = {
            name: re.compile(rx, re.S) for name, rx in ex.get("field_regexes", {}).items()
        }
        self._date_fields = set(ex.get("date_fields", []))
        self._date_format = ex.get("date_format", "%d-%m-%Y")

    # ------------------------------------------------------------------ http

    def _real_http_get(self, url: str, params: dict[str, Any]) -> dict:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        retry = self.politeness.get("retry", {})
        attempts = retry.get("attempts", 3)
        backoff = retry.get("backoff", 1.5)
        retry_on = set(retry.get("retry_on_status", [429, 500, 502, 503, 504]))
        timeout = self.politeness.get("timeout_seconds", 60)
        for attempt in range(attempts):
            try:
                resp = self._session.get(url, params=params, timeout=timeout)
                if resp.status_code in retry_on:
                    raise requests.HTTPError(f"status {resp.status_code}")
                resp.raise_for_status()
                return {"html": resp.text}
            except Exception:
                if attempt == attempts - 1:
                    raise
                time.sleep(backoff * (attempt + 1))
        raise RuntimeError("unreachable")

    def _get(self, url: str, params: dict[str, Any]) -> str:
        page = self._http_get(url, params)
        self.requests_made += 1
        delay = self.politeness.get("delay_seconds", 1.0)
        if delay:
            time.sleep(delay)
        return page["html"]

    # ------------------------------------------------------------------ walk

    def discover(
        self, *, until_before: str | None = None, overlap_pages: int = 0
    ) -> Iterator[DiscoveredItem]:
        req = self.role["request"]
        part = self.role["partition"]
        win = self.role["window"]
        pag = self.role.get("pagination", {})
        size = pag.get("size", 50)
        sanity_cap = get(self.role, "sanity_cap.records")

        start_year = datetime.fromisoformat(get(part, "range.start", "1994-01-01")).year
        if until_before:
            start_year = max(start_year, datetime.fromisoformat(until_before).year)
        end_year = datetime.now().year

        seen: set[str] = set()

        for year in range(start_year, end_year + 1):
            leaf = str(year)
            stats = self.leaf_stats.setdefault(
                leaf, {"claimed": None, "harvested": 0, "cap_hit": False}
            )
            page_no = 1
            while True:
                params = dict(req.get("params", {}))
                params[win["param_from"]] = datetime(year, 1, 1).strftime(win.get("date_format", "%d-%m-%Y"))
                params[win["param_to"]] = datetime(year, 12, 31).strftime(win.get("date_format", "%d-%m-%Y"))
                params[pag.get("param_page", "page")] = page_no
                params[pag.get("param_size", "count")] = size
                html = self._get(req["url"], params)

                m = self._re_total.search(html)
                total = int(m.group(1).replace(".", "").replace(",", "")) if m else 0
                if stats["claimed"] is None:
                    stats["claimed"] = total
                    self.claimed_total_first = (self.claimed_total_first or 0) + total
                self.claimed_total_last = self.claimed_total_first

                blocks = self._re_block.findall(html)
                if not blocks:
                    break
                for block in blocks:
                    self.raw_results += 1
                    item = self._parse_block(block, leaf)
                    if item is None:
                        continue
                    if item.natural_key in seen:
                        self.walk_duplicates += 1
                        continue
                    seen.add(item.natural_key)
                    stats["harvested"] += 1
                    yield item
                    if sanity_cap and len(seen) > sanity_cap:
                        self.sanity_cap_hit = True
                        return

                if page_no * size >= total:
                    break
                page_no += 1

    # ------------------------------------------------------------------ parse

    def _parse_block(self, block: str, leaf: str) -> DiscoveredItem | None:
        fields: dict[str, Any] = {}
        for name, rx in self._re_fields.items():
            m = rx.search(block)
            fields[name] = m.group(1).strip() if m else None

        key = fields.get("key")
        if not key:
            self.unrecordable += 1
            return None
        pattern = self.identity.get("natural_key_pattern")
        if pattern and not re.fullmatch(pattern, key):
            self.unrecordable += 1
            return None

        published = None
        for name in self._date_fields:
            if fields.get(name):
                try:
                    fields[name] = datetime.strptime(
                        fields[name], self._date_format
                    ).strftime("%Y-%m-%d")
                except ValueError:
                    pass  # keep raw string; health checks catch systematic misparses
        published = fields.get("published")

        vars = _url_vars(key)
        file_url = get(self.role, "file_url_template")
        landing_url = get(self.role, "landing_url_template")
        return DiscoveredItem(
            natural_key=key,
            urn=self.identity["urn_template"].format(natural_key=key),
            partition_leaf=leaf,
            disposition="ok",
            reason=None,
            title=fields.get("title") or key,
            publisher=fields.get("publisher") or "",
            published=published,
            landing_url=landing_url.format(**vars) if landing_url else None,
            file_url=file_url.format(**vars) if file_url else None,
            pages=None,
            file_type="application/xml",
            payload=fields,
        )
