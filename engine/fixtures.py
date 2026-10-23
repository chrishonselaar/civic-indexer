"""Fixture recording/replay: cached source pages for regression testing.

The design's regression method (onboarding-flow.md): strategies must run
identically off cached fixtures, so manifest changes are testable without
touching the live site.
"""

import json
import hashlib
from pathlib import Path
from typing import Any

from engine import db
from engine.config import PROJECT_ROOT

FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"


def recorder(source_id: str, max_pages: int = 2):
    """Wrap an http_get so its first N responses are saved as fixtures."""

    def wrap(http_get):
        state = {"n": 0}

        def recording_get(url: str, params: dict[str, Any]) -> dict:
            page = http_get(url, params)
            if state["n"] < max_pages:
                name = f"page_{state['n']}"
                save(source_id, name, "json", page, meta={"url": url, "params": params})
                state["n"] += 1
            return page

        return recording_get

    return wrap


def save(source_id: str, name: str, kind: str, payload: Any, meta: dict | None = None) -> Path:
    directory = FIXTURE_ROOT / source_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.{kind}"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with db.tx() as cur:
        db.upsert(
            cur,
            "fixtures",
            {
                "source_id": source_id,
                "name": name,
                "kind": kind,
                "path": str(path.relative_to(PROJECT_ROOT)),
                "meta": db.jsonb(meta or {}),
            },
            conflict="source_id, name",
        )
    return path


def replay_http_get(pages: list[dict]):
    """An http_get that serves canned pages in order, then empty results."""
    state = {"n": 0}

    def get(url: str, params: dict[str, Any]) -> dict:
        i = state["n"]
        state["n"] += 1
        if i < len(pages):
            return pages[i]
        return {"totaal": pages[-1].get("totaal") if pages else 0, "resultaten": []}

    return get
