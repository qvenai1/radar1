-- 001_initial_schema.sql
-- Foundation: snapshots + state_vectors hypertable.
--
-- Design notes (see README-backend.md for the full rationale):
--   * ts (TIMESTAMPTZ) is the canonical time/partition column; snapshot_time
--     (BIGINT, unix seconds) is kept verbatim as received from OpenSky. The
--     CHECK constraint enforces ts = to_timestamp(snapshot_time) at the DB
--     level, so the two can never diverge.
--   * The natural key is (icao24, snapshot_time). TimescaleDB requires the
--     partition column in every unique index, so the PRIMARY KEY is
--     (icao24, ts) — equivalent, because ts <-> snapshot_time is a bijection
--     enforced by the CHECK constraint.
--   * Text fields that the collector guarantees non-null (callsign, squawk,
--     origin_country) are stored verbatim, empty string included — no silent
--     coercion of ground truth. Numeric telemetry (altitudes, velocity,
--     track, vertical_rate) is nullable: null means "not reported".

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- One row per distinct OpenSky snapshot. bbox is nullable: it is only known
-- when ingesting live (from latest.json) or when passed to the backfill CLI;
-- the raw JSONL rows do not carry it. count is maintained by ingestion as
-- SELECT count(*) FROM state_vectors WHERE ts = snapshots.ts — i.e. always
-- derived from actual stored rows, never trusted from the writer.
CREATE TABLE snapshots (
    snapshot_time BIGINT PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL,
    lamin         DOUBLE PRECISION,
    lomin         DOUBLE PRECISION,
    lamax         DOUBLE PRECISION,
    lomax         DOUBLE PRECISION,
    count         INTEGER NOT NULL DEFAULT 0,
    generated_at  TIMESTAMPTZ,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT snapshots_ts_matches_epoch CHECK (ts = to_timestamp(snapshot_time))
);

CREATE UNIQUE INDEX snapshots_ts_idx ON snapshots (ts DESC);

-- The state-vector archive index layer. Raw JSONL remains ground truth; this
-- table is a derived query index (idempotently rebuildable from the archive).
CREATE TABLE state_vectors (
    ts              TIMESTAMPTZ NOT NULL,
    snapshot_time   BIGINT      NOT NULL,
    icao24          TEXT        NOT NULL,
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
    -- Natural-key dedup enforced at the DB level (constraint #4 of the spec):
    CONSTRAINT state_vectors_pkey PRIMARY KEY (icao24, ts),
    CONSTRAINT state_vectors_ts_matches_epoch CHECK (ts = to_timestamp(snapshot_time))
);

-- Hypertable partitioned on time. 1-day chunks: at the current cadence
-- (~30s polls, hundreds of vectors each) a day is ~1M rows worst case —
-- comfortably inside the recommended chunk size at production volumes.
SELECT create_hypertable('state_vectors', 'ts', chunk_time_interval => INTERVAL '1 day');

-- Query patterns and their indexes:
--   * history (icao24 + time range)  -> PK (icao24, ts)
--   * replay / live (ts = X)         -> default hypertable index (ts DESC)
--   * stats (time buckets)           -> continuous aggregate (002)
--   * bbox / search filters run WITHIN one snapshot's rows (hundreds), after
--     the ts index has narrowed the scan — no extra index needed.
