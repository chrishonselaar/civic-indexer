"""Crawler identity is a control, not a promise (failure-modes rule 5).

POL-1 §5 recorded contact-in-User-Agent as a condition of any GO that had *no
implementation*: eight call sites hard-coded a UA with no contact. These tests pin the
fix so it cannot silently regress:

1. No module reintroduces a literal UA string — one source of truth in engine.config.
2. Every outbound path actually carries it.
3. The `+URL` the UA advertises is served by the UI. A contact URL that 404s is
   identification without accountability, which is worse than either alone.

They deliberately do not assert a specific domain: CIVIC_CRAWLER_CONTACT_URL is
deployment configuration. They assert the *shape* holds whatever it is set to.
"""

import pathlib
import re

from fastapi.testclient import TestClient

from engine import config, probe, process
from engine.strategies import (
    notubiz_events,
    oai_pmh,
    ori_elastic,
    paged_html_search,
    paged_json_api,
    sru_records,
)
from ui.search_api import app

client = TestClient(app)

ENGINE_DIR = pathlib.Path(config.PROJECT_ROOT) / "engine"

# Strategy modules whose module-level headers dict goes out on every request.
HEADER_MODULES = [
    oai_pmh,
    ori_elastic,
    paged_html_search,
    paged_json_api,
    sru_records,
    notubiz_events,
]


def test_user_agent_carries_a_contact_url():
    assert config.USER_AGENT.startswith("civic-indexer/")
    # The `+https://…` form is the convention Googlebot uses; operators grep for it.
    assert f"(+{config.CRAWLER_CONTACT_URL}" in config.USER_AGENT
    assert config.CRAWLER_CONTACT_URL.startswith("https://")


def test_probe_user_agent_is_distinguishable():
    """Probe traffic is a different activity and should be blockable separately."""
    assert config.CRAWLER_CONTACT_URL in config.PROBE_USER_AGENT
    assert config.PROBE_USER_AGENT != config.USER_AGENT


def test_no_module_hardcodes_its_own_user_agent():
    """The regression this guards: a new strategy pasted from an old one.

    Eight call sites drifted last time precisely because each carried its own copy.
    """
    offenders = []
    for path in ENGINE_DIR.rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r'["\']User-Agent["\']\s*:', line) and "config." not in line:
                offenders.append(f"{path.relative_to(config.PROJECT_ROOT)}:{n}")
    assert not offenders, "hard-coded User-Agent, use engine.config: " + ", ".join(offenders)


def test_every_strategy_sends_the_shared_user_agent():
    for module in HEADER_MODULES:
        headers = [
            v for v in vars(module).values()
            if isinstance(v, dict) and "User-Agent" in v
        ]
        assert headers, f"{module.__name__} declares no headers dict"
        for h in headers:
            assert h["User-Agent"] == config.USER_AGENT, module.__name__


def test_probe_and_process_send_it_too():
    probe_headers = [
        v for v in vars(probe).values() if isinstance(v, dict) and "User-Agent" in v
    ]
    assert probe_headers, "engine.probe declares no headers dict"
    assert probe_headers[0]["User-Agent"] == config.PROBE_USER_AGENT
    # process.py sets it on a session inside a function; assert the source references
    # the constant rather than re-inventing a string (the literal check above covers
    # the inverse case).
    src = (ENGINE_DIR / "process.py").read_text()
    assert "config.USER_AGENT" in src


def test_contact_page_is_served():
    r = client.get("/bot")
    assert r.status_code == 200
    body = r.text
    # The three things an operator arrives here for.
    assert "robots.txt" in body
    assert "User-agent: civic-indexer" in body, "no copy-pasteable block rule"
    assert "mailto:" in body, "no contact route"
    assert client.get("/bot.css").status_code == 200


def test_contact_page_does_not_promise_automated_takedown():
    """URN-level suppression does not exist yet (POL-1 §5). Saying it does would be
    the exact rule-5 violation this page is meant to close, not open."""
    body = client.get("/bot").text.lower()
    assert "met de hand" in body or "handmatig" in body
