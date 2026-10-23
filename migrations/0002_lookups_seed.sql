-- 0002_lookups_seed.sql — seed vocabulary lookup tables.
-- New values later = INSERT here (or at runtime), never ALTER TYPE.

BEGIN;

INSERT INTO vocab_stage (name, rank) VALUES
    ('discovered', 1),
    ('fetched',    2),
    ('extracted',  3),
    ('enriched',   4),
    ('indexed',    5);

INSERT INTO vocab_disposition (name, description) VALUES
    ('ok',               'processed normally'),
    ('partial',          'usable but incomplete (see missing[])'),
    ('deferred',         'intentionally postponed (see reason)'),
    ('failed',           'processing failed (retryable flag applies)'),
    ('stub',             'metadata-only record, no primary content at this source'),
    ('superseded',       'replaced by a newer version'),
    ('gone_from_source', 'no longer present at the source'),
    ('out_of_scope',     'discovered but outside the active scope predicate');

INSERT INTO vocab_edge_type (name, description) VALUES
    ('attachment_of',  'bijlage belongs to a besluit/document'),
    ('part_of_dossier','member of a dossier grouping'),
    ('appeal_of',      'appeal decision over an earlier decision'),
    ('supersedes',     'newer version replaces target'),
    ('rectifies',      'rectification of target'),
    ('responds_to',    'response to a request/document'),
    ('derived_from',   'derived artifact of target'),
    ('about_entity',   'document is about an entity record'),
    ('cites',          'citation of another document');

INSERT INTO vocab_missing_reason (name, description) VALUES
    ('fulltext',    'no text distillate yet'),
    ('attachments', 'attachments not yet acquired'),
    ('ocr_tail',    'scanned pages awaiting deferred OCR burst'),
    ('case_route',  'case-level route/context not yet resolved');

INSERT INTO vocab_kept_reason (name, description) VALUES
    ('ttl_source',           'source deletes originals; blob is the archive'),
    ('partial_ocr',          'kept for later OCR-tail processing'),
    ('zip_source',           'container kept for later member extraction'),
    ('deleted_after_distill','not kept: deleted once text distillate existed');

INSERT INTO vocab_deferred_reason (name, description) VALUES
    ('ocr_tail',     'awaiting deferred GPU-burst OCR'),
    ('zip_dossier',  'ZIP container; member extraction deferred'),
    ('out_of_scope', 'outside active scope predicate; metadata stub only'),
    ('budget',       'deferred by budget gate');

INSERT INTO vocab_run_mode (name) VALUES
    ('backfill'),
    ('incremental'),
    ('upgrade');

COMMIT;
