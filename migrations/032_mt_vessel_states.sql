-- 032_mt_vessel_states.sql
-- migrate:no-transaction
--
-- MarineTraffic archival layer — a DEDICATED sibling of vessel_states, keyed on
-- MarineTraffic's own natural id. This is the mature scraplan pattern for a new
-- source whose identity differs from the existing one: a dedicated table keyed
-- on THAT source's natural key, with its own provenance — exactly as each
-- derived layer (vessel_identity, satellite_info/orbit/…) got its own table
-- rather than being forced onto a foreign key shape.
--
-- WHY NOT vessel_states. vessel_states' natural key is (mmsi, ts, source) with
-- mmsi NOT NULL — a frozen contract. MarineTraffic's tile feed keys on its
-- internal SHIP_ID and carries NO mmsi for the vast majority of vessels, so it
-- cannot be keyed there without either (a) faking an mmsi (a hack that would
-- corrupt mmsi semantics and break mmsi_to_flag / vessel_identity), or (b)
-- mutating the frozen key. Neither is acceptable. A source-native table keeps
-- both identity spaces honest; they are bridged by vessel_xref (migration 033)
-- whenever a real mmsi becomes known.
--
-- STRICTLY ADDITIVE: a NEW table only. vessel_states, its (mmsi, ts, source)
-- key, vessel_snapshots, vessel_identity and every other object are UNTOUCHED.
-- The per-source count-over-time series REUSES vessel_snapshots (its PK is
-- (snapshot_time, source) precisely so multiple sources coexist) with
-- source='marinetraffic' — so no second snapshot table is needed.
--
-- SEMANTICS mirror vessel_states exactly (speed in KNOTS, nulls = "not
-- reported" never zero-filled, identity carried on every row because the Fleet
-- has merged the latest static data before archival) EXCEPT the key:
--   * ship_id     — MarineTraffic internal vessel id (the natural key).
--   * mmsi         — NULLABLE enrichment: filled only on the rare rows where
--                    MarineTraffic actually carries one; the link lives in
--                    vessel_xref. Never invented.
--   * flag         — MarineTraffic-REPORTED flag state (2-letter code). Distinct
--                    from the AIS layer's MID-derived vessel_identity.flag_country;
--                    here it is a free, direct signal even without an mmsi.
--   * geo columns  — country / near_country / distance_km / geo_source are the
--                    SAME scraplan classifier output as migration 021 (12 NM
--                    territorial-sea test, AEQD projected distance, geo_source
--                    'scraplan-aoi'), computed in maritime.db, so an AIS×MT
--                    cross-view joins on identical geography.
--
-- Reversal (migrations are forward-only): DROP TABLE mt_vessel_states.

CREATE TABLE mt_vessel_states (
    ts              TIMESTAMPTZ NOT NULL,
    ship_id         TEXT        NOT NULL,
    source          TEXT        NOT NULL DEFAULT 'marinetraffic',
    data_origin     TEXT        NOT NULL DEFAULT 'terrestrial',
    mmsi            TEXT,                               -- enrichment only (usually NULL); link in vessel_xref
    -- position
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    speed           DOUBLE PRECISION,   -- SOG, KNOTS (same unit as vessel_states)
    course          DOUBLE PRECISION,   -- COG, degrees true
    heading         DOUBLE PRECISION,   -- degrees true
    nav_status      SMALLINT,           -- not in the MT tile feed today; kept for parity
    -- static identity (carried per-row, as in vessel_states)
    imo             TEXT,
    name            TEXT,
    callsign        TEXT,
    ship_type_code  SMALLINT,
    ship_type       TEXT,
    destination     TEXT,
    draught         DOUBLE PRECISION,   -- meters
    length_m        DOUBLE PRECISION,   -- MarineTraffic reports length per row
    beam_m          DOUBLE PRECISION,   -- MarineTraffic reports width per row
    flag            TEXT,               -- MarineTraffic-reported flag (2-letter)
    -- geo classification (migration 021 parity — scraplan's ONE shared classifier)
    country         TEXT,               -- territorial-sea country (<=12 NM), else NULL
    near_country    TEXT,               -- nearest coast when OUTSIDE every territorial sea
    distance_km     DOUBLE PRECISION,   -- true km to nearest coast (AEQD projected)
    geo_source      TEXT,               -- classifier identity: 'scraplan-aoi'
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mt_vessel_states_pkey PRIMARY KEY (ship_id, ts, source)
);

-- Hypertable on the fix time, 1-day chunks — identical cadence to vessel_states.
SELECT create_hypertable('mt_vessel_states', 'ts', chunk_time_interval => INTERVAL '1 day');

-- Compression segments by (ship_id, source) so per-vessel and per-source track
-- queries stay efficient on compressed chunks — mirrors vessel_states' setup.
ALTER TABLE mt_vessel_states SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ship_id, source',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('mt_vessel_states', INTERVAL '7 days');

-- Query dimensions: reverse lookup by an enriched mmsi, and per-flag / per-geo
-- distributions — the partial index keeps the (usually NULL) mmsi index tiny.
CREATE INDEX mt_vessel_states_mmsi_idx ON mt_vessel_states (mmsi, ts DESC)
    WHERE mmsi IS NOT NULL;
CREATE INDEX mt_vessel_states_country_idx ON mt_vessel_states (country, ts DESC)
    WHERE country IS NOT NULL;
