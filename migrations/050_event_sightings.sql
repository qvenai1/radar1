-- 050_event_sightings.sql
--
-- DASH-3 — «متى رأيناه أول مرّة؟» فعلياً هذه المرّة. Migrations 047/048 added
-- `maritime_events.first_seen_at` and could not keep it, and this file records why before it
-- fixes it.
--
-- ⛔ WHAT 047 GOT WRONG. It verified that every detector's `INSERT` uses an explicit column
-- list and that no `ON CONFLICT ... DO UPDATE SET` names the column — both true — and
-- concluded "zero detector changes needed". It never checked whether the detectors INSERT at
-- all. They do not, mostly: five of them DELETE first.
--
--     detect_ais_gaps.py:249       DELETE FROM maritime_events WHERE event_type='ais_gap'
--     detect_zone_dwell.py:220     DELETE ... AND source=$2 AND start_ts > now() - $1
--     detect_rendezvous.py:306     DELETE ...
--     detect_identity_change.py    DELETE ...
--     detect_gnss_anomaly.py       DELETE ...
--
-- Their own comment names it: "delete+insert discipline ... so a recompute leaves no stale
-- row". A deleted row comes back as a NEW row, the column DEFAULT fires again, and
-- `first_seen_at` becomes the time of the last detector run — the exact defect 047 was written
-- to remove, reached by a different route.
--
-- ⛔ MEASURED, NOT REASONED. 048 emptied the column at 10:03:42Z. The timers then ran and
-- re-stamped 71,995 rows:
--     10:10 x2,565   10:12 x116   10:15 x65,063   10:20 x72   10:25 x4,179
-- matching the fire times of scraplan-gaps / gnss / identity / mt-zone-dwell.
--
-- ⚠ AND THE TEST THAT PASSED WAS TESTING THE WRONG PATH. 047's detonation created a row and
-- re-UPSERTed it, proving the value survives a conflict. It does. The production path is
-- DELETE then INSERT, and it was never exercised — a check that only covers the assumed code
-- path is the failure mode this project has been bitten by repeatedly.
--
-- ---------------------------------------------------------------------------------------
-- THE FIX: AN IDENTITY THAT OUTLIVES THE RECOMPUTE
-- ---------------------------------------------------------------------------------------
-- A first-sighting cannot live in a table whose rows are periodically destroyed. It lives in
-- a SEPARATE table keyed by the event's stable identity, written once and never deleted — the
-- same shape PagerDuty's `dedup_key` and Alertmanager's label fingerprint have: a key that
-- survives the lifecycle of the thing it identifies.
--
-- ⛔ A TRIGGER, NOT SIX EDITED DETECTORS — and this time "no detector changes" is a property
-- of the mechanism rather than of six files that happen to agree today. A BEFORE INSERT
-- trigger fires on every insert path: the five detectors, the backfill, any future writer, and
-- any ad-hoc INSERT. Six coordinated edits would have to be repeated by whoever adds the
-- seventh detector, and 047 is the evidence that "verified across all current callers" is not
-- a guarantee.
--
-- ⚠ THE KEY IS RE-DERIVED, NOT READ. `maritime_events.vessel_key` is
-- GENERATED ALWAYS AS (COALESCE(mmsi, ship_id)) STORED (migration 043), and PostgreSQL
-- computes generated columns AFTER BEFORE-triggers run — so NEW.vessel_key is NULL inside this
-- trigger and MUST NOT be read. The same COALESCE is restated below.
-- ⛔ If 043's expression ever changes, this trigger must change with it, or sightings key on a
-- different identity than the events do and every row looks new again.
--
-- ⚠ EXISTING ROWS ARE NOT SEEDED. The archive's true first sighting is unknown and stays
-- unknown: no sighting row is created for it, so `first_seen_at` remains NULL — unknown
-- novelty, never "new" (047's rule, unchanged). Sightings begin accruing from this migration.
--
-- Additive: a new table, function and trigger. `maritime_events` keeps every column, key and
-- index; no row is rewritten by this file; the detectors are untouched.
-- Reversal: DROP TRIGGER maritime_events_sighting ON maritime_events;
--           DROP FUNCTION maritime_events_record_sighting();
--           DROP TABLE maritime_event_sightings;
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS maritime_event_sightings (
    event_type    TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    vessel_key    TEXT        NOT NULL,
    start_ts      TIMESTAMPTZ NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT maritime_event_sightings_pkey
        PRIMARY KEY (event_type, source, vessel_key, start_ts)
);

COMMENT ON TABLE maritime_event_sightings IS
    'When each maritime event was FIRST detected. Deliberately OUTSIDE maritime_events, whose '
    'rows are destroyed and rebuilt by the detectors'' delete+insert recompute (migration 050). '
    '⛔ Rows are inserted once and NEVER deleted or updated — deleting one resets an event''s '
    'novelty to "now" and reintroduces the defect this table exists to fix.';

CREATE OR REPLACE FUNCTION maritime_events_record_sighting() RETURNS trigger AS $fn$
DECLARE k TEXT;
BEGIN
    -- ⚠ NOT NEW.vessel_key: it is GENERATED and is still NULL at BEFORE INSERT (see header).
    k := COALESCE(NEW.mmsi, NEW.ship_id);
    IF k IS NULL THEN
        -- No identity to remember. Leave first_seen_at NULL rather than invent a key —
        -- an unkeyed sighting could never be matched again and would report "new" forever.
        RETURN NEW;
    END IF;

    INSERT INTO maritime_event_sightings (event_type, source, vessel_key, start_ts)
         VALUES (NEW.event_type, NEW.source, k, NEW.start_ts)
    ON CONFLICT ON CONSTRAINT maritime_event_sightings_pkey DO NOTHING;

    SELECT s.first_seen_at INTO NEW.first_seen_at
      FROM maritime_event_sightings s
     WHERE s.event_type = NEW.event_type AND s.source = NEW.source
       AND s.vessel_key = k             AND s.start_ts = NEW.start_ts;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ⚠ BEFORE INSERT ONLY. On UPDATE the stored value must not move, and an ON CONFLICT DO UPDATE
-- does not fire an INSERT trigger for the conflicting row — so a recompute that upserts leaves
-- first_seen_at exactly as it was, which is the required behaviour.
DROP TRIGGER IF EXISTS maritime_events_sighting ON maritime_events;
CREATE TRIGGER maritime_events_sighting
    BEFORE INSERT ON maritime_events
    FOR EACH ROW EXECUTE FUNCTION maritime_events_record_sighting();
