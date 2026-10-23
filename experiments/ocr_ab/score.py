"""Automatic scoring across engine outputs in data/ocr_ab/<engine>/<stem>.json.

Azure DI is the REFERENCE engine (the goal is: close enough to Azure, orders of
magnitude cheaper). Metrics:
- sim: per-page normalized SequenceMatcher ratio vs azure_di, char-weighted mean.
- chars/page and density confidence (chars / (2000 * pages), the production heuristic)
- redaction-label fidelity: count of Woo exemption labels recognised in the output
  (5.1.2e / 5.1-2-e / 10.2.e style). For redacted docs an engine that reads labels
  beats one that hallucinates across black boxes.

Writes experiments/ocr_ab/results/scores.md (committed).
"""
import json
import re
from difflib import SequenceMatcher

from common import MANIFEST, OUT, RESULTS

REFERENCE = "azure_di"


def norm(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def sim_to_ref(pages, ref_pages):
    ref = {p["page"]: norm(p["text"]) for p in ref_pages}
    num = den = 0.0
    for p in pages:
        a, b = norm(p["text"]), ref.get(p["page"], "")
        w = max(len(b), 1)
        num += SequenceMatcher(None, a, b).ratio() * w
        den += w
    return num / den if den else 0.0

# 5.1.2e | 5.1-2-e | 5.1 lid 2 e | 10.2.e | 10.2e | art. 10 lid 2 onder e ...
LABEL_RE = re.compile(
    r"\b(?:5\.1(?:[.\-\s]?(?:lid\s*)?2)[.\-\s]?[a-i]\b"
    r"|10[.\-\s]?2[.\-\s]?[a-i]\b"
    r"|artikel\s+(?:5\.1|10)\b)",
    re.IGNORECASE,
)

engines = sorted(
    d.name for d in OUT.iterdir()
    if d.is_dir() and d.name != "pages" and not d.name.startswith(".") and any(d.glob("*.json"))
)
rows = []
for stem, cat in MANIFEST.items():
    row = {"file": stem, "cat": cat}
    ref_pages = None
    ref_f = OUT / REFERENCE / f"{stem}.json"
    if ref_f.exists():
        ref_pages = json.loads(ref_f.read_text())
    for eng in engines:
        f = OUT / eng / f"{stem}.json"
        if not f.exists():
            row[eng] = None
            continue
        pages = json.loads(f.read_text())
        text = "\n".join(p["text"] for p in pages)
        n = len(pages)
        row[eng] = {
            "pages": n,
            "chars": len(text),
            "cpp": round(len(text) / max(n, 1)),
            "conf": round(min(len(text) / (2000 * max(n, 1)), 1.0), 2),
            "labels": len(LABEL_RE.findall(text)),
            "sim": (round(sim_to_ref(pages, ref_pages), 2)
                    if ref_pages and eng != REFERENCE else None),
        }
    rows.append(row)

RESULTS.mkdir(exist_ok=True)
lines = ["# OCR A/B — automatic scores", "",
         f"Engines: {', '.join(engines)}. Reference engine: {REFERENCE}.",
         "cpp = chars/page; conf = density confidence; labels = Woo exemption labels",
         f"found (redaction-fidelity proxy); sim = similarity to {REFERENCE} (0-1).", ""]
for eng in engines:
    lines += [f"## {eng}", "", "| file | category | pages | chars | cpp | conf | labels | sim |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        m = r[eng]
        cells = (("—",) * 6 if m is None else
                 (m["pages"], m["chars"], m["cpp"], m["conf"], m["labels"],
                  "ref" if m["sim"] is None else m["sim"]))
        lines.append(f"| {r['file']} | {r['cat']} | " + " | ".join(str(c) for c in cells) + " |")
    lines.append("")
(RESULTS / "scores.md").write_text("\n".join(lines))
print(f"wrote {RESULTS / 'scores.md'} ({len(engines)} engines, {len(rows)} files)")
