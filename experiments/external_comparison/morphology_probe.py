#!/usr/bin/env python3
"""Why the gap exists: does their `text=` match Dutch morphology, or literal tokens?

An nDCG table says one system ranks better. It does not say why, and a finding
without a mechanism is not evidence — it is a scoreboard. Dutch is the mechanism
candidate here: it compounds ruthlessly (`medicatiefout`, `dossiervoering`,
`tuchtklacht`) and inflects, so a search that matches literal tokens loses
documents that a stemmed or dense one keeps.

Each row is one concept written several ways. If a system's hit count swings
wildly across spellings of the same concept, its matching is literal; if the
count holds, it normalises.

Windowed to 2025 on BOTH sides, exactly like the main comparison. The first run
of this probe was corpus-wide and therefore worthless: we hold full text for 2025
only, so every "ours" count was a coverage number wearing a matching number's
clothes. Same discipline as the comparison — control coverage or measure nothing.

Writes morphology.md. Counts only; no hit text leaves the session.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
DELAY_S = 1.2
UA = "civic-indexer benchmark (one-off manual comparison, ~1 rps)"

VARIANTS = [
    ("medicatiefout", ["medicatiefout", "medicatiefouten", "fout met medicatie"]),
    ("tandarts", ["tandarts", "tandartsen", "tandheelkundige"]),
    ("dossiervoering", ["dossiervoering", "dossier voering", "bijhouden van het dossier"]),
    ("doorhaling", ["doorhaling", "doorgehaald", "geschrapt uit het register"]),
]


YEAR = "2025"
MONTHS = [f"{YEAR}-{m:02d}" for m in range(1, 13)]
THEIR_WINDOW = {
    "ftsscope": "uitspraak",
    "datumtype": "uitspraak",
    "datumvan": f"01-01-{YEAR}",
    "datumtot": f"31-12-{YEAR}",
}


def theirs(q: str) -> int:
    url = "https://tuchtrecht.overheid.nl/zoeken/resultaat?" + urllib.parse.urlencode(
        {"text": q, **THEIR_WINDOW}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
    m = re.search(r"van de ([\d.,]+) resultaten", html)
    return int(m.group(1).replace(".", "").replace(",", "")) if m else 0


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from ui.search_api import app

    client = TestClient(app)

    def ours(q: str) -> int:
        r = client.get("/api/search", params=[
            ("q", q), ("dataset", "tuchtrecht"), ("mode", "bm25"), ("size", "1"),
        ] + [("month", m) for m in MONTHS])
        return r.json()["total"]

    rows = []
    first = True
    for concept, forms in VARIANTS:
        for form in forms:
            if not first:
                time.sleep(DELAY_S)
            first = False
            t, o = theirs(form), ours(form)
            rows.append((concept, form, t, o))
            print(f"{concept:16s} {form:34s} theirs={t:7d} ours_bm25={o:7d}", flush=True)

    lines = [
        "# Morphology probe — does `text=` match Dutch word forms?",
        "",
        "Hit counts over uitspraken dated 2025 on both sides, one concept per block "
        "written several ways. Our side is BM25-only, so this compares matching "
        "against matching — the dense channel is not what is being tested here.",
        "",
        "| concept | query form | theirs | ours (bm25) |",
        "|---|---|---:|---:|",
    ]
    lines += [f"| {c} | `{f}` | {t} | {o} |" for c, f, t, o in rows]
    (HERE / "morphology.md").write_text("\n".join(lines) + "\n")
    (HERE / "morphology.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\nwrote {HERE / 'morphology.md'}")


if __name__ == "__main__":
    main()
