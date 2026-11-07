-- 020_satellite_passes.sql
--
-- Satellite RESEARCH layer Sat-Passes: the REGIONAL CAPSTONE. Which tracked
-- satellites pass over the seven-country AOI, and when — computed deterministically
-- from the current TLEs (python-sgp4), joinable to identity (S1), strategic class
-- (Sat-UCS), and orbit (Sat-Orbit) by norad_id. This is the layer the whole base
-- was built to enable: "which MILITARY satellites overfly the region in the next
-- 24 h", etc.
--
-- PASS DEFINITION (documented, observer-independent, reproducible — see
-- backend/passes_derive.py): a pass is a contiguous interval where the satellite's
-- SUB-SATELLITE GROUND-TRACK POINT falls INSIDE a seven-country polygon OR NEAR it
-- (geo_area.py's 50 km border buffer — the SAME classifier every other geo layer
-- uses). Sub-point = SGP4 TEME → ECEF (GMST) → WGS-84 geodetic, validated against
-- skyfield to < 0.001 deg. Time-step 60 s over a forward 24 h window (configurable).
--
-- GEO SPECIAL CASE (documented): a geosync sub-point is ~equatorial, so it is never
-- directly over the NORTHERN AOI; the polygon test would yield nothing. For GEO we
-- instead flag "STATIONED OVER THE AOI LONGITUDE BAND" (a point at the AOI's central
-- latitude and the satellite's stationing longitude classified by geo_area) and emit
-- AT MOST ONE row (is_persistent=TRUE) for the window — never per-step spam.
--
-- FORWARD-WINDOW CACHE (documented): this is a recomputed PREDICTION for the next N
-- hours, NOT append-only history. Each refresh (after a TLE refresh) RECOMPUTES and
-- REPLACES the whole table (DELETE + INSERT in one transaction). Idempotent: the
-- same TLEs + window produce the same pass rows. window_start/window_end record the
-- window; tle_epoch records the per-satellite TLE the pass was computed from.
--
-- PROVENANCE: source='derived-sgp4', tle_epoch, computed_at.
--
-- STRICTLY ADDITIVE: a new table only. satellite_elements / satellite_history /
-- satellite_info / satellite_strategic / satellite_reentry / satellite_orbit and
-- their keys are UNTOUCHED. No FK (joined by norad_id at query time). Reversal
-- (forward-only migrations): DROP TABLE satellite_passes.

CREATE TABLE satellite_passes (
    norad_id       INTEGER     NOT NULL,
    start_time     TIMESTAMPTZ NOT NULL,     -- pass start (window start for GEO persistent)
    end_time       TIMESTAMPTZ NOT NULL,
    duration_s     DOUBLE PRECISION,
    countries      TEXT[],                   -- seven-country polygons the ground track crossed (INSIDE)
    near_countries TEXT[],                   -- countries the track was NEAR (buffer)
    closest_km     DOUBLE PRECISION,         -- closest approach to the AOI centre (provenance)
    is_persistent  BOOLEAN NOT NULL DEFAULT false,  -- TRUE = GEO stationed over the longitude band
    n_points       INTEGER,                  -- in-AOI ground-track samples (derivation provenance)
    -- provenance
    source         TEXT NOT NULL DEFAULT 'derived-sgp4',
    tle_epoch      TIMESTAMPTZ,              -- epoch of the TLE this pass was computed from
    window_start   TIMESTAMPTZ,             -- forward-window bounds this run covered
    window_end     TIMESTAMPTZ,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT satellite_passes_pkey PRIMARY KEY (norad_id, start_time)
);

CREATE INDEX satellite_passes_start_idx     ON satellite_passes (start_time);
CREATE INDEX satellite_passes_norad_idx     ON satellite_passes (norad_id);
CREATE INDEX satellite_passes_countries_idx ON satellite_passes USING GIN (countries);
