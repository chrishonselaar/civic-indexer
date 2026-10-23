-- 0004_suppressions.sql — URN-level suppression list (POL-1 §5; failure-modes rule 5).
--
-- manifests/vlaardingen_raad.yaml policy.takedown promised "Engine-side: URN-level
-- suppression list" while `grep -ri "suppress|takedown" engine/ migrations/` returned
-- nothing. That is the exact shape rule 5 names: a prompt-level obligation with no
-- mechanical check is not a control. This table is the check.
--
-- No foreign key to items(urn). A removal request can arrive for a URN that has not
-- been discovered yet, and a ledger row can be rebuilt. The suppression has to outlive
-- and precede the item it names — otherwise the next discover run silently re-publishes
-- exactly what someone asked to have taken down.
--
-- source_id is a NULLABLE CACHE, not an identity, and it is NOT derived from the urn.
-- Measured 2026-07-27: the urn prefix is the *provider* namespace, not the dataset —
--
--     source_id        urn prefix
--     haarlem_raad     notubiz
--     vlaardingen_raad notubiz      <- same prefix, two different datasets
--     tuchtrecht       tuchtrecht
--     woo_besluiten    woo_besluiten
--
-- so `split_part(urn, ':', 1)` names the wrong thing for half the fleet, and cannot
-- disambiguate the Notubiz pair at all. The authority is items.source_id; this column
-- only records it when the item is already ledgered, and readers join items anyway.

CREATE TABLE suppressions (
    urn          text PRIMARY KEY,          -- as ledgered in items.urn
    source_id    text,                      -- cache of items.source_id; NULL if not yet discovered
    reason       text NOT NULL,             -- why: an unexplained suppression is unreviewable
    requested_by text,                      -- who asked, when known
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX suppressions_source_idx ON suppressions (source_id);
