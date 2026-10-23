#!/usr/bin/env python3
"""API reranker runner (local, no GPU) — Voyage or Cohere over stage-1 candidates.

Reads rerank_input_<base>.json, reranks each query's 100 candidates via a managed rerank
API, writes the reordered candidate-id list per query.

Usage: python rerank_api.py <base> <provider> [model]
       provider = voyage | cohere | jina;  model overrides the default for that provider
Writes: rerank_out_<base>_<tag>.json = {qidx: [ordered candidate ids]}
        tag = provider, or provider-<model> when a model override is given, so two
        generations of the same vendor can sit side by side in one scored table.
"""
import json, os, sys, time, threading
from concurrent.futures import ThreadPoolExecutor

ENV = "/home/chris/claude-projects/civic-indexer/.env"
VOYAGE_MODEL = "rerank-2.5"
COHERE_MODEL = "rerank-v3.5"
JINA_MODEL = "jina-reranker-v3"
JINA_URL = "https://api.jina.ai/v1/rerank"
WORKERS = 4
# min seconds between request starts (paces under provider limits). Voyage rerank-2.5 is
# TPM-limited (2M tok/min; ~50k tok/query -> ~40 q/min) -> 1.8s. Cohere is request-limited.
PACE = {"voyage": 1.8, "cohere": 0.8, "jina": 0.5}

def load_env():
    for l in open(ENV):
        if "=" in l and not l.strip().startswith("#"):
            k, v = l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def main():
    base, provider = sys.argv[1], sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else None
    tag = provider if not model else f"{provider}-{model.replace('.', '').replace('_', '-')}"
    load_env()
    data = json.load(open(f"rerank_input_{base}.json"))

    if provider == "voyage":
        import voyageai
        client = voyageai.Client()
        def rerank(query, texts):
            r = client.rerank(query=query, documents=texts, model=model or VOYAGE_MODEL)
            return [x.index for x in r.results]
    elif provider == "cohere":
        import cohere
        client = cohere.ClientV2(api_key=os.environ["COHERE_API_KEY"])
        def rerank(query, texts):
            r = client.rerank(model=model or COHERE_MODEL, query=query, documents=texts)
            return [x.index for x in r.results]
    elif provider == "jina":
        # Plain HTTP: the jina SDK is not a dependency here and the endpoint is one POST.
        import urllib.request
        key = os.environ["JINA_API_KEY"]
        def rerank(query, texts):
            body = json.dumps({"model": model or JINA_MODEL, "query": query,
                               "documents": texts, "top_n": len(texts)}).encode()
            req = urllib.request.Request(
                JINA_URL, data=body, method="POST",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.load(r)
            return [x["index"] for x in res["results"]]
    else:
        raise SystemExit(f"unknown provider {provider}")

    # Pre-flight: one real call before launching 250. The retry loop below deliberately
    # never gives up on a rate limit (falling back would corrupt the A/B), which means a
    # key that is not ENTITLED to a model — Cohere answers 429 for those, indistinguishable
    # from throttling — stalls the whole run silently. Measured 2026-07-27: a trial key
    # returns 429 for rerank-v4.0-fast/pro while rerank-v3.5 succeeds in the same second.
    # Three seconds of pre-flight buys a clear error instead of a fifteen-minute stall.
    try:
        rerank(data[0]["query"], [c["text"] for c in data[0]["cands"]][:8])
    except Exception as e:
        raise SystemExit(f"pre-flight FAILED for {provider}/{model or 'default'}: "
                         f"{type(e).__name__}: {str(e)[:200]}")

    out = {}; fallbacks = []
    pace = PACE[provider]; last = [0.0]; pace_lock = threading.Lock()
    def gate():                                    # space out request starts
        with pace_lock:
            wait = last[0] + pace - time.time()
            if wait > 0: time.sleep(wait)
            last[0] = time.time()

    lock = threading.Lock(); done = [0]; t0 = time.time()
    def run(item):
        texts = [c["text"] for c in item["cands"]]
        ids = None
        for attempt in range(40):
            gate()
            try:
                order = rerank(item["query"], texts)
                ids = [item["cands"][i]["id"] for i in order]
                break
            except Exception as e:
                msg = str(e).lower()
                rate = "rate" in msg or "429" in msg or "tpm" in msg or "quota" in msg
                # NEVER fall back on rate limits — wait it out (would corrupt the A/B)
                time.sleep(30 if rate else min(5 * (attempt + 1), 30))
        if ids is None:                            # only non-rate failures reach here
            sys.stderr.write(f"FALLBACK q{item['qidx']} -> stage-1 order\n")
            ids = [c["id"] for c in item["cands"]]
            fallbacks.append(item["qidx"])
        with lock:
            out[str(item["qidx"])] = ids
            done[0] += 1
            if done[0] % 25 == 0:
                sys.stderr.write(f"[{base}/{tag}] {done[0]}/{len(data)} "
                                 f"({done[0]/(time.time()-t0):.2f} q/s)\n")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(run, data))

    path = f"rerank_out_{base}_{tag}.json"
    json.dump(out, open(path, "w"))
    tag = f"  WARNING {len(fallbacks)} fallbacks: {fallbacks}" if fallbacks else ""
    print(f"wrote {path} ({len(out)} queries, {time.time()-t0:.0f}s){tag}")

if __name__ == "__main__":
    main()
