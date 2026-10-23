# User story — one source, end to end

*A deliberately short narrative of what the system feels like to use. Companion detail:
`onboarding-flow.md` (the machinery behind acts 1–2).*

---

## Act 1 — Adding a source (Tuesday, 10 minutes)

Chris pastes a URL into the admin app: the uitspraken-database of a tuchtcollege, plus
one sentence: *"Alle uitspraken doorzoekbaar, met facetten op uitkomst en beroepsgroep;
oude uitspraken zijn gescande PDF's."*

The agent probes the site for a few minutes and comes back with a plan in plain
language: it found the search form (ASP.NET, needs a session token — handled), detected
a results cap at 250, proposes partitioning by year, found ~14.000 uitspraken back to
2004, and estimates that ~60% of pre-2015 PDFs are scans — **projected backfill cost:
€19 OCR, 2,1 GB storage; daily updates: ~€0,10/month.** Two questions: *"Ook de
persberichten-sectie meenemen? En is €25 een acceptabel backfill-plafond?"* Chris
answers no and yes.

## Act 2 — Verifying (same day, 15 minutes)

The agent runs a 30-item test and shows the results **as search result cards**: title,
date, extracted uitkomst, beroepsgroep facet, snippet, link to the official source. One
card shows "Uitkomst: 12-03-2019" — clearly a date leaked into the wrong field. Chris
clicks it, types "this is wrong, that's the zittingsdatum." The agent fixes the
extraction rule, re-runs against the already-downloaded pages (no re-crawl, ~seconds),
and the card now reads "Uitkomst: Berisping." Chris approves, confirms the daily 06:00
schedule and the €25 cap. The backfill runs overnight in budgeted tranches.

## Act 3 — Monitoring (the following months, ~1 minute/day)

Each morning a digest lists one line per source: *"tuchtcollege: 3 nieuw, reconciled ✓,
€0,004."* Weeks later the site redesigns and a selector goes to zero matches. The run
halts as **not reconciled** instead of silently harvesting nothing; the healing agent
diffs the new HTML against stored fixtures, patches one selector expression, passes
regression, auto-applies (low blast radius), and the digest reads: *"tuchtcollege:
selector healed (auto), 41 items caught up, reconciled ✓."* Chris reads it, and that's
all Chris had to do.

## Act 4 — Using it (any day, any user)

A journalist researching a negligent-care pattern opens the search UI: full-text query
with operators, filters on uitkomst = "Doorhaling" and years 2010–2020. Because the
scanned decisions were OCR'd, she finds three cases **invisible on the official site's
own search**. Each result links to the official source for citation, shows the appeal
chain grouped in one dossier view, and exports to CSV. Total time from her question to
three verifiable primary sources: four minutes.

---

# Nano Banana Pro — illustration prompt

*A single technical illustration of the main approach, for the README / presentations.*

> A wide 16:9 flat isometric technical illustration on an off-white background, clean
> editorial infographic style, thin dark-indigo outlines, muted palette of deep indigo,
> warm orange accents and soft grey, generous whitespace, subtle dot-grid backdrop, crisp
> sans-serif labels, no photorealism, no gradients heavier than subtle paper shading.
>
> **Left third — "messy sources":** a cluster of six small mismatched isometric
> buildings and portals representing Dutch government websites, each slightly different:
> one labeled "open.overheid.nl", one "raadsinformatie", one antenna-topped feed tower
> labeled "SRU / BUS feed", one dusty archive building leaking a stack of scanned PDF
> pages with visible black redaction bars, one with a locked gate section (small
> "geweigerd" sign), one cracked/renovating (scaffolding, "redesign!"). A small friendly
> robot figure with a magnifying glass walks among them holding a clipboard checklist
> labeled "stress-test", with tiny checkmarks; from its clipboard a glowing blueprint
> document flows rightward, labeled "manifest".
>
> **Center third — "the engine":** the manifest blueprint slots like a cartridge into a
> compact isometric factory line: five conveyor stations in a row labeled "discover",
> "fetch", "OCR", "enrich", "index"; the OCR station has a coin-slot budget gate with a
> small euro coin and a meter; between stations, small document tiles ride the belt,
> some with black redaction bars. Beneath the factory, a solid vault-like database
> cylinder labeled "ledger — ground truth", connected upward by pipes; a small gauge on
> the vault reads "reconciled ✓".
>
> **Right third — "the payoff":** a large clean search-interface panel floating like a
> screen, search bar with the word "zoeken", left column of facet checkboxes (a few
> checked, orange), result cards with small text lines and one highlighted snippet; in
> front of it a journalist figure with a notebook and a subtle lightbulb. From the
> screen, a thin dotted feedback arrow loops back over the top of the scene to the robot
> on the left, carrying a tiny report icon labeled "run report", closing the loop.
>
> Composition: strong left-to-right flow, the three thirds connected by the conveyor and
> the manifest/feedback arrows; horizon-free, floating on the off-white field; overall
> feeling: order emerging from mess, small-scale and frugal rather than big-tech;
> labels exactly as quoted, in a neutral geometric sans-serif; aspect ratio 16:9.
