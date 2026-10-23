"""Tier 0 query understanding: free text -> editable filter chips (W4).

design/query-understanding.md §4 "Tier 0 — deterministic grammar". A case-folded
scan of the query against the *live* facet vocabulary, a small alias table, and a
closed Dutch date grammar. No LLM (§8 step 1 is deliberately LLM-free) and no
I/O: the caller hands in the vocabulary, which is what makes the alias table and
the date grammar testable without OpenSearch.

Closed world, by construction: a filter value is only ever a member of the
vocabulary passed in, so the dominant failure mode of query parsing — a
hallucinated filter value that silently returns zero results — cannot occur here.
Anything not understood stays in the query; parsing only ever *adds* structure.

Chip contract (§4): every chip is

    {kind, field, values, label, source, span, text, id}

  kind    filter (a real facet term) | term | expansion   (Tier 0 emits filters)
  field   the *index* field the filter applies to (publisher, published_month, ...)
  values  the terms to filter on. A list because one date expression ("2026")
          is one chip covering twelve months.
  label   what to show — always absolute, never relative (§7).
  source  vocab | alias | date_grammar   (provenance is displayed)
  span    [start, end) in the original query, so the UI can show what was
          understood and put the text back into `q` when the chip is removed.
  text    the matched substring, verbatim.
  id      field + matched text — the handle the UI removes a chip by.

The search API adds a `count` (how many hits this chip alone would yield) before
returning; that one is decoration, computed against the index, not part of the
parse.
"""

import re
from datetime import date

MONTH_FIELD = "published_month"
VOCAB_FIELDS = ("publisher", "file_type", "disposition")

# A vocabulary value shorter than this is not scanned for. Short values ("ok")
# are false-positive machines in free text; aliases are exempt because they are
# declared on purpose.
MIN_VOCAB_MATCH_CHARS = 4

# Word characters for boundary tests, on an already-lowercased string.
_W = r"[0-9a-zÀ-ɏ]"

DUTCH_MONTHS = {
    "januari": 1, "jan": 1,
    "februari": 2, "feb": 2, "febr": 2,
    "maart": 3, "mrt": 3,
    "april": 4, "apr": 4,
    "mei": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "augustus": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Abbreviation -> the keywords its canonical vocabulary value must contain.
# Deliberately NOT the literal value: the alias resolves against the *live*
# vocabulary, so it can never introduce a value the corpus lacks, and it
# survives a publisher being spelled slightly differently per source. An alias
# that matches zero or more than one vocabulary value is dropped — ambiguity is
# left in the query rather than guessed at.
# Ministry abbreviations are stable and hardcodable; per-source aliases
# (colleges, gemeenten) belong in the manifest, drafted during onboarding — §9.
ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "publisher": {
        "ocw": ("onderwijs", "cultuur", "wetenschap"),
        "lvvn": ("landbouw", "visserij"),
        "lnv": ("landbouw", "natuur"),
        "ezk": ("economische zaken",),
        "jenv": ("justitie", "veiligheid"),
        "vws": ("volksgezondheid", "welzijn", "sport"),
        "szw": ("sociale zaken", "werkgelegenheid"),
        "bzk": ("binnenlandse zaken",),
        "ienw": ("infrastructuur", "waterstaat"),
        "buza": ("buitenlandse zaken",),
        "az": ("algemene zaken",),
        "ctg": ("centraal tuchtcollege",),
    },
    "file_type": {
        "pdf": ("pdf",),
        "zip": ("zip",),
        "xml": ("xml",),
    },
}

# Dropped from the residue once something has been understood, so that
# "besluiten van OCW over subsidie" leaves "besluiten subsidie" rather than a
# string of stranded connectors — which matters because the BM25 leg ANDs its
# terms. Visible to the caller as `q_effective`; never applied to a query we
# understood nothing in.
STRANDED_WORDS = frozenset(
    "van de het een over voor uit met in op door bij aan tot te en of".split()
)


def _key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    i = year * 12 + (month - 1) + delta
    return i // 12, i % 12 + 1


def _months_ending(year: int, month: int, n: int) -> list[str]:
    """The n months ending at (year, month) inclusive, chronological."""
    return [_key(*_shift(year, month, -i)) for i in range(n - 1, -1, -1)]


def _year_to_date(now: date) -> list[str]:
    return [_key(now.year, m) for m in range(1, now.month + 1)]


def _quarter_start(month: int) -> int:
    return month - (month - 1) % 3


# Relative expressions. Resolved against `now` and frozen into absolute months
# in the chip (§7: relative dates are never stored relative), so a bookmarked
# URL keeps meaning what it meant.
RELATIVE: dict[str, object] = {
    "deze maand": lambda now: [_key(now.year, now.month)],
    "vorige maand": lambda now: [_key(*_shift(now.year, now.month, -1))],
    "afgelopen maand": lambda now: [_key(*_shift(now.year, now.month, -1))],
    "dit jaar": _year_to_date,
    "vorig jaar": lambda now: [_key(now.year - 1, m) for m in range(1, 13)],
    "afgelopen jaar": lambda now: _months_ending(now.year, now.month, 12),
    "laatste jaar": lambda now: _months_ending(now.year, now.month, 12),
    "dit kwartaal": lambda now: [
        _key(now.year, m) for m in range(_quarter_start(now.month), now.month + 1)
    ],
    "vorig kwartaal": lambda now: _months_ending(
        *_shift(now.year, _quarter_start(now.month), -1), 3
    ),
    "afgelopen kwartaal": lambda now: _months_ending(
        *_shift(now.year, _quarter_start(now.month), -1), 3
    ),
}


def _spans(haystack_lower: str, needle: str) -> list[tuple[int, int]]:
    """Word-boundary occurrences of `needle` in an already-lowercased haystack.

    Whitespace in the needle matches any run of whitespace, so a vocabulary
    value survives sloppy spacing in the query.
    """
    words = needle.lower().split()
    if not words:
        return []
    body = r"\s+".join(re.escape(w) for w in words)
    pattern = rf"(?<!{_W}){body}(?!{_W})"
    return [m.span() for m in re.finditer(pattern, haystack_lower)]


def chip_id(chip: dict) -> str:
    """Stable identity for a chip: its field plus the text it matched.

    What a removed chip is remembered by (`drop_chip=publisher:OCW`). Keyed on
    the matched text rather than the span, so it survives the same query being
    re-parsed, and dies as soon as the user edits that part of the query — which
    is the right lifetime for "I did not mean that word as a filter".
    """
    return f"{chip['field']}:{chip['text'].lower()}"


def _chip(
    field: str,
    values: list[str],
    label: str,
    source: str,
    span: tuple[int, int],
    q: str,
) -> dict:
    chip = {
        "kind": "filter",
        "field": field,
        "values": list(values),
        "label": label,
        "source": source,
        "span": [span[0], span[1]],
        "text": q[span[0] : span[1]],
    }
    chip["id"] = chip_id(chip)
    return chip


def _vocab_candidates(q: str, ql: str, vocab: dict) -> list[dict]:
    out = []
    for field in VOCAB_FIELDS:
        for value in vocab.get(field) or []:
            if len(value) < MIN_VOCAB_MATCH_CHARS:
                continue
            for span in _spans(ql, value):
                out.append(_chip(field, [value], value, "vocab", span, q))
    return out


def _resolve_alias(keywords: tuple[str, ...], values: list[str]) -> str | None:
    """The one vocabulary value containing every keyword, or None if 0 or >1."""
    hits = [v for v in values if all(k in v.lower() for k in keywords)]
    return hits[0] if len(hits) == 1 else None


def _alias_candidates(q: str, ql: str, vocab: dict) -> list[dict]:
    out = []
    for field, table in ALIASES.items():
        values = vocab.get(field) or []
        if not values:
            continue
        for alias, keywords in table.items():
            spans = _spans(ql, alias)
            if not spans:
                continue
            value = _resolve_alias(keywords, values)
            if value is None:  # unknown or ambiguous — leave the word in `q`
                continue
            for span in spans:
                out.append(_chip(field, [value], value, "alias", span, q))
    return out


def _range_label(months: list[str]) -> str:
    return months[0] if len(months) == 1 else f"{months[0]} – {months[-1]}"


_YEAR = r"(?:19|20)\d{2}"


def _date_candidates(q: str, ql: str, now: date) -> list[dict]:
    out: list[dict] = []

    def add(span: tuple[int, int], months: list[str]) -> None:
        if months:
            out.append(
                _chip(MONTH_FIELD, months, _range_label(months), "date_grammar", span, q)
            )

    # ISO month: 2026-06
    for m in re.finditer(rf"(?<!\d)({_YEAR})-(0[1-9]|1[0-2])(?!\d)", ql):
        add(m.span(), [f"{m.group(1)}-{m.group(2)}"])

    # Named month with a year: "juni 2026", "jun. 2026". A bare month name
    # without a year is deliberately NOT understood — picking a year for it
    # would be a guess, and guessing is what the closed world exists to avoid.
    names = "|".join(sorted(DUTCH_MONTHS, key=len, reverse=True))
    for m in re.finditer(rf"(?<!{_W})({names})\.?\s+({_YEAR})(?!\d)", ql):
        add(m.span(), [_key(int(m.group(2)), DUTCH_MONTHS[m.group(1)])])

    # Bare year: the twelve months of it. Loses to the longer spans above when
    # they overlap (see _resolve).
    for m in re.finditer(rf"(?<![\d-]){_YEAR}(?![\d-])", ql):
        year = int(m.group(0))
        add(m.span(), [_key(year, mo) for mo in range(1, 13)])

    # Rolling window: "afgelopen 6 maanden"
    for m in re.finditer(
        rf"(?<!{_W})(?:afgelopen|laatste|vorige)\s+(\d{{1,2}})\s+maanden(?!{_W})", ql
    ):
        n = int(m.group(1))
        if 1 <= n <= 60:
            add(m.span(), _months_ending(now.year, now.month, n))

    for phrase, resolve in RELATIVE.items():
        for span in _spans(ql, phrase):
            add(span, resolve(now))

    return out


def _resolve(candidates: list[dict]) -> list[dict]:
    """Longest span wins; overlaps and repeats of the same filter are dropped.

    A repeated value keeps its second span in `q` rather than being silently
    consumed by a chip the user cannot see (§7: no silent query rewriting).
    """
    chosen: list[dict] = []
    taken: list[tuple[int, int]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    ordered = sorted(
        candidates,
        key=lambda c: (
            -(c["span"][1] - c["span"][0]),
            c["span"][0],
            c["field"],
            c["source"],
        ),
    )
    for cand in ordered:
        start, end = cand["span"]
        if any(start < t_end and t_start < end for t_start, t_end in taken):
            continue
        key = (cand["field"], tuple(cand["values"]))
        if key in seen:
            continue
        taken.append((start, end))
        seen.add(key)
        chosen.append(cand)
    return sorted(chosen, key=lambda c: c["span"][0])


def residual(q: str, chips: list[dict]) -> str:
    """The query minus every chip's span — what actually goes to `q`.

    Returns `q` untouched when no chip was extracted, so a query we understood
    nothing in is passed through exactly as typed.
    """
    if not chips:
        return q
    kept: list[str] = []
    cursor = 0
    for start, end in sorted((tuple(c["span"]) for c in chips)):
        kept.append(q[cursor:start])
        cursor = max(cursor, end)
    kept.append(q[cursor:])
    words = " ".join(kept).split()
    return " ".join(w for w in words if w.lower() not in STRANDED_WORDS)


def chip_filters(chips: list[dict]) -> list[dict]:
    """Chips -> OpenSearch filter clauses. Same field twice = one OR'd terms."""
    by_field: dict[str, list[str]] = {}
    for chip in chips:
        if chip["kind"] != "filter":
            continue
        values = by_field.setdefault(chip["field"], [])
        values.extend(v for v in chip["values"] if v not in values)
    return [{"terms": {field: values}} for field, values in by_field.items()]


def parse(q: str, vocab: dict, now: date | None = None) -> tuple[list[dict], str]:
    """Free text -> (chips, residual query). Pure; `vocab` is field -> values."""
    if not q.strip():
        return [], q
    now = now or date.today()
    ql = q.lower()
    chips = _resolve(
        _vocab_candidates(q, ql, vocab)
        + _alias_candidates(q, ql, vocab)
        + _date_candidates(q, ql, now)
    )
    return chips, residual(q, chips)
