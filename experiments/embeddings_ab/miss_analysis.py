#!/usr/bin/env python3
"""Categorize the 'misses' from a rank dump to see whether recall@100 is really
low or deflated by single-gold / boilerplate artifacts.

For each query whose gold chunk fell OUTSIDE the top-100, ask Haiku whether any of
the top-5 RETRIEVED chunks also correctly answers the query:
  - artifact_miss : a retrieved chunk answers it just as well (single-gold undercount)
  - generic_query : the query is too generic to have one specific answer
  - true_miss     : none of the retrieved chunks answer it (a real retrieval failure)

Also samples HITS (gold in top-100) as a control. Writes miss_analysis_<key>.md.

Usage: python miss_analysis.py results_bgem3_ranks.jsonl [--n 60]
"""
import json, sys, argparse, threading, re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import anthropic

KEY_ENV = "/home/chris/claude-projects/civic-indexer/.env"
POOL = "slim_pool.jsonl"
MODEL = "claude-haiku-4-5-20251001"
SEED = 20260722

def load_key():
    for line in open(KEY_ENV):
        if line.strip().startswith("ANTHROPIC_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")

client = anthropic.Anthropic(api_key=load_key())

SYS = (
    "Je beoordeelt de kwaliteit van zoekresultaten voor Nederlandse overheidsdocumenten. "
    "Je krijgt een zoekvraag, het 'gouden' fragment waaruit de vraag is afgeleid, en de "
    "top-5 fragmenten die het zoeksysteem WEL ophaalde (het gouden fragment stond niet in "
    "de top-100). Bepaal:\n"
    "- Beantwoordt minstens één van de opgehaalde fragmenten de vraag net zo goed als het "
    "gouden fragment? (een even geldig antwoord)\n"
    "- Of is de vraag zo algemeen dat er geen uniek juist fragment bestaat?\n"
    "Antwoord ALLEEN met JSON: "
    '{"verdict":"artifact_miss|generic_query|true_miss","reason":"<kort>"}\n'
    "artifact_miss = een opgehaald fragment beantwoordt de vraag ook correct.\n"
    "generic_query = de vraag past op heel veel fragmenten, geen uniek antwoord.\n"
    "true_miss = geen enkel opgehaald fragment beantwoordt de vraag."
)

def judge(item, id2text):
    q = item["query"]
    gold = (id2text.get(item["gold_id"]) or "")[:1500]
    retr = "\n\n".join(
        f"[Fragment {i+1}]\n{(id2text.get(cid) or '')[:900]}"
        for i, cid in enumerate(item["top20"][:5])
    )
    user = f"ZOEKVRAAG: {q}\n\nGOUDEN FRAGMENT (niet opgehaald):\n{gold}\n\nOPGEHAALDE TOP-5:\n{retr}"
    for attempt in range(4):
        try:
            m = client.messages.create(model=MODEL, max_tokens=200, system=SYS,
                                       messages=[{"role": "user", "content": user}])
            raw = re.sub(r"^```(?:json)?|```$", "", m.content[0].text.strip(),
                         flags=re.MULTILINE).strip()
            d = json.loads(raw)
            if d.get("verdict") in ("artifact_miss", "generic_query", "true_miss"):
                return item, d
        except Exception:
            pass
    return item, {"verdict": "true_miss", "reason": "(judge failed -> default)"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ranks")
    ap.add_argument("--n", type=int, default=60)
    a = ap.parse_args()
    key = a.ranks.split("_")[1] if "_" in a.ranks else a.ranks

    print("loading pool text...", file=sys.stderr)
    id2text = {}
    for line in open(POOL):
        d = json.loads(line)
        id2text[d["id"]] = d["text"]

    rows = [json.loads(l) for l in open(a.ranks)]
    misses = [r for r in rows if r["gold_rank"] is None]
    hits = [r for r in rows if r["gold_rank"] is not None]
    print(f"{len(rows)} queries: {len(hits)} hits, {len(misses)} misses "
          f"(recall@100={len(hits)/len(rows):.3f})", file=sys.stderr)

    import random
    rng = random.Random(SEED)
    sample = rng.sample(misses, min(a.n, len(misses)))

    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for item, verdict in ex.map(lambda it: judge(it, id2text), sample):
            results.append((item, verdict))

    cnt = Counter(v["verdict"] for _, v in results)
    n = len(results)
    # Extrapolate the artifact fraction to a corrected recall estimate.
    art = cnt["artifact_miss"] + cnt["generic_query"]
    art_frac = art / n if n else 0
    base_recall = len(hits) / len(rows)
    miss_frac = len(misses) / len(rows)
    corrected = base_recall + miss_frac * art_frac

    lines = []
    lines.append(f"# Miss analysis — {key}\n")
    lines.append(f"- Queries: {len(rows)} | hits@100: {len(hits)} | misses: {len(misses)} "
                 f"| **recall@100 = {base_recall:.3f}**")
    lines.append(f"- Judged sample of misses: {n}")
    lines.append(f"  - artifact_miss (equally-valid chunk retrieved): "
                 f"{cnt['artifact_miss']} ({cnt['artifact_miss']/n:.0%})")
    lines.append(f"  - generic_query (no unique answer): "
                 f"{cnt['generic_query']} ({cnt['generic_query']/n:.0%})")
    lines.append(f"  - true_miss (real retrieval failure): "
                 f"{cnt['true_miss']} ({cnt['true_miss']/n:.0%})")
    lines.append(f"\n**{art_frac:.0%} of sampled misses are artifacts, not real failures.**")
    lines.append(f"Artifact-corrected recall@100 estimate: "
                 f"{base_recall:.3f} + {miss_frac:.3f}×{art_frac:.2f} = **~{corrected:.3f}**")
    lines.append(f"(True-miss-only 'floor' recall would be ~{1 - miss_frac*(cnt['true_miss']/n):.3f}.)\n")
    lines.append("## Sample verdicts\n")
    for item, v in results[:25]:
        lines.append(f"- **{v['verdict']}** — _{item['query']}_")
        lines.append(f"  - {v.get('reason','')}")

    report = "\n".join(lines)
    open(f"miss_analysis_{key}.md", "w").write(report)
    print("\n" + report)

if __name__ == "__main__":
    main()
