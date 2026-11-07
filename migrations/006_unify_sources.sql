-- 006_unify_sources.sql
-- migrate:no-transaction
--
-- UNIFY the archival layer across data sources (OpenSky + airplanes.live
-- military). The natural key gains `source`, making a same-(icao24, second)
-- collision between two feeds structurally impossible instead of a silent
-- ON-CONFLICT race. state_vectors also gains the airframe attributes the
-- military feed carries (actype/acdesc/reg), nullable for OpenSky.
--
-- Approach: this migration REBUILDS the derived index layer (state_vectors,
-- its 1-minute continuous aggregate, and snapshots) rather than performing
-- fragile in-place PK/compression surgery on a live compressed hypertable.
-- That is safe and intentional: the JSONL archive is ground truth and
-- ingest.py re-backfills both sources; /api/v1/live keeps serving from
-- latest.json during the rebuild. On a fresh DB this supersedes the
-- state_vectors / cagg / compression objects created in 001, 002 and 005.
--
-- The continuous aggregate becomes source-aware (GROUP BY bucket, source)
-- so /api/v1/stats can keep returning OpenSky-only numbers by default and
-- serve military rollups on demand — the frozen v1 contract is preserved.

DROP MATERIALIZED VIEW IF EXISTS state_vectors_1m CASCADE;
DROP TABLE IF EXISTS state_vectors CASCADE;
DROP TABLE IF EXISTS snapshots CASCADE;

-- One row per distinct (snapshot_time, source). bbox nullable as before.
CREATE TABLE snapshots (
    snapshot_time BIGINT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'opensky',
    ts            TIMESTAMPTZ NOT NULL,
    lamin         DOUBLE PRECISION,
    lomin         DOUBLE PRECISION,
    lamax         DOUBLE PRECISION,
    lomax         DOUBLE PRECISION,
    count         INTEGER NOT NULL DEFAULT 0,
    complete      BOOLEAN NOT NULL DEFAULT false,
    generated_at  TIMESTAMPTZ,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT snapshots_pkey PRIMARY KEY (snapshot_time, source),
    CONSTRAINT snapshots_ts_matches_epoch CHECK (ts = to_timestamp(snapshot_time))
);
CREATE UNIQUE INDEX snapshots_src_ts_idx ON snapshots (source, ts DESC);
CREATE INDEX snapshots_complete_idx ON snapshots (source, snapshot_time DESC) WHERE complete;

-- Unified state-vector index layer.
CREATE TABLE state_vectors (
    ts              TIMESTAMPTZ NOT NULL,
    snapshot_time   BIGINT      NOT NULL,
    icao24          TEXT        NOT NULL,
    source          TEXT        NOT NULL DEFAULT 'opensky',
    callsign        TEXT        NOT NULL DEFAULT '',
    origin_country  TEXT        NOT NULL DEFAULT '',
    time_position   BIGINT,
    last_contact    BIGINT,
    longitude       DOUBLE PRECISION,
    latitude        DOUBLE PRECISION,
    baro_altitude   DOUBLE PRECISION,   -- meters
    geo_altitude    DOUBLE PRECISION,   -- meters
    on_ground       BOOLEAN     NOT NULL,
    velocity        DOUBLE PRECISION,   -- m/s
    true_track      DOUBLE PRECISION,   -- degrees
    vertical_rate   DOUBLE PRECISION,   -- m/s
    squawk          TEXT        NOT NULL DEFAULT '',
    spi             BOOLEAN     NOT NULL DEFAULT FALSE,
    position_source SMALLINT,
    category        SMALLINT,
    fetched_at      TIMESTAMPTZ,
    -- military-feed extras (nullable; NULL for OpenSky rows)
    actype          TEXT,
    acdesc          TEXT,
    reg             TEXT,
    CONSTRAINT state_vectors_pkey PRIMARY KEY (icao24, ts, source),
    CONSTRAINT state_vectors_ts_matches_epoch CHECK (ts = to_timestamp(snapshot_time))
);

SELECT create_hypertable('state_vectors', 'ts', chunk_time_interval => INTERVAL '1 day');

-- compression: segment by (icao24, source) so per-aircraft and per-source
-- history queries stay efficient on compressed chunks.
ALTER TABLE state_vectors SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'icao24, source',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('state_vectors', INTERVAL '7 days');

-- source-aware 1-minute rollups
CREATE MATERIALIZED VIEW state_vectors_1m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', ts)                 AS bucket,
    source,
    count(*)                                    AS state_count,
    sum(CASE WHEN on_ground THEN 1 ELSE 0 END)  AS ground_count,
    max(baro_altitude)                          AS max_baro_altitude,
    max(geo_altitude)                           AS max_geo_altitude,
    max(velocity)                               AS max_velocity
FROM state_vectors
GROUP BY bucket, source
WITH NO DATA;

ALTER MATERIALIZED VIEW state_vectors_1m SET (timescaledb.materialized_only = false);

SELECT add_continuous_aggregate_policy('state_vectors_1m',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');
