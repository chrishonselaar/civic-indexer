"""onboarding agent runtime — W6 build slice 2 (engine/onboarding_agent.py).

Everything the runtime does *deterministically* is tested here with no SDK, no API key
and no live host: the engine verbs are plain functions over an OnboardingSession, so the
tool surface the agent will drive is exactly the surface these tests drive.

Guards the properties design/w6-onboarding-agent.md §A depends on:
  - the verb surface and the declared tool specs cannot drift apart;
  - a verb failure is returned as data (`ok: False`), never raised into the session;
  - the surface gate stays fail-closed through the tool layer (no probe / incomplete
    probe ⇒ `fail_closed`, not a quiet "allowed");
  - the intervention counter is INSTRUMENTED — a write to engine code is logged whether
    or not the agent reports it, while manifests and reads are not interventions;
  - the system prompt is assembled from the real flow spec (no silent paraphrase);
  - a session artifact round-trips, so a run is resumable.

The last test constructs a real SDK MCP server when claude-agent-sdk is installed — that
is in-process, so it verifies the adapter without spending anything.
"""

import asyncio
import json
from pathlib import Path

import pytest

from engine import onboarding, onboarding_agent as agent, probe

HOST = "https://repository.example"

# --- canned protocol bodies (trimmed but real-shaped) -----------------------------------

SRU_EXPLAIN = (
    '<?xml version="1.0"?>'
    '<sru:explainResponse xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse">'
    "<sru:version>2.0</sru:version>"
    "</sru:explainResponse>"
)


def router(routes: dict, *, default=(404, "")):
    """http_get dispatching on URL path; an unlisted path 404s (= verified-absent)."""

    def http_get(url, params):
        if url.endswith("/sru"):
            key = "sru"
        elif url.endswith("/oai"):
            key = f"oai:{params.get('verb')}"
        elif url.endswith("/robots.txt"):
            key = "robots"
        elif url.endswith("/sitemap.xml"):
            key = "sitemap"
        elif url.endswith("/api/3/action/package_search"):
            key = "ckan"
        else:
            key = url
        status, text = routes.get(key, default)
        if status is None:
            raise RuntimeError(text or "connection failed")
        return {"status": status, "text": text}

    return http_get


def make_tools(tmp_path: Path, routes: dict | None = None, **kw) -> agent.EngineTools:
    session = onboarding.OnboardingSession(source_id="probe_source", intent="test intake")
    return agent.EngineTools(
        session,
        probe_dir=tmp_path / "probes",
        http_get=router(routes if routes is not None else {}),
        **kw,
    )


# --- the verb surface -------------------------------------------------------------------


def test_every_declared_verb_has_a_handler_and_a_wellformed_schema():
    tools = agent.EngineTools(onboarding.OnboardingSession(source_id="x"))
    for verb in agent.VERBS:
        assert callable(getattr(tools, verb.name, None)), f"{verb.name} has no handler"
        assert verb.schema["type"] == "object"
        assert set(verb.schema["required"]) <= set(verb.schema["properties"]), verb.name
        assert verb.description.strip(), verb.name


def test_unknown_verb_is_data_not_an_exception(tmp_path):
    out = agent.EngineTools(onboarding.OnboardingSession(source_id="x")).call("nope")
    assert out["ok"] is False
    assert "unknown verb" in out["error"]
    assert "surface_gate" in out["verbs"]


def test_engine_failure_comes_back_as_a_result(tmp_path):
    tools = make_tools(tmp_path)
    out = tools.call("validate_manifest", {"path": str(tmp_path / "absent.yaml")})
    assert out["ok"] is False
    assert out["error_type"]  # an engine exception, reported not raised


def test_bad_arguments_come_back_as_a_result(tmp_path):
    out = make_tools(tmp_path).call("set_phase", {"nonsense": 1})
    assert out["ok"] is False
    assert "bad arguments" in out["error"]


# --- probe 0 + the surface gate through the tool layer ----------------------------------


def test_probe_writes_an_artifact_and_reports_the_verdict(tmp_path):
    tools = make_tools(tmp_path, {"sru": (200, SRU_EXPLAIN)})
    out = tools.call("probe_bulk_surfaces", {"hosts": [HOST]})
    assert out["ok"] is True
    assert out["verdict"] == "bulk_surface_available"
    artifact = Path(out["artifact_path"])
    assert artifact.exists()
    assert json.loads(artifact.read_text())["recommended"]["surface"] == "sru"


def test_gate_without_a_probe_fails_closed(tmp_path):
    out = make_tools(tmp_path).call("surface_gate", {"chosen_surface": "paged_html_search"})
    assert out["ok"] is False
    assert out["fail_closed"] is True


def test_gate_on_an_incomplete_probe_fails_closed(tmp_path):
    # A transport failure leaves a surface undetermined -> the probe is unenforceable.
    tools = make_tools(tmp_path, {"sru": (None, "connection reset")})
    tools.call("probe_bulk_surfaces", {"hosts": [HOST]})
    out = tools.call("surface_gate", {"chosen_surface": "paged_html_search"})
    assert out["ok"] is False
    assert out["fail_closed"] is True


def test_gate_blocks_a_scraper_over_sru_and_records_it_on_the_session(tmp_path):
    tools = make_tools(tmp_path, {"sru": (200, SRU_EXPLAIN)})
    tools.call("probe_bulk_surfaces", {"hosts": [HOST]})
    out = tools.call("surface_gate", {"chosen_surface": "paged_html_search"})
    assert out["ok"] is True
    assert out["status"] == "blocked"
    assert tools.session.gate is not None and tools.session.gate.blocked
    # ... and the same choice with a recorded reason is an override, not a block.
    out2 = tools.call(
        "surface_gate",
        {"chosen_surface": "paged_html_search", "justification": "SRU lacks the body text"},
    )
    assert out2["status"] == "overridden"
    assert tools.session.gate.status == "overridden"


# --- manifest validation ----------------------------------------------------------------


def test_validate_manifest_reports_problems_without_storing(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("source:\n  id: nope\n", encoding="utf-8")
    out = make_tools(tmp_path).call("validate_manifest", {"path": str(bad), "store": True})
    assert out["ok"] is False
    assert out["problems"]
    assert "stored_version" not in out


def test_validate_manifest_refuses_to_store_a_probe_allowed_manifest(tmp_path):
    # A manifest that only validates with allow_probes still carries open TODO(probe)
    # markers; storing it would let it leave test mode with open probes.
    real = Path("manifests/vlaardingen_raad.yaml")
    out = make_tools(tmp_path).call(
        "validate_manifest", {"path": str(real), "allow_probes": True, "store": True}
    )
    assert out["ok"] is True
    assert out["stored"] is False
    assert "stored_version" not in out


# --- the intervention counter -----------------------------------------------------------


def test_counter_excludes_gates_and_reports_the_budget(tmp_path):
    tools = make_tools(tmp_path)
    out = tools.call("log_intervention", {"category": "GATE", "note": "admin approved"})
    assert out["intervention_count"] == 0
    out = tools.call("log_intervention", {"category": "DIAGNOSIS", "note": "decoy param"})
    assert out["intervention_count"] == 1
    assert out["budget"] == onboarding.INTERVENTION_BUDGET
    assert out["within_budget"] is True


def test_bad_intervention_category_is_rejected(tmp_path):
    out = make_tools(tmp_path).call("log_intervention", {"category": "OOPS", "note": "x"})
    assert out["ok"] is False
    assert "unknown intervention category" in out["error"]


@pytest.mark.parametrize(
    "tool_name,tool_input,expected",
    [
        ("Write", {"file_path": "engine/strategies/new_thing.py"}, "ENGINE-GAP"),
        ("Edit", {"file_path": "/abs/repo/ui/monitor.js"}, "ENGINE-GAP"),
        ("Write", {"file_path": "tests/test_new.py"}, "ENGINE-GAP"),
        ("Write", {"file_path": "manifests/nieuwegein.yaml"}, None),  # the declared output
        ("Read", {"file_path": "engine/frontier.py"}, None),          # reading is not a change
        ("Bash", {"command": "curl -s https://x/robots.txt"}, None),  # recon, not a code change
        (agent.qualified("probe_bulk_surfaces"), {}, None),           # a declared verb
    ],
)
def test_classify_tool_use(tool_name, tool_input, expected):
    assert agent.classify_tool_use(tool_name, tool_input) == expected


def test_engine_edits_are_logged_even_if_the_agent_never_reports_them(tmp_path):
    tools = make_tools(tmp_path)
    assert tools.record_tool_use("Write", {"file_path": "engine/extract/xml_text.py"}) == "ENGINE-GAP"
    assert tools.record_tool_use("Write", {"file_path": "manifests/x.yaml"}) is None
    assert tools.session.intervention_count == 1
    assert tools.session.interventions[0].category == "ENGINE-GAP"
    assert "auto-detected" in tools.session.interventions[0].note


# --- acceptance -------------------------------------------------------------------------


def test_acceptance_needs_the_gate_and_says_it_is_only_half(tmp_path):
    tools = make_tools(tmp_path, {"sru": (200, SRU_EXPLAIN)})
    out = tools.call("acceptance_check")
    assert out["ok"] is False
    assert any("surface gate not run" in p for p in out["problems"])
    assert "other half" in out["note"]

    tools.call("probe_bulk_surfaces", {"hosts": [HOST]})
    tools.call(
        "surface_gate",
        {"chosen_surface": "sru", "resolved_obligations": ["content_completeness"]},
    )
    assert tools.call("acceptance_check")["ok"] is True


def test_cost_projection_is_tracked_separately_from_agent_spend(tmp_path):
    tools = make_tools(tmp_path)
    tools.call("record_cost", {"projected_eur": 1.0, "eur": 0.5})
    assert tools.call("record_cost", {"eur": 1.0})["within_projection"] is True
    assert tools.call("record_cost", {"eur": 1.0})["within_projection"] is False
    assert tools.session.agent_cost_usd == 0.0  # token spend never pollutes the EUR check


# --- prompt assembly --------------------------------------------------------------------


def test_system_prompt_carries_the_contract_the_flow_and_the_intake():
    prompt = agent.build_system_prompt(
        "https://vlaardingen.raadsinformatie.nl", "council decisions", "vlaardingen_raad"
    )
    assert "https://vlaardingen.raadsinformatie.nl" in prompt
    assert "council decisions" in prompt
    assert "manifests/vlaardingen_raad.yaml" in prompt
    # the hard rules...
    assert "Probe 0 before any scraper" in prompt
    assert str(onboarding.INTERVENTION_BUDGET) in prompt
    # ...every verb, under its SDK-visible name...
    for verb in agent.VERBS:
        assert agent.qualified(verb.name) in prompt
    # ...and the flow spec verbatim, not a paraphrase.
    assert "Phase 3 — The stress-test battery" in prompt
    assert agent.FLOW_DOC.read_text(encoding="utf-8")[-200:] in prompt


def test_missing_flow_spec_is_loud_not_a_silent_fallback(tmp_path):
    with pytest.raises(FileNotFoundError):
        agent.build_system_prompt("u", "i", "s", flow_doc=tmp_path / "gone.md")


def test_allow_list_covers_the_verbs_and_readonly_recon_only():
    allowed = agent.allowed_tools()
    assert all(agent.qualified(v.name) in allowed for v in agent.VERBS)
    assert "Read" in allowed
    # Write/Edit/Bash are deliberately NOT allow-listed: they must reach the permission
    # gate so the intervention counter sees them.
    assert not ({"Write", "Edit", "Bash"} & set(allowed))


# --- persistence ------------------------------------------------------------------------


def test_session_artifact_round_trips(tmp_path):
    tools = make_tools(tmp_path, {"sru": (200, SRU_EXPLAIN)})
    tools.call("probe_bulk_surfaces", {"hosts": [HOST]})
    tools.call("surface_gate", {"chosen_surface": "sru"})
    tools.call("log_intervention", {"category": "JUDGMENT", "note": "picked uitspraak axis"})
    tools.call("set_phase", {"phase": 5})
    tools.session.agent_cost_usd = 1.25

    path = agent.save_session(tools.session, tmp_path / "sessions")
    assert path.exists()
    back = agent.load_session("probe_source", tmp_path / "sessions")
    assert back.phase == 5
    assert back.intervention_count == 1
    assert back.gate.chosen_surface == "sru"
    assert back.agent_cost_usd == 1.25
    assert agent.load_session("never_onboarded", tmp_path / "sessions") is None


# --- the SDK adapter (in-process; no API call) ------------------------------------------


def _mcp_handlers(server):
    return server["instance"].request_handlers


def test_sdk_server_exposes_every_verb_with_its_schema(tmp_path):
    pytest.importorskip("claude_agent_sdk")
    import mcp.types as t

    server = agent.build_sdk_server(make_tools(tmp_path))
    assert server["type"] == "sdk" and server["name"] == agent.SERVER
    listed = asyncio.run(
        _mcp_handlers(server)[t.ListToolsRequest](t.ListToolsRequest(method="tools/list"))
    ).root.tools
    assert {x.name for x in listed} == {v.name for v in agent.VERBS}
    # the JSON Schema survives the adapter, so optional args stay optional
    schema = {x.name: x.inputSchema for x in listed}["surface_gate"]
    assert schema["required"] == ["chosen_surface"]
    assert "justification" in schema["properties"]


def test_sdk_server_marks_a_failed_verb_as_an_error(tmp_path):
    """The failure flag the SDK reads is `is_error`; getting the spelling wrong would show
    the agent every fail-closed gate as a success."""
    pytest.importorskip("claude_agent_sdk")
    import mcp.types as t

    server = agent.build_sdk_server(make_tools(tmp_path))
    call = t.CallToolRequest(
        method="tools/call",
        params=t.CallToolRequestParams(
            name="surface_gate", arguments={"chosen_surface": "paged_html_search"}
        ),
    )
    result = asyncio.run(_mcp_handlers(server)[t.CallToolRequest](call)).root
    assert result.isError is True
    assert json.loads(result.content[0].text)["fail_closed"] is True
