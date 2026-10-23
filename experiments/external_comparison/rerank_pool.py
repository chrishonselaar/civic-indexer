#!/usr/bin/env python3
"""Add a reranked system to the comparison, using an OPEN-WEIGHTS cross-encoder.

⚠️ **This scores a reranker the project REJECTED, and it does not close criterion #2.**
Read `FINDINGS.md` §5 before quoting any number this script produces.

Criterion #2's literal wording is "hybrid+**rerank** ... beats the equivalent
source-site/opub search on the same query". The production reranker is Voyage
`rerank-2.5`, and it cannot be used here: `tuchtrecht` declares
`policy.processors: []` because these decisions concern named professionals, and
that is an admin decision the agent files rather than answers.

The substitute is `BAAI/bge-reranker-v2-m3`, self-hosted — +0.071 nDCG@10 over stage-1
against Voyage's +0.187 and Cohere `rerank-v3.5`'s +0.105
(`experiments/embeddings_ab/rerank_open_vs_api_findings.md`), which is why that A/B
rejected it. An earlier version of this docstring argued the substitution made the
result a **lower bound** on any shipped reranker. It does not: measured here, the delta
over `hybrid` runs -0.189 to +0.404 and is **negative on 5 of 12 queries**, so the
model is not monotone-worse-but-still-positive, it is noisy. "Weakest of the set" also
bounds nothing about a model outside the set on a different corpus.

What this script is good for: showing that reranking a top-100 hybrid pool of long
Dutch decisions is *mechanically* sound end-to-end, and quantifying how much a cheap
self-hosted option gives (and takes). The policy question it should have raised first
is **Cohere-EU** for `tuchtrecht` — an admin call, not a model swap.

Depth 100, matching production (`HYBRID_POOL`). Safe for this model specifically:
`rerank_depth_findings.md` measured bge-reranker-v2-m3 as **flat** between depth 20
and 100 — it is too weak to exploit a deep pool, which is the same weakness the
lower-bound framing rests on.

Three steps, because the GPU is not on this machine (`experiments/embed_pod/` uses
the same shape). The laptop GPU has 3.7 GB and the search app already occupies it
with bge-m3 for query encoding, and CPU was measured at **~234 s per query, ~49 min
for 12** — against ~13 s of 3090 time (0.90 q/s at depth 100). The first version of
this script ran on CPU on the argument that "renting a machine for 13 seconds of
compute is worse than waiting"; measured, waiting is ~200x the GPU time, so that
argument was wrong and the burst pod is the documented path for a reason.

    uv run python experiments/external_comparison/rerank_pool.py export  # -> rerank_input.jsonl
    scp rerank_input.jsonl pod_rerank.py root@POD:/root/
    ssh root@POD 'pip install -q "transformers==4.49.0" && python3 pod_rerank.py'
    scp root@POD:/root/rerank_scores.jsonl .
    uv run python experiments/external_comparison/rerank_pool.py import rerank_scores.jsonl

Writes the reranked top-10 into runs.json as `ours_hybrid_rerank_open`.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
MODEL = "BAAI/bge-reranker-v2-m3"
POOL = 100
TOPK = 10
CHARS = 6000  # what the pod truncates to 1024 tokens; keeps the payload ~7 MB
YEAR = "2025"
MONTHS = [f"{YEAR}-{m:02d}" for m in range(1, 13)]


def do_export(out_path: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from engine import blobstore, db
    from ui.search_api import app

    runs = json.loads((HERE / "runs.json").read_text())
    client = TestClient(app)

    # Stage 1: the hybrid pool, exactly what production would hand a reranker.
    pools = {}
    for qid, rec in runs.items():
        r = client.get("/api/search", params=[
            ("q", rec["q"]), ("dataset", "tuchtrecht"), ("mode", "hybrid"),
            ("size", str(POOL)),
        ] + [("month", m) for m in MONTHS])
        r.raise_for_status()
        pools[qid] = [h["natural_key"] for h in r.json()["results"]][:POOL]
        print(f"{qid}: pool {len(pools[qid])}", flush=True)

    # Text from our own store, the same source the judge reads.
    keys = sorted({k for ids in pools.values() for k in ids})
    texts = {}
    with db.tx() as cur:
        cur.execute(
            "SELECT i.natural_key, e.text_ref FROM items i "
            "JOIN extractions e ON e.urn = i.urn "
            "WHERE i.source_id = 'tuchtrecht' AND i.natural_key = ANY(%s)",
            (keys,),
        )
        for row in cur.fetchall():
            p = blobstore.from_ref(row["text_ref"])
            texts[row["natural_key"]] = p.read_text(encoding="utf-8") if p.exists() else ""

    with out_path.open("w", encoding="utf-8") as f:
        for qid, rec in runs.items():
            f.write(json.dumps({
                "qid": qid, "q": rec["q"], "model": MODEL,
                "candidates": [{"id": k, "text": texts.get(k, "")[:CHARS]}
                               for k in pools[qid]],
            }, ensure_ascii=False) + "\n")
    mb = out_path.stat().st_size / 1e6
    print(f"wrote {out_path} ({mb:.1f} MB, {len(keys)} unique docs)")


def do_import(scores_path: Path) -> None:
    runs = json.loads((HERE / "runs.json").read_text())
    for line in scores_path.open(encoding="utf-8"):
        r = json.loads(line)
        ranked = [i for _, i in sorted(
            zip(r["scores"], r["ids"]), key=lambda x: -x[0])]
        runs[r["qid"]]["systems"]["ours_hybrid_rerank_open"] = {
            "ids": ranked[:TOPK], "total": len(r["ids"]), "reranker": r.get("model", MODEL),
        }
        print(f"{r['qid']}: reranked {len(r['ids'])}")
    (HERE / "runs.json").write_text(json.dumps(runs, indent=2, ensure_ascii=False))
    print(f"wrote {HERE / 'runs.json'}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "export":
        do_export(Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "rerank_input.jsonl")
    elif mode == "import":
        do_import(Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "rerank_scores.jsonl")
    else:
        sys.exit("usage: rerank_pool.py export [path] | import [path]")
