-- 008_vessel_info.sql
--
-- Server-side cache for best-effort VESSEL PHOTO enrichment. Unlike aircraft
-- (Planespotters gives dense coverage), there is NO free vessel-photo API of
-- comparable coverage; the one clean, free, ToS-friendly source is Wikimedia
-- Commons (searched by IMO, then name). Coverage is SPARSE — mostly notable
-- or large vessels — so most rows will be found=false. Negative caching keeps
-- those misses from re-hitting Commons every selection.
--
-- Same shape/rationale as aircraft_info (migration 004): browser never calls
-- Commons; the backend proxies + caches; TTL enforced at read time.

CREATE TABLE vessel_info (
    mmsi          TEXT PRIMARY KEY,
    found         BOOLEAN NOT NULL,
    photo_thumb   TEXT,
    photo_page    TEXT,          -- Commons File: page — attribution link
    photographer  TEXT,
    license       TEXT,
    matched_on    TEXT,          -- 'imo:...' or 'name:...' — provenance
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
