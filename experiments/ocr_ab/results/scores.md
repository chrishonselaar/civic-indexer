# OCR A/B — automatic scores

Engines: azure_di, glm_ocr, got_ocr2, mineru25_pro, native, olmocr, paddleocr_vl, paddleocr_vl_15. Reference engine: azure_di.
cpp = chars/page; conf = density confidence; labels = Woo exemption labels
found (redaction-fidelity proxy); sim = similarity to azure_di (0-1).

## azure_di

| file | category | pages | chars | cpp | conf | labels | sim |
|---|---|---:|---:|---:|---:|---:|---:|
| zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115 | letterpress | 26 | 30688 | 1180 | 0.59 | 0 | ref |
| vuurwerkramp_typemachine_1989_gemeentepolitie | typewriter | 2 | 2108 | 1054 | 0.53 | 5 | ref |
| koeweit_photocopy_treaty_2001_excerpt_p1-40 | photocopy | 40 | 45512 | 1138 | 0.57 | 2 | ref |
| vuurwerkramp_telefax_2001_china | telefax | 1 | 528 | 528 | 0.26 | 7 | ref |
| vuurwerkramp_email_scan_2003_minbzk | email-scan | 1 | 381 | 381 | 0.19 | 6 | ref |
| seawatch_scanned_emails_black_redactions_2019 | redacted-scan | 85 | 56615 | 666 | 0.33 | 264 | ref |
| chat_screenshots_dark_2019_29a69abb | chat-screenshot | 24 | 8801 | 367 | 0.18 | 63 | ref |
| bzk_scanprint_redacted_emails_2022_000b7dee | redacted-scan | 138 | 151159 | 1095 | 0.55 | 1312 | ref |
| onderwijsinspectie_2024_dashboards_excerpt_p10-30 | dashboard | 21 | 20468 | 975 | 0.49 | 95 | ref |
| meitellingen_borndigital_landscape_tables_2024 | born-digital-tables | 46 | 62470 | 1358 | 0.68 | 16 | ref |
| bestuurskosten_sparse_form_2026_50558ad5 | born-digital-sparse | 12 | 2503 | 209 | 0.1 | 0 | ref |

## glm_ocr

| file | category | pages | chars | cpp | conf | labels | sim |
|---|---|---:|---:|---:|---:|---:|---:|
| zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115 | letterpress | — | — | — | — | — | — |
| vuurwerkramp_typemachine_1989_gemeentepolitie | typewriter | — | — | — | — | — | — |
| koeweit_photocopy_treaty_2001_excerpt_p1-40 | photocopy | 40 | 193931 | 4848 | 1.0 | 0 | 0.02 |
| vuurwerkramp_telefax_2001_china | telefax | — | — | — | — | — | — |
| vuurwerkramp_email_scan_2003_minbzk | email-scan | — | — | — | — | — | — |
| seawatch_scanned_emails_black_redactions_2019 | redacted-scan | — | — | — | — | — | — |
| chat_screenshots_dark_2019_29a69abb | chat-screenshot | 24 | 8500 | 354 | 0.18 | 62 | 0.73 |
| bzk_scanprint_redacted_emails_2022_000b7dee | redacted-scan | 138 | 157098 | 1138 | 0.57 | 2096 | 0.71 |
| onderwijsinspectie_2024_dashboards_excerpt_p10-30 | dashboard | — | — | — | — | — | — |
| meitellingen_borndigital_landscape_tables_2024 | born-digital-tables | — | — | — | — | — | — |
| bestuurskosten_sparse_form_2026_50558ad5 | born-digital-sparse | 12 | 2533 | 211 | 0.11 | 0 | 1.0 |

## got_ocr2

| file | category | pages | chars | cpp | conf | labels | sim |
|---|---|---:|---:|---:|---:|---:|---:|
| zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115 | letterpress | 26 | 42525 | 1636 | 0.82 | 0 | 0.74 |
| vuurwerkramp_typemachine_1989_gemeentepolitie | typewriter | 2 | 326 | 163 | 0.08 | 1 | 0.17 |
| koeweit_photocopy_treaty_2001_excerpt_p1-40 | photocopy | 40 | 55018 | 1375 | 0.69 | 2 | 0.87 |
| vuurwerkramp_telefax_2001_china | telefax | 1 | 4182 | 4182 | 1.0 | 80 | 0.01 |
| vuurwerkramp_email_scan_2003_minbzk | email-scan | 1 | 276 | 276 | 0.14 | 5 | 0.7 |
| seawatch_scanned_emails_black_redactions_2019 | redacted-scan | 85 | 114172 | 1343 | 0.67 | 148 | 0.55 |
| chat_screenshots_dark_2019_29a69abb | chat-screenshot | 24 | 8989 | 375 | 0.19 | 46 | 0.41 |
| bzk_scanprint_redacted_emails_2022_000b7dee | redacted-scan | 138 | 216765 | 1571 | 0.79 | 525 | 0.27 |
| onderwijsinspectie_2024_dashboards_excerpt_p10-30 | dashboard | 21 | 46057 | 2193 | 1.0 | 3 | 0.28 |
| meitellingen_borndigital_landscape_tables_2024 | born-digital-tables | — | — | — | — | — | — |
| bestuurskosten_sparse_form_2026_50558ad5 | born-digital-sparse | — | — | — | — | — | — |

## mineru25_pro

| file | category | pages | chars | cpp | conf | labels | sim |
|---|---|---:|---:|---:|---:|---:|---:|
| zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115 | letterpress | 26 | 58606 | 2254 | 1.0 | 0 | 0.72 |
| vuurwerkramp_typemachine_1989_gemeentepolitie | typewriter | 2 | 2096 | 1048 | 0.52 | 5 | 0.97 |
| koeweit_photocopy_treaty_2001_excerpt_p1-40 | photocopy | 40 | 45332 | 1133 | 0.57 | 2 | 0.97 |
| vuurwerkramp_telefax_2001_china | telefax | 1 | 533 | 533 | 0.27 | 7 | 0.37 |
| vuurwerkramp_email_scan_2003_minbzk | email-scan | 1 | 383 | 383 | 0.19 | 6 | 0.77 |
| seawatch_scanned_emails_black_redactions_2019 | redacted-scan | 85 | 129022 | 1518 | 0.76 | 230 | 0.78 |
| chat_screenshots_dark_2019_29a69abb | chat-screenshot | 24 | 22206 | 925 | 0.46 | 49 | 0.75 |
| bzk_scanprint_redacted_emails_2022_000b7dee | redacted-scan | 138 | 149880 | 1086 | 0.54 | 1188 | 0.69 |
| onderwijsinspectie_2024_dashboards_excerpt_p10-30 | dashboard | 21 | 20609 | 981 | 0.49 | 83 | 0.77 |
| meitellingen_borndigital_landscape_tables_2024 | born-digital-tables | 46 | 113589 | 2469 | 1.0 | 16 | 0.33 |
| bestuurskosten_sparse_form_2026_50558ad5 | born-digital-sparse | 12 | 4207 | 351 | 0.18 | 0 | 0.66 |

## native

| file | category | pages | chars | cpp | conf | labels | sim |
|---|---|---:|---:|---:|---:|---:|---:|
| zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115 | letterpress | 26 | 31778 | 1222 | 0.61 | 0 | 0.88 |
| vuurwerkramp_typemachine_1989_gemeentepolitie | typewriter | 2 | 1 | 0 | 0.0 | 0 | 0.0 |
| koeweit_photocopy_treaty_2001_excerpt_p1-40 | photocopy | 40 | 44811 | 1120 | 0.56 | 2 | 0.75 |
| vuurwerkramp_telefax_2001_china | telefax | 1 | 0 | 0 | 0.0 | 0 | 0.0 |
| vuurwerkramp_email_scan_2003_minbzk | email-scan | 1 | 0 | 0 | 0.0 | 0 | 0.0 |
| seawatch_scanned_emails_black_redactions_2019 | redacted-scan | 85 | 61396 | 722 | 0.36 | 240 | 0.64 |
| chat_screenshots_dark_2019_29a69abb | chat-screenshot | 24 | 2095 | 87 | 0.04 | 0 | 0.0 |
| bzk_scanprint_redacted_emails_2022_000b7dee | redacted-scan | 138 | 17407 | 126 | 0.06 | 0 | 0.0 |
| onderwijsinspectie_2024_dashboards_excerpt_p10-30 | dashboard | 21 | 20762 | 989 | 0.49 | 45 | 0.25 |
| meitellingen_borndigital_landscape_tables_2024 | born-digital-tables | 46 | 62804 | 1365 | 0.68 | 16 | 0.54 |
| bestuurskosten_sparse_form_2026_50558ad5 | born-digital-sparse | 12 | 2973 | 248 | 0.12 | 0 | 1.0 |

## olmocr

| file | category | pages | chars | cpp | conf | labels | sim |
|---|---|---:|---:|---:|---:|---:|---:|
| zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115 | letterpress | 26 | 32637 | 1255 | 0.63 | 0 | 0.84 |
| vuurwerkramp_typemachine_1989_gemeentepolitie | typewriter | 2 | 1647 | 824 | 0.41 | 1 | 0.79 |
| koeweit_photocopy_treaty_2001_excerpt_p1-40 | photocopy | 40 | 44058 | 1101 | 0.55 | 2 | 0.96 |
| vuurwerkramp_telefax_2001_china | telefax | 1 | 351 | 351 | 0.18 | 5 | 0.34 |
| vuurwerkramp_email_scan_2003_minbzk | email-scan | 1 | 389 | 389 | 0.19 | 6 | 0.76 |
| seawatch_scanned_emails_black_redactions_2019 | redacted-scan | 85 | 87080 | 1024 | 0.51 | 243 | 0.86 |
| chat_screenshots_dark_2019_29a69abb | chat-screenshot | 24 | 6401 | 267 | 0.13 | 12 | 0.73 |
| bzk_scanprint_redacted_emails_2022_000b7dee | redacted-scan | 138 | 152543 | 1105 | 0.55 | 1506 | 0.66 |
| onderwijsinspectie_2024_dashboards_excerpt_p10-30 | dashboard | 21 | 26251 | 1250 | 0.63 | 11 | 0.66 |
| meitellingen_borndigital_landscape_tables_2024 | born-digital-tables | 46 | 127545 | 2773 | 1.0 | 16 | 0.42 |
| bestuurskosten_sparse_form_2026_50558ad5 | born-digital-sparse | 12 | 5202 | 434 | 0.22 | 0 | 0.64 |

## paddleocr_vl

| file | category | pages | chars | cpp | conf | labels | sim |
|---|---|---:|---:|---:|---:|---:|---:|
| zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115 | letterpress | 26 | 172751 | 6644 | 1.0 | 0 | 0.26 |
| vuurwerkramp_typemachine_1989_gemeentepolitie | typewriter | 2 | 429 | 214 | 0.11 | 4 | 0.29 |
| koeweit_photocopy_treaty_2001_excerpt_p1-40 | photocopy | 40 | 48671 | 1217 | 0.61 | 2 | 0.86 |
| vuurwerkramp_telefax_2001_china | telefax | 1 | 399 | 399 | 0.2 | 7 | 0.84 |
| vuurwerkramp_email_scan_2003_minbzk | email-scan | 1 | 339 | 339 | 0.17 | 6 | 0.78 |
| seawatch_scanned_emails_black_redactions_2019 | redacted-scan | 85 | 98181 | 1155 | 0.58 | 269 | 0.71 |
| chat_screenshots_dark_2019_29a69abb | chat-screenshot | 24 | 15534 | 647 | 0.32 | 632 | 0.73 |
| bzk_scanprint_redacted_emails_2022_000b7dee | redacted-scan | 138 | 299269 | 2169 | 1.0 | 5896 | 0.44 |
| onderwijsinspectie_2024_dashboards_excerpt_p10-30 | dashboard | 21 | 57465 | 2736 | 1.0 | 362 | 0.6 |
| meitellingen_borndigital_landscape_tables_2024 | born-digital-tables | 46 | 89986 | 1956 | 0.98 | 16 | 0.44 |
| bestuurskosten_sparse_form_2026_50558ad5 | born-digital-sparse | 12 | 2503 | 209 | 0.1 | 0 | 1.0 |

## paddleocr_vl_15

| file | category | pages | chars | cpp | conf | labels | sim |
|---|---|---:|---:|---:|---:|---:|---:|
| zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115 | letterpress | 26 | 96172 | 3699 | 1.0 | 0 | 0.64 |
| vuurwerkramp_typemachine_1989_gemeentepolitie | typewriter | 2 | 2056 | 1028 | 0.51 | 6 | 0.9 |
| koeweit_photocopy_treaty_2001_excerpt_p1-40 | photocopy | 40 | 54176 | 1354 | 0.68 | 2 | 0.95 |
| vuurwerkramp_telefax_2001_china | telefax | 1 | 535 | 535 | 0.27 | 7 | 0.46 |
| vuurwerkramp_email_scan_2003_minbzk | email-scan | 1 | 353 | 353 | 0.18 | 7 | 0.78 |
| seawatch_scanned_emails_black_redactions_2019 | redacted-scan | 85 | 125975 | 1482 | 0.74 | 775 | 0.81 |
| chat_screenshots_dark_2019_29a69abb | chat-screenshot | 24 | 31402 | 1308 | 0.65 | 1214 | 0.57 |
| bzk_scanprint_redacted_emails_2022_000b7dee | redacted-scan | 138 | 236023 | 1710 | 0.86 | 3288 | 0.58 |
| onderwijsinspectie_2024_dashboards_excerpt_p10-30 | dashboard | 21 | 28843 | 1373 | 0.69 | 290 | 0.78 |
| meitellingen_borndigital_landscape_tables_2024 | born-digital-tables | 46 | 95352 | 2073 | 1.0 | 16 | 0.46 |
| bestuurskosten_sparse_form_2026_50558ad5 | born-digital-sparse | 12 | 3699 | 308 | 0.15 | 0 | 1.0 |
