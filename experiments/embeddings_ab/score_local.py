#!/usr/bin/env python3
"""Unified local scorer — re-ranks ALL models through ONE code path from saved
vectors, so cross-model numbers are guaranteed apples-to-apples (no GPU-arm vs
Voyage-arm harness difference). Also the reusable base for reranker/fusion experiments.

Reads embeddings/<key>.doc.f16.npy (row order = slim_pool.jsonl) and
embeddings/<key>.q.f16.npy (row order = queries.jsonl). Known-item: each query's
gold chunk is the single relevant doc.

Usage: python score_local.py [key ...]   (default: every model with vectors on disk)
Writes: unified_scores.md + unified_scores.json
"""
import json, sys, glob, os
import numpy as np

POOL = "slim_pool.jsonl"
QUERIES = "queries.jsonl"
EMB = "embeddings"

def load_ids():
    pool_ids = [json.loads(l)["id"] for l in open(POOL)]
    q = [json.loads(l) for l in open(QUERIES)]
    gold_ids = [x["gold_id"] for x in q]
    return pool_ids, gold_ids

def score(key, pool_ids, gold_ids, id2row):
    D = np.load(f"{EMB}/{key}.doc.f16.npy").astype(np.float32)
    Q = np.load(f"{EMB}/{key}.q.f16.npy").astype(np.float32)
    assert D.shape[0] == len(pool_ids), f"{key}: doc rows {D.shape[0]} != pool {len(pool_ids)}"
    assert Q.shape[0] == len(gold_ids), f"{key}: q rows {Q.shape[0]} != queries {len(gold_ids)}"
    D /= (np.linalg.norm(D, axis=1, keepdims=True) + 1e-9)
    Q /= (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-9)
    gold_rows = np.array([id2row[g] for g in gold_ids])

    K = 100
    ranks = np.full(len(gold_ids), -1, dtype=np.int64)
    B = 512
    for i in range(0, Q.shape[0], B):
        sims = Q[i:i+B] @ D.T
        top = np.argpartition(-sims, K, axis=1)[:, :K]
        # order the top-K per row
        for j in range(top.shape[0]):
            row = top[j][np.argsort(-sims[j, top[j]])]
            hit = np.where(row == gold_rows[i + j])[0]
            if len(hit):
                ranks[i + j] = int(hit[0]) + 1
    M = len(ranks)
    hit = ranks > 0
    def r_at(k): return float(np.mean(hit & (ranks <= k)))
    ndcg = float(np.mean(np.where(hit & (ranks <= 10), 1.0 / np.log2(ranks + 1), 0.0)))
    mrr = float(np.mean(np.where(hit, 1.0 / np.where(hit, ranks, 1), 0.0)))
    return dict(key=key, dim=int(D.shape[1]), n_pool=len(pool_ids), n_queries=M,
                recall_at_10=round(r_at(10), 4), recall_at_20=round(r_at(20), 4),
                recall_at_100=round(r_at(100), 4), ndcg_at_10=round(ndcg, 4),
                mrr_at_100=round(mrr, 4))

def main():
    keys = sys.argv[1:] or sorted({os.path.basename(p).split(".doc.")[0]
                                   for p in glob.glob(f"{EMB}/*.doc.f16.npy")})
    pool_ids, gold_ids = load_ids()
    id2row = {cid: i for i, cid in enumerate(pool_ids)}
    rows = []
    for k in keys:
        r = score(k, pool_ids, gold_ids, id2row)
        rows.append(r)
        print(f"{r['key']:16} dim={r['dim']:5} r@10={r['recall_at_10']:.3f} "
              f"r@20={r['recall_at_20']:.3f} r@100={r['recall_at_100']:.3f} "
              f"nDCG@10={r['ndcg_at_10']:.3f} MRR={r['mrr_at_100']:.3f}", flush=True)
    rows.sort(key=lambda r: -r["recall_at_100"])
    json.dump(rows, open("unified_scores.json", "w"), indent=2)
    with open("unified_scores.md", "w") as f:
        f.write("# Unified local scores (one code path, from saved vectors)\n\n")
        f.write(f"Pool = {len(pool_ids)} chunks, {len(gold_ids)} queries, "
                "known-item single-gold retrieval.\n\n")
        f.write("| Model | dim | r@10 | r@20 | r@100 | nDCG@10 | MRR@100 |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|\n")
        for r in rows:
            f.write(f"| {r['key']} | {r['dim']} | {r['recall_at_10']:.3f} | "
                    f"{r['recall_at_20']:.3f} | {r['recall_at_100']:.3f} | "
                    f"{r['ndcg_at_10']:.3f} | {r['mrr_at_100']:.3f} |\n")
    print("\n-> wrote unified_scores.md + unified_scores.json")

if __name__ == "__main__":
    main()
