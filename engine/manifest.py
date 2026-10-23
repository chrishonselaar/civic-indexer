"""Manifest loading + hand-written semantic validation (Stage A: no DSL interpreter).

Validation rules per design/onboarding-flow.md Phase 7 and the SRC friction log:
- absent budgets is INVALID (never "unlimited")
- any open TODO(probe) marker blocks leaving test mode
- claimed_total: none requires compensating_controls
- unknown discovery strategy is rejected
"""

import json
from pathlib import Path
from typing import Any

import yaml

from engine import db, exposure

KNOWN_STRATEGIES = {
    "paged_json_api", "notubiz_events", "paged_html_search", "sru_records", "oai_pmh",
    "ori_elastic",
}

REQUIRED_SECTIONS = [
    "manifest_schema",
    "source",
    "politeness",
    "budgets",
    "discovery",
    "identity",
    "fetch",
    "extraction",
    "index",
    "health",
]


class ManifestError(Exception):
    """Raised with a list of validation problems."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def get(d: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def load(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate(m: dict, *, allow_probes: bool = False) -> list[str]:
    """Return a list of problems (empty = valid)."""
    problems: list[str] = []

    for section in REQUIRED_SECTIONS:
        if section not in m or m[section] is None:
            problems.append(f"missing required section: {section}")
    if problems:
        return problems

    for key in ("id", "name", "base_url"):
        if not get(m, f"source.{key}"):
            problems.append(f"source.{key} is required")

    # Budgets: absent is invalid, != unlimited.
    if get(m, "budgets.eur_per_run_cap") is None:
        problems.append("budgets.eur_per_run_cap is required (absent budget is invalid)")

    # TODO(probe) markers anywhere block production mode.
    if not allow_probes:
        markers = _find_probe_markers(m)
        for where in markers:
            problems.append(f"open TODO(probe) marker at {where} (blocks leaving test mode)")

    roles = get(m, "discovery.roles", {})
    if not roles:
        problems.append("discovery.roles must define at least one role")
    for role_name, role in roles.items():
        base = role
        if "extends" in role:
            parent = roles.get(role["extends"])
            if parent is None:
                problems.append(f"role {role_name} extends unknown role {role['extends']}")
                continue
            base = {**parent, **role}
        strategy = base.get("strategy")
        if strategy not in KNOWN_STRATEGIES:
            problems.append(f"role {role_name}: unknown strategy {strategy!r}")
        ct = base.get("claimed_total")
        if ct is None:
            problems.append(f"role {role_name}: claimed_total must be declared (use 'declared: none' explicitly)")
        elif ct.get("declared") == "none" and not ct.get("compensating_controls"):
            problems.append(
                f"role {role_name}: claimed_total 'none' requires compensating_controls"
            )
        if base.get("sanity_cap") is None:
            problems.append(f"role {role_name}: sanity_cap is required (servers lie about counts)")

    for key in ("natural_key", "urn_template"):
        if not get(m, f"identity.{key}"):
            problems.append(f"identity.{key} is required")

    if not get(m, "health.fixtures"):
        problems.append("health.fixtures must register at least one fixture")
    if get(m, "health.zero_match_policy") != "alarm":
        problems.append("health.zero_match_policy must be 'alarm'")

    if not get(m, "extraction.ladder"):
        problems.append("extraction.ladder is required")

    problems += _validate_lookup_tables(m)
    problems += _validate_exposure(m)

    return problems


def _validate_exposure(m: dict) -> list[str]:
    """The access posture: both axes required, values checked (design/access-posture.md).

    **Fail-closed since 2026-07-27** (W6.5 item 1's second half). It could not be turned
    on earlier: with no posture recorded anywhere it would have wedged all four sources at
    once. Now that every manifest carries both axes it is the mechanical control that
    makes "the agent may not resolve the gate" true rather than merely instructed — which
    is rule 5, and the reason `policy.takedown` and the User-Agent both got code.

    A typo is rejected rather than tolerated because `exposure.rank()` falls back to the
    permissive default: an unvalidated `"T2 (metadata only)"` would silently over-serve.
    """
    problems: list[str] = []

    # It lives under policy.robots — that is where the agent writes it and where all four
    # manifests carry it. Reading policy.admin_decision instead would make the gate pass
    # for every source, which is the failure mode this check exists to prevent.
    decision = get(m, "policy.robots.admin_decision")
    if not decision:
        problems.append(
            "policy.admin_decision is required — the acquisition posture is an admin "
            "ruling and a manifest may not run without one (a NO-GO is a valid value)"
        )
    elif str(decision).strip() == "pending_gate_1":
        problems.append(
            "policy.admin_decision is still 'pending_gate_1' — gate 1 has not been ruled "
            "for this source; see design/access-posture.md"
        )

    tier = get(m, "policy.exposure")
    if tier is None:
        problems.append(
            f"policy.exposure is required — one of {', '.join(exposure.TIERS)} "
            "(design/access-posture.md §3)"
        )
    elif tier not in exposure.TIERS:
        problems.append(
            f"policy.exposure must be one of {', '.join(exposure.TIERS)} (got {tier!r})"
        )

    declared = get(m, "policy.processors")
    if declared is not None:
        if not isinstance(declared, list):
            problems.append("policy.processors must be a list")
        else:
            unknown = [p for p in declared if p not in exposure.PROCESSORS]
            if unknown:
                problems.append(
                    f"policy.processors: unknown {unknown} "
                    f"(known: {', '.join(exposure.PROCESSORS)})"
                )
    return problems


def _validate_lookup_tables(m: dict) -> list[str]:
    """enrichment.lookup_tables: optional, but a declared join must be complete.

    PROJ-1: the join fills a projected field at projection time, so exactly one
    thing may own that field. A table whose `to` also has an index.projection
    payload path is the contradiction that produced the dead publisher facet in
    the first place — reject it rather than silently pick a winner.
    """
    problems: list[str] = []
    projected = get(m, "index.projection", {}) or {}
    for name, table in (get(m, "enrichment.lookup_tables", {}) or {}).items():
        where = f"enrichment.lookup_tables.{name}"
        if not isinstance(table, dict):
            problems.append(f"{where} must be a mapping")
            continue
        for key in ("from", "to", "values"):
            if not table.get(key):
                problems.append(f"{where}.{key} is required")
        if not isinstance(table.get("values", {}), dict):
            problems.append(f"{where}.values must be a mapping of key -> label")
        field = table.get("to")
        if field and field in projected:
            problems.append(
                f"{where} fills '{field}', but index.projection also declares a payload "
                f"path for it — a projected field may have exactly one owner"
            )
    return problems


def _find_probe_markers(node: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            found += _find_probe_markers(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += _find_probe_markers(v, f"{path}[{i}]")
    elif isinstance(node, str) and "TODO(probe)" in node:
        found.append(path)
    return found


def resolve_role(m: dict, role_name: str) -> dict:
    """Return the role config with 'extends' flattened."""
    roles = get(m, "discovery.roles", {})
    role = roles[role_name]
    if "extends" in role:
        parent = roles[role["extends"]]
        role = {**parent, **{k: v for k, v in role.items() if k != "extends"}}
    return role


def store(m: dict) -> int:
    """Upsert source and store the manifest as a new version (no-op if payload unchanged).

    Returns the stored/current manifest version.
    """
    source_id = get(m, "source.id")
    with db.tx() as cur:
        db.upsert(
            cur,
            "sources",
            {
                "id": source_id,
                "name": get(m, "source.name"),
                "base_url": get(m, "source.base_url"),
            },
            conflict="id",
        )
        cur.execute(
            "SELECT version, payload FROM manifests WHERE source_id = %s "
            "ORDER BY version DESC LIMIT 1",
            (source_id,),
        )
        latest = cur.fetchone()
        canonical = json.loads(json.dumps(m, default=str))
        if latest and latest["payload"] == canonical:
            return latest["version"]
        version = (latest["version"] + 1) if latest else 1
        cur.execute(
            "INSERT INTO manifests (source_id, version, payload) VALUES (%s, %s, %s)",
            (source_id, version, db.jsonb(canonical)),
        )
        return version


def load_validated(path: str | Path, *, allow_probes: bool = False) -> dict:
    m = load(path)
    problems = validate(m, allow_probes=allow_probes)
    if problems:
        raise ManifestError(problems)
    return m
