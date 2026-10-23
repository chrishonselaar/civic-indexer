# W6 — Onboarding-agent architecture (the flagship)

**Status: architecture doc (the item the roadmap says must land "first").** All four W6
*prerequisites* now exist — the three bulk-protocol strategies (`sru_records`, `oai_pmh`,
bulk-archive router) plus the fail-closed **probe 0** (`engine/probe.py`, `civic
probe-bulk`). What did **not** exist was the thing that consumes them: the agent runtime,
the probe sandbox, and the UI that make onboarding a *product surface* instead of a Claude
Code terminal session. This doc specifies those three pieces and, concretely, the **gate**
that wires probe 0 into the agent's surface-ranking step (the remaining SURF-1/SURF-2 work
from `bulk-endpoint-discovery.md`).

It is grounded, per the working method, in what already happened: Stage B ran the full
onboarding flow end-to-end for two novel sources at **5 / 2 / 0** human interventions
(`../experiments/stage_b/intervention-log.md`) — but it ran it in a *general coding agent*
(Claude Code) driving the engine CLI by hand. W6 productizes **exactly that flow**; it does
not invent a new one. `onboarding-flow.md` is the behavioural spec; this doc is how it
becomes software.

## The core reframing

Stage B is the existence proof and the blueprint at once. A general agent, given the
onboarding flow as instructions and the engine CLI as its hands, onboarded Haarlem,
tuchtrecht, and Vlaardingen. The interventions it logged were **almost all once-per-engine
skeleton gaps**, not per-source reasoning failures (`intervention-log.md`: "four of five are
once-per-engine"). That tells us the productization target precisely:

> W6 is a **thin harness** around the proven flow — a purpose-built agent with the engine's
> own verbs as tools, a bounded live-HTTP sandbox, and a UI that renders probe results,
> manifest diffs, and gate approvals as first-class objects. The engine, the manifest DSL,
> the fixtures, and the 13-probe battery are unchanged.

This respects the standing invariant (`design/README.md`, decisions taken): **new source mechanics land as
strategies inside existing hooks, never as new pipeline stages.** W6 adds *no* pipeline
stage and *no* envelope change. It adds an operator on top of the engine, the same way the
W5 monitor is a read-only operator on top of the ledger.

Design stance, inherited verbatim from `onboarding-flow.md`: **the agent is expensive and
rare** (onboarding, healing); **the engine is deterministic and cheap** (every run).
Everything the agent learns — including negative knowledge — is written into the manifest,
so nothing is rediscovered.

## The three pieces (roadmap W6: "agent runtime; probe sandbox; UI stack")

### A. Agent runtime — Claude Agent SDK, engine verbs as tools

The agent is a **Claude Agent SDK session** with three inputs:

- a **system prompt** = the onboarding flow (`onboarding-flow.md`, Phases 0–8) plus the
  standing procedures (the 13-probe battery, the blast-radius tiers, the DSL primitives from
  `decision-inventory.md`);
- a **tool set** = the engine's already-existing verbs, exposed as SDK tools (below);
- the **intake**: a URL and one sentence of purpose (`onboarding-flow.md` Phase 0).

The key economy: **the engine CLI is already the tool surface.** Every phase of the flow
maps to a subcommand that exists today. The runtime does not reimplement engine logic; it
calls it and reads structured results.

**This runtime is now built** — `engine/onboarding_agent.py`, `civic onboard` — as the
mapping below, minus the parts that need probes 1–13 to be tooled (still agent-driven via
read-only recon). Ten verbs are exposed as an in-process SDK MCP server (`mcp__civic__*`);
`tests/test_onboarding_agent.py` drives the same surface with no SDK and no live host.

| Flow phase / probe | Existing engine capability | Exposed as tool |
|---|---|---|
| Phase 1 recon · **probe 0** (bulk surface) | `engine/probe.py` · `civic probe-bulk` | `probe_bulk_surfaces(hosts)` → `BulkProbeReport` |
| Phase 1 recon · fingerprint | archetype match (manifest templates) | `fingerprint(url)` (reuses cheap-surface scan) |
| Phase 3 battery (1–13) | polite live-HTTP probes (sandbox, §B) | one tool per probe class; results → `onboarding_probes:` |
| Phase 4 draft | `manifest.validate` / `manifest.store` | `validate_manifest(yaml)` (accepts `--allow-probes` in test mode) |
| Phase 5 test run | `civic run <src> --phase … --record-fixtures` | `run_phase(src, phases, window, limit, record_fixtures=True)` |
| Phase 5 facet discovery | SRC-enricher TBL-rule pattern | `propose_facets(sample)` |
| Phase 6 verify | test index + `civic report <run_id>` | `render_results(run_id)`, `run_report(run_id)` |
| Phase 5/6 iterate | fixture replay (no live requests) | `regression_run(src)` against `tests/fixtures/` |
| Phase 7 accept | acceptance validation (hard gates) | `acceptance_check(src)` |

Two runtime concepts the SDK session must carry that Stage B tracked by hand:

- **Intervention counter, built in.** `intervention-log.md` defines the categories
  (`ENGINE-GAP`, `DIAGNOSIS`, `JUDGMENT`, `GATE`) and the pass bar (≤5 non-gate interventions
  per source). W6 makes this a live counter on the session object: any time the agent must
  step outside declared tools — write engine code, diagnose a trap the battery missed, make a
  judgment the probes couldn't settle — it logs a categorized intervention. This is both the
  product's honesty surface (the admin sees how much hand-holding a source cost) and the
  regression signal (a `DIAGNOSIS` intervention is a battery-extension ticket, exactly as in
  Stage B).
- **Everything lands in two artifacts** (`onboarding-flow.md` through-line): the **manifest**
  (all decisions + probe results + negative knowledge) and the **fixture set** (ground truth
  for zero-cost iteration + future self-healing). The runtime writes both as a side effect of
  running the flow — no separate persistence step.

Model: the onboarding agent is the reasoning-heaviest, lowest-frequency actor in the system;
default it to the strongest available Claude model. Cost is dominated by the (rare)
onboarding event, not by steady-state runs (which never invoke the agent).

### B. Probe sandbox — bounded, polite, read-only live HTTP

Probe 0 and the 13-probe battery are the only place the agent touches a live site, and every
one of them is already written against an **injectable `http_get`** (`probe.py`,
`strategies/*`) so it record/replays against fixtures with no live host touched under test.
The sandbox is the *production* `http_get` with the discipline the flow requires:

- **Read-only, bounded, polite by construction.** Probe 11 (politeness ceiling) sets the
  delay/concurrency; the sandbox enforces it. `robots.txt` and ToS notes captured in Phase 1
  gate what the sandbox will fetch. This is the lowest blast-radius agent activity: no writes,
  small bounded request counts (Phase 4 notes the entire battery is "a few hundred polite
  requests").
- **Fixture recording is automatic.** Every fetched page becomes a fixture (Phase 5), seeding
  the permanent regression suite. Iteration after the first pass is **zero live requests**
  (Phase 6 loops against cached fixtures).
- **Fail-closed carries through.** Probe 0 already returns `verdict: incomplete` +
  `enforceable: False` on any transport/5xx error, and the CLI exits non-zero
  (`cmd_probe_bulk` returns 2). The sandbox must surface that to the runtime as a hard gate,
  not a warning — see §C gate model.

Nothing new is needed in the engine here; the sandbox is a thin production wrapper (rate
limiter + logger + fixture writer) around the `http_get` the probes already accept.

### C. UI stack — first-class probe/diff/gate objects, reusing the existing surface

The UI is the product. It reuses the stack already serving the search UI and the W5 monitor:
**FastAPI + static assets** (`ui/search_api.py`, `ui/monitor.py`, `ui/monitor.{html,js,css}`)
— no new framework decision is forced by W6, and reuse keeps the deploy story (the
ubuntu-srv FastAPI pattern) intact. W6 adds an onboarding surface alongside them.

The flow's artifacts become **first-class UI objects**, not chat transcript:

- **Probe report cards.** A `BulkProbeReport` (and each battery probe's result) renders as a
  card: the ranked surface list, the recommended winner, and — critically — the
  **verified-absent** and **manual** rows shown as positive knowledge, not omissions. Probe 0
  already serializes to exactly this shape (`BulkProbeReport.to_dict()`).
- **Manifest diffs.** Each agent revision to the draft manifest renders as a diff the admin
  reviews — the flow's "human-readable plan, not YAML" (Phase 4), with the YAML attached for
  inspection.
- **Gate cards.** The three verification gates (Phases 4 / 6 / 7) are approval objects:
  approve / adjust / redirect. Gate 2 is deliberately the *search-results* view (Phase 6:
  "wrong extractions are obvious when you see 'Datum: Aanbeveling' in a result card") — it
  reuses the search UI's result renderer against the test index.
- **Intervention counter + cost projection**, live on the session header.
- **Consumes W5.** The verify/monitor acts (user-story Acts 2–3) are the monitor surface
  already built — run history, reconciliation verdicts, queues, spend. W6 embeds those views
  rather than rebuilding them (the roadmap's reason for sequencing W5 before W6).

## The gate model — wiring probe 0 in (the concrete SURF-1/SURF-2 work)

This is the part with a callable dependency today and the smallest buildable slice. Probe 0
made the surface *ranking* enforceable-not-prose; the **gate** makes the agent *honour it*
enforceable-not-prose. Without it, an agent could still call `probe-bulk`, read the JSON, and
then scrape anyway — the exact Stage B failure mode, one level up.

**The rule (from `bulk-endpoint-discovery.md` §"Where this lands"):** before the agent may
select any scraping strategy (`paged_html_search`, grid enumeration, link-walk, headless), it
MUST:

1. run probe 0 on the provider host(s) — including the KOOP/overheid split-host case
   `cmd_probe_bulk` already handles (`repository.overheid.nl` ≠ the public host);
2. call `report.raise_if_unenforced()` — an `incomplete` probe blocks the scraper fallback
   (fail-closed: an undetermined probe can never read as "no bulk surface exists");
3. if `report.recommended` is non-null, prefer it; a lower surface may be chosen **only with a
   recorded reason** written into the manifest (the flow's negative-knowledge discipline).

Two boundaries probe 0 explicitly leaves to the agent (recorded in `probe.py` and the SURF
entries), which the gate must turn into **owed obligations**, not silent skips:

- **`frontend_xhr` (SURF-1 residue).** Probe 0 records this surface as `manual` — a private
  JSON API cannot be found without executing page JS / reading a HAR, and a private API
  outranks scraping (the rijksoverheid HTML→SPA→JSON lesson, `onboarding-flow.md` Phase 1.3).
  The gate raises this as an **explicit agent task**: do the DevTools/HAR pass before
  concluding "scrape is best." In the UI it is a checklist item on the recon card, not an
  automatable green tick.
- **Content-completeness classification (SURF-2).** A found bulk surface may carry
  metadata/abstract only, not full body text (tuchtrecht SRU's `gzd:url` points back at the
  1-rps FRBR host). The gate requires the agent to **classify each found surface full-content
  vs metadata-only** and, when metadata-only, wire the **two-tier config** (`fetch.transport:
  none` + `extraction: payload_text` + `missing:['fulltext']` upgrade) rather than shipping a
  shallow index that looks complete.

**Make it code, mirroring probe 0's own lesson.** Probe 0 exists because a ranking that lived
in prose was unenforceable. By the same logic the gate should be a thin, tested engine helper
— proposed `engine/onboarding.py::surface_gate(report, chosen_surface) -> GateDecision` —
rather than a paragraph in the system prompt. It:

- calls `report.raise_if_unenforced()` (propagates `BulkProbeIncomplete`);
- if a scraping `chosen_surface` is proposed while `report.recommended` is a `bulk_metadata`
  surface, returns `blocked` with the recommended surface attached (agent must justify in the
  manifest to override);
- emits the two owed obligations (`frontend_xhr_pass`, `content_completeness`) as unmet flags
  until the agent records their resolution;
- is the acceptance-check dependency: a manifest cannot leave test mode (Phase 7) with an
  unresolved gate, exactly as it cannot leave with an open `TODO(probe)`.

This helper + its tests was the **first slice** (build order below) and is now **built**
(`engine/onboarding.py::surface_gate` → `GateDecision`; `tests/test_onboarding.py`): it is
enforceable, unit-tested against the existing tuchtrecht probe-0 artifact, and needs neither
the SDK runtime nor the UI to be correct.

## Onboarding session state

An onboarding is a resumable object (Stage B's mid-flow stops were resumable — e.g. the
Haarlem `12|{fetch,extract}` open run). Most of its state already has a home; W6 adds a thin
session record tying them together. **This model is now built** —
`engine/onboarding.py::OnboardingSession` (+ `Intervention`), `tests/test_onboarding.py`:
the intervention counter (categories + ≤5 pass bar verbatim from `intervention-log.md`, GATE
entries logged-not-counted), the surface-gate decision, and cost actuals, with a Phase-7
`acceptance_problems()` / `raise_if_not_acceptable()` that the session owns (the engine owns
the reconciliation half). Persisted as a JSON artifact for now (`to_dict`/`from_dict`); the
Postgres `onboarding_sessions` home is deferred with the runtime-hosting question below.

- **Draft manifest** — versioned already via `manifest.store()` (validate-manifest prints the
  stored version).
- **Probe artifacts** — already written under `experiments/stage_b/probes/<source>/…` (probe 0
  writes `probe0_bulk_surfaces.json` there); production onboarding writes the same shape.
- **Fixtures** — `tests/fixtures/` (Phase 5).
- **Gate approvals + intervention log + cost actuals** — the *new* per-session state. Small; a
  single `onboarding_sessions` row (or a JSON artifact) keyed by source id, holding phase
  cursor, gate decisions, intervention entries, and running cost. Follows the ledger-is-
  ground-truth bet (Postgres), not a separate store.

### D. Runtime hosting — settled 2026-07-28: in-process, one live run per source

The build order's slice 3 could not start without answering this, so it is answered here
rather than left in "open questions": **the agent runs as an asyncio task inside the same
FastAPI process that serves the UI** (`engine/onboarding_runner.py`), not in a separate
worker.

The reason is the gate model below, not convenience. An onboarding is **one conversation
that pauses three times**, not a batch job: at each verification gate the agent stops and
waits for an admin decision that may be *approve*, *refuse*, or *change this and continue*.
The SDK's `ClaudeSDKClient` is exactly that shape — an open session you send a follow-up
query to — and keeping it open across a gate means keeping a live object in memory. A
separate worker would need a queue, a protocol for "here is the admin's answer", and a
second deploy unit, for a tool a single admin runs a handful of times a year.

What that costs, recorded rather than hidden:

- **A uvicorn restart kills the live conversation.** This is survivable, not lossless: the
  session artifact is written after every message and the event log after every event, so
  the run resumes as a *fresh agent against recovered state* (`--resume` semantics), losing
  the agent's own conversational memory. Resuming the SDK conversation itself
  (`resume=<session-id>`) stays unused and stays an open question.
- **The run must never block the event loop.** The runner owns an `asyncio.Task`; every
  engine verb it calls is the same synchronous engine code the CLI calls, so a long
  `run_phase` blocks that task and not the request handlers only because the SDK executes
  tools off the request path. The UI therefore **polls** (`/api/onboard/session/{id}`,
  like `monitor.js`) instead of holding a streaming connection.
- **One run per source, refused rather than queued.** `POST /api/onboard/sessions` for a
  source with a live run returns 409. Two agents writing one manifest is not a scenario
  worth supporting.

Deliberately *not* decided by this: the Postgres `onboarding_sessions` home. The runner
reads and writes the JSON artifact `engine/onboarding_agent.py` already defines, so moving
the store later touches one module.

### E. The verification-gate model — a refusal is a first-class outcome

Phases 4, 6 and 7 are admin decisions (`onboarding-flow.md`), and Stage B took them by a
human typing into a terminal. Making them UI objects raises a question the flow never had
to answer: **what is a gate decision, as data?** The trap to avoid is a UI where "approve"
is a button and "no" is the absence of a click — that records agreement and forgets
refusal, which is the half worth keeping.

So a gate is a **request/verdict pair on the session**, both persisted:

- `GateRequest` — the agent, at phase 4/6/7, calls `request_gate(phase, summary, detail)`
  and *stops*. The request carries the human-readable plan/results/evidence the flow asks
  for (Phase 4: "human-readable, not YAML"). A request with no verdict is `pending`, and a
  pending gate is an acceptance problem — silence is not consent.
- `GateVerdict` — `approved` | `refused` | `changes_requested`, plus a **required note**
  and the admin's name. All three are recorded identically; none is inferable from the
  others' absence.

The three verdicts differ only in what happens next, and each is honest about it:

| verdict | run | session | acceptance |
|---|---|---|---|
| `approved` | resumed with the approval | verdict stored, `GATE` intervention logged (not counted) | clears that gate |
| `changes_requested` | resumed with the note as the next instruction | same, and the gate reopens when the agent asks again | still open until re-asked and approved |
| `refused` | **stopped**, not resumed | verdict stored; the run's terminal state is `refused` | permanently blocks acceptance for that phase until a later request supersedes it |

A refusal is therefore *durable and legible*: `civic onboard --resume` and the UI both show
"gate 2 refused by <admin>: <note>", and `acceptance_problems()` reports it, exactly as it
reports a blocked surface gate or an open `TODO(probe)`. The manifest cannot leave test mode
on a source whose admin said no, and the reason why is in the artifact rather than in
someone's memory.

Two smaller rules fall out:

- **Reaching phase 7 without an approved gate-7 verdict is a problem**, not a pass. Below
  phase 7 the absence of a verdict is merely "not there yet", so sessions that never reach
  the gate are unaffected (this keeps the existing acceptance semantics intact).
- **The gate verdict is the admin's, so it is logged as a `GATE` intervention** — the
  category `intervention-log.md` already defines as logged-but-not-counted. The intervention
  counter measures how much the *agent* needed hand-holding; an admin exercising a designed
  decision point is not hand-holding.

## Blast radius & autonomy

Onboarding is the highest-touch agent activity in the system, so it is the most gated —
consistent with the blast-radius tiers (`decision-inventory.md` §16):

- **Live HTTP:** read-only, bounded, polite (sandbox §B) — low blast radius.
- **Manifest authorship:** every change is admin-gated (three verification gates); nothing
  crawls at scale before gate 1; nothing reaches production before gate 3's hard acceptance
  checks.
- **The intervention counter** is the honesty instrument: it exposes precisely where the agent
  left its declared tools, and (per Stage B) routes `DIAGNOSIS` events to battery extension
  rather than burying them.

## Build order (smallest enforceable slice first)

1. **`surface_gate()` helper + tests** — ✅ **DONE** (`engine/onboarding.py`,
   `tests/test_onboarding.py`, 8 tests). The probe-0 wiring as code (§C): fail-closed on an
   incomplete probe, blocks a scraper chosen over a metadata-bearing bulk surface (override
   only with a recorded justification), and emits `frontend_xhr_pass` + `content_completeness`
   as owed obligations that `raise_if_unresolved()` (the acceptance hook) holds. Unit-tested
   against synthetic reports and the real tuchtrecht probe-0 artifact (via new
   `BulkProbeReport.from_dict`). Closes the last enforceable gap in SURF-1/SURF-2.
   The **session-state model** it needs (`OnboardingSession` — intervention counter, gate
   decision, cost actuals, Phase-7 acceptance) is likewise **built** and unit-tested
   (`engine/onboarding.py`), so the runtime (2) and UI (3) consume it rather than inventing it.
2. **Headless agent runtime** — ⚙️ **BUILT, not yet live-validated**
   (`engine/onboarding_agent.py`, `civic onboard`, `tests/test_onboarding_agent.py`, 28
   tests). The Stage-B flow as an Agent SDK session with the engine-verb tool set (§A):
   ten verbs as an in-process SDK MCP server, the system prompt assembled from
   `onboarding-flow.md` itself (so editing the spec edits the agent), and
   `OnboardingSession` threaded through and persisted per turn.
   One design addition worth recording: **the intervention counter is instrumented, not
   self-reported.** Only the verbs plus read-only recon are allow-listed, so every
   Write/Edit/Bash reaches the permission callback, which auto-logs an `ENGINE-GAP` when
   the agent edits engine code. `DIAGNOSIS`/`JUDGMENT` stay agent-reported — they have no
   tool signature — so the counter is honest about its own coverage rather than trusting
   the agent for the one category that *does* leave a trace. Same lesson as probe 0 and
   the surface gate: a rule that lives only in the prompt is unenforceable.
   Remaining for this slice: the success criterion — re-onboard a known source (e.g.
   Vlaardingen, the 0-intervention run) driven by the SDK instead of by hand, with the
   counter agreeing with the logged count. That needs live HTTP + API spend, so it is a
   deliberate separate step; `civic onboard --dry-run` verifies the whole assembly
   (prompt, tool surface, session) without either.
3. **UI surface** — ✅ **BUILT 2026-07-28** (`ui/onboard.{py,html,css,js}`,
   `engine/onboarding_runner.py`, `tests/test_onboard_ui.py` + `tests/test_onboarding_gates.py`,
   35 tests). Probe/diff/gate cards on the existing FastAPI+static stack (§C), served at
   `/onboard` beside `/monitor`, with the run hosted in-process per §D and the three-verdict
   gate card per §E. Two things worth recording beyond the plan:
   - **A second driver, `replay`.** A live onboarding is ~$3.40 and touches a real site, so
     it cannot be the thing you develop or test against. The replay driver retells a recorded
     Stage-B session as events and files *real* gates, so the whole product path — task
     lifecycle, pause/resume, refusal, polling, artifact views — is exercised at zero cost.
     It writes to `var/onboarding/` and only ever reads `experiments/stage_b/sessions/`:
     replaying a measurement must not be able to overwrite it.
   - **The robots posture is promoted, not buried.** Probe 0's `policy_flags` render as a
     red block on the probe card (with the aggregator's "shrinks, does not dissolve" caveat
     where one applies), because POL-1's actual failure was four sources shipping against
     `Disallow: /` hosts with nothing anywhere *showing* it.
   Not built: the manifest is read-only in the UI (changes go through
   `changes_requested`), and the W5 monitor is linked rather than embedded.
4. **Exit criterion #6** (roadmap): a *new* municipality onboarded end-to-end **through the
   UI** from URL + intent statement, gates included — which re-opens Stage C. **Still open,
   and the surface being built does not close it**: every run to date is `replay`. The
   criterion needs one live `sdk` run against a source nobody has onboarded, with the
   intervention counter agreeing with what actually happened.

## Open questions (not settled here)

- **UI interactivity depth** — how much of the 13-probe battery is one-click tools vs
  agent-narrated; where the admin edits the draft (inline manifest vs guided fields).
  Partly answered by slice 3: the admin does not edit YAML in the UI at all — the draft is
  read-only with a diff, and `changes_requested` is how an edit is asked for. Whether that
  survives a real onboarding is untested.
- ~~**Agent runtime hosting**~~ — **settled 2026-07-28, §D**: in-process asyncio task, one
  run per source, polling UI. Still open underneath it: resuming the agent's *own*
  conversation across a process restart (the SDK's `resume=<session-id>` is unused — a
  resumed run restarts the agent against the recovered state).
- **How much battery is auto-tooled** — probe 0 is fully automated; probes 1–13 are a mix of
  automatable (cap, pagination, content-type) and judgment-bearing (relationships, schema
  drift). The split decides how much is a tool vs a prompt.
- **Multi-tenant** — deferred (single admin, per settled decisions); the session model should
  not preclude it but need not serve it yet.

## Where this lands

- **`README.md` index** — add this doc (row 17).
- **`failure-modes.md`** — SURF-1/SURF-2 gain their enforcement home: the `frontend_xhr` and
  content-completeness boundaries move from "probe 0 leaves to the agent" to "the gate holds
  the agent to." Update those rows' mitigation column to point here once `surface_gate()`
  lands.
- **`product-roadmap.md`** — W6's "architecture doc first" item is satisfied by this doc; the
  build order above is the workstream plan.
- **`bulk-endpoint-discovery.md`** — its "what remains for W6 is wiring probe 0 into the
  ranking step" is specified by §C here.
