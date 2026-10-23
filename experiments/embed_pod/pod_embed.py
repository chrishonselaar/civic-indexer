#!/usr/bin/env python3
"""Runs ON the pod: encode chunks.jsonl with bge-m3, write vectors.jsonl.gz.

Matches engine/embed.py on normalization + max_seq 2048 so pod and local vectors
are interchangeable; encodes at fp16 (per the embed_eval.py A/B) where the engine
query path stays fp32 — the ~1e-3 cosine difference is below retrieval noise.
"""

import gzip
import json
import os
import time

os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

# Peak VRAM is set by the LONGEST batch, not the average one, and attention is
# O(L^2): 96 x 16 heads x 2048 x 2048 x fp16 = 12.9 GB for one tensor. sentence-
# transformers sorts by length descending, so a corpus that contains ANY full-2048-token
# chunks hits that on its first batch — measured 2026-07-30, a 4090 (23.5 GB) OOMed on
# chunk 1 of 108,696 where the same script had run fine over 15,484 shorter ones. 24 is
# the largest that fits a full-length batch with the model resident; raise it with
# EMBED_BATCH only for a corpus known to be short-chunked.
BATCH = int(os.environ.get("EMBED_BATCH", "24"))


def main() -> None:
    rows = [json.loads(l) for l in open("chunks.jsonl", encoding="utf-8")]
    print(f"{len(rows)} chunks to encode", flush=True)
    # fp16 is the settled precision: the A/B that SELECTED bge-m3 (embed_eval.py,
    # success@10 0.972) encoded at fp16 and stored fp16 vectors — so this ships
    # exactly what was benchmarked, ~4x faster than the fp32 default on Ampere
    # tensor cores. fp16 vs fp32 retrieval delta is ~1e-3 cosine, below eval noise.
    model = SentenceTransformer("BAAI/bge-m3", model_kwargs={"torch_dtype": torch.float16})
    model.max_seq_length = 2048
    t0 = time.time()
    vectors = model.encode(
        [r["text"] for r in rows],
        batch_size=BATCH,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )
    print(f"encoded in {time.time() - t0:.0f}s", flush=True)
    with gzip.open("vectors.jsonl.gz", "wt", encoding="utf-8") as out:
        for r, vec in zip(rows, vectors):
            out.write(json.dumps({
                "key": r["key"], "source_id": r["source_id"],
                "content_hash": r["content_hash"],
                "vector": [round(float(x), 7) for x in vec],
            }) + "\n")
    print("wrote vectors.jsonl.gz", flush=True)


if __name__ == "__main__":
    main()
