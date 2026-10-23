#!/usr/bin/env python3
"""Generate SHORT keyword queries (how users actually search) from gold chunks, via Haiku.

Real users type 1-4 word phrases into a search box, not full questions. This produces the
realistic query distribution the product will see. Output: keyword_queries.jsonl
  {"query": str, "gold_id": chunk_id, "qidx": 0..N}
"""
import json, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
import anthropic

MODEL = "claude-haiku-4-5-20251001"
KEY_ENV = "/home/chris/claude-projects/civic-indexer/.env"
N_QUERIES = 3
MAX_CHARS = 5000
WORKERS = 12

def load_key():
    for line in open(KEY_ENV):
        if line.strip().startswith("ANTHROPIC_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")
client = anthropic.Anthropic(api_key=load_key())

SYS = (
    "Je bedenkt korte zoekopdrachten voor een zoekmachine over Nederlandse "
    "overheids-/Woo-documenten, zoals een burger of journalist ze in een zoekbalk typt. "
    "Je krijgt één documentfragment. Geef precies 3 KORTE zoekopdrachten (1 tot 4 woorden) "
    "waarmee iemand DIT document zou zoeken.\n"
    "Eisen:\n"
    "- 1 tot 4 woorden per zoekopdracht. GEEN volledige zinnen of vragen.\n"
    "- Gebruik concrete termen: namen, plaatsen, onderwerpen, organisaties, kenmerken.\n"
    "- Zoals mensen echt typen: bv 'mondkapjes NVWA', 'stikstof Ravenswaarden', "
    "'kinderopvangtoeslag besluit'.\n"
    "- Varieer: 1 heel kort (1-2 woorden), 1 iets specifieker (3-4 woorden).\n"
    "- Geen leestekens behalve spaties.\n"
    "Antwoord ALLEEN met een JSON-array van 3 strings."
)

def gen(rec):
    cid = rec["id"]
    text = (rec.get("title") or "") + "\n\n" + rec["text"][:MAX_CHARS]
    for attempt in range(4):
        try:
            m = client.messages.create(model=MODEL, max_tokens=200, system=SYS,
                                       messages=[{"role": "user", "content": text}])
            raw = re.sub(r"^```(?:json)?|```$", "", m.content[0].text.strip(),
                         flags=re.MULTILINE).strip()
            arr = json.loads(raw)
            qs = [q.strip() for q in arr if isinstance(q, str) and 0 < len(q.split()) <= 6][:N_QUERIES]
            if qs:
                return cid, qs
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return cid, []

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--out", default="keyword_queries.jsonl")
    a = ap.parse_args()
    gold = [json.loads(l) for l in open("gold_chunks.jsonl")][:a.limit]
    out = open(a.out, "w"); lock = threading.Lock(); done = [0]; total = [0]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for cid, qs in ex.map(gen, gold):
            with lock:
                for i, q in enumerate(qs):
                    out.write(json.dumps({"query": q, "gold_id": cid, "qidx": i},
                                         ensure_ascii=False) + "\n")
                    total[0] += 1
                done[0] += 1
                if done[0] % 100 == 0:
                    sys.stderr.write(f"{done[0]}/{len(gold)} chunks, {total[0]} queries\n")
    out.close()
    sys.stderr.write(f"DONE {done[0]} chunks -> {total[0]} keyword queries\n")

if __name__ == "__main__":
    main()
