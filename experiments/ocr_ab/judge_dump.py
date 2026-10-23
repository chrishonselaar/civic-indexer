"""Dump side-by-side engine outputs for the spot-check pages (page 1 + middle page of
each file, per the test-set protocol's ~2 pages/file guidance).

Output: data/ocr_ab/judge/<stem>_p<NNNN>.md, referencing the page JPEG to judge against.
"""
import json

from common import MANIFEST, OUT

engines = sorted(
    d.name for d in OUT.iterdir()
    if d.is_dir() and d.name not in ("pages", "judge") and not d.name.startswith(".")
    and any(d.glob("*.json"))
)
judge_dir = OUT / "judge"
judge_dir.mkdir(exist_ok=True)

for stem in MANIFEST:
    loaded = {}
    for eng in engines:
        f = OUT / eng / f"{stem}.json"
        if f.exists():
            loaded[eng] = {p["page"]: p["text"] for p in json.loads(f.read_text())}
    if not loaded:
        continue
    n = max(max(pp) for pp in loaded.values())
    picks = sorted({1, (n + 1) // 2})
    for pg in picks:
        lines = [f"# {stem} — page {pg}/{n}",
                 f"Image: data/ocr_ab/pages/{stem}/p{pg:04d}.jpg", ""]
        for eng in engines:
            lines += [f"## {eng}", "", "```",
                      loaded.get(eng, {}).get(pg, "(missing)").strip() or "(empty)",
                      "```", ""]
        (judge_dir / f"{stem}_p{pg:04d}.md").write_text("\n".join(lines))
print(f"wrote spot-check dumps for {len(MANIFEST)} files to {judge_dir}")
