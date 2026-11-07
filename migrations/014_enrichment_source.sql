-- 014_enrichment_source.sql
--
-- Enrichment layer E2: per-row PROVENANCE for the airframe reference facts
-- (registration / aircraft_type / icao_type / manufacturer / operator) on the
-- aircraft_info cache, because those facts can now come from TWO layers:
--   * the interactive on-click enricher (adsbdb, live) — marks the row found=true;
--   * this bulk offline layer (OpenSky aircraftDatabase.csv snapshot).
--
-- Precedence (documented, enforced in the E2 backfill, NOT here): adsbdb (live)
-- outranks the CSV snapshot for the same field. E2 fills ONLY rows the
-- interactive path has not enriched (found=false — E1-only or negative-cache
-- rows); it NEVER overwrites a found=true adsbdb row. So the effective source of
-- a row's airframe facts is: found=true => adsbdb (interactive); found=false with
-- source='opensky_csv' => this layer; found=false with NULL facts => unfilled
-- (an E3 target).
--
-- STRICTLY ADDITIVE: nullable columns on aircraft_info only. state_vectors and
-- every natural key are UNTOUCHED; the aircraft_info primary key (icao24) is
-- unchanged. E1's registration_country* columns are independent and untouched.
--   * source            — origin label of the airframe facts (e.g. 'opensky_csv')
--   * csv_snapshot_date  — the CSV snapshot version (last-modified date of the
--                          dump), so analysis knows how OLD a CSV-sourced fact is
--   * source_updated_at  — when this layer last wrote the row's facts (distinct
--                          from fetched_at, which is the adsbdb/planespotters
--                          fetch time managed by the interactive enricher)
--
-- Reversal (forward-only migrations): DROP COLUMN source, csv_snapshot_date,
-- source_updated_at.

ALTER TABLE aircraft_info
    ADD COLUMN source            TEXT,
    ADD COLUMN csv_snapshot_date DATE,
    ADD COLUMN source_updated_at TIMESTAMPTZ;
