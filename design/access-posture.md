# Access posture — consent is ambiguous by default, and the posture is two axes

**Status: framework, adopted 2026-07-27 by the admin. Per-source values are still owed.**

POL-1 treated a `Disallow: /` as an exceptional finding requiring a one-off ruling. Four
sources in, the exception rate is **4/4**. This document records the correction: ambiguity
about crawl consent is the *normal* state of a Dutch civic source, not an anomaly, and the
gate has to be an instrument that can absorb that — including changing its mind later.

It replaces nothing in the gate-1 memo `pol1-posture-decision.md` (internal, not in the
public export); that memo stays as the record of how the first three hosts were reasoned about. This is the shape the
*next* forty onboardings use.

## 1. Why ambiguity is the premise, not the exception

Measured, not assumed:

- **The rate is 100%.** Every source onboarded so far harvests a host serving
  `robots.txt: Disallow: /` — `open.overheid.nl`, `repository.overheid.nl`,
  `api.notubiz.nl` ×2, and (2026-07-27) the citizen-facing `vlaardingen.raadsinformatie.nl`
  as well.
- **Publishers are not consistent with themselves.** KOOP serves **four different postures
  across four of its own hosts**: permissive (`zoek.officielebekendmakingen.nl`), a Googlebot
  allowlist (`open.overheid.nl`), blanket disallow (`repository.overheid.nl`), and no file at
  all (`tuchtrecht.overheid.nl`). A directive that contradicts itself across one operator's
  estate is weak evidence of intent.
- **The convention has no legal force here.** `robots.txt` is a 1994 convention, codified as
  informational in RFC 9309. Dutch law contains no obligation to obey it. What law there is
  points the other way: the Wet hergebruik van overheidsinformatie obliges *bestuursorganen*
  to enable reuse, and the Wet open overheid obliges them to publish.
- **The major crawlers do not obey it.** Measured 2026-07-27 against Brave's index:
  `vlaardingen.raadsinformatie.nl` is indexed to depth (meeting pages, member pages) and
  `api.notubiz.nl` is indexed *including a `/documents/{id}/file` download endpoint* — both
  hosts serving `Disallow: /` to `*` with no exception. The same holds for
  `open.overheid.nl` (internal evidence memo `robots-posture-evidence.md` §5, not in the
  public export).

> ⚠️ **"Everyone ignores it" is not our justification, and must never be written as one.**
> The admin's position, recorded verbatim because it is load-bearing: the LLM industry
> decided to disregard this question entirely and pushed that to the extreme, *"iets wat ik
> overigens niet goedkeur"*. Our posture has to stand on its own reasons — statutory
> publicity, a public body that admits one crawler and excludes the rest, no bulk
> alternative — and each of those is recorded per source. The behaviour of other crawlers is
> **context for how much intent to read into a directive**, never a permission.

The other half of the premise is the reason this project exists at all: Dutch public bodies
put strikingly little effort into making public information retrievable. If the information
were properly unlocked there would be nothing to build. A posture framework that treats every
unclear signal as a stop sign therefore fails at the project's purpose; one that treats every
unclear signal as a green light fails at being defensible. Hence two axes.

## 2. The two axes

A single GO/NO-GO conflates two decisions that have different owners, different evidence and
different reversal costs:

| axis | question | values | reversal cost |
|---|---|---|---|
| **acquisition** | may we *fetch* from this host, and at what rate | `go` · `hold` · `no` | high — refetching a backfill is expensive, and un-fetching is impossible |
| **exposure** | what may *leave* this system, to whom | `T0`–`T3`, see §3 | low by design — the projection is rebuildable |

Splitting them is what makes "we may change our mind" real rather than a hope. A posture
change on the exposure axis is a re-projection, not a re-harvest. That asymmetry should drive
the defaults: **acquire early where the case is defensible, expose conservatively, and move
exposure up once the consent question is settled rather than betting the harvest on it.**

## 3. Exposure tiers

Declared per source as `policy.exposure`. Each tier includes everything below it.

| tier | what may leave the system | typical use |
|---|---|---|
| **T0 · metadata only** | title, date, publisher, document type, and a **link back to the publisher's own page**. No text, no snippets, no file bytes. | contested consent; personal-data-dense corpora; anything under an unanswered ask |
| **T1 · search with snippets** | T0 + retrieval over the text and short snippets in results, still linking back for the document itself | the default for statutorily public material |
| **T2 · full text** | T1 + the extracted full text served from our own copy | material with a clear licence or an affirmative permission |
| **T3 · derived artefacts redistributable** | T2 + OCR output, chunk text and embedding vectors available through the API for onward use | only with an explicit licence permitting it (e.g. CC-0) |

Two consequences worth naming, because they are the point:

- **Harvesting and publishing are separated.** A source can sit at `acquisition: go` and
  `exposure: T0`. We hold the corpus in the ledger, we can search it internally, and the
  public surface shows only what the publisher already shows plus a link home. That is a real
  and defensible position, and it is currently unavailable — the projection has one setting.
- **Derived artefacts are their own question.** OCR text and embeddings are things *we*
  produced from someone else's bytes, at our cost. Serving a vector index of a corpus is a
  materially different act from linking to it. T3 exists so that the decision to redistribute
  those is taken explicitly rather than inherited from "the documents are public".

## 4. Revisability is a mechanical property, not an intention

The reason this can work is architectural and already true: **Postgres is ground truth,
OpenSearch is a rebuildable projection** (`design/README.md`). Lowering a source's exposure
tier must therefore be a projection-time filter, never a migration or a delete.

State of the machinery:

- ✅ **URN-level suppression** — `civic suppress <urn>`, `migrations/0004`,
  `engine/suppress.py`. Durable (survives `--recreate`) and immediate (deletes from the live
  index). Built 2026-07-27.
- ✅ **Tier enforcement** — `engine/exposure.py`, read by the projector (T0) and by
  `ui/search_api.py` (T1/T2/T3), with `policy.exposure` validated in `engine/manifest.py`.
  Built 2026-07-27, `tests/test_exposure.py` (15 tests). Verified live: lowering
  `vlaardingen_raad` to T0 emptied the text from all 24 documents and **withdrew all 35
  chunks**, raising it back to T2 restored both, and the same holds under `--recreate`.
  Two design points worth keeping: the tier is **stamped on each document**, so a stale
  index cannot over-serve after a manifest edit; and at T0 `content` is **omitted, not
  emptied**, so a consumer sees "there is no text here" rather than a blank one.
- ❌ **Source-level suppression** — the generalisation of URN suppression: one command to
  pull an entire source out of the public projection while leaving the ledger intact. This is
  what "we changed our mind about this publisher" costs, and it should cost one command.
- ❌ **Disclaimers on the UI and API** — see §5.

- ✅ **Third-party processors** — `policy.processors`, opt-in per source, empty by default,
  stamped per document and enforced in the search API. Built 2026-07-27,
  `tests/test_exposure.py`.

**Why the processor axis exists, measured 2026-07-27.** `hybrid_rerank` sends the title and
full text of the top candidates to Voyage, a US API, on every query. Their own privacy
policy says what that means absent an opt-out:

> "unless you 'opt out' … you grant Voyage AI … a worldwide, irrevocable, perpetual,
> royalty-free … right and license to use, copy, reproduce, distribute, prepare derivative
> works of, display and perform the Customer Content … to train, improve, and otherwise
> further develop the Service."

The policy names **no retention period, no GDPR reference, no controller/processor role, no
SCC or Data Privacy Framework transfer mechanism, and no sub-processor list**; processing is
US-based. The opt-out is a setting on their website and — stated in the policy — applies
**only to content submitted after it is set**: *"any data provided prior to such period may
continue to be subject to"* the training licence. So content already sent during the
embedding/rerank A/B is not retrievable by opting out now.

Consequence, applied rather than noted: a source sends nothing to any third party unless its
manifest names the processor. Only `woo_besluiten` does — government decisions written for
publication. `tuchtrecht` does not (named professionals), and neither council source does
(`personal_data.smell: present` — residents named in raadsadressen, bezwaren,
handhavingsdossiers). When the pool contains a document without the permission the API
degrades to `hybrid` and says so in `rerank_blocked`, because a silent quality drop gets
"fixed" by someone deleting the check.

**Opt-out set 2026-07-27 (admin). What that fixed, and what it did not.**

Fixed — and it was the worst part: the perpetual training licence no longer attaches to new
submissions, and the policy's own words make retention effectively zero — *"If you opt out,
your Customer Content will be immediately deleted by Voyage AI after it is processed for
you."*

Not fixed, because it is a different kind of gap:

| requirement | status for `api.voyageai.com` |
|---|---|
| Art. 28 processor agreement (DPA) | **absent.** MongoDB's DPA is scoped to *"your use of the Cloud Services"* and never mentions Voyage or the embedding/reranking API |
| controller/processor roles named | absent |
| Chapter V transfer mechanism (SCCs / DPF) | **absent for this endpoint.** MongoDB certifies under the EU-US DPF, but for its Cloud Services — not for the Voyage API |
| sub-processor list | absent |
| processing location | US, no EU residency option |
| retroactivity | **none** — *"any data provided prior to such period may continue to be subject to"* the old licence, so the A/B traffic stays under it |

So the position after the opt-out: **defensible for content that carries no personal data,
still not defensible for content that does** — not because of what they do with it, but
because the paperwork GDPR requires for a US processor does not exist for this endpoint.

That is exactly how the manifests are set. The gap that used to sit here — `woo_besluiten`
being the only opt-in while its `personal_data.smell` read `not_assessed` — was **closed by
measurement on 2026-07-28**: `experiments/personal_data/findings.md`, a census (not a
sample) over all 409 documents we hold text for, which is precisely the set shipped to the
reranker.

Result: `smell: low`. The lakwerk is heavy and privacy-directed — **102,042 redaction
markers across 338 of 409 documents, 90% of them citing art. 5.1.2e** — and the residual is
**one probable unredacted BSN and a handful of third-party names in 26.2M characters**. So
the original argument survives, but not untouched: "no residents' correspondence" is true of
the bulk and wrong at the edges, because LNV handhaving files *are* correspondence with
private parties. The configuration is now defensible on evidence rather than on assertion —
with the caveat that the name detector had no NER pass, so the third-party count is a floor.

The one item that is hard to defend on any reading is the probable BSN in one ministry
release (identifier deliberately not given here). Suppressing it is an
admin call, not an agent one, and it is a matter for the publisher independently of what
this project does.

**Alternatives, if the reranker should stop being an exception at all** (measured
2026-07-27; none swapped in on vendor claims — the project has a rerank A/B harness and a
gold set, and Voyage rerank-2.5 earned its place there with +0.187 nDCG@10 over bge-m3 and a
win over Cohere):

| option | legal position | trade |
|---|---|---|
| **`bge-reranker-v2-m3`, self-hosted** | **no transfer at all** — nothing leaves | Apache 2.0, 568M params, multilingual, *same family as the bge-m3 embedder already in use*, so the existing pod workflow applies. Needs a GPU at query time, which is precisely why an API was chosen. Mitigable by reranking the top ~20 instead of the top 100. **The only option that would let all four sources rerank, including the T1 council corpora.** |
| **`mxbai-rerank-large-v2`, self-hosted** | same | Apache 2.0, 1.5B, Qwen2.5-based, German vendor. Larger and slower than bge; worth including in the A/B rather than assuming. |
| **Jina reranker API** (now Elastic) | **strongest hosted position**: Art. 28 DPA incorporated by reference (Elastic DPA), no-training stated in the *terms* rather than behind an opt-out — *"Jina AI does not use Customer request data, inputs, prompts, or uploaded content to train its models"* — and storage limited to *"the extent required to provide the Services"*. Elastic maintains SCCs, a sub-processor list and DPF coverage. | Drop-in HTTP rerank, same request shape. No EU residency commitment stated for the service itself. Note the *weights* are CC-BY-NC 4.0 — fine for this non-commercial project if ever self-hosted, poison for a commercial future. |

Recommendation: **measure `bge-reranker-v2-m3` on the existing gold set first.** If it lands
close to Voyage, self-hosting ends the question permanently and unlocks reranking for the two
council sources that are currently excluded. If it does not, Jina/Elastic is the hosted
fallback with real Art. 28 cover, and Voyage becomes the thing we stop needing.

## 5. What the public surface has to say

Not legal boilerplate; the four things a reader actually needs, and each of them is also a
thing we are claiming about ourselves:

1. **We are not the publisher.** Every result links back to the source, and the source is
   named. We are an index, not an archive of record.
2. **The text is machine-derived and may be wrong.** 16.5% of the Woo Stage-A sample were
   scans with no text layer; that text now comes from OCR. A snippet is evidence of what our
   pipeline read, not of what the document says.
3. **How to have something removed** — the route already advertised at
   `https://civic-indexer.edubox.nl/bot`, now backed by `civic suppress`.
4. **What this project is.** An individual research project, no company, no organisation —
   already stated on the bot page, and it belongs on the search UI too, because an operator
   who finds us via a search result never sees the bot page.

`ui/bot.html` covers 3 and 4 for crawler operators. The search UI covers none of them for
readers. That gap is the third owed item.

## 6. What this changes in onboarding

The onboarding agent's job on this question is **to characterise, never to resolve** — POL-1
already says the posture is an admin gate decision. What changes:

- Probe 0 already fetches and parses `robots.txt` and every report carries `policy_flags`
  (built 2026-07-25). It should additionally record, for each host: whether the directive
  names any crawler specifically, whether the operator's other hosts disagree, whether a
  declared reuse channel exists (aggregator, open-data portal, SRU/OAI), and whether the
  major search engines' indices contain disallowed paths. All four are cheap and all four
  were decisive in the three rulings taken so far.
- The agent proposes **both** axes with reasoning, and sets both to `pending_gate_1`.
- `validate_manifest` fails closed while **either** axis is unset. This is W6.5 item 1's
  second half; it stays off until every current manifest has values, because turning it on
  earlier wedges the fleet.
- In the W6 GUI, the two axes are one approval object with two fields — the "gate approvals
  as first-class UI objects" the roadmap already specifies.

## 7. Decisions recorded so far

**Gate 1 is closed as of 2026-07-27.** No manifest reads `pending_gate_1`, and
`validate_manifest` now fails closed on both axes — so a source cannot run without a
recorded posture, and the agent cannot pass the gate by omission.

| host | source(s) | acquisition | exposure | processors |
|---|---|---|---|---|
| `open.overheid.nl` | `woo_besluiten` | **go** — Googlebot gets the whole site while everyone else gets nothing; statutory publicity; no bulk alternative (SRU `woo`: 0 records); sole channel since 2026-04-28 | **T2** | `voyage_rerank` |
| `repository.overheid.nl` | `tuchtrecht` | **go** — SRU/FRBR are machine-access protocols with no other purpose; the operator's own discovery host serves no robots.txt; ~1 rps measured ceiling respected as hard | **T2** | none |
| `api.notubiz.nl` | `vlaardingen_raad`, `haarlem_raad` | **go, narrow** — same posture, weaker case (§8): ORI is the primary route, the provider is used only for what ORI provably cannot serve (pre-2014 archive, lagging recent window). No general backfill through the provider. | **T1** | none |

All three are conditional on the same three mechanisms, all of which exist: identifying
User-Agent with a resolving contact URL, a declared politeness ceiling, and URN-level
takedown on request.

The two council sources sit one tier lower for two independent reasons — the acquisition
ruling is narrow rather than plain, and `personal_data.smell` is `present` for Vlaardingen
(`not_assessed` for Haarlem, which is a reason for more care, not less). Showing no more
than the publisher already shows is the conservative half of a narrow ruling, and raising it
later costs a re-projection.

`tuchtrecht` is the one likely to move: `data.overheid.nl` lists that corpus as CC-0, which
would justify T3, but the manifest's own `license` still reads `not_assessed` and declaring
T3 on an unconfirmed licence would outrun the evidence.

## 8. The open one, and why it reads differently

`api.notubiz.nl` is not harder because the material is less public — council information is
statutorily public and nobody disputes that. It is harder because **every argument that
carried the two KOOP rulings is either absent or reversed**:

| | KOOP hosts | `api.notubiz.nl` |
|---|---|---|
| asymmetry to point at | Googlebot gets the whole site, we get nothing | **none** — `*` is disallowed with no exception, Google included |
| declared reuse channel | none exists (SRU `woo`: 0 records) | **ORI**, which the supplier itself names as its Woo channel |
| who we would be overriding | the bestuursorgaan with the publishing duty | a **BV**; the duty and the remedy sit with the gemeente |
| shape of the access | one bounded backfill | a bounded backfill **plus an ongoing poll** (~4 docs/day) |

**Does ORI cover it? Measured per era (785-document provider sample, 2026-07-25):**

| era | ORI coverage |
|---|---|
| 2008 | **0%** (0/28) |
| 2012 | **0%** (0/58) |
| 2014 | 100% (39/39) |
| 2016 | 95.6% (87/91) |
| 2018 | 99.3% (135/136) |
| 2020–2021 | 99.7–100% (423/424) |
| 2024 / 2026 | 100% (9/9) |
| recent window | **lagging** — 28 of 30 recent provider documents absent despite falling inside the covered window (TOT-5) |

So the honest answer to "is ORI a reasonably complete alternative": **yes for 2014–2025, no at
both ends.** Its Vlaardingen archive has a sparse head (133 items in 2010, zero in 2011–2012,
606 in 2013) and none of the sampled 2008/2012 provider documents are in it — an estimated
**~2,000–3,000 real documents** outside its range, in scope by the admin's own
`admin_intent` (*"archief vanaf 2008"*). And an ORI-only route cannot even *discover* those,
which collides with the project's own scoping rule (*discover all, process scoped, defer the
rest as metadata stubs*).

Haarlem is the counter-example that keeps this honest: same aggregator, and its provider
corpus has no pre-2014 documents at all (DRIFT-2), so the cutoff costs it nothing. Archive
depth is probed per source, never read off the registry.

**The route that does not exist as a shortcut:** `data.overheid.nl` lists **4** raadsinformatie
datasets nationally (Den Haag, Nijmegen, Amsterdam, Eindhoven) — nothing for Vlaardingen or
Haarlem, nothing from Notubiz. `api.notubiz.nl`'s own root reads *"Er is nog geen documentatie
beschikbaar voor publiek gebruik"* (read from Brave's index, not by requesting the host).

**The consent route that does exist:** per Open State Foundation, unlocking a municipality's
council information as open data requires only that *"een gemeente toestemming geeft aan de
leverancier"*. The pre-2014 gap is therefore a question for the **griffie**, not for Notubiz —
and it is the one place in this whole file where a *declared* consent is obtainable cheaply.
**Unmeasured:** whether ORI/Notubiz can technically ingest pre-2014 at all. If not, the
consent route is not a route and the choice narrows to a bounded provider pass or dropping the
era.

## 9. Open items

Closed 2026-07-27: tier enforcement, the processor axis, all four manifests declaring both
axes, the three acquisition rulings, and `validate_manifest` fail-closed. What is left:

1. ~~Set the training opt-out in the Voyage account.~~ **DONE 2026-07-27.** What it did and
   did not fix is in §4. Follow-ups:
   - **Sample `woo_besluiten` for personal data** — its `voyage_rerank` opt-in still rests on
     an argument, not a measurement. Still owed.
   - ~~**A/B `bge-reranker-v2-m3` self-hosted** against Voyage — if it lands close, the
     third-party question ends.~~ **ANSWERED 2026-07-30, and the answer is no.** It does not
     land close. On the external comparison it beat the source site (0.698 vs 0.383) but per
     query it was **worse than plain `hybrid` on 5 of 12**, destroying one perfect result
     page outright (`experiments/external_comparison/FINDINGS.md` §5). So the third-party
     question does **not** end, and the two council sources do not get reranking this way.
   - **New, and it inverts a recorded ranking (2026-07-30).** §7 records Voyage as "worst
     legal position" and Cohere-EU as the strongest. That is only true of Cohere via **Azure
     AI Foundry / AWS Bedrock in an EU region** — a paid cloud deployment under the cloud
     provider's DPA and SCCs. It is **not** true of Cohere's own API on a free trial key,
     which is the only Cohere route the project currently has: the Cohere SaaS Agreement
     §3(a) lets Cohere share API Data with third parties "for other purposes", §13(d) says
     Cohere "will have no obligation to delete or otherwise render inaccessible any API
     Data", there is **no trial/production distinction** in those clauses and **no
     geographic restriction** anywhere in the agreement. Voyage *with the opt-out set* gives
     zero-day retention and no training licence. So on retention and training the ranking is
     **Voyage-with-opt-out > Cohere-trial**, and the Cohere advantage exists only on the paid
     EU-cloud route. There is a live Azure subscription in the estate
     (`src-ocr.cognitiveservices.azure.com`, from the prior Woo project), so that route is
     reachable — as a new paid processor relationship, which is a gate, not a config change.
2. **Disclaimers in the search UI** (§5), which today says none of the four things. The bot
   page covers crawler operators; a reader who arrives from a search result sees nothing.
3. **Source-level suppression** (`civic suppress --source`) — the "we changed our mind about
   this publisher" command. Today it would be a manual per-URN loop.
4. **Ask the Vlaardingen griffie** to let Notubiz unlock the pre-2014 archive to ORI, and
   **ask Open State whether ORI can ingest pre-2014 at all** — the second decides whether the
   first is a route. Both would narrow §8's ruling further; neither blocks anything.
5. **`tuchtrecht`'s licence** — confirm the CC-0 listing on `data.overheid.nl` covers the
   SRU/FRBR corpus, which is what would justify T3.
6. **`onboarding-flow.md` Phase-1 wording** for the two axes, batched with W6.5 item 2's
   aggregator-step wording — one live re-onboard, ~$3.40. The code gate is already on; this
   is the agent's instructions catching up to it.
7. **`UNDECLARED_DEFAULT`** can now be deleted: with validation fail-closed, no manifest can
   reach the projector without a tier. It survives only as the fallback for documents
   projected before today, which one `--recreate` per source would clear.
8. **Four federated hosts, 20,712 documents — §10.** The largest open acquisition question
   in the project, and it did not exist yesterday because the blocker was recorded as
   technical.

## 10. Gate 2 — four federated hosts, filed 2026-07-30

**This is not a ruling. It is the gate, with the evidence assembled so the admin can rule
in one sitting.** Nothing has been fetched from any of these hosts.

`woo_besluiten` holds **20,712** items as `disposition: stub`, keyed `woo-idx-*`. Their
`identity.federated_stub.reason` has read *"federated portal record; no file on
open.overheid (needs per-portal scraper)"* since the manifest was written. The first half is
true. **The second half is wrong**, and that mattered: "needs a per-portal scraper" reads as
*engineering not yet done*, so nobody asked the access question, and 78% of the corpus sat
behind a blocker nobody re-examined.

Measured 2026-07-30 off the ledger, not inferred:

- **Every one of the 20,712 carries a `weblocatie`** — a direct PDF URL. 0 nulls.
- There are **four hosts, not twenty thousand portals**:

  | host | items | publisher | `robots.txt` |
  |---|---:|---|---|
  | `open.rijkswaterstaat.nl` | 20,102 | Rijkswaterstaat | 200. `User-agent: *` → **`Disallow: /publish/pages/*`** — and every one of our 20,102 URLs is under `/publish/pages/`. Also: `User-agent: Wooharvester` → `Disallow:` (empty = allow all), same for a named enterprise-search crawler. Plus **six DiWoo sitemap indices**. |
  | `media.gelderland.nl` | 446 | provincie Gelderland | **403** on `/robots.txt` (S3-style bucket). No file, so no directive — and no permission signal either. |
  | `es-connector.zeeland.nl` | 164 | provincie Zeeland | 200. `User-agent: *` with **no `Disallow` at all** — fully permissive — plus a DiWoo sitemap. |
  | `d2sra5koyio669.cloudfront.net` | 12 | (Gelderland CDN) | **does not resolve.** Dead; treat as gone_from_source. |

- Dates run 2024-02-28 → 2026-07-17. All are `aanbieder: Harvester`.

**Why this is a genuinely different posture question from §7's three hosts, in both
directions.** It is the first case where the two directions disagree inside one gate:

- `es-connector.zeeland.nl` is the **most permissive host the project has met** — an explicit
  `User-agent: *` with nothing disallowed, plus a declared bulk surface. Under §2 that is a
  plain `go`; there is no ambiguity to absorb.
- `open.rijkswaterstaat.nl` is the **first host whose directive is aimed precisely at us.**
  §7's rulings all leaned on a blanket `Disallow: /` being weak evidence of intent — a
  self-contradicting, unmaintained default. This is the opposite: a *maintained* file that
  allows two named crawlers, publishes six sitemaps, and disallows exactly the one path tree
  our documents live in. That is a considered directive, not a default, and the KOOP
  reasoning does not transfer to it.
- But the same file also **names the consent-bearing route**: `User-agent: Wooharvester` is
  allowed everything. A Woo harvester is what this project is. Whether to present as one is
  the admin's call and touches the standing constraint that identification must never be
  *personal* — a project UA with a resolving project contact URL is the shape §7 already
  requires, and it is available here rather than needing to be argued for.
- The **DiWoo sitemaps are the bigger prize than the 20,712 URLs.** Six sitemap indices are a
  declared bulk surface on the publisher's own host — exactly what `civic probe-bulk` exists
  to find, and exactly what `aggregator-registry.md` prefers over scraping. If RWS is
  approached at all, it should be through the sitemaps, not by fetching 20,102 PDF URLs
  harvested out of somebody else's index.

**What the agent is not doing.** No fetch, no manifest `admin_decision` written, no
`Wooharvester` UA adopted, no contact sent. The four hosts stay unruled and the 20,712 stay
stubs.

**A resolved sub-question, recorded so it is not re-asked.** These 20,712 are **not** the
same documents as the ones the prior `wob-search-mcp` project already downloaded — a
plausible hope, since that project holds 12,335 open.overheid documents. They cannot be: it
fetched only `open.overheid.nl/documenten/<uuid>/file`, and **0 of its rows have a `pdf_url`
containing `woo-idx`** (verified against its ledger, not assumed). The overlap it *does*
have is a different cohort entirely, adopted 2026-07-30 by `civic import-wob-texts` — see
`engine/import_wob.py`, whose `WOO_IDX_IS_NOT_THIS` section exists to stop exactly this
conflation.
