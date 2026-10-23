#!/usr/bin/env python3
"""Score the reranker A/B against pooled multi-relevance qrels (local, no GPU).

Methods scored per base: stage-1 dense (no rerank) + every rerank_out_<base>_<reranker>.json.
Metrics per method: success@10, nDCG@10 (pooled graded), recall@10 (grade>=1, pool-limited),
and single-gold nDCG@10 for continuity with rerank_results.md. All over the same judged
query set (pooled_qrels_v2.json).

Writes: rerank_pooled_results.md + rerank_pooled_results.json
"""
import json, glob, sys
from collections import defaultdict
import numpy as np

QRELS = "pooled_qrels_v2.json"; QUERIES = "queries.jsonl"

def dcg(grades):
    return sum((2**g - 1) / np.log2(i + 2) for i, g in enumerate(grades))

def main():
    qrels = json.load(open(QRELS))                       # {qidx: {id: grade}}
    # qrels keys = LINE POSITION in queries.jsonl (NOT the cyclic "qidx" field)
    gold = {str(i): json.loads(l)["gold_id"] for i, l in enumerate(open(QUERIES))}
    qkeys = list(qrels)

    # collect rankings: method -> {qidx: [ids]}
    methods = {}
    for f in glob.glob("rerank_input_*.json"):
        base = f[len("rerank_input_"):-len(".json")]
        methods[f"{base} · stage-1 (no rerank)"] = {
            str(it["qidx"]): [c["id"] for c in it["cands"]] for it in json.load(open(f))}
    for f in glob.glob("rerank_out_*.json"):
        name = f[len("rerank_out_"):-len(".json")]        # <base>_<reranker>
        base, rr = name.split("_", 1)
        methods[f"{base} + {rr}"] = json.load(open(f))

    rows = []
    for name, ranking in methods.items():
        succ = ndcg = recall = sg = 0; n = 0
        for qk in qkeys:
            if qk not in ranking:
                continue
            rel = qrels[qk]                                # id -> grade
            relset = {i for i, g in rel.items() if g >= 1}
            if not relset:
                continue
            n += 1
            top10 = ranking[qk][:10]
            succ += bool(set(top10) & relset)
            grades10 = [rel.get(i, 0) for i in top10]
            ideal = sorted(rel.values(), reverse=True)[:10]
            ndcg += dcg(grades10) / (dcg(ideal) or 1e-9)
            recall += len(set(top10) & relset) / len(relset)
            gid = gold[qk]
            r = ranking[qk].index(gid) if gid in ranking[qk] else 10**9
            sg += 1.0 / np.log2(r + 2) if r < 10 else 0.0
        rows.append(dict(method=name, n=n,
                         success_at_10=round(succ/n, 3), ndcg_at_10=round(ndcg/n, 3),
                         recall_at_10=round(recall/n, 3), ndcg_singlegold=round(sg/n, 3)))

    rows.sort(key=lambda r: -r["ndcg_at_10"])
    json.dump(rows, open("rerank_pooled_results.json", "w"), indent=2)
    with open("rerank_pooled_results.md", "w") as f:
        f.write("# Reranker A/B — pooled multi-relevance scoring\n\n")
        f.write(f"n={rows[0]['n']} judged queries. nDCG@10 & recall@10 use pooled graded "
                "relevance (0/1/2); success@10 = any grade>=1 in top-10. Sorted by pooled "
                "nDCG@10.\n\n")
        f.write("| method | success@10 | nDCG@10 (pooled) | recall@10 | nDCG@10 (single-gold) |\n")
        f.write("|---|---:|---:|---:|---:|\n")
        for r in rows:
            f.write(f"| {r['method']} | {r['success_at_10']:.3f} | {r['ndcg_at_10']:.3f} "
                    f"| {r['recall_at_10']:.3f} | {r['ndcg_singlegold']:.3f} |\n")
    for r in rows:
        print(json.dumps(r), flush=True)

if __name__ == "__main__":
    main()
