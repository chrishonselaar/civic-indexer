"""ori_elastic — bulk CONTENT harvest over an Open Raadsinformatie Elasticsearch index.

The aggregator route (design/aggregator-registry.md, W6.5 item 2 step 3). ORI republishes
the council-information corpora of six suppliers — Notubiz among them — and each
`MediaObject` carries metadata, the resolvable original bytes AND already-extracted page
text. That makes it `bulk_content` (rank 0): the one case where the two-tier content model
collapses into one tier because someone else already paid for tier 2.

Two properties are load-bearing and are pinned by tests:

1. **Identity is unchanged.** The natural key is derived from `original_url`
   (`api.notubiz.nl/document/{id}/{version}` -> `{id}`), which is exactly the key the
   Notubiz manifests already use, so a document harvested via ORI gets the *same URN* as
   the same document harvested via the provider. A source can switch routes, or run both,
   without re-keying a single item (design §9a).
2. **The text is second-hand.** `text_pages` is mapped to the same page model our OCR tail
   emits (`<!-- page N -->` blocks), and the extract-from-payload step records it as
   `method: aggregator, engine: ori, confidence: NULL` — never 1.0, which is reserved for a
   source's own authored text (design §9b).

Probed facts this code relies on (all measured live 2026-07-25 against
`api.openraadsinformatie.nl/v1/elastic`, see design/aggregator-registry.md §2):
  - ES bodies go through the GET-only HttpGet contract via `source=` +
    `source_content_type=`; no POST transport is needed.
  - `@id` is a sortable keyword and unique, so `search_after` gives a stable total order.
    `_count`-with-body and `_cat/indices` are BOTH traps — see engine/probe.py::_es_search.
  - `_source` include-filtering works, dotted paths included. This matters: a full record
    averages ~125 KB because `text`, `md_text` and `text_pages` each carry the whole body
    once. We request `text_pages` only, and store ONE mapped copy (the page model) in the
    payload — never the triplicate.
  - Not every `MediaObject` is a provider document: ~0.7% are links to meeting pages
    (`*.raadsinformatie.nl/vergadering/...`). Those cannot yield a document key and are
    counted `unrecordable`, never silently dropped.
"""

import json
import re
import time
from typing import Any, Callable, Iterator

import requests

from engine import config
from engine.envelope import DiscoveredItem
from engine.manifest import get

HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "application/json",
}

HttpGet = Callable[[str, dict[str, Any]], dict]

# The page model, byte-identical to what the OCR pod worker emits and what
# engine/drain.py counts (PAGE_MARK). Keeping one shape means a document whose text came
# from ORI and one we OCR'd ourselves are indistinguishable downstream.
PAGE_MARK = "<!-- page {n} -->"
PAGE_SEP = "\n\n---\n\n"


def pages_to_markdown(text_pages: list[dict]) -> tuple[str, int]:
    """[{page_number, text}] -> the engine's page model. Returns (markdown, page_count)."""
    blocks = []
    for i, page in enumerate(text_pages or []):
        n = page.get("page_number") or (i + 1)
        blocks.append(PAGE_MARK.format(n=n) + "\n" + (page.get("text") or ""))
    return PAGE_SEP.join(blocks), len(text_pages or [])


class OriElasticStrategy:
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
        self.facets: dict[str, Any] = {}

        rec = role["record"]
        self._key_field = rec["key_field"]
        self._key_pattern = re.compile(rec["key_pattern"])
        self._published_field = rec.get("published_field", "last_discussed_at")
        self._title_field = rec.get("title_field", "name")
        self._bytes_url_field = rec.get("bytes_url_field", "url")
        self._pages_field = rec.get("pages_field", "text_pages")
        self._engine_version_field = rec.get("engine_version_field")
        self._fields: dict[str, str] = rec.get("fields", {})

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
                return resp.json()
            except Exception:
                if attempt == attempts - 1:
                    raise
                time.sleep(backoff * (attempt + 1))
        raise RuntimeError("unreachable")

    def _search(self, body: dict[str, Any]) -> dict:
        req = self.role["request"]
        url = f"{req['url'].rstrip('/')}/{req['index']}/_search"
        page = self._http_get(
            url,
            {"source": json.dumps(body), "source_content_type": "application/json"},
        )
        self.requests_made += 1
        delay = self.role.get("delay_seconds", self.politeness.get("delay_seconds", 0.2))
        if delay:
            time.sleep(delay)
        return page

    # ------------------------------------------------------------------ walk

    def discover(
        self, *, until_before: str | None = None, overlap_pages: int = 0
    ) -> Iterator[DiscoveredItem]:
        pag = self.role.get("pagination", {})
        size = pag.get("size", 1000)
        sort_field = pag.get("sort_field", "@id")
        item = self.role.get("item", {})
        sanity_cap = get(self.role, "sanity_cap.records")

        must: list[dict[str, Any]] = []
        if item.get("type_field") and item.get("type_value"):
            must.append({"term": {item["type_field"]: item["type_value"]}})
        if until_before:
            # A windowed (incremental) walk keeps only items discussed at/after the cutoff.
            must.append({"range": {self._published_field: {"gte": until_before}}})

        base_body: dict[str, Any] = {
            "size": size,
            "track_total_hits": True,
            "query": {"bool": {"must": must}} if must else {"match_all": {}},
            "sort": [{sort_field: "asc"}],
        }
        includes = self.role.get("record", {}).get("source_includes")
        if includes:
            # Never request `text`/`md_text`: each duplicates the whole body (see header).
            base_body["_source"] = {"includes": list(includes)}

        seen: set[str] = set()
        search_after: list | None = None
        while True:
            body = dict(base_body)
            if search_after is not None:
                body["search_after"] = search_after
            doc = self._search(body)

            hits_env = doc.get("hits") or {}
            total = ((hits_env.get("total") or {}).get("value")) or 0
            if self.claimed_total_first is None:
                self.claimed_total_first = total
            self.claimed_total_last = total

            hits = hits_env.get("hits") or []
            if not hits:
                break
            for hit in hits:
                self.raw_results += 1
                built = self._parse(hit)
                if built is None:
                    continue
                if built.natural_key in seen:
                    self.walk_duplicates += 1
                    continue
                seen.add(built.natural_key)
                yield built
                if sanity_cap and len(seen) > sanity_cap:
                    self.sanity_cap_hit = True
                    return

            search_after = hits[-1].get("sort")
            if not search_after or len(hits) < size:
                break

        self.facets["media_objects_claimed"] = self.claimed_total_last
        self.facets["documents_keyed"] = len(seen)

    # ------------------------------------------------------------------ parse

    def _parse(self, hit: dict) -> DiscoveredItem | None:
        src = hit.get("_source") or {}
        raw_key = src.get(self._key_field)
        m = self._key_pattern.match(raw_key) if isinstance(raw_key, str) else None
        if m is None:
            # A MediaObject that is not a provider document (a link to a meeting page, a
            # supplier we have no key rule for). Reported, never silently dropped.
            self.unrecordable += 1
            return None
        key = m.group(1) if m.groups() else m.group(0)

        pattern = self.identity.get("natural_key_pattern")
        if pattern and not re.fullmatch(pattern, key):
            self.unrecordable += 1
            return None

        published_raw = src.get(self._published_field)
        published = str(published_raw)[:10] if published_raw else None
        leaf = published[:4] if published else "?"

        pages_markdown, page_count = pages_to_markdown(src.get(self._pages_field) or [])

        payload: dict[str, Any] = {}
        for payload_key, field in self._fields.items():
            v = get(src, field)
            if v is not None:
                payload[payload_key] = v
        payload["pages_markdown"] = pages_markdown
        payload["page_count"] = page_count
        payload["provider_url"] = raw_key
        if self._engine_version_field:
            payload["text_engine_version"] = get(src, self._engine_version_field)

        return DiscoveredItem(
            natural_key=key,
            urn=self.identity["urn_template"].format(natural_key=key),
            partition_leaf=leaf,
            disposition="ok",
            reason=None,
            title=src.get(self._title_field) or src.get("file_name") or key,
            publisher="",
            published=published,
            landing_url=None,
            file_url=src.get(self._bytes_url_field),
            pages=page_count or None,
            file_type=payload.get("mime_type"),
            payload=payload,
            # Multi-provenance: the provider's own URL is preserved beside the aggregator's
            # resolve URL (item-envelope §1 bet 2). A DISTINCT role, because the fetch
            # candidate query joins on role='file' and two 'file' rows would double-fetch.
            extra_urls=[(raw_key, "provider_file")],
        )
