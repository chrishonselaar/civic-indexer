"""Exposure tiers — what may leave this system, per source.

The second axis of `design/access-posture.md`. Acquisition ("may we fetch") is expensive
to reverse; exposure ("what may leave") is cheap, because Postgres is ground truth and
OpenSearch is a rebuildable projection. Splitting them is what makes changing our mind
about a publisher a re-projection instead of a re-harvest.

    T0  metadata only        title/date/publisher + a link back. No text in the index at
                             all, no chunks, no vectors.
    T1  search + snippets    text is indexed and searchable; results carry highlights;
                             the full text is not served from our copy.
    T2  full text            the extracted text may be served from our copy.
    T3  derived artefacts    T2 + OCR output, chunk text and embeddings redistributable
                             through the API.

Enforced in two places, because the boundaries genuinely live in two places:

* **T0 is a projection boundary** — text that is never indexed cannot leak from any
  endpoint, present or future. `engine/project/projector.py`.
* **T1/T2/T3 are API boundaries** — the text is indexed (retrieval needs it) and the
  question is what a response may contain. `ui/search_api.py`.

Each projected document carries its own `exposure` value, so an endpoint can enforce the
tier without a database round-trip and a stale index cannot silently over-serve: a
document projected at T0 has no text to give even if the manifest later says T2.
"""

TIERS = ("T0", "T1", "T2", "T3")

# What an undeclared manifest gets. This is a STOPGAP and a permissive one: it preserves
# the behaviour every source had before 2026-07-27 rather than silently blanking four
# live projections. It is removed by W6.5 item 1's fail-closed `validate_manifest` rule,
# which makes `policy.exposure` required — at which point "undeclared" stops existing.
# Until then `civic project` warns, because a permissive default that nobody sees is the
# rule-5 failure mode wearing a different hat.
UNDECLARED_DEFAULT = "T2"


def rank(tier: str | None) -> int:
    """Position in the ordering; unknown/absent values sort as the default's rank."""
    if tier not in TIERS:
        tier = UNDECLARED_DEFAULT
    return TIERS.index(tier)


def at_least(tier: str | None, minimum: str) -> bool:
    return rank(tier) >= rank(minimum)


def indexes_text(tier: str | None) -> bool:
    """T0 keeps text out of the index entirely — the only tier that is a projection-time
    decision rather than a response-time one."""
    return at_least(tier, "T1")


def serves_full_text(tier: str | None) -> bool:
    return at_least(tier, "T2")


def serves_derived_artefacts(tier: str | None) -> bool:
    """Chunk text and embeddings: things we produced from someone else's bytes, at our
    cost. Redistributing them is a different act from linking to the source, so it is a
    tier of its own rather than something T2 inherits."""
    return at_least(tier, "T3")


# --------------------------------------------------------------------- processors
#
# A third axis in practice, kept here because it answers the same question: what may
# leave the system. Sending document text to a third-party API is not publication, but it
# is transmission, and the tiers above do not govern it.
#
# Measured 2026-07-27 from Voyage AI's own privacy policy — the reason this defaults to
# nothing rather than to "the reranker we already use":
#   "unless you 'opt out' … you grant Voyage AI … a worldwide, irrevocable, perpetual,
#    royalty-free … right and license to use, copy, reproduce, distribute, prepare
#    derivative works of … the Customer Content … to train, improve, and otherwise
#    further develop the Service."
# The policy names no retention period, no GDPR basis, no controller/processor role, no
# SCC/DPF transfer mechanism, and no sub-processor list; processing is US-based. The
# opt-out is on their website and applies only to content submitted AFTER it is set.
#
# So a processor is opt-in per source, declared as `policy.processors`. An undeclared
# source sends nothing to anyone.

PROCESSORS = ("voyage_rerank",)


def processors_of_manifest(m: dict) -> list[str]:
    declared = ((m or {}).get("policy") or {}).get("processors") or []
    return [p for p in declared if p in PROCESSORS]


def permits_processor(doc: dict, processor: str) -> bool:
    """Read the permission off the projected document, same as the tier.

    Per-document rather than per-index because the thing being transmitted is the
    document's own text; a pool that mixes sources must be governed by its strictest
    member, and that falls out of checking each one.
    """
    return processor in (doc.get("processors") or [])


def of_manifest(m: dict) -> str | None:
    """Declared tier, or None. Deliberately does not substitute the default: callers that
    need to warn about an undeclared tier have to be able to tell the difference."""
    tier = ((m or {}).get("policy") or {}).get("exposure")
    return tier if tier in TIERS else None
