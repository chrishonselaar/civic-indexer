"""The verification-gate model (design/w6-onboarding-agent.md §E).

The property under test throughout: **a refusal is as first-class as an approval.** It is
stored, it is legible after a restart, and it blocks acceptance — the failure mode being
guarded against is a UI where "approve" is a button and "no" is the absence of a click.
"""

import pytest

from engine import onboarding


def _session(**kw) -> onboarding.OnboardingSession:
    return onboarding.OnboardingSession(source_id="test_src", **kw)


def _cleared(s: onboarding.OnboardingSession) -> None:
    """Give a session a clean surface gate, so gate assertions are not masked by it."""
    report = _report()
    s.set_surface_gate(onboarding.surface_gate(report, "sru"))


def _report():
    from engine import probe

    return probe.BulkProbeReport(
        hosts=["example.org"],
        surfaces=[probe.SurfaceProbe(surface="sru", host="example.org",
                                     url="https://example.org/sru", status="absent")],
    )


# ------------------------------------------------------------------ request/verdict

def test_a_gate_is_requested_then_answered():
    s = _session()
    gate = s.request_gate(4, "plan for test_src", "the evidence")
    assert gate.pending and s.pending_gate is gate
    assert s.phase == 4                        # the session parks on the gate's phase
    s.resolve_gate("approved", "plan looks right", admin="chris")
    assert s.pending_gate is None
    assert gate.verdict == "approved" and gate.admin == "chris" and gate.decided_at


def test_a_pending_gate_blocks_acceptance_because_silence_is_not_consent():
    s = _session()
    _cleared(s)
    assert s.acceptable
    s.request_gate(4, "plan for test_src")
    assert not s.acceptable
    assert "awaiting an admin decision" in "; ".join(s.acceptance_problems())


def test_a_refusal_blocks_acceptance_and_says_who_and_why():
    s = _session()
    _cleared(s)
    s.request_gate(6, "test-run results")
    s.resolve_gate("refused", "extraction is wrong on half the sample", admin="chris")
    problems = "; ".join(s.acceptance_problems())
    assert "refused by chris" in problems
    assert "extraction is wrong" in problems
    with pytest.raises(onboarding.OnboardingNotAcceptable):
        s.raise_if_not_acceptable()


def test_changes_requested_leaves_the_gate_answered_but_the_source_unapproved():
    s = _session()
    _cleared(s)
    s.request_gate(4, "plan v1")
    s.resolve_gate("changes_requested", "use the SRU surface, not the scraper")
    assert s.pending_gate is None               # answered ...
    assert s.acceptable                         # ... and not itself a blocker at phase 4
    assert s.latest_gate(4).verdict == "changes_requested"


def test_a_later_request_supersedes_an_earlier_refusal():
    s = _session()
    _cleared(s)
    s.request_gate(6, "results v1")
    s.resolve_gate("refused", "no")
    assert not s.acceptable
    s.request_gate(6, "results v2, extraction fixed")
    s.resolve_gate("approved", "fixed; sample reads correctly now")
    assert s.acceptable, s.acceptance_problems()


def test_reaching_phase_7_without_an_approval_is_a_problem_not_a_pass():
    s = _session()
    _cleared(s)
    s.advance(7)
    assert "without an approved production gate" in "; ".join(s.acceptance_problems())
    s.request_gate(7, "take it to production")
    s.resolve_gate("approved", "acceptance evidence checks out")
    assert s.acceptable, s.acceptance_problems()


def test_a_session_below_phase_7_is_not_penalised_for_having_no_gates():
    """The pre-gate acceptance semantics must not change: no gates == "not there yet"."""
    s = _session()
    _cleared(s)
    assert s.acceptable and s.verification_gates == []


# ------------------------------------------------------------------ guard rails

def test_every_verdict_needs_a_note():
    s = _session()
    s.request_gate(4, "plan")
    with pytest.raises(ValueError, match="needs a note"):
        s.resolve_gate("approved", "   ")


def test_unknown_verdict_is_rejected():
    s = _session()
    s.request_gate(4, "plan")
    with pytest.raises(ValueError, match="unknown gate verdict"):
        s.resolve_gate("ok", "sure")


def test_only_the_flows_own_phases_are_gates():
    s = _session()
    with pytest.raises(ValueError, match="not a verification gate"):
        s.request_gate(5, "mid-run check")


def test_a_second_gate_cannot_open_while_one_is_pending():
    """Two open questions with one answer field is how a refusal lands on the wrong one."""
    s = _session()
    s.request_gate(4, "plan")
    with pytest.raises(ValueError, match="awaiting a decision"):
        s.request_gate(6, "results")


def test_resolving_with_no_pending_gate_is_an_error_not_a_silent_no_op():
    s = _session()
    with pytest.raises(ValueError, match="no gate is awaiting"):
        s.resolve_gate("approved", "fine")


# ------------------------------------------------------------------ persistence

def test_gates_survive_the_round_trip_including_the_refusal():
    s = _session(intent="council decisions findable by residents")
    _cleared(s)
    s.request_gate(4, "plan", "detail")
    s.resolve_gate("approved", "go ahead")
    s.request_gate(6, "results")
    s.resolve_gate("refused", "dates extract as 'Aanbeveling'", admin="chris")

    back = onboarding.OnboardingSession.from_dict(s.to_dict())
    assert [g.verdict for g in back.verification_gates] == ["approved", "refused"]
    assert back.latest_gate(6).note == "dates extract as 'Aanbeveling'"
    assert not back.acceptable
    assert "refused by chris" in "; ".join(back.acceptance_problems())


def test_the_verdict_is_logged_as_a_gate_intervention_and_does_not_count():
    """An admin exercising a designed decision point is not hand-holding the agent."""
    s = _session()
    s.request_gate(4, "plan")
    s.resolve_gate("approved", "go")
    assert s.intervention_count == 0
    assert [i.category for i in s.interventions] == ["GATE"]
    assert "approved" in s.interventions[0].note
