# Morphology probe — does `text=` match Dutch word forms?

Hit counts over uitspraken dated 2025 on both sides, one concept per block written several ways. Our side is BM25-only, so this compares matching against matching — the dense channel is not what is being tested here.

| concept | query form | theirs | ours (bm25) |
|---|---|---:|---:|
| medicatiefout | `medicatiefout` | 0 | 0 |
| medicatiefout | `medicatiefouten` | 0 | 0 |
| medicatiefout | `fout met medicatie` | 55 | 55 |
| tandarts | `tandarts` | 66 | 66 |
| tandarts | `tandartsen` | 66 | 66 |
| tandarts | `tandheelkundige` | 25 | 40 |
| dossiervoering | `dossiervoering` | 77 | 77 |
| dossiervoering | `dossier voering` | 1 | 1 |
| dossiervoering | `bijhouden van het dossier` | 50 | 9 |
| doorhaling | `doorhaling` | 78 | 86 |
| doorhaling | `doorgehaald` | 36 | 29 |
| doorhaling | `geschrapt uit het register` | 1 | 1 |
