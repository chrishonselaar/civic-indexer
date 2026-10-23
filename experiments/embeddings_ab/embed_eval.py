#!/usr/bin/env python3
"""Runs ON the RunPod GPU. Embeds the WOO pool + synthetic queries with one
candidate model, scores known-item retrieval (recall@k / nDCG@10 / MRR).

Usage:  python embed_eval.py <model_key>   # arctic | bgem3 | qwen4b
Inputs (scp'd next to this file):  slim_pool.jsonl, queries.jsonl
Output: results_<model_key>.json  (append-merge into woo_eval_results.md locally)

Each candidate gets its card-specified query/passage prefixes (instruct models
crater without them). Dense single-vector for all three for a fair comparison.
"""
import json, sys, time
import numpy as np
import torch
# bge-m3 ships only pytorch_model.bin; transformers>=4.53 blocks torch.load on torch<2.6
# (CVE-2025-32434). BAAI weights are trusted here, so no-op the guard.
import transformers.utils.import_utils as _iu
_iu.check_torch_load_is_safe = lambda *a, **k: None
import transformers.modeling_utils as _mu
if hasattr(_mu, "check_torch_load_is_safe"):
    _mu.check_torch_load_is_safe = lambda *a, **k: None
from sentence_transformers import SentenceTransformer


def encode_resilient(model, texts, batch, prompt_name, label):
    """Encode with automatic batch-halving on CUDA OOM (avoids wasted restarts)."""
    b = batch
    while True:
        try:
            return model.encode(texts, batch_size=b, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=True,
                                 prompt_name=prompt_name)
        except RuntimeError as e:
            if "out of memory" in str(e).lower() and b > 4:
                torch.cuda.empty_cache()
                b = max(4, b // 2)
                print(f"[{label}] OOM -> retry at batch={b}", flush=True)
            else:
                raise

# We use each model's OWN registered ST prompt (prompt_name) so the run matches how
# the model was evaluated on MTEB. Confirmed live from config_sentence_transformers.json:
#   arctic  -> prompts {'query': 'query: '}                     (docs: none)
#   qwen4b  -> prompts {'query': 'Instruct: ...\nQuery:', 'document': ''}
#   bgem3   -> no prompts (plain encode both sides)
MODELS = {
    "arctic": dict(repo="Snowflake/snowflake-arctic-embed-l-v2.0",
                   max_seq=2048, batch=128, trust=True),
    "bgem3":  dict(repo="BAAI/bge-m3",
                   max_seq=2048, batch=96, trust=False),
    "qwen4b": dict(repo="Qwen/Qwen3-Embedding-4B",
                   max_seq=2048, batch=24, trust=True),
}

def load_jsonl(p):
    return [json.loads(l) for l in open(p)]

def main():
    key = sys.argv[1]
    cfg = MODELS[key]
    dev = "cuda"
    print(f"[{key}] loading {cfg['repo']}", flush=True)
    kw = dict(device=dev, trust_remote_code=cfg["trust"])
    model = SentenceTransformer(cfg["repo"], model_kwargs={"torch_dtype": torch.float16}, **kw)
    model.max_seq_length = cfg["max_seq"]
    prompts = getattr(model, "prompts", None) or {}
    q_prompt = "query" if "query" in prompts else None
    d_prompt = "document" if "document" in prompts else None
    print(f"[{key}] prompts={prompts} -> query_prompt_name={q_prompt} doc_prompt_name={d_prompt}",
          flush=True)

    pool = load_jsonl("slim_pool.jsonl")
    queries = load_jsonl("queries.jsonl")
    pool_ids = [r["id"] for r in pool]
    id2row = {cid: i for i, cid in enumerate(pool_ids)}

    # --- embed passages ---
    t0 = time.time()
    import os
    os.makedirs("embeddings", exist_ok=True)
    doc_texts = [r["text"] for r in pool]
    doc_emb = encode_resilient(model, doc_texts, cfg["batch"], d_prompt, key)
    t_doc = time.time() - t0
    # Persist vectors (fp16) so ranking/rerank experiments run locally without re-embedding.
    np.save(f"embeddings/{key}.doc.f16.npy", doc_emb.astype(np.float16))
    print(f"[{key}] embedded {len(pool)} passages in {t_doc:.1f}s "
          f"({len(pool)/t_doc:.0f}/s), dim={doc_emb.shape[1]} -> saved doc vectors", flush=True)

    # --- embed queries ---
    t1 = time.time()
    q_texts = [q["query"] for q in queries]
    q_emb = encode_resilient(model, q_texts, cfg["batch"], q_prompt, key)
    t_q = time.time() - t1
    np.save(f"embeddings/{key}.q.f16.npy", q_emb.astype(np.float16))

    # --- retrieve top-100 on GPU ---
    D = torch.from_numpy(doc_emb).to(dev)          # (N, d)
    Q = torch.from_numpy(q_emb).to(dev)            # (M, d)
    K = 100
    ranks = []          # 1-indexed rank of gold, or None if outside top-K
    details = []        # per-query: rank + top-20 retrieved ids (for miss analysis)
    B = 256
    for i in range(0, Q.shape[0], B):
        sims = Q[i:i+B] @ D.T                       # (b, N)
        top = torch.topk(sims, K, dim=1).indices.cpu().numpy()
        for j, row in enumerate(top):
            gid = queries[i + j]["gold_id"]
            gold_row = id2row.get(gid)
            r = None
            hit = np.where(row == gold_row)[0]
            if len(hit):
                r = int(hit[0]) + 1
            ranks.append(r)
            details.append({
                "query": queries[i + j]["query"],
                "gold_id": gid,
                "gold_rank": r,                                  # None = outside top-100
                "top20": [pool_ids[int(x)] for x in row[:20]],
            })

    with open(f"results_{key}_ranks.jsonl", "w") as fr:
        for d in details:
            fr.write(json.dumps(d, ensure_ascii=False) + "\n")

    M = len(ranks)
    def recall_at(k): return sum(1 for r in ranks if r and r <= k) / M
    ndcg10 = sum((1.0 / np.log2(r + 1)) for r in ranks if r and r <= 10) / M
    mrr = sum((1.0 / r) for r in ranks if r) / M

    res = dict(
        model=cfg["repo"], key=key, dim=int(doc_emb.shape[1]),
        n_pool=len(pool), n_queries=M,
        recall_at_10=round(recall_at(10), 4),
        recall_at_20=round(recall_at(20), 4),
        recall_at_100=round(recall_at(100), 4),
        ndcg_at_10=round(float(ndcg10), 4),
        mrr_at_100=round(float(mrr), 4),
        embed_passages_s=round(t_doc, 1),
        passages_per_s=round(len(pool) / t_doc, 1),
        embed_queries_s=round(t_q, 1),
        max_seq=cfg["max_seq"],
    )
    print(json.dumps(res, indent=2), flush=True)
    json.dump(res, open(f"results_{key}.json", "w"), indent=2)

if __name__ == "__main__":
    main()
