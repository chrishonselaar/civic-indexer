## universele crawler en indexer voor civic data

> **Status (2026-07-21):** conceptfase uitgewerkt in [`design/`](design/README.md) —
> begin bij `design/README.md`. Volgende stap: het feasibility-experiment
> (`design/feasibility-plan.md`).

* openbare en semi-openbare overheids documenten en aanverwante web datasets
* voorbeeld: open.overheid.nl, stichting reclame code besluiten, gemeente raadsverslagen, vergunning openbaarmakingen, (semi)rechtspraak, etc etc
* ook (lokale) bijlages bij resultaten/documenten vinden en opnemen
* (admin) gebruiker geeft een site door die zij doorzoekbaar willen maken, plus een korte beschrijving van wat ze willen vinden en hoe: een AI agent stelt een crawling/scraping/metadata-en-text-extractie/ocr/embeddings-generation plan op, legt dit uit aan gebruiker voor verificatie, doet een snelle beperkte testrun, toont de resultaten aan gebruiker voor tweede verficatie, stelt interactief een plan op voor bijvoorbeeld dagelijkse crawling en wat voor (kosten/opslag) limieten er ingesteld moeten worden
* automatische zoek UI per dataset, met per data(sub)set relevante automatisch bepaalde facet filters
* zoek UI biedt fulltext OpenSearch search met operators etc
* volledig doorzoekbaar door bijvoorbeeld OCR-en van pdfs, extraheren metadata
* filteren op allerlei contextueel relevante kenmerken die de dataset snel 'doorklieven'
* BM25 fulltext indexing
* minimaliseer kosten voor opslag, embeddings genereren, vooral OCR - bijna geen budget
* gebruik goedkoop Google Gemini Flash (Lite) 3.5 model of vergelijkbaar voor zaken zoals samenvattingen maken, kunstmatige filter optie data destillatie, claims extraheren (claimify model), automatische monitoring van pijplijn
* semantische embeddings maken (over nadenken: op een AI-gemaakte samenvatting van de data? Of alles en dan gechunkt, of combi daarvan)
* in real-time of zeer snel nieuwe indexing bronnen/datasets kunnen toevoegen

## doel
* alles gefocust op wat *maatschappelijke organisaties*, journalisten en politiek nodig hebben om beter geïnformeerde campagenes te voeren, beslissingen te nemen, te procederen, klachten in te dienen, voorstellen voor maatschappelijke verbetering te doen, corruptie op te speuren
* alternatief voor https://opub.nl/zoeken - opub werkt goed en heeft een hele professionele en goede UI, maar beperkt zich grotendeels tot door *partijen zelf* aangeleverde indexing data via het federatie model. Als een aangesloten overheids site, zoals WOO besluiten, geen OCR uitvoert, zijn documenten met gescande text dus ook niet goed doorzoekbaar. Bijvoorbeeld. Ook willen we ZELF datasets kunnen toevoegen, die het systeem ZELF direct automatisch gaat crawlen en indexen, zonder tussenkomst van programmeurs
* alternatief voor overheidssites en sites zoals reclame code commissie die vaak weliswaar zelf zoek interfaces of zelfs APIs bieden, maar vaak is zoeken traag, ondersteunt geen geavanceerde operators of uitgebreide filters, geen semantisch zoeken, geen samenvattingen, geen of beperkte OCR, etc
* uiteindelijk nevendoel: datasets hebben die zich ideaal lenen voor semantische vector analyse (informatie lacunes, verbeteringsmogelijkheden, invloed netwerken, foutanalyse, misleiding, corruptie, etc etc)