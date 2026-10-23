#!/usr/bin/env python3
"""Blind pooled judging of the external comparison, then score every run.

TREC-style, and the same method the embedder A/B used (pooled_eval.py): pool the
top-10 of every system, judge each pooled document for graded relevance to the
query, then re-score each ranking against the shared judgments. Single-gold would
understate every system here for the same reason it did there — many decisions
answer one query equally well.

Two properties make it a fair judgment rather than a flattering one:

- **Blind.** The judge sees a query and a shuffled bag of documents. It is never
  told which system returned what, or how many did.
- **One text source.** Every pooled document is read from OUR extracted text,
  including the ones only their search found. Grading must not depend on which
  system surfaced a document, and their site exposes no text we could grade from
  symmetrically anyway.

Writes qrels.json (reusable judgments: ECLI -> grade, no text) and results.md.
Judged text stays in memory: exposure T2, named professionals — no hit contexts
land in the repo.
"""

import json
import math
import random
import re
import sys
import time
from pathlib import Path

import anthropic

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
MODEL = "claude-haiku-4-5-20251001"
BATCH = 6
CHARS = 2500
SEED = 20260729

SYS = (
    "Je beoordeelt of Nederlandse tuchtrechtuitspraken een zoekvraag beantwoorden. "
    "Voor ELK genummerd document geef je een graad:\n"
    "2 = gaat echt over dit onderwerp; een zoeker naar deze vraag wil dit zien\n"
    "1 = raakt het onderwerp zijdelings (noemt het, maar het is niet waar de zaak over gaat)\n"
    "0 = niet relevant\n"
    "Oordeel op de INHOUD van de zaak, niet op losse woordovereenkomst. "
    "Antwoord ALLEEN met een JSON-object dat documentnummer -> graad mapt, bijv "
    '{"1":2,"2":0,"3":1}. Geen extra tekst.'
)


def load_key() -> str:
    for line in (ROOT / ".env").open():
        if line.strip().startswith("ANTHROPIC_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no ANTHROPIC_API_KEY in .env")


def texts_for(eclis: list[str]) -> dict[str, str]:
    """Extracted text for pooled ECLIs, straight from the ledger + blobstore."""
    sys.path.insert(0, str(ROOT))
    from engine import blobstore, db

    out: dict[str, str] = {}
    with db.tx() as cur:
        cur.execute(
            "SELECT i.natural_key, e.text_ref FROM items i "
            "JOIN extractions e ON e.urn = i.urn "
            "WHERE i.source_id = 'tuchtrecht' AND i.natural_key = ANY(%s)",
            (eclis,),
        )
        for row in cur.fetchall():
            p = blobstore.from_ref(row["text_ref"])
            out[row["natural_key"]] = p.read_text(encoding="utf-8") if p.exists() else ""
    return out


def judge(client, query: str, items: list[tuple[str, str]]) -> dict[str, int]:
    body = "\n\n".join(
        f"[Document {i + 1}]\n{t[:CHARS]}" for i, (_, t) in enumerate(items)
    )
    user = f"ZOEKVRAAG: {query}\n\nDOCUMENTEN:\n{body}"
    for attempt in range(4):
        try:
            m = client.messages.create(
                model=MODEL, max_tokens=400, system=SYS,
                messages=[{"role": "user", "content": user}],
            )
            raw = re.sub(r"^```(?:json)?|```$", "", m.content[0].text.strip(),
                         flags=re.MULTILINE).strip()
            d = json.loads(raw)
            return {
                cid: (int(g) if (g := d.get(str(i + 1), 0)) in (0, 1, 2, "0", "1", "2")
                      else 0)
                for i, (cid, _) in enumerate(items)
            }
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {cid: 0 for cid, _ in items}


def ndcg_at(ids: list[str], grades: dict[str, int], k: int = 10) -> float:
    gain = [2 ** grades.get(i, 0) - 1 for i in ids[:k]]
    dcg = sum(g / math.log2(r + 2) for r, g in enumerate(gain))
    ideal = sorted((2 ** g - 1 for g in grades.values()), reverse=True)[:k]
    idcg = sum(g / math.log2(r + 2) for r, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def prec_at(ids: list[str], grades: dict[str, int], k: int = 10) -> float:
    """Precision@k counting only grade 2 — 'a zoeker wants to see this'."""
    return sum(1 for i in ids[:k] if grades.get(i, 0) == 2) / k


def main() -> None:
    runs = json.loads((HERE / "runs.json").read_text())
    client = anthropic.Anthropic(api_key=load_key())
    rng = random.Random(SEED)

    qrels: dict[str, dict[str, int]] = {}
    for qid, rec in runs.items():
        pool = sorted({i for s in rec["systems"].values() for i in s["ids"]})
        txt = texts_for(pool)
        cands = [(e, txt.get(e, "")) for e in pool if txt.get(e)]
        rng.shuffle(cands)  # blind: the judge sees no system order
        grades: dict[str, int] = {}
        for i in range(0, len(cands), BATCH):
            grades.update(judge(client, rec["q"], cands[i:i + BATCH]))
        for e in pool:
            grades.setdefault(e, 0)  # pooled but no text we hold -> ungradeable, 0
        qrels[qid] = grades
        print(f"{qid}: pooled {len(pool)}, graded>0 "
              f"{sum(1 for g in grades.values() if g)}", flush=True)

    (HERE / "qrels.json").write_text(json.dumps(qrels, indent=2))

    systems = ["ours_bm25", "ours_hybrid", "ours_hybrid_rerank_open", "theirs"]
    lines = [
        "# External comparison — tuchtrecht, uitspraken dated 2025",
        "",
        f"{len(runs)} queries, pooled top-10 across four systems, blind Haiku-judged "
        "graded relevance (0/1/2). nDCG@10 uses gains 2^g-1; P@10 counts grade 2 only.",
        "",
        "| Query | kind | " + " | ".join(systems) + " (nDCG@10) |",
        "|---|---|" + "---:|" * len(systems),
    ]
    agg: dict[str, list[float]] = {s: [] for s in systems}
    aggp: dict[str, list[float]] = {s: [] for s in systems}
    bykind: dict[str, dict[str, list[float]]] = {}
    for qid, rec in runs.items():
        row = []
        for s in systems:
            n = ndcg_at(rec["systems"][s]["ids"], qrels[qid])
            p = prec_at(rec["systems"][s]["ids"], qrels[qid])
            agg[s].append(n)
            aggp[s].append(p)
            bykind.setdefault(rec["kind"], {}).setdefault(s, []).append(n)
            row.append(f"{n:.3f}")
        lines.append(f"| {rec['q'][:46]} | {rec['kind']} | " + " | ".join(row) + " |")

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    lines += [
        "",
        "## Means",
        "",
        "| System | nDCG@10 | P@10 | hits matched (median) |",
        "|---|---:|---:|---:|",
    ]
    for s in systems:
        tot = sorted(r["systems"][s]["total"] for r in runs.values())
        # hybrid's total is the fused POOL size, not a corpus match count — reporting
        # it beside two real match counts would read as recall it never measured.
        med = "pool-capped" if s.startswith("ours_hybrid") else str(tot[len(tot) // 2])
        lines.append(f"| {s} | {mean(agg[s]):.3f} | {mean(aggp[s]):.3f} | {med} |")

    lines += ["", "## By query kind (nDCG@10)", "",
              "| kind | " + " | ".join(systems) + " |",
              "|---|" + "---:|" * len(systems)]
    for kind, d in bykind.items():
        lines.append(f"| {kind} | " + " | ".join(f"{mean(d[s]):.3f}" for s in systems)
                     + " |")

    (HERE / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-14:]))


if __name__ == "__main__":
    main()
