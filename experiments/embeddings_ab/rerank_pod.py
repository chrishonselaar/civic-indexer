#!/usr/bin/env python3
"""GPU reranker runner for open-weights cross-encoders (runs on a RunPod pod).

Reads rerank_input_<base>.json (shipped from the laptop), reranks each query's candidates
with an open reranker, writes rerank_out_<base>_<reranker>.json in the SAME format as the
API runner so rerank_score.py treats every method uniformly.

Backends:
  bge-v2-m3   BAAI/bge-reranker-v2-m3            (568M, CrossEncoder)   [small / CPU-servable]
  jina-v2     jinaai/jina-reranker-v2-base-multilingual (278M, seq-cls) [small / CPU-servable]
  qwen3-0.6b  Qwen/Qwen3-Reranker-0.6B           (causal LM yes/no)     [mid]
  qwen3-4b    Qwen/Qwen3-Reranker-4B             (causal LM yes/no)     [ceiling anchor]
  mxbai-v2    mixedbread-ai/mxbai-rerank-large-v2 (2B, mxbai-rerank pkg)[ceiling anchor]

Usage: python rerank_pod.py <base> <backend>
"""
import json, os, sys, time
import numpy as np

MAXLEN = 1024        # candidate text is pre-truncated to 2000 chars (~500 tok) in prep
# A 2B/4B reranker at 1024 tokens OOMs a 24 GB card at 64; override per model rather
# than lowering it for the 568M cross-encoder, which is 8x faster at the larger batch.
BATCH = int(os.environ.get("RERANK_BATCH", "64"))
T0 = time.time()


def tick(i, n, every=10):
    """Progress to stdout. Absent until 2026-07-27, which twice made 'how far along is
    it?' unanswerable — once while a chain had silently died, once during an 85-minute
    run with no way to bound the remainder from above."""
    if (i + 1) % every and i + 1 != n:
        return
    done, el = i + 1, time.time() - T0
    print(f"  {done}/{n} q  {el:.0f}s  {done/el:.3f} q/s  eta {(n-done)*el/done:.0f}s",
          flush=True)

def score_crossencoder(model_id, queries, cands_per_q):
    import torch
    from sentence_transformers import CrossEncoder
    # sentence-transformers renamed the passthrough kwarg between 3.x and 4.x, and the
    # 3.4.1 pin this repo uses elsewhere (for the embed workflow) predates `model_kwargs`.
    # Try newest first, fall back, and never let fp16 be the reason a run does not start.
    fp16 = {"torch_dtype": torch.float16}
    for kwargs in ({"model_kwargs": fp16}, {"automodel_args": fp16}, {}):
        try:
            ce = CrossEncoder(model_id, max_length=MAXLEN, device="cuda",
                              trust_remote_code=True, **kwargs)
            break
        except TypeError:
            continue
    orders = []
    for i, (q, cands) in enumerate(zip(queries, cands_per_q)):
        pairs = [[q, c["text"]] for c in cands]
        s = ce.predict(pairs, batch_size=BATCH, show_progress_bar=False)
        orders.append(list(np.argsort(-np.asarray(s))))
        tick(i, len(queries))
    return orders

def score_qwen3(model_id, queries, cands_per_q):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    tok = AutoTokenizer.from_pretrained(model_id, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=torch.float16, device_map="cuda").eval()
    yes_id = tok.convert_tokens_to_ids("yes"); no_id = tok.convert_tokens_to_ids("no")
    PRE = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the "
           "Query and the Instruct provided. Note that the answer can only be \"yes\" or \"no\"."
           "<|im_end|>\n<|im_start|>user\n")
    POST = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    INSTR = "Given a Dutch search query, retrieve passages that answer the query"
    orders = []
    for q, cands in zip(queries, cands_per_q):
        scores = []
        prompts = [f"{PRE}<Instruct>: {INSTR}\n<Query>: {q}\n<Document>: {c['text']}{POST}"
                   for c in cands]
        for b in range(0, len(prompts), BATCH):
            batch = prompts[b:b+BATCH]
            inp = tok(batch, return_tensors="pt", padding=True, truncation=True,
                      max_length=MAXLEN + 256).to("cuda")
            with torch.no_grad():
                logits = model(**inp).logits[:, -1, :]
            pair = torch.stack([logits[:, no_id], logits[:, yes_id]], dim=1)
            p = torch.nn.functional.log_softmax(pair, dim=1)[:, 1].exp()
            scores.extend(p.float().cpu().tolist())
        orders.append(list(np.argsort(-np.asarray(scores))))
        tick(len(orders) - 1, len(queries))
    return orders

def score_mxbai(model_id, queries, cands_per_q):
    from mxbai_rerank import MxbaiRerankV2
    # The constructor forwards unknown kwargs into the HF model, so batch_size belongs on
    # rank(), not here — passing it up front dies with a Qwen2ForCausalLM TypeError.
    m = MxbaiRerankV2(model_id, max_length=MAXLEN)
    orders = []
    for q, cands in zip(queries, cands_per_q):
        docs = [c["text"] for c in cands]
        try:
            res = m.rank(q, docs, return_documents=False, top_k=len(docs), batch_size=BATCH)
        except TypeError:
            res = m.rank(q, docs, return_documents=False, top_k=len(docs))
        orders.append([r.index for r in res])
        tick(len(orders) - 1, len(queries))
    return orders


def score_listwise(model_id, queries, cands_per_q):
    """jina-reranker-v3 and friends: the whole candidate list goes into one context
    ('last but not late interaction'), so there is no (query, doc) pair to batch."""
    import torch
    from transformers import AutoModel
    m = AutoModel.from_pretrained(model_id, torch_dtype=torch.float16,
                                  trust_remote_code=True).eval().cuda()
    orders = []
    for i, (q, cands) in enumerate(zip(queries, cands_per_q)):
        docs = [c["text"] for c in cands]
        # Signature varies by release; the listwise models take no max_length (the whole
        # list shares one context window, so truncation is the model's business).
        try:
            res = m.rerank(q, docs, top_n=len(docs))
        except TypeError:
            res = m.rerank(q, docs)
        # Accepts either dicts with 'index'/'corpus_id' or objects with .index.
        orders.append([r.get("index", r.get("corpus_id")) if isinstance(r, dict) else r.index
                       for r in res])
        tick(i, len(queries))
    return orders

BACKENDS = {
    "bge-v2-m3":  (score_crossencoder, "BAAI/bge-reranker-v2-m3"),
    "jina-v2":    (score_crossencoder, "jinaai/jina-reranker-v2-base-multilingual"),
    "qwen3-0.6b": (score_qwen3, "Qwen/Qwen3-Reranker-0.6B"),
    "qwen3-4b":   (score_qwen3, "Qwen/Qwen3-Reranker-4B"),
    "mxbai-v2":   (score_mxbai, "mixedbread-ai/mxbai-rerank-large-v2"),
    # Added 2026-07-27 — the current generation, which the first pass missed entirely.
    "zerank-2":   (score_crossencoder, "zeroentropy/zerank-2-reranker"),  # 4B, Qwen3-4B tune
    "jina-v3":    (score_listwise, "jinaai/jina-reranker-v3"),            # 0.6B, listwise
}

def main():
    base, backend = sys.argv[1], sys.argv[2]
    fn, model_id = BACKENDS[backend]
    data = json.load(open(f"rerank_input_{base}.json"))
    queries = [d["query"] for d in data]
    cands_per_q = [d["cands"] for d in data]
    t0 = time.time()
    orders = fn(model_id, queries, cands_per_q)
    out = {str(data[i]["qidx"]): [cands_per_q[i][j]["id"] for j in orders[i]]
           for i in range(len(data))}
    path = f"rerank_out_{base}_{backend}.json"
    json.dump(out, open(path, "w"))
    print(f"wrote {path} ({len(out)} q, {time.time()-t0:.0f}s, "
          f"{len(out)/(time.time()-t0):.2f} q/s)")

if __name__ == "__main__":
    main()
