# External comparison — tuchtrecht, uitspraken dated 2025

12 queries, pooled top-10 across four systems, blind Haiku-judged graded relevance (0/1/2). nDCG@10 uses gains 2^g-1; P@10 counts grade 2 only.

| Query | kind | ours_bm25 | ours_hybrid | ours_hybrid_rerank_open | theirs (nDCG@10) |
|---|---|---:|---:|---:|---:|
| tandarts | lexical | 1.000 | 1.000 | 1.000 | 0.875 |
| doorhaling | lexical | 0.907 | 0.596 | 1.000 | 0.527 |
| medicatiefout | lexical | 0.000 | 0.666 | 0.494 | 0.000 |
| onvoldoende dossiervoering | concept | 0.669 | 0.750 | 0.741 | 0.237 |
| seksueel grensoverschrijdend gedrag | concept | 0.934 | 1.000 | 0.811 | 0.909 |
| informed consent ontbrak | concept | 0.677 | 0.640 | 0.822 | 0.414 |
| declaratiegeschil advocaat | concept | 0.524 | 0.528 | 0.533 | 0.504 |
| mag een huisarts weigeren een verwijzing te ge | natural | 0.399 | 0.592 | 0.618 | 0.270 |
| klacht over te late diagnose van kanker | natural | 0.400 | 0.457 | 0.806 | 0.425 |
| notaris passeerde een akte bij belangenverstre | natural | 0.000 | 0.574 | 0.458 | 0.122 |
| dierenarts die een paard onnodig heeft laten i | natural | 0.000 | 0.242 | 0.393 | 0.000 |
| accountant keurde de jaarrekening goed zonder  | natural | 0.315 | 0.826 | 0.701 | 0.315 |

## Means

| System | nDCG@10 | P@10 | hits matched (median) |
|---|---:|---:|---:|
| ours_bm25 | 0.485 | 0.367 | 14 |
| ours_hybrid | 0.656 | 0.450 | pool-capped |
| ours_hybrid_rerank_open | 0.698 | 0.517 | pool-capped |
| theirs | 0.383 | 0.317 | 34 |

## By query kind (nDCG@10)

| kind | ours_bm25 | ours_hybrid | ours_hybrid_rerank_open | theirs |
|---|---:|---:|---:|---:|
| lexical | 0.636 | 0.754 | 0.831 | 0.467 |
| concept | 0.701 | 0.730 | 0.727 | 0.516 |
| natural | 0.223 | 0.538 | 0.595 | 0.226 |
