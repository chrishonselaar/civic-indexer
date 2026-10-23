#!/usr/bin/env python3
"""Generate Dutch synthetic queries for each gold chunk via Claude Haiku.

For each gold chunk, produce 3 natural Dutch questions a citizen/journalist might
ask that THIS chunk specifically answers. Output: queries.jsonl
  {"query": str, "gold_id": chunk_id, "qidx": 0..2}

Known-item retrieval eval: query -> its source gold chunk is the single relevant doc.

API key is read from a project .env WITHOUT printing it.
"""
import json, os, re, sys, threading, queue, time
from concurrent.futures import ThreadPoolExecutor
import anthropic

MODEL = "claude-haiku-4-5-20251001"
KEY_ENV = "/home/chris/claude-projects/civic-indexer/.env"
N_QUERIES = 3
MAX_CHARS = 6000      # truncate chunk text sent to the model
WORKERS = 12

def load_key():
    for line in open(KEY_ENV):
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no ANTHROPIC_API_KEY in " + KEY_ENV)

client = anthropic.Anthropic(api_key=load_key())

SYS = (
    "Je bent een assistent die realistische zoekvragen bedenkt voor een zoekmachine "
    "over Nederlandse overheids- en Woo-documenten. Je krijgt één tekstfragment uit een "
    "document. Bedenk precies 3 natuurlijke Nederlandse zoekvragen die een burger of "
    "journalist zou stellen en die SPECIFIEK door DIT fragment beantwoord worden.\n"
    "Eisen:\n"
    "- Elke vraag is beantwoordbaar met de informatie in dit fragment.\n"
    "- Varieer: 1 vraag over een concreet detail, 1 breder over het onderwerp, 1 ertussenin.\n"
    "- Geen letterlijke kopie van zinnen; formuleer als een echte vraag.\n"
    "- Geen verwijzing naar 'het fragment' of 'dit document'; stel de vraag alsof je het "
    "antwoord nog niet kent.\n"
    "- Vermijd vragen die op duizenden documenten passen; maak ze onderscheidend.\n"
    "Antwoord ALLEEN met een JSON-array van 3 strings, niets anders."
)

def gen(rec):
    cid = rec["id"]
    title = rec.get("title") or ""
    text = rec["text"][:MAX_CHARS]
    user = f"Titel: {title}\n\nFragment:\n{text}"
    for attempt in range(4):
        try:
            m = client.messages.create(
                model=MODEL, max_tokens=500, system=SYS,
                messages=[{"role": "user", "content": user}],
            )
            raw = m.content[0].text.strip()
            # strip code fences if any
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
            arr = json.loads(raw)
            qs = [q.strip() for q in arr if isinstance(q, str) and q.strip()][:N_QUERIES]
            if len(qs) >= 1:
                return cid, qs
        except Exception as e:
            if attempt == 3:
                sys.stderr.write(f"FAIL {cid}: {e}\n")
                return cid, []
            time.sleep(1.5 * (attempt + 1))
    return cid, []

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only first N gold chunks")
    ap.add_argument("--out", default="queries.jsonl")
    a = ap.parse_args()
    gold = [json.loads(l) for l in open("gold_chunks.jsonl")]
    if a.limit:
        gold = gold[:a.limit]
    out = open(a.out, "w")
    lock = threading.Lock()
    done = 0
    total_q = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for cid, qs in ex.map(gen, gold):
            with lock:
                for i, q in enumerate(qs):
                    out.write(json.dumps({"query": q, "gold_id": cid, "qidx": i},
                                         ensure_ascii=False) + "\n")
                    total_q += 1
                done += 1
                if done % 100 == 0:
                    sys.stderr.write(f"{done}/{len(gold)} chunks, {total_q} queries\n")
    out.close()
    sys.stderr.write(f"DONE {done} chunks -> {total_q} queries\n")

if __name__ == "__main__":
    main()
