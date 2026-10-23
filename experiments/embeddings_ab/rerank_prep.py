#!/usr/bin/env python3
"""Stage-1 prep for the reranker A/B (local, no GPU).

For each base embedder, compute the top-100 dense candidates (from saved fp16 vectors)
for exactly the queries that have pooled judgments (pooled_qrels.json keys = qidx). Writes
a self-contained, shippable input file per base so downstream rerankers (API or pod) only
need (query, candidate texts) — no big vectors / pool required on the rerank host.

Writes: rerank_input_<base>.json = [{qidx, query, gold_id, cands:[{id, text}]}]  (text<=2000ch)
"""
import json, sys
import numpy as np

POOL = "slim_pool.jsonl"; QUERIES = "queries.jsonl"; EMB = "embeddings"
QRELS = "pooled_qrels.json"
TOPK = 100
TEXT_CHARS = 2000

def main():
    bases = sys.argv[1:] or ["bgem3", "voyage-4-large"]
    pool = [json.loads(l) for l in open(POOL)]
    pool_ids = [r["id"] for r in pool]
    id2text = {r["id"]: r["text"] for r in pool}
    queries = [json.loads(l) for l in open(QUERIES)]

    qrels = json.load(open(QRELS))
    judged_qidx = sorted(int(k) for k in qrels)          # the 250 judged queries
    print(f"{len(judged_qidx)} judged queries", file=sys.stderr)

    for base in bases:
        D = np.load(f"{EMB}/{base}.doc.f16.npy").astype(np.float32)
        Q = np.load(f"{EMB}/{base}.q.f16.npy").astype(np.float32)
        D /= (np.linalg.norm(D, axis=1, keepdims=True) + 1e-9)
        Q /= (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-9)
        out = []
        for qi in judged_qidx:
            sims = D @ Q[qi]
            top = np.argpartition(-sims, TOPK)[:TOPK]
            top = top[np.argsort(-sims[top])]
            cands = [{"id": pool_ids[int(r)], "text": id2text[pool_ids[int(r)]][:TEXT_CHARS]}
                     for r in top]
            out.append({"qidx": qi, "query": queries[qi]["query"],
                        "gold_id": queries[qi]["gold_id"], "cands": cands})
        path = f"rerank_input_{base}.json"
        json.dump(out, open(path, "w"))
        print(f"wrote {path}  ({len(out)} queries x {TOPK} cands)", file=sys.stderr)

if __name__ == "__main__":
    main()
