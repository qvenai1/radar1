-- 048_first_seen_unbackfill.sql
--
-- ⛔ THIS FIXES A DEFECT IN MIGRATION 047, WHICH 047'S OWN COMMENT DESCRIBED AND THEN
-- SHIPPED. That file spends a paragraph rejecting a backfill:
--
--     "`first_seen_at = computed_at` — claims every historical event was first seen at its
--      LAST recompute ... The whole archive would then read as first-seen-today, and the
--      first 'what's new since I last looked' query after this migration would return the
--      entire history as new. The defect, wearing the fix's clothes."
--
-- It then wrote `ADD COLUMN first_seen_at TIMESTAMPTZ DEFAULT now()` and produced exactly
-- that outcome — not via `computed_at`, but via the DEFAULT itself.
--
-- ⛔ THE POSTGRES BEHAVIOUR THAT WAS MISREAD. Since PostgreSQL 11, `ADD COLUMN ... DEFAULT
-- <expr>` does NOT leave existing rows NULL. The expression is evaluated once and applied to
-- every pre-existing row (stored as a fast "missing value" attribute, but it reads back as
-- the value). The intended two-step idiom is:
--
--     ALTER TABLE t ADD COLUMN c TIMESTAMPTZ;             -- existing rows: NULL
--     ALTER TABLE t ALTER COLUMN c SET DEFAULT now();     -- future INSERTs only
--
-- ⚠ 047 IS NOT EDITED. The runner records each file's sha256 and refuses to continue if an
-- applied file changes ("schema drift is an error, never silently accepted" — migrate.py).
-- The repair is a new migration, and 047's reasoning stays on the record beside the mistake
-- it failed to prevent.
--
-- ---------------------------------------------------------------------------------------
-- MEASURED BEFORE WRITING THIS, on the live database
-- ---------------------------------------------------------------------------------------
--   SELECT first_seen_at, count(*) FROM maritime_events GROUP BY 1;
--     2026-08-23 10:01:43.665018+00  x 72,148      -- ONE distinct value, the whole table
--   schema_migrations.applied_at for 047:
--     2026-08-23 10:01:43.665018+00                -- byte-identical
--   rows with first_seen_at > applied_at: 0        -- no genuine insert has landed yet
--
-- So the backfilled set is exactly "first_seen_at = 047's applied_at", and no real
-- first-sighting can be inside it. The window between the two migrations is minutes; a
-- detector tick landing in it would be nulled, which costs one tick of first-sightings and
-- is the conservative direction (unknown, never falsely "new").
--
-- ---------------------------------------------------------------------------------------
-- ⛔ THE PREDICATE READS THE MIGRATION LOG, IT DOES NOT HARD-CODE A TIMESTAMP. A literal
-- would be unverifiable at review time and unsafe if this file were ever replayed against a
-- database where 047 ran at a different instant. `=` (not `<=`) is deliberate: only the
-- single instant the DEFAULT stamped can be a backfill artefact. Anything later is a real
-- insert and must survive.
-- ⚠ Idempotent: after the first run no row matches, so a replay is a no-op.
--
-- The DEFAULT stays on the column — it is correct for INSERTs and was only ever wrong as a
-- retroactive fill. `computed_at` and every other column are untouched; no row is deleted.
-- Reversal: none possible or wanted — the values being removed were never true.
-- ---------------------------------------------------------------------------------------

UPDATE maritime_events me
   SET first_seen_at = NULL
  FROM schema_migrations sm
 WHERE sm.filename = '047_maritime_events_first_seen.sql'
   AND me.first_seen_at = sm.applied_at;

-- ⛔ FAIL CLOSED. If the archive is somehow still fully stamped after the UPDATE, the
-- predicate did not match what was measured and the "what's new" axis would silently claim
-- 72,148 new events. Abort the transaction rather than record a repair that did not happen.
DO $$
DECLARE stamped BIGINT; total BIGINT;
BEGIN
    SELECT count(*) FILTER (WHERE first_seen_at IS NOT NULL), count(*)
      INTO stamped, total FROM maritime_events;
    IF total > 0 AND stamped = total THEN
        RAISE EXCEPTION
            'first_seen_at un-backfill did not take: % of % rows still stamped. '
            'The backfilled value no longer equals 047''s applied_at — re-measure before retrying.',
            stamped, total;
    END IF;
    RAISE NOTICE 'first_seen_at: % of % rows carry a genuine first sighting', stamped, total;
END $$;
