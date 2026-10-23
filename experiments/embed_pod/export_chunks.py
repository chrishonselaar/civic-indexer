#!/usr/bin/env python3
"""Export chunks still missing bge-m3 vectors as JSONL for a burst pod.

Usage:  uv run python experiments/embed_pod/export_chunks.py haarlem_raad [...] > chunks.jsonl
Then:   scp chunks.jsonl pod_embed.py root@<pod>:/root/
        ssh root@<pod> 'cd /root && pip install -q sentence-transformers && python3 pod_embed.py'
        scp root@<pod>:/root/vectors.jsonl.gz .
        uv run python experiments/embed_pod/import_vectors.py vectors.jsonl.gz
        uv run civic embed <source>   # no-op encode; fills doc-level mean vectors
"""

import json
import sys

from engine import blobstore, db
from engine.chunker import DocumentChunker
from engine.embed import MODEL_ID, _hash
from engine.project.projector import _select_projectable


def main() -> None:
    sources = sys.argv[1:]
    if not sources:
        sys.exit("usage: export_chunks.py <source_id> [...]")
    chunker = DocumentChunker()
    exported = 0
    for source_id in sources:
        with db.tx() as cur:
            rows = _select_projectable(cur, source_id)
            cur.execute(
                "SELECT key, content_hash FROM embeddings "
                "WHERE source_id = %s AND model = %s AND kind = 'chunk'",
                (source_id, MODEL_ID),
            )
            cached = {r["key"]: r["content_hash"] for r in cur.fetchall()}
        for row in rows:
            if not row["text_ref"]:
                continue
            path = blobstore.from_ref(row["text_ref"])
            if not path.exists():
                continue
            for c in chunker.chunk_document(path.read_text(encoding="utf-8"), row["urn"]):
                chash = _hash(c.text)
                if cached.get(c.chunk_id) == chash:
                    continue
                print(json.dumps(
                    {"key": c.chunk_id, "source_id": source_id,
                     "content_hash": chash, "text": c.text},
                    ensure_ascii=False,
                ))
                exported += 1
    print(f"exported {exported} chunks", file=sys.stderr)


if __name__ == "__main__":
    main()
