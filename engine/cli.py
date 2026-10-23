"""civic — CLI entry point for the walking-skeleton engine."""

import argparse
import json
import sys

from engine import db, manifest


def cmd_run(args) -> int:
    from engine import fixtures, frontier, runs

    m = manifest.load_validated(f"manifests/{args.source}.yaml")
    phases = args.phase.split(",")
    window_start = f"{args.window}-01" if args.window else None

    if "discover" in phases:
        recorder = (
            fixtures.recorder(manifest.get(m, "source.id"))
            if args.record_fixtures
            else None
        )
        run_id, report = frontier.run_discover(
            m,
            mode=args.mode,
            window_start=window_start,
            fixture_recorder=recorder,
        )
        print(runs.render_report(run_id))
        if report["verdict"] == "NOT_reconciled":
            return 2

    if "fetch" in phases or "extract" in phases:
        from engine import process

        run_id, report = process.run_fetch_extract(
            m,
            mode=args.mode,
            window=args.window,
            limit=args.limit,
            budget_eur=args.budget_eur,
        )
        print(runs.render_report(run_id))
        if report["verdict"] == "NOT_reconciled":
            return 2
    return 0


def cmd_drain(args) -> int:
    from pathlib import Path

    from engine import drain, runs

    if args.export:
        stats = drain.export(Path(args.export), source=args.source)
        print(stats)
        return 1 if stats["missing_blob"] else 0
    if args.ingest:
        reports = drain.ingest(Path(args.ingest), source=args.source)
        worst = 0
        for _, (run_id, report) in sorted(reports.items()):
            print(runs.render_report(run_id))
            print()
            if report["verdict"] == "NOT_reconciled":
                worst = 2
        return worst
    with db.tx() as cur:
        rows = drain.select_queue(cur, source=args.source)
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["source_id"]] = by_source.get(r["source_id"], 0) + 1
    print({"queued": len(rows), "by_source": by_source})
    return 0


def cmd_resolve_archives(args) -> int:
    from engine import archive_resolve, runs

    m = manifest.load_validated(f"manifests/{args.source}.yaml")
    run_id, report = archive_resolve.resolve_archives(m, limit=args.limit)
    print(runs.render_report(run_id))
    return 2 if report["verdict"] == "NOT_reconciled" else 0


def cmd_probe_bulk(args) -> int:
    from engine import probe

    hosts: list[str] = list(args.host or [])
    corpus_type, organisation = args.corpus_type, args.organisation
    if args.source:
        m = manifest.load_validated(f"manifests/{args.source}.yaml")
        # probe.candidate_hosts owns the split-host rule (a provider's bulk host is not
        # always its public host); the W6 onboarding runtime calls the same helper.
        hosts = probe.candidate_hosts(manifest.get(m, "source.base_url"), extra=hosts)
        # Corpus identity for the aggregator step (SURF-3). Optional manifest fields: a
        # source that has not declared them gets a recorded `manual` obligation, not a
        # skipped step and not a failure.
        corpus_type = corpus_type or manifest.get(m, "source.corpus_type")
        organisation = organisation or manifest.get(m, "source.organisation")
    if not hosts:
        print("probe-bulk: give one or more hosts, or --source <id>", file=sys.stderr)
        return 1

    hints = probe.AggregatorHints(
        corpus_type=corpus_type, organisation=organisation, provider_hosts=hosts
    )
    report = probe.probe_bulk_surfaces(hosts, hints=hints)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    # Fail-closed exit: an incomplete battery must not read as "no bulk surface exists".
    return 2 if not report.enforceable else 0


def cmd_onboard(args) -> int:
    """W6 slice 2: drive one onboarding as a headless Agent SDK session.

    --dry-run assembles everything deterministically (system prompt, tool surface,
    session artifact) without the SDK, an API key or a live request — use it to verify
    the harness before spending anything.
    """
    import asyncio

    from engine import onboarding, onboarding_agent as agent

    source_id = args.source or _source_id_from_url(args.url)
    session = agent.load_session(source_id) if args.resume else None
    if args.resume and session is None:
        print(f"onboard: no session artifact to resume at {agent.session_path(source_id)}",
              file=sys.stderr)
        return 1
    session = session or onboarding.OnboardingSession(source_id=source_id, intent=args.intent)

    if args.dry_run:
        prompt = agent.build_system_prompt(args.url, args.intent, source_id)
        print(f"source_id:      {source_id}")
        print(f"session file:   {agent.session_path(source_id)}")
        print(f"system prompt:  {len(prompt)} chars (flow spec: {agent.FLOW_DOC})")
        print(f"verbs ({len(agent.VERBS)}):")
        for v in agent.VERBS:
            print(f"  - {agent.qualified(v.name)}")
        print(f"allow-listed:   {', '.join(agent.allowed_tools())}")
        print("interventions:  auto-logged for writes to " + ", ".join(agent.ENGINE_CODE_ROOTS))
        print(f"session state:  {json.dumps(session.to_dict(), ensure_ascii=False)}")
        return 0

    def show(message) -> None:
        print(message)

    session = asyncio.run(
        agent.run_onboarding(
            args.url,
            args.intent,
            source_id,
            session=session,
            max_turns=args.max_turns,
            model=args.model,
            on_message=show,
        )
    )
    print(runs_divider := "-" * 60)
    print(json.dumps(session.to_dict(), indent=2, ensure_ascii=False))
    print(runs_divider)
    problems = session.acceptance_problems()
    for p in problems:
        print(f"  ! {p}", file=sys.stderr)
    return 2 if problems else 0


def _source_id_from_url(url: str) -> str:
    from engine.onboarding import source_id_from_url

    return source_id_from_url(url)


def cmd_embed(args) -> int:
    from engine import embed

    stats = embed.embed_source(args.source)
    print(stats)
    return 0


def cmd_project(args) -> int:
    from engine.project import projector

    stats = projector.project(args.source, recreate=args.recreate)
    print(stats)
    return 1 if stats["docs_failed"] or stats["chunks_failed"] else 0


def cmd_check_projection(args) -> int:
    from engine.project import os_client, verify

    reports = verify.check_indices(os_client.client())
    print(verify.format_report(reports))
    return 0 if all(verify.is_clean(r) for r in reports) else 1


def cmd_check_ledger(args) -> int:
    from engine import ledger_check

    if args.repair:
        fixed = ledger_check.repair(args.source)
        print(f"repaired {len(fixed)} item(s)" + (f": {fixed[:5]} ..." if fixed else ""))
    reports = ledger_check.check(args.source)
    print(ledger_check.format_report(reports))
    return 0 if ledger_check.is_clean(reports) else 1


def cmd_import_wob(args) -> int:
    from pathlib import Path

    from engine import import_wob

    stats = import_wob.import_texts(
        dry_run=not args.commit,
        limit=args.limit,
        report_path=Path(args.report) if args.report else None,
    )
    mode = "COMMITTED" if args.commit else "DRY RUN (nothing written; pass --commit)"
    print(mode)
    for k in sorted(stats):
        print(f"  {stats[k]:7}  {k}")
    if args.commit and stats.get("imported"):
        print("\nnext: uv run civic embed woo_besluiten && uv run civic project woo_besluiten")
    return 0


def cmd_suppress(args) -> int:
    from engine import suppress

    row = suppress.suppress(args.urn, reason=args.reason, requested_by=args.requested_by)
    print(json.dumps(row, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_unsuppress(args) -> int:
    from engine import suppress

    if not suppress.remove(args.urn):
        print(f"not suppressed: {args.urn}", file=sys.stderr)
        return 1
    print(f"lifted: {args.urn} (reappears at the next `civic project` for its source)")
    return 0


def cmd_suppressions(args) -> int:
    from engine import suppress

    rows = suppress.listing(args.source)
    for r in rows:
        who = f" [{r['requested_by']}]" if r["requested_by"] else ""
        where = r["source_id"] or "(not yet ledgered)"
        print(f"{r['created_at']:%Y-%m-%d}  {r['urn']}  {where}{who}  {r['reason']}")
    print(f"-- {len(rows)} suppressed")
    return 0


def cmd_report(args) -> int:
    from engine import runs

    print(runs.render_report(args.run_id))
    return 0


def cmd_migrate(args) -> int:
    applied = db.migrate()
    print(f"applied: {applied or 'nothing (up to date)'}")
    return 0


def cmd_validate_manifest(args) -> int:
    m = manifest.load(args.path)
    problems = manifest.validate(m, allow_probes=args.allow_probes)
    if problems:
        print("INVALID manifest:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    version = manifest.store(m)
    print(f"valid: {manifest.get(m, 'source.id')} -> stored as version {version}")
    print(json.dumps(m, indent=2, ensure_ascii=False, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="civic", description="civic-indexer engine")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply pending SQL migrations").set_defaults(
        func=cmd_migrate
    )

    vm = sub.add_parser("validate-manifest", help="validate + store a manifest YAML")
    vm.add_argument("path")
    vm.add_argument(
        "--allow-probes",
        action="store_true",
        help="permit open TODO(probe) markers (test mode only)",
    )
    vm.set_defaults(func=cmd_validate_manifest)

    run = sub.add_parser("run", help="run pipeline phases for a source")
    run.add_argument("source", help="source id (manifests/<source>.yaml)")
    run.add_argument("--mode", choices=["backfill", "incremental", "upgrade"], default="backfill")
    run.add_argument("--phase", default="discover", help="comma-separated: discover,fetch,extract")
    run.add_argument("--window", help="YYYY-MM: discovery cutoff / fetch scope")
    run.add_argument("--limit", type=int, help="max items for fetch/extract phases")
    run.add_argument("--budget-eur", type=float, help="override manifest budget cap")
    run.add_argument("--record-fixtures", action="store_true", help="save first pages as fixtures")
    run.set_defaults(func=cmd_run)

    dr = sub.add_parser(
        "drain", help="drain a deferred queue via burst-pod export/ingest"
    )
    dr.add_argument("reason", choices=["ocr_tail"], help="queue to drain")
    dr.add_argument("--source", help="limit to one source id")
    dr.add_argument("--export", metavar="DIR", help="stage queued blobs + manifest.json")
    dr.add_argument("--ingest", metavar="DIR", help="apply <stem>.md OCR results")
    dr.set_defaults(func=cmd_drain)

    ra = sub.add_parser(
        "resolve-archives",
        help="resolve zip_dossier deferred items: OOXML office docs + fan out archive members",
    )
    ra.add_argument("source", help="source id (manifests/<source>.yaml)")
    ra.add_argument("--limit", type=int, help="max archives to resolve this pass")
    ra.set_defaults(func=cmd_resolve_archives)

    pb = sub.add_parser(
        "probe-bulk",
        help="Probe 0: fail-closed test for a bulk discovery surface (SRU/OAI/sitemap/CKAN)",
    )
    pb.add_argument("host", nargs="*", help="provider host(s) to probe, e.g. https://repository.overheid.nl")
    pb.add_argument("--source", help="derive host(s) from manifests/<source>.yaml base_url")
    pb.add_argument("--corpus-type", help="corpus identity for the aggregator registry step "
                                          "(e.g. council_information); overrides the manifest")
    pb.add_argument("--organisation", help="organisation whose corpus this is (e.g. Vlaardingen); "
                                           "overrides the manifest")
    pb.set_defaults(func=cmd_probe_bulk)

    ob = sub.add_parser(
        "onboard",
        help="W6: onboard a source with the headless agent runtime (engine verbs as tools)",
    )
    ob.add_argument("url", help="the source URL (flow Phase 0 intake)")
    ob.add_argument("--intent", required=True, help="one sentence of purpose (Phase 0 intake)")
    ob.add_argument("--source", help="source id to use (default: derived from the URL host)")
    ob.add_argument("--resume", action="store_true", help="continue a stored session artifact")
    ob.add_argument("--dry-run", action="store_true",
                    help="assemble prompt/tools/session and exit — no SDK, no API key, no requests")
    ob.add_argument("--max-turns", type=int, default=200)
    ob.add_argument("--model", help="model for the onboarding agent (default: opus)")
    ob.set_defaults(func=cmd_onboard)

    rp = sub.add_parser("report", help="print a run report")
    rp.add_argument("run_id", type=int)
    rp.set_defaults(func=cmd_report)

    em = sub.add_parser(
        "embed", help="fill the bge-m3 embedding cache for a source (needs --extra embed)"
    )
    em.add_argument("source")
    em.set_defaults(func=cmd_embed)

    pj = sub.add_parser("project", help="(re)build the OpenSearch projection")
    pj.add_argument("source")
    pj.add_argument("--recreate", action="store_true", help="drop + rebuild indices")
    pj.set_defaults(func=cmd_project)

    cp = sub.add_parser(
        "check-projection",
        help="does every civic-* index still match the declared mapping? "
        "(cross-corpus search unions them, and one drifted index fails silently)",
    )
    cp.set_defaults(func=cmd_check_projection)

    cl = sub.add_parser(
        "check-ledger",
        help="do the ledger's own columns still agree about each item? "
        "(reconciliation counts items in vs out and cannot see this)",
    )
    cl.add_argument("--source", help="limit to one source_id")
    cl.add_argument(
        "--repair", action="store_true",
        help="fix ONLY the finding whose correct value is derivable from evidence "
        "the item already carries; never guesses a lost upgrade mark",
    )
    cl.set_defaults(func=cmd_check_ledger)

    iw = sub.add_parser(
        "import-wob-texts",
        help="adopt text the prior wob-search-mcp project already extracted for "
        "woo_besluiten stubs, instead of re-fetching at ~1 rps",
    )
    iw.add_argument(
        "--commit", action="store_true",
        help="actually write; without it this is a dry run that counts and reports only",
    )
    iw.add_argument("--limit", type=int, help="import at most N items (smoke test)")
    iw.add_argument("--report", help="write a per-item CSV of what was adopted")
    iw.set_defaults(func=cmd_import_wob)

    sp = sub.add_parser(
        "suppress",
        help="keep a URN out of the projection (takedown) and delete it from the index now",
    )
    sp.add_argument("urn", help="{source_id}:{natural_key}")
    sp.add_argument("--reason", required=True, help="why — an unexplained suppression is unreviewable")
    sp.add_argument("--requested-by", help="who asked, when known")
    sp.set_defaults(func=cmd_suppress)

    us = sub.add_parser("unsuppress", help="lift a suppression (re-indexes at the next project run)")
    us.add_argument("urn")
    us.set_defaults(func=cmd_unsuppress)

    sl = sub.add_parser("suppressions", help="list suppressed URNs")
    sl.add_argument("--source", help="limit to one source id")
    sl.set_defaults(func=cmd_suppressions)

    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
