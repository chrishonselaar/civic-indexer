# OCR benchmark test set — challenging WOO PDFs

**Purpose:** representative set of hard OCR scenarios from our own WOO corpus, for comparing
Azure Document Intelligence (current) against open VLM-OCR models (PaddleOCR-VL, etc.).

**Location:** `data/ocr_testset/` (gitignored — PDFs are not committed; this doc is the
authoritative catalog + regeneration guide). Total ~49 MB, 12 files.

Every file is public WOO material from open.overheid.nl / rijksoverheid.nl. Documents span
**four decades of ministry document production**: 19th-century letterpress → 1989 typewriter →
2001 telefax → 2003 email printouts → 2019 chat screenshots → 2026 born-digital forms.

## Catalog

| File | Era / type | Challenge | Pages |
|---|---|---|---|
| `zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115.pdf` | 19th c. letterpress book scan | Aged paper, archaic typography & spelling, guilder amounts in dot-leader tables | 26 (of 1011) |
| `vuurwerkramp_typemachine_1989_gemeentepolitie.pdf` | 1989 typewriter memo | N-th generation photocopy, heavy skew/border artifacts, sparse monospaced text, yellow redaction boxes | 2 |
| `koeweit_photocopy_treaty_2001_excerpt_p1-40.pdf` | ~2001 photocopied treaty (NL+EN) | Photocopy noise, punch holes, stamped page numbers, mixed Dutch/English legal text | 40 (of 190) |
| `vuurwerkramp_telefax_2001_china.pdf` | 2001 telefax cover sheet | Fax rendering, sparse layout, yellow `10.2.e` redaction labels | 1 |
| `vuurwerkramp_email_scan_2003_minbzk.pdf` | 2003 scan of printed email | Scan-of-print, yellow marker redactions over header fields, embedded attachment icon | 1 |
| `seawatch_scanned_emails_black_redactions_2019.pdf` | 2019 scanned email threads | **Dense black-box redactions** with inline red `5.1-2-e` exemption labels interrupting text flow; scan noise (ES text_confidence 0.35, 950 redactions) | 85 |
| `chat_screenshots_dark_2019_29a69abb.pdf` | 2019 iMessage screenshots (pub. 2026) | **User's reference case.** Heavily compressed dark-mode phone screenshots, white/blue text on black, emoji, redaction stickers over avatars | 24 |
| `bzk_scanprint_redacted_emails_2022_000b7dee.pdf` | 2022 scans of printed, manually redacted emails | **User's reference case.** Poor-quality scan of print, compression artifacts, manual redactions | 138 |
| `onderwijsinspectie_2024_dashboards_excerpt_p10-30.pdf` | 2024 media-monitoring dashboards + emails | Infographics, sentiment gauges, bar charts, tiny URL text, mixed with email pages (parent doc deel 4 has 1,875 redactions) | 21 (of 165) |
| `meitellingen_borndigital_landscape_tables_2024.pdf` | Born-digital landscape data tables | Wide landscape tables of livestock counts per year — table-structure extraction test, trivial for text OCR | 46 |
| `bestuurskosten_sparse_form_2026_50558ad5.pdf` | 2026 born-digital expense forms | **False-OCR-trigger control:** perfectly clean text layer but so sparse that our density heuristic scores it 0.11 → would waste OCR. Good "do no harm" test | 12 |

## Provenance (source URLs)

| File | WOO ID / source |
|---|---|
| zeemansgraf | `a0a015b2-e3bd-4af8-bba1-89fc96685b9b` — https://open.overheid.nl/documenten/a0a015b2-e3bd-4af8-bba1-89fc96685b9b/file (1,011-page archival book scan; excerpt = original pages 90–115) |
| vuurwerkramp_* (3 files) | Inner PDFs of ZIP-packaged publication `documenten-deel-1-bij-6e-deelbesluit-op-wob-verzoek-vuurwerkrampen-enschede-en-culemborg` — https://open.overheid.nl/documenten/e7fabad1-604d-421b-b58a-1f65e82eb3de/file (471 MB ZIP, 186 inner PDFs; on server at `pdf_output/2023/`). Inner names: `0077 - 12134 - DIV-6243 - 114 - Gemeentepolitie Enschede _Geredigeerd.pdf`, `0259 - 12142 (DIV-6638) - 36 - CHINA_Geredigeerd.pdf`, `0311 - 12141 (DIV-6609) - 3 - MINBZK _Geredigeerd.pdf` |
| koeweit | `81a449b4-376f-4237-98ac-824e9085938d` (IBO Koeweit) — https://open.overheid.nl/documenten/81a449b4-376f-4237-98ac-824e9085938d/file (103 MB, 190 p; excerpt = pages 1–40) |
| seawatch | Wob/Woo bezwaar "relocatie 6 Sea Watch / NGO-schepen jan 2019" — https://open.overheid.nl/repository/ronl-2873312e1d261f0ffc0db4a96607dcb14307c143/1/pdf/... (full 85 p) |
| chat_screenshots | `29a69abb-4fa4-491f-b376-4276a4052c1c` — https://open.overheid.nl/documenten/29a69abb-4fa4-491f-b376-4276a4052c1c/file (full; also on server `pdf_output/2026/`) |
| bzk_scanprint | `000b7dee-3fa8-4fbb-8fdb-5c88498c67e5` — https://open.overheid.nl/documenten/000b7dee-3fa8-4fbb-8fdb-5c88498c67e5/file (full 138 p) |
| onderwijsinspectie | deel 1 of `openbaargemaakte-documenten-...-bezoek-scholen-door-onderwijsinspectie` (server `pdf_output/2024/`, 161 MB, 165 p; excerpt = pages 10–30). Related deel 4 (`be101e81-5080-4b7b-9633-7634380cb2dc`) holds the 1,875-redaction record |
| meitellingen | `297a37ba-8ddc-46e6-bdb3-29fb455cfa15` — https://open.overheid.nl/documenten/297a37ba-8ddc-46e6-bdb3-29fb455cfa15/file (full 46 p) |
| bestuurskosten | `50558ad5-39e3-49bb-b955-4161352e5685` — https://open.overheid.nl/documenten/50558ad5-39e3-49bb-b955-4161352e5685/file (full 12 p) |

## How the candidates were found (repeatable)

Queried the prod ES index `woo-besluiten` on the server, using these signals:

- **Chat screenshots:** `content_text` match on `iMessage` / `"Vodafone NL"` / `"Getypt bericht"` / `WhatsApp` → 1,164 docs (large future pool).
- **Faxes:** match `faxbericht` / `telefax` / `"fax transmissie"` → 196 docs.
- **Old-era scans:** `used_ocr:true` + phrases `1991/1993/1995/gravenhage` → 157 docs (Vuurwerkramp Enschede/Culemborg cohort dominates).
- **Extreme redactions:** sort by `redaction_count desc` (record: 1,875 in 55 pages).
- **Worst extraction:** sort `text_confidence asc` — but note this surfaces mostly *sparse born-digital* docs (forms, inventarislijsten), not bad scans; confidence = text density, not OCR quality.
- **Handwriting:** match `handgeschreven` → 110 docs (⚠️ **gap: still unrepresented as a
  dedicated sample** — though koeweit p1 carries incidental handwritten margin annotations
  that even human review [Chris, 2026-07-21] could not fully decipher; ground truth for
  those fragments is best-effort, so don't score engines hard against them).
  Candidate `47018976-472b-4eaf-9c5a-e61d247afdf6` (adoption reflectiebijeenkomst JenV) was
  downloaded and visually verified 2026-07-21: **rejected** — all 26 pages are printed-email
  scans; it matched only because the email *text* says "handgeschreven". Lesson for this
  query: content match on `handgeschreven` mostly finds documents *talking about*
  handwriting; candidates need page-image verification before inclusion. (Portal note:
  `open.overheid.nl/documenten/<id>/file` returns 404 to HEAD requests but serves the PDF on GET.)

## Regenerating / extending

```bash
# Excerpts made with qpdf (local) and PyMuPDF (server):
qpdf full.pdf --pages . 90-115 -- excerpt.pdf

# Inner PDFs from ZIP-packaged publications (server):
python3 -c "import zipfile; z=zipfile.ZipFile('pub.pdf'); open('out.pdf','wb').write(z.read('<inner name>'))"
```

Note: several `open.overheid.nl` "PDF" publications are actually **ZIP archives with a .pdf
URL** (Vuurwerkramp deel 1, KNMI-aardgas `d249c8d7`). Check magic bytes (`PK` vs `%PDF`)
before treating them as PDFs.

## Suggested evaluation protocol

1. Run each engine (Azure DI, PaddleOCR-VL, …) on all 12 files at the same DPI.
2. Score per category, not just aggregate: chat screenshots and redacted scans are where
   engines diverge most; letterpress tests archaic-glyph handling; meitellingen tests table
   structure; bestuurskosten tests that clean text layers aren't degraded by needless OCR.
3. For redacted docs, also score **redaction fidelity**: does the engine emit the exemption
   labels (`5.1.2e`, `10.2.e`) and mark gaps, or hallucinate text across black boxes?
4. No ground truth exists yet — for a first pass, human-spot-check ~2 pages per file, or use
   a strong VLM as judge with the page image as reference.
