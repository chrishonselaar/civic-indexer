"""Native PDF text extraction — the free first tier of the extraction ladder.

Ported from wob-search-mcp/shared/pdf_processor.py (extract_text_pymupdf +
text-density confidence + convert_to_markdown), native tier only: OCR, Azure,
pdfplumber and redaction detection deliberately left behind (ocr-strategy.md —
the tail defers to the queue, never blocks).
"""

import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

ENGINE = "pymupdf"
ENGINE_VERSION = fitz.version[0]

EXPECTED_CHARS_PER_PAGE = 2000  # text-density normalization from the Woo project


@dataclass
class NativeExtraction:
    text: str
    confidence: float
    pages_processed: int
    total_pages: int


def extract(pdf_path: Path) -> NativeExtraction:
    doc = fitz.open(pdf_path)
    try:
        parts: list[str] = []
        text_chars = 0
        total_pages = len(doc)
        for page in doc:
            page_text = page.get_text()
            parts.append(page_text)
            text_chars += len(page_text.strip())
        expected = total_pages * EXPECTED_CHARS_PER_PAGE
        confidence = min(text_chars / expected, 1.0) if expected else 0.0
        return NativeExtraction(
            text="\n\n".join(parts).strip(),
            confidence=confidence,
            pages_processed=total_pages,
            total_pages=total_pages,
        )
    finally:
        doc.close()


def to_markdown(title: str, text: str) -> str:
    """Distillate formatting (redaction markers dropped with the OCR tier)."""
    markdown = f"# {title}\n\n"
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if "http" in para:
            para = re.sub(r"(?<![(\[])(https?://[^\s)\]]+)", r"[\1](\1)", para)
        markdown += para + "\n\n"
    return markdown.strip()
