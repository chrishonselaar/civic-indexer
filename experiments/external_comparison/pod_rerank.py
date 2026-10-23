#!/usr/bin/env python3
"""Runs ON the pod: score rerank_input.jsonl with a cross-encoder, write scores.

Mirrors experiments/embed_pod/pod_embed.py — no project imports, reads one JSONL and
writes another, so the pod needs nothing but transformers + torch.

Only the SCORES come back, never a ranking computed here: ordering happens in
rerank_pool.py import so the tie-break and top-k live with the rest of the harness.

`transformers==4.49.0` is pinned for the same reason embed_pod pins it (see the
ocr_drain traps): the newer stack on these images has broken this model class before.
"""

import gzip
import json
import os
import time

os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
import torch  # noqa: E402
from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: E402

IN = "rerank_input.jsonl"
OUT = "rerank_scores.jsonl"
MAX_LEN = 1024   # what rerank_open_vs_api_findings.md measured at 0.90 q/s on a 3090
BATCH = 16


def main() -> None:
    rows = [json.loads(line) for line in open(IN, encoding="utf-8")]
    model_id = rows[0].get("model", "BAAI/bge-reranker-v2-m3")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"{len(rows)} queries, model {model_id}, device {dev}", flush=True)

    tok = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForSequenceClassification.from_pretrained(model_id)
    # fp16 on GPU: same precision decision as pod_embed, and a cross-encoder's
    # score is only ever used as a sort key, so 1e-3 differences cannot change a
    # ranking that was not already a tie.
    if dev == "cuda":
        model = model.half()
    model = model.to(dev).eval()

    t0 = time.time()
    with open(OUT, "w", encoding="utf-8") as out:
        for row in rows:
            ids = [c["id"] for c in row["candidates"]]
            texts = [c["text"] for c in row["candidates"]]
            scores = []
            with torch.no_grad():
                for i in range(0, len(texts), BATCH):
                    enc = tok(
                        [[row["q"], t] for t in texts[i:i + BATCH]],
                        padding=True, truncation=True, max_length=MAX_LEN,
                        return_tensors="pt",
                    ).to(dev)
                    scores += model(**enc).logits.view(-1).float().tolist()
            out.write(json.dumps({
                "qid": row["qid"], "model": model_id, "ids": ids, "scores": scores,
            }) + "\n")
            print(f"{row['qid']}: {len(ids)} scored  ({time.time() - t0:.0f}s)", flush=True)
    print(f"done in {time.time() - t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
