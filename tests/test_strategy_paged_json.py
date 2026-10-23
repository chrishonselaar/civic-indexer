"""paged_json_api strategy against recorded fixtures — no live site touched."""

import json
from pathlib import Path

import pytest

from engine import manifest
from engine.fixtures import replay_http_get
from engine.strategies.paged_json_api import PagedJsonApiStrategy

FIXDIR = Path(__file__).parent / "fixtures" / "woo_besluiten"
M = manifest.load("manifests/woo_besluiten.yaml")


def load_pages() -> list[dict]:
    return [
        json.loads((FIXDIR / name).read_text())
        for name in ("page_0.json", "page_1.json")
    ]


def make_strategy(pages: list[dict]) -> PagedJsonApiStrategy:
    role = manifest.resolve_role(M, "backfill")
    politeness = {**M["politeness"], "delay_seconds": 0}
    return PagedJsonApiStrategy(
        role, M["identity"], politeness, http_get=replay_http_get(pages)
    )


def test_fixture_walk_yields_items_and_captures_claimed_total():
    strategy = make_strategy(load_pages())
    items = list(strategy.discover())
    assert len(items) > 0
    assert strategy.claimed_total_first == 26429
    assert strategy.raw_results == 40  # two pages of 20
    # every yielded item has an identity and a leaf
    for item in items:
        assert item.urn.startswith("woo_besluiten:")
        assert item.partition_leaf != ""


def test_federated_stub_classification():
    pages = load_pages()
    # doctor one record into a federated Harvester stub
    rec = pages[0]["resultaten"][0]
    rec["document"]["id"] = "woo-idx-abc123"
    rec["document"]["aanbieder"] = "Harvester"
    strategy = make_strategy(pages)
    items = list(strategy.discover())
    stubs = [i for i in items if i.disposition == "stub"]
    assert len(stubs) >= 1
    assert stubs[0].natural_key == "woo-idx-abc123"
    assert stubs[0].reason  # never silent


def test_record_without_any_id_is_counted_unrecordable():
    pages = load_pages()
    pages[0]["resultaten"][0]["document"]["id"] = ""
    strategy = make_strategy(pages)
    items = list(strategy.discover())
    assert strategy.unrecordable == 1
    assert all(i.natural_key for i in items)


def test_cutoff_stops_walk_and_overlap_pages_extend_it():
    pages = load_pages()
    # find a date below which page_0 has items, so cutoff triggers on page 0
    dates = sorted(
        r["document"]["openbaarmakingsdatum"] for r in pages[0]["resultaten"]
    )
    cutoff = dates[-1]  # newest date: nearly everything is below cutoff
    strategy = make_strategy(pages)
    list(strategy.discover(until_before=cutoff, overlap_pages=0))
    assert strategy.requests_made == 1  # stopped after the crossing page

    strategy2 = make_strategy(load_pages())
    list(strategy2.discover(until_before=cutoff, overlap_pages=1))
    assert strategy2.requests_made == 2  # walked one overlap page past the crossing


def test_sanity_cap_stops_walk():
    pages = load_pages() * 50  # endless-ish stream
    role = manifest.resolve_role(M, "backfill")
    role = {**role, "sanity_cap": {"records": 30}}
    politeness = {**M["politeness"], "delay_seconds": 0}
    strategy = PagedJsonApiStrategy(
        role, M["identity"], politeness, http_get=replay_http_get(pages)
    )
    list(strategy.discover())
    assert strategy.sanity_cap_hit is True


def test_mid_walk_duplicates_detected():
    pages = load_pages()
    pages[1] = pages[0]  # page shift: same records served twice
    strategy = make_strategy(pages)
    items = list(strategy.discover())
    assert strategy.walk_duplicates > 0
    keys = [i.natural_key for i in items]
    assert len(keys) == len(set(keys))  # yielded items stay unique
