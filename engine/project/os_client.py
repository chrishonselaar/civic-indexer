"""OpenSearch client wrapper.

Ported from wob-search-mcp/shared/opensearch_client.py, trimmed to what the
projection needs: connect, ensure/recreate index, streaming bulk.
"""

from opensearchpy import OpenSearch, helpers

from engine.config import OPENSEARCH_URL


def client() -> OpenSearch:
    return OpenSearch(
        hosts=[OPENSEARCH_URL],
        use_ssl=OPENSEARCH_URL.startswith("https"),
        verify_certs=False,
        timeout=60,
    )


def ensure_index(os_client: OpenSearch, name: str, config: dict, recreate: bool = False) -> None:
    if recreate and os_client.indices.exists(index=name):
        os_client.indices.delete(index=name)
    if not os_client.indices.exists(index=name):
        os_client.indices.create(index=name, body=config)


def delete_by_source(os_client: OpenSearch, index: str, source_id: str) -> int:
    """Withdraw everything a source has in one index. Returns the deleted count.

    Used when an exposure tier is lowered (design/access-posture.md): the chunk index is
    additive, so text written under a higher tier has to be actively removed.
    """
    if not os_client.indices.exists(index=index):
        return 0
    result = os_client.delete_by_query(
        index=index,
        body={"query": {"term": {"source_id": source_id}}},
        refresh=True,
        conflicts="proceed",
    )
    return int(result.get("deleted", 0))


def bulk_index(os_client: OpenSearch, index: str, docs) -> tuple[int, int]:
    """Streaming bulk upsert; doc['_id'] is required. Returns (ok, failed)."""
    ok = failed = 0
    actions = (
        {"_op_type": "index", "_index": index, "_id": d.pop("_id"), "_source": d}
        for d in docs
    )
    for success, _info in helpers.streaming_bulk(
        os_client, actions, chunk_size=200, raise_on_error=False, request_timeout=120
    ):
        if success:
            ok += 1
        else:
            failed += 1
    return ok, failed
