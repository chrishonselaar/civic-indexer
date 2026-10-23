"""Demo screens (funding/demo-bouw-ontwerp.md) are prose assembled from measured
files. These tests keep the prose tied to the evidence it quotes."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from ui.search_api import app

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments" / "external_comparison" / "results.md"

client = TestClient(app)


def _means() -> dict[str, tuple[str, str]]:
    """System -> (nDCG@10, P@10) from the Means table of results.md."""
    text = RESULTS.read_text()
    block = text.split("## Means", 1)[1].split("## ", 1)[0]
    rows = {}
    for m in re.finditer(r"^\| (\w+) \| ([\d.]+) \| ([\d.]+) \|", block, re.M):
        rows[m.group(1)] = (m.group(2), m.group(3))
    assert rows, "results.md Means table not parsed"
    return rows


def _nl(x: str) -> str:
    return x.replace(".", ",")


def test_bewijs_page_is_served():
    r = client.get("/bewijs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert client.get("/bot.css").status_code == 200  # the page's stylesheet


def test_bewijs_quotes_the_measured_means():
    """Every headline number on the page must be the one in results.md, verbatim."""
    body = client.get("/bewijs").text
    means = _means()
    for system in ("ours_bm25", "ours_hybrid", "theirs"):
        ndcg, p10 = means[system]
        assert _nl(ndcg) in body, f"{system} nDCG@10 {ndcg} missing from /bewijs"
        assert _nl(p10) in body, f"{system} P@10 {p10} missing from /bewijs"


def test_bewijs_shows_no_rerank_figure():
    """demo-bouw-ontwerp.md §2: the measured rerank number came from a model this
    project rejected; a demo that shines with it claims something not shipped."""
    body = client.get("/bewijs").text
    ndcg, p10 = _means()["ours_hybrid_rerank_open"]
    assert _nl(ndcg) not in body and ndcg not in body
    assert _nl(p10) not in body and p10 not in body


def test_bewijs_names_the_reproduction_command():
    body = client.get("/bewijs").text
    assert "experiments/external_comparison/judge.py" in body
    assert "experiments/external_comparison/fetch_sides.py" in body


def test_vertrouwen_page_is_served():
    r = client.get("/vertrouwen")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_vertrouwen_states_every_source_and_its_tier():
    """The exposure table must agree with the manifests, not with itself."""
    import yaml

    body = client.get("/vertrouwen").text
    for path in sorted((ROOT / "manifests").glob("*.yaml")):
        m = yaml.safe_load(path.read_text())
        sid = m["source"]["id"]
        tier = m["policy"]["exposure"]
        assert sid in body, f"{sid} missing from /vertrouwen"
        row = [l for l in body.splitlines() if sid in l]
        assert any(f">{tier}<" in l for l in row), f"{sid} not shown as {tier}"


def test_vertrouwen_keeps_its_open_questions():
    """A trust page that drops its caveats is the failure mode it exists to prevent."""
    body = client.get("/vertrouwen").text
    assert "Wat hier niet af is" in body
    for claim in ("licentie", "repareert zichzelf nog niet", "nog niet gedaan"):
        assert claim in body, claim


def test_stats_says_whether_this_deployment_can_rerank(monkeypatch):
    """The mode dropdown offers a reranked mode by default. A deployment without a key
    runs hybrid instead — correctly, and `mode_used` says so — but the UI would still
    be naming a capability the host does not have, which is the one claim the demo is
    not allowed to make (funding/demo-bouw-ontwerp.md §2)."""
    from ui import search_api

    monkeypatch.setattr(search_api.osc, "count", lambda **kw: {"count": 7})
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert client.get("/api/stats").json() == {
        "total_docs": 7,
        "rerank_available": False,
    }

    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    assert client.get("/api/stats").json()["rerank_available"] is True


def test_the_ui_relabels_the_mode_when_rerank_is_unavailable():
    js = (ROOT / "ui" / "app.js").read_text()
    assert "rerank_available === false" in js
    assert "Slim (hybride)" in js
