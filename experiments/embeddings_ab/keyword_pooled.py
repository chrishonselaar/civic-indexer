#!/usr/bin/env python3
"""Pooled multi-relevance eval for the SHORT-KEYWORD run — fairly credits dense's semantic
recall (which single-gold hides). Pools top-15 from BM25/dense/hybrid, Haiku grades each,
re-scores all three methods against multi-relevant qrels.

Reads keyword_rankings.json (from keyword_retrieval.py). Writes keyword_pooled_results.md.
"""
import json, sys, re, threading, random, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import anthropic

POOL = "slim_pool.jsonl"; RANK = "keyword_rankings.json"
KEY_ENV = "/home/chris/claude-projects/civic-indexer/.env"
MODEL = "claude-haiku-4-5-20251001"
METHODS = ["BM25", "dense", "hybrid"]
N_QUERIES = 250; POOL_TOPK = 15; JUDGE_BATCH = 8; SEED = 20260723

def load_key():
    for line in open(KEY_ENV):
        if line.strip().startswith("ANTHROPIC_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")
client = anthropic.Anthropic(api_key=load_key())

SYS = ("Je beoordeelt of documentfragmenten passen bij een korte zoekopdracht over "
       "Nederlandse overheids-/Woo-documenten. Voor ELK genummerd fragment geef je een graad:\n"
       "2 = duidelijk relevant / gaat over het gezochte onderwerp\n"
       "1 = deels relevant / raakt het onderwerp zijdelings\n"
       "0 = niet relevant\n"
       'Antwoord ALLEEN met JSON: {"1":2,"2":0,...}. Geen extra tekst.')

def judge_batch(query, items):
    body = "\n\n".join(f"[Fragment {i+1}]\n{t[:800]}" for i, (_, t) in enumerate(items))
    user = f"ZOEKOPDRACHT: {query}\n\nFRAGMENTEN:\n{body}"
    for attempt in range(4):
        try:
            m = client.messages.create(model=MODEL, max_tokens=300, system=SYS,
                                       messages=[{"role": "user", "content": user}])
            raw = re.sub(r"^```(?:json)?|```$", "", m.content[0].text.strip(),
                         flags=re.MULTILINE).strip()
            d = json.loads(raw)
            return {cid: (int(d.get(str(i+1), 0)) if str(d.get(str(i+1), 0)) in "012" else 0)
                    for i, (cid, _) in enumerate(items)}
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {cid: 0 for cid, _ in items}

def main():
    id2text = {json.loads(l)["id"]: json.loads(l)["text"] for l in open(POOL)}
    R = json.load(open(RANK))
    nq = len(R["queries"])
    rng = random.Random(SEED)
    sel = rng.sample(range(nq), min(N_QUERIES, nq))

    # candidate pool per query = union of top-POOL_TOPK across methods + gold
    cand = {}
    for qi in sel:
        s = set()
        for meth in ["BM25", "dense", "hybrid"]:
            s.update(R[meth][qi][:POOL_TOPK])
        s.add(R["gold_ids"][qi])
        cand[qi] = list(s)
    total = sum(len(c) for c in cand.values())
    print(f"{len(sel)} queries, {total} judgments (~{total/len(sel):.0f}/query)", file=sys.stderr)

    qrels = {qi: {} for qi in sel}
    jobs = []
    for qi in sel:
        c = cand[qi]
        for b in range(0, len(c), JUDGE_BATCH):
            jobs.append((qi, c[b:b+JUDGE_BATCH]))
    lock = threading.Lock(); done = [0]
    def run(job):
        qi, ids = job
        res = judge_batch(R["queries"][qi], [(cid, id2text[cid]) for cid in ids])
        with lock:
            qrels[qi].update({cid: res.get(cid, 0) for cid in ids})
            done[0] += 1
            if done[0] % 50 == 0: sys.stderr.write(f"judged {done[0]}/{len(jobs)}\n")
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(run, jobs))
    for qi in sel:
        qrels[qi][R["gold_ids"][qi]] = max(qrels[qi].get(R["gold_ids"][qi], 0), 2)

    def dcg(gr): return sum((2**g-1)/np.log2(i+2) for i, g in enumerate(gr))
    def score(meth):
        s10=s100=0; ndcgs=[]; relrec=[]
        for qi in sel:
            rel = qrels[qi]; ideal = sorted(rel.values(), reverse=True)[:10]
            idcg = dcg(ideal) or 1e-9
            rk = R[meth][qi]
            ndcgs.append(dcg([rel.get(c,0) for c in rk[:10]])/idcg)
            relset = {c for c,g in rel.items() if g>=1}
            s10 += bool(set(rk[:10]) & relset); s100 += bool(set(rk[:100]) & relset)
            relrec.append(len(set(rk[:100]) & relset)/max(1,len(relset)))
        n=len(sel)
        return dict(method=meth, success_at_10=round(s10/n,3), success_at_100=round(s100/n,3),
                    ndcg_at_10=round(float(np.mean(ndcgs)),3),
                    recall_at_100=round(float(np.mean(relrec)),3))
    rows = [score(m) for m in ["BM25","dense","hybrid"]]
    avg = np.mean([sum(1 for g in q.values() if g>=1) for q in qrels.values()])
    json.dump({"rows":rows,"avg_rel":float(avg)}, open("keyword_pooled_results.json","w"), indent=2)
    with open("keyword_pooled_results.md","w") as f:
        f.write("# Short-keyword search, POOLED multi-relevance (BM25 vs dense vs hybrid)\n\n")
        f.write(f"{len(sel)} keyword queries, pooled top-{POOL_TOPK} across 3 methods, "
                f"Haiku-graded. Avg **{avg:.1f} relevant chunks/query**.\n\n")
        f.write("| method | success@10 | success@100 | nDCG@10 | recall@100 |\n|---|---:|---:|---:|---:|\n")
        for r in rows:
            f.write(f"| {r['method']} | {r['success_at_10']:.3f} | {r['success_at_100']:.3f} "
                    f"| {r['ndcg_at_10']:.3f} | {r['recall_at_100']:.3f} |\n")
    for r in rows: print(json.dumps(r), flush=True)
    print(f"\navg relevant/query = {avg:.1f}", flush=True)

if __name__ == "__main__":
    main()
