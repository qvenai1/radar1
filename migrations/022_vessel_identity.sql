-- 022_vessel_identity.sql
--
-- Maritime enrichment M2a — deterministic FLAG STATE (country of registration)
-- derived LOCALLY from each MMSI's Maritime Identification Digits (MID), the
-- sea-going analog of the aircraft E1 layer (migration 013: registration_country
-- from the icao24 24-bit allocation). Like E1 this is a REFERENCE FACT computed
-- from a committed ITU table (backend/mmsi_flag.py + backend/data/mmsi_mid.json),
-- NOT an inference and NOT an external API call.
--
-- WHY A DEDICATED TABLE (not columns on vessel_info):
--   vessel_info (migration 008) is specifically the best-effort PHOTO cache
--   (found=false negative caching, matched_on for photo matching). A derived
--   IDENTITY fact with its own provenance does not belong in a photo cache. The
--   mature scraplan pattern for a new derived-facts layer is a DEDICATED table
--   keyed on the natural id with its own source + derived_at — exactly what the
--   satellite layers did (satellite_info/orbit/strategic/reentry). vessel_identity
--   is the home for the identity layers: M2a fills the flag now; M2b/M2c
--   (name/type/IMO, external registry) will ADD columns here additively later.
--
-- COLUMNS:
--   * mmsi         — PK, the vessel's Maritime Mobile Service Identity.
--   * flag_country — flag state from the leading-3-digit MID of a SHIP-STATION
--                    MMSI (first digit 2-7). NULL for non-ship stations and for
--                    a ship station whose MID is unallocated (never a guess).
--                    AOI flag names match backend/geo_area.py (e.g. 'Syria'),
--                    so an M1(geo) x M2a(flag) cross-view is a direct string join.
--   * station_type — closed vocabulary {ship, coast, sar, aid-to-navigation,
--                    other} so non-vessel MMSIs (coast stations, AtoN, SAR
--                    transmitters, …) are recorded HONESTLY, never mis-flagged.
--   * flag_source  — provenance of the derivation = 'mmsi-mid' (distinct from
--                    vessel_states.source, the AIS feed). Every derived fact
--                    carries it; DEFAULT keeps it uniform.
--   * derived_at   — when the derivation ran (locally derived, not fetched — so
--                    its own timestamp, mirroring E1's registration_country_derived_at).
--
-- DB-LEVEL GUARANTEES (this task's rule): station_type is constrained to the
-- closed set, and a flag_country may exist ONLY for a ship station — so the
-- table can never hold a "flag" for a coast station or AtoN.
--
-- RESEARCH-INTEGRITY CAVEAT (documented in code + here): the MMSI/flag is the
-- CURRENT registration and CHANGES on re-flagging — a POINT-IN-TIME flag, not a
-- permanent identity. IMO (later, M2b) is the permanent hull-linked identifier.
--
-- STRICTLY ADDITIVE: a NEW table only. vessel_states and its natural key
-- (mmsi, ts, source), vessel_snapshots, vessel_info and every other table are
-- UNTOUCHED. Reversal (forward-only migrations): DROP TABLE vessel_identity.

CREATE TABLE vessel_identity (
    mmsi          TEXT PRIMARY KEY,
    flag_country  TEXT,                                   -- flag state (ship stations w/ allocated MID), else NULL
    station_type  TEXT NOT NULL,                          -- ship | coast | sar | aid-to-navigation | other
    flag_source   TEXT NOT NULL DEFAULT 'mmsi-mid',       -- derivation provenance
    derived_at    TIMESTAMPTZ NOT NULL DEFAULT now(),     -- when derived (local, not fetched)
    CONSTRAINT vessel_identity_station_type_ck
        CHECK (station_type IN ('ship','coast','sar','aid-to-navigation','other')),
    CONSTRAINT vessel_identity_flag_only_ship_ck
        CHECK (flag_country IS NULL OR station_type = 'ship')
);

-- Query dimensions: the flag distribution + "vessels flagged to country X", and
-- separating real vessels (ship) from coast/SAR/AtoN infrastructure.
CREATE INDEX vessel_identity_flag_country_idx ON vessel_identity (flag_country);
CREATE INDEX vessel_identity_station_type_idx ON vessel_identity (station_type);
