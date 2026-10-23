# Desk-check: the KOOP event-feed (BUS changelog) discovery contract

**Question being tested.** The item-envelope draft flagged one future source shape as the
only one that stresses the run-report/reconciliation model rather than merely adding a
strategy: KOOP's daily mutation feed. Does "consume a mutation stream" fit the stage
model and envelope, or does it break them?

**Grounding.** Checked against the official *Handleiding uitvragen Officiële Publicaties
V1.1* (KOOP, 07-10-2014, PUC_234679_13) — the authoritative description of the SRU
zoekdienst and the **Bulk UitleverSysteem (BUS)**. ⚠️ The handleiding is from 2014; every
fact below must be **re-verified by the onboarding probe** before a manifest is accepted
(the doc itself wavers between "anonymous passive FTP" and "SFTP", which is exactly the
kind of drift 12 years produce).

---

## 1. What the feed actually is (facts)

- **Transport:** SFTP/FTP at `bestanden.officielebekendmakingen.nl`, anonymous login.
  Server may refuse login when busy → retry later.
- **Layout:** a date tree `/{JJJJ}/{MM}/{DD}/{publicatieblad}/{OP-id}/` where
  publicatieblad ∈ {TRB, STB, STCRT, AG, KST, H, AH, KV, NDS, **BLG (bijlagen)**}. Each
  OP-id folder holds every verschijningsvorm: `{id}.pdf`, `{id}.xml`, `{id}.html.zip`,
  `{id}.odt`, plus `metadata.xml` and `metadata_owms.xml`.
- **The event file:** every day folder contains **`changelog.xml`** — "altijd aanwezig,
  altijd dezelfde naam", newest entries on top, its own last-modified timestamp updated
  on every change. Per publication it lists the file locations and an **actie**:
  - `toegevoegd` — first publication (possibly placed into an *older* date folder;
    recorded in both today's and that folder's changelog).
  - `veranderd` — correction: BUS **overwrites the files in the original date folder**
    and appends the action to *both* today's changelog and the original folder's
    changelog. Example in the doc: a 2008 correction to a 2006 kamerstuk mutates
    `/2006/01/12/kst/…` in place.
  - `verwijderd` — depublication: the folder is **removed**; the action is recorded in
    both changelogs.
- **Recommended consumption:** daily, process *yesterday's* `changelog.xml` in full —
  "gegarandeerd dat er geen content gemist wordt", at the cost of max one day of lag.
  Intraday polling is possible but requires diffing changelog versions (parliamentary
  docs arrive unformatted during the day and are later *replaced* by formatted versions).
- **Coverage caveat:** privacy-sensitive publications are **excluded from the BUS**
  entirely (and are only temporarily findable on the website, `/noindex/` URLs).
- **Companion pull interface:** SRU at `zoek.officielebekendmakingen.nl/sru/Search`
  (`x-connection=oep`), OWMS metadata, `enrichedData/locationURI` pointing into the BUS
  tree, externe bijlagen via `repository.officiele-overheidspublicaties.nl` paths — and a
  **maximum of 4200 results per query**: yet another silent cap, spotted in the manual
  itself. The cap-probe battery earns its keep again.

## 2. Contract comparison

| | Enumerate-a-window (all 4 existing projects) | Consume-a-changelog (BUS) |
|---|---|---|
| Unit of work | partition leaf (query slice) | one day's `changelog.xml` |
| Completeness claim | source's claimed total per leaf | changelog is *authoritative and complete* per day |
| Progress state | partitions covered | **high-water mark**: last fully-processed day |
| Sees deletions? | never (absence ≠ deletion) | yes — `verwijderd` events |
| Sees corrections? | only by re-crawling | yes — `veranderd` events, but **files mutate in place** |
| Failure mode | silent caps, partition holes | missed days; intraday churn double-processing |

## 3. What survives unchanged

Mapping the BUS onto the existing model, most things just… fit:

- **Envelope identity:** OP-id is a clean natural key (`oep:stb-2012-538`); the folder
  holds multiple verschijningsvormen of one work — the envelope's blob slot plus
  format-router handles pdf/xml/html.zip/odt (prefer XML: free, structured, no OCR ever).
- **Edges:** BLG members are `attachment_of` their parent publication; the
  formatted-replaces-unformatted cycle is the `supersedes` disposition we already have.
- **Tombstones:** `verwijderd` maps exactly onto disposition `gone_from_source` — and
  since officielebekendmakingen promises permanent URLs, a deletion is *itself signal*
  worth surfacing (our copy becomes the archive, same as the Vergunningen TTL logic).
- **Run accounting:** a day-changelog is formally identical to a partition leaf:
  `claimed` = entries in the changelog, `harvested` = entries processed, and the
  reconciliation verdict computes the same way. The run-report schema holds.
- **Once-per-day idempotence, ledger upsert, cheap-guard-before-expensive-work:** all
  engine invariants apply verbatim.
- **Known exclusions:** the privacy-sensitive gap is the forbidden-sections pattern from
  Gemeentes — a declared coverage caveat, reported with every run.

## 4. Amendments required (the actual findings)

**A. Discovery roles become first-class: `backfill` / `incremental` / `repair`.**
A stream tells you what changed since you listened; it cannot give you the initial state.
For BUS: backfill = grid-enumerate the date tree itself (no cap, no search API needed —
the tree *is* the corpus) or SRU with date partitioning (4200-cap); incremental = daily
changelog consumption; repair = replay the changelogs of missed days (day folders appear
to be retained indefinitely — verify at onboarding; if retention were bounded, repair
would escalate to windowed re-enumeration). The manifest schema must compose multiple
strategies per source with these roles — SRC/Vergunningen already hinted at
backfill-vs-daily modes; BUS makes it structural.

**B. The discovery hook emits tombstones, not just items.** The envelope already had
`gone_from_source`; what was missing is the *discovery output contract*: discovery yields
`item | tombstone(urn, observed_at, evidence)`. Enumerate-a-window sources simply never
emit tombstones (absence from a listing is not evidence of deletion — only a stream can
assert it).

**C. Run accounting gains a stream mode.** Three additions to the run report:
- `high_water_mark`: last fully-processed day (replaces "partitions covered" as the
  progress representation);
- `contiguity` invariant: every calendar day between first-consumed and high-water mark
  has exactly one processed changelog — a gap is a loud failure, the stream analogue of
  Σ(partitions) vs total;
- `reference_reconciliation`: scheduled periodic checksum against an *independent* pull
  interface (SRU `numberOfRecords` per date slice vs ledger counts) — because unlike a
  window enumeration, a stream consumer that silently drops events has **no per-run
  signal** that it did. The compensating control must be structural and scheduled.

**D. In-place mutation semantics.** `veranderd` overwrites files in the *original* date
folder, and past changelogs mutate too. Consequences: (1) a BUS path is a stable
*location* but not stable *content* — content identity must come from our stored hash,
never from path+date; (2) on `veranderd`, re-fetch, compare `content_hash`, and only
re-extract/re-enrich on change (cheap guard); (3) the mutated original-folder changelog
gives a second detection path — the repair role can use it to reconcile a suspected gap.

**E. The engine needs a transport abstraction.** BUS is SFTP/FTP — the first non-HTTP
source. Fetcher politeness generalizes too: "server refuses login when busy" is
retry-later semantics rather than HTTP 429/backoff. Transport = {http, sftp, …} becomes
an engine-level adapter beneath every discovery strategy; this was invisible while all
four projects were HTTP.

**F. Consumption-lag policy is a declarable choice.** "Process yesterday's changelog"
(complete, one day stale) vs intraday polling (fresh, must diff and will double-process
the unformatted→formatted churn). This is a per-source freshness/cost trade-off →
manifest declaration, default: one-day lag.

## 5. Verdict

**The stage model holds — no new hook.** The stresses land where predicted, and they are
schema amendments, not architecture changes: discovery-role composition (A), tombstones
in the discovery output contract (B), a stream mode in run accounting (C), and one new
engine capability (transport adapters, E). The envelope needed nothing: identity, typed
edges, `gone_from_source`, `supersedes`, and known-exclusion reporting all absorbed the
BUS semantics as-is.

Bonus finding: the 2014 manual *itself* documents another silent result cap (SRU 4200).
Every new source examined so far — five for five — has one. The mandatory cap-probe in
the onboarding stress-test battery is not paranoia; it is the base rate.

### Appendix — manifest fragment (discovery section only, illustrative)

```yaml
discovery:
  transport: sftp
  endpoint: bestanden.officielebekendmakingen.nl   # anonymous; verify FTP vs SFTP at onboarding
  roles:
    backfill:
      strategy: grid_enumeration
      path_template: "/{year}/{month:02d}/{day:02d}/{blad}/"   # typed format specifiers
      dimensions: {blad: [TRB, STB, STCRT, AG, KST, H, AH, KV, NDS, BLG]}
      empty_cell: skip
    incremental:
      strategy: changelog_feed
      feed_file: "/{year}/{month:02d}/{day:02d}/changelog.xml"
      lag: one_day                      # completeness over freshness (declared choice)
      actions: {toegevoegd: item, veranderd: item_refetch, verwijderd: tombstone}
      high_water_mark: day
    repair:
      strategy: changelog_replay        # replay missed days; escalate to backfill if folders gone
  reference_reconciliation:
    against: sru                        # zoek.officielebekendmakingen.nl, x-connection=oep
    cadence: monthly
    caps: {sru_max_results: 4200, mitigation: date_partition}
  known_exclusions:
    - "privacy-sensitive publications (not delivered to BUS; /noindex/ on web)"
```
