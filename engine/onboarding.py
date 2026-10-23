"""onboarding — the W6 surface gate (design/w6-onboarding-agent.md §C).

Probe 0 (`engine/probe.py`) made the surface *ranking* enforceable-not-prose. The gate
here makes the agent *honour* that ranking enforceable-not-prose: without it an onboarding
agent could still call `probe-bulk`, read the JSON, and scrape anyway — the exact Stage-B
failure mode, one level up (tuchtrecht: SRU missed, ~13 h of 1-rps scraping instead).

The rule (bulk-endpoint-discovery.md §"Where this lands"): before the agent may select a
scraping strategy (`paged_html_search`, grid enumeration, link-walk, headless) it MUST

  1. run probe 0 on the provider host(s) — incl. the KOOP/overheid split-host case;
  2. call `report.raise_if_unenforced()` — an `incomplete` probe blocks the scraper
     fallback (fail-closed: an undetermined probe can never read as "no bulk surface");
  3. if `report.recommended` is a metadata-bearing bulk surface, prefer it — a scraper may
     be chosen ONLY with a reason recorded into the manifest (negative-knowledge discipline).

`surface_gate()` turns that into code. It also emits the two boundaries probe 0 explicitly
leaves to the agent as **owed obligations**, not silent skips:

  - `frontend_xhr_pass` (SURF-1 residue) — a private JSON API cannot be found without a
    DevTools/HAR pass and outranks scraping, so before concluding "scrape is best" the
    agent owes that pass;
  - `content_completeness` (SURF-2) — a found bulk surface may be metadata/abstract only
    (tuchtrecht SRU's `gzd:url` points back at the 1-rps FRBR host), so each found
    metadata-bulk surface must be classified full-content vs metadata-only and, when
    metadata-only, wired to the two-tier config rather than shipped as a shallow index.

Two more arrived with the aggregator step (SURF-3, design/aggregator-registry.md §9). An
aggregator route is *second-hand*: identity stays the publisher's but the content does not,
and neither fact is checkable by the engine.

  - `aggregator_provenance` — owed when the agent CHOOSES an aggregator. Carries the two
    rules §9 settled (a reconciled verdict means aggregator-complete, not
    publisher-complete; imported text records `confidence: NULL`, never 1.0) plus the data
    licence, which an OSS licence on the aggregator's own code does not grant.
  - `aggregator_considered` — owed when an aggregator was FOUND and the agent chose
    something else. It does not block the choice; it blocks *silence* about the choice.
    Freshness, coverage, licence or content quality are all legitimate answers. Where the
    aggregator also republishes a host whose robots.txt disallows us, the detail says so,
    because that choice then forgoes a route that avoids the host entirely.

The gate is the acceptance dependency: a manifest cannot leave test mode (onboarding-flow
Phase 7) while the gate is blocked or an obligation is open, exactly as it cannot leave
with an open `TODO(probe)` — see `GateDecision.raise_if_unresolved()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from engine import probe

# Scraping surfaces — the tier below structured/bulk in the best-first ranking
# ("… paged search → grid → link-walk → headless"). Choosing one of these while a
# metadata-bearing bulk surface was found is what the gate blocks (absent a justification).
# A private JSON API (`frontend_xhr`, `paged_json_api`) is structured, NOT scraping, and so
# is not gated here — but it is exactly what the `frontend_xhr_pass` obligation guards.
SCRAPING_SURFACES = frozenset({
    "paged_html_search",
    "grid",
    "grid_enum",
    "grid_enumeration",
    "link_walk",
    "link-walk",
    "headless",
})

# Recommendation classes strong enough that choosing a SCRAPER over them must be justified.
# `bulk_index` is deliberately absent — a bare sitemap carries URLs only and still needs
# per-URL fetching, so a scraper alongside one is legitimate (do not "tighten" this; it is
# pinned by test_bare_sitemap_does_not_block_a_scraper). `bulk_content` was added with the
# aggregator step (SURF-3): it is strictly richer than `bulk_metadata`, so a scraper chosen
# over it must clear at least the same bar.
BLOCKING_RANK_CLASSES = frozenset({"bulk_content", "bulk_metadata"})

# Discovery strategies that consume an aggregator surface. Used, alongside the
# `aggregator:` surface prefix probe 0 emits, to tell "the agent chose the aggregator" from
# "the agent chose something else while one was available".
AGGREGATOR_STRATEGIES = frozenset({"ori_elastic"})


def _is_aggregator(surface: str) -> bool:
    return surface.startswith("aggregator:") or surface in AGGREGATOR_STRATEGIES


class SurfaceGateUnresolved(RuntimeError):
    """Raised by GateDecision.raise_if_unresolved() when a manifest tries to leave test
    mode with the surface gate blocked or an obligation still open."""


@dataclass
class Obligation:
    """A boundary probe 0 left to the agent, owed until the agent records its resolution."""

    key: str
    met: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "met": self.met, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Obligation":
        return cls(key=d["key"], met=bool(d.get("met")), detail=d.get("detail", ""))


@dataclass
class GateDecision:
    chosen_surface: str
    status: str                                    # allowed | overridden | blocked
    recommended: probe.SurfaceProbe | None
    obligations: list[Obligation] = field(default_factory=list)
    justification: str | None = None
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def open_obligations(self) -> list[Obligation]:
        return [o for o in self.obligations if not o.met]

    @property
    def acceptable(self) -> bool:
        """The Phase-7 acceptance predicate: the scraper choice was not blocked AND every
        owed obligation has been recorded resolved."""
        return not self.blocked and not self.open_obligations

    def raise_if_unresolved(self) -> None:
        """Acceptance-check hook: a manifest MUST NOT leave test mode while the gate is
        blocked or an obligation is open (mirrors an open TODO(probe))."""
        if self.blocked:
            raise SurfaceGateUnresolved(self.reason)
        if self.open_obligations:
            keys = ", ".join(o.key for o in self.open_obligations)
            raise SurfaceGateUnresolved(
                f"surface gate for '{self.chosen_surface}' has open obligations: {keys}. "
                "Record their resolution in the manifest before acceptance."
            )

    def to_dict(self) -> dict[str, Any]:
        rec = self.recommended
        return {
            "chosen_surface": self.chosen_surface,
            "status": self.status,
            "acceptable": self.acceptable,
            "reason": self.reason,
            "justification": self.justification,
            "recommended": (
                {"surface": rec.surface, "host": rec.host, "url": rec.url,
                 "rank_class": rec.rank_class}
                if rec else None
            ),
            "obligations": [o.to_dict() for o in self.obligations],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GateDecision":
        """Rebuild from a to_dict() record (recommended is compacted to the fields a card
        needs; `acceptable` is derived, not stored)."""
        rd = d.get("recommended")
        rec = (
            probe.SurfaceProbe(
                surface=rd["surface"], host=rd["host"], url=rd["url"], status="found",
                rank_class=rd.get("rank_class"),
            )
            if rd else None
        )
        return cls(
            chosen_surface=d["chosen_surface"],
            status=d["status"],
            recommended=rec,
            obligations=[Obligation.from_dict(o) for o in d.get("obligations", [])],
            justification=d.get("justification"),
            reason=d.get("reason", ""),
        )


def surface_gate(
    report: probe.BulkProbeReport,
    chosen_surface: str,
    *,
    justification: str | None = None,
    resolved_obligations: Iterable[str] = (),
) -> GateDecision:
    """Hold the onboarding agent to probe 0's ranking before it commits to a surface.

    report:            a completed BulkProbeReport (from `probe_bulk_surfaces` or a stored
                       artifact via `BulkProbeReport.from_dict`).
    chosen_surface:    the surface/strategy the agent proposes (e.g. "sru", "paged_html_search").
    justification:     a recorded reason for choosing a scraper over a recommended bulk
                       surface — the manifest-level override. None ⇒ no override.
    resolved_obligations: keys the agent has recorded as resolved (e.g. after the HAR pass
                       or the content-completeness classification).

    Raises `probe.BulkProbeIncomplete` first (fail-closed): an undetermined probe can never
    justify a scraper. Otherwise returns a GateDecision — `blocked` when a scraper is chosen
    over a metadata-bearing bulk surface with no justification, `overridden` when justified,
    `allowed` otherwise — carrying the two owed obligations as unmet flags until resolved.
    """
    # Fail-closed: never rank/gate on partial evidence.
    report.raise_if_unenforced()

    chosen = (chosen_surface or "").strip()
    resolved = set(resolved_obligations)
    rec = report.recommended
    is_scraper = chosen in SCRAPING_SURFACES

    # The block condition: a scraper chosen while probe 0 recommends a content- or
    # metadata-bearing bulk surface. A bare sitemap (bulk_index) still needs per-URL
    # fetching, so it does NOT block a scraper.
    over_bulk_metadata = (
        is_scraper and rec is not None and rec.rank_class in BLOCKING_RANK_CLASSES
    )

    obligations: list[Obligation] = []
    # frontend_xhr_pass (SURF-1 residue): owed when the agent is about to scrape — it must
    # first rule out a private JSON API, which probe 0 recorded `manual` and which outranks
    # scraping. Not owed when a bulk surface is chosen (that already outranks a private API).
    if is_scraper:
        obligations.append(Obligation(
            key="frontend_xhr_pass",
            met="frontend_xhr_pass" in resolved,
            detail=(
                "DevTools/HAR pass owed before concluding 'scrape is best': probe 0 records "
                "`frontend_xhr` as `manual`; a private JSON API outranks scraping and cannot "
                "be found without executing the site's JS."
            ),
        ))
    # content_completeness (SURF-2): owed for each found metadata-bearing bulk surface — it
    # may carry abstract/metadata only, not full body text.
    meta_found = [s for s in report.found if s.rank_class == "bulk_metadata"]
    if meta_found:
        names = ", ".join(f"{s.surface}@{s.host}" for s in meta_found)
        obligations.append(Obligation(
            key="content_completeness",
            met="content_completeness" in resolved,
            detail=(
                f"classify full-content vs metadata-only for: {names}; when metadata-only, "
                "wire the two-tier config (fetch.transport:none + payload_text + "
                "missing:['fulltext'] upgrade) rather than shipping a shallow index."
            ),
        ))

    # --- aggregator obligations (SURF-3) -------------------------------------------------
    # An aggregator route is second-hand: identity stays the publisher's, content does not.
    # The envelope carries that without change (aggregator-registry.md §9), but two rules and
    # an unresolved licence ride along, and none of them is checkable by the engine — so they
    # are owed obligations, exactly like the SURF-1/SURF-2 boundaries above.
    agg_found = [s for s in report.found if s.rank_class == "bulk_content"]
    if agg_found and _is_aggregator(chosen):
        hit = next((s for s in agg_found if s.surface == chosen), agg_found[0])
        licence = hit.evidence.get("license") or "not_assessed"
        oldest, newest = hit.evidence.get("oldest_item"), hit.evidence.get("newest_item")
        # Archive depth is a coverage question, not a freshness one, and it is where the
        # aggregator route quietly fails: ORI is current to within weeks for Vlaardingen and
        # carries nothing before 2014 while the manifest declares 2008 (TOT-5). Named as a
        # fourth item rather than left to the agent's judgement — rule 5 of the register.
        span = (
            f" (4) the aggregator's own archive spans {str(oldest)[:10]} … "
            f"{str(newest)[:10]}: compare that against this source's declared range and "
            f"record which eras it CANNOT serve — a current aggregator can still start years "
            f"after the publisher's archive, and coverage is not implied by freshness. Read "
            f"the span off `items_per_year`, not off the start date alone: a sparse head "
            f"(ORI: 133 items in 2010, none in 2011-12, dense only from 2014) makes an "
            f"archive look years deeper than it is."
            if oldest else
            " (4) the aggregator's archive START was not measured; do not assume its coverage "
            "reaches the source's declared range."
        )
        obligations.append(Obligation(
            key="aggregator_provenance",
            met="aggregator_provenance" in resolved,
            detail=(
                f"'{hit.surface}' is a second-hand route; record before acceptance: "
                f"(1) `discovery.completeness_claim: aggregator` — a reconciled verdict on "
                f"this walk means AGGREGATOR-complete, not publisher-complete, and the "
                f"monitoring UI must say so rather than showing a bare green tick; "
                f"(2) imported text is written with `confidence: NULL`, never 1.0 — it is "
                f"someone else's extraction of unverified quality; "
                f"(3) the data licence, currently `{licence}` in the registry (an OSS "
                f"licence on the aggregator's code does not license its data)."
                + span
            ),
        ))
    elif agg_found:
        names = ", ".join(f"{s.surface} ({s.evidence.get('item_count')} items)"
                          for s in agg_found)
        avoidable = report.disallow_avoidable_via
        extra = (
            f" It also republishes {', '.join(sorted(avoidable))}, whose robots.txt "
            f"disallows us — so this choice forgoes a route that avoids that host for most "
            f"of the backfill. Not for all of it: an aggregator carries its own harvest, so "
            f"check its archive span and recent-window lag before treating the host as "
            f"avoided (TOT-5)."
            if avoidable else ""
        )
        obligations.append(Obligation(
            key="aggregator_considered",
            met="aggregator_considered" in resolved,
            detail=(
                f"probe 0 found an aggregator carrying this corpus ({names}) and the chosen "
                f"surface is '{chosen}'. Record WHY it was not preferred — freshness, "
                f"coverage, licence, or content quality are all legitimate answers; silence "
                f"is not.{extra}"
            ),
        ))

    if over_bulk_metadata and not justification:
        status = "blocked"
        reason = (
            f"chosen surface '{chosen}' is a scraper while probe 0 recommends "
            f"'{rec.surface}' ({rec.rank_class}) at {rec.host} ({rec.url}). Prefer the bulk "
            "surface, or record a justification in the manifest to override."
        )
    elif over_bulk_metadata:
        status = "overridden"
        reason = (
            f"scraper '{chosen}' chosen over recommended '{rec.surface}' at {rec.host} — "
            f"override justified: {justification}"
        )
    else:
        status = "allowed"
        if is_scraper:
            reason = (
                f"scraper '{chosen}' allowed: probe 0 found no metadata-bearing bulk surface "
                "to prefer (verify obligations below)."
            )
        else:
            reason = f"surface '{chosen}' allowed (not a scraper; no bulk surface superseded)."

    return GateDecision(
        chosen_surface=chosen,
        status=status,
        recommended=rec,
        obligations=obligations,
        justification=justification if over_bulk_metadata else None,
        reason=reason,
    )


# ============================================================================ session
#
# The onboarding-session state model (design/w6-onboarding-agent.md §"Onboarding session
# state"). Most onboarding state already has a home — the draft manifest is versioned via
# manifest.store(), probe artifacts are written under experiments/stage_b/probes/<src>/,
# fixtures under tests/fixtures/. What is *new* is the per-session honesty record: the
# intervention counter, the gate decisions — the surface gate above AND the admin
# verification gates at Phases 4/6/7 (design/w6-onboarding-agent.md §E, where a refusal is
# stored as a first-class outcome rather than as the absence of an approval) — and the cost
# actuals. This is the smallest thing that makes the Stage-B measurement (5/2/0 interventions,
# ≤5 pass bar) a live product surface instead of a hand-kept markdown log.
#
# Persistence: modelled here as a JSON-serialisable artifact (to_dict/from_dict). The design
# notes the eventual home is a Postgres `onboarding_sessions` row (ledger-is-ground-truth),
# but that needs a migration and ties to the still-open runtime-hosting question, so this
# slice keeps it a self-contained artifact — no schema commitment yet.

# Intervention taxonomy — verbatim from experiments/stage_b/intervention-log.md. A `GATE`
# entry (admin approval/redirect) is recorded for the honesty log but does NOT count against
# the budget; the other three do.
INTERVENTION_CATEGORIES = ("ENGINE-GAP", "DIAGNOSIS", "JUDGMENT", "GATE")
COUNTED_CATEGORIES = frozenset({"ENGINE-GAP", "DIAGNOSIS", "JUDGMENT"})
INTERVENTION_BUDGET = 5           # ≤5 non-gate interventions per source (Stage-B pass bar)
COST_PROJECTION_FACTOR = 2.0      # cost actual must be ≤ 2× the agent's projection

# Verification gates in the flow (onboarding-flow.md): Phase 4 (draft), 6 (results), 7 (prod).
VERIFICATION_GATE_PHASES = (4, 6, 7)

# The three admin answers at a verification gate (design/w6-onboarding-agent.md §E). All
# three are recorded identically; none is inferable from another's absence — a UI where
# "approve" is a button and "no" is the absence of a click records agreement and forgets
# refusal, which is the half worth keeping.
GATE_VERDICTS = ("approved", "changes_requested", "refused")


class OnboardingNotAcceptable(RuntimeError):
    """Raised by OnboardingSession.raise_if_not_acceptable() at Phase 7 when a
    session-owned acceptance condition (intervention budget, surface gate, cost) is unmet."""


@dataclass
class Intervention:
    """One step beyond the declared tools — an ENGINE-GAP/DIAGNOSIS/JUDGMENT, or a GATE
    admin decision (logged, not counted). See the Stage-B intervention log for the meaning
    of each category; a DIAGNOSIS is the expensive kind and is a battery-extension ticket."""

    category: str
    note: str
    phase: int | None = None

    def __post_init__(self) -> None:
        if self.category not in INTERVENTION_CATEGORIES:
            raise ValueError(
                f"unknown intervention category {self.category!r}; "
                f"one of {INTERVENTION_CATEGORIES}"
            )

    @property
    def counted(self) -> bool:
        return self.category in COUNTED_CATEGORIES

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "note": self.note, "phase": self.phase}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Intervention":
        return cls(category=d["category"], note=d.get("note", ""), phase=d.get("phase"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_id_from_url(url: str) -> str:
    """The default source id for an intake URL (flow Phase 0). Shared by `civic onboard`
    and the onboarding UI so both propose the same id for the same URL."""
    from urllib.parse import urlsplit

    host = urlsplit(url if "//" in url else f"https://{url}").netloc
    return host.replace(":", "_").replace(".", "_") or "unnamed_source"


@dataclass
class VerificationGate:
    """One admin decision point (flow Phases 4/6/7), as data.

    A gate is a request/verdict pair, both persisted. The agent files the request and
    stops; the admin answers `approved`, `changes_requested` or `refused` with a note.
    An unanswered request is `pending` — and a pending gate is an acceptance problem,
    because silence is not consent.
    """

    phase: int
    summary: str                     # the human-readable ask (Phase 4: "not YAML")
    detail: str = ""                 # the evidence behind it (plan, results, acceptance)
    requested_at: str = field(default_factory=_now)
    verdict: str | None = None
    note: str = ""
    admin: str = ""
    decided_at: str | None = None

    @property
    def pending(self) -> bool:
        return self.verdict is None

    @property
    def refused(self) -> bool:
        return self.verdict == "refused"

    def resolve(self, verdict: str, note: str, admin: str = "admin") -> "VerificationGate":
        if verdict not in GATE_VERDICTS:
            raise ValueError(f"unknown gate verdict {verdict!r}; one of {GATE_VERDICTS}")
        if not (note or "").strip():
            # A note is required on every verdict, including approval: the artifact must
            # say WHY the admin decided, or the record degrades to a click count.
            raise ValueError("a gate verdict needs a note — record the reason, not just the answer")
        self.verdict = verdict
        self.note = note
        self.admin = admin or "admin"
        self.decided_at = _now()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "summary": self.summary,
            "detail": self.detail,
            "requested_at": self.requested_at,
            "verdict": self.verdict,
            "pending": self.pending,
            "note": self.note,
            "admin": self.admin,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VerificationGate":
        return cls(
            phase=int(d["phase"]),
            summary=d.get("summary", ""),
            detail=d.get("detail", ""),
            requested_at=d.get("requested_at") or _now(),
            verdict=d.get("verdict"),
            note=d.get("note", ""),
            admin=d.get("admin", ""),
            decided_at=d.get("decided_at"),
        )


@dataclass
class OnboardingSession:
    """The resumable per-session record tying together the new state W6 owns."""

    source_id: str
    intent: str = ""
    phase: int = 0
    interventions: list[Intervention] = field(default_factory=list)
    gate: GateDecision | None = None            # the surface gate (probe-0 wiring)
    # The admin decision points (Phases 4/6/7), newest last. Distinct from `gate` above:
    # that one holds the agent to probe 0's ranking, these hold the *agent* to the admin.
    verification_gates: list[VerificationGate] = field(default_factory=list)
    cost_eur: float = 0.0
    cost_projected_eur: float | None = None
    # The *agent's own* token spend for this onboarding (USD, from the SDK's ResultMessage).
    # Deliberately separate from cost_eur: that is the crawl's engine spend, which the ≤2x
    # projection check is about. Mixing the two would corrupt that check.
    agent_cost_usd: float = 0.0

    # -- mutation ----------------------------------------------------------------------

    def log_intervention(self, category: str, note: str, phase: int | None = None) -> Intervention:
        """Record a step beyond the declared tools. Returns the entry so a caller (or the
        future SDK runtime) can surface it. A DIAGNOSIS should also open a battery ticket."""
        entry = Intervention(category, note, self.phase if phase is None else phase)
        self.interventions.append(entry)
        return entry

    def record_gate_decision(self, admin: str, note: str, phase: int | None = None) -> Intervention:
        """Record an admin gate approval/redirect (Phases 4/6/7) — logged, not counted."""
        return self.log_intervention("GATE", f"{admin}: {note}", phase)

    def set_surface_gate(self, decision: GateDecision) -> None:
        self.gate = decision

    def request_gate(self, phase: int, summary: str, detail: str = "") -> VerificationGate:
        """File a verification-gate request and park the session on it (flow Phases 4/6/7).

        Refuses to open a second gate while one is pending: two open questions with one
        answer field is how a refusal gets attached to the wrong decision.
        """
        phase = int(phase)
        if phase not in VERIFICATION_GATE_PHASES:
            raise ValueError(
                f"phase {phase} is not a verification gate; gates are {VERIFICATION_GATE_PHASES}"
            )
        if self.pending_gate is not None:
            raise ValueError(
                f"gate at phase {self.pending_gate.phase} is still awaiting a decision"
            )
        gate = VerificationGate(phase=phase, summary=summary, detail=detail)
        self.verification_gates.append(gate)
        self.phase = phase
        return gate

    def resolve_gate(self, verdict: str, note: str, admin: str = "admin") -> VerificationGate:
        """Record the admin's answer to the pending gate.

        Also logs a `GATE` intervention: the honesty log shows the decision, and the
        counter stays a measure of how much the *agent* needed hand-holding (an admin
        exercising a designed decision point is not hand-holding).
        """
        gate = self.pending_gate
        if gate is None:
            raise ValueError("no gate is awaiting a decision")
        gate.resolve(verdict, note, admin)
        self.record_gate_decision(gate.admin, f"phase {gate.phase} {verdict}: {note}", gate.phase)
        return gate

    def advance(self, phase: int) -> None:
        self.phase = phase

    def add_cost(self, eur: float) -> None:
        self.cost_eur += eur

    # -- derived -----------------------------------------------------------------------

    @property
    def counted_interventions(self) -> list[Intervention]:
        return [i for i in self.interventions if i.counted]

    @property
    def intervention_count(self) -> int:
        """The number that must stay ≤ INTERVENTION_BUDGET (GATE entries excluded)."""
        return len(self.counted_interventions)

    @property
    def within_intervention_budget(self) -> bool:
        return self.intervention_count <= INTERVENTION_BUDGET

    @property
    def pending_gate(self) -> VerificationGate | None:
        """The gate awaiting an admin decision, if any (at most one by construction)."""
        return next((g for g in self.verification_gates if g.pending), None)

    def latest_gate(self, phase: int) -> VerificationGate | None:
        """The most recent gate filed at `phase` — a later request supersedes an earlier
        verdict, so this is what acceptance reads."""
        return next((g for g in reversed(self.verification_gates) if g.phase == phase), None)

    @property
    def refused_gates(self) -> list[VerificationGate]:
        """Gates whose standing answer is a refusal (superseded refusals excluded)."""
        return [
            g for phase in VERIFICATION_GATE_PHASES
            if (g := self.latest_gate(phase)) is not None and g.refused
        ]

    @property
    def cost_within_projection(self) -> bool:
        if self.cost_projected_eur is None:
            return True
        return self.cost_eur <= COST_PROJECTION_FACTOR * self.cost_projected_eur

    def acceptance_problems(self) -> list[str]:
        """The session-owned half of Phase-7 acceptance (the engine owns the other half —
        reconciliation, budget). Empty ⇒ the session clears its own gates.

        Requires: intervention budget kept; the surface gate run AND resolved (blocked or an
        open obligation fails, mirroring an open TODO(probe)); cost within 2× of projection;
        and no verification gate left pending or standing refused. A session that has
        *reached* phase 7 additionally needs an approved phase-7 verdict — below that phase
        a missing verdict is "not there yet", not a failure.
        """
        problems: list[str] = []
        if (pending := self.pending_gate) is not None:
            problems.append(
                f"gate at phase {pending.phase} is awaiting an admin decision: {pending.summary}"
            )
        for g in self.refused_gates:
            problems.append(f"gate at phase {g.phase} refused by {g.admin}: {g.note}")
        if self.phase >= 7:
            final = self.latest_gate(7)
            if final is None or final.verdict != "approved":
                problems.append(
                    "phase 7 reached without an approved production gate — the admin has not "
                    "signed this source off"
                )
        if not self.within_intervention_budget:
            problems.append(
                f"intervention budget exceeded: {self.intervention_count} > {INTERVENTION_BUDGET}"
            )
        if self.gate is None:
            problems.append(
                "surface gate not run — probe 0 → surface_gate() is required before acceptance"
            )
        else:
            try:
                self.gate.raise_if_unresolved()
            except SurfaceGateUnresolved as e:
                problems.append(str(e))
        if not self.cost_within_projection:
            problems.append(
                f"cost {self.cost_eur:.4f} exceeds {COST_PROJECTION_FACTOR}× projection "
                f"{self.cost_projected_eur}"
            )
        return problems

    @property
    def acceptable(self) -> bool:
        return not self.acceptance_problems()

    def raise_if_not_acceptable(self) -> None:
        problems = self.acceptance_problems()
        if problems:
            raise OnboardingNotAcceptable(
                f"onboarding '{self.source_id}' cannot leave test mode: " + "; ".join(problems)
            )

    # -- persistence -------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "intent": self.intent,
            "phase": self.phase,
            "interventions": [i.to_dict() for i in self.interventions],
            "intervention_count": self.intervention_count,        # convenience for the UI
            "gate": self.gate.to_dict() if self.gate else None,
            "verification_gates": [g.to_dict() for g in self.verification_gates],
            "pending_gate": self.pending_gate.to_dict() if self.pending_gate else None,
            "cost_eur": self.cost_eur,
            "cost_projected_eur": self.cost_projected_eur,
            "agent_cost_usd": self.agent_cost_usd,
            "acceptable": self.acceptable,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OnboardingSession":
        return cls(
            source_id=d["source_id"],
            intent=d.get("intent", ""),
            phase=int(d.get("phase", 0)),
            interventions=[Intervention.from_dict(i) for i in d.get("interventions", [])],
            gate=GateDecision.from_dict(d["gate"]) if d.get("gate") else None,
            verification_gates=[
                VerificationGate.from_dict(g) for g in d.get("verification_gates", [])
            ],
            cost_eur=float(d.get("cost_eur", 0.0)),
            cost_projected_eur=d.get("cost_projected_eur"),
            agent_cost_usd=float(d.get("agent_cost_usd", 0.0)),
        )
