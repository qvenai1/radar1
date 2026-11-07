-- 012_geo_classification.sql
--
-- Phase P0 of the ADS-B Exchange source merge: ADDITIVE columns on
-- state_vectors to carry a per-position geographic classification + validation
-- provenance (produced by the adsb_scraper source, area.py: seven-country
-- INSIDE/NEAR/OUTSIDE + 50 km border buffer, and its validation layer).
--
-- STRICTLY ADDITIVE — final, not a patch:
--   * every column is NULLABLE with no default beyond NULL, so all existing
--     rows and all existing sources (opensky, airplanes_live) stay valid and
--     untouched, reading back as NULL;
--   * the natural key PRIMARY KEY (icao24, ts, source) is NOT touched, so
--     idempotency (ON CONFLICT DO NOTHING) is preserved exactly;
--   * the ts = to_timestamp(snapshot_time) CHECK is NOT touched.
--
-- These are plain scalar/array columns — NO PostGIS, NO geometry (scraplan has
-- no PostGIS and we are not adding it; the classification is computed upstream
-- by the source and archived here as its result).
--
-- Naming: `geo_source` documents WHICH feed classified the row (e.g.
-- 'adsbexchange'); it is deliberately distinct from the natural-key `source`
-- column so a future shared scraplan geo layer could classify an opensky row
-- and record geo_source separately from source. NULL on legacy rows.
--
-- ADD COLUMN on the hypertable is transactional and cheap (metadata-only for
-- nullable-no-default columns); current chunks are uncompressed. Reversal
-- (migrations are forward-only): DROP COLUMN country, near_country,
-- position_quality, validation_flags, distance_km, geo_source.

ALTER TABLE state_vectors
    ADD COLUMN country          TEXT,              -- seven-country INSIDE match, else NULL
    ADD COLUMN near_country      TEXT,              -- nearest country when NEAR the buffer, else NULL
    ADD COLUMN position_quality  TEXT,              -- 'confirmed' | 'no_position' | 'suspect'
    ADD COLUMN validation_flags  TEXT[],            -- native array of validation flag strings
    ADD COLUMN distance_km       DOUBLE PRECISION,  -- great-circle distance provenance
    ADD COLUMN geo_source        TEXT;              -- feed that classified the row (e.g. 'adsbexchange')
