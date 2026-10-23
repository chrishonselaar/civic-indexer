"""Monitoring dashboard endpoints (roadmap W5).

Read-only aggregations over the live ledger — like test_drain's DB halves, these
run against the real civic_indexer database. Assertions pin the response *shape*
and cross-field invariants (funnel completeness, totals consistency, health
domain), never magic counts, so they stay valid as the corpus grows.
"""

import pytest
from fastapi.testclient import TestClient

from ui.search_api import app
from ui.monitor import STAGE_ORDER

client = TestClient(app)


@pytest.fixture(scope="module")
def overview():
    r = client.get("/api/monitor/overview")
    assert r.status_code == 200
    return r.json()


def test_overview_top_level_shape(overview):
    for key in ("generated_at", "totals", "sources", "recent_runs",
                "queues", "blobs", "costs", "alerts"):
        assert key in overview, f"missing {key}"


def test_totals_are_non_negative_ints(overview):
    t = overview["totals"]
    for key in ("sources", "items", "indexed", "open_queue", "disk_bytes",
                "reclaimed_bytes", "runs", "unfinished_runs", "not_reconciled_runs"):
        assert isinstance(t[key], int) and t[key] >= 0, key


def test_totals_match_source_rollups(overview):
    t, srcs = overview["totals"], overview["sources"]
    assert t["sources"] == len(srcs)
    assert t["items"] == sum(s["items_total"] for s in srcs)
    assert t["indexed"] == sum(s["indexed"] for s in srcs)
    assert t["open_queue"] == sum(sum(s["open_queue"].values()) for s in srcs)


def test_each_source_card_is_well_formed(overview):
    for s in overview["sources"]:
        assert s["health"] in ("green", "amber", "red")
        # funnel carries every stage in rank order, monotonic-friendly keys
        assert list(s["funnel"].keys()) == STAGE_ORDER
        # dispositions sum to the item total (every item has exactly one)
        assert sum(s["dispositions"].values()) == s["items_total"]
        # indexed mirrors the funnel's terminal stage
        assert s["indexed"] == s["funnel"]["indexed"]


def test_paused_or_not_reconciled_source_reads_red(overview):
    for s in overview["sources"]:
        lr = s["last_run"]
        if s["paused"] or (lr and lr["verdict"] == "NOT_reconciled"):
            assert s["health"] == "red"


def test_unfinished_run_raises_amber_alert(overview):
    if overview["totals"]["unfinished_runs"]:
        assert any("never finished" in a["msg"] for a in overview["alerts"])


def test_blob_on_disk_never_exceeds_recorded(overview):
    for b in overview["blobs"]["by_reason"]:
        assert 0 <= b["on_disk_bytes"] <= b["bytes"]
        assert 0 <= b["on_disk_count"] <= b["count"]


def test_source_detail_shape_and_404():
    srcs = client.get("/api/monitor/overview").json()["sources"]
    if srcs:
        sid = srcs[0]["id"]
        d = client.get(f"/api/monitor/source/{sid}").json()
        assert d["source"]["id"] == sid
        assert list(d["funnel"].keys()) == STAGE_ORDER
        for key in ("matrix", "dispositions", "missing", "runs",
                    "queue", "cost", "blobs", "failed_items"):
            assert key in d
    assert client.get("/api/monitor/source/__nope__").status_code == 404


def test_run_report_shape_and_404():
    runs = client.get("/api/monitor/overview").json()["recent_runs"]
    if runs:
        rid = runs[0]["id"]
        d = client.get(f"/api/monitor/run/{rid}").json()
        assert d["id"] == rid
        assert "partitions" in d and "report" in d
    assert client.get("/api/monitor/run/999999999").status_code == 404


def test_static_dashboard_is_served():
    assert client.get("/monitor").status_code == 200
    assert "text/html" in client.get("/monitor").headers["content-type"]
    assert client.get("/monitor.js").status_code == 200
    assert client.get("/monitor.css").status_code == 200


# ------------------------------------------------- projection drift card (PROJ-2)
# `civic check-projection` existed but had to be *chosen*, and a control nobody runs
# is not a control. These pin the endpoint that puts it on the dashboard — including
# the case the whole card exists for: it must never raise, because the failure mode
# being guarded against is silence.

def test_projection_reports_every_live_index_against_its_schema():
    d = client.get("/api/monitor/projection").json()
    assert set(d) >= {"available", "indices", "drifted", "checked_at"}
    if not d["available"]:
        pytest.skip(f"no OpenSearch: {d['error']}")
    for i in d["indices"]:
        assert i["kind"] in ("doc", "chunk")
        assert i["clean"] == (not i["wrong_type"] and not i["undeclared"])
        for w in i["wrong_type"]:
            assert {"field", "declared", "live"} == set(w)
    assert d["drifted"] == sum(1 for i in d["indices"] if not i["clean"])
    assert d["fix"].startswith("uv run civic project")


def test_an_unreachable_cluster_is_reported_not_raised(monkeypatch):
    """The dashboard must be able to say "I could not check" — an exception here
    would be rendered as a dead panel, which reads exactly like a clean one."""
    from engine.project import os_client

    monkeypatch.setattr(os_client, "client",
                        lambda: (_ for _ in ()).throw(ConnectionError("refused")))
    r = client.get("/api/monitor/projection")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is False and d["indices"] == [] and d["drifted"] == 0
    assert "refused" in d["error"]


def test_drift_is_counted_even_when_it_is_the_only_finding(monkeypatch):
    from engine.project import os_client, verify

    monkeypatch.setattr(os_client, "client", lambda: object())
    monkeypatch.setattr(verify, "check_indices", lambda osc: [
        {"index": "civic-a", "kind": "doc", "wrong_type": [], "undeclared": [],
         "unmaterialised": ["processors"]},
        {"index": "civic-b", "kind": "doc", "undeclared": [], "unmaterialised": [],
         "wrong_type": [{"field": "exposure", "declared": "keyword", "live": "text"}]},
    ])
    d = client.get("/api/monitor/projection").json()
    # unmaterialised is a warning, not a disagreement — it must not inflate the count
    assert d["drifted"] == 1
    assert [i["clean"] for i in d["indices"]] == [True, False]
