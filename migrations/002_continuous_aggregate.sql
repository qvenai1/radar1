-- 002_continuous_aggregate.sql
-- migrate:no-transaction
--
-- Per-minute rollups for dashboard stats, so /api/v1/stats never recomputes
-- from raw rows. CREATE MATERIALIZED VIEW .. WITH (timescaledb.continuous)
-- cannot run inside a transaction — hence the no-transaction directive; the
-- runner executes each statement of this file individually.
--
-- Note: count(DISTINCT ..) is not supported in continuous aggregates, so
-- distinct-aircraft-per-bucket is intentionally absent. Per-snapshot aircraft
-- counts live in snapshots.count (a small, plain table that is cheap to
-- bucket at query time).

CREATE MATERIALIZED VIEW state_vectors_1m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', ts)                 AS bucket,
    count(*)                                    AS state_count,
    sum(CASE WHEN on_ground THEN 1 ELSE 0 END)  AS ground_count,
    max(baro_altitude)                          AS max_baro_altitude,
    max(geo_altitude)                           AS max_geo_altitude,
    max(velocity)                               AS max_velocity
FROM state_vectors
GROUP BY bucket
WITH NO DATA;

-- Real-time aggregation: buckets newer than the last materialization are
-- computed on the fly, so stats are never stale by more than the raw data.
ALTER MATERIALIZED VIEW state_vectors_1m SET (timescaledb.materialized_only = false);

-- Keep the recent window materialized automatically. Historical backfill is
-- covered by an explicit refresh_continuous_aggregate() call at the end of
-- every backfill run (see ingest.py).
SELECT add_continuous_aggregate_policy('state_vectors_1m',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');
