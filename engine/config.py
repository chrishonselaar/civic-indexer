"""Engine configuration: .env-driven connection settings and data directories."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql:///civic_indexer?host=/var/run/postgresql"
)
OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "http://127.0.0.1:9200")

DATA_DIR = Path(os.environ.get("CIVIC_DATA_DIR", PROJECT_ROOT / "data"))
BLOB_DIR = DATA_DIR / "blobs"
TEXT_DIR = DATA_DIR / "texts"

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
MANIFEST_DIR = PROJECT_ROOT / "manifests"

# --- Crawler identity (POL-1, design/access-posture.md) ---
# The contact is a URL, never a personal address: `+https://…/bot` is the same form
# Googlebot uses, and the page it points at is what makes the identification useful —
# it names the project, the rate, how to block this crawler, and how to ask for a
# document to be removed. A UA with no reachable contact is identification without
# accountability, which is the worse half of both options.
#
# Set CIVIC_CRAWLER_CONTACT_URL in .env to wherever ui/bot.html is actually served.
# The default assumes this repo's own UI is deployed; if it is not, the URL 404s and
# the politeness claim is not true yet. Serve the page before running against a live
# host — that is the whole point of it.
CRAWLER_CONTACT_URL = os.environ.get(
    "CIVIC_CRAWLER_CONTACT_URL", "https://civic-indexer.edubox.nl/bot"
)
USER_AGENT = f"civic-indexer/0.1 (+{CRAWLER_CONTACT_URL})"
PROBE_USER_AGENT = f"civic-indexer/0.1 (+{CRAWLER_CONTACT_URL}; probe0)"
