-- 013_registration_country.sql
--
-- Enrichment layer E1: strict COUNTRY OF REGISTRATION, derived locally from the
-- icao24 hex via the official ICAO 24-bit address allocation blocks
-- (backend/icao_country.py + backend/data/icao24_allocations.json). This is a
-- deterministic reference fact, NOT an inference and NOT an external API call.
--
-- Distinct from the existing aircraft_info.operator_country (the registered
-- OWNER's country, from adsbdb): a plane can be owned in one country and
-- registered/address-allocated in another. registration_country is an ADDITIONAL,
-- additive fact alongside it — the on-click adsbdb/planespotters enrichment and
-- operator_country are untouched.
--
-- STRICTLY ADDITIVE: nullable columns on the aircraft_info cache table only.
-- state_vectors and every natural key are UNTOUCHED. The aircraft_info primary
-- key (icao24) is unchanged. Because this fact is locally DERIVED (not fetched),
-- it carries its OWN provenance columns rather than reusing fetched_at (which
-- tracks the adsbdb/planespotters fetch):
--   * registration_country_source  — the derivation source label
--     (backend.icao_country.REGISTRATION_COUNTRY_SOURCE = 'icao-24bit-allocation')
--   * registration_country_derived_at — when the derivation ran
--
-- Reversal (migrations are forward-only): DROP COLUMN registration_country,
-- registration_country_source, registration_country_derived_at.

ALTER TABLE aircraft_info
    ADD COLUMN registration_country            TEXT,
    ADD COLUMN registration_country_source     TEXT,
    ADD COLUMN registration_country_derived_at TIMESTAMPTZ;
