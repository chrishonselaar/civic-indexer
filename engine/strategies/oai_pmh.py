"""oai_pmh — bulk metadata harvest over an OAI-PMH 2.0 endpoint.

OAI-PMH (Open Archives Initiative Protocol for Metadata Harvesting) is the second
of the three bulk-discovery surfaces named in design/bulk-endpoint-discovery.md
(SRU done; bulk-archive still TODO). Like SRU it is a *bulk discovery* surface: a
`verb=ListRecords` walk returns Dublin-Core (or a richer `metadataPrefix`) record
set for the whole collection, paged by an opaque `resumptionToken`, with the total
carried in that token's `completeListSize` attribute — the same shape SRU expresses
via `numberOfRecords`/`nextRecordPosition`.

This mirrors sru_records.py deliberately: same DiscoveryStrategy counters, same
injectable http_get returning {"html": <xml>} (so fixture record/replay works),
same manifest-driven record→envelope map. The OAI-PMH *envelope* (the ListRecords /
record / header / metadata / resumptionToken elements, deleted-record status, and
the resumption-token paging rule) is protocol-standard and parsed here; everything
source-specific — endpoint, metadataPrefix, set, the record field → payload-key map —
comes from the manifest role.

Protocol notes that shape the parser (OAI-PMH 2.0 spec):
- A record is <record><header>…</header><metadata>…</metadata></record>. The header
  carries <identifier> (the OAI id, e.g. `oai:host:ECLI:…`), <datestamp>, <setSpec>.
- A *deleted* record is <header status="deleted"> with no <metadata>. These are
  counted (deleted_records) and skipped — never silently dropped.
- Paging: the FIRST request sends verb + metadataPrefix (+ optional set/from/until);
  every SUBSEQUENT request sends ONLY verb + the exclusive resumptionToken value.
  An empty or absent <resumptionToken> ends the walk. `completeListSize` (when
  present) is the claimed total, cross-checked against records actually harvested.

Because the OAI header identifier is usually namespaced (`oai:host:<key>`), the
manifest may declare `record.key_strip_prefix` to recover the bare natural key
(e.g. an ECLI) before the identity `natural_key_pattern` is applied.
"""

import html
import re
import time
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

_RE_RECORD = re.compile(r"<record>(.*?)</record>", re.S)
_RE_HEADER = re.compile(r"<header\b([^>]*)>(.*?)</header>", re.S)
# resumptionToken carries the cursor value as text and completeListSize as an attribute;
# a self-closing/empty token (<resumptionToken/> or <resumptionToken></resumptionToken>)
# signals the end of the walk.
_RE_TOKEN = re.compile(r"<resumptionToken\b([^>]*)>(.*?)</resumptionToken>", re.S)
_RE_TOKEN_EMPTY = re.compile(r"<resumptionToken\b[^>]*/>")
_RE_LIST_SIZE = re.compile(r'completeListSize\s*=\s*"(\d+)"')


def _field(block: str, tag: str) -> str | None:
    """First value of a (possibly namespaced) XML tag inside a record block."""
    m = re.search(rf"<{re.escape(tag)}(?:\s[^>]*)?>(.*?)</{re.escape(tag)}>", block, re.S)
    if not m:
        return None
    val = html.unescape(m.group(1)).strip()
    return val or None


class OaiPmhStrategy:
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
        self.deleted_records = 0
        self.sanity_cap_hit = False
        self.facets: dict[str, Any] = {}
        self.leaf_stats: dict[str, dict[str, Any]] = {}

        rec = role["record"]
        self._key_tag = rec["key_field"]
        self._key_strip_prefix = rec.get("key_strip_prefix")
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
        token_param = get(self.role, "pagination.param_token") or "resumptionToken"
        sanity_cap = get(self.role, "sanity_cap.records")
        pattern = self.identity.get("natural_key_pattern")
        urn_template = self.identity["urn_template"]

        seen: set[str] = set()
        # First request carries the full argument set; resumptionToken requests carry
        # ONLY verb + token (OAI-PMH 2.0 exclusivity rule), so we replace params wholesale.
        base_params = dict(req.get("params", {}))
        verb = base_params.get("verb", "ListRecords")
        params = base_params
        while True:
            xml = self._get(req["url"], params)

            size_m = _RE_LIST_SIZE.search(xml)
            page_total = int(size_m.group(1)) if size_m else None
            if page_total is not None:
                if self.claimed_total_first is None:
                    self.claimed_total_first = page_total
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

            token = self._next_token(xml)
            if not token:
                break
            params = {"verb": verb, token_param: token}

    def _next_token(self, xml: str) -> str | None:
        """Exclusive resumptionToken value, or None when the walk is complete."""
        if _RE_TOKEN_EMPTY.search(xml):
            return None
        m = _RE_TOKEN.search(xml)
        if not m:
            return None
        token = html.unescape(m.group(2)).strip()
        return token or None

    # ------------------------------------------------------------------ parse

    def _parse(
        self, block: str, urn_template: str, pattern: str | None, until_before: str | None
    ) -> DiscoveredItem | None:
        # Deleted records carry status="deleted" on the header and no metadata; count + skip.
        hm = _RE_HEADER.search(block)
        if hm and re.search(r'status\s*=\s*"deleted"', hm.group(1)):
            self.deleted_records += 1
            return None

        key = _field(block, self._key_tag)
        if key and self._key_strip_prefix and key.startswith(self._key_strip_prefix):
            key = key[len(self._key_strip_prefix):]
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
            publisher=payload.get("instantie") or payload.get("creator") or "",
            published=published,
            landing_url=landing_url,
            file_url=file_url,
            pages=None,
            file_type="xml",
            payload=payload,
        )
