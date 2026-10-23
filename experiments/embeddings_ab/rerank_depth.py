#!/usr/bin/env python3
"""How deep does reranking actually have to go? (local, no API, no GPU)

The production path reranks the fused top-100, and every cost/latency figure for every
reranker option scales linearly with that number — API price, GPU seconds, cold-start
budget. If depth 20 scores like depth 100, the whole vendor question gets ~5x cheaper
before anyone is chosen.

No new inference is needed to answer it. A cross-encoder scores each (query, document)
pair INDEPENDENTLY, so the ranking it would have produced over the stage-1 top-K is
exactly its ranking over the top-100 restricted to those K. Simulating depth K is
therefore a pure re-ordering of artefacts already on disk:

    rerank the stage-1 top-K   -> those K in reranker order
    leave the tail alone       -> ranks K+1.. in stage-1 order

Scored with the same pooled multi-relevance qrels and the same nDCG formula as
`rerank_score.py`, so the numbers drop straight into that table.

Writes: rerank_depth_results.md + rerank_depth_results.json
"""
import glob
import json

import numpy as np

QRELS = "pooled_qrels_v2.json"
QUERIES = "queries.jsonl"
DEPTHS = [5, 10, 20, 30, 50, 100]


def dcg(grades):
    return sum((2**g - 1) / np.log2(i + 2) for i, g in enumerate(grades))


def truncated(stage1: list[str], reranked: list[str], k: int) -> list[str]:
    """The ranking a depth-k rerank would have produced."""
    head = set(stage1[:k])
    kept = [d for d in reranked if d in head]          # reranker order, restricted
    return kept + [d for d in stage1 if d not in head]  # untouched tail, stage-1 order


def score(ranking: dict[str, list[str]], qrels: dict, gold: dict) -> dict:
    succ = ndcg = recall = sg = 0.0
    n = 0
    for qk, rel in qrels.items():
        if qk not in ranking:
            continue
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
    return dict(n=n, success_at_10=round(succ / n, 3), ndcg_at_10=round(ndcg / n, 3),
                recall_at_10=round(recall / n, 3), ndcg_singlegold=round(sg / n, 3))


def main():
    qrels = json.load(open(QRELS))
    gold = {str(i): json.loads(l)["gold_id"] for i, l in enumerate(open(QUERIES))}

    stage1 = {}
    for f in glob.glob("rerank_input_*.json"):
        base = f[len("rerank_input_"):-len(".json")]
        stage1[base] = {str(it["qidx"]): [c["id"] for c in it["cands"]]
                        for it in json.load(open(f))}

    rows = []
    for f in sorted(glob.glob("rerank_out_*.json")):
        name = f[len("rerank_out_"):-len(".json")]
        base, rr = name.split("_", 1)
        if base not in stage1:
            print(f"skip {name}: no stage-1 input for base {base}")
            continue
        reranked = json.load(open(f))
        for k in DEPTHS:
            ranking = {qk: truncated(stage1[base][qk], reranked[qk], k)
                       for qk in reranked if qk in stage1[base]}
            rows.append({"base": base, "reranker": rr, "depth": k,
                         **score(ranking, qrels, gold)})
        rows.append({"base": base, "reranker": "— (stage-1)", "depth": 0,
                     **score(stage1[base], qrels, gold)})

    json.dump(rows, open("rerank_depth_results.json", "w"), indent=2)
    with open("rerank_depth_results.md", "w") as out:
        out.write("# How deep does reranking have to go?\n\n")
        out.write("Simulated from the existing rerank outputs — a cross-encoder scores each\n"
                  "pair independently, so its ranking over the stage-1 top-K is its ranking\n"
                  "over the top-100 restricted to those K. No new inference.\n\n")
        out.write("| base | reranker | depth | nDCG@10 | Δ vs depth-100 | success@10 | recall@10 |\n")
        out.write("|---|---|---:|---:|---:|---:|---:|\n")
        full = {(r["base"], r["reranker"]): r["ndcg_at_10"]
                for r in rows if r["depth"] == 100}
        for r in rows:
            ref = full.get((r["base"], r["reranker"]))
            delta = "" if ref is None or r["depth"] == 0 else f"{r['ndcg_at_10'] - ref:+.3f}"
            out.write(f"| {r['base']} | {r['reranker']} | {r['depth'] or '—'} "
                      f"| {r['ndcg_at_10']:.3f} | {delta} | {r['success_at_10']:.3f} "
                      f"| {r['recall_at_10']:.3f} |\n")
    print(open("rerank_depth_results.md").read())


if __name__ == "__main__":
    main()
