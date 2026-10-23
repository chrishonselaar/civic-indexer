"""The onboarding UI's API surface (roadmap W6 slice 3).

Driven entirely by the **replay driver**: no SDK, no API key, no live request, no spend —
which is the point of having it (a live onboarding is ~$3.40 and touches a real site, so
it cannot be a test). What replay exercises is the whole product path anyway: the runner's
task lifecycle, the gate pause/resume, refusal as a terminal state, event polling, and the
artifact views.

The registry is redirected at a tmp dir, so nothing here can write over the recorded
Stage-B session artifacts it reads.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from engine import onboarding_runner
from ui import onboard
from ui.search_api import app

REPLAYED = "vlaardingen_raad"      # the recorded Stage-B session (3 DIAGNOSIS, 0 gates)
URL = "https://vlaardingen.raadsinformatie.nl"
INTENT = "council decisions findable by residents"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "registry", onboarding_runner.Registry(tmp_path))
    monkeypatch.setattr(onboarding_runner, "RUN_DIR", tmp_path)
    with TestClient(app) as c:            # context manager ⇒ one persistent event loop,
        yield c                           # so the run's asyncio task survives between calls


def start(client, source_id=REPLAYED, **kw):
    body = {"url": URL, "intent": INTENT, "source_id": source_id, "driver": "replay", **kw}
    return client.post("/api/onboard/sessions", json=body)


def wait_for(client, source_id, predicate, timeout=15.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/onboard/session/{source_id}").json()
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"timed out; last state: {json.dumps(last)[:800]}")


def at_gate(phase):
    def _p(state):
        gate = state["session"].get("pending_gate")
        return bool(gate) and gate["phase"] == phase and state["status"] == "awaiting_gate"
    return _p


def decide(client, source_id, verdict, note="because"):
    return client.post(f"/api/onboard/session/{source_id}/gate",
                       json={"verdict": verdict, "note": note, "admin": "chris"})


# ------------------------------------------------------------------ intake

def test_intake_is_url_plus_one_sentence_and_nothing_else(client):
    r = start(client)
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == REPLAYED and body["driver"] == "replay"
    assert body["status"] in ("running", "awaiting_gate")


def test_intake_refuses_a_missing_intent(client):
    r = client.post("/api/onboard/sessions", json={"url": URL})
    assert r.status_code == 400


def test_unknown_driver_is_refused(client):
    r = start(client, driver="whatever")
    assert r.status_code == 400
    assert "replay" in r.json()["drivers"]


def test_one_run_per_source(client):
    assert start(client).status_code == 200
    wait_for(client, REPLAYED, at_gate(4))
    r = start(client)
    assert r.status_code == 409


def test_the_source_id_defaults_to_the_url_host(client):
    r = client.post("/api/onboard/sessions",
                    json={"url": "https://opendata.example.nl/foo", "intent": INTENT})
    assert r.json()["source_id"] == "opendata_example_nl"


# ------------------------------------------------------------------ the run

def test_the_run_reaches_the_first_gate_and_stops_there(client):
    start(client)
    state = wait_for(client, REPLAYED, at_gate(4))
    gate = state["session"]["pending_gate"]
    assert gate["summary"] and gate["pending"] is True
    # a pending gate is an acceptance problem — silence is not consent
    assert any("awaiting an admin decision" in p for p in state["acceptance_problems"])


def test_the_recorded_interventions_show_up_as_first_class_objects(client):
    start(client)
    state = wait_for(client, REPLAYED, at_gate(4))
    session = state["session"]
    assert session["intervention_count"] == 3          # the Stage-B measurement, replayed
    assert {i["category"] for i in session["interventions"]} == {"DIAGNOSIS"}
    kinds = [e["kind"] for e in state["events"]]
    assert "intervention" in kinds and "tool_use" in kinds and "gate_requested" in kinds


def test_events_are_polled_incrementally_not_re_sent(client):
    start(client)
    first = wait_for(client, REPLAYED, at_gate(4))
    assert first["events"]
    again = client.get(
        f"/api/onboard/session/{REPLAYED}?since={first['last_seq']}"
    ).json()
    assert all(e["seq"] > first["last_seq"] for e in again["events"])


def test_approval_resumes_the_run_to_the_next_gate(client):
    start(client)
    wait_for(client, REPLAYED, at_gate(4))
    assert decide(client, REPLAYED, "approved", "plan is right").status_code == 200
    state = wait_for(client, REPLAYED, at_gate(6))
    assert state["session"]["verification_gates"][0]["verdict"] == "approved"
    assert state["session"]["verification_gates"][0]["admin"] == "chris"


def test_changes_requested_sends_the_agent_back_to_the_same_gate(client):
    start(client)
    wait_for(client, REPLAYED, at_gate(4))
    decide(client, REPLAYED, "changes_requested", "prefer the aggregator route")
    state = wait_for(
        client, REPLAYED,
        lambda s: (g := s["session"].get("pending_gate")) and g["phase"] == 4
        and g["summary"].startswith("(revised)"),
    )
    assert state["session"]["verification_gates"][0]["verdict"] == "changes_requested"


def test_a_refusal_stops_the_run_and_stays_in_the_record(client, tmp_path):
    start(client)
    wait_for(client, REPLAYED, at_gate(4))
    decide(client, REPLAYED, "approved", "plan is right")
    wait_for(client, REPLAYED, at_gate(6))
    decide(client, REPLAYED, "refused", "dates extract as 'Aanbeveling'")

    state = wait_for(client, REPLAYED, lambda s: s["status"] == "refused")
    assert state["live"] is False
    assert any("refused by chris" in p for p in state["acceptance_problems"])
    assert state["session"]["acceptable"] is False

    # durable, not just in memory: a fresh reader of the artifact sees the refusal
    stored = json.loads((tmp_path / f"{REPLAYED}.json").read_text())
    refusal = stored["verification_gates"][-1]
    assert refusal["verdict"] == "refused" and refusal["admin"] == "chris"
    assert refusal["note"] == "dates extract as 'Aanbeveling'"


def test_a_verdict_needs_a_note_and_an_unknown_verdict_is_refused(client):
    start(client)
    wait_for(client, REPLAYED, at_gate(4))
    assert decide(client, REPLAYED, "approved", "   ").status_code == 400
    assert decide(client, REPLAYED, "lgtm", "sure").status_code == 400
    # ... and the gate is still open, not silently consumed
    assert client.get(f"/api/onboard/session/{REPLAYED}").json()["session"]["pending_gate"]


def test_a_gate_for_a_source_with_no_run_is_404_not_a_silent_write(client):
    assert decide(client, "no_such_source", "approved", "fine").status_code == 404


def test_stopping_a_run_is_a_recorded_terminal_state(client):
    start(client)
    wait_for(client, REPLAYED, at_gate(4))
    r = client.post(f"/api/onboard/session/{REPLAYED}/stop")
    assert r.status_code == 200
    state = client.get(f"/api/onboard/session/{REPLAYED}").json()
    assert state["status"] == "stopped" and state["live"] is False


def test_the_full_flow_can_be_approved_to_the_end(client):
    start(client)
    for phase, verdict in ((4, "approved"), (6, "approved"), (7, "approved")):
        wait_for(client, REPLAYED, at_gate(phase))
        assert decide(client, REPLAYED, verdict, f"gate {phase} ok").status_code == 200
    state = wait_for(client, REPLAYED, lambda s: s["status"] == "finished")
    assert [g["verdict"] for g in state["session"]["verification_gates"]] == ["approved"] * 3
    assert state["session"]["phase"] == 8


# ------------------------------------------------------------------ listings + artifacts

def test_sessions_lists_live_runs_and_stored_artifacts(client):
    start(client)
    wait_for(client, REPLAYED, at_gate(4))
    body = client.get("/api/onboard/sessions").json()
    live = [s for s in body["sessions"] if s["live"]]
    assert [s["source_id"] for s in live] == [REPLAYED]
    assert live[0]["pending_gate"]["phase"] == 4
    assert body["intervention_budget"] == 5
    assert REPLAYED in body["recorded"]           # the recorded Stage-B session, untouched


def test_an_unknown_session_is_404(client):
    assert client.get("/api/onboard/session/nope").status_code == 404


def test_probe_artifacts_are_served_as_they_were_recorded(client):
    body = client.get("/api/onboard/session/tuchtrecht/probes").json()
    names = [a["name"] for a in body["artifacts"]]
    assert "probe0_bulk_surfaces.json" in names
    probe0 = next(a for a in body["artifacts"] if a["name"] == "probe0_bulk_surfaces.json")
    payload = probe0["payload"]
    assert payload["verdict"] and "surfaces" in payload
    # verified-absent rows are positive knowledge and must survive to the UI
    assert any(s["status"] == "absent" for s in payload["surfaces"])


def test_the_draft_manifest_view_reports_validation_not_just_yaml(client):
    body = client.get("/api/onboard/session/woo_besluiten/manifest").json()
    # served verbatim, comments and all — the admin reads the file the agent wrote
    assert body["exists"] and "\nsource:" in body["yaml"] and body["yaml"].startswith("#")
    assert body["problems"] == []                 # a shipped manifest validates
    assert "diff" in body                         # None when Postgres has no baseline


def test_a_source_with_no_draft_says_so_rather_than_erroring(client):
    body = client.get("/api/onboard/session/no_such_source/manifest").json()
    assert body["exists"] is False and "Phase 4" in body["note"]


def test_the_flow_spec_served_to_the_ui_is_the_one_the_agent_runs(client):
    body = client.get("/api/onboard/flow").json()
    assert body["path"].endswith("onboarding-flow.md")
    assert "Phase 0 — Intake" in body["markdown"]
    assert body["gate_phases"] == [4, 6, 7]
    assert body["verdicts"] == ["approved", "changes_requested", "refused"]
