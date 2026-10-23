"""probe — Probe 0: the fail-closed bulk-surface probe (design/bulk-endpoint-discovery.md).

Stage-B onboarding ranked discovery surfaces best-first in *prose* — "bulk/feed →
structured API (SRU/CKAN/JSON) → paged search → grid → link-walk → headless" — but the
engine had no runtime step that actively *tested* for a bulk surface before a scraper was
chosen. That gap cost tuchtrecht ~13 h of 1-rps scraping when repository.overheid.nl/sru
would have served the whole corpus in <1 min: SRU was not rejected, it was **missed**,
because nothing probed for it. Probe 0 closes that: BEFORE the surface ranking decides,
it actively hits each provider host and records what answers.

Two design principles are baked in:

1. **Negative knowledge is kept.** Each surface is recorded as `found`, `absent`
   (verified-absent — a positive fact), `error` (could not determine), or `manual`
   (not automatable here). "We checked and there is no OAI endpoint" is knowledge, not a
   blank.

2. **Fail-closed.** If any surface could not be determined (`error`), the verdict is
   `incomplete` and `enforceable` is False. A caller (the future W6 onboarding harness)
   MUST call `report.raise_if_unenforced()` before falling back to a scraper — an
   incomplete probe must never be read as "no bulk endpoint exists".

Ranking refinement (a finding from building this — see the design doc). The prose ranking
put "bulk/feed" above "structured API", but a bare **sitemap/robots** carries only URLs —
no metadata, no body — so it still needs a per-URL fetch, whereas **SRU/OAI/CKAN** carry
rich metadata inline (the two-tier "searchable now" tier). So the recommendation ranks
metadata-bearing bulk (`bulk_metadata`, rank 1) ABOVE a bare URL index (`bulk_index`,
rank 2). This matches the tuchtrecht lesson: SRU, not the sitemap, was the win.

Everything network here goes through an injectable `http_get` returning
`{"status": int, "text": str}` so the whole battery record/replays against fixtures
exactly like the discovery strategies — no live host touched under test.

Policy flags (POL-1, added 2026-07-25). Probe 0 already fetched robots.txt, but read only
its `Sitemap:` lines — so four sources shipped against hosts serving `Disallow: /` with
nothing anywhere recording it. `probe_robots` now parses the full file (RFC 9309 group
selection, `Disallow`/`Allow`/`Crawl-delay`) per host and the report carries a
`policy_flags` section. This is deliberately **recording, not gating**: the report's
`enforceable`/`verdict` are unchanged by a Disallow, because the posture is an admin gate
decision (`policy.admin_decision` in the manifest), never one the engine or the agent
resolves. The fail-closed half — `validate_manifest` refusing test-mode exit when a
harvested host disallows and no `policy` block is declared — lands separately, after that
decision.

Aggregators (SURF-3, added 2026-07-25). Everything above is HOST-scoped, so a third-party
aggregator republishing the same corpus is structurally invisible — probe 0 answered
`no_bulk_surface` for Vlaardingen while Open Raadsinformatie carried the whole corpus with
full text. `probe_aggregators` adds a CORPUS-scoped step driven by
`registries/aggregators.yaml`, matched on corpus type + organisation rather than provider
host, with a new top rank class `bulk_content` (metadata + full text + bytes) above
`bulk_metadata`. Where a hit republishes a disallowing host, `policy_flags.
disallow_avoidable_via` says so: the POL-1 question for that host SHRINKS — it does not
disappear. Read that flag as "how much of this host's traffic an aggregator can carry",
never as "this host need not be contacted": measured on Vlaardingen (TOT-5, 2026-07-25) ORI
carries ~85% of the corpus and none of it before 2014, so the residue is a pre-2014 backfill
plus a recent-window poll. Design + live measurements: design/aggregator-registry.md.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests
import yaml

from engine import config

HEADERS = {
    "User-Agent": config.PROBE_USER_AGENT,
    "Accept": "*/*",
}

# http_get(url, params) -> {"status": int, "text": str}. Unlike the discovery strategies'
# http_get, probe 0 needs the status code: an HTTP 404/410 is how a surface is proven ABSENT.
HttpGet = Callable[[str, dict[str, Any]], dict]

# Best-first rank classes (lower = preferred). See the module docstring for why a bare
# URL index (sitemap) ranks below metadata-bearing bulk (SRU/OAI/CKAN), and why an
# aggregator carrying full text + bytes (`bulk_content`) ranks above both — it is the case
# where the two-tier content model collapses into one tier because someone else already
# paid for tier 2 (design/aggregator-registry.md §3).
RANK = {"bulk_content": 0, "bulk_metadata": 1, "bulk_index": 2}

# Conventional well-known paths per surface. Probe 0 tests these on each candidate host;
# a host that answers on a non-standard path is out of scope for the automated pass and is
# handled by the `frontend_xhr` manual boundary below.
SRU_PATH = "/sru"
OAI_PATH = "/oai"
SITEMAP_PATH = "/sitemap.xml"
ROBOTS_PATH = "/robots.txt"
CKAN_PATH = "/api/3/action/package_search"

# The KOOP/overheid family serves its bulk surfaces from a DIFFERENT host than the public
# site (repository.overheid.nl vs <x>.overheid.nl) — the exact reason tuchtrecht's SRU
# endpoint was missed. `candidate_hosts` always adds it for an *.overheid.nl source.
KOOP_BULK_HOST = "https://repository.overheid.nl"

# The product token we match robots.txt groups against, per RFC 9309 §2.2.1. Must stay in
# sync with the product token in HEADERS above.
ROBOTS_UA_TOKEN = "civic-indexer"

# The curated aggregator registry (SURF-3). Data, not code — see the file's own header.
AGGREGATOR_REGISTRY_PATH = "registries/aggregators.yaml"


@dataclass
class RobotsPolicy:
    """What one host's robots.txt says to *us* — recorded whether permissive or not.

    `status` mirrors the surface vocabulary: `found` (directives parsed), `absent`
    (verified: no robots.txt, or it carries no directives — a positive fact meaning
    nothing is disallowed), `error` (could not determine — do not read as permission).
    """

    host: str
    url: str
    status: str                                     # found | absent | error
    http_status: int | None = None
    user_agent_group: str | None = None             # the group that applied: our token or "*"
    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)
    note: str = ""
    error: str | None = None                        # transport failure message, if any

    @property
    def blanket_disallow(self) -> bool:
        """The whole host is off-limits to our product token."""
        return "/" in self.disallow

    @property
    def summary(self) -> str:
        """One line fit to paste into a manifest `policy.robots` entry."""
        if self.status != "found":
            return f"{self.status} ({self.note or self.url})"
        ua = f"User-agent: {self.user_agent_group}"
        rules = " / ".join(f"Disallow: {d}" for d in self.disallow) or "Disallow: (none)"
        if self.crawl_delay is not None:
            rules += f" / Crawl-delay: {self.crawl_delay:g}"
        return f"{ua} / {rules}"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RobotsPolicy":
        return cls(
            host=d["host"], url=d["url"], status=d["status"],
            http_status=d.get("http_status"), user_agent_group=d.get("user_agent_group"),
            disallow=list(d.get("disallow") or []), allow=list(d.get("allow") or []),
            crawl_delay=d.get("crawl_delay"), sitemaps=list(d.get("sitemaps") or []),
            note=d.get("note", ""), error=d.get("error"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host, "url": self.url, "status": self.status,
            "http_status": self.http_status, "user_agent_group": self.user_agent_group,
            "disallow": self.disallow, "allow": self.allow,
            "crawl_delay": self.crawl_delay, "sitemaps": self.sitemaps,
            "blanket_disallow": self.blanket_disallow, "summary": self.summary,
            "note": self.note, "error": self.error,
        }


@dataclass
class SurfaceProbe:
    """The outcome of testing one surface on one host — found OR verified-absent."""

    surface: str                       # sru | oai_pmh | sitemap | ckan | frontend_xhr
    host: str
    url: str
    status: str                        # found | absent | error | manual
    http_status: int | None = None
    rank_class: str | None = None      # set only when found (see RANK)
    evidence: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def rank(self) -> int:
        return RANK.get(self.rank_class or "", 99)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SurfaceProbe":
        return cls(
            surface=d["surface"], host=d["host"], url=d["url"], status=d["status"],
            http_status=d.get("http_status"), rank_class=d.get("rank_class"),
            evidence=dict(d.get("evidence") or {}), note=d.get("note", ""),
        )


@dataclass
class BulkProbeReport:
    hosts: list[str]
    surfaces: list[SurfaceProbe]
    policies: list[RobotsPolicy] = field(default_factory=list)

    @property
    def found(self) -> list[SurfaceProbe]:
        return [s for s in self.surfaces if s.status == "found"]

    @property
    def errored(self) -> list[SurfaceProbe]:
        return [s for s in self.surfaces if s.status == "error"]

    @property
    def recommended(self) -> SurfaceProbe | None:
        """Best-first: the found surface with the lowest rank (metadata-bulk beats index)."""
        return min(self.found, key=lambda s: s.rank) if self.found else None

    @property
    def enforceable(self) -> bool:
        """False when the battery could not fully determine the surfaces (fail-closed).

        Deliberately NOT affected by `policy_flags`: a Disallow is recorded for the admin
        gate, it does not make the *surface* evidence incomplete. See the module docstring.
        """
        return not self.errored

    @property
    def disallowing_hosts(self) -> list[str]:
        """Hosts whose robots.txt forbids our product token outright (POL-1)."""
        return [p.host for p in self.policies if p.blanket_disallow]

    @property
    def aggregator_hits(self) -> list[SurfaceProbe]:
        """Registry entries that answered with a corpus (SURF-3)."""
        return [s for s in self.found if s.rank_class == "bulk_content"]

    @property
    def disallow_avoidable_via(self) -> dict[str, list[str]]:
        """Disallowing host -> aggregators that republish its corpus.

        The link between SURF-3 and POL-1: when an aggregator carries the same corpus, most
        of the posture question for that host can be avoided rather than answered.

        **Not all of it, and the name of this flag overstates it** (kept for continuity —
        the correction belongs in the reading, not in a rename). An aggregator republishes
        *its own harvest*: measured on Vlaardingen 2026-07-25 (TOT-5), ORI covers 2014-2026
        at 95.6-100% but 2008-2013 at **0%**, and lags the recent window. What is avoidable
        is therefore the bulk of the BACKFILL, not the source. Compare `oldest_item` in the
        hit's evidence against the source's declared range before reading this as coverage.

        Still not a decision the engine takes; it is evidence handed to the admin gate.
        """
        out: dict[str, list[str]] = {}
        for host in self.disallowing_hosts:
            bare = urlsplit(host).netloc or host
            for hit in self.aggregator_hits:
                if any(bare == h or (h.startswith("*.") and bare.endswith(h[1:]))
                       for h in hit.evidence.get("republishes_hosts", [])):
                    out.setdefault(host, []).append(hit.evidence.get("aggregator", hit.surface))
        return out

    @property
    def policy_flags(self) -> dict[str, Any]:
        """Per-host robots posture — recorded for the admin gate, never auto-resolved."""
        return {
            "robots_checked": [p.host for p in self.policies],
            "blanket_disallow": self.disallowing_hosts,
            "crawl_delay": {
                p.host: p.crawl_delay for p in self.policies if p.crawl_delay is not None
            },
            "undetermined": [p.host for p in self.policies if p.status == "error"],
            "requires_admin_decision": bool(self.disallowing_hosts),
            "disallow_avoidable_via": self.disallow_avoidable_via,
            "hosts": [p.to_dict() for p in self.policies],
        }

    @property
    def verdict(self) -> str:
        if self.errored:
            return "incomplete"           # fail closed — do NOT read as "no bulk surface"
        if self.found:
            return "bulk_surface_available"
        return "no_bulk_surface"          # verified-absent everywhere probed (clean)

    def raise_if_unenforced(self) -> None:
        """The fail-closed gate: a caller MUST call this before choosing a scraper.

        Raises when the probe could not conclusively rule a bulk surface in or out, so an
        incomplete battery can never silently justify a fragile scrape.
        """
        if not self.enforceable:
            errs = ", ".join(f"{s.surface}@{s.host}" for s in self.errored)
            raise BulkProbeIncomplete(
                f"probe 0 incomplete — undetermined surfaces: {errs}. "
                "Refusing to rank discovery surfaces on partial evidence."
            )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BulkProbeReport":
        """Rebuild a report from a persisted to_dict() artifact (recommended/verdict are
        derived, so only hosts + surfaces round-trip)."""
        return cls(
            hosts=list(d.get("hosts", [])),
            surfaces=[SurfaceProbe.from_dict(s) for s in d.get("surfaces", [])],
            policies=[
                RobotsPolicy.from_dict(p)
                for p in (d.get("policy_flags") or {}).get("hosts", [])
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        rec = self.recommended
        return {
            "hosts": self.hosts,
            "verdict": self.verdict,
            "enforceable": self.enforceable,
            "policy_flags": self.policy_flags,
            "recommended": (
                {"surface": rec.surface, "host": rec.host, "url": rec.url,
                 "rank_class": rec.rank_class, "evidence": rec.evidence}
                if rec else None
            ),
            "surfaces": [
                {
                    "surface": s.surface, "host": s.host, "url": s.url,
                    "status": s.status, "http_status": s.http_status,
                    "rank_class": s.rank_class, "evidence": s.evidence, "note": s.note,
                }
                for s in self.surfaces
            ],
        }


class BulkProbeIncomplete(RuntimeError):
    """Raised by raise_if_unenforced() when probe 0 could not fully determine surfaces."""


# --------------------------------------------------------------------------- http


def _real_http_get(url: str, params: dict[str, Any]) -> dict:
    resp = requests.get(
        url, params=params, headers=HEADERS, timeout=30, allow_redirects=True
    )
    return {"status": resp.status_code, "text": resp.text}


def _norm_host(host: str) -> str:
    """Return scheme://netloc, defaulting to https and dropping any path/query."""
    h = host.strip()
    if "://" not in h:
        h = "https://" + h
    parts = urlsplit(h)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _url(host: str, path: str) -> str:
    return host.rstrip("/") + path


# --------------------------------------------------------------------------- surfaces
#
# Each surface prober is total: it always returns a SurfaceProbe. A transport exception or
# an ambiguous 5xx becomes status="error" (→ fail-closed), a clean 404/410 or a well-formed
# "not this protocol" body becomes status="absent" (verified knowledge), and a positive
# protocol signal becomes status="found" with the numbers a ranking needs.


def _fetch(http_get: HttpGet, url: str, params: dict[str, Any]) -> tuple[int | None, str, str | None]:
    """(http_status, text, error) — error is a message string when the call could not run."""
    try:
        r = http_get(url, params)
        return r.get("status"), r.get("text", ""), None
    except Exception as e:  # transport failure — undetermined, fail closed
        return None, "", f"{type(e).__name__}: {e}"


def _absent_or_error(surface: str, host: str, url: str, code: int | None, err: str | None) -> SurfaceProbe:
    """Map a non-positive result to absent (clean 4xx) vs error (transport/5xx/unknown)."""
    if err is not None:
        return SurfaceProbe(surface, host, url, "error", code, note=err)
    if code is not None and code in (404, 410):
        return SurfaceProbe(surface, host, url, "absent", code, note=f"HTTP {code}")
    if code is not None and 500 <= code < 600:
        return SurfaceProbe(surface, host, url, "error", code, note=f"HTTP {code} (server error — undetermined)")
    # 200/3xx but no protocol signal, or a 4xx other than 404/410: the surface answered but
    # is not this protocol. Verified-absent.
    return SurfaceProbe(surface, host, url, "absent", code, note="no protocol signal")


def probe_sru(host: str, http_get: HttpGet) -> SurfaceProbe:
    url = _url(host, SRU_PATH)
    params = {"operation": "explain", "version": "2.0"}
    code, text, err = _fetch(http_get, url, params)
    if err is None and code == 200 and re.search(r"<[\w:]*explainResponse", text):
        ev: dict[str, Any] = {"protocol": "SRU", "explain": True}
        # Page cap appears either as an element (<...maximumRecords>N) or, in the SRU 2.0
        # explain configInfo, as an attribute (<setting type="maximumRecords">N).
        m = re.search(r"maximumRecords[\"']?[^>]*>\s*(\d+)", text)
        if m:
            ev["maximum_records"] = int(m.group(1))
        # A cheap searchRetrieve for the claimed corpus size is a useful ranking signal but
        # needs a source-specific CQL query; probe 0 stops at "SRU is here + its page cap".
        return SurfaceProbe("sru", host, url, "found", code, "bulk_metadata", ev,
                            "SRU explainResponse present")
    return _absent_or_error("sru", host, url, code, err)


def probe_oai(host: str, http_get: HttpGet) -> SurfaceProbe:
    url = _url(host, OAI_PATH)
    code, text, err = _fetch(http_get, url, {"verb": "Identify"})
    if err is None and code == 200 and "<Identify>" in text:
        ev: dict[str, Any] = {"protocol": "OAI-PMH"}
        name = re.search(r"<repositoryName>(.*?)</repositoryName>", text, re.S)
        if name:
            ev["repository_name"] = name.group(1).strip()
        gran = re.search(r"<granularity>(.*?)</granularity>", text, re.S)
        if gran:
            ev["granularity"] = gran.group(1).strip()
        # metadataPrefix richness decides DC-only vs a fuller schema (design §OAI probe).
        fmt_code, fmt_text, fmt_err = _fetch(http_get, url, {"verb": "ListMetadataFormats"})
        if fmt_err is None and fmt_code == 200:
            prefixes = re.findall(r"<metadataPrefix>(.*?)</metadataPrefix>", fmt_text)
            if prefixes:
                ev["metadata_prefixes"] = prefixes
        return SurfaceProbe("oai_pmh", host, url, "found", code, "bulk_metadata", ev,
                            "OAI-PMH Identify present")
    return _absent_or_error("oai_pmh", host, url, code, err)


def probe_ckan(host: str, http_get: HttpGet) -> SurfaceProbe:
    url = _url(host, CKAN_PATH)
    code, text, err = _fetch(http_get, url, {"rows": 0})
    if err is None and code == 200:
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and doc.get("success") and isinstance(doc.get("result"), dict):
            count = doc["result"].get("count")
            return SurfaceProbe("ckan", host, url, "found", code, "bulk_metadata",
                                {"protocol": "CKAN", "dataset_count": count},
                                "CKAN package_search answered")
    return _absent_or_error("ckan", host, url, code, err)


def parse_robots(text: str, ua_token: str = ROBOTS_UA_TOKEN) -> dict[str, Any]:
    """Parse robots.txt and return the rules that apply to `ua_token` (RFC 9309 §2.2.1).

    Group selection is the part a naive `grep Disallow` gets wrong, and it matters on real
    hosts: open.overheid.nl carries a permissive `User-agent: Googlebot` group with Allow
    lines directly ABOVE the `User-agent: *` group that says `Disallow: /`. Grepping sees
    the Allows; we are not Googlebot. So: consecutive user-agent lines form one group,
    an exact match on our product token wins outright, otherwise `*` applies, and every
    group with the winning key is merged. `Sitemap:` is file-scoped, never per-group.
    """
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    sitemaps: list[str] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name, value = name.strip().lower(), value.strip()
        if name == "user-agent":
            if rules:                       # a rule line closed the previous group
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif name == "sitemap":
            sitemaps.append(value)
        elif name in ("disallow", "allow", "crawl-delay") and agents:
            rules.append((name, value))
    if agents:
        groups.append((agents, rules))

    applied: str | None = None
    merged: list[tuple[str, str]] = []
    for key in (ua_token.lower(), "*"):
        matching = [r for ag, r in groups if key in ag]
        if matching:
            applied = key
            merged = [rule for r in matching for rule in r]
            break

    delays = [v for n, v in merged if n == "crawl-delay"]
    crawl_delay: float | None = None
    for v in reversed(delays):              # last parseable value wins
        try:
            crawl_delay = float(v)
            break
        except ValueError:
            continue

    return {
        "user_agent_group": applied,
        # An empty `Disallow:` value means "nothing is disallowed" — it is not a rule, so
        # it is dropped here rather than recorded as a path.
        "disallow": [v for n, v in merged if n == "disallow" and v],
        "allow": [v for n, v in merged if n == "allow" and v],
        "crawl_delay": crawl_delay,
        "sitemaps": sitemaps,
    }


def probe_robots(host: str, http_get: HttpGet) -> RobotsPolicy:
    """Fetch and parse one host's robots.txt (POL-1). Recorded, never enforced here."""
    url = _url(host, ROBOTS_PATH)
    code, text, err = _fetch(http_get, url, {})
    if err is not None:
        return RobotsPolicy(host, url, "error", code, note=err, error=err)
    if code in (404, 410):
        return RobotsPolicy(host, url, "absent", code,
                            note=f"HTTP {code} — no robots.txt (nothing disallowed)")
    if code != 200:
        # A 5xx/403 on robots.txt is NOT permission. Undetermined stays undetermined.
        return RobotsPolicy(host, url, "error", code,
                            note=f"HTTP {code} — robots directives undetermined")

    p = parse_robots(text)
    if p["user_agent_group"] is None and not p["sitemaps"]:
        return RobotsPolicy(host, url, "absent", code, sitemaps=[],
                            note="HTTP 200 but no applicable robots directives parsed")
    policy = RobotsPolicy(
        host, url, "found", code,
        user_agent_group=p["user_agent_group"], disallow=p["disallow"],
        allow=p["allow"], crawl_delay=p["crawl_delay"], sitemaps=p["sitemaps"],
    )
    if policy.blanket_disallow:
        policy.note = (
            "blanket Disallow: / for our product token — harvesting this host is an ADMIN "
            "GATE decision (manifest policy.admin_decision), not an engine or agent one"
        )
    return policy


def probe_sitemap(host: str, http_get: HttpGet, policy: RobotsPolicy | None = None) -> SurfaceProbe:
    """robots.txt Sitemap: directive OR a /sitemap.xml urlset/sitemapindex.

    A sitemap is a discovery aid only (URLs, no metadata) — hence rank_class bulk_index,
    below the metadata-bearing surfaces.

    `policy` is the host's already-fetched robots.txt (probe_bulk_surfaces passes it so
    the file is fetched once per host, not twice); it is re-fetched when omitted.
    """
    # 1) robots.txt Sitemap: lines are the canonical advertisement.
    policy = policy or probe_robots(host, http_get)
    if policy.sitemaps:
        return SurfaceProbe("sitemap", host, policy.url, "found", policy.http_status,
                            "bulk_index",
                            {"via": "robots.txt", "sitemaps": policy.sitemaps},
                            f"{len(policy.sitemaps)} Sitemap: directive(s) in robots.txt")

    # 2) conventional /sitemap.xml.
    sm_url = _url(host, SITEMAP_PATH)
    code2, text2, err2 = _fetch(http_get, sm_url, {})
    if err2 is None and code2 == 200 and re.search(r"<(?:urlset|sitemapindex)\b", text2):
        kind = "sitemapindex" if "<sitemapindex" in text2 else "urlset"
        urls = len(re.findall(r"<loc>", text2))
        return SurfaceProbe("sitemap", host, sm_url, "found", code2, "bulk_index",
                            {"via": "/sitemap.xml", "kind": kind, "loc_count": urls},
                            f"{kind} with {urls} <loc> entries")

    # Neither surfaced. Prefer reporting the robots probe (the canonical advertisement);
    # only escalate to error if BOTH could not be determined.
    if policy.error is not None and err2 is not None:
        return SurfaceProbe("sitemap", host, policy.url, "error", policy.http_status,
                            note=policy.error)
    return _absent_or_error("sitemap", host, sm_url, code2, err2)


def probe_frontend_xhr(host: str, http_get: HttpGet) -> SurfaceProbe:
    """The site's own fetch/XHR JSON API — a known strong signal (private API outranks
    scraping) that CANNOT be found without executing the page's JS / reading a HAR. Probe 0
    records this as an explicit `manual` boundary rather than silently omitting it, so the
    W6 agent knows a DevTools/HAR pass is still owed. This is honest negative knowledge: the
    surface is *not verified absent*, only *not automatable here*.
    """
    return SurfaceProbe(
        "frontend_xhr", host, host, "manual", None,
        note="requires headless/DevTools HAR capture — not automatable in probe 0; "
             "W6 agent must inspect the site's own XHR calls (private JSON API outranks scraping).",
    )


# --------------------------------------------------------------------------- aggregators
#
# SURF-3. Everything above this line is HOST-scoped: it asks "what does this provider's host
# serve?". That is structurally blind to a third-party aggregator republishing the same
# corpus, so `no_bulk_surface` comes back correct about the hosts probed and wrong in
# substance — which is exactly what happened on the Vlaardingen re-onboard.
#
# The step below is CORPUS-scoped: it matches a curated registry on corpus type + the
# source's declared organisation, deliberately NOT on the provider host. Design and the
# live measurements: design/aggregator-registry.md.


@dataclass
class AggregatorHints:
    """What the registry matches on. Corpus identity, never provider identity.

    `provider_hosts` is carried only so a hit can say "you were about to harvest X; this
    aggregator republishes X's corpus" — it is never the lookup key.
    """

    corpus_type: str | None = None
    organisation: str | None = None
    provider_hosts: list[str] = field(default_factory=list)

    @property
    def sufficient(self) -> bool:
        return bool(self.corpus_type and self.organisation)

    @property
    def slug(self) -> str:
        return _slug(self.organisation or "")


def _slug(name: str) -> str:
    """Organisation name -> index slug ('Den Haag' -> 'den_haag')."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", s)).strip("_")


def load_aggregator_registry(path: str | None = None) -> list[dict[str, Any]]:
    """Load the curated registry. A missing file is empty, not an error — the aggregator
    step is additive, and no deployment should fail closed merely for lacking one."""
    p = Path(path or AGGREGATOR_REGISTRY_PATH)
    if not p.exists():
        return []
    return list(yaml.safe_load(p.read_text(encoding="utf-8")) or [])


def _es_search(http_get: HttpGet, base_url: str, index: str, body: dict[str, Any]):
    """One Elasticsearch read, expressed as a GET so the injectable HttpGet contract holds.

    ES accepts a query body via `source` + `source_content_type` (verified live against
    ORI). Two traps this deliberately avoids, both of which would produce a WRONG probe
    result rather than a loud failure:
      - `_count` with a request body returns non-JSON through the ORI proxy — a probe that
        read that as "no data" would record a false absent. Use `_search` with size 0 and
        `track_total_hits` instead.
      - `_cat/indices` doc counts are NOT document counts (they include nested docs:
        ori_haarlem reports 903,693 there vs 112,792 searched). Never rank on `_cat`.
    """
    url = f"{base_url.rstrip('/')}/{index}/_search"
    params = {"source": json.dumps(body), "source_content_type": "application/json"}
    code, text, err = _fetch(http_get, url, params)
    if err is not None:
        return None, None, err
    try:
        return code, json.loads(text), None
    except ValueError:
        return code, None, f"HTTP {code} but response was not JSON"


def probe_aggregator(entry: dict[str, Any], hints: AggregatorHints,
                     http_get: HttpGet) -> SurfaceProbe:
    """Test ONE registry entry against one source's corpus hints."""
    agg_id = entry.get("id", "?")
    surface = f"aggregator:{agg_id}"
    cfg = entry.get("probe") or {}
    base_url = cfg.get("base_url", "")

    if hints.corpus_type not in (entry.get("corpus_types") or []):
        return SurfaceProbe(surface, base_url, base_url, "absent", None,
                            note=f"registry entry does not cover corpus_type "
                                 f"{hints.corpus_type!r}")
    if cfg.get("kind") != "elasticsearch":
        # Honest boundary, same shape as frontend_xhr: recorded, not silently skipped.
        return SurfaceProbe(surface, base_url, base_url, "manual", None,
                            note=f"registry probe kind {cfg.get('kind')!r} not automated yet")

    index = str(cfg.get("index_pattern", "")).replace("{slug}", hints.slug)
    url = f"{base_url.rstrip('/')}/{index}/_search"
    item_field, item_value = cfg.get("item_type_field"), cfg.get("item_type_value")
    fresh_field = cfg.get("freshness_field")

    body: dict[str, Any] = {"size": 0, "track_total_hits": True}
    if item_field and item_value:
        body["query"] = {"term": {item_field: item_value}}
    if fresh_field:
        # Freshness is MEASURED, never read off the index name (that is a reindex stamp).
        # BOTH ends are measured. `newest` alone answers "is it current?" and says nothing
        # about ARCHIVE DEPTH — an aggregator can be current to within days and still start
        # years after the source's own archive. Measured on Vlaardingen 2026-07-25: ORI is
        # current to 2026-07-09 and carries NOTHING before 2010 (0/28 sampled 2008 documents,
        # 0/58 sampled 2012), while the manifest declares `range.start: 2008-01-01`. A probe
        # reporting only `newest` recommends that route as if it covered the whole corpus.
        # `oldest` alone is still not enough, and this bit is measured too: ORI's earliest
        # Vlaardingen item is 2010-01-07, but 2010 holds 133 items, 2011-2012 hold ZERO and
        # 2013 holds 599 against ~1,400/yr from 2014. Read as a start date, "2010" claims an
        # archive the aggregator does not have. The per-year shape is what exposes a sparse
        # head, so it is reported rather than reduced to a threshold nobody measured.
        body["aggs"] = {
            "newest": {"max": {"field": fresh_field}},
            "oldest": {"min": {"field": fresh_field}},
            "by_year": {"date_histogram": {
                "field": fresh_field, "calendar_interval": "year",
                "format": "yyyy", "min_doc_count": 1,
            }},
        }

    code, doc, err = _es_search(http_get, base_url, index, body)
    if err is not None:
        return SurfaceProbe(surface, base_url, url, "error", code, note=err)
    if code is not None and 500 <= code < 600:
        return SurfaceProbe(surface, base_url, url, "error", code,
                            note=f"HTTP {code} (server error — undetermined)")
    if code != 200 or not isinstance(doc, dict):
        return _absent_or_error(surface, base_url, url, code, None)

    total = (((doc.get("hits") or {}).get("total")) or {}).get("value", 0)
    if not total:
        # A clean 200 with zero hits is VERIFIED-ABSENT: this aggregator does not carry
        # this corpus. That is the expected outcome for most pairings and is knowledge.
        return SurfaceProbe(surface, base_url, url, "absent", code,
                            note=f"no items in {index} — aggregator does not carry this corpus")

    aggs = doc.get("aggregations") or {}
    newest = aggs.get("newest") or {}
    oldest = aggs.get("oldest") or {}
    ev: dict[str, Any] = {
        "aggregator": agg_id,
        "index": index,
        "item_count": total,
        "carries": entry.get("carries") or [],
        "natural_key_field": (entry.get("join") or {}).get("natural_key_field"),
        "consent_posture": entry.get("consent_posture"),
        "license": entry.get("license"),
        "robots": entry.get("robots"),
        "republishes_hosts": (entry.get("join") or {}).get("matches_provider_hosts") or [],
    }
    if newest.get("value_as_string") or newest.get("value"):
        ev["newest_item"] = newest.get("value_as_string") or newest.get("value")
        ev["freshness_field"] = fresh_field
    if oldest.get("value_as_string") or oldest.get("value"):
        # Archive depth — the other end of the same measurement. Reported so the gate can
        # compare it against the source's declared range instead of assuming coverage.
        ev["oldest_item"] = oldest.get("value_as_string") or oldest.get("value")
    buckets = (aggs.get("by_year") or {}).get("buckets") or []
    if buckets:
        ev["items_per_year"] = {
            b.get("key_as_string") or str(b.get("key")): b.get("doc_count", 0)
            for b in buckets
        }
    return SurfaceProbe(
        surface, base_url, url, "found", code, "bulk_content", ev,
        f"{total} items in {index}"
        + (f", newest {ev['newest_item']}" if "newest_item" in ev else "")
        + (f", oldest {ev['oldest_item']}" if "oldest_item" in ev else ""),
    )


def probe_aggregators(hints: AggregatorHints | None, http_get: HttpGet,
                      registry: list[dict[str, Any]] | None = None) -> list[SurfaceProbe]:
    """The corpus-scoped registry step. Returns one SurfaceProbe per registry entry.

    With insufficient hints this records ONE `manual` surface rather than probing: the
    registry was not consulted, which is an obligation the onboarding agent still owes
    (declare `source.corpus_type` + `source.organisation`). Deliberately `manual`, never
    `absent` — "we did not look" must never read as "we looked and found nothing" — and
    deliberately not `error`, because gating every undeclared source would wedge the fleet,
    the same failure the phased robots landing exists to avoid.
    """
    entries = load_aggregator_registry() if registry is None else registry
    if not entries:
        return []
    if hints is None or not hints.sufficient:
        missing = "corpus_type" if (hints is None or not hints.corpus_type) else "organisation"
        return [SurfaceProbe(
            "aggregator_registry", "", "", "manual", None,
            note=f"registry NOT consulted — source.{missing} not declared. A third-party "
                 f"aggregator may republish this corpus (SURF-3); declare the hints or "
                 f"check {len(entries)} registry entr{'y' if len(entries) == 1 else 'ies'} by hand.",
        )]
    return [probe_aggregator(e, hints, http_get) for e in entries]


# Probers with the uniform (host, http_get) signature. `probe_sitemap` is run separately
# by probe_bulk_surfaces because it shares that host's single robots.txt fetch with
# `probe_robots`; the emitted surface order is unchanged.
AUTOMATED_SURFACES = (probe_sru, probe_oai, probe_ckan)


def candidate_hosts(base_url: str | None, extra: Iterable[str] = ()) -> list[str]:
    """The host list probe 0 should test for a source, de-duplicated in order.

    Shared by `civic probe-bulk` and the W6 onboarding runtime so the split-host rule
    lives in exactly one place: a provider's bulk host is not always the public host.
    """
    hosts = [h for h in extra if h]
    if base_url:
        hosts.append(base_url)
        if urlsplit(base_url).netloc.endswith("overheid.nl"):
            hosts.append(KOOP_BULK_HOST)
    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        key = _norm_host(h)
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def probe_bulk_surfaces(hosts: list[str], http_get: HttpGet | None = None,
                        hints: AggregatorHints | None = None,
                        registry: list[dict[str, Any]] | None = None) -> BulkProbeReport:
    """Run the full probe-0 battery against each candidate host, then the aggregators.

    hosts: provider host(s) to test — e.g. ["https://repository.overheid.nl"]. For the
    KOOP/overheid family the bulk host (repository.overheid.nl) differs from the source's
    public base_url (tuchtrecht.overheid.nl), which is exactly why the surface was missed;
    pass every host worth testing.

    hints: corpus identity for the aggregator step (SURF-3). Omitted or incomplete, the
    registry step records a `manual` obligation rather than silently skipping — a
    host-scoped battery alone cannot see a republished corpus.
    """
    get = http_get or _real_http_get
    norm = [_norm_host(h) for h in hosts]
    surfaces: list[SurfaceProbe] = []
    policies: list[RobotsPolicy] = []
    for host in norm:
        # One robots.txt fetch per host, read twice: for Sitemap: advertisements (a
        # surface) and for Disallow/Crawl-delay (policy — POL-1).
        policy = probe_robots(host, get)
        policies.append(policy)
        for prober in AUTOMATED_SURFACES:
            surfaces.append(prober(host, get))
        surfaces.append(probe_sitemap(host, get, policy=policy))
        surfaces.append(probe_frontend_xhr(host, get))
    # Corpus-scoped, once per source rather than once per host.
    surfaces.extend(probe_aggregators(hints, get, registry=registry))
    return BulkProbeReport(hosts=norm, surfaces=surfaces, policies=policies)
