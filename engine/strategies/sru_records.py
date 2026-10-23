"""sru_records — bulk metadata harvest over a KOOP SRU 2.0 endpoint.

The overheid.nl family exposes an SRU (Search/Retrieve via URL) service at
repository.overheid.nl/sru that returns rich OWMS metadata — title, creator,
dates, subject, and a human-written `dcterms:description` abstract — for up to
1000 records per request, with no per-request rate limit (unlike the FRBR file
repository, which caps at ~1 rps; see manifests/tuchtrecht.yaml fetch_rate_ceiling).

This makes SRU a *bulk discovery* surface: the whole corpus lands in
ceil(N/1000) requests. The abstract is carried inline in the item payload, so the
extract phase can distil it WITHOUT fetching the per-document XML (fetch.transport:
none). The FRBR file URL is still recorded (role='file') so full body text can be
backfilled later as a deferred upgrade.

Everything source-specific — endpoint, CQL query, page params, the record field →
payload-key map — comes from the manifest role; the SRU response envelope itself
(sru:record / sru:numberOfRecords / sru:nextRecordPosition) is standard and parsed
here. The injectable http_get returns {"html": <xml>} so fixture record/replay works
exactly as for the other strategies.
"""

import html
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
    "Accept": "application/xml",
}

HttpGet = Callable[[str, dict[str, Any]], dict]

_RE_RECORD = re.compile(r"<sru:record>(.*?)</sru:record>", re.S)
_RE_TOTAL = re.compile(r"<sru:numberOfRecords>(\d+)</sru:numberOfRecords>")
_RE_NEXT = re.compile(r"<sru:nextRecordPosition>(\d+)</sru:nextRecordPosition>")


def _field(block: str, tag: str) -> str | None:
    """First value of a (possibly namespaced) XML tag inside a record block."""
    m = re.search(rf"<{re.escape(tag)}(?:\s[^>]*)?>(.*?)</{re.escape(tag)}>", block, re.S)
    if not m:
        return None
    val = html.unescape(m.group(1)).strip()
    return val or None


class SruRecordsStrategy:
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
        self.leaf_stats: dict[str, dict[str, Any]] = {}

        rec = role["record"]
        self._key_tag = rec["key_field"]
        self._fields: dict[str, str] = rec.get("fields", {})
        self._file_url_field = rec.get("file_url_field")
        self._landing_url_field = rec.get("landing_url_field")
        self._published_key = rec.get("published_field")  # a payload key (post-map)

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
        delay = self.role.get("delay_seconds", self.politeness.get("delay_seconds", 0.2))
        if delay:
            time.sleep(delay)
        return page["html"]

    # ------------------------------------------------------------------ walk

    def discover(
        self, *, until_before: str | None = None, overlap_pages: int = 0
    ) -> Iterator[DiscoveredItem]:
        req = self.role["request"]
        pag = self.role.get("pagination", {})
        size = pag.get("size", 1000)
        p_start = pag.get("param_start", "startRecord")
        p_size = pag.get("param_size", "maximumRecords")
        sanity_cap = get(self.role, "sanity_cap.records")
        pattern = self.identity.get("natural_key_pattern")
        urn_template = self.identity["urn_template"]

        seen: set[str] = set()
        start = 1
        total: int | None = None
        while True:
            params = dict(req.get("params", {}))
            params[p_start] = start
            params[p_size] = size
            xml = self._get(req["url"], params)

            m = _RE_TOTAL.search(xml)
            page_total = int(m.group(1)) if m else 0
            if self.claimed_total_first is None:
                self.claimed_total_first = page_total
                total = page_total
            self.claimed_total_last = page_total

            blocks = _RE_RECORD.findall(xml)
            if not blocks:
                break
            for block in blocks:
                self.raw_results += 1
                item = self._parse(block, urn_template, pattern, until_before)
                if item is None:
                    continue
                if item.natural_key in seen:
                    self.walk_duplicates += 1
                    continue
                seen.add(item.natural_key)
                leaf = self.leaf_stats.setdefault(
                    item.partition_leaf, {"claimed": None, "harvested": 0, "cap_hit": False}
                )
                leaf["harvested"] += 1
                yield item
                if sanity_cap and len(seen) > sanity_cap:
                    self.sanity_cap_hit = True
                    return

            nxt = _RE_NEXT.search(xml)
            if nxt:
                start = int(nxt.group(1))
            else:
                start += size
            if total is not None and start > total:
                break

    # ------------------------------------------------------------------ parse

    def _parse(
        self, block: str, urn_template: str, pattern: str | None, until_before: str | None
    ) -> DiscoveredItem | None:
        key = _field(block, self._key_tag)
        if not key:
            self.unrecordable += 1
            return None
        if pattern and not re.fullmatch(pattern, key):
            self.unrecordable += 1
            return None

        payload: dict[str, Any] = {}
        for payload_key, tag in self._fields.items():
            v = _field(block, tag)
            if v is not None:
                payload[payload_key] = v
        payload["mime_type"] = "text/xml"

        published = payload.get(self._published_key) if self._published_key else None
        # A windowed (incremental) walk keeps only records at/after the cutoff.
        if until_before and published and published < until_before:
            return None
        leaf = published[:4] if published and len(published) >= 4 else "?"

        file_url = _field(block, self._file_url_field) if self._file_url_field else None
        landing_url = (
            _field(block, self._landing_url_field) if self._landing_url_field else None
        )
        return DiscoveredItem(
            natural_key=key,
            urn=urn_template.format(natural_key=key),
            partition_leaf=leaf,
            disposition="ok",
            reason=None,
            title=payload.get("title") or key,
            publisher=payload.get("instantie") or "",
            published=published,
            landing_url=landing_url,
            file_url=file_url,
            pages=None,
            file_type="xml",
            payload=payload,
        )
