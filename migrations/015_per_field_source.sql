-- 015_per_field_source.sql
--
-- Enrichment layer E3: PER-FIELD provenance for the airframe reference facts on
-- aircraft_info. E2 recorded a single row-level `source`, but E3 (targeted
-- adsbdb gap-fill + refresh) updates facts PER FIELD — a refresh may overwrite
-- the CSV `operator` with a fresher adsbdb value while leaving a CSV
-- `manufacturer` that adsbdb did not return. A single row-level source cannot
-- express that, so each airframe fact gets its own source column:
--   registration_source, aircraft_type_source, icao_type_source,
--   manufacturer_source, operator_source
--
-- Precedence (evaluated per field, value-present-only, in the E3 backfill):
--   adsbdb (freshest) > opensky_csv (E2) > empty.
-- These columns are the authoritative per-field provenance going forward; E2's
-- coarse row-level `source` is retained but superseded by these for the airframe
-- facts. E1's registration_country* provenance is separate and untouched.
--
-- STRICTLY ADDITIVE: nullable columns on aircraft_info only. state_vectors and
-- every natural key are UNTOUCHED; the aircraft_info primary key (icao24) is
-- unchanged.
--
-- Data init: seed the per-field sources from the CURRENT row state so E3 starts
-- from correct provenance — a present field is attributed to E2's row `source`
-- ('opensky_csv') or, for interactively-enriched rows (found=true, no CSV
-- source), to 'adsbdb'. aircraft_info is a plain table (not a hypertable), so
-- this UPDATE is an ordinary heap update — no chunk/compression concerns.
--
-- Reversal (forward-only migrations): DROP COLUMN registration_source,
-- aircraft_type_source, icao_type_source, manufacturer_source, operator_source.

ALTER TABLE aircraft_info
    ADD COLUMN registration_source  TEXT,
    ADD COLUMN aircraft_type_source TEXT,
    ADD COLUMN icao_type_source     TEXT,
    ADD COLUMN manufacturer_source  TEXT,
    ADD COLUMN operator_source      TEXT;

UPDATE aircraft_info SET
    registration_source  = CASE WHEN registration  IS NOT NULL
        THEN COALESCE(NULLIF(source, ''), CASE WHEN found THEN 'adsbdb' END) END,
    aircraft_type_source = CASE WHEN aircraft_type IS NOT NULL
        THEN COALESCE(NULLIF(source, ''), CASE WHEN found THEN 'adsbdb' END) END,
    icao_type_source     = CASE WHEN icao_type     IS NOT NULL
        THEN COALESCE(NULLIF(source, ''), CASE WHEN found THEN 'adsbdb' END) END,
    manufacturer_source  = CASE WHEN manufacturer  IS NOT NULL
        THEN COALESCE(NULLIF(source, ''), CASE WHEN found THEN 'adsbdb' END) END,
    operator_source      = CASE WHEN operator      IS NOT NULL
        THEN COALESCE(NULLIF(source, ''), CASE WHEN found THEN 'adsbdb' END) END;
