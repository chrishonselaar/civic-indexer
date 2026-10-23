#!/usr/bin/env python3
"""Build the WOO embedding-eval set from the real chunk export.

Outputs (all in this dir):
  slim_pool.jsonl   -- {id, text} for EVERY substantive chunk = the retrieval pool
                       (distractors + gold targets live here; gold must be in-pool).
  gold_chunks.jsonl -- the sampled gold chunks (full record) to generate queries from.
  gold_ids.json     -- list of gold chunk ids (for quick reference).

Substantive filter: chunk_size_words >= MIN_WORDS and not dominated by redaction
boilerplate. Reproducible via fixed SEED.
"""
import json, random, re, sys

SRC = "/home/chris/claude-projects/wob-search-mcp/es_export/woo-besluiten-chunks.jsonl"
MIN_WORDS = 150
N_GOLD = 1000
SEED = 20260722

def is_substantive(s):
    if (s.get("chunk_size_words") or 0) < MIN_WORDS:
        return False
    t = s.get("chunk_text") or ""
    if len(t) < 400:
        return False
    # Reject chunks that are mostly redaction markers / inventory scaffolding.
    red = t.count("[REDACTED")
    if red > 0 and red * 40 > len(t):   # heuristic: dense redaction lists
        return False
    # Require a reasonable amount of running alphabetic text.
    alpha = sum(c.isalpha() for c in t)
    if alpha < 0.5 * len(t):
        return False
    return True

def main():
    pool = []          # (id, text)
    substantive = []   # full source dicts for eligible gold candidates
    n = 0
    with open(SRC) as f:
        for line in f:
            n += 1
            d = json.loads(line)
            s = d["_source"]
            if not is_substantive(s):
                continue
            cid = s.get("chunk_id") or d.get("_id")
            text = s["chunk_text"]
            pool.append((cid, text))
            substantive.append((cid, s))

    print(f"read {n} chunks; substantive pool = {len(pool)}", file=sys.stderr)

    # Sample gold from substantive, but bias slightly toward mid/long chunks that
    # actually carry answerable content (avoid ultra-short survivors).
    rng = random.Random(SEED)
    idx = list(range(len(substantive)))
    rng.shuffle(idx)
    gold_idx = idx[:N_GOLD]
    gold_ids = [substantive[i][0] for i in gold_idx]
    gold_set = set(gold_ids)

    with open("slim_pool.jsonl", "w") as f:
        for cid, text in pool:
            f.write(json.dumps({"id": cid, "text": text}, ensure_ascii=False) + "\n")

    with open("gold_chunks.jsonl", "w") as f:
        for i in gold_idx:
            cid, s = substantive[i]
            f.write(json.dumps({
                "id": cid,
                "text": s["chunk_text"],
                "title": s.get("title"),
                "ministry": s.get("ministry"),
                "document_type": s.get("document_type"),
                "publication_date": s.get("publication_date"),
            }, ensure_ascii=False) + "\n")

    with open("gold_ids.json", "w") as f:
        json.dump(gold_ids, f)

    # sanity: all gold in pool
    pool_ids = {cid for cid, _ in pool}
    assert gold_set <= pool_ids, "gold not subset of pool!"
    print(f"wrote slim_pool.jsonl ({len(pool)}), gold_chunks.jsonl ({len(gold_ids)})",
          file=sys.stderr)

if __name__ == "__main__":
    main()
