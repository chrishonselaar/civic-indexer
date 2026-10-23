"""Onboarding surface over the W6 agent runtime (roadmap W6, design §C/§D/§E).

The GUI half of onboarding: an admin gives a URL and one sentence of intent, and watches
the agent work the 8-phase flow — with probe reports, the draft manifest and the three
verification gates as first-class objects rather than chat transcript.

Everything stateful lives in `engine/onboarding_runner.Registry` (one asyncio task per
source, in this process, per §D). This module is transport only: it validates input, calls
the registry, and shapes JSON. The two places it does more than that are the manifest view
(draft YAML + validation problems + a diff against the last stored version) and the probe
view (the artifacts probe 0 already writes) — both read-only.

Mounted by ui/search_api.py under /api/onboard.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from engine import manifest, onboarding, onboarding_agent as agent, onboarding_runner

router = APIRouter(prefix="/api/onboard", tags=["onboard"])

MANIFEST_DIR = Path("manifests")
PROBE_DIR = Path("experiments/stage_b/probes")

registry = onboarding_runner.Registry()


def _err(msg: str, status: int, **extra) -> JSONResponse:
    return JSONResponse({"error": msg, **extra}, status_code=status)


# ------------------------------------------------------------------ sessions

@router.get("/sessions")
def sessions():
    """Every onboarding this box knows about, live runs first."""
    return {
        "sessions": registry.listing(),
        "drivers": list(onboarding_runner.DRIVERS),
        "sdk_available": _sdk_available(),
        "recorded": sorted(p.stem for p in onboarding_runner.RECORDED_DIR.glob("*.json")),
        "intervention_budget": onboarding.INTERVENTION_BUDGET,
    }


@router.post("/sessions")
async def start_session(body: dict[str, Any] = Body(...)):
    """Start a run: URL + one sentence of intent (flow Phase 0) — the whole intake.

    `driver` defaults to `replay` deliberately. A live SDK run costs ~$3.40 and touches a
    real site, so it is something the admin asks for explicitly, never a default that
    fires because a form was submitted.
    """
    url = (body.get("url") or "").strip()
    intent = (body.get("intent") or "").strip()
    if not url or not intent:
        return _err("both url and intent are required (flow Phase 0 intake)", 400)
    source_id = (body.get("source_id") or "").strip() or onboarding.source_id_from_url(url)
    driver = (body.get("driver") or "replay").strip()
    if driver not in onboarding_runner.DRIVERS:
        return _err(f"unknown driver {driver!r}", 400,
                    drivers=list(onboarding_runner.DRIVERS))
    if driver == "sdk" and not _sdk_available():
        return _err(
            "the SDK driver needs claude-agent-sdk (`uv sync --extra agent`), the `claude` "
            "CLI on PATH and an API key. Use the replay driver to work on the UI.", 409)
    try:
        run = registry.start(
            url, intent, source_id, driver=driver, model=body.get("model") or None,
            max_turns=int(body.get("max_turns") or 200),
            resume=bool(body.get("resume")),
        )
    except onboarding_runner.RunAlreadyActive as e:
        return _err(str(e), 409)
    return run.state()


@router.get("/session/{source_id}")
def session_state(source_id: str, since: int = Query(0, ge=0)):
    """The run's state plus every event after `since` (the UI polls this)."""
    run = registry.get(source_id)
    if run is not None:
        return run.state(since=since)
    stored = agent.load_session(source_id, onboarding_runner.RUN_DIR) or agent.load_session(
        source_id, onboarding_runner.RECORDED_DIR
    )
    if stored is None:
        return _err(f"no onboarding session for {source_id!r}", 404)
    return {
        "source_id": source_id,
        "url": None,
        "intent": stored.intent,
        "driver": None,
        "status": "stored",
        "live": False,
        "error": None,
        "session": stored.to_dict(),
        "acceptance_problems": stored.acceptance_problems(),
        "events": _stored_events(source_id, since),
        "last_seq": 0,
    }


@router.post("/session/{source_id}/gate")
async def resolve_gate(source_id: str, body: dict[str, Any] = Body(...)):
    """Answer the pending verification gate: approved | changes_requested | refused.

    A refusal is a recorded outcome, not a cancelled request: it stops the run, stays in
    the session artifact, and blocks acceptance until a later gate supersedes it (§E).
    """
    verdict = (body.get("verdict") or "").strip()
    note = (body.get("note") or "").strip()
    admin = (body.get("admin") or "admin").strip()
    if verdict not in onboarding.GATE_VERDICTS:
        return _err(f"unknown verdict {verdict!r}", 400,
                    verdicts=list(onboarding.GATE_VERDICTS))
    run = registry.get(source_id)
    if run is None:
        return _err(f"no live run for {source_id!r} — restart it to answer its gate", 404)
    try:
        gate = run.resolve_gate(verdict, note, admin)
    except ValueError as e:               # no pending gate, or an empty note
        return _err(str(e), 400)
    return {"ok": True, "gate": gate, "status": run.status}


@router.post("/session/{source_id}/stop")
async def stop_session(source_id: str):
    run = registry.get(source_id)
    if run is None:
        return _err(f"no run for {source_id!r}", 404)
    await run.stop()
    return {"ok": True, "status": run.status}


# ------------------------------------------------------------------ artifacts

@router.get("/session/{source_id}/manifest")
def draft_manifest(source_id: str):
    """The draft manifest as the admin should read it: YAML, what validation says about
    it, and what changed against the last stored version.

    The admin does not edit YAML here (design §"open questions"): to change the draft you
    answer a gate with `changes_requested` and let the agent revise it, so every manifest
    change keeps its authorship and its reason.
    """
    path = MANIFEST_DIR / f"{source_id}.yaml"
    if not path.exists():
        return {"source_id": source_id, "exists": False, "path": str(path),
                "note": "no draft manifest yet — the agent writes one at flow Phase 4"}
    text = path.read_text(encoding="utf-8")
    try:
        m = manifest.load(path)
        problems = manifest.validate(m, allow_probes=True)
        strict = manifest.validate(m, allow_probes=False)
    except Exception as e:
        return {"source_id": source_id, "exists": True, "path": str(path), "yaml": text,
                "parse_error": f"{type(e).__name__}: {e}"}
    baseline, version = _stored_manifest_yaml(source_id)
    return {
        "source_id": source_id,
        "exists": True,
        "path": str(path),
        "yaml": text,
        "problems": problems,                      # blocking even in test mode
        "open_probe_markers": [p for p in strict if p not in problems],
        "stored_version": version,
        # Both sides go through the same dump before diffing: the stored side is JSONB, so
        # a raw-text diff would report every quoting and key-order difference as a change
        # the agent never made.
        "diff": (
            _diff(baseline, _canonical_yaml(m), source_id) if baseline is not None else None
        ),
    }


@router.get("/session/{source_id}/probes")
def probe_artifacts(source_id: str):
    """Probe artifacts for this source — probe 0's ranked surfaces first.

    The verified-absent and manual rows are returned as they are recorded: on this surface
    they are positive knowledge ("we looked, it is not there"), not omissions.
    """
    directory = PROBE_DIR / source_id
    out: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                out.append({"name": path.name, "path": str(path),
                            "payload": json.loads(path.read_text(encoding="utf-8"))})
            except json.JSONDecodeError as e:
                out.append({"name": path.name, "path": str(path), "error": str(e)})
    return {"source_id": source_id, "dir": str(directory), "artifacts": out}


@router.get("/flow")
def flow():
    """The behavioural spec the agent is running (design/onboarding-flow.md).

    Served so the UI can show *what phase means what* beside the run, from the same file
    that assembles the agent's system prompt — one home for the spec, per §A.
    """
    path = agent.FLOW_DOC
    return {
        "path": str(path),
        "markdown": path.read_text(encoding="utf-8") if path.exists() else "",
        "gate_phases": list(onboarding.VERIFICATION_GATE_PHASES),
        "verdicts": list(onboarding.GATE_VERDICTS),
    }


# ------------------------------------------------------------------ internals

def _sdk_available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception:
        return False
    return True


def _stored_events(source_id: str, since: int) -> list[dict[str, Any]]:
    """Replay the events JSONL for a run this process no longer holds in memory."""
    path = onboarding_runner.events_path(source_id)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("seq", 0) > since:
            events.append(event)
    return events[-onboarding_runner.MAX_EVENTS_IN_MEMORY:]


def _stored_manifest_yaml(source_id: str) -> tuple[str | None, int | None]:
    """The last stored manifest version rendered back to YAML, for the diff.

    Postgres is the ledger, but the onboarding surface must stay usable when it is down —
    a missing baseline degrades to "no diff", never to an error page.
    """
    try:
        from engine import db

        with db.tx() as cur:
            cur.execute(
                "SELECT version, payload FROM manifests WHERE source_id = %s "
                "ORDER BY version DESC LIMIT 1",
                (source_id,),
            )
            row = cur.fetchone()
    except Exception:
        return None, None
    if row is None:
        return None, None
    return _canonical_yaml(row["payload"]), row["version"]


def _canonical_yaml(payload: Any) -> str:
    return yaml.safe_dump(
        json.loads(json.dumps(payload, default=str)), sort_keys=True, allow_unicode=True
    )


def _diff(baseline: str, draft: str, source_id: str) -> dict[str, Any]:
    lines = list(difflib.unified_diff(
        baseline.splitlines(), draft.splitlines(),
        fromfile=f"{source_id} (stored)", tofile=f"{source_id} (draft)", lineterm="",
    ))
    return {
        "unified": "\n".join(lines),
        "added": sum(1 for x in lines if x.startswith("+") and not x.startswith("+++")),
        "removed": sum(1 for x in lines if x.startswith("-") and not x.startswith("---")),
    }
