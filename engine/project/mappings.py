"""OpenSearch index mappings, parametrized by dataset.

Ported from wob-search-mcp/config/opensearch_mappings.py (Dutch analyzer,
parent + chunk indices, denormalized facet fields), reshaped to the envelope:
generic field names, reserved geo_point + knn_vector fields (unpopulated in
Stage A — populating them later is a re-projection, not a schema migration).
"""

EMBEDDING_DIMENSION = 1024  # bge-m3 (settled embedder; design/README.md)


def doc_index_name(dataset: str) -> str:
    return f"civic-{dataset}"


def chunk_index_name(dataset: str) -> str:
    return f"civic-{dataset}-chunks"


# Cross-corpus search (design/cross-corpus-search.md). Every dataset keeps its OWN
# index — that is what makes `civic project <source> --recreate` a per-source
# operation and the projection rebuildable — and searching across corpora is a
# query-time union rather than a shared index. Legal because the mappings above are
# not parametrized by dataset: every civic-* index has the same fields, the same
# analyzer and the same 1024-dim vector space, so a union searches, aggregates and
# kNN-ranks as one index would.
#
# The exclusion keeps the chunk indices out (they are a different document shape).
# A pattern rather than an enumeration, so a newly projected source is searchable
# the moment it exists, with nothing to register; a pattern matching nothing is an
# empty result set, not an error.
ALL_DATASETS = "all"
ALL_DOC_INDICES = "civic-*,-civic-*-chunks"


def doc_index_target(dataset: str | None) -> str:
    """What to search: one dataset's index, or every dataset's at once.

    `None` and `"all"` both mean the whole corpus — searching everything is the
    product default (design/user-story.md Act 4), so it is also the default here.
    """
    if not dataset or dataset == ALL_DATASETS:
        return ALL_DOC_INDICES
    return doc_index_name(dataset)


def analysis_settings() -> dict:
    """The Dutch analysis chain. Kept separate so drift can be checked against it.

    `dutch_analyzer` is the built-in `dutch` analyzer's chain, rebuilt explicitly with
    **`keyword_repeat` + `remove_duplicates`** around the stemmers, so every token is
    indexed BOTH as written and as stemmed.

    Measured 2026-07-29 (`experiments/external_comparison/`): the built-in analyzer
    alone sends singular and plural to *different* stems —

        tandarts    -> ['tandart']      # snowball reads the -s as a plural marker
        tandartsen  -> ['tandarts']     # and strips the -en

    — so they never meet, and searching `tandartsen` found 20 of the 66 documents
    `tandarts` finds. The source site we were benchmarked against found all 66. Keeping
    the surface form alongside the stem makes the two overlap on `tandarts`:

        tandarts    -> ['tandarts', 'tandart']
        tandartsen  -> ['tandartsen', 'tandarts']

    This is the standard remedy for an over-eager stemmer and it is symmetric: it fixes
    the query side and the document side with one change, because both run this analyzer.
    The cost is a slightly larger index and marginally softer IDF where stem != surface;
    the alternative (an unstemmed sub-field) needs every query site to know about it.

    `dutch_override` carries the built-in's own irregular rules — dropping them while
    rebuilding the chain would have quietly un-fixed `kind`/`kinderen`.
    """
    return {
        "filter": {
            "dutch_stop": {"type": "stop", "stopwords": "_dutch_"},
            "dutch_override": {
                "type": "stemmer_override",
                "rules": ["fiets=>fiets", "bromfiets=>bromfiets", "ei=>eier",
                          "kind=>kinder"],
            },
            "dutch_stemmer": {"type": "stemmer", "language": "dutch"},
        },
        "analyzer": {
            "dutch_analyzer": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": [
                    "lowercase",
                    "dutch_stop",
                    "keyword_repeat",   # emit the surface form beside the stem
                    "dutch_override",
                    "dutch_stemmer",
                    "remove_duplicates",  # collapse the pair where stem == surface
                ],
            },
            "dutch_exact": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding"],
            },
        },
    }


def index_settings() -> dict:
    return {
        "analysis": analysis_settings(),
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "knn": True,
        },
    }


def _knn_vector() -> dict:
    return {
        "type": "knn_vector",
        "dimension": EMBEDDING_DIMENSION,
        "method": {
            "name": "hnsw",
            "space_type": "cosinesimil",
            "engine": "lucene",
            "parameters": {"ef_construction": 128, "m": 24},
        },
    }


DATE_FORMAT = "yyyy-MM-dd||yyyy-MM-dd'T'HH:mm:ss||yyyy-MM-dd'T'HH:mm:ssXXX||yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"


def doc_mappings() -> dict:
    return {
        "properties": {
            "urn": {"type": "keyword"},
            "source_id": {"type": "keyword"},
            "natural_key": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "dutch_analyzer",
                "fields": {
                    "keyword": {"type": "keyword", "ignore_above": 256},
                    "exact": {"type": "text", "analyzer": "dutch_exact"},
                },
            },
            "content": {"type": "text", "analyzer": "dutch_analyzer"},
            # The exposure tier this document was projected under; the API enforces
            # T1/T2/T3 from it. See design/access-posture.md and engine/exposure.py.
            "exposure": {"type": "keyword"},
            # Third-party processors this source opted into (e.g. voyage_rerank).
            # Empty means the text may not be transmitted to anyone.
            "processors": {"type": "keyword"},
            "publisher": {"type": "keyword"},
            "published": {"type": "date", "format": DATE_FORMAT},
            "published_month": {"type": "keyword"},
            "file_type": {"type": "keyword"},
            "disposition": {"type": "keyword"},
            "missing": {"type": "keyword"},
            "pages": {"type": "integer"},
            "extraction_method": {"type": "keyword"},
            "extraction_confidence": {"type": "float"},
            "landing_url": {"type": "keyword"},
            "file_url": {"type": "keyword"},
            "indexed_at": {"type": "date"},
            # Reserved seams (unpopulated in Stage A):
            "geo_point": {"type": "geo_point"},
            "embedding": _knn_vector(),
        }
    }


def chunk_mappings() -> dict:
    return {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "parent_urn": {"type": "keyword"},
            "chunk_text": {"type": "text", "analyzer": "dutch_analyzer"},
            "chunk_number": {"type": "integer"},
            "total_chunks": {"type": "integer"},
            "chunk_size_words": {"type": "integer"},
            "chunker_version": {"type": "keyword"},
            # Denormalized parent facets:
            "source_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "dutch_analyzer",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
            "publisher": {"type": "keyword"},
            "published": {"type": "date", "format": DATE_FORMAT},
            "published_month": {"type": "keyword"},
            "file_type": {"type": "keyword"},
            "disposition": {"type": "keyword"},
            "landing_url": {"type": "keyword"},
            # Reserved seam:
            "embedding": _knn_vector(),
        }
    }


def doc_index_config() -> dict:
    return {"settings": index_settings(), "mappings": doc_mappings()}


def chunk_index_config() -> dict:
    return {"settings": index_settings(), "mappings": chunk_mappings()}
