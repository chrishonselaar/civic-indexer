"""Archive classification + member iteration — the bulk-archive format router.

The fetch phase sniffs magic bytes and files anything ZIP-shaped as `zip`, then
defers it (`zip_dossier`) because two very different things share that magic:

  * an **OOXML office document** (DOCX/XLSX/PPTX) — ONE born-digital document that
    merely happens to be a zip container; text lives in a `word/`|`xl/`|`ppt/` part;
  * a **document archive** — a real bundle whose members are the documents (e.g. a
    Woo besluit's released PDFs, 3–93 per zip).

Routing them apart needs the member list, not the magic bytes — so it is a
resolver-time decision, kept out of the hot fetch path. This module is the pure
(no-DB) classifier + member enumerator; `engine/archive_resolve.py` acts on it.
See design/bulk-endpoint-discovery.md (bulk-archive router).
"""

import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Member extensions we can route downstream today. PDFs go through the native
# ladder (+ deferred OCR tail); XML through xml_text. Everything else is recorded
# as an unrouted member (negative knowledge kept), never silently dropped.
ROUTABLE_EXTS = {"pdf", "xml"}

# OOXML content-type markers: presence of this part + a body part => single office
# document, not an archive.
_CONTENT_TYPES = "[Content_Types].xml"
_OOXML_BODY = {"word/": "docx", "xl/": "xlsx", "ppt/": "pptx"}


@dataclass
class Member:
    name: str          # full member path inside the zip
    index: int         # 0-based position among routable members (stable child key)
    ext: str           # lowercased extension, no dot
    routable: bool


def _is_junk(name: str) -> bool:
    if name.endswith("/"):  # directory entry
        return True
    base = posixpath.basename(name)
    return (
        not base
        or name.startswith("__MACOSX/")
        or base.startswith("._")
        or base == ".DS_Store"
    )


def _ext(name: str) -> str:
    base = posixpath.basename(name)
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def classify(zip_path: Path) -> str:
    """Return one of: docx | xlsx | pptx | ooxml | document_archive | empty | corrupt.

    `ooxml` = an OOXML container we recognise as single-document but not one of the
    three we extract (routed to a failed/unsupported terminal, not fanned out)."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        return "corrupt"
    nameset = set(names)
    if _CONTENT_TYPES in nameset:
        for prefix, kind in _OOXML_BODY.items():
            if any(n.startswith(prefix) for n in names):
                return kind
        return "ooxml"
    real = [n for n in names if not _is_junk(n)]
    if not real:
        return "empty"
    return "document_archive"


def members(zip_path: Path) -> list[Member]:
    """Enumerate a document archive's members in stable order.

    Order is the zip's own member order (deterministic across reads); the routable
    index is assigned over routable members only, so a child key never shifts when
    an unroutable sibling is present."""
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not _is_junk(n)]
    out: list[Member] = []
    idx = 0
    for name in names:
        ext = _ext(name)
        routable = ext in ROUTABLE_EXTS
        out.append(Member(name=name, index=idx if routable else -1,
                          ext=ext, routable=routable))
        if routable:
            idx += 1
    return out


def extract_member(zip_path: Path, member_name: str, dest: Path) -> int:
    """Write one member's bytes to `dest`. Returns bytes written."""
    with zipfile.ZipFile(zip_path) as z:
        data = z.read(member_name)
    dest.write_bytes(data)
    return len(data)
