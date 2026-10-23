#!/usr/bin/env python3
"""Pooled multi-relevance eval (TREC-style) — fixes the single-gold artifact.

Instead of one labelled gold per query, we POOL the top-K candidates across ALL models,
have Haiku judge each for graded relevance (0/1/2), and build a multi-relevant qrels set.
Then every model's ranking is re-scored against that set, so an equally-valid chunk counts
as a hit. This gives artifact-free success@k / nDCG@10 for the embedders.

Local: uses saved vectors in embeddings/ + Haiku for judging. No GPU needed.
Writes: pooled_eval_results.md + pooled_qrels.json (reusable judgments).
"""
import json, sys, re, threading, random, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import anthropic

POOL = "slim_pool.jsonl"; QUERIES = "queries.jsonl"; EMB = "embeddings"
KEY_ENV = "/home/chris/claude-projects/civic-indexer/.env"
MODEL = "claude-haiku-4-5-20251001"
MODELS = ["bgem3", "arctic", "qwen4b", "voyage-4-lite", "voyage-law-2", "voyage-4-large"]
N_QUERIES = 250          # sampled queries to judge
POOL_TOPK = 15           # pool top-K per model for the judged candidate set
JUDGE_BATCH = 8          # candidates per Haiku call
SEED = 20260723

def load_key():
    for line in open(KEY_ENV):
        if line.strip().startswith("ANTHROPIC_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")
client = anthropic.Anthropic(api_key=load_key())

SYS = (
    "Je beoordeelt of documentfragmenten een Nederlandse zoekvraag beantwoorden. "
    "Voor ELK genummerd fragment geef je een graad:\n"
    "2 = beantwoordt de vraag volledig en direct\n"
    "1 = gedeeltelijk relevant / raakt het onderwerp maar beantwoordt niet volledig\n"
    "0 = niet relevant\n"
    "Antwoord ALLEEN met een JSON-object dat fragmentnummer -> graad mapt, bijv "
    '{"1":2,"2":0,"3":1}. Geen extra tekst.'
)

def judge_batch(query, items):
    """items: list of (cand_id, text). Returns {cand_id: grade}."""
    body = "\n\n".join(f"[Fragment {i+1}]\n{t[:900]}" for i, (_, t) in enumerate(items))
    user = f"ZOEKVRAAG: {query}\n\nFRAGMENTEN:\n{body}"
    for attempt in range(4):
        try:
            m = client.messages.create(model=MODEL, max_tokens=300, system=SYS,
                                       messages=[{"role": "user", "content": user}])
            raw = re.sub(r"^```(?:json)?|```$", "", m.content[0].text.strip(),
                         flags=re.MULTILINE).strip()
            d = json.loads(raw)
            out = {}
            for i, (cid, _) in enumerate(items):
                g = d.get(str(i + 1), 0)
                out[cid] = int(g) if g in (0, 1, 2, "0", "1", "2") else 0
            return out
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {cid: 0 for cid, _ in items}

def main():
    pool = [json.loads(l) for l in open(POOL)]
    pool_ids = [r["id"] for r in pool]
    id2text = {r["id"]: r["text"] for r in pool}
    id2row = {c: i for i, c in enumerate(pool_ids)}
    queries = [json.loads(l) for l in open(QUERIES)]

    rng = random.Random(SEED)
    qidx = rng.sample(range(len(queries)), min(N_QUERIES, len(queries)))
    qsel = [queries[i] for i in qidx]

    # load + normalize vectors, precompute each model's top-100 rows for the sampled queries
    print("loading vectors...", file=sys.stderr)
    tops = {}   # model -> list over qsel of top-100 row arrays
    for mk in MODELS:
        D = np.load(f"{EMB}/{mk}.doc.f16.npy").astype(np.float32)
        Q = np.load(f"{EMB}/{mk}.q.f16.npy").astype(np.float32)[qidx]
        D /= (np.linalg.norm(D, axis=1, keepdims=True) + 1e-9)
        Q /= (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-9)
        mt = []
        for i in range(len(qsel)):
            sims = D @ Q[i]
            t = np.argpartition(-sims, 100)[:100]
            t = t[np.argsort(-sims[t])]
            mt.append(t)
        tops[mk] = mt

    # pool candidates per query = union of top-POOL_TOPK across models (+ the original gold)
    cand_per_q = []
    for qi in range(len(qsel)):
        s = set()
        for mk in MODELS:
            s.update(int(r) for r in tops[mk][qi][:POOL_TOPK])
        s.add(id2row[qsel[qi]["gold_id"]])
        cand_per_q.append(sorted(s))
    total = sum(len(c) for c in cand_per_q)
    print(f"{len(qsel)} queries, {total} candidate judgments "
          f"(~{total/len(qsel):.0f}/query)", file=sys.stderr)

    # judge (batched) with concurrency
    qrels = [dict() for _ in qsel]
    jobs = []
    for qi in range(len(qsel)):
        rows = cand_per_q[qi]
        for b in range(0, len(rows), JUDGE_BATCH):
            jobs.append((qi, rows[b:b + JUDGE_BATCH]))
    lock = threading.Lock(); done = [0]
    def run(job):
        qi, rows = job
        items = [(r, id2text[pool_ids[r]]) for r in rows]
        res = judge_batch(qsel[qi]["query"], items)
        with lock:
            for r in rows:
                qrels[qi][r] = res.get(r, 0)
            done[0] += 1
            if done[0] % 50 == 0:
                sys.stderr.write(f"judged {done[0]}/{len(jobs)} batches\n")
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(run, jobs))

    # force gold >= 2 (it generated the query, definitionally relevant)
    for qi in range(len(qsel)):
        g = id2row[qsel[qi]["gold_id"]]
        qrels[qi][g] = max(qrels[qi].get(g, 0), 2)

    json.dump({str(qidx[i]): {str(r): g for r, g in qrels[i].items()}
               for i in range(len(qsel))}, open("pooled_qrels.json", "w"))

    # scoring
    def dcg(grades):
        return sum((2**g - 1) / np.log2(i + 2) for i, g in enumerate(grades))
    def score_model(mk):
        s10 = s100 = 0; ndcgs = []; sg_ndcgs = []; rel_at100 = []
        for qi in range(len(qsel)):
            rel = qrels[qi]                       # row -> grade
            ideal = sorted(rel.values(), reverse=True)[:10]
            idcg = dcg(ideal) or 1e-9
            rank = tops[mk][qi]                   # top-100 rows
            grades10 = [rel.get(int(r), 0) for r in rank[:10]]
            ndcgs.append(dcg(grades10) / idcg)
            # success@k = any relevant (grade>=1) in top-k
            top10 = set(int(r) for r in rank[:10]); top100 = set(int(r) for r in rank[:100])
            relset = {r for r, g in rel.items() if g >= 1}
            s10 += bool(top10 & relset); s100 += bool(top100 & relset)
            # recall@100 over judged relevants (pool-limited)
            rel_at100.append(len(top100 & relset) / max(1, len(relset)))
            # single-gold nDCG@10 for comparison
            g = id2row[qsel[qi]["gold_id"]]
            gr = np.where(rank == g)[0]
            sg_ndcgs.append(1.0 / np.log2(gr[0] + 2) if len(gr) and gr[0] < 10 else 0.0)
        n = len(qsel)
        return dict(model=mk, success_at_10=round(s10/n, 3), success_at_100=round(s100/n, 3),
                    ndcg_at_10_pooled=round(float(np.mean(ndcgs)), 3),
                    recall_at_100_pooled=round(float(np.mean(rel_at100)), 3),
                    ndcg_at_10_singlegold=round(float(np.mean(sg_ndcgs)), 3))

    rows = [score_model(mk) for mk in MODELS]
    rows.sort(key=lambda r: -r["ndcg_at_10_pooled"])
    json.dump(rows, open("pooled_eval_results.json", "w"), indent=2)
    avg_rel = np.mean([sum(1 for g in q.values() if g >= 1) for q in qrels])
    with open("pooled_eval_results.md", "w") as f:
        f.write("# Pooled multi-relevance eval (fixes single-gold artifact)\n\n")
        f.write(f"{len(qsel)} queries, pooled top-{POOL_TOPK} across {len(MODELS)} models, "
                f"Haiku-judged graded relevance (0/1/2). Avg **{avg_rel:.1f} relevant "
                f"chunks/query** (vs 1 under single-gold).\n\n")
        f.write("| Model | success@10 | success@100 | nDCG@10 (pooled) | nDCG@10 (single-gold) | recall@100 (pooled) |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        for r in rows:
            f.write(f"| {r['model']} | {r['success_at_10']:.3f} | {r['success_at_100']:.3f} "
                    f"| {r['ndcg_at_10_pooled']:.3f} | {r['ndcg_at_10_singlegold']:.3f} "
                    f"| {r['recall_at_100_pooled']:.3f} |\n")
    for r in rows:
        print(json.dumps(r), flush=True)
    print(f"\navg relevant/query = {avg_rel:.1f} -> wrote pooled_eval_results.md", flush=True)

if __name__ == "__main__":
    main()
