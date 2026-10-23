#!/usr/bin/env python3
"""Import pod-encoded vectors.jsonl.gz into the embeddings cache.

After import, run `uv run civic embed <source>` — it finds every chunk vector
cached (encodes nothing) and fills the doc-level mean vectors; then re-project.
"""

import gzip
import json
import sys

from engine import db
from engine.embed import DIMENSION, MODEL_ID


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "vectors.jsonl.gz"
    opener = gzip.open if path.endswith(".gz") else open
    n = 0
    with opener(path, "rt", encoding="utf-8") as f, db.tx() as cur:
        for line in f:
            r = json.loads(line)
            assert len(r["vector"]) == DIMENSION, f"bad dim for {r['key']}"
            db.upsert(
                cur, "embeddings",
                {
                    "kind": "chunk", "key": r["key"], "source_id": r["source_id"],
                    "model": MODEL_ID, "content_hash": r["content_hash"],
                    "dim": DIMENSION, "vector": r["vector"],
                },
                conflict="kind, key, model",
            )
            n += 1
    print(f"imported {n} chunk vectors")


if __name__ == "__main__":
    main()
