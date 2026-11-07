-- 007_vessel_states.sql
-- migrate:no-transaction
--
-- Maritime archival layer — the sea-going parallel of state_vectors, built
-- to the SAME pattern established by migration 006: `source` is part of the
-- natural key so multiple AIS feeds (aisstream now; gfw/satellite later)
-- can never collide on the same (vessel, instant).
--
-- Difference from aviation, and why it drives the schema:
--   * ADS-B is polled as whole snapshots keyed on a shared snapshot_time;
--     AIS is a per-vessel event stream, so each vessel carries its OWN fix
--     time. The natural key is therefore (mmsi, ts, source) where ts is the
--     position-fix timestamp — the exact analogue of (icao24, ts, source).
--   * speed is stored in KNOTS (AIS-native), NOT normalized to m/s. The
--     aviation layer is metric because ADS-B is; AIS is knots by nature and
--     the frontend maritime layer will format from knots.
--
-- vessel_snapshots records one row per collector snapshot (a periodic
-- materialization of the live fleet): the live vessel COUNT at that instant.
-- Distinct-vessel counts can't live in a continuous aggregate (count(DISTINCT)
-- is unsupported there — same limitation noted for aviation), so the
-- count-over-time series comes from this small table, exactly as aviation's
-- per-snapshot counts come from snapshots.count rather than the 1m cagg.

CREATE TABLE vessel_snapshots (
    snapshot_time BIGINT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'aisstream',
    ts            TIMESTAMPTZ NOT NULL,
    lamin         DOUBLE PRECISION,
    lomin         DOUBLE PRECISION,
    lamax         DOUBLE PRECISION,
    lomax         DOUBLE PRECISION,
    count         INTEGER NOT NULL DEFAULT 0,
    generated_at  TIMESTAMPTZ,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT vessel_snapshots_pkey PRIMARY KEY (snapshot_time, source),
    CONSTRAINT vessel_snapshots_ts_matches_epoch CHECK (ts = to_timestamp(snapshot_time))
);
CREATE INDEX vessel_snapshots_src_ts_idx ON vessel_snapshots (source, ts DESC);

-- One row per (vessel, position-fix, source). Nulls mean "not reported" —
-- never zero-filled (project-wide rule). Identity fields (name/imo/type) are
-- carried on every row because the collector's Fleet has already merged the
-- latest ShipStaticData into each archived position before it reaches here.
CREATE TABLE vessel_states (
    ts              TIMESTAMPTZ NOT NULL,
    mmsi            TEXT        NOT NULL,
    source          TEXT        NOT NULL DEFAULT 'aisstream',
    data_origin     TEXT        NOT NULL DEFAULT 'terrestrial',
    -- position
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    speed           DOUBLE PRECISION,   -- SOG, KNOTS (AIS-native)
    course          DOUBLE PRECISION,   -- COG, degrees true
    heading         DOUBLE PRECISION,   -- degrees true
    nav_status      SMALLINT,
    -- static identity
    imo             TEXT,
    name            TEXT,
    callsign        TEXT,
    ship_type_code  SMALLINT,
    ship_type       TEXT,
    destination     TEXT,
    draught         DOUBLE PRECISION,   -- meters
    CONSTRAINT vessel_states_pkey PRIMARY KEY (mmsi, ts, source)
);

-- Hypertable on the fix time, 1-day chunks (same cadence choice as aviation).
SELECT create_hypertable('vessel_states', 'ts', chunk_time_interval => INTERVAL '1 day');

-- Compression segments by (mmsi, source) so per-vessel and per-source track
-- queries stay efficient on compressed chunks — mirrors 006's aviation setup.
ALTER TABLE vessel_states SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'mmsi, source',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('vessel_states', INTERVAL '7 days');
