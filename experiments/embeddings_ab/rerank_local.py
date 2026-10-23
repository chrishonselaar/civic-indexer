#!/usr/bin/env python3
"""Reranking A/B: take a base embedder's top-100 dense candidates (from saved vectors),
rerank with an open multilingual cross-encoder, compare metrics before/after.

Key question: a cross-encoder can only REORDER the stage-1 top-K, so recall@100 is the
ceiling. It should lift recall@10 / nDCG@10 / MRR. Does cheap-embedder+rerank catch a
commercial embedder?

Usage: python rerank_local.py <base_key> [--n N] [--topk 100]
Writes: rerank_<base_key>.json  (appended into rerank_results.md)
"""
import json, sys, time, argparse
import numpy as np
import torch
from sentence_transformers import CrossEncoder

POOL = "slim_pool.jsonl"; QUERIES = "queries.jsonl"; EMB = "embeddings"
RERANKER = "BAAI/bge-reranker-v2-m3"

def metrics(ranks, M):
    ranks = np.array([r if r else 10**9 for r in ranks])
    def r_at(k): return float(np.mean(ranks <= k))
    ndcg = float(np.mean(np.where(ranks <= 10, 1.0 / np.log2(ranks + 1), 0.0)))
    mrr = float(np.mean(1.0 / ranks))
    return dict(recall_at_10=round(r_at(10), 4), recall_at_20=round(r_at(20), 4),
                recall_at_100=round(r_at(100), 4), ndcg_at_10=round(ndcg, 4),
                mrr_at_100=round(mrr, 4))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--topk", type=int, default=100)
    a = ap.parse_args()
    K = a.topk

    pool = [json.loads(l) for l in open(POOL)]
    id2text = {r["id"]: r["text"] for r in pool}
    pool_ids = [r["id"] for r in pool]
    queries = [json.loads(l) for l in open(QUERIES)]
    if a.n:
        queries = queries[:a.n]
    id2row = {c: i for i, c in enumerate(pool_ids)}

    D = np.load(f"{EMB}/{a.base}.doc.f16.npy").astype(np.float32)
    Q = np.load(f"{EMB}/{a.base}.q.f16.npy").astype(np.float32)[:len(queries)]
    D /= (np.linalg.norm(D, axis=1, keepdims=True) + 1e-9)
    Q /= (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-9)

    # stage-1: top-K dense candidates (ordered) per query
    cand, stage1_ranks = [], []
    for i in range(len(queries)):
        sims = D @ Q[i]
        top = np.argpartition(-sims, K)[:K]
        top = top[np.argsort(-sims[top])]
        cand.append(top)
        gr = id2row[queries[i]["gold_id"]]
        hit = np.where(top == gr)[0]
        stage1_ranks.append(int(hit[0]) + 1 if len(hit) else None)

    # rerank with cross-encoder
    ce = CrossEncoder(RERANKER, max_length=512, device="cuda",
                      model_kwargs={"torch_dtype": torch.float16})
    t0 = time.time()
    reranked_ranks = []
    for i in range(len(queries)):
        q = queries[i]["query"]
        rows = cand[i]
        pairs = [[q, id2text[pool_ids[r]][:2000]] for r in rows]
        scores = ce.predict(pairs, batch_size=128, show_progress_bar=False)
        order = rows[np.argsort(-scores)]
        gr = id2row[queries[i]["gold_id"]]
        hit = np.where(order == gr)[0]
        reranked_ranks.append(int(hit[0]) + 1 if len(hit) else None)
        if (i + 1) % 250 == 0:
            sys.stderr.write(f"[{a.base}] reranked {i+1}/{len(queries)} "
                             f"({(i+1)/(time.time()-t0):.1f} q/s)\n")
    dt = time.time() - t0

    M = len(queries)
    before = metrics(stage1_ranks, M)
    after = metrics(reranked_ranks, M)
    res = dict(base=a.base, reranker=RERANKER, n_queries=M, topk=K,
               rerank_s=round(dt, 1), stage1=before, reranked=after)
    print(json.dumps(res, indent=2), flush=True)
    json.dump(res, open(f"rerank_{a.base}.json", "w"), indent=2)
    print(f"\n{a.base}:  r@10 {before['recall_at_10']:.3f} -> {after['recall_at_10']:.3f}   "
          f"nDCG@10 {before['ndcg_at_10']:.3f} -> {after['ndcg_at_10']:.3f}   "
          f"MRR {before['mrr_at_100']:.3f} -> {after['mrr_at_100']:.3f}", flush=True)

if __name__ == "__main__":
    main()
