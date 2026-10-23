"""Blob and text-distillate storage: bytes on disk, refs in Postgres (never DB BLOBs)."""

import re
from pathlib import Path

from engine.config import BLOB_DIR, DATA_DIR, TEXT_DIR

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(name: str) -> str:
    """Sanitize a path component (decision-inventory.md §5: never trust source strings)."""
    return _SAFE.sub("_", name)[:200]


def blob_path(source_id: str, natural_key: str, ext: str = "bin") -> Path:
    p = BLOB_DIR / _safe(source_id) / f"{_safe(natural_key)}.{_safe(ext)}"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def text_path(source_id: str, leaf: str, natural_key: str) -> Path:
    p = TEXT_DIR / _safe(source_id) / _safe(leaf or "unknown") / f"{_safe(natural_key)}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def ref(path: Path) -> str:
    """Store refs relative to the data dir so the tree is relocatable."""
    return str(path.relative_to(DATA_DIR))


def from_ref(reference: str) -> Path:
    return DATA_DIR / reference
