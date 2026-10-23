#!/usr/bin/env python3
"""Short-keyword search eval: BM25 vs dense vs hybrid-RRF on the REAL query distribution
(1-4 word queries), the thing the product actually serves.

- BM25: rank_bm25 over slim_pool, Dutch analyzer (lowercase, stopwords, snowball stem).
- Dense: bge-m3 (durable open default). Doc vectors from embeddings/bgem3.doc.f16.npy;
  keyword queries embedded fresh with bge-m3.
- Hybrid: Reciprocal Rank Fusion (RRF, k=60) of BM25 + dense.

Single-gold metrics first (fast). Saves per-query top-100 for each method so the pooled
multi-relevance pass (keyword_pooled.py) can score them artifact-free.

Outputs: keyword_single_gold.md + keyword_rankings.json (method -> per-query top-100 ids)
"""
import json, sys, re, time
import numpy as np

POOL = "slim_pool.jsonl"; QUERIES = "keyword_queries.jsonl"; EMB = "embeddings"
DENSE_KEY = "bgem3"

# --- Dutch analyzer (approximates ES dutch_analyzer) ---
import snowballstemmer
STEM = snowballstemmer.stemmer("dutch").stemWords
STOP = set("de het een en van te dat die in op met voor is als aan er om ook naar "
           "tot uit door over bij of maar dan wel niet zijn was worden wordt heeft "
           "hebben deze dit u we ze je ik hij zij het den der des".split())
def analyze(text):
    toks = re.findall(r"[a-zA-Z0-9À-ſ]+", text.lower())
    toks = [t for t in toks if t not in STOP and len(t) > 1]
    return STEM(toks)

def load():
    pool = [json.loads(l) for l in open(POOL)]
    pool_ids = [r["id"] for r in pool]
    id2row = {c: i for i, c in enumerate(pool_ids)}
    queries = [json.loads(l) for l in open(QUERIES)]
    return pool, pool_ids, id2row, queries

def main():
    pool, pool_ids, id2row, queries = load()
    gold_rows = np.array([id2row[q["gold_id"]] for q in queries])

    # --- BM25 index ---
    from rank_bm25 import BM25Okapi
    print("building BM25 index...", file=sys.stderr)
    t0 = time.time()
    corpus_tok = [analyze(r["text"]) for r in pool]
    bm25 = BM25Okapi(corpus_tok)
    print(f"BM25 index built in {time.time()-t0:.0f}s", file=sys.stderr)

    # --- dense: embed keyword queries with bge-m3, doc vectors from disk ---
    print("loading dense doc vectors + embedding queries...", file=sys.stderr)
    D = np.load(f"{EMB}/{DENSE_KEY}.doc.f16.npy").astype(np.float32)
    D /= (np.linalg.norm(D, axis=1, keepdims=True) + 1e-9)
    from sentence_transformers import SentenceTransformer
    import torch
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    st = SentenceTransformer("BAAI/bge-m3", device=dev)
    Q = st.encode([q["query"] for q in queries], normalize_embeddings=True,
                  convert_to_numpy=True, batch_size=64, show_progress_bar=False).astype(np.float32)

    K = 100
    bm25_rank, dense_rank, rrf_rank = [], [], []
    t1 = time.time()
    for i in range(len(queries)):
        # BM25 top-100
        scores = bm25.get_scores(analyze(queries[i]["query"]))
        b_top = np.argpartition(-scores, K)[:K]; b_top = b_top[np.argsort(-scores[b_top])]
        # dense top-100
        sims = D @ Q[i]
        d_top = np.argpartition(-sims, K)[:K]; d_top = d_top[np.argsort(-sims[d_top])]
        # RRF fusion (k=60) over the two ranked lists
        rrf = {}
        for r, doc in enumerate(b_top): rrf[doc] = rrf.get(doc, 0) + 1.0 / (60 + r)
        for r, doc in enumerate(d_top): rrf[doc] = rrf.get(doc, 0) + 1.0 / (60 + r)
        h_top = np.array(sorted(rrf, key=lambda d: -rrf[d])[:K])
        bm25_rank.append(b_top); dense_rank.append(d_top); rrf_rank.append(h_top)
        if (i+1) % 200 == 0:
            sys.stderr.write(f"retrieved {i+1}/{len(queries)} ({(i+1)/(time.time()-t1):.0f} q/s)\n")

    def metrics(ranks):
        r10 = r100 = ndcg = mrr = 0; n = len(ranks)
        for i, rk in enumerate(ranks):
            hit = np.where(np.asarray(rk) == gold_rows[i])[0]
            if len(hit):
                p = int(hit[0]) + 1
                r100 += 1; r10 += p <= 10
                if p <= 10: ndcg += 1.0/np.log2(p+1)
                mrr += 1.0/p
        return dict(recall_at_10=round(r10/n,3), recall_at_100=round(r100/n,3),
                    ndcg_at_10=round(ndcg/n,3), mrr_at_100=round(mrr/n,3))

    res = {"BM25": metrics(bm25_rank), "dense(bge-m3)": metrics(dense_rank),
           "hybrid-RRF": metrics(rrf_rank)}
    for k, v in res.items():
        print(f"{k:16} r@10={v['recall_at_10']:.3f} r@100={v['recall_at_100']:.3f} "
              f"nDCG@10={v['ndcg_at_10']:.3f} MRR={v['mrr_at_100']:.3f}", flush=True)

    # save rankings (ids) for the pooled pass
    rankings = {"BM25": [[pool_ids[int(x)] for x in r] for r in bm25_rank],
                "dense": [[pool_ids[int(x)] for x in r] for r in dense_rank],
                "hybrid": [[pool_ids[int(x)] for x in r] for r in rrf_rank],
                "queries": [q["query"] for q in queries],
                "gold_ids": [q["gold_id"] for q in queries]}
    json.dump(rankings, open("keyword_rankings.json", "w"))
    with open("keyword_single_gold.md", "w") as f:
        f.write("# Short-keyword search: BM25 vs dense vs hybrid (single-gold)\n\n")
        f.write(f"{len(queries)} keyword queries (1-4 words), pool {len(pool)}. "
                "Dense = bge-m3 (durable open default). RRF k=60.\n\n")
        f.write("| method | recall@10 | recall@100 | nDCG@10 | MRR@100 |\n|---|---:|---:|---:|---:|\n")
        for k, v in res.items():
            f.write(f"| {k} | {v['recall_at_10']:.3f} | {v['recall_at_100']:.3f} "
                    f"| {v['ndcg_at_10']:.3f} | {v['mrr_at_100']:.3f} |\n")
    print("\n-> wrote keyword_single_gold.md + keyword_rankings.json", flush=True)

if __name__ == "__main__":
    main()
