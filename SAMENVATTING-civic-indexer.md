# Civic Indexer — samenvatting

*Een universeel, pluggable crawl → extract → index platform voor Nederlandse
civic/overheidsdata, waarbij een AI-agent nieuwe bronnen interactief onboardt en een
deterministische engine ze daarna goedkoop draait.*

## Wat het is

Civic Indexer maakt versnipperde Nederlandse overheidsbronnen (Woo-besluiten,
tuchtrecht, raadsinformatie, vergunningen) doorzoekbaar in één moderne zoekinterface —
cross-corpus, hybride (BM25 + dense vectors + reranking), met facetten, operatoren en
document-naar-document aanbevelingen.

Het onderscheidende punt is **hoe een nieuwe bron wordt aangesloten: moeiteloos, zonder
technische kennis**. Je geeft in de onboarding-GUI (`/onboard`) een URL en een intentie
op, en een **AI-agent onderzoekt de bron zelf** — hij probet de site en haar API's,
zoekt actief naar bulk-endpoints, draait een stresstest-batterij van 13 probes tegen
bekende valkuilen, en **schrijft zelf het YAML-manifest** dat de bron beschrijft. De
mens hoeft alleen nog te beoordelen: de GUI toont probe-resultaten als kaarten en het
manifest als diff, met een expliciete approve / changes / refuse-gate. Daarna draait
een deterministische engine de bron goedkoop en herhaalbaar; alle moeilijk verworven
kennis over caps, ledgers, reconciliatie en budgetten zit eenmalig in die engine, nooit
in per-bron code. Het project is dus geen verzameling losse scrapers, maar een platform
waarin een adapter *data* is — geschreven door de agent, gecontroleerd door een mens.

Onder de motorkap zitten twee zelf ontwikkelde, **betaalbare en schaalbare
verwerkingspipelines**. De **OCR-pipeline** maakt gescande overheids-PDF's doorzoekbaar
zonder ooit het indexeren te blokkeren: kop-pagina's inline tijdens extractie, de rest
gebatcht gedraind op kortlevende cloud-GPU-pods (burst pods) met een open-weights
OCR-VLM (MinerU2.5-Pro, gekozen via A/B-benchmark) — **≈ $17 per miljoen pagina's,
~86× goedkoper dan Azure** bij vrijwel gelijke kwaliteit, en de doorvoer is per pod
uitgebenchmarkt tot ~95% van het theoretische plafond. De **embedding-pipeline** volgt
hetzelfde burst-patroon: een self-hosted open embedder (bge-m3, gekozen uit een A/B met
zes modellen op het echte corpus) encodeert vectoren op een pod in minuten in plaats
van uren lokaal, cachet ze idempotent in Postgres, en projecteert ze naar de zoekindex.
Doordat de reranker een losgekoppelde API-call is, draait de hele zoekstack in
productie **zonder vaste GPU-kosten** (~$0,002 per zoekopdracht).

Het draait end-to-end: ~49k documenten uit vier bronnen, een zoek-UI met
monitoringdashboard (`/monitor`) en de onboarding-GUI, gemeten kwaliteit
(hybride zoek verslaat de eigen zoekfunctie van tuchtrecht.overheid.nl met **0.656 vs
0.383 nDCG@10** in een geblindeerde vergelijking).

## De unieke benaderingswijze

### 1. Empirisch afgeleid, niet bedacht
Elke ontwerpkeuze is gedestilleerd uit **vier eerdere, werkende crawlerprojecten**
(Woo, SRC/Reclame Code, Vergunningen Groningen, Gemeentes/Notubiz), gedocumenteerd in
`documented_existing_indexers/`. Uit ~100 per-bron beslissingen is afgeleid wat
universeel is (→ engine-invariant) en wat per bron verschilt (→ manifest). Negatieve
kennis — verworpen identity keys, kapotte filtervocabulaires, mislukte alternatieven —
wordt vastgelegd, niet weggegooid.

### 2. Engine/manifest-splitsing: adapters als data, geschreven door een agent
Een bron-adapter is geen code maar een gevalideerd YAML-manifest: configuratie +
strategiekeuzes + beperkte expressies. Een **AI-agent** (Claude Agent SDK) onboardt een
nieuwe bron via probe → manifest → verify → schedule, met een 13-probes stresstest-
batterij die uit de "war stories" van de vier projecten is gedistilleerd. De hypothese
is gemeten: onboarding van drie nieuwe bronnen kostte **5 / 2 / 0 menselijke
interventies** (Stage B: GO). Nieuwe bronmechanieken worden *strategieën binnen
bestaande hooks*, nooit nieuwe pipeline-stages.

### 3. Compleetheid is een reconciliatieprobleem
Elke onderzochte bron (5/5) verbergt een stille resultaat-cap. "Succes" is daarom nooit
"draaide zonder exceptions" maar een door de engine berekend **reconciled**-oordeel:
items-in versus items-uit moet kloppen, anders stopt de run als `NOT_reconciled`.
`civic check-ledger` en `civic check-projection` zijn staande controles met een
dashboard-trigger, zodat drift niet stil kan blijven.

### 4. Postgres = grondwaarheid, OpenSearch = herbouwbare projectie
De ledger in Postgres is de enige bron van waarheid; de zoekindex is volledig
herbouwbaar (`civic project --recreate`). Dat maakt schemawijzigingen, analyzer-fixes
en takedowns (`civic suppress`) goedkoop en omkeerbaar — en het is de basis onder het
toegangsbeleid (zie punt 6).

### 5. Cross-corpus zoeken zonder gedeelde index
Elke bron blijft z'n eigen index; zoeken is standaard een **query-time union** over
`civic-*`. Zo blijft de per-bron herbouwbaarheid intact terwijl de gebruiker (bijv. een
journalist) over alle corpora tegelijk zoekt, met kNN-"gerelateerde documenten" over
corpusgrenzen heen (17 van 100 gesamplede documenten hebben een cross-source buur in
hun top-8).

### 6. Toegangsbeleid als expliciet framework, nooit agent-beslist
Ambiguïteit over crawl-consent bleek de *normale* toestand van een Nederlandse civic
bron (4/4 bronnen serveren `robots.txt: Disallow: /`). Het antwoord is
`design/access-posture.md`: twee assen — **acquisitie** (`go`/`hold`/`no`, duur om
terug te draaien) en **exposure** (`T0` metadata-only t/m `T3` herdistribueerbaar,
goedkoop om terug te draaien dankzij de herbouwbare projectie). Elke manifest draagt
beide velden, validatie is fail-closed, en elke wijziging is een **menselijke
admin-beslissing** (POL-1). Ook third-party processors (zoals de Voyage-reranker) zijn
per bron opt-in — tuchtrecht verbiedt ze, dus daar degradeert de stack netjes naar
`hybrid` met een gemelde reden.

### 7. Meten vóór bouwen — ook als dat features schrapt
- Query-expansie (decompounder) werd **niet** gebouwd: meting toonde dat de hybride
  stack Nederlandse samenstellingen al oplost (`tandartsklacht`: BM25 0 hits → hybrid
  100, top-10 precisie 10/10).
- De reranker-A/B verwierp het open model (`bge-reranker-v2-m3`, +0.071) ten gunste van
  Voyage rerank-2.5 (+0.187 nDCG@10).
- Een stemmer-defect (`tandarts` vs `tandartsen` ontmoetten elkaar nooit) werd gevonden
  via de externe vergelijking en gefixt (66/66 i.p.v. 20/66 hits).
- Regressietests draaien tegen **opgenomen fixtures**, nooit tegen live sites.

### 8. Kosten gedisciplineerd via burst-compute en twee-tier verwerking
- **OCR** is nooit blokkerend: inline head-pagina's tijdens extractie, de staart wordt
  periodiek gedraind op goedkope GPU-burst-pods met open-weights OCR-VLM's
  (MinerU2.5-Pro, ~86× goedkoper dan Azure).
- **Embeddings** (bge-m3, self-hosted) en **rerank-runs** rijden hetzelfde
  burst-patroon: 12×100 pairs = 15 s op een 3090-pod vs ~49 min lokaal op CPU.
- **Hergebruik boven her-crawlen**: 3.771 documenten werden geadopteerd uit een eerder
  project (join bewezen uniek én onafhankelijk gefalsifieerd) in plaats van opnieuw
  gefetcht op ~1 rps.
- **Scope beperkt uitgaven, nooit kennis**: alles wordt ontdekt, alleen het gescopede
  deel verwerkt; de rest blijft als metadata-stub beschikbaar voor latere expansie.

## Zoekfeatures (gebruikerskant)

| Feature | Status |
|---|---|
| Hybride zoek (BM25 + dense RRF), default met rerank | ✅ live |
| Cross-corpus union over alle bronnen, `source=` als gewone facet | ✅ live (criterium #3) |
| Zoekoperatoren: `"exacte zin"`, AND/EN, OR/OF, NOT/-, groepen, wildcard* | ✅ live, fail-open |
| Query-understanding: vrije tekst → verwijderbare filterchips (deterministisch, geen LLM) | ✅ live (criterium #4) |
| Gerelateerde documenten (doc-naar-doc kNN, cross-corpus) | ✅ live |
| Monitoring-dashboard (`/monitor`) met ledger- en schemachecks | ✅ live |
| Onboarding-GUI (`/onboard`): probe-cards, manifest-diff, approve/refuse-gates | ✅ gebouwd; live run nog verschuldigd |
| Self-healing, scheduling + ochtenddigest, correctieloop, dossiers/CSV | ⏳ roadmap W7–W15 |

## Status (2026-08)

Stages A (walking skeleton) en B (agent-onboarding) zijn **geslaagd**. Stage C
(clienttest) is bewust uitgesteld tot "vision-readiness", bewaakt door zeven
exitcriteria waarvan er **4,5 gehaald** zijn. Open staan: de live onboarding-run door
de GUI (#6), de rerank-clausule van de externe vergelijking (#2, geblokkeerd op een
admin-beleidsbeslissing over Cohere-EU), en criterium #1. De actieve planning staat in
`design/product-roadmap.md`; het ontwerpdossier begint bij `design/README.md`.
