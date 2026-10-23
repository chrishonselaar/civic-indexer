#!/usr/bin/env python3
"""Voyage API arm of the WOO embedding eval — identical pool/queries/metrics to the
GPU arm (embed_eval.py), so numbers are directly comparable.

Usage: python voyage_eval.py voyage-law-2 [--limit-pool N]
Outputs: results_<model>.json  +  results_<model>_ranks.jsonl

Uses input_type="document"/"query" (Voyage's asymmetric embedding), 1024-dim to
match arctic/bge-m3. Inputs truncated to ~8000 chars (~2048 tokens) to mirror the
GPU arm's max_seq=2048. Batches by token budget with retry/backoff on rate limits.
"""
import json, sys, time, argparse, threading
import numpy as np
import voyageai

KEY_ENV = "/home/chris/claude-projects/civic-indexer/.env"
POOL = "slim_pool.jsonl"
QUERIES = "queries.jsonl"
CHAR_CAP = 8000          # ~2048 tokens, parity with GPU arm
BATCH_TEXTS = 32         # texts/request; token cap also enforced below
TOKEN_CAP = 50_000       # conservative estimate to stay under per-request limits
WORKERS = 4
TARGET_TPM = 2_400_000   # stay safely under the 3M tokens-per-minute account limit


class RateLimiter:
    """Global token-bucket so concurrent workers don't blow the TPM limit.

    Capacity must exceed the largest single request or consume() deadlocks — so
    the bucket holds a few seconds of budget (>= max request size)."""
    def __init__(self, tpm, max_req):
        self.rate = tpm / 60.0                       # tokens per second
        self.capacity = max(self.rate * 3, max_req * 1.5)
        self.allowance = self.capacity
        self.last = time.time()
        self.lock = threading.Lock()
    def consume(self, tokens):
        tokens = min(tokens, self.capacity)          # safety: never ask > capacity
        while True:
            with self.lock:
                now = time.time()
                self.allowance = min(self.capacity, self.allowance + (now - self.last) * self.rate)
                self.last = now
                if self.allowance >= tokens:
                    self.allowance -= tokens
                    return
                wait = (tokens - self.allowance) / self.rate
            time.sleep(min(wait, 5))

limiter = RateLimiter(TARGET_TPM, TOKEN_CAP)

def load_key():
    for line in open(KEY_ENV):
        if line.strip().startswith("VOYAGE_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no VOYAGE_API_KEY")

vo = voyageai.Client(api_key=load_key())

def batches(texts):
    """Yield (start_idx, chunk) batches bounded by count and rough token budget."""
    i = 0
    while i < len(texts):
        j = i
        tok = 0
        while j < len(texts) and (j - i) < BATCH_TEXTS:
            tok += len(texts[j]) // 3 + 8      # conservative token estimate
            if tok > TOKEN_CAP and j > i:
                break
            j += 1
        yield i, texts[i:j]
        i = j

def embed_all(texts, input_type, label):
    """Embed all texts, preserving order. Threaded with retry/backoff."""
    out = [None] * len(texts)
    jobs = list(batches(texts))
    done = [0]
    lock = threading.Lock()
    def embed_span(start, chunk, depth=0):
        """Embed one span; on an oversized-batch error, split and recurse."""
        for attempt in range(12):
            try:
                limiter.consume(sum(len(t) // 3 + 8 for t in chunk))
                r = vo.embed(chunk, model=MODEL, input_type=input_type,
                             output_dimension=1024, truncation=True)
                for k, e in enumerate(r.embeddings):
                    out[start + k] = e
                with lock:
                    done[0] += len(chunk)
                    if done[0] % 4800 < len(chunk):
                        sys.stderr.write(f"[{label}] {done[0]}/{len(texts)}\n")
                return
            except Exception as e:
                msg = str(e)
                oversized = "max allowed tokens" in msg or "InvalidRequest" in type(e).__name__
                if oversized and len(chunk) > 1:
                    mid = len(chunk) // 2
                    embed_span(start, chunk[:mid], depth + 1)
                    embed_span(start + mid, chunk[mid:], depth + 1)
                    return
                wait = min(30, 2 ** attempt)
                sys.stderr.write(f"[{label}] {type(e).__name__}: {msg[:80]} -> retry in {wait}s\n")
                time.sleep(wait)
        raise SystemExit(f"[{label}] batch at {start} failed after retries")
    def run(job):
        embed_span(job[0], job[1])
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(run, jobs))
    return np.array(out, dtype=np.float32)

def main():
    global MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--limit-pool", type=int, default=0)
    a = ap.parse_args()
    MODEL = a.model
    key = MODEL

    pool = [json.loads(l) for l in open(POOL)]
    if a.limit_pool:
        pool = pool[:a.limit_pool]
    queries = [json.loads(l) for l in open(QUERIES)]
    pool_ids = [r["id"] for r in pool]
    id2row = {cid: i for i, cid in enumerate(pool_ids)}

    import os
    os.makedirs("embeddings", exist_ok=True)
    t0 = time.time()
    doc_emb = embed_all([r["text"][:CHAR_CAP] for r in pool], "document", key + ":doc")
    t_doc = time.time() - t0
    np.save(f"embeddings/{key}.doc.f16.npy", doc_emb.astype(np.float16))
    print(f"[{key}] embedded {len(pool)} passages in {t_doc:.1f}s, dim={doc_emb.shape[1]}"
          " -> saved doc vectors", flush=True)
    t1 = time.time()
    q_emb = embed_all([q["query"][:CHAR_CAP] for q in queries], "query", key + ":q")
    t_q = time.time() - t1
    np.save(f"embeddings/{key}.q.f16.npy", q_emb.astype(np.float16))

    # normalize (Voyage returns unit vectors, but be safe) and score on CPU
    doc_emb /= (np.linalg.norm(doc_emb, axis=1, keepdims=True) + 1e-9)
    q_emb /= (np.linalg.norm(q_emb, axis=1, keepdims=True) + 1e-9)

    K = 100
    ranks, details = [], []
    for i in range(q_emb.shape[0]):
        sims = doc_emb @ q_emb[i]
        top = np.argpartition(-sims, K)[:K]
        top = top[np.argsort(-sims[top])]
        gid = queries[i]["gold_id"]
        gr = id2row.get(gid)
        r = None
        hit = np.where(top == gr)[0]
        if len(hit):
            r = int(hit[0]) + 1
        ranks.append(r)
        details.append({"query": queries[i]["query"], "gold_id": gid, "gold_rank": r,
                        "top20": [pool_ids[int(x)] for x in top[:20]]})

    with open(f"results_{key}_ranks.jsonl", "w") as fr:
        for d in details:
            fr.write(json.dumps(d, ensure_ascii=False) + "\n")

    M = len(ranks)
    def recall_at(k): return sum(1 for r in ranks if r and r <= k) / M
    ndcg10 = sum((1.0 / np.log2(r + 1)) for r in ranks if r and r <= 10) / M
    mrr = sum((1.0 / r) for r in ranks if r) / M
    res = dict(model=MODEL, key=key, dim=int(doc_emb.shape[1]), n_pool=len(pool),
               n_queries=M, recall_at_10=round(recall_at(10), 4),
               recall_at_20=round(recall_at(20), 4), recall_at_100=round(recall_at(100), 4),
               ndcg_at_10=round(float(ndcg10), 4), mrr_at_100=round(float(mrr), 4),
               embed_passages_s=round(t_doc, 1), embed_queries_s=round(t_q, 1),
               api="voyage")
    print(json.dumps(res, indent=2), flush=True)
    json.dump(res, open(f"results_{key}.json", "w"), indent=2)

if __name__ == "__main__":
    main()
