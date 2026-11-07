-- 005_compression.sql
--
-- Native TimescaleDB compression for state_vectors: chunks older than 7 days
-- are compressed automatically (columnar, typically ~10x smaller). Recent
-- data stays row-oriented for fast ingest/queries.
--
-- segmentby=icao24 keeps per-aircraft history queries (the /history endpoint,
-- PK-indexed by icao24) efficient on compressed chunks; orderby ts DESC
-- matches the read pattern.
--
-- Documented caveat: backfilling data older than 7 days (e.g. rebuilding
-- from rotated archives) writes INTO compressed chunks — supported, but
-- slower than fresh-chunk ingest. For a full from-scratch rebuild, restore
-- speed by decompressing first or dropping/recreating the DB.

ALTER TABLE state_vectors SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'icao24',
    timescaledb.compress_orderby = 'ts DESC'
);

SELECT add_compression_policy('state_vectors', INTERVAL '7 days');
