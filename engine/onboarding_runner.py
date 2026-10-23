"""onboarding_runner — W6 build slice 3: the live run behind the onboarding GUI.

`onboarding_agent.run_onboarding()` drives one onboarding as a *headless* session: one
query, one stream of messages, done. The GUI needs the same flow with two properties it
does not have:

  1. **It pauses.** Phases 4/6/7 are admin decisions (design/w6-onboarding-agent.md §E).
     The agent files a gate with `request_gate` and ends its turn; the run then sits in
     `awaiting_gate` until a human answers, and is resumed with that answer — or stopped,
     if the answer is a refusal.
  2. **It is observable while it runs.** Every assistant message, tool call and tool result
     becomes an append-only event the UI polls (`monitor.js` pattern — polling, not a
     streaming connection, so a reload never loses the run).

Hosting is settled in §D: the run is an **asyncio task inside the FastAPI process**, one
per source, refused rather than queued if one is already live. The durable record is the
same JSON session artifact `onboarding_agent` already writes, plus an events JSONL beside
it — so a process restart loses the agent's conversational memory but nothing else.

Two drivers, one shape:

  - `sdk` — the real thing (`claude-agent-sdk`, an API key, live HTTP, ~$3.40 a run).
  - `replay` — no SDK, no key, no requests: it replays a recorded Stage-B session artifact
    as events and files real gates, so the whole UI (probe cards, diffs, gate approve /
    refuse / changes, the counter) can be built and tested without spending anything. The
    driver is stamped on every run and rendered in the UI: a replay must never be mistakable
    for a live onboarding.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine import onboarding, onboarding_agent as agent

DRIVERS = ("sdk", "replay")

# Where the *product's* onboarding state lives. Deliberately NOT
# `experiments/stage_b/sessions/`: that directory holds recorded Stage-B evidence
# (`vlaardingen_raad.json` is the 3-DIAGNOSIS measurement the roadmap cites), and a replay
# run of the same source id would silently overwrite the measurement it is replaying.
# The runner writes here and only ever *reads* the recorded dir.
RUN_DIR = Path("var/onboarding")
RECORDED_DIR = agent.SESSION_DIR

# Terminal states carry *why* the run ended: a refusal is not a failure and neither is an
# admin stop, and the UI must be able to tell the three apart.
STATUSES = ("running", "awaiting_gate", "finished", "refused", "stopped", "failed")
LIVE_STATUSES = frozenset({"running", "awaiting_gate"})

MAX_EVENT_TEXT = 4000       # per-event text cap; the artifacts hold the full objects
MAX_EVENTS_IN_MEMORY = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def events_path(source_id: str, session_dir: Path = RUN_DIR) -> Path:
    return Path(session_dir) / f"{source_id}.events.jsonl"


class RunAlreadyActive(RuntimeError):
    """One run per source (§D): two agents writing one manifest is not a supported case."""


@dataclass
class OnboardingRun:
    """One onboarding, live or finished."""

    source_id: str
    url: str
    intent: str
    driver: str = "replay"
    model: str | None = None
    max_turns: int = 200
    session_dir: Path = RUN_DIR
    session: onboarding.OnboardingSession | None = None

    status: str = "running"
    error: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    _task: asyncio.Task | None = None
    _answer: asyncio.Future | None = None
    _seq: int = 0

    # -- lifecycle ---------------------------------------------------------------------

    def __post_init__(self) -> None:
        if self.driver not in DRIVERS:
            raise ValueError(f"unknown driver {self.driver!r}; one of {DRIVERS}")
        if self.session is None:
            self.session = agent.load_session(self.source_id, self.session_dir) or (
                onboarding.OnboardingSession(source_id=self.source_id, intent=self.intent)
            )

    def start(self) -> "OnboardingRun":
        body = self._drive_sdk if self.driver == "sdk" else self._drive_replay
        self.emit("run", text=f"onboarding started ({self.driver}) — {self.url}",
                  meta={"intent": self.intent})
        self._task = asyncio.get_running_loop().create_task(self._guard(body()))
        return self

    async def _guard(self, coro) -> None:
        try:
            await coro
            if self.status in LIVE_STATUSES:
                self._settle("finished", "run complete")
        except asyncio.CancelledError:
            self._settle("stopped", "run stopped by the admin")
            raise
        except Exception as e:  # a dead agent must say so, not vanish
            self.error = f"{type(e).__name__}: {e}"
            self._settle("failed", self.error)

    def _settle(self, status: str, note: str) -> None:
        self.status = status
        self.finished_at = _now()
        self.emit("run", text=note, meta={"status": status})
        self._save()

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        elif self.status in LIVE_STATUSES:
            self._settle("stopped", "run stopped by the admin")

    # -- gates -------------------------------------------------------------------------

    def resolve_gate(self, verdict: str, note: str, admin: str = "admin") -> dict[str, Any]:
        """Record the admin's answer and release the paused run.

        `refused` stops the run; the other two resume it. All three are stored — the
        session artifact keeps a refusal exactly as it keeps an approval (§E).
        """
        assert self.session is not None
        gate = self.session.resolve_gate(verdict, note, admin)
        self.emit("gate_resolved", text=f"phase {gate.phase}: {verdict} — {note}",
                  meta={"gate": gate.to_dict()})
        self._save()
        if self._answer is not None and not self._answer.done():
            # Released via the future's own loop: FastAPI may run this handler in a
            # threadpool, and setting a future from another thread is a silent race.
            self._answer.get_loop().call_soon_threadsafe(
                self._answer.set_result, {"verdict": verdict, "note": note, "admin": admin}
            )
        elif verdict == "refused":
            # No live task to release (e.g. the process restarted): the verdict still
            # stands, so record the terminal state rather than leaving it "awaiting".
            self._settle("refused", f"refused at phase {gate.phase} by {admin}")
        elif self.status == "awaiting_gate":
            self.status = "stopped"
            self.emit("run", text="verdict recorded, but the agent process is gone — "
                                  "restart the run to continue from the artifact")
        return gate.to_dict()

    async def _await_gate(self) -> dict[str, Any]:
        """Park the run on the pending gate and wait for the admin."""
        assert self.session is not None
        gate = self.session.pending_gate
        self.status = "awaiting_gate"
        self.emit("gate_requested", text=gate.summary,
                  meta={"gate": gate.to_dict()})
        self._save()
        self._answer = asyncio.get_running_loop().create_future()
        answer = await self._answer
        self._answer = None
        self.status = "running"
        return answer

    # -- drivers -----------------------------------------------------------------------

    async def _drive_sdk(self) -> None:
        """The real onboarding: one SDK conversation, paused and resumed at each gate."""
        sdk = agent._sdk()
        assert self.session is not None
        tools = agent.EngineTools(self.session)
        options = agent.build_options(
            tools, url=self.url, intent=self.intent,
            max_turns=self.max_turns, model=self.model,
        )
        kickoff = (
            f"Onboard this source: {self.url}\n\nPurpose: {self.intent}\n\n"
            f"Start at Phase 0/1 of the flow. Work through the phases; at each verification "
            f"gate (4, 6, 7) call request_gate and end your turn. Source id: {self.source_id}."
        )
        async with sdk.ClaudeSDKClient(options=options) as client:
            await client.query(kickoff)
            while True:
                async for message in client.receive_response():
                    self._ingest(message)
                    self._sync_session()
                    self._save()
                if self.session.pending_gate is None:
                    return                      # the agent finished without a new gate
                answer = await self._await_gate()
                if answer["verdict"] == "refused":
                    self._settle("refused", f"refused by {answer['admin']}: {answer['note']}")
                    return
                await client.query(_resume_text(answer))

    async def _drive_replay(self) -> None:
        """Replay a recorded session as events + real gates. No SDK, no key, no requests."""
        assert self.session is not None
        for step in replay_script(self.source_id, self.url, self.intent):
            await asyncio.sleep(step.get("delay", 0.05))
            kind = step["kind"]
            if kind == "intervention":
                self.session.log_intervention(
                    step["category"], step["note"], step.get("phase")
                )
                self._sync_session()
            elif kind == "surface_gate":
                self.session.set_surface_gate(
                    onboarding.GateDecision.from_dict(step["decision"])
                )
                self.emit("surface_gate", text=step["decision"]["reason"],
                          meta={"decision": step["decision"]})
            elif kind == "cost":
                self.session.add_cost(step.get("eur", 0.0))
                if step.get("projected_eur") is not None:
                    self.session.cost_projected_eur = step["projected_eur"]
                self.session.agent_cost_usd += step.get("agent_usd", 0.0)
            elif kind == "gate":
                self.session.request_gate(step["phase"], step["summary"], step.get("detail", ""))
                answer = await self._await_gate()
                if answer["verdict"] == "refused":
                    self._settle("refused", f"refused by {answer['admin']}: {answer['note']}")
                    return
                self.emit("assistant", text=_resume_text(answer))
                if answer["verdict"] == "changes_requested":
                    # Honest replay: a change request means the agent goes back and asks
                    # again, so the same gate is re-filed rather than silently skipped.
                    self.session.request_gate(
                        step["phase"], f"(revised) {step['summary']}",
                        f"Revised after: {answer['note']}",
                    )
                    answer = await self._await_gate()
                    if answer["verdict"] == "refused":
                        self._settle("refused", f"refused by {answer['admin']}: {answer['note']}")
                        return
            else:
                self.emit(kind, text=step.get("text", ""), meta=step.get("meta"))
                if step.get("phase") is not None:
                    self.session.advance(step["phase"])
            self._save()

    # -- events ------------------------------------------------------------------------

    def emit(self, kind: str, text: str = "", meta: dict[str, Any] | None = None) -> dict:
        self._seq += 1
        event = {
            "seq": self._seq,
            "at": _now(),
            "kind": kind,
            "text": (text or "")[:MAX_EVENT_TEXT],
            "meta": meta or {},
        }
        self.events.append(event)
        if len(self.events) > MAX_EVENTS_IN_MEMORY:
            del self.events[: len(self.events) - MAX_EVENTS_IN_MEMORY]
        self._append_event(event)
        return event

    def _append_event(self, event: dict[str, Any]) -> None:
        path = events_path(self.source_id, self.session_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _ingest(self, message: Any) -> None:
        """Turn one SDK message into events, without importing SDK types.

        Deliberately duck-typed: the runtime must keep working across SDK versions, and a
        message shape it does not recognise is recorded as `unknown` rather than dropped.
        """
        for block in getattr(message, "content", None) or []:
            text = getattr(block, "text", None)
            name = getattr(block, "name", None)
            if text:
                self.emit("assistant", text=text)
            elif name:
                self.emit("tool_use", text=name,
                          meta={"tool": name, "args": _jsonable(getattr(block, "input", {}))})
            elif getattr(block, "tool_use_id", None):
                self.emit("tool_result", text=_summarise(getattr(block, "content", "")),
                          meta={"is_error": bool(getattr(block, "is_error", False))})
        cost = getattr(message, "total_cost_usd", None)
        if cost:
            assert self.session is not None
            self.session.agent_cost_usd += float(cost)
            self.emit("result", text=f"turn cost ${float(cost):.4f}",
                      meta={"turns": getattr(message, "num_turns", None)})

    def _sync_session(self) -> None:
        """Emit an event for any intervention that appeared since the last check.

        The counter is instrumented (the permission callback auto-logs engine edits), so
        interventions can appear without any message saying so — the UI would otherwise
        show a number changing with no explanation.
        """
        assert self.session is not None
        seen = sum(1 for e in self.events if e["kind"] == "intervention")
        for entry in self.session.interventions[seen:]:
            self.emit("intervention", text=entry.note, meta=entry.to_dict())

    # -- state -------------------------------------------------------------------------

    def _save(self) -> None:
        assert self.session is not None
        agent.save_session(self.session, self.session_dir)

    def state(self, since: int = 0) -> dict[str, Any]:
        assert self.session is not None
        return {
            "source_id": self.source_id,
            "url": self.url,
            "intent": self.intent,
            "driver": self.driver,
            "status": self.status,
            "live": self.status in LIVE_STATUSES,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "session": self.session.to_dict(),
            "acceptance_problems": self.session.acceptance_problems(),
            "events": [e for e in self.events if e["seq"] > since],
            "last_seq": self._seq,
        }


def _resume_text(answer: dict[str, Any]) -> str:
    verdict = answer["verdict"]
    if verdict == "approved":
        return (f"Gate APPROVED by {answer['admin']}: {answer['note']}\n\n"
                f"Continue with the next phase of the flow.")
    return (f"Gate: CHANGES REQUESTED by {answer['admin']}: {answer['note']}\n\n"
            f"Address exactly that, then file the gate again with request_gate.")


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _summarise(content: Any) -> str:
    if isinstance(content, str):
        return content[:MAX_EVENT_TEXT]
    if isinstance(content, list):
        parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
        return "\n".join(parts)[:MAX_EVENT_TEXT]
    return str(content)[:MAX_EVENT_TEXT]


# ============================================================================ registry


class Registry:
    """The live runs, keyed by source id. One process, one registry (§D)."""

    def __init__(self, session_dir: Path = RUN_DIR) -> None:
        self.session_dir = Path(session_dir)
        self.runs: dict[str, OnboardingRun] = {}

    def start(
        self, url: str, intent: str, source_id: str, *,
        driver: str = "replay", model: str | None = None, max_turns: int = 200,
        resume: bool = False,
    ) -> OnboardingRun:
        existing = self.runs.get(source_id)
        if existing is not None and existing.status in LIVE_STATUSES:
            raise RunAlreadyActive(
                f"'{source_id}' already has a {existing.status} run — stop it first"
            )
        session = (
            agent.load_session(source_id, self.session_dir) if resume
            else onboarding.OnboardingSession(source_id=source_id, intent=intent)
        )
        run = OnboardingRun(
            source_id=source_id, url=url, intent=intent, driver=driver, model=model,
            max_turns=max_turns, session_dir=self.session_dir, session=session,
        )
        self.runs[source_id] = run
        return run.start()

    def get(self, source_id: str) -> OnboardingRun | None:
        return self.runs.get(source_id)

    def listing(self) -> list[dict[str, Any]]:
        """Every session this box knows about: live runs first, then stored artifacts.

        A finished onboarding is still a thing the admin reads (what was decided, what it
        cost, whether a gate was refused), so the list is artifact-backed rather than
        memory-backed.
        """
        rows: list[dict[str, Any]] = []
        for run in self.runs.values():
            s = run.session
            rows.append({
                "source_id": run.source_id, "intent": run.intent, "driver": run.driver,
                "status": run.status, "live": run.status in LIVE_STATUSES,
                "phase": s.phase, "interventions": s.intervention_count,
                "pending_gate": s.pending_gate.to_dict() if s.pending_gate else None,
                "agent_cost_usd": s.agent_cost_usd, "acceptable": s.acceptable,
                "started_at": run.started_at,
            })
        known = {r["source_id"] for r in rows}
        for path in sorted(Path(self.session_dir).glob("*.json")):
            source_id = path.stem
            if source_id in known:
                continue
            try:
                s = onboarding.OnboardingSession.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
            rows.append({
                "source_id": source_id, "intent": s.intent, "driver": None,
                "status": "stored", "live": False, "phase": s.phase,
                "interventions": s.intervention_count,
                "pending_gate": s.pending_gate.to_dict() if s.pending_gate else None,
                "agent_cost_usd": s.agent_cost_usd, "acceptable": s.acceptable,
                "started_at": None,
            })
        return sorted(rows, key=lambda r: (not r["live"], r["source_id"]))


# ============================================================================ replay


def replay_script(
    source_id: str, url: str, intent: str, recorded_dir: Path = RECORDED_DIR
) -> list[dict[str, Any]]:
    """The development driver: a recorded Stage-B session, told as events.

    Interventions, the surface-gate decision and the cost figures come from the stored
    artifact when there is one (`experiments/stage_b/sessions/<id>.json` — Vlaardingen's
    3-DIAGNOSIS run is the reference), so what the UI renders in replay is real recorded
    material, not invented material. Everything else is the flow's own phase narrative.
    """
    recorded: dict[str, Any] = {}
    path = Path(recorded_dir) / f"{source_id}.json"
    if path.exists():
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            recorded = {}

    script: list[dict[str, Any]] = [
        {"kind": "assistant", "phase": 0,
         "text": f"Phase 0 — intake. URL {url}; intent: {intent}."},
        {"kind": "assistant", "phase": 1,
         "text": "Phase 1 — recon: robots.txt, ToS, API docs, and probe 0 (bulk surfaces) "
                 "before any surface is chosen."},
        {"kind": "tool_use", "text": "probe_bulk_surfaces",
         "meta": {"tool": "probe_bulk_surfaces", "args": {"source_id": source_id},
                  "replayed": True}},
    ]
    for entry in recorded.get("interventions", []):
        if entry.get("category") == "GATE":
            continue        # gate decisions are replayed as gates, not as interventions
        script.append({"kind": "intervention", "category": entry["category"],
                       "note": entry.get("note", ""), "phase": entry.get("phase")})
    if recorded.get("gate"):
        script.append({"kind": "surface_gate", "decision": recorded["gate"]})
    script += [
        {"kind": "assistant", "phase": 2,
         "text": "Phase 2 — identity + envelope mapping; Phase 3 — the 13-probe battery."},
        {"kind": "cost", "eur": 0.0,
         "projected_eur": recorded.get("cost_projected_eur") or 2.0},
        {"kind": "gate", "phase": 4,
         "summary": f"Plan for {source_id}: discovery surface, identity key, extraction "
                    f"and a priced projection — approve before the test crawl.",
         "detail": "Phase 4 gate (replay). In a live run this carries the human-readable "
                   "plan, the probe evidence behind each choice, and the cost projection."},
        {"kind": "assistant", "phase": 5,
         "text": "Phase 5 — test run on a small window, recording fixtures so later "
                 "iteration costs zero live requests."},
        {"kind": "cost", "eur": recorded.get("cost_eur") or 0.0,
         "agent_usd": recorded.get("agent_cost_usd") or 0.0},
        {"kind": "gate", "phase": 6,
         "summary": "Test-run results: reconciliation verdict, sample records and the "
                    "extraction quality you can see in the result cards.",
         "detail": "Phase 6 gate (replay). Wrong extractions are meant to be obvious here — "
                   "this is the gate that reuses the search result renderer."},
        {"kind": "assistant", "phase": 7,
         "text": "Phase 7 — acceptance evidence: intervention budget, surface gate resolved, "
                 "cost within projection, reconciled verdict."},
        {"kind": "gate", "phase": 7,
         "summary": f"Take {source_id} out of test mode and into production scheduling.",
         "detail": "Phase 7 gate (replay). A refusal here is recorded and permanently "
                   "blocks acceptance until a later request supersedes it."},
        {"kind": "assistant", "phase": 8,
         "text": "Phase 8 — scheduled. Manifest and fixtures are the two artifacts; "
                 "everything learned is in the manifest, including what was ruled out."},
    ]
    return script
