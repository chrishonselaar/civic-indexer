"""Render every test-set page to JPEG at the shared DPI (input for image-based OCR
engines and for the VLM judge). Output: data/ocr_ab/pages/<stem>/p0001.jpg ...

Run with a python that has PyMuPDF.
"""
import fitz

from common import MANIFEST, OUT, RENDER_DPI, pdf_path

zoom = RENDER_DPI / 72
for stem in MANIFEST:
    d = OUT / "pages" / stem
    d.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path(stem))
    for i, page in enumerate(doc):
        f = d / f"p{i + 1:04d}.jpg"
        if f.exists():
            continue
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        pix.save(f, jpg_quality=85)
    print(f"{stem:55s} {len(doc):4d} pages rendered")
