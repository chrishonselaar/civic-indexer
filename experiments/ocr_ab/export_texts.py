"""Export every engine's output as readable .txt files for manual review.

data/ocr_ab/review_texts/<engine>/<stem>.txt — pages separated by '===== page N ====='.
"""
import json

from common import OUT

exported = 0
for eng_dir in sorted(OUT.iterdir()):
    if not eng_dir.is_dir() or eng_dir.name in ("pages", "judge", "review", "review_texts") \
       or eng_dir.name.startswith("."):
        continue
    out_dir = OUT / "review_texts" / eng_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(eng_dir.glob("*.json")):
        pages = json.loads(f.read_text())
        txt = "\n".join(f"===== page {p['page']} =====\n{p['text']}" for p in pages)
        (out_dir / f"{f.stem}.txt").write_text(txt)
        exported += 1
print(f"exported {exported} files to {OUT / 'review_texts'}")
