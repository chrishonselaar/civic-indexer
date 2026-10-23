"""Score every engine against the human-corrected ground truth
(results/corrections.json from the review app). Azure DI is scored too — that number
is the trustworthiness of the reference itself.

Writes results/gt_scores.md: per-page and per-engine mean similarity (normalized
SequenceMatcher, same normalization as score.py's sim metric).
"""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from common import MANIFEST, OUT, RESULTS

HERE = Path(__file__).resolve().parent
GT = json.loads((RESULTS / "corrections.json").read_text())


def norm(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def tokens(s):
    s = re.sub(r"<[^>]+>", " ", s)          # html tags
    s = re.sub(r"[|#*`_]+", " ", s)          # markdown syntax
    return re.findall(r"\w+", s.lower())


def token_f1(a, b):
    from collections import Counter
    ca, cb = Counter(tokens(a)), Counter(tokens(b))
    overlap = sum((ca & cb).values())
    total = sum(ca.values()) + sum(cb.values())
    return 2 * overlap / total if total else 1.0


engines = sorted(
    d.name for d in OUT.iterdir()
    if d.is_dir() and d.name not in ("pages", "judge", "review") and not d.name.startswith(".")
    and any(d.glob("*.json"))
)

cache = {}
def engine_page(eng, stem, page):
    if (eng, stem) not in cache:
        f = OUT / eng / f"{stem}.json"
        cache[(eng, stem)] = ({p["page"]: p["text"] for p in json.loads(f.read_text())}
                              if f.exists() else {})
    return cache[(eng, stem)].get(page)


rows = []
for pid, entry in GT.items():
    stem, page = pid.rsplit(":", 1)
    page = int(page)
    gt = norm(entry["text"])
    row = {"stem": stem, "page": page, "cat": MANIFEST[stem], "status": entry["status"]}
    for eng in engines:
        t = engine_page(eng, stem, page)
        row[eng] = None if t is None else (
            round(SequenceMatcher(None, norm(t), gt).ratio(), 2),
            round(token_f1(t, entry["text"]), 2),
        )
    rows.append(row)

lines = ["# OCR A/B — scores vs human-corrected ground truth", "",
         f"{len(rows)} sample pages (review app, corrected by Chris). Cells: sim/F1.",
         "sim = char-sequence similarity (order+format sensitive);",
         "F1 = bag-of-words token overlap, markup stripped (content only).",
         "azure_di's own scores = trustworthiness of the reference engine.", "",
         "| file | p | category | " + " | ".join(engines) + " |",
         "|---|---:|---|" + "---:|" * len(engines)]
for r in rows:
    cells = " | ".join("—" if r[e] is None else f"{r[e][0]:.2f}/{r[e][1]:.2f}" for e in engines)
    lines.append(f"| {r['stem'][:40]} | {r['page']} | {r['cat']} | {cells} |")

lines += ["", "## Mean per engine", "", "| engine | mean sim | mean F1 | pages scored |", "|---|---:|---:|---:|"]
for eng in engines:
    vals = [r[eng] for r in rows if r[eng] is not None]
    ms = sum(v[0] for v in vals) / len(vals)
    mf = sum(v[1] for v in vals) / len(vals)
    lines.append(f"| {eng} | {ms:.3f} | {mf:.3f} | {len(vals)} |")

(RESULTS / "gt_scores.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines[-len(engines):]))
