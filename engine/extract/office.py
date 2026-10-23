"""OOXML single-document text extraction (DOCX / XLSX / PPTX).

An Office Open XML file *is* a ZIP, so the fetch-phase magic sniffer files it as
`zip` and it lands on the `zip_dossier` deferred queue alongside true document
archives (bulk-endpoint-discovery.md). The distinction is a member fingerprint,
not the magic bytes: an OOXML file carries `[Content_Types].xml` plus a
`word/`|`xl/`|`ppt/` part; it is one born-digital document, NOT a dossier of
members. This module distils that one document to text.

Born-digital like `xml_text` — confidence is 1.0 by construction when body text
exists; there is no OCR tail. Stdlib only (zipfile + ElementTree), no new deps.
"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

ENGINE = "ooxml-text"
ENGINE_VERSION = "1"


@dataclass
class OfficeExtraction:
    text: str
    confidence: float
    kind: str          # docx | xlsx | pptx
    units: int         # paragraphs (docx) | rows w/ text (xlsx) | slides (pptx)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_of(node) -> str:
    """Concatenate every descendant <*:t> run under node, in document order.

    Namespace-agnostic (local-name match) so it serves word `w:t`, drawing `a:t`
    and spreadsheet `t` alike."""
    out: list[str] = []
    for el in node.iter():
        if _local(el.tag) == "t" and el.text:
            out.append(el.text)
    return "".join(out)


def _extract_docx(z: zipfile.ZipFile) -> OfficeExtraction:
    root = ElementTree.fromstring(z.read("word/document.xml"))
    paras: list[str] = []
    for el in root.iter():
        if _local(el.tag) == "p":
            t = _text_of(el).strip()
            if t:
                paras.append(t)
    text = "\n\n".join(paras).strip()
    return OfficeExtraction(text, 1.0 if text else 0.0, "docx", len(paras))


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
    return [_text_of(si) for si in root if _local(si.tag) == "si"]


def _extract_xlsx(z: zipfile.ZipFile) -> OfficeExtraction:
    shared = _shared_strings(z)
    sheets = sorted(
        n for n in z.namelist()
        if n.startswith("xl/worksheets/") and n.endswith(".xml")
    )
    rows_out: list[str] = []
    for sheet in sheets:
        root = ElementTree.fromstring(z.read(sheet))
        for row in root.iter():
            if _local(row.tag) != "row":
                continue
            cells: list[str] = []
            for c in row:
                if _local(c.tag) != "c":
                    continue
                ctype = c.get("t")
                v = "".join(
                    ch.text or "" for ch in c.iter()
                    if _local(ch.tag) in ("v", "t")
                )
                if not v:
                    continue
                if ctype == "s":  # shared-string index
                    try:
                        v = shared[int(v)]
                    except (ValueError, IndexError):
                        pass
                if v.strip():
                    cells.append(v.strip())
            if cells:
                rows_out.append("\t".join(cells))
    text = "\n".join(rows_out).strip()
    return OfficeExtraction(text, 1.0 if text else 0.0, "xlsx", len(rows_out))


def _extract_pptx(z: zipfile.ZipFile) -> OfficeExtraction:
    slides = sorted(
        n for n in z.namelist()
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
    )
    blocks: list[str] = []
    for slide in slides:
        root = ElementTree.fromstring(z.read(slide))
        # One text run per <a:p> keeps line structure readable.
        lines = [
            t for p in root.iter() if _local(p.tag) == "p"
            if (t := _text_of(p).strip())
        ]
        if lines:
            blocks.append("\n".join(lines))
    text = "\n\n".join(blocks).strip()
    return OfficeExtraction(text, 1.0 if text else 0.0, "pptx", len(slides))


_EXTRACTORS = {"docx": _extract_docx, "xlsx": _extract_xlsx, "pptx": _extract_pptx}


def extract(zip_path: Path, kind: str) -> OfficeExtraction:
    """Distil an OOXML office file to text. `kind` comes from archive.classify()."""
    if kind not in _EXTRACTORS:
        raise ValueError(f"office.extract: unsupported OOXML kind {kind!r}")
    with zipfile.ZipFile(zip_path) as z:
        return _EXTRACTORS[kind](z)
