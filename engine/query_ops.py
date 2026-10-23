"""Search operators: a boolean query language over the free-text `q` (W11(a)).

`idee.md` promises *"fulltext OpenSearch search met operators etc"* and names slow,
operator-less source search UIs as the thing to beat. Until now the query path was a
single `multi_match` with `operator: and`: no phrase quotes, no OR, no exclusion, no
wildcard. This module is the parser that closes that gap.

Grammar (recursive descent, precedence NOT > AND > OR):

    expr    := or
    or      := and ( OR and )*
    and     := unary ( AND? unary )*        # juxtaposition means AND — today's default
    unary   := (NOT | '-') unary | primary
    primary := '(' expr ')' | '"' phrase '"' | term

Keywords are **uppercase only**, in Dutch and English: `AND EN`, `OR OF`, `NOT NIET`.
Lowercase `en`/`of` are ordinary Dutch words (and are in `query_parse.STRANDED_WORDS`),
so accepting them lowercase would turn half the corpus's natural phrasing into
operators. The residual risk — an all-caps query where `EN` was meant literally — is
accepted: `EN` and `AND` mean the conjunction the engine applies by default anyway, so
only an all-caps `OF`/`NIET` can surprise, and it is visibly reported (`canonical`).

Three deliberate exclusions:

  * **`?` is not a single-character wildcard.** Dutch queries end in question marks far
    more often than anyone wants a one-character wildcard, and silently searching for
    `wat is dit?` as a wildcard pattern is worse than not having the feature.
  * **No leading wildcard.** `*wet` is a full-index scan over a corpus with OCR'd
    content fields; the leading star is stripped and reported instead.
  * **No field-scoped syntax** (`publisher:OCW`). W4's chips already do that, better —
    they resolve against the live facet vocabulary, so they cannot name a value the
    corpus lacks (`query_parse.py`). Adding `field:value` here would be a second,
    hallucination-prone way to say the same thing.

Fail-open, like W4's Tier 0 and extraction-side enrichment: anything this module
cannot make sense of leaves `used=False`, and the caller runs the literal query it
always ran. Parsing never gates search — it only ever adds structure.
"""

import re
from dataclasses import dataclass

# Same fields and weights as the plain path, so switching an operator on does not
# silently re-weight the query.
DEFAULT_FIELDS: tuple[str, ...] = ("title^2", "content")

AND_WORDS = frozenset({"AND", "EN"})
OR_WORDS = frozenset({"OR", "OF"})
NOT_WORDS = frozenset({"NOT", "NIET"})
KEYWORDS = AND_WORDS | OR_WORDS | NOT_WORDS

# Everything `query_string` treats as syntax, minus `*`: the wildcard is the only
# metacharacter a user of this path is allowed to send through unescaped.
_QS_SPECIAL = set('+-=&|><!(){}[]^"~:\\/')


@dataclass(frozen=True)
class Parsed:
    """The result of looking at a query for operators.

    detected   the query *contains* operator syntax (so the response says something
               about it, even when we failed to use it — silence would read as the
               operators having been applied)
    used       the compiled clause is the one to search with
    clause     the OpenSearch query clause, without filters
    canonical  what was understood, rendered back as text for display
    dense_text the positive text to embed for the dense leg (negations dropped)
    exclusions must_not clauses that hold for the *whole* query, so the dense leg can
               honour them too (see `nested_negation`)
    nested_negation
               a NOT sits under an OR, so no single filter expresses it — the dense
               leg cannot honour the exclusion and hybrid must step aside
    has_phrase a phrase appears somewhere — the highlighter needs to know (§10)
    free_spans spans of the root-conjunctive bare terms: the only positions a W4 chip
               may be derived from (§9.1), because a chip is a global filter
    tree       the parse itself, kept so `prune()` can drop chip'd leaves and recompile
    warnings   what we repaired or ignored, in the reader's language
    """

    detected: bool = False
    used: bool = False
    clause: dict | None = None
    canonical: str = ""
    dense_text: str = ""
    exclusions: tuple[dict, ...] = ()
    nested_negation: bool = False
    has_phrase: bool = False
    free_spans: tuple[tuple[int, int], ...] = ()
    tree: tuple | None = None
    warnings: tuple[str, ...] = ()


NO_OPERATORS = Parsed()

# `"`, `(`, `)` anywhere; a `-` glued to the front of a word (never a spaced dash, which
# is punctuation in `Wob - besluit`); a `*` glued to a word; an uppercase keyword.
_DASH = re.compile(r'(?<!\S)-(?=[^\s)])')
_STAR = re.compile(r'\w\*|\*\w')
_KEYWORD = re.compile(rf'(?<!\S)({"|".join(sorted(KEYWORDS))})(?!\S)')


def has_operators(q: str) -> bool:
    """Cheap, pure test. False here means the query path does not change at all."""
    if any(c in q for c in '"()'):
        return True
    return bool(_DASH.search(q) or _STAR.search(q) or _KEYWORD.search(q))


# --- tokenizer ---------------------------------------------------------------


def _tokenize(q: str, warnings: list[str]) -> list[tuple]:
    tokens: list[tuple] = []
    i, n = 0, len(q)
    while i < n:
        ch = q[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            start = i
            end = q.find('"', i + 1)
            if end == -1:
                # Google's behaviour, and the forgiving one: an unclosed quote is a
                # typo, not a reason to refuse the search.
                text, i = q[i + 1 :].strip(), n
                warnings.append(
                    'aanhalingsteken niet afgesloten — de rest is als één zin gezocht'
                )
            else:
                text, i = q[i + 1 : end].strip(), end + 1
            if text:
                tokens.append(("phrase", text, (start, i)))
            continue
        if ch in "()":
            tokens.append(("lparen" if ch == "(" else "rparen",))
            i += 1
            continue
        start = i
        while i < n and not q[i].isspace() and q[i] not in '()"':
            i += 1
        word = q[start:i]
        # A bare dash is punctuation ("Wob - besluit"); a dash glued to its operand is
        # negation. The distinction is the whole reason `-` is detected on whitespace.
        if word == "-":
            if i < n and q[i] == '"':
                tokens.append(("not",))
            continue
        while word.startswith("-"):
            tokens.append(("not",))
            word, start = word[1:], start + 1
        if not word:
            continue
        if word in AND_WORDS:
            tokens.append(("and",))
        elif word in OR_WORDS:
            tokens.append(("or",))
        elif word in NOT_WORDS:
            tokens.append(("not",))
        else:
            # The span is what §9's chip composition prunes by, so it is the span of
            # the word as typed — after any leading `-`, which is syntax, not text.
            tokens.append(("term", word, (start, i)))
    return tokens


# --- parser ------------------------------------------------------------------
#
# Nodes are tuples: ("term"|"wild"|"phrase", text, span) | ("not", node) |
# ("and"|"or", (node, ...)). Plain data, so the tests can assert on a parse without
# reading OpenSearch DSL. Leaves carry their span in the original query because §9's
# chip composition prunes leaves by span.


def _leaf(kind: str, text: str, span: tuple[int, int], warnings: list[str]):
    if kind == "term" and "*" in text:
        stripped = text.lstrip("*")
        if stripped != text:
            warnings.append(
                f'jokerteken aan het begin van "{text}" genegeerd — dat leest de hele index'
            )
        if not stripped.strip("*"):
            return None
        return ("wild", stripped, span)
    return (kind, text, span)


class _Parser:
    def __init__(self, tokens: list[tuple], warnings: list[str]):
        self.tokens = tokens
        self.pos = 0
        self.warnings = warnings

    def _peek(self) -> tuple | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> tuple:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self):
        """Whole token stream -> one node, or None when nothing searchable is left."""
        nodes = []
        while self._peek():
            if self._peek()[0] == "rparen":
                self._next()
                self.warnings.append("haakje sluit niets af — genegeerd")
                continue
            before = self.pos
            node = self._or()
            if node is not None:
                nodes.append(node)
            if self.pos == before:  # nothing consumed: stop rather than spin
                break
        return _combine("and", nodes)

    def _or(self):
        operands = [self._and()]
        while self._peek() and self._peek()[0] == "or":
            self._next()
            operands.append(self._and())
        operands = [o for o in operands if o is not None]
        if not operands:
            return None
        return _combine("or", operands)

    def _and(self):
        operands = []
        while True:
            tok = self._peek()
            if tok is None or tok[0] in ("or", "rparen"):
                break
            if tok[0] == "and":
                self._next()
                continue
            before = self.pos
            node = self._unary()
            if node is not None:
                operands.append(node)
            if self.pos == before:  # defensive: never spin on a token nobody consumed
                self._next()
        return _combine("and", operands)

    def _unary(self):
        tok = self._peek()
        if tok is None:
            return None
        if tok[0] == "not":
            self._next()
            operand = self._unary()
            if operand is None:
                self.warnings.append("NOT zonder zoekterm — genegeerd")
                return None
            return ("not", operand)
        if tok[0] == "lparen":
            self._next()
            inner = self._or()
            if self._peek() and self._peek()[0] == "rparen":
                self._next()
            else:
                self.warnings.append("haakje niet gesloten — aan het eind gesloten")
            if inner is None:
                self.warnings.append("leeg haakjespaar — genegeerd")
            return inner
        if tok[0] in ("term", "phrase"):
            self._next()
            return _leaf(tok[0], tok[1], tok[2], self.warnings)
        return None


def _combine(kind: str, nodes: list):
    nodes = [n for n in nodes if n is not None]
    if not nodes:
        return None
    if len(nodes) == 1:
        return nodes[0]
    return (kind, tuple(nodes))


# --- compilation -------------------------------------------------------------


def _escape(text: str) -> str:
    return "".join("\\" + c if c in _QS_SPECIAL else c for c in text)


def _clause(node, fields: tuple[str, ...]) -> dict:
    kind = node[0]
    if kind == "term":
        # Byte-identical to the plain path's clause for a one-word query, on purpose:
        # one word plus one operator elsewhere must not re-rank that word.
        return {
            "multi_match": {
                "query": node[1],
                "fields": list(fields),
                "operator": "and",
                "type": "cross_fields",
            }
        }
    if kind == "phrase":
        return {"multi_match": {"query": node[1], "fields": list(fields), "type": "phrase"}}
    if kind == "wild":
        # The one place a user string reaches query syntax, so everything except the
        # wildcard itself is escaped before it gets there.
        return {
            "query_string": {
                "query": _escape(node[1]),
                "fields": list(fields),
                "analyze_wildcard": True,
                "default_operator": "and",
            }
        }
    if kind == "not":
        # A bare must_not matches every document that lacks the term — correct for a
        # query that is nothing but an exclusion, and harmless inside a should.
        return {"bool": {"must_not": [_clause(node[1], fields)]}}
    if kind == "and":
        must, must_not = [], []
        for child in node[1]:
            if child[0] == "not":
                must_not.append(_clause(child[1], fields))
            else:
                must.append(_clause(child, fields))
        body: dict = {}
        if must:
            body["must"] = must
        if must_not:
            body["must_not"] = must_not
        return {"bool": body}
    if kind == "or":
        return {
            "bool": {
                "should": [_clause(child, fields) for child in node[1]],
                "minimum_should_match": 1,
            }
        }
    raise ValueError(f"unknown node {kind}")  # pragma: no cover


def _collect_negations(node, conjunctive: bool, fields, out: list, state: dict) -> None:
    """Which exclusions hold for the whole query, and which cannot be expressed.

    A NOT reached through AND nodes only is a global exclusion: every document in the
    answer lacks that term, so the dense leg can be given the same must_not and stays
    truthful. A NOT under an OR is *not* — `a OR NOT b` admits documents containing
    `b` — and there is no filter that expresses it. That case is reported upward
    (`nested_negation`) rather than approximated, because approximating it means the
    dense leg quietly returns exactly what the user asked to exclude.
    """
    kind = node[0]
    if kind == "not":
        if conjunctive:
            out.append(_clause(node[1], fields))
        else:
            state["nested"] = True
        return
    if kind == "and":
        for child in node[1]:
            _collect_negations(child, conjunctive, fields, out, state)
    elif kind == "or":
        for child in node[1]:
            _collect_negations(child, False, fields, out, state)


def _has_phrase(node) -> bool:
    """Does the query contain a phrase anywhere — including inside a NOT or an OR?

    The caller is the highlighter (§10), and it cares about the whole query: a fragment
    is chosen for the document, not per boolean branch.
    """
    if node[0] == "phrase":
        return True
    if node[0] == "not":
        return _has_phrase(node[1])
    if node[0] in ("and", "or"):
        return any(_has_phrase(c) for c in node[1])
    return False


def _dense_text(node, parts: list[str]) -> None:
    """Positive text only — the dense leg is a recall channel, and embedding the words
    the user excluded would retrieve precisely the documents they ruled out."""
    kind = node[0]
    if kind in ("term", "phrase", "wild"):
        parts.append(node[1].replace("*", " ").strip())
    elif kind in ("and", "or"):
        for child in node[1]:
            _dense_text(child, parts)
    # "not" subtrees are skipped entirely


def _render(node, parent: str | None = None) -> str:
    """What was understood, as text — shown to the reader (design §5).

    Grouping is made explicit rather than minimal: `a AND b OR c` renders as
    `(a AND b) OR c` even though the precedence rule already says so. The string
    exists to let someone check that the engine read their query the way they meant
    it, and "you can infer the grouping from the documented precedence" is not that.
    """
    kind = node[0]
    if kind == "phrase":
        return f'"{node[1]}"'
    if kind in ("term", "wild"):
        return node[1]
    if kind == "not":
        return "NOT " + _render(node[1], "not")
    joined = f" {kind.upper()} ".join(_render(c, kind) for c in node[1])
    return f"({joined})" if parent is not None and parent != kind else joined


def compile_query(q: str, fields: tuple[str, ...] = DEFAULT_FIELDS) -> Parsed:
    """Free text -> Parsed. Pure, no I/O, never raises."""
    if not q.strip() or not has_operators(q):
        return NO_OPERATORS

    warnings: list[str] = []
    try:
        node = _Parser(_tokenize(q, warnings), warnings).parse()
    except Exception as e:  # a parser bug must not take the search down with it
        return Parsed(
            detected=True,
            warnings=(f"operatoren niet verwerkt ({e}) — letterlijk gezocht",),
        )
    if node is None:
        return Parsed(
            detected=True,
            warnings=(*warnings, "geen zoekterm overgebleven — letterlijk gezocht"),
        )

    return _finish(node, fields, warnings)


def _finish(node, fields: tuple[str, ...], warnings: list[str]) -> Parsed:
    """Tree -> Parsed. Shared by the first parse and by `prune()`, so a pruned query is
    described exactly the way an equivalent typed query would be."""
    exclusions: list[dict] = []
    state = {"nested": False}
    _collect_negations(node, True, fields, exclusions, state)
    parts: list[str] = []
    _dense_text(node, parts)
    return Parsed(
        detected=True,
        used=True,
        clause=_clause(node, fields),
        canonical=_render(node),
        dense_text=" ".join(p for p in parts if p),
        exclusions=tuple(exclusions),
        nested_negation=bool(state["nested"]),
        has_phrase=_has_phrase(node),
        free_spans=_free_spans(node),
        tree=node,
        warnings=tuple(warnings),
    )


# --- §9: composing W4 chips with an operator query ---------------------------


def _free_spans(node) -> tuple[tuple[int, int], ...]:
    """The root-conjunctive bare terms — the only chip-able positions (§9.1).

    A chip is a *global* filter, so it may only be derived from a position that is
    globally conjunctive: a `term` leaf directly under the root AND, or a query that is
    one bare term. Never a phrase (quotes are literal), never a wildcard, never a
    negated term, and never anything inside a group or an OR branch — `subsidie OF
    (onderwijs 2026)` must not yield a `2026` filter, because that would drop every
    `subsidie` document from another year. It is the same rule §6 applies to
    exclusions, for the same reason.
    """
    if node[0] == "term":
        return (node[2],)
    if node[0] == "and":
        return tuple(c[2] for c in node[1] if c[0] == "term")
    return ()


def mask_outside(q: str, spans) -> str:
    """`q` with every character outside `spans` replaced by a space.

    This is what lets `query_parse.py` stay *completely unchanged*: the vocabulary scan
    and the date grammar see only the free text, spaces are word boundaries so nothing
    can match across a masked region, and — because the mask preserves length — every
    chip span still indexes into the original query.
    """
    keep = [False] * len(q)
    for start, end in spans:
        for i in range(start, min(end, len(q))):
            keep[i] = True
    return "".join(c if k else " " for c, k in zip(q, keep))


def prune(parsed: Parsed, spans, stranded=frozenset(), fields=DEFAULT_FIELDS) -> Parsed | None:
    """Drop the leaves a chip took, and recompile. None -> nothing left to search.

    The tree is pruned; the query string is never edited. That is the whole point: an
    operator is a *node*, never a leaf, so nothing here can reach one — which makes the
    class of bug §4 describes (chip extraction deleting a user's `EN` because `en` is a
    stranded connective) impossible by construction rather than by care.

    `stranded` is the connective vocabulary to drop alongside the chips ("van", "over"),
    applied only to root-conjunctive terms — the same words `query_parse.residual()`
    drops, and only ever from the free run.
    """
    if not parsed.used or parsed.tree is None or not spans:
        return parsed
    taken = [tuple(s) for s in spans]

    def keep(child) -> bool:
        if child[0] != "term":
            return True
        # Containment, not equality: one chip ("juni 2026", a whole publisher name)
        # routinely covers several leaves, and dropping only the exactly-matching one
        # would leave half a chip's words behind as required search terms.
        start, end = child[2]
        if any(a <= start and end <= b for a, b in taken):
            return False
        return child[1].lower() not in stranded

    root = parsed.tree
    if root[0] == "term":
        node = root if keep(root) else None
    elif root[0] == "and":
        node = _combine("and", [c for c in root[1] if keep(c)])
    else:
        node = root  # nothing chip-able at the root, so nothing to prune
    if node is None:
        return None
    return _finish(node, fields, list(parsed.warnings))
