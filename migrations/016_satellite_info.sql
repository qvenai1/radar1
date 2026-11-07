-- 016_satellite_info.sql
--
-- Satellite enrichment layer S1: SATCAT IDENTITY enrichment cache, keyed on the
-- NORAD catalog id — the orbital equivalent of aircraft_info (migration 004) +
-- its bulk OpenSky-CSV backfill layer (migration 014 / scripts/backfill_from_
-- opensky_csv.py). Same discipline, keyed on norad_id instead of icao24.
--
-- Source: CelesTrak SATCAT (https://celestrak.org/pub/satcat.csv) — the SAME
-- keyless/free provider already used for TLEs (tracker_satellites.py). One
-- offline download covers the whole catalog; scripts/backfill_from_satcat.py
-- fills this table in bulk for every norad_id present in satellite_elements /
-- satellite_history. NOTHING is fetched at runtime in the serving path.
--
-- Reference data is a periodically-refreshed SNAPSHOT, so every enriched fact
-- carries provenance, exactly like the aircraft CSV layer:
--   * source               — origin label, 'celestrak_satcat'
--   * satcat_snapshot_date  — the SATCAT dump version (HTTP Last-Modified date),
--                             so analysis knows how OLD a SATCAT fact is
--   * source_updated_at     — when this layer last WROTE the row's facts (bumped
--                             only on a real change; a no-op re-run leaves it)
--   * fetched_at            — row creation stamp (mirrors aircraft_info)
--
-- STRICTLY ADDITIVE: this is a NEW table only. satellite_elements (mig 009) and
-- satellite_history (mig 011) and their primary keys are UNTOUCHED. This table
-- does not reference them (no FK) so a norad_id can be enriched even if its group
-- membership later changes; coverage is reconciled by the backfill, not the DB.
--
-- FIELD NOTES (verified against the LIVE satcat.csv header, 2026-07-17):
--   OBJECT_NAME,OBJECT_ID,NORAD_CAT_ID,OBJECT_TYPE,OPS_STATUS_CODE,OWNER,
--   LAUNCH_DATE,LAUNCH_SITE,DECAY_DATE,PERIOD,INCLINATION,APOGEE,PERIGEE,RCS,
--   DATA_STATUS_CODE,ORBIT_CENTER,ORBIT_TYPE
--   * object_type  = raw SATCAT code: PAY (payload) | R/B (rocket body) |
--     DEB (debris) | UNK (unknown). Stored raw (authoritative); the API maps a
--     readable label, mirroring how aircraft store raw ICAO type codes.
--   * owner        = raw CelesTrak OWNER/source code (e.g. US, CIS, PRC, ESA).
--     owner_name    = decoded country/organization name (static CelesTrak source
--                     map in the backfill; NULL when a code is unknown — never
--                     guessed). CelesTrak's OWNER is the only ownership field;
--                     there is NO distinct "operator" in SATCAT, so no empty
--                     operator column is created (an S2/UCS item).
--   * rcs_m2       = radar cross-section in m^2 (CelesTrak RCS; numeric, not the
--                    legacy S/M/L size class). NULL when unknown.
--   * ops_status   = OPS_STATUS_CODE; decay_date NULL means on-orbit.
--   Period/inclination/apogee/perigee are intentionally OUT of scope: they are
--   derivable from the TLE the frontend already has (orbit.ts) — S1 is IDENTITY.
--
-- Reversal (migrations are forward-only): DROP TABLE satellite_info.

CREATE TABLE satellite_info (
    norad_id                 INTEGER PRIMARY KEY,
    international_designator  TEXT,               -- OBJECT_ID, e.g. '1998-067A'
    object_name              TEXT,               -- SATCAT name (may differ from TLE)
    object_type              TEXT,               -- PAY | R/B | DEB | UNK (raw)
    owner                    TEXT,               -- CelesTrak OWNER code (raw, authoritative)
    owner_name               TEXT,               -- decoded name; NULL if code unknown
    launch_date              DATE,
    launch_site              TEXT,               -- CelesTrak launch-site code, e.g. 'TYMSC'
    decay_date               DATE,               -- NULL = on-orbit
    ops_status               TEXT,               -- OPS_STATUS_CODE
    rcs_m2                   DOUBLE PRECISION,   -- radar cross-section, m^2 (NULL if unknown)
    -- provenance (mirrors aircraft_info's CSV layer: source + snapshot + stamps)
    source                   TEXT NOT NULL DEFAULT 'celestrak_satcat',
    satcat_snapshot_date     DATE,
    source_updated_at        TIMESTAMPTZ,
    fetched_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX satellite_info_owner_idx ON satellite_info (owner);
CREATE INDEX satellite_info_object_type_idx ON satellite_info (object_type);
