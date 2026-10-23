"""Shared manifest + paths for the OCR engine A/B (see data/ocr_testset/OCR_TESTSET.md)."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTSET = REPO / "data" / "ocr_testset"
OUT = REPO / "data" / "ocr_ab"          # gitignored: raw engine outputs + rendered pages
RESULTS = Path(__file__).resolve().parent / "results"  # committed: scores + summaries

# file stem -> category (scoring is per category, per the test-set protocol)
MANIFEST = {
    "zeemansgraf_letterpress_19e-eeuw_excerpt_p90-115": "letterpress",
    "vuurwerkramp_typemachine_1989_gemeentepolitie": "typewriter",
    "koeweit_photocopy_treaty_2001_excerpt_p1-40": "photocopy",
    "vuurwerkramp_telefax_2001_china": "telefax",
    "vuurwerkramp_email_scan_2003_minbzk": "email-scan",
    "seawatch_scanned_emails_black_redactions_2019": "redacted-scan",
    "chat_screenshots_dark_2019_29a69abb": "chat-screenshot",
    "bzk_scanprint_redacted_emails_2022_000b7dee": "redacted-scan",
    "onderwijsinspectie_2024_dashboards_excerpt_p10-30": "dashboard",
    "meitellingen_borndigital_landscape_tables_2024": "born-digital-tables",
    "bestuurskosten_sparse_form_2026_50558ad5": "born-digital-sparse",
}

RENDER_DPI = 200  # shared render resolution for image-input engines and the VLM judge


def pdf_path(stem: str) -> Path:
    return TESTSET / f"{stem}.pdf"


def engine_dir(engine: str) -> Path:
    d = OUT / engine
    d.mkdir(parents=True, exist_ok=True)
    return d
