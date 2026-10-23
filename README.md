# Civic Indexer

*Een universeel, pluggable crawl → extract → index platform voor Nederlandse
civic/overheidsdata, waarbij een AI-agent nieuwe bronnen interactief onboardt en een
deterministische engine ze daarna goedkoop draait.*

Civic Indexer maakt versnipperde Nederlandse overheidsbronnen (Woo-besluiten,
tuchtrecht, raadsinformatie, vergunningen) doorzoekbaar in één moderne zoekinterface —
cross-corpus, hybride (BM25 + dense vectors + reranking), met facetten, operatoren en
document-naar-document aanbevelingen.

Het onderscheidende punt is **hoe een nieuwe bron wordt aangesloten: moeiteloos,
zonder technische kennis**. Je geeft in de onboarding-GUI een URL en een intentie op,
en een AI-agent onderzoekt de bron zelf — hij probet de site en haar API's, zoekt
actief naar bulk-endpoints, draait een stresstest-batterij tegen bekende valkuilen, en
schrijft zelf het declaratieve YAML-manifest dat de bron beschrijft. De mens beoordeelt
alleen nog: probe-resultaten als kaarten, het manifest als diff, en een expliciete
approve / changes / refuse-gate. Daarna draait de engine de bron goedkoop en
herhaalbaar; adapters zijn *data*, geen code.

**Lees verder:**

- [`SAMENVATTING-civic-indexer.md`](SAMENVATTING-civic-indexer.md) — wat het is, de unieke benaderingswijze, de
  featuretabel en de actuele status.
- [`design/README.md`](design/README.md) — het ontwerpdossier: kernbets, leesvolgorde,
  genomen beslissingen.
- [`idee.md`](idee.md) — het oorspronkelijke idee en de doelen.

Licentie: [EUPL-1.2](LICENSE), de licentie die de Rijksoverheid zelf aanbeveelt,
OSI-goedgekeurd, met een rechtsgeldige Nederlandse tekst.

## Waar we staan

Drie blokken: wat werkt en gemeten is, wat een aanvraag zou betalen, en wat daarna
komt. Elk cijfer hieronder is terug te vinden in een bestand in deze repo.

### Werkt

- **De pipeline, end-to-end, op vier bronnen.** Stage A (walking skeleton) en Stage B
  (agent-onboarding: 5 interventies bij de eerste bron, 2 bij de tweede, 0 bij de derde,
  [`experiments/stage_b/intervention-log.md`](experiments/stage_b/intervention-log.md))
  zijn geslaagd. Stand van de ledger op 28 juli 2026
  ([`design/product-roadmap.md`](design/product-roadmap.md)):

  | bron | ontdekt | met tekst | vectoren |
  |---|---:|---:|---:|
  | `tuchtrecht` (tuchtrecht.overheid.nl) | 47.950 | 47.950 | 47.950 |
  | `woo_besluiten` (open.overheid.nl) | 26.628 | 4.185 | 4.185 |
  | `haarlem_raad` (Notubiz) | 11.622 | 1.017 | 1.000 |
  | `vlaardingen_raad` (Notubiz) | 89 | 24 | 24 |

  Alleen `tuchtrecht` is af. Wat de rest van `woo_besluiten` tegenhoudt is een regex en
  een toegangsbeslissing, geen crawlsnelheid; de roadmap zegt precies welke.
- **Gemeten beter dan het eigen zoekvak van de bron.** Hybride zoeken (BM25 + bge-m3)
  scoort **0,656 nDCG@10 tegen 0,383** voor de zoekfunctie van tuchtrecht.overheid.nl,
  twaalf vragen, 2.504 uitspraken uit 2025 aan beide kanten, blind en gepoold beoordeeld.
  Cijfers, vragen, oordelen en het script staan in
  [`experiments/external_comparison/`](experiments/external_comparison/FINDINGS.md);
  de UI serveert ze leesbaar op `/bewijs`. De vergelijking vond en verhielp een fout in
  de standaard Nederlandse stemmer (`tandartsen` vond 20 van 66, nu 66 van 66).
- **Tests.** `uv run pytest`: 452 geslaagd, 1 overgeslagen (8 september 2026), tegen
  opgenomen fixtures, zonder live sites. Gecontroleerd vanaf een schone clone met alleen
  `uv sync --group dev --extra ui`.
- **Ervaring.** Vier eerdere, werkende crawlers (Woo, SRC, vergunningen, gemeenten) zijn
  het empirische fundament van het ontwerp:
  [`documented_existing_indexers/`](documented_existing_indexers/).
- **Toegang en verantwoording.** Elke bron draagt een expliciete harvest- en
  exposure-posture, fail-closed gevalideerd ([Toegangsbeleid](#toegangsbeleid)).
  Modellen: open gewichten, zelf gehost; data op een eigen server in de EU.

### Betaalt de aanvraag

Wat er gevraagd wordt is steeds een nieuw, afgebakend stuk; niets hieronder is al
gebouwd of betaald. Details per fonds in
[`funding/samenvatting-financiering-nl.md`](funding/samenvatting-financiering-nl.md).

- **SIDN fonds Pioniers** (≤ €10k, ≤ 6 maanden): één open, herbruikbaar gereedschap. De
  kandidaten zijn de Nederlandse analyzer met de stemmerfix als OpenSearch- of
  Elasticsearch-pakket, of een machineleesbaarheidsscan van Woo-publicaties.
- **MCOIG call 2027** (≤ €50k): een tweede bron van een bestuursorgaan live aansluiten
  via het onboarding-scherm, met die organisatie als landingsplek, en de cross-corpus
  zoekopdracht daarna.
- **Innovatiebudget Digitale Overheid 2027** (via twee overheden, één regiepartij): de
  roadmap-workstreams die het systeem van één beheerder naar meerdere organisaties
  brengen: self-healing (W7), scheduling met dagelijks overzicht (W8) en de
  correctielus op kaartniveau (W9).

### Komt daarna

Uit [`design/product-roadmap.md`](design/product-roadmap.md), W7 tot W15. Elk item staat
in het oorspronkelijke idee en is niet gebouwd.

- **W7 Self-healing**: een veranderde bron zelf repareren; nu stopt een run veilig
  (`NOT_reconciled`) en repareert niets.
- **W8 Scheduling + ochtendoverzicht**: `schedule:` in het manifest, een uitvoerder, één
  regel per bron per dag.
- **W9 Correctielus op kaartniveau**: een fout veld aanklikken, de agent past de
  extractie aan, de kaart hertekent zonder nieuwe crawl.
- **W10 Goedkope-LLM-verrijking**: samenvattingen, filteropties, claims; de hook
  `enriched` bestaat, niets vult hem.
- **W12 Dossiergroepering en CSV-export**: beroepsketens in één beeld.
- **W13 Automatisch bepaalde facetten**: afgeleid uit de data, niet gedeclareerd.
- **W14 Scope-predicaten**: ontworpen, niet in de engine.
- **W15 Semantische analyse** op collectieniveau: het nevendoel, onderzoeksvormig.
- **Open exitcriteria**: één live onboarding via de GUI (elke run tot nu toe is de
  gratis `replay`-driver), en de rerank-clausule, die een verwerkersbeslissing per bron
  vraagt.

## Quickstart

Vereisten: Python 3.12 + [uv](https://docs.astral.sh/uv/), Postgres 16 (lokale socket,
database `civic_indexer`), OpenSearch 2.19 via
`docker/opensearch/docker-compose.yml`. Verbindingsinstellingen in `.env`
(`DATABASE_URL`, `OPENSEARCH_URL`).

```bash
uv sync --group dev --extra ui --extra embed --extra agent
uv run pytest                        # tests draaien tegen opgenomen fixtures, geen live sites
uv run civic migrate                 # migrations/*.sql toepassen

# Pipeline draaien voor een bron
uv run civic run woo_besluiten --phase discover --window 2026-06
uv run civic run woo_besluiten --phase fetch,extract --window 2026-06 --limit 200
uv run civic embed woo_besluiten     # bge-m3 vectoren naar de PG-cache (idempotent)
uv run civic project woo_besluiten   # herbouwbare OpenSearch-projectie

# Controles
uv run civic check-ledger            # klopt de ledger nog met zichzelf?
uv run civic check-projection        # matchen de live mappings nog met mappings.py?

# UI (zoek + /monitor + /onboard)
uv run uvicorn ui.search_api:app --port 8080
```

Nieuwe bron onboarden (agent-gedreven):

```bash
uv run civic onboard <url> --intent "..." --source <id> --dry-run
# zonder --dry-run: echte run — vereist --extra agent, de `claude` CLI en een API-key
```

Of via de GUI op `http://localhost:8080/onboard`.

## Repostructuur

| Pad | Inhoud |
|---|---|
| `engine/` | De deterministische engine: strategies, extract, project, onboarding-runtime |
| `manifests/` | Declaratieve YAML-bronbeschrijvingen (agent-geschreven, mens-gekeurd) |
| `migrations/` | Plain-SQL migraties; Postgres is de grondwaarheid |
| `ui/` | Zoek-UI + FastAPI-backend, incl. `/monitor` en `/onboard` |
| `design/` | Het ontwerpdossier — begin bij `design/README.md` |
| `documented_existing_indexers/` | De empirische basis: vier eerdere werkende crawlerprojecten (read-only) |
| `experiments/` | Gemeten A/B's: embeddings, rerankers, OCR-engines, externe vergelijking |
| `tests/fixtures/` | Opgenomen API-pagina's — de regressiemethode |
| `registries/` | Reviewbare data zoals het aggregator-register |

## Toegangsbeleid

Crawl-consent is bij Nederlandse civic bronnen zelden eenduidig. Elke bron draagt
daarom een expliciete, door de admin genomen posture op twee assen — acquisitie
(`go`/`hold`/`no`) en exposure (`T0` t/m `T3`) — fail-closed gevalideerd en afgedwongen
in projectie en API. Zie [`design/access-posture.md`](design/access-posture.md).
