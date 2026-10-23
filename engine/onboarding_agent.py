"""onboarding_agent — W6 build slice 2: the headless Agent SDK runtime.

Stage B proved the onboarding flow works (5/2/0 interventions across three novel
sources) but it ran in a *general* coding agent driving the engine CLI by hand. This
module is the thin harness the architecture doc specifies (design/w6-onboarding-agent.md
§A): a **Claude Agent SDK session** with

  - a system prompt = the onboarding flow (`design/onboarding-flow.md`) + the standing
    runtime contract (surface gate, intervention budget, artifacts),
  - a tool set = the engine's *already existing* verbs exposed as SDK tools — the CLI
    subcommands are the tool surface, so nothing here reimplements engine logic,
  - the intake = a URL and one sentence of purpose (flow Phase 0).

It adds no pipeline stage and no envelope change; it is an operator on top of the
engine, the same way the W5 monitor is a read-only operator on top of the ledger.

Three properties are deliberate, and each is why the module is laid out as it is:

1. **The verbs are plain functions, not SDK objects.** `EngineTools` methods take and
   return JSON-able dicts and never import the SDK. That makes the whole tool surface
   unit-testable with no SDK, no API key and no live HTTP — the SDK layer below is a
   thin adapter over it. A tool that raises is reported back to the agent as a result
   (`ok: False`) rather than killing the session: the agent must be able to read a
   failure and adapt, exactly as the Stage-B operator did.

2. **The intervention counter is instrumented, not self-reported.** The declared verbs
   plus read-only recon are the allow-list; anything else (Write/Edit/Bash) reaches
   `permission_gate()`, which classifies it with `classify_tool_use()` and logs an
   `ENGINE-GAP` when the agent edits engine code — the Stage-B category for "the
   skeleton lacked a capability". `DIAGNOSIS` and `JUDGMENT` stay agent-reported via the
   `log_intervention` verb because they are irreducibly judgment calls (a debugging
   insight has no tool signature), which is how Stage B counted them too. So the counter
   is *honest about its own coverage*: code edits cannot be silently omitted; reasoning
   interventions still rely on the agent's own report.

3. **The session is the state.** `OnboardingSession` (engine/onboarding.py) is threaded
   through every verb and persisted as a JSON artifact after each turn, so a run is
   resumable and the counter/gate/cost survive a restart. The runtime does not invent
   parallel state.

Live-run boundary: everything above is deterministic and tested. The actual SDK session
(`run_onboarding`) needs the `claude-agent-sdk` package, the `claude` CLI, an API key and
a live site — `civic onboard --dry-run` exercises the whole assembly (prompt, tool
surface, session) without any of them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from engine import manifest, onboarding, probe

# Where a session artifact lands. Probe artifacts already live under
# experiments/stage_b/probes/<source>/; sessions sit beside them until the design's
# deferred Postgres `onboarding_sessions` home exists.
SESSION_DIR = Path("experiments/stage_b/sessions")
MANIFEST_DIR = Path("manifests")
FLOW_DOC = Path("design/onboarding-flow.md")

# Paths whose modification means "the skeleton lacked a capability" — the Stage-B
# ENGINE-GAP category. Writing a *manifest* is the declared output of onboarding and is
# NOT an intervention; writing engine/UI/test code is.
ENGINE_CODE_ROOTS = ("engine/", "ui/", "tests/", "migrations/")
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# Built-in Claude Code tools the agent may use without it counting as stepping outside
# the declared surface: read-only recon (flow Phase 1 reads robots.txt, docs, the repo).
READ_ONLY_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")

# The MCP server key AND name. Keep them identical: SDK MCP tools are addressed as
# mcp__<server>__<tool>, and using one string for both removes any ambiguity about
# which of the two forms the runtime registers.
SERVER = "civic"


def qualified(tool_name: str) -> str:
    """The SDK-visible name of one of our verbs (mcp__civic__<verb>)."""
    return f"mcp__{SERVER}__{tool_name}"


# ============================================================================ verbs


@dataclass(frozen=True)
class Verb:
    """One engine capability exposed to the agent. `schema` is a JSON Schema object —
    the MCP-native form, so optional arguments can actually be optional."""

    name: str
    description: str
    schema: dict[str, Any]


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}

VERBS: tuple[Verb, ...] = (
    Verb(
        "probe_bulk_surfaces",
        "Probe 0 (flow Phase 1 / battery row 0): actively test provider host(s) for a bulk "
        "discovery surface (SRU/OAI/CKAN/sitemap) and record verified-absent results. Pass "
        "hosts, or source_id to derive them from a stored manifest (the KOOP/overheid "
        "split-host rule is applied for you). Writes the probe artifact. MUST be run before "
        "any scraping surface is chosen.",
        _schema({"hosts": {"type": "array", "items": _STR}, "source_id": _STR}),
    ),
    Verb(
        "surface_gate",
        "Hold the surface choice to probe 0's ranking (flow Phase 1 → gate). Returns "
        "allowed|overridden|blocked plus the obligations you still owe. A scraper chosen over "
        "a metadata-bearing bulk surface is BLOCKED unless you pass a recorded justification. "
        "Records the decision on the session; acceptance depends on it.",
        _schema(
            {
                "chosen_surface": _STR,
                "justification": _STR,
                "resolved_obligations": {"type": "array", "items": _STR},
            },
            ["chosen_surface"],
        ),
    ),
    Verb(
        "validate_manifest",
        "Validate a manifest YAML (flow Phase 4). allow_probes permits open TODO(probe) "
        "markers while drafting. store=true persists a manifest version — only for a manifest "
        "that validates with allow_probes=false.",
        _schema({"path": _STR, "allow_probes": _BOOL, "store": _BOOL}, ["path"]),
    ),
    Verb(
        "run_phase",
        "Run engine pipeline phases for a source (flow Phase 5 test run): phases is a "
        "comma-separated subset of discover,fetch,extract. Use limit + window to keep the "
        "test slice small, and record_fixtures on the first discover pass so later iteration "
        "is zero live requests.",
        _schema(
            {
                "source_id": _STR,
                "phases": _STR,
                "window": _STR,
                "limit": _INT,
                "mode": _STR,
                "budget_eur": {"type": "number"},
                "record_fixtures": _BOOL,
            },
            ["source_id", "phases"],
        ),
    ),
    Verb(
        "run_report",
        "Render the ledger report for a run id (flow Phase 6): counts, reconciliation verdict, "
        "requests, spend.",
        _schema({"run_id": _INT}, ["run_id"]),
    ),
    Verb(
        "log_intervention",
        "Record that you had to step outside the declared tools. category is one of "
        "ENGINE-GAP (engine code had to change), DIAGNOSIS (a trap the probe battery missed, "
        "found by ad-hoc debugging — also a battery-extension ticket), JUDGMENT (a decision "
        "probes and rules could not settle), GATE (an admin decision; logged, not counted). "
        "Budget: at most 5 non-GATE interventions.",
        _schema({"category": _STR, "note": _STR, "phase": _INT}, ["category", "note"]),
    ),
    Verb(
        "request_gate",
        "File a verification gate (flow Phases 4, 6 and 7) and STOP. summary is the "
        "human-readable ask — the plan in prose, not YAML (Phase 4), the test-run results "
        "(Phase 6), or the acceptance evidence (Phase 7); detail carries the evidence "
        "behind it. Your turn ends here: the admin answers approved | changes_requested | "
        "refused, and you are resumed with that answer. Do not proceed past a gate on your "
        "own authority, and do not open a second gate while one is pending.",
        _schema({"phase": _INT, "summary": _STR, "detail": _STR}, ["phase", "summary"]),
    ),
    Verb(
        "set_phase",
        "Advance the session's phase cursor (0-8) as you move through the flow.",
        _schema({"phase": _INT}, ["phase"]),
    ),
    Verb(
        "record_cost",
        "Record spend against the session: actual euros spent, and/or your cost projection "
        "for the source (flow Phase 4 asks for a priced proposal). Acceptance fails if the "
        "actual exceeds 2x the projection.",
        _schema({"eur": {"type": "number"}, "projected_eur": {"type": "number"}}),
    ),
    Verb(
        "session_state",
        "The current onboarding session: phase, intervention counter and entries, surface-gate "
        "decision, cost. Read it before claiming you are done.",
        _schema({}),
    ),
    Verb(
        "acceptance_check",
        "The session-owned half of the Phase-7 acceptance gate: intervention budget kept, "
        "surface gate run AND resolved, cost within 2x projection. Returns the list of "
        "problems (empty = this half passes). The engine still owns the other half "
        "(reconciliation verdict, per-run budget) — check the run report too.",
        _schema({}),
    ),
)

VERBS_BY_NAME = {v.name: v for v in VERBS}


class EngineTools:
    """The engine verbs as plain, SDK-free callables over one OnboardingSession.

    Every method returns a JSON-able dict and never raises: a failure is data the agent
    reads and reacts to (`ok: False`), because an onboarding run that dies on the first
    404 is not the flow Stage B validated.
    """

    def __init__(
        self,
        session: onboarding.OnboardingSession,
        *,
        manifest_dir: Path = MANIFEST_DIR,
        probe_dir: Path | None = None,
        http_get: probe.HttpGet | None = None,
    ) -> None:
        self.session = session
        self.manifest_dir = Path(manifest_dir)
        self.probe_dir = (
            Path(probe_dir)
            if probe_dir is not None
            else Path("experiments/stage_b/probes") / session.source_id
        )
        self.http_get = http_get
        self.report: probe.BulkProbeReport | None = None

    # -- dispatch ----------------------------------------------------------------------

    def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke a verb by name. Unknown verb, bad arguments and engine exceptions all
        come back as results rather than exceptions — the agent must see them."""
        if name not in VERBS_BY_NAME:
            return {"ok": False, "error": f"unknown verb {name!r}", "verbs": sorted(VERBS_BY_NAME)}
        try:
            return getattr(self, name)(**(args or {}))
        except TypeError as e:  # wrong/missing arguments from the model
            return {"ok": False, "error": f"bad arguments for {name}: {e}"}
        except Exception as e:  # engine failure — data, not a crash
            return {"ok": False, "error": str(e), "error_type": type(e).__name__}

    # -- Phase 1: recon ----------------------------------------------------------------

    def probe_bulk_surfaces(
        self, hosts: list[str] | None = None, source_id: str | None = None
    ) -> dict[str, Any]:
        candidates = list(hosts or [])
        if source_id:
            m = manifest.load_validated(
                self.manifest_dir / f"{source_id}.yaml", allow_probes=True
            )
            candidates = probe.candidate_hosts(
                manifest.get(m, "source.base_url"), extra=candidates
            )
        else:
            candidates = probe.candidate_hosts(None, extra=candidates)
        if not candidates:
            return {"ok": False, "error": "give hosts, or source_id with a stored manifest"}

        report = probe.probe_bulk_surfaces(candidates, http_get=self.http_get)
        self.report = report
        artifact = report.to_dict()
        path = self._write_artifact("probe0_bulk_surfaces.json", artifact)
        return {
            "ok": True,
            "artifact_path": str(path),
            "verdict": report.verdict,
            "enforceable": report.enforceable,
            **artifact,
        }

    # -- the surface gate (probe 0 wiring) ---------------------------------------------

    def surface_gate(
        self,
        chosen_surface: str,
        justification: str | None = None,
        resolved_obligations: list[str] | None = None,
    ) -> dict[str, Any]:
        if self.report is None:
            return {
                "ok": False,
                "error": "no probe-0 report in this session — run probe_bulk_surfaces first",
                "fail_closed": True,
            }
        try:
            decision = onboarding.surface_gate(
                self.report,
                chosen_surface,
                justification=justification,
                resolved_obligations=resolved_obligations or (),
            )
        except probe.BulkProbeIncomplete as e:
            # Fail-closed: an undetermined probe can never justify a scraper. This is a
            # distinct outcome from a returned `blocked` and the agent must re-probe.
            return {"ok": False, "fail_closed": True, "error": str(e)}
        self.session.set_surface_gate(decision)
        return {"ok": True, **decision.to_dict()}

    # -- Phase 4/5/6: manifest, run, verify --------------------------------------------

    def validate_manifest(
        self, path: str, allow_probes: bool = False, store: bool = False
    ) -> dict[str, Any]:
        m = manifest.load(path)
        problems = manifest.validate(m, allow_probes=allow_probes)
        out: dict[str, Any] = {
            "ok": not problems,
            "path": path,
            "problems": problems,
            "source_id": manifest.get(m, "source.id"),
            "allow_probes": allow_probes,
        }
        if problems or not store:
            return out
        if allow_probes:
            # Storing a manifest that still carries TODO(probe) markers would let it leave
            # test mode with open probes — the rule the engine validation exists to hold.
            out["stored"] = False
            out["note"] = "refused to store: validated with allow_probes=true"
            return out
        out["stored_version"] = manifest.store(m)
        return out

    def run_phase(
        self,
        source_id: str,
        phases: str,
        window: str | None = None,
        limit: int | None = None,
        mode: str = "backfill",
        budget_eur: float | None = None,
        record_fixtures: bool = False,
    ) -> dict[str, Any]:
        from engine import fixtures, frontier, process, runs

        m = manifest.load_validated(self.manifest_dir / f"{source_id}.yaml", allow_probes=True)
        wanted = [p.strip() for p in phases.split(",") if p.strip()]
        unknown = [p for p in wanted if p not in ("discover", "fetch", "extract")]
        if unknown:
            return {"ok": False, "error": f"unknown phase(s): {unknown}"}

        results: list[dict[str, Any]] = []
        if "discover" in wanted:
            recorder = fixtures.recorder(source_id) if record_fixtures else None
            run_id, report = frontier.run_discover(
                m,
                mode=mode,
                window_start=f"{window}-01" if window else None,
                fixture_recorder=recorder,
            )
            results.append(
                {"phase": "discover", "run_id": run_id, "verdict": report["verdict"],
                 "report": runs.render_report(run_id)}
            )
        if "fetch" in wanted or "extract" in wanted:
            run_id, report = process.run_fetch_extract(
                m, mode=mode, window=window, limit=limit, budget_eur=budget_eur
            )
            results.append(
                {"phase": "fetch,extract", "run_id": run_id, "verdict": report["verdict"],
                 "report": runs.render_report(run_id)}
            )
        return {
            "ok": all(r["verdict"] != "NOT_reconciled" for r in results),
            "runs": results,
        }

    def run_report(self, run_id: int) -> dict[str, Any]:
        from engine import runs

        return {"ok": True, "run_id": run_id, "report": runs.render_report(run_id)}

    # -- session bookkeeping -----------------------------------------------------------

    def log_intervention(self, category: str, note: str, phase: int | None = None) -> dict[str, Any]:
        entry = self.session.log_intervention(category, note, phase)
        return {
            "ok": True,
            "logged": entry.to_dict(),
            "counted": entry.counted,
            "intervention_count": self.session.intervention_count,
            "budget": onboarding.INTERVENTION_BUDGET,
            "within_budget": self.session.within_intervention_budget,
        }

    def request_gate(self, phase: int, summary: str, detail: str = "") -> dict[str, Any]:
        """Park the session on an admin decision (design §E). The runner reads
        `session.pending_gate` to know the agent has stopped and the UI owes an answer."""
        gate = self.session.request_gate(int(phase), summary, detail)
        return {
            "ok": True,
            "gate": gate.to_dict(),
            "note": (
                "Gate filed — stop here and wait. You will be resumed with the admin's "
                "verdict (approved | changes_requested | refused)."
            ),
        }

    def set_phase(self, phase: int) -> dict[str, Any]:
        self.session.advance(int(phase))
        return {"ok": True, "phase": self.session.phase}

    def record_cost(self, eur: float | None = None, projected_eur: float | None = None) -> dict[str, Any]:
        if eur is not None:
            self.session.add_cost(float(eur))
        if projected_eur is not None:
            self.session.cost_projected_eur = float(projected_eur)
        return {
            "ok": True,
            "cost_eur": self.session.cost_eur,
            "cost_projected_eur": self.session.cost_projected_eur,
            "within_projection": self.session.cost_within_projection,
        }

    def session_state(self) -> dict[str, Any]:
        return {"ok": True, **self.session.to_dict()}

    def acceptance_check(self) -> dict[str, Any]:
        problems = self.session.acceptance_problems()
        return {
            "ok": not problems,
            "problems": problems,
            "note": (
                "This is the session-owned half only (interventions, surface gate, cost). "
                "The engine owns the other half: a `reconciled` run verdict and the per-run "
                "budget — check run_report before declaring the source production-ready."
            ),
        }

    # -- instrumentation ---------------------------------------------------------------

    def record_tool_use(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        """Called for every tool the agent uses that is not on the allow-list. Logs an
        intervention when the use is one the Stage-B log would have counted, and returns
        the category (or None)."""
        category = classify_tool_use(tool_name, tool_input)
        if category:
            target = tool_input.get("file_path") or tool_input.get("path") or tool_name
            self.session.log_intervention(
                category, f"{tool_name} on {target} (auto-detected: engine code changed)"
            )
        return category

    # -- internals ---------------------------------------------------------------------

    def _write_artifact(self, name: str, payload: dict[str, Any]) -> Path:
        self.probe_dir.mkdir(parents=True, exist_ok=True)
        path = self.probe_dir / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def classify_tool_use(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Which Stage-B intervention category (if any) a raw tool use represents.

    Deliberately narrow: only a *write to engine code* is auto-classified, because that is
    the one category with an unambiguous tool signature (ENGINE-GAP — "the skeleton lacked
    a capability"). Reading files, running commands, and writing manifests/fixtures are
    normal flow work. DIAGNOSIS and JUDGMENT have no signature and stay agent-reported.
    """
    if tool_name.startswith(f"mcp__{SERVER}__"):
        return None
    if tool_name not in WRITE_TOOLS:
        return None
    target = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if not target:
        return None
    rel = target.replace("\\", "/")
    return "ENGINE-GAP" if any(root in rel for root in ENGINE_CODE_ROOTS) else None


# ============================================================================ prompt

RUNTIME_CONTRACT = """
# Runtime contract (W6 onboarding agent)

You are the civic-indexer onboarding agent. Your job is to take one civic/government
data source from a URL to a validated, production-ready manifest by running the
onboarding flow reproduced below. You are expensive and rare; the engine you configure
is deterministic and cheap. Everything you learn — including negative knowledge (what
you checked and ruled out, and why) — must end up in the manifest, so nothing is ever
rediscovered.

## Your tools are the engine's own verbs

The `{server}` tools below are the engine CLI. Prefer them over shelling out; they keep
the session's honesty record (intervention counter, surface gate, cost) correct.

{verbs}

You may also use Read/Glob/Grep/WebFetch/WebSearch freely for read-only reconnaissance —
including reading this repository's design docs (`design/decision-inventory.md` for the
manifest DSL primitives, `design/failure-modes.md`, `design/*-manifest-example.md` for
worked manifests) and the source's own robots.txt and API documentation.

## Hard rules

1. **Probe 0 before any scraper.** Before you choose a scraping strategy
   (paged_html_search, grid enumeration, link-walk, headless) you MUST call
   `probe_bulk_surfaces` and then `surface_gate`. A `blocked` gate means prefer the bulk
   surface; you may override ONLY by passing a justification, which is recorded. An
   `incomplete` probe fails closed: re-probe, never treat "undetermined" as "absent".
2. **Owed obligations are not optional.** The gate returns obligations
   (`frontend_xhr_pass` — do the DevTools/HAR pass before concluding scraping is best;
   `content_completeness` — classify each found bulk surface as full-content vs
   metadata-only and wire the two-tier config if metadata-only). Resolve them explicitly
   by passing `resolved_obligations` on a later `surface_gate` call.
3. **Intervention budget: at most 5 non-GATE interventions.** Call `log_intervention`
   whenever you step outside the declared tools — a diagnosis the probe battery missed
   (DIAGNOSIS), a judgment probes could not settle (JUDGMENT), or engine code you had to
   change (ENGINE-GAP). Edits to engine code are detected and logged automatically, so do
   not double-log those; do log the ones only you can see. Exceeding the budget is not a
   failure to hide — it is the measurement.
4. **`TODO(probe)` markers are never guessed.** A value that must come from live probing
   stays a marker until a probe fills it. A manifest with open markers cannot leave test
   mode.
5. **Stay polite and bounded.** Read-only, small request counts, the delay/concurrency
   ceiling probe 11 establishes. The whole battery should be a few hundred requests.
6. **Stop at the verification gates.** Phases 4, 6 and 7 are admin decisions, and you take
   them by calling `request_gate` — the plan (Phase 4: human-readable, not YAML), the
   results (Phase 6), the acceptance evidence (Phase 7) — and then ending your turn. The
   admin answers `approved`, `changes_requested` (fix what the note says, then ask again)
   or `refused` (stop; the source does not go to production), and you are resumed with that
   answer. Do not crawl at scale, and do not take a source to production, on your own
   authority.

## What "done" looks like

Two artifacts: the **manifest** (every decision, probe result and rejected alternative)
and the **fixture set** (recorded pages, so future iteration costs zero live requests).
Call `acceptance_check` before you claim readiness, and remember it is only the
session-owned half — a `reconciled` run verdict from `run_report` is the other half.

## This onboarding

- **Source URL:** {url}
- **Intent (Phase 0, one sentence — expand it into a draft after recon):** {intent}
- **Source id:** {source_id}  (manifest path: `manifests/{source_id}.yaml`)

# The onboarding flow (behavioural spec — follow it phase by phase)

{flow}
"""


def build_system_prompt(
    url: str, intent: str, source_id: str, *, flow_doc: Path = FLOW_DOC
) -> str:
    """Assemble the agent's system prompt: runtime contract + the flow doc verbatim.

    The flow doc is read from disk rather than duplicated here so the behavioural spec
    has exactly one home — editing `design/onboarding-flow.md` changes the agent.
    """
    path = Path(flow_doc)
    if not path.exists():
        raise FileNotFoundError(
            f"onboarding flow spec not found at {path} — the system prompt is assembled "
            "from it and must not silently fall back to a paraphrase"
        )
    verbs = "\n".join(f"- `{qualified(v.name)}` — {v.description}" for v in VERBS)
    return RUNTIME_CONTRACT.format(
        server=SERVER,
        verbs=verbs,
        url=url,
        intent=intent,
        source_id=source_id,
        flow=path.read_text(encoding="utf-8"),
    )


def allowed_tools() -> list[str]:
    """The auto-approved surface: our verbs plus read-only recon. Everything else falls
    through to `permission_gate`, which is what makes the counter instrumented."""
    return [qualified(v.name) for v in VERBS] + list(READ_ONLY_TOOLS)


# ============================================================================ persistence


def session_path(source_id: str, session_dir: Path = SESSION_DIR) -> Path:
    return Path(session_dir) / f"{source_id}.json"


def save_session(session: onboarding.OnboardingSession, session_dir: Path = SESSION_DIR) -> Path:
    path = session_path(session.source_id, session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(session.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def load_session(source_id: str, session_dir: Path = SESSION_DIR) -> onboarding.OnboardingSession | None:
    path = session_path(source_id, session_dir)
    if not path.exists():
        return None
    return onboarding.OnboardingSession.from_dict(json.loads(path.read_text(encoding="utf-8")))


# ============================================================================ SDK layer
#
# Everything below adapts the SDK-free surface above to claude-agent-sdk. The import is
# lazy so the engine, the CLI's --dry-run and the whole test suite work without the
# package installed.


def _sdk():
    try:
        import claude_agent_sdk as sdk
    except ImportError as e:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "claude-agent-sdk is not installed. `uv sync --extra agent` (it also needs the "
            "`claude` CLI on PATH and an API key). `civic onboard --dry-run` needs neither."
        ) from e
    return sdk


def build_sdk_server(tools: EngineTools):
    """Wrap the engine verbs as an in-process SDK MCP server.

    Each wrapper is a one-liner over `EngineTools.call`, so the SDK boundary carries no
    logic of its own — what the agent runs is what the tests run.
    """
    sdk = _sdk()

    def make(verb: Verb):
        @sdk.tool(verb.name, verb.description, verb.schema)
        async def _handler(args: dict[str, Any]) -> dict[str, Any]:
            result = tools.call(verb.name, args)
            return {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                ],
                # snake_case: the SDK reads `is_error` (claude_agent_sdk maps it to the MCP
                # `isError`). Spelling it `isError` here silently marks every failed verb a
                # success — see test_sdk_server_marks_a_failed_verb_as_an_error.
                "is_error": not result.get("ok", True),
            }

        return _handler

    return sdk.create_sdk_mcp_server(name=SERVER, version="0.1.0", tools=[make(v) for v in VERBS])


def permission_gate(tools: EngineTools) -> Callable:
    """The permission callback: allow, but *record*.

    Anything reaching here is off the declared surface by construction (the allow-list
    covers the verbs and read-only recon), so this is precisely the hook the honesty
    record needs. It allows the call — Stage B's agent legitimately wrote engine code —
    and logs the intervention rather than silently permitting it.
    """
    sdk = _sdk()

    async def can_use_tool(tool_name: str, input_data: dict[str, Any], context: Any):
        tools.record_tool_use(tool_name, input_data or {})
        return sdk.PermissionResultAllow(updated_input=input_data)

    return can_use_tool


def build_options(
    tools: EngineTools,
    *,
    url: str,
    intent: str,
    max_turns: int = 200,
    model: str | None = None,
    cwd: str | None = None,
):
    """The ClaudeAgentOptions for an onboarding session."""
    sdk = _sdk()
    return sdk.ClaudeAgentOptions(
        system_prompt=build_system_prompt(url, intent, tools.session.source_id),
        mcp_servers={SERVER: build_sdk_server(tools)},
        allowed_tools=allowed_tools(),
        can_use_tool=permission_gate(tools),
        permission_mode="default",
        max_turns=max_turns,
        # The onboarding agent is the reasoning-heaviest, lowest-frequency actor in the
        # system (design §A) — default it to the strongest model.
        model=model or "opus",
        cwd=cwd,
        # Onboarding reads this repo's design docs; it does not inherit the operator's
        # personal Claude Code settings.
        setting_sources=["project"],
    )


async def run_onboarding(
    url: str,
    intent: str,
    source_id: str,
    *,
    session: onboarding.OnboardingSession | None = None,
    session_dir: Path = SESSION_DIR,
    max_turns: int = 200,
    model: str | None = None,
    http_get: probe.HttpGet | None = None,
    on_message: Callable[[Any], None] | None = None,
) -> onboarding.OnboardingSession:
    """Drive one onboarding as a headless Agent SDK session.

    Replays the Stage-B flow with the engine's verbs as tools instead of a human driving
    the CLI. The session artifact is written after every message, so a crash or an
    interrupt leaves a resumable record rather than nothing.
    """
    sdk = _sdk()
    session = session or onboarding.OnboardingSession(source_id=source_id, intent=intent)
    tools = EngineTools(session, http_get=http_get)
    options = build_options(
        tools, url=url, intent=intent, max_turns=max_turns, model=model
    )

    kickoff = (
        f"Onboard this source: {url}\n\nPurpose: {intent}\n\n"
        f"Start at Phase 0/1 of the flow. Work through the phases, stop at each "
        f"verification gate and report. Source id to use: {source_id}."
    )

    async with sdk.ClaudeSDKClient(options=options) as client:
        await client.query(kickoff)
        async for message in client.receive_response():
            if on_message:
                on_message(message)
            if isinstance(message, sdk.ResultMessage):
                cost = getattr(message, "total_cost_usd", None)
                if cost:
                    session.agent_cost_usd += float(cost)
            save_session(session, session_dir)
    save_session(session, session_dir)
    return session
