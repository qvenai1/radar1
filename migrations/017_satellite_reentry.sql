-- 017_satellite_reentry.sql
--
-- Satellite layer S2c: DECAY/REENTRY predictions from Space-Track's TIP class
-- (Tracking and Impact Prediction), geo-linked to scraplan's seven-country AOI.
-- The reason reentry was chosen over conjunctions: TIP carries a predicted impact
-- LAT/LON, so each event can be classified against the region we care about.
--
-- SOURCE (verified against the LIVE Space-Track `tip` class schema, 2026-07-18):
--   NORAD_CAT_ID, MSG_EPOCH, INSERT_EPOCH, DECAY_EPOCH, WINDOW, REV, DIRECTION,
--   LAT, LON, INCL, NEXT_REPORT, ID, HIGH_INTEREST, OBJECT_NUMBER.
--   (The `decay` class carries DECAY_EPOCH + identity but NO lat/lon, so it can't
--   be geo-linked; TIP is the geo-bearing source. Object NAME/COUNTRY/TYPE are
--   NOT stored here — they come from the S1 join to satellite_info by norad_id.)
--
-- KEY CHOICE (idempotency + history), documented:
--   PRIMARY KEY = `id` (the TIP message ID, globally unique per message). Each
--   TIP message is ONE row. Re-fetching the same daily pull is idempotent
--   (ON CONFLICT (id) DO UPDATE with a change-guard -> 0 rows changed; a corrected
--   same-id message updates in place, never duplicates). When Space-Track issues
--   a REFINED prediction it is a NEW TIP message (new id) -> a new row, so the
--   sequence of predictions is PRESERVED (append pattern, the same discipline as
--   satellite_history preserving each TLE epoch). "Latest prediction per object"
--   is a QUERY concern (DISTINCT ON (norad_id) ORDER BY msg_epoch DESC), not a
--   storage overwrite. Low volume (TIP is issued only for imminent/high-interest
--   reentries) -> a plain table, like satellite_info; no hypertable needed.
--
-- GEO-LINKING: lat/lon is the NOMINAL predicted impact point, classified with
-- scraplan's shared geo_area.py (seven-country INSIDE/NEAR + 50 km buffer, the
-- same classifier aircraft use) -> country / near_country / distance_km /
-- geo_source. The raw predicted point is a SINGLE point with LARGE uncertainty:
-- window_min (± minutes around decay_epoch) and next_report are kept so the
-- imprecision is VISIBLE, not hidden. LON is stored normalized to [-180,180]
-- (Space-Track reports 0..360 E) — same point, standard convention.
--
-- STRICTLY ADDITIVE: a new table only. satellite_elements / satellite_history /
-- satellite_info (S1) and their keys are UNTOUCHED. No FK to satellite_info (a
-- reentering object may not be in the tracked/enriched set); identity is joined
-- at read time. Reversal (forward-only migrations): DROP TABLE satellite_reentry.

CREATE TABLE satellite_reentry (
    id             BIGINT PRIMARY KEY,        -- TIP message ID (idempotency key)
    norad_id       INTEGER NOT NULL,
    msg_epoch      TIMESTAMPTZ,               -- when the TIP message was issued
    insert_epoch   TIMESTAMPTZ,               -- Space-Track insert time
    decay_epoch    TIMESTAMPTZ,               -- predicted NOMINAL reentry time
    window_min     INTEGER,                   -- ± uncertainty window, minutes
    lat            DOUBLE PRECISION,          -- predicted nominal impact latitude
    lon            DOUBLE PRECISION,          -- predicted nominal impact longitude (-180..180)
    direction      TEXT,                      -- 'ascending' | 'descending'
    incl           DOUBLE PRECISION,          -- orbit inclination, degrees
    rev            INTEGER,                   -- revolution number
    next_report    INTEGER,                   -- raw NEXT_REPORT (0 = final)
    high_interest  BOOLEAN,                   -- HIGH_INTEREST flag
    -- geo classification of the nominal predicted point (shared geo_area.py)
    country        TEXT,                      -- seven-country INSIDE match, else NULL
    near_country   TEXT,                      -- nearest country when NEAR the buffer
    distance_km    DOUBLE PRECISION,          -- great-circle distance provenance
    geo_source     TEXT,                      -- 'scraplan-aoi'
    -- provenance
    source         TEXT NOT NULL DEFAULT 'spacetrack_tip',
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX satellite_reentry_norad_idx    ON satellite_reentry (norad_id, msg_epoch DESC);
CREATE INDEX satellite_reentry_decay_idx    ON satellite_reentry (decay_epoch DESC);
CREATE INDEX satellite_reentry_country_idx  ON satellite_reentry (country);
CREATE INDEX satellite_reentry_near_idx     ON satellite_reentry (near_country);
