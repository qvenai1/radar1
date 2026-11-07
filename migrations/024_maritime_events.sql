-- 024_maritime_events.sql
--
-- Behavioural-analysis layer — ONE generalized events table for ALL maritime
-- behavioural patterns, mirroring signal_events' typed-columns + JSONB-detail
-- shape. A1 (ais_gap) is the first event_type; rendezvous / identity_change /
-- anomaly are later types in the SAME table, so a new pattern is a new
-- event_type + detail shape, never a new table/migration.
--
-- Why one table (not per-pattern vessel_gaps): the behavioural patterns share
-- the shape — a subject vessel, a time window, a location, a classification, and
-- provenance. A shared table gives one query surface (by type / vessel / time /
-- classification) and a JSONB tail for pattern specifics. IMO on the row (from
-- the M2b identity spine) means events survive re-flagging and join across the
-- identity/flag/geo layers.
--
-- CLASSIFICATION is first-class and HONEST: a gap is a 'candidate' ONLY after
-- the three confounds are removed (feed outage, stationary/dedup, coverage
-- edge). All other gaps are STORED with their reason ('outage-excluded' /
-- 'stationary' / 'edge'), never discarded — transparency. A 'candidate' is an
-- INVESTIGATION LEAD, not proven intent (surfaced with that caveat).
--
-- RECOMPUTE-AND-REPLACE KEY: PRIMARY KEY (event_type, mmsi, start_ts). A gap is
-- uniquely identified by its vessel and the last-seen fix time before it, so a
-- re-detection over the same history UPSERTs the same row (replace, never
-- duplicate) — and an OPEN gap (end_ts NULL) closing later updates in place.
-- Idempotent; the detector uses ON CONFLICT DO UPDATE.
--
-- Additive: a NEW table only. vessel_states / vessel_identity / signal_events
-- and every key are UNTOUCHED. No PostGIS (lat/lon scalars, same as every layer).
-- Reversal (forward-only): DROP TABLE maritime_events.

CREATE TABLE maritime_events (
    event_type     TEXT        NOT NULL,               -- 'ais_gap' (rendezvous/identity_change/... later)
    mmsi           TEXT        NOT NULL,               -- subject vessel (AIS-native key)
    start_ts       TIMESTAMPTZ NOT NULL,               -- last fix before the event (gap start)
    end_ts         TIMESTAMPTZ,                        -- first fix after (NULL = open/ongoing)
    imo            TEXT,                               -- durable anchor via the M2b spine (nullable, honest)
    start_lat      DOUBLE PRECISION,
    start_lon      DOUBLE PRECISION,
    end_lat        DOUBLE PRECISION,
    end_lon        DOUBLE PRECISION,
    duration_s     DOUBLE PRECISION,                   -- end_ts - start_ts (NULL while open)
    classification TEXT        NOT NULL,               -- candidate | outage-excluded | edge | stationary
    method         TEXT        NOT NULL,               -- provenance, e.g. 'ais-gap-detector-v1'
    detail         JSONB       NOT NULL DEFAULT '{}',  -- pattern specifics (see below)
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT maritime_events_pkey PRIMARY KEY (event_type, mmsi, start_ts),
    CONSTRAINT maritime_events_class_ck
        CHECK (classification IN ('candidate', 'outage-excluded', 'edge', 'stationary'))
);

-- ais_gap detail carries: distance_at_start_km, nav_status, speed_at_last_fix,
-- coverage_cell_density, start_country / start_near_country (M1 — whose waters it
-- went dark in). These are the confound-removal evidence + the intel context.

-- Query dimensions: candidates by type + classification, newest-first; and the
-- IMO cross-reference for joining across the identity spine / future patterns.
CREATE INDEX maritime_events_type_class_idx
    ON maritime_events (event_type, classification, start_ts DESC);
CREATE INDEX maritime_events_imo_idx ON maritime_events (imo);
