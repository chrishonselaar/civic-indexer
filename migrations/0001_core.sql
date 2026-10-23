-- 0001_core.sql — item-envelope ground truth per design/item-envelope.md §3–§7.
-- Vocabularies are lookup tables, never DB enums (envelope-desk-checks.md Q2):
-- adding a value later is an INSERT, not a migration.

BEGIN;

CREATE TABLE schema_migrations (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- vocabularies

CREATE TABLE vocab_stage (
    name text PRIMARY KEY,
    rank int  NOT NULL UNIQUE          -- stage axis is monotonic; rank enforces order in app code
);

CREATE TABLE vocab_disposition (
    name        text PRIMARY KEY,
    description text
);

CREATE TABLE vocab_edge_type (
    name        text PRIMARY KEY,
    description text
);

CREATE TABLE vocab_missing_reason (
    name        text PRIMARY KEY,
    description text
);

CREATE TABLE vocab_kept_reason (
    name        text PRIMARY KEY,
    description text
);

CREATE TABLE vocab_deferred_reason (
    name        text PRIMARY KEY,
    description text
);

CREATE TABLE vocab_run_mode (
    name text PRIMARY KEY
);

-- ---------------------------------------------------------------- sources & manifests

CREATE TABLE sources (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    base_url      text,
    paused        boolean NOT NULL DEFAULT false,  -- set on NOT_reconciled; blocks next backfill
    paused_reason text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE manifests (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id  text NOT NULL REFERENCES sources(id),
    version    int  NOT NULL,
    payload    jsonb NOT NULL,                     -- the validated YAML, normalized to JSON
    fixtures   jsonb NOT NULL DEFAULT '[]',        -- refs into the fixtures table
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, version)
);

-- ---------------------------------------------------------------- runs (before items: items reference run ids)

CREATE TABLE runs (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id           text NOT NULL REFERENCES sources(id),
    manifest_version    int  NOT NULL,
    mode                text NOT NULL REFERENCES vocab_run_mode(name),
    phases              text[] NOT NULL DEFAULT '{}',
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    claimed_total_first int,                       -- totaal on the first page of the walk
    claimed_total_last  int,                       -- totaal on the last page (mutation churn detector)
    sum_of_leaves       int,
    harvested_total     int,
    verdict             text,                      -- reconciled | reconciled_with_warnings | NOT_reconciled
    report              jsonb                      -- the full run report (item-envelope.md §6)
);

CREATE TABLE run_partitions (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     bigint NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    leaf       text   NOT NULL,                    -- e.g. "2026-06"
    claimed    int,                                -- NULL = source declares no per-leaf total
    harvested  int    NOT NULL DEFAULT 0,
    cap_hit    boolean NOT NULL DEFAULT false,     -- triggers adaptive subdivision next run
    subdivided boolean NOT NULL DEFAULT false,
    notes      text,
    UNIQUE (run_id, leaf)
);

-- ---------------------------------------------------------------- items (envelope core)

CREATE TABLE items (
    urn              text PRIMARY KEY,             -- {source_id}:{natural_key}
    source_id        text NOT NULL REFERENCES sources(id),
    natural_key      text NOT NULL,
    content_hash     text,                         -- sha256:… set after fetch
    merge_policy     text NOT NULL DEFAULT 'keep_separate',
    merged_into      text REFERENCES items(urn),
    cross_source_keys jsonb NOT NULL DEFAULT '[]',

    manifest_version int,
    discovered_via_strategy       text,
    discovered_via_partition_leaf text,
    discovered_via_run_id         bigint REFERENCES runs(id),

    stage       text NOT NULL REFERENCES vocab_stage(name),
    disposition text NOT NULL REFERENCES vocab_disposition(name),
    missing     text[] NOT NULL DEFAULT '{}',      -- values from vocab_missing_reason (app-checked)
    reason      text,
    retryable   boolean,
    upgradable  boolean NOT NULL DEFAULT false,

    group_keys  jsonb NOT NULL DEFAULT '{}',
    payload     jsonb NOT NULL DEFAULT '{}',       -- source metadata record as discovered
    cost        jsonb NOT NULL DEFAULT '{}',       -- accounting.cost {ocr_pages, ocr_eur, llm_calls, llm_tokens}
    claimed_total_context jsonb,                   -- accounting {leaf_claimed, leaf_position}

    source_modified   timestamptz,
    trusted_published timestamptz,
    discovered_at     timestamptz NOT NULL DEFAULT now(),
    fetched_at        timestamptz,
    processed_at      timestamptz,
    indexed_at        timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now(),

    UNIQUE (source_id, natural_key)
);

CREATE INDEX items_stage_disposition ON items (stage, disposition);
CREATE INDEX items_source ON items (source_id);
CREATE INDEX items_trusted_published ON items (trusted_published);

CREATE TABLE item_urls (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    urn        text NOT NULL REFERENCES items(urn) ON DELETE CASCADE,
    url        text NOT NULL,
    role       text NOT NULL,                      -- landing | file | api_detail | …
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (urn, url)
);

-- Edges: typed target_ref + nullable resolved_urn (envelope-desk-checks.md Q3).
-- Dangling edges (resolved_urn IS NULL) are a normal state, not an error.
CREATE TABLE edges (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_urn    text NOT NULL REFERENCES items(urn) ON DELETE CASCADE,
    type          text NOT NULL REFERENCES vocab_edge_type(name),
    target_scheme text NOT NULL,                   -- urn | group_key | ecli | op_id | …
    target_value  text NOT NULL,
    resolved_urn  text REFERENCES items(urn),
    established   text NOT NULL,                   -- crawl | index | enrichment
    confidence    real,
    via           text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (type, source_urn, target_scheme, target_value)
);

CREATE INDEX edges_unresolved ON edges (target_scheme, target_value) WHERE resolved_urn IS NULL;

-- ---------------------------------------------------------------- acquisition & content

CREATE TABLE blobs (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    urn                text NOT NULL REFERENCES items(urn) ON DELETE CASCADE,
    url                text,
    sniffed_type       text,                       -- by magic bytes / Content-Type, never URL extension
    bytes              bigint,
    pages              int,
    validated_magic    boolean,
    validated_eof      boolean,
    validated_min_size boolean,
    content_hash       text,
    kept               boolean NOT NULL DEFAULT false,
    kept_reason        text REFERENCES vocab_kept_reason(name),
    storage_ref        text,                       -- path under data/blobs/, NULL once deleted
    fetched_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (urn, url)
);

CREATE TABLE extractions (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    urn             text NOT NULL REFERENCES items(urn) ON DELETE CASCADE,
    method          text NOT NULL,                 -- native | ocr_full | ocr_partial | llm_extract | structured
    engine          text NOT NULL,
    engine_version  text,
    confidence      real,
    pages_processed int,
    text_ref        text,                          -- path under data/texts/
    raw_llm_json_ref text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (urn, method, engine)                   -- reprocess = replace, never accumulate
);

CREATE TABLE enrichments (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    urn              text NOT NULL REFERENCES items(urn) ON DELETE CASCADE,
    fields           jsonb NOT NULL DEFAULT '{}',
    enricher_version text NOT NULL,                -- derived artifacts carry versions; ground truth does not
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (urn, enricher_version)
);

-- ---------------------------------------------------------------- deferred work & accounting

CREATE TABLE deferred_queue (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    urn         text NOT NULL REFERENCES items(urn) ON DELETE CASCADE,
    reason      text NOT NULL REFERENCES vocab_deferred_reason(name),
    payload     jsonb NOT NULL DEFAULT '{}',
    queued_at   timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    UNIQUE (urn, reason)
);

CREATE INDEX deferred_open ON deferred_queue (reason) WHERE resolved_at IS NULL;

CREATE TABLE cost_ledger (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     bigint REFERENCES runs(id),
    source_id  text REFERENCES sources(id),
    category   text NOT NULL,                      -- requests | bytes | ocr_pages | llm_tokens | budget_stop
    amount     numeric NOT NULL DEFAULT 0,
    eur        numeric NOT NULL DEFAULT 0,
    note       text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- fixtures (regression method)

CREATE TABLE fixtures (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   text NOT NULL REFERENCES sources(id),
    name        text NOT NULL,
    kind        text NOT NULL,                     -- json | html | pdf
    path        text NOT NULL,                     -- under tests/fixtures/
    meta        jsonb NOT NULL DEFAULT '{}',
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, name)
);

-- ---------------------------------------------------------------- entity records (created-but-unused; EntityRecord seam)

CREATE TABLE entity_records (
    urn        text PRIMARY KEY,
    source_id  text NOT NULL REFERENCES sources(id),
    natural_key text NOT NULL,
    fields     jsonb NOT NULL DEFAULT '{}',
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, natural_key)
);

CREATE TABLE entity_deltas (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_urn text NOT NULL REFERENCES entity_records(urn) ON DELETE CASCADE,
    run_id     bigint REFERENCES runs(id),
    delta      jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
