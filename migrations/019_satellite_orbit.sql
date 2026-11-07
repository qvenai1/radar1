-- 019_satellite_orbit.sql
--
-- Satellite RESEARCH layer Sat-Orbit: DETERMINISTIC orbit classification COMPUTED
-- from the TLEs scraplan already holds (satellite_elements), via the reference
-- SGP4 implementation (python-sgp4). This is the LIVE, always-current orbital
-- complement to UCS's self-reported, FROZEN 2023 orbit fields — a study can keep
-- BOTH and compare the 2023 classification against the live-derived one.
--
-- COMPUTED, not sourced: every value here is derived from the two TLE lines by
-- standard orbital mechanics (backend/orbit_derive.py). Because it is
-- recomputable, this table is a CACHE OF THE LATEST DERIVATION — a refreshed TLE
-- (new epoch) recomputes and UPDATES the row (unlike the append-only
-- satellite_history). tle_epoch records exactly which TLE a row was computed from.
--
-- ORBIT-CLASS THRESHOLDS (documented + citable; see orbit_derive.py, evaluated
-- in order): HEO if ecc >= 0.25; else LEO if apogee < 2000 km; else GEO if
-- |mean altitude - 35786 km| <= 1500 km; else MEO if 2000 <= mean altitude <
-- 34286 km; else 'other' (above the GEO belt). FLAGS: is_geostationary = GEO +
-- inclination <= 1 deg + ecc <= 0.01 + period within 30 min of one sidereal day;
-- is_sun_synchronous computed ONLY for LEO (nodal precession matching the
-- mean-solar rate within 1 deg of the required inclination) — NULL for non-LEO
-- (not applicable, not guessed).
--
-- PROVENANCE: source='derived-sgp4'; tle_epoch (epoch of the source TLE);
-- computed_at (when the derivation ran). Distinct from UCS's orbit_class (2023,
-- self-reported) — this layer NEVER overwrites UCS; they coexist for comparison.
--
-- STRICTLY ADDITIVE: a new table only. satellite_elements / satellite_history /
-- satellite_info / satellite_strategic / satellite_reentry and their keys are
-- UNTOUCHED. No FK (derivation is independent of catalog membership). Reversal
-- (forward-only migrations): DROP TABLE satellite_orbit.

CREATE TABLE satellite_orbit (
    norad_id            INTEGER PRIMARY KEY,
    orbit_class         TEXT,               -- LEO | MEO | GEO | HEO | other
    apogee_km           DOUBLE PRECISION,   -- altitude above surface
    perigee_km          DOUBLE PRECISION,   -- altitude above surface
    eccentricity        DOUBLE PRECISION,
    inclination_deg     DOUBLE PRECISION,
    period_min          DOUBLE PRECISION,
    mean_motion         DOUBLE PRECISION,   -- revolutions per day
    semimajor_axis_km   DOUBLE PRECISION,
    is_geostationary    BOOLEAN,
    is_sun_synchronous  BOOLEAN,            -- NULL for non-LEO (not applicable)
    -- provenance
    source              TEXT NOT NULL DEFAULT 'derived-sgp4',
    tle_epoch           TIMESTAMPTZ,        -- epoch of the TLE this row was derived from
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX satellite_orbit_class_idx ON satellite_orbit (orbit_class);
