-- migrate:no-transaction
--
-- D4 area-search — native-point GiST spatial index (D4-pre option c).
-- ============================================================================
-- "draw a box → everything that crossed it" needs a spatial index on the air
-- table: D4-pre measured an unindexed 0.5deg box at ~230 ms WARM but ~3.5 s COLD
-- (a full parallel seq-scan of the time-window), and a native-point GiST at
-- ~28 ms (index-seeks only the box rows, killing the cold cliff).
--
-- Built-in ONLY — no PostGIS, no image change, no new column: PostgreSQL's core
-- geometric types give `point`, `box`, the `<@` (point-in-box) operator, and the
-- `point_ops` GiST opclass. This is an EXPRESSION index on point(longitude,
-- latitude); the area query MUST use the matching
--   point(longitude, latitude) <@ box(point(lon1,lat1), point(lon2,lat2))
-- form to hit it (a lat/lon bounding box).
--
-- D4-pre rejected the alternatives by measurement: BRIN(lat,lon) is INEFFECTIVE
-- (lat/lon don't correlate with ts-append storage order -> pruned ~nothing, box
-- query got SLOWER at ~1036 ms); a thinned AOI-only layer misses the wider
-- collector bbox (only 11% of rows are AOI-classified) and brief transits;
-- PostGIS's polygon/geodesic power is unused by a rectangular box.
--
-- OPERATIONAL NOTE (honest): TimescaleDB HYPERTABLES DO NOT SUPPORT
-- `CREATE INDEX CONCURRENTLY` ("hypertables do not support concurrent index
-- creation"). So this is a LOCKING build (~17 s on the active ~9.7M-row chunk,
-- ~60-90 s total across chunks) that briefly stalls the collector's inserts to
-- the chunk being indexed. RUN DURING LOW INGESTION / a maintenance window.
-- It is NOT wrapped in a transaction (this file is -- migrate:no-transaction)
-- so each index commits as it finishes. TimescaleDB auto-creates the index on
-- new chunks, so this is a one-time build.

-- Air (state_vectors, ~35M) — the whole point: removes the cold seq-scan cliff.
CREATE INDEX IF NOT EXISTS state_vectors_geo_gist
    ON state_vectors USING gist (point(longitude, latitude));

-- Sea (vessel_states, ~690k) — the mirror. D4-pre flagged this OPTIONAL (the sea
-- box is already ~165 ms un-indexed). Taken: yes — cheap to build, makes sea
-- cold-safe too, and keeps the area query one code path across domains.
CREATE INDEX IF NOT EXISTS vessel_states_geo_gist
    ON vessel_states USING gist (point(longitude, latitude));
