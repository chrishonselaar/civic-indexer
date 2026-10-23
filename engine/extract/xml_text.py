"""XML text extraction — for sources whose repository serves structured XML.

First exercised by Stage B source 2 (tuchtrecht: KOOP FRBR repository serves
per-uitspraak XML with OWMS metadata + full body text). Confidence is 1.0 by
construction when body text exists — XML is born-digital; there is no OCR tail.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

ENGINE = "xml-text"
ENGINE_VERSION = "1"

# Elements whose text is metadata, not body (OWMS/meta blocks).
DEFAULT_EXCLUDE = {"meta"}


@dataclass
class XmlExtraction:
    text: str
    confidence: float
    pages_processed: int
    total_pages: int


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract(xml_path: Path, exclude_tags: set[str] | None = None) -> XmlExtraction:
    exclude = exclude_tags or DEFAULT_EXCLUDE
    root = ElementTree.parse(xml_path).getroot()

    parts: list[str] = []

    def walk(node) -> None:
        if _strip_ns(node.tag) in exclude:
            return
        if node.text and node.text.strip():
            parts.append(node.text.strip())
        for child in node:
            walk(child)
        if node.tail and node.tail.strip():
            parts.append(node.tail.strip())

    walk(root)
    text = "\n\n".join(parts).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return XmlExtraction(
        text=text,
        confidence=1.0 if text else 0.0,
        pages_processed=1,
        total_pages=1,
    )
