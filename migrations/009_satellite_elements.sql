-- 009_satellite_elements.sql
--
-- Orbital-elements layer for the satellite tab. Architectural choice (per the
-- source review): DON'T poll a per-position API (N2YO, 1000 req/hr, observer-
-- centric). Instead cache TLE/orbital elements from CelesTrak (free, keyless,
-- refreshed a few times/day) and PROPAGATE POSITIONS LOCALLY — here, in the
-- browser via satellite.js (SGP4). A satellite position is DETERMINISTIC from
-- its TLE, unlike an observed aircraft/vessel, so the backend only needs to
-- serve elements; the client animates with zero server load and no rate cap.
--
-- CelesTrak throttles aggressive polling (verified: rapid repeat → 403), which
-- is exactly why elements are cached here and refreshed on a slow timer.
--
-- This is a plain table of CURRENT elements (small — a few thousand rows),
-- NOT a hypertable: there is one live TLE per (object, group). Historical TLE
-- archival (a hypertable on epoch, enabling past-orbit replay) is a documented
-- future extension, deliberately out of scope for the live map.
--
-- LIMITS (documented in the UI, in the project's honesty ethos):
--   * TLE accuracy decays from its epoch (~1 km near epoch, kilometres over
--     days) — good for a map, not for precision; refresh keeps it fresh.
--   * CelesTrak is the PUBLIC unclassified catalogue (Space-Track derived);
--     classified / maneuvering objects are absent.

CREATE TABLE satellite_elements (
    norad_id     INTEGER NOT NULL,
    grp          TEXT NOT NULL,          -- CelesTrak group (acts like `source`)
    object_name  TEXT NOT NULL DEFAULT '',
    tle_line1    TEXT NOT NULL,
    tle_line2    TEXT NOT NULL,
    epoch        TIMESTAMPTZ,            -- parsed from TLE line 1
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT satellite_elements_pkey PRIMARY KEY (norad_id, grp)
);

CREATE INDEX satellite_elements_grp_idx ON satellite_elements (grp);
