"""URN-level suppression — the rule-5 closure for `policy.takedown`.

`manifests/vlaardingen_raad.yaml` promised "Engine-side: URN-level suppression list"
while nothing in `engine/` or `migrations/` implemented it (POL-1 §5). These tests pin
the two properties that make the promise true rather than aspirational:

* **durable** — a suppressed URN is excluded by the projection query itself, so it stays
  out of every later run including `--recreate`;
* **immediate** — suppressing deletes what is already in OpenSearch, because a
  projection run only ever adds.

Pure: no Postgres, no OpenSearch. The DB-shaped paths are exercised through a recording
fake cursor and a fake OpenSearch client, the same way `test_project.py` keeps the
projection tests in the normal fixture suite.
"""

import pathlib
import re

import pytest

from engine import config, suppress
from engine.project import mappings
from engine.project.projector import _select_projectable

PROJECTOR_SRC = (
    pathlib.Path(config.PROJECT_ROOT) / "engine" / "project" / "projector.py"
).read_text()
MIGRATIONS = sorted((pathlib.Path(config.PROJECT_ROOT) / "migrations").glob("*.sql"))


# ------------------------------------------------------------- urn -> dataset


@pytest.mark.parametrize("bad", ["", "   ", "no-colon-here", ":orphan", "source:"])
def test_a_malformed_urn_is_refused(bad):
    with pytest.raises(ValueError):
        suppress.check_urn(bad)


def test_the_dataset_is_looked_up_and_never_parsed_out_of_the_urn():
    """The defect a live round-trip caught on 2026-07-27, before this shipped.

    The URN prefix is the *provider* namespace, not the dataset: `notubiz:262:…` backs
    both vlaardingen_raad and haarlem_raad, so `split_part(urn, ':', 1)` cannot even
    disambiguate them. A purge routed by the prefix targets `civic-notubiz`, which does
    not exist — and deleting from a missing index is a legal no-op, so the takedown
    reports success while the document stays searchable.
    """
    assert not hasattr(suppress, "source_of"), "the prefix is not a source id"
    cur = RecordingCursor(one={"source_id": "vlaardingen_raad"})
    assert suppress.dataset_of(cur, "notubiz:262:1") == "vlaardingen_raad"
    assert "FROM items" in cur.sql and cur.params == ("notubiz:262:1",)


def test_an_undiscovered_urn_has_no_dataset_yet():
    """Suppressing before discovery is the case the table exists for; it must not
    invent a dataset."""
    assert suppress.dataset_of(RecordingCursor(one=None), "notubiz:262:1") is None


def test_a_suppression_needs_a_reason():
    """An unexplained suppression cannot be reviewed or lifted with confidence, so it
    is refused before it reaches the database."""
    with pytest.raises(ValueError, match="reason"):
        suppress.add("notubiz:262:1", reason="  ")


# ------------------------------------------------------- durable: the query excludes


class RecordingCursor:
    def __init__(self, rows=(), one=None):
        self.rows = list(rows)
        self.one = one
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one


def test_the_projection_query_excludes_suppressed_urns():
    cur = RecordingCursor()
    _select_projectable(cur, "vlaardingen_raad")
    normalised = " ".join(cur.sql.split()).lower()
    assert "not exists" in normalised and "suppressions" in normalised, cur.sql


def test_a_datasets_suppressions_are_found_through_items_not_the_cached_column():
    """A suppression recorded before the item was discovered carries source_id NULL.
    If the lookup trusted that column, the takedown would quietly stop applying the
    moment the item finally arrived — the worst possible time for it to lapse."""
    cur = RecordingCursor(rows=[{"urn": "notubiz:262:1"}])
    assert suppress.urns_for(cur, "vlaardingen_raad") == ["notubiz:262:1"]
    normalised = " ".join(cur.sql.split()).lower()
    assert "join items" in normalised, cur.sql
    assert cur.params == ("vlaardingen_raad", "vlaardingen_raad")


def test_the_stage_update_excludes_them_too():
    """An item marked stage='indexed' that is not in the index is a lie in the ledger,
    and the ledger is ground truth. Both statements must carry the guard."""
    guards = len(re.findall(r"FROM suppressions s WHERE s\.urn", PROJECTOR_SRC))
    assert guards == 2, f"expected the SELECT and the stage UPDATE to guard, found {guards}"


# ---------------------------------------------------- immediate: the index is purged


class FakeIndices:
    def __init__(self, existing):
        self.existing = set(existing)

    def exists(self, index):
        return index in self.existing


class FakeOpenSearch:
    def __init__(self, existing=(), deleted=3):
        self.indices = FakeIndices(existing)
        self.deleted = deleted
        self.calls = []

    def delete_by_query(self, index, body, **kw):
        self.calls.append((index, body, kw))
        return {"deleted": self.deleted}


def test_purge_deletes_the_document_and_its_chunks():
    doc_index = mappings.doc_index_name("vlaardingen_raad")
    chunk_index = mappings.chunk_index_name("vlaardingen_raad")
    osc = FakeOpenSearch(existing=[doc_index, chunk_index], deleted=1)

    stats = suppress.purge_from_projection(
        "vlaardingen_raad", ["notubiz:262:1"], osc=osc
    )

    assert stats == {"docs_deleted": 1, "chunks_deleted": 1}
    targets = {index: body["query"]["terms"] for index, body, _ in osc.calls}
    assert targets[doc_index] == {"urn": ["notubiz:262:1"]}
    # Chunks are keyed by parent_urn — deleting only the doc leaves the full text
    # searchable in the chunk index, which is the failure this asserts against.
    assert targets[chunk_index] == {"parent_urn": ["notubiz:262:1"]}


def test_purge_refreshes_so_the_deletion_is_visible_immediately():
    """Without refresh, a takedown looks unapplied to the very next search."""
    idx = mappings.doc_index_name("s")
    osc = FakeOpenSearch(existing=[idx, mappings.chunk_index_name("s")])
    suppress.purge_from_projection("s", ["s:1"], osc=osc)
    assert all(kw.get("refresh") for _, _, kw in osc.calls)


def test_purge_is_a_no_op_when_the_indices_do_not_exist_yet():
    """Suppressing before the first projection must not raise — the durable half
    still records it, and that is what keeps it out later."""
    osc = FakeOpenSearch(existing=[])
    assert suppress.purge_from_projection("s", ["s:1"], osc=osc) == {
        "docs_deleted": 0,
        "chunks_deleted": 0,
    }
    assert osc.calls == []


def test_purge_with_nothing_suppressed_touches_opensearch_at_all():
    osc = FakeOpenSearch(existing=[mappings.doc_index_name("s")])
    assert suppress.purge_from_projection("s", [], osc=osc)["docs_deleted"] == 0
    assert osc.calls == []


# --------------------------------------------------------------- rule 5 itself


def test_the_manifest_promise_has_code_behind_it():
    """The generalised form of the defect: a manifest may not promise an engine-side
    mechanism that no migration and no projection code implements. If a future manifest
    reintroduces the phrase without the machinery, this fails."""
    manifests = (pathlib.Path(config.PROJECT_ROOT) / "manifests").glob("*.yaml")
    promising = [m.name for m in manifests if "suppression" in m.read_text().lower()]
    assert promising, "expected at least vlaardingen_raad.yaml to promise it"

    ddl = "\n".join(m.read_text() for m in MIGRATIONS)
    assert "CREATE TABLE suppressions" in ddl, promising
    assert "suppressions" in PROJECTOR_SRC, promising
