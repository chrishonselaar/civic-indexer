#!/usr/bin/env python3
"""Re-pool judging depth for the reranker A/B (local, Haiku, no GPU).

The original pooled_qrels.json only judged the embedders' top-15, so a reranker that
promotes a DEEPER stage-1 candidate into its top-10 would hit an *unjudged* doc = an unfair
grade-0. This fixes that: gather every id any method ranks in its top-10 (stage-1 + all
rerank_out_*.json), judge the ones not already in qrels with Haiku (same 0/1/2 rubric as
pooled_eval.py), and merge into an expanded id-space qrels. Incremental & idempotent — safe
to re-run after adding pod reranker outputs (only new ids are judged).

Writes: pooled_qrels_v2.json = {qidx: {doc_id: grade}}
"""
import json, os, re, sys, time, threading, glob
from concurrent.futures import ThreadPoolExecutor
import anthropic

ENV = "/home/chris/claude-projects/civic-indexer/.env"
MODEL = "claude-haiku-4-5-20251001"
POOL = "slim_pool.jsonl"; QUERIES = "queries.jsonl"
OLD_QRELS = "pooled_qrels.json"; OUT = "pooled_qrels_v2.json"
JUDGE_TOPN = 10          # judge each method's top-N
JUDGE_BATCH = 8
WORKERS = 12

def load_key():
    for l in open(ENV):
        if l.strip().startswith("ANTHROPIC_API_KEY"):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
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

def judge_batch(query, items):   # items: [(id, text)]
    body = "\n\n".join(f"[Fragment {i+1}]\n{t[:900]}" for i, (_, t) in enumerate(items))
    user = f"ZOEKVRAAG: {query}\n\nFRAGMENTEN:\n{body}"
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
    pool = [json.loads(l) for l in open(POOL)]
    id2text = {r["id"]: r["text"] for r in pool}
    row2id = {i: r["id"] for i, r in enumerate(pool)}
    queries = [json.loads(l) for l in open(QUERIES)]   # qrels keys = LINE POSITION

    # existing qrels: row-space -> id-space
    old = json.load(open(OLD_QRELS))
    qrels = {}   # qidx(str) -> {id: grade}
    for qk, inner in old.items():
        qrels[qk] = {row2id[int(r)]: int(g) for r, g in inner.items()}

    # gather every id ranked in a method's top-N (stage-1 inputs + all rerank_out files)
    ranked = {}  # qidx(str) -> set(ids)
    for f in glob.glob("rerank_input_*.json"):
        for item in json.load(open(f)):
            ranked.setdefault(str(item["qidx"]), set()).update(
                c["id"] for c in item["cands"][:JUDGE_TOPN])
    for f in glob.glob("rerank_out_*.json"):
        for qk, ids in json.load(open(f)).items():
            ranked.setdefault(qk, set()).update(ids[:JUDGE_TOPN])

    # candidates needing a judgment = ranked-top-N not already in qrels
    jobs = []   # (qidx, [(id,text),...])
    for qk, ids in ranked.items():
        need = [i for i in ids if i not in qrels.get(qk, {})]
        for b in range(0, len(need), JUDGE_BATCH):
            jobs.append((qk, [(i, id2text[i]) for i in need[b:b+JUDGE_BATCH]]))
    n_new = sum(len(b) for _, b in jobs)
    print(f"{len(qrels)} queries; {n_new} new (id,q) judgments in {len(jobs)} batches",
          file=sys.stderr)

    lock = threading.Lock(); done = [0]
    def run(job):
        qk, items = job
        res = judge_batch(queries[int(qk)]["query"], items)
        with lock:
            qrels.setdefault(qk, {}).update(res)
            done[0] += 1
            if done[0] % 25 == 0:
                sys.stderr.write(f"judged {done[0]}/{len(jobs)} batches\n")
    if jobs:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(run, jobs))

    # force gold >= 2
    for qk in qrels:
        g = queries[int(qk)]["gold_id"]
        qrels[qk][g] = max(qrels[qk].get(g, 0), 2)

    json.dump(qrels, open(OUT, "w"))
    avg_rel = sum(sum(1 for v in q.values() if v >= 1) for q in qrels.values()) / len(qrels)
    print(f"wrote {OUT}: {len(qrels)} queries, avg {avg_rel:.1f} relevant/query", flush=True)

if __name__ == "__main__":
    main()
