# Crawl / Scrape / Index — architectuur en geleerde lessen

Dit document beschrijft hoe deze pijplijn publieke bronnen over kapvergunningen
in gemeente Groningen *crawlt*, *scraped* en *indexeert*. Het is bewust
geschreven met een tweede doel voor ogen: een gevoel krijgen voor hoe een
**universeel, pluggable crawl/scrape/index-systeem** eruit zou moeten zien, en
tegen welke soorten randgevallen zo'n systeem bestand moet zijn. Overal waar we
een concrete oplossing kozen, staat expliciet welke *algemene* eis daaronder
schuilt.

> **Codekaart.** De relevante modules: `sru_client.py`, `scraper.py`,
> `monitor.py`, `historisch.py`, `bomenbestand.py`,
> `bomenbestand_changelog.py`, `fetch_arcgis.py`, `db.py`, `extract.py`,
> `normalize.py`, `config.py`.

---

## 1. Waarom dit lastiger is dan "download de pagina"

De centrale complicatie is **vluchtigheid**: besluitbrieven en bijlagen staan
*alleen* op lokalebekendmakingen.nl gedurende de 6-weken bezwaartermijn. Daarna
worden ze verwijderd. Er is geen archief. Dat draait de gebruikelijke prioriteit
om:

- Een normale crawler mag rustig achterlopen — de bron blijft staan.
- Hier is **100% dekking binnen een tijdsvenster** de harde eis. Een document dat
  we vandaag missen is over zes weken *permanent* weg.

Daaruit volgt bijna alles: dagelijkse cadans, idempotentie (opnieuw draaien mag
niets kapotmaken en niets overslaan), en het feit dat de "index" (SQLite) de
enige duurzame kopie is.

> **Universele eis #1 — Time-to-live per bron.** Een pluggable systeem moet per
> bron kunnen weten of content *persistent* of *vluchtig* is, en met welk
> venster. Dat bepaalt crawlfrequentie, retry-agressie, en of falen "later nog
> eens proberen" of "voorgoed verloren" betekent.

---

## 2. De drie datavlakken

De pijplijn combineert bronnen met heel verschillende toegangsmodellen. Dat is
precies waarom een universeel systeem *per-bron-adapters* nodig heeft in plaats
van één crawl-strategie.

| Bron | Protocol | Vorm | Paging | Vluchtig? |
|------|----------|------|--------|-----------|
| KOOP SRU 2.0 (`repository.overheid.nl`) | SRU/HTTP, JSON | metadata-records | `nextRecordPosition` | nee (archief) |
| lokalebekendmakingen.nl (Grexx Atalanta) | HTML-pagina + file-download | PDF's, afbeeldingen | n.v.t. (per case) | **ja (6 wk)** |
| Bomenbestand WFS (`maps.groningen.nl`) | OGC WFS 1.0, GeoJSON | ~122k punt-features | `maxFeatures` (bulk) | levend (muteert dagelijks) |
| ArcGIS FeatureServer (org `chCSiGO4ORzXeSGk`) | ArcGIS REST, GeoJSON | project-lagen | `resultOffset` | levend |

Elk vlak heeft zijn eigen "index"-tabel in dezelfde SQLite-DB (`db.py` +
migraties in de losse modules), zodat downstream (dashboard, rapporten) alles
via één query-oppervlak ziet.

> **Universele eis #2 — Adapter per protocol, uniforme sink.** De crawl-laag is
> per bron verschillend; de opslag-/index-laag is gedeeld. De naad daartussen is
> een genormaliseerd record. Zie §7.

---

## 3. Crawlen: paging en volledigheid per bron

### 3.1 SRU — de subtiliteiten van `nextRecordPosition`

`sru_client.py::_fetch_paginated` is de kern. De niet-triviale details die 100%
dekking bepalen:

- **CQL-query als filter-aan-de-bron.** We halen niet alles op en filteren
  lokaal; we duwen het filter naar de API:
  ```
  c.product-area==officielepublicaties
  AND dt.creator=="Groningen"
  AND (dt.title=vellen OR dt.title=kappen OR dt.title=houtopstand
       OR w.activiteit=="kappen")
  ```
  De `w.activiteit=="kappen"`-tak is later toegevoegd omdat titel-matching alleen
  records mist die wél als kap-activiteit getagd zijn maar een afwijkende titel
  hebben. **Les:** trefwoord-filters op één veld lekken; combineer met
  gestructureerde velden waar de bron die heeft.
- **Paging via server-cursor, niet via offset-rekenen.** We volgen
  `nextRecordPosition` uit het antwoord en stoppen pas als die ontbreekt óf
  voorbij `numberOfRecords` wijst. We berekenen de volgende `startRecord` *niet*
  zelf uit `pageSize` — de server is de bron van waarheid over de cursor. Dat is
  robuuster tegen records die tijdens het pagineren verdwijnen/bijkomen.
- **Twee query-modi, twee sorteringen.**
  - *Dagelijks* (`fetch_recent_kapvergunningen`): `dt.modified>=<since>`,
    `LOOKBACK_DAYS=60`, gesorteerd **descending**, pagegrootte 100. Ruime
    overlap zodat late publicaties/wijzigingen alsnog binnenkomen.
  - *Historisch* (`fetch_all_kapvergunningen`): geen datumfilter, pagegrootte
    1000, gesorteerd **ascending** op `dt.modified`, met `delay=2.0s` tussen
    pagina's. Oplopend sorteren geeft een stabiele volgorde bij een groeiende
    dataset (nieuwe records komen achteraan, verschuiven bestaande niet).
- **Polymorfe JSON.** `records.record` is soms een lijst, soms één dict; idem
  `gebiedsmarkering` en de `Punt`-subboom (`_get_punt` normaliseert
  list-vs-dict). **Les:** REST/JSON-bronnen zijn vaak niet type-stabiel bij n=1.
  Een universele parser moet "singleton wordt geen lijst" als standaard-randgeval
  behandelen.

### 3.2 De ~april-2024 breuk — de moeilijkste dekkingsgrens

De case-URL (waarmee we bij de documenten komen) staat in SRU-veld
`tpmeta.terinzageleggingBG`. **Dat veld bestaat pas sinds ~april 2024.** Alle
oudere records hebben wél metadata maar géén weg naar hun documenten.

Consequentie in `monitor.py`: records zonder `case_url` worden geteld als
`skipped_no_case` en de metadata wordt tóch opgeslagen (`upsert_vergunning` draait
altijd, vóór de skip-checks). We indexeren dus de *kennis dat de vergunning
bestaat* ook als we het document nooit kunnen krijgen. Voor de historische diepte
gebruiken we in plaats daarvan **titel-parsing** (§7.2) als surrogaat voor de
inhoud.

> **Universele eis #3 — Schema-drift over tijd.** Dezelfde bron levert door de
> jaren heen verschillende velden. Een record dat een deel-pipeline niet kan
> voeden (hier: geen document) mag niet worden weggegooid — het moet als
> "gedeeltelijk" geïndexeerd blijven, met expliciete registratie van wat ontbreekt.

### 3.3 WFS bomenbestand — bulk in één klap, met fallback-geometrie

`bomenbestand.py::fetch_bomenbestand` haalt het volledige bestand (~122k bomen)
in één request op (`maxFeatures=200000`, 300s timeout). Geen paging: WFS 1.0 op
deze server levert alles in één GeoJSON-document. Randgevallen bij het inlezen
(`store_bomen`):

- **Coördinaten uit twee mogelijke plekken.** Eerst `properties.LAT/LON`;
  ontbreken die, dan terugvallen op `geometry.coordinates`. Features zonder enige
  coördinaat worden overgeslagen.
- **Type-coercion.** `KIEMJAAR` wordt defensief naar `int` geparsed of `None`.
- **Batched insert** in blokken van 5000 (`executemany`) — zie §8.

### 3.4 ArcGIS — discovery-by-convention + echte offset-paging

`fetch_arcgis.py` is het meest "crawler-achtig": het *ontdekt* welke lagen
bestaan in plaats van een vaste lijst af te lopen.

- **Discovery via regex op servicenamen.** `list_services()` enumereert álle
  FeatureServers in de org en classificeert ze met `CATEGORIES`-patronen
  (`Kapprogramma_...`, `essentaksterfte`, `Snoeikaart`, ...). Nieuwe jaargangen
  zoals `Kapprogramma_zomer_2026` worden **automatisch** meegenomen — geen
  code-wijziging. `EXCLUDE_PATTERNS` filtert ruis (bv. het bomenbestand dat we al
  via WFS krijgen — bewuste anti-dubbeling tussen bronnen).
- **Sublayer-selectie.** Per service kiezen we de eerste punt/polygon-laag; valt
  terug op `layers[0]`.
- **Echte paging** (`fetch_features`): `resultOffset` + `resultRecordCount=2000`,
  doorgaan tot een pagina korter is dan de pagegrootte, met een **sanity-cap** op
  100.000 features (breekt af en logt i.p.v. eindeloos door te pagineren als de
  API zich misdraagt).
- **Volledigheid = stop-conditie.** "Laatste pagina" wordt herkend aan
  `len(batch) < PAGE_SIZE`. Dat is de universele valkuil: als de server exact een
  veelvoud teruggeeft, doe je één lege extra request — acceptabel; als de server
  liegt over counts, red de sanity-cap je.

> **Universele eis #4 — Bron-discovery als first-class stap.** Niet elke bron is
> een vaste URL-lijst. Een pluggable systeem moet een *enumerate → classify →
> filter → fetch* fase kennen, waarbij classify/exclude datagedreven regels zijn
> zodat nieuwe deelbronnen zonder code binnenkomen.

---

## 4. Scrapen: van case-pagina naar bestanden

`scraper.py` haalt de HTML van een case-pagina en extraheert download-links met
één regex tegen een bekend URL-prefix
(`https://groningen.lokalebekendmakingen.nl/downloads/atalanta/9822/...`). De
lessen die 100%-dekking hier compliceerden:

- **Dubbel-URL-gecodeerde bestandsnamen.** De echte bestandsnaam zit in een
  `filename=`-query-parameter en is *twee keer* ge-URL-encodeerd
  (`%2520` → `%20` → spatie). `_decode_filename` doet daarom `unquote(unquote(...))`.
  Zonder de dubbele decode krijg je onbruikbare namen. Fallback op het
  hash-deel van de URL als er geen `filename=` is.
- **Platform-assets uitfilteren.** De pagina bevat ook `logo.png`, `favicon.ico`,
  CSS enz. We whitelisten op extensie (`.pdf`, `.jpg`, `.jpeg`, `.png`) en
  blacklisten expliciete asset-namen. **Alleen documenten, geen chrome.**
- **Besluitbrief-herkenning** (`_is_besluitbrief`) op trefwoorden in de naam
  (`besluitbrief`, `besluit omgevingsvergunning`) — later gebruikt door
  `extract.py` om te weten welk PDF de LLM in moet.
- **Bestandsnaam-sanitatie** (`_sanitize_filename`) tegen padscheidings- en
  Windows-verboden tekens vóór ze op schijf belanden.
- **De-duplicatie binnen de pagina** via een `seen_urls`-set (dezelfde link komt
  vaak meerdere keren voor in de HTML).

> **Universele eis #5 — Extractie-heuristieken zijn per-bron plugins.** De
> URL-prefix, de dubbele encoding, de asset-blacklist: allemaal bron-specifiek.
> Een universeel systeem levert dit als een *link-extractor + filename-decoder +
> classifier*-drieluik dat per bron te configureren is, niet als hardcoded
> globale logica.

---

## 5. Zip-bestanden en bundels — bewust *niet* uitpakken

Dit project ontvangt geen zip-/archiefbundels, en dat is een **ontwerpkeuze**,
geen toeval:

- De scraper downloadt **individuele bestanden** (per-document URL's), niet één
  gebundeld archief. De extensie-whitelist (`.pdf/.jpg/.jpeg/.png`) sluit
  `.zip`/`.tar` expliciet uit — een zip zou dus genegeerd worden, niet
  uitgepakt.
- Bulk-geodata (WFS, ArcGIS) komt als **één GeoJSON-document** binnen, niet als
  gecomprimeerd archief. We streamen dat direct in het geheugen naar de parser en
  daarna in batches naar SQLite; er wordt **niets naar een temp-directory
  uitgepakt** (zie §8).

Waarom dit relevant is voor een universeel systeem: zodra een bron *wél* zips
levert (denk aan een KOOP-eventfile-bundel of een dataset-dump), heb je een extra
laag nodig die dit project nog niet heeft. De eisen die dan ontstaan:

> **Universele eis #6 — Container-formaten als aparte "unpack"-stap.** Een
> pluggable systeem moet archieven (zip/tar/gzip) *streamend* kunnen uitpakken —
> lezen uit een `BytesIO`/stream, per lid verwerken, en het lid weer vrijgeven —
> in plaats van eerst het hele archief naar schijf te schrijven en dan te
> extraheren. Elk uitgepakt lid loopt vervolgens door dezelfde
> classify/dedup/index-pijplijn als een los gedownload bestand. De dedup-sleutel
> (§6) moet zowel op archiefniveau als op lid-niveau werken, zodat een herhaalde
> bundel niet elk lid opnieuw verwerkt.

*(Referentie voor een toekomstige bron: de KOOP-eventfiles op
`repository.officielebekendmakingen.nl/officielepublicaties/_events/` leveren
dagelijkse mutatiebestanden — dat is het meest waarschijnlijke punt waarop
archief-/bundelverwerking nodig wordt.)*

---

## 6. Dedup-beslissingen — over meerdere webbronnen en over tijd

Deduplicatie gebeurt op meerdere niveaus, elk met een andere sleutel. Dit is een
van de subtielste onderdelen.

1. **Identiteit van een vergunning = SRU `identifier` (GMB-id).**
   `upsert_vergunning` doet `INSERT ... ON CONFLICT(id) DO UPDATE` — opnieuw
   ophalen actualiseert, dupliceert niet. De primaire sleutel is de natuurlijke
   bron-id.
2. **Al-compleet-skip.** `vergunning_exists` checkt de vlag
   `documents_downloaded`; is die gezet, dan slaat `monitor.py` de dure
   scrape+download over. Werk dat af is, wordt niet herhaald.
3. **Bestand-op-schijf-skip.** Vóór elke download: `if dest.exists(): continue`.
   Idempotent op bestandsniveau, ook binnen een half-afgemaakte run.
4. **Link-dedup binnen een pagina** via `seen_urls` (§4).
5. **Dossier-dedup over titel-varianten** (`transparantie_rapport.py::
   get_grote_vergunningen`). Eén fysiek dossier verschijnt vaak als twee records:
   de *aanvraag* en het *besluit ("verleend")*, met licht verschillende titels.
   We ontdubbelen op `case_id`; ontbreekt die, dan op een uit de titel
   geëxtraheerd dossiernummer (`\d{9,}`); anders op het record-id. **Les:**
   dezelfde entiteit kan onder meerdere records/titels verschijnen; dedup vergt
   een *stabiele zaak-sleutel* die losstaat van de record-id.
6. **Cross-bron anti-dubbeling.** Het bomenbestand komt uit WFS; de
   ArcGIS-discovery sluit `Bomen_gemeente_Groningen` expliciet uit
   (`EXCLUDE_PATTERNS`) zodat dezelfde data niet via twee bronnen dubbel
   binnenkomt.
7. **Temporele dedup = delta-detectie** (§6.1).

### 6.1 Delta i.p.v. volledige historie (bomenbestand_changelog.py)

Het bomenbestand muteert dagelijks. We bewaren *niet* elke dag een volledige
kopie (122k × 365 = onwerkbaar). In plaats daarvan:

- Vergelijk de verse WFS-fetch (dict op `OBJECT`-id) met de opgeslagen stand
  (`detecteer_wijzigingen`): set-verschillen geven **verdwenen** en **nieuw**;
  per gemeenschappelijke id vergelijkt `VERGELIJK_VELDEN` de attributen voor
  **gewijzigd** (met een JSON-diff in `details`).
- Alleen de *deltas* gaan naar `bomen_changelog`; de `bomen`-tabel wordt daarna
  vervangen door de actuele stand (`werk_bomen_bij`). Zo groeit de opslag met de
  verandering, niet met de omvang.
- **Idempotentie per dag:** `bomen_snapshot_log` houdt bij of we vandaag al
  draaiden; een tweede run op dezelfde dag wordt overgeslagen (tenzij `--force`).

> **Universele eis #7 — Dedup is meerlaags en bron-afhankelijk.** Minimaal:
> (a) natuurlijke-sleutel-upsert, (b) reeds-verwerkt-skip, (c) entiteit-over-
> record-varianten met een afgeleide stabiele sleutel, (d) cross-bron-exclusie,
> (e) temporele delta i.p.v. snapshots. Een universeel systeem moet elk van deze
> als configureerbare strategie aanbieden.

---

## 7. Indexeren en metadata-extractie

De "index" is SQLite (`db.py`), met per data-vlak een eigen tabel-set en
idempotente migraties (`ALTER TABLE ... ADD COLUMN` in een try/except — de
goedkoopste vorm van schema-evolutie).

### 7.1 Metadata uit gestructureerde bron (SRU)

`_parse_record` normaliseert elk SRU-record naar één platte dict: `id`, `title`,
`modified_date`, `case_url`, `case_id` (afgeleid: laatste segment van de URL na
`:`), adres/postcode, lat/lon, `type` (regex-classificatie
verleend/geweigerd/aanvraag/onbekend), en `deadline_bezwaar`. Falen op één record
wordt gelogd en levert `None` — één kapot record breekt de crawl niet.

### 7.2 Metadata uit ongestructureerde bron (LLM)

Waar de bron vrije tekst is, gebruiken we een LLM (Gemini Flash Lite) i.p.v.
regex. Twee toepassingen:

- **Titel-parsing** (`historisch.py`): batches van 50 titels → JSON met
  `is_bomenkap`, `aantal_bomen_titel`, `boomsoort_titel`, `locatie_type`,
  `privaat_indicator`. Regex (`parse_titel_regex`) blijft bestaan als *fallback*
  voor records die de LLM mist én als offline modus (`--regex-only`). Een
  belangrijke functie is **false-positive-filtering**: "kap" betekent vaak een
  *dakconstructie*, niet bomenkap — de LLM classificeert dat expliciet weg.
- **PDF-extractie** (`extract.py`): de besluitbrief-PDF gaat als bytes naar
  Gemini met een strak veldenschema (besluit, dossiernummer, soort, aantal,
  aanvrager-type, herplantplicht, ...). Idempotent: records die al in
  `extracties` staan worden overgeslagen. Ruwe LLM-JSON wordt naast de
  gestructureerde kolommen bewaard (`raw_json`) zodat herinterpretatie later
  mogelijk is zonder de LLM opnieuw te draaien.

### 7.3 Post-hoc normalisatie

`normalize.py` clustert varianten ("berk"/"berken"/"berkenboom") naar een
canonieke vorm ná extractie, en bewaart de mapping in `normalisatie_mapping`. De
extractie zelf blijft dus "dom"/letterlijk; consolidatie is een aparte,
herhaalbare stap.

> **Universele eis #8 — Extractie is een strategie-keten met fallback.** Per
> bron: gestructureerd-parsen → LLM-parsen → regex-fallback, met de ruwe output
> altijd bewaard. Normalisatie is een losse, idempotente stap ná indexeren, niet
> vervlochten met extractie.

---

## 8. Werkstroom-optimalisatie — weinig tijdelijke ruimte, veel idempotentie

Een expliciet doel: de pijplijn draait dagelijks op een gedeelde VPS zonder
temp-ruimte te verspillen en zonder werk te herhalen. Concrete mechanismen:

- **Geen temp-uitpak, geen tussenbestanden.** HTTP-responses gaan direct van
  `httpx`/`requests` naar de parser en van daar in batches naar SQLite. Er wordt
  niets naar `/tmp` geschreven. De enige bestanden op schijf zijn de eind-PDF's
  (die *moeten* bewaard, want de bron verdwijnt) en één GeoJSON-cache.
- **Één GeoJSON-cache, niet per-run.** `bomenbestand.py` cachet de WFS-download
  naar `data/bomenbestand.json` en hergebruikt die tenzij `--refresh`. De
  dagelijkse changelog-variant houdt de stand juist in de DB en vervangt die
  in-place.
- **Conditioneel fetchen via modified-timestamps.** `fetch_arcgis.py` vraagt eerst
  de AGOL-item-`modified` op en slaat de (dure) feature-download over als die
  gelijk is aan wat opgeslagen staat (`get_stored_modified`). Alleen veranderde
  lagen worden opnieuw gehaald. **Goedkope check vóór dure fetch.**
- **Skip-lagen die werk vermijden** (zie §6): al-compleet-vlag, bestand-bestaat,
  al-geëxtraheerd-set, al-vandaag-gedraaid. Elke dure stap heeft een goedkope
  guard ervoor.
- **Batched writes.** Inserts gaan in blokken van 1000–5000 via `executemany`,
  niet per rij. Scheelt commits en fsync-druk.
- **SQLite in WAL-modus** (`db.py`) — betere concurrency tussen de schrijvende
  cron-pijplijn en de lezende dashboard-service op dezelfde DB.
- **Delta-opslag i.p.v. snapshots** (§6.1) — opslag schaalt met verandering, niet
  met datasetgrootte.
- **Beleefdheids-throttling** tussen requests: `DOWNLOAD_DELAY=1.0s`,
  SRU-historie `delay=2.0s`, ArcGIS `0.3s`, LLM-batches `1s`. Plus
  download-retries met oplopende backoff (`2 * attempt`).
- **Streamen door de generator.** SRU-paging is een `Iterator[list[dict]]`
  (`_fetch_paginated` `yield`t per pagina); callers verwerken en committen
  per pagina i.p.v. de hele ~8300-record-set in geheugen te verzamelen.

> **Universele eis #9 — "Cheap guard before expensive work" als kernprincipe.**
> Elke kostbare I/O (fetch, uitpakken, LLM-call) hoort voorafgegaan door de
> goedkoopst mogelijke check die hem kan overslaan: een timestamp-vergelijking,
> een bestaat-al-vlag, een hash. Tijdelijke ruimte wordt vermeden door te
> streamen (response → parser → gebatchte sink) i.p.v. te materialiseren.

---

## 9. Distillaat: hoe een universeel pluggable systeem eruitziet

Alles hierboven wijst naar dezelfde vorm. Een universeel crawl/scrape/index-
systeem is een keten van **per-bron plugins** rond een **gedeelde
genormaliseerde sink**, met deze uitbreidingspunten en verplichte randgeval-
ondersteuning:

**Pluginpunten**
1. **Source discovery** — vaste lijst *of* enumerate+classify+exclude (§3.4).
2. **Fetcher** — protocol-adapter (SRU-cursor / WFS-bulk / ArcGIS-offset /
   HTML-page), inclusief paging-strategie en stop-conditie.
3. **Unpacker** *(nog niet in dit project)* — streamend archief uitpakken naar
   per-lid items (§5).
4. **Link-extractor + filename-decoder + asset-filter** voor pagina→bestanden
   (§4).
5. **Dedup-strategie** — natuurlijke sleutel, entiteit-sleutel, cross-bron-
   exclusie, temporele delta (§6).
6. **Extractor-keten** — structured → LLM → regex-fallback, ruwe output bewaard
   (§7).
7. **Normalizer** — losse, idempotente consolidatiestap (§7.3).

**Verplichte randgevallen (hard geleerd)**
- Vluchtige bronnen met TTL → dekking-binnen-venster is de eis, niet
  eventuele-consistentie (§1).
- Schema-drift over tijd → velden komen/gaan; partieel indexeren i.p.v.
  weggooien (§3.2).
- JSON-type-instabiliteit bij n=1 (singleton-vs-lijst) (§3.1).
- Dubbel/meervoudig gecodeerde bestandsnamen (§4).
- Eén entiteit onder meerdere records/titels → afgeleide stabiele sleutel (§6).
- Dezelfde data via meerdere bronnen → expliciete exclusie (§6).
- Servers die liegen over counts → sanity-caps op paging (§3.4).
- Alles idempotent en herstartbaar; goedkope guard vóór elke dure stap (§8).

Kort gezegd: **de crawl-laag is per bron uniek, de index-laag is uniform, en de
lijm ertussen is een genormaliseerd record plus een reeks goedkope idempotentie-
checks die zowel dubbel werk als verspilde tijdelijke ruimte voorkomen.**
