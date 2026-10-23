#!/usr/bin/env python3
"""Fetch the top-10 both systems return for the same query, in the same window.

Exit criterion #2 has two halves. The internal half (hybrid beats BM25-only) was
measured in W2. This is the EXTERNAL half, never taken before: does our search beat
the source site's own search on the same query?

Fairness, all of it deliberate:

- **Window.** Coverage is not query quality. We hold full FRBR body text for 2025
  only (the deferred-upgrade backfill), so both sides are restricted to uitspraken
  dated 2025. Ours by `month=2025-01..12` (published_month, built from
  uitspraakdatum); theirs by `datumtype=uitspraak&datumvan=01-01-2025&
  datumtot=31-12-2025`. Same axis, probe-verified exact (12 months = 2,504 = the
  year window).
- **Their strongest setting.** `ftsscope=uitspraak` — full decision text, their
  default, not the `indicatie` (summary-only) alternative.
- **No rerank.** tuchtrecht declares `policy.processors: []` and gives a reason:
  these decisions concern named professionals and Voyage's default terms are not a
  defensible place to send them. So `hybrid_rerank` is unavailable HERE by design,
  and this measures `hybrid`. Stated, not silently omitted.
- **Plain queries.** No W11 operator syntax, no chip hints — what a user types.

Writes runs.json: query -> system -> ordered ECLI list. ECLIs and ranks only, never
hit text: tuchtrecht is exposure T2 and concerns named individuals, and judging text
belongs in the session, not in the repo.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
YEAR = "2025"
MONTHS = [f"{YEAR}-{m:02d}" for m in range(1, 13)]
TOPK = 10

THEIRS = "https://tuchtrecht.overheid.nl/zoeken/resultaat"
THEIR_WINDOW = {
    "datumtype": "uitspraak",
    "datumvan": f"01-01-{YEAR}",
    "datumtot": f"31-12-{YEAR}",
    "ftsscope": "uitspraak",
    "count": "20",
}
# The discovery host was probed clean at ~1 rps over ~140 requests; this run is ~15.
DELAY_S = 1.2
UA = "civic-indexer benchmark (one-off manual comparison, ~1 rps)"


def theirs(q: str) -> tuple[list[str], int, list[str]]:
    """Their top-K, their total, and their result order's publication dates.

    The dates come back because their default order turned out to BE the finding:
    the sort control offers only Datum and Titel, and the unsorted default is
    publication-date descending, monotone across all 20 hits on the probe query.
    `text=` is a filter on their side, not a ranker — so this is ranked retrieval
    against a chronological filter, and the report has to say so rather than
    present it as one ranker beating another.
    """
    url = f"{THEIRS}?{urllib.parse.urlencode({'text': q, **THEIR_WINDOW})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
    m = re.search(r"van de ([\d.,]+) resultaten", html)
    total = int(m.group(1).replace(".", "").replace(",", "")) if m else 0
    eclis, pubs = [], []
    for block in re.split(r'<li class="item">', html)[1:]:
        e = re.search(r'href="/(ECLI:[^" ]+)', block)
        p = re.search(r"Datum publicatie: ([\d-]+)", block)
        if e:
            eclis.append(e.group(1))
            pubs.append(p.group(1) if p else "")
    return eclis[:TOPK], total, pubs[:TOPK]


def ours(client, q: str, mode: str) -> tuple[list[str], int]:
    r = client.get(
        "/api/search",
        params=[("q", q), ("dataset", "tuchtrecht"), ("mode", mode),
                ("size", str(TOPK))] + [("month", mth) for mth in MONTHS],
    )
    r.raise_for_status()
    d = r.json()
    return [h["natural_key"] for h in d["results"]][:TOPK], d["total"]


def main() -> None:
    sys.path.insert(0, str(HERE.parent.parent))
    from fastapi.testclient import TestClient

    from ui.search_api import app

    queries = [json.loads(line) for line in (HERE / "queries.jsonl").open()]
    client = TestClient(app)
    out: dict = {}

    for i, item in enumerate(queries):
        q = item["q"]
        rec = {"kind": item["kind"], "q": q, "systems": {}}
        for mode in ("bm25", "hybrid"):
            ids, total = ours(client, q, mode)
            rec["systems"][f"ours_{mode}"] = {"ids": ids, "total": total}
        ids, total, pubs = theirs(q)
        rec["systems"]["theirs"] = {"ids": ids, "total": total, "pub_dates": pubs}
        out[item["id"]] = rec
        print(f"{item['id']} {q[:40]:42s} "
              f"ours_bm25={rec['systems']['ours_bm25']['total']:6d} "
              f"ours_hybrid={rec['systems']['ours_hybrid']['total']:6d} "
              f"theirs={total:6d}", flush=True)
        if i < len(queries) - 1:
            time.sleep(DELAY_S)

    (HERE / "runs.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nwrote {HERE / 'runs.json'}")


if __name__ == "__main__":
    main()
