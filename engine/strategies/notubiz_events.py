"""notubiz_events — server-side windowed, page-number-paged, two-level discovery.

The Notubiz archetype strategy (design/notubiz-archetype.md), written once for the
fleet: walk /events per date-bracketed leaf (DateBracketingStrategy keeps every leaf
under the probed 200 cap), fetch each event's meeting detail, and yield one
DiscoveredItem per *document* found in the detail (agenda_items recursive +
top-level). Reconciliation unit is EVENTS (the walk's native unit, claimed per leaf
by pagination.total_items); documents are children — their completeness control is
Σ ledgered docs vs Σ parsed docs per meeting (trivially closed at discovery,
meaningful at fetch; failure-modes TOT-4).

Probed facts this code relies on (manifests/haarlem_raad.yaml header): `version`
param required; strict datetime format; total_items truthful; has_more_pages
truthful; document publication_date LIES -> meeting planning date is published.
"""

import time
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator

import requests

from engine import config
from engine.envelope import DiscoveredItem
from engine.manifest import get
from engine.partition import DateBracketingStrategy

HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "application/json",
}

HttpGet = Callable[[str, dict[str, Any]], dict]


class NotubizEventsStrategy:
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
        self.raw_results = 0          # events seen
        self.walk_duplicates = 0      # same event id twice (bracket overlap would be a bug)
        self.unrecordable = 0
        self.sanity_cap_hit = False
        self.facets: dict = {}
        # Per-leaf event accounting; frontier prefers this over yield-counting
        # because yielded items are documents, not events.
        self.leaf_stats: dict[str, dict[str, Any]] = {}

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

    def _get(self, url: str, params: dict[str, Any]) -> dict:
        page = self._http_get(url, params)
        self.requests_made += 1
        delay = self.politeness.get("delay_seconds", 1.0)
        if delay:
            time.sleep(delay)
        return page

    # ------------------------------------------------------------------ walk

    def discover(
        self, *, until_before: str | None = None, overlap_pages: int = 0
    ) -> Iterator[DiscoveredItem]:
        req = self.role["request"]
        part = self.role["partition"]
        window_fmt = part.get("window_format", "%Y-%m-%d %H:%M:%S")
        range_start = datetime.fromisoformat(get(part, "range.start", "2008-01-01"))
        # window_start (YYYY-MM-DD) scopes a test/window run to leaves >= that date.
        if until_before:
            floor = datetime.fromisoformat(until_before)
            range_start = max(range_start, floor)
        range_end = datetime.now()

        def count(a: datetime, b: datetime) -> int:
            page = self._get(req["url"], self._window_params(req, a, b, window_fmt, page_no=1, size=1))
            return get(page, "pagination.total_items", 0) or 0

        brack = part.get("bracketing", {})
        leaves = DateBracketingStrategy(
            max_items=brack.get("max_items", 180), min_days=brack.get("min_days", 1)
        ).generate_date_ranges(range_start, range_end, count)

        sanity_cap = get(self.role, "sanity_cap.records")
        seen_events: set[int] = set()
        seen_docs: set[str] = set()
        events_total_claimed = 0
        self.facets["doc_cross_refs"] = 0

        for a, b in leaves:
            label = f"{a:%Y-%m-%d}..{b:%Y-%m-%d}"
            stats = self.leaf_stats.setdefault(
                label, {"claimed": None, "harvested": 0, "cap_hit": False}
            )
            page_no = 1
            while True:
                page = self._get(
                    req["url"],
                    self._window_params(req, a, b, window_fmt, page_no=page_no,
                                        size=get(self.role, "pagination.size", 200)),
                )
                pag = page.get("pagination", {}) or {}
                if stats["claimed"] is None:
                    stats["claimed"] = pag.get("total_items", 0)
                    events_total_claimed += stats["claimed"]
                # Per-leaf claims accumulate over the walk; first == last == the sum.
                self.claimed_total_first = self.claimed_total_last = events_total_claimed

                events = page.get("events", []) or []
                if not events:
                    break
                for ev in events:
                    self.raw_results += 1
                    ev_id = ev.get("id")
                    if ev_id in seen_events:
                        self.walk_duplicates += 1
                        continue
                    seen_events.add(ev_id)
                    stats["harvested"] += 1
                    yield from self._expand_event(ev, label, seen_docs)
                    if sanity_cap and len(seen_events) > sanity_cap:
                        self.sanity_cap_hit = True
                        return

                if not pag.get("has_more_pages", False):
                    break
                page_no += 1

        self.facets["events_claimed"] = events_total_claimed
        self.facets["events_walked"] = len(seen_events)

    def _window_params(
        self, req: dict, a: datetime, b: datetime, fmt: str, *, page_no: int, size: int
    ) -> dict[str, Any]:
        pag = self.role.get("pagination", {})
        params = dict(req.get("params", {}))
        params["date_from"] = a.strftime(fmt)
        params["date_to"] = b.strftime(fmt)
        params[pag.get("param_page", "page")] = page_no
        params[pag.get("param_size", "per_page")] = size
        return params

    # ------------------------------------------------------------------ expand

    def _expand_event(
        self, ev: dict, leaf: str, seen_docs: set[str]
    ) -> Iterator[DiscoveredItem]:
        ev_id = ev.get("id")
        published = None
        plannings = ev.get("plannings") or []
        if plannings and plannings[0].get("start_date"):
            published = str(plannings[0]["start_date"])[:10]
        title = ""
        for attr in ev.get("attributes") or []:
            if attr.get("id") == 1:  # stable attribute id 1 = title (probed)
                title = attr.get("value") or ""
                break
        gremium_id = get(ev, "gremium.id")

        if ev.get("confidential"):
            # ACC-2: never fetch confidential content; ledger the fact.
            yield self._stub_item(
                f"e{ev_id}", leaf, published,
                reason="confidential event: detail not fetched",
                title=title,
            )
            return

        detail_cfg = self.role.get("detail", {})
        url = detail_cfg["url_template"].format(event_id=ev_id)
        try:
            detail = self._get(url, dict(detail_cfg.get("params", {})))
        except Exception as e:
            yield self._stub_item(
                f"e{ev_id}", leaf, published,
                reason=f"meeting detail fetch failed: {e}", title=title,
            )
            return
        meeting = detail.get("meeting", detail) or {}

        # PROJ-1: this used to carry "gremium_name": None with the comment "gremia
        # lookup joined at enrichment time" — an enrichment stage that does not
        # exist, so two manifests projected publisher from a permanently-null path
        # and indexed a dead facet. The id is what the API gives us; the label
        # comes from the manifest's enrichment.lookup_tables at projection time.
        # Do not reinstate a null name key here.
        context = {
            "meeting_id": ev_id,
            "meeting_title": title,
            "gremium_id": gremium_id,
            "event_date": published,
        }

        docs: list[dict] = list(meeting.get("documents") or [])

        def walk_items(items: list) -> None:
            for ai in items or []:
                if ai.get("confidential"):
                    continue  # documents under confidential agenda items stay unfetched
                docs.extend(ai.get("documents") or [])
                walk_items(ai.get("agenda_items"))

        walk_items(meeting.get("agenda_items"))

        for doc in docs:
            key = str(doc.get("id") or "")
            if not key:
                self.unrecordable += 1
                continue
            if key in seen_docs:
                self.facets["doc_cross_refs"] = self.facets.get("doc_cross_refs", 0) + 1
                continue
            seen_docs.add(key)
            version = (doc.get("versions") or [{}])[0]
            payload = dict(doc)
            payload["meeting_context"] = context
            payload["file_size"] = version.get("file_size")
            payload["mime_type"] = version.get("mime_type")
            confidential = bool(doc.get("confidential"))
            yield DiscoveredItem(
                natural_key=key,
                urn=self.identity["urn_template"].format(natural_key=key),
                partition_leaf=leaf,
                disposition="stub" if confidential else "ok",
                reason="confidential document" if confidential else None,
                title=doc.get("title") or key,
                publisher="",
                published=published,  # meeting date; doc publication_date lies (probed)
                landing_url=None,
                file_url=None if confidential else doc.get("url"),
                pages=None,
                file_type=version.get("mime_type"),
                payload=payload,
            )

    def _stub_item(
        self, key: str, leaf: str, published: str | None, *, reason: str, title: str
    ) -> DiscoveredItem:
        return DiscoveredItem(
            natural_key=key,
            urn=self.identity["urn_template"].format(natural_key=key),
            partition_leaf=leaf,
            disposition="stub",
            reason=reason,
            title=title,
            publisher="",
            published=published,
            landing_url=None,
            file_url=None,
            pages=None,
            file_type=None,
            payload={},
        )
