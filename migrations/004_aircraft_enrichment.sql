-- 004_aircraft_enrichment.sql
--
-- Server-side cache for best-effort aircraft enrichment fetched from free
-- external sources (adsbdb.com for airframe + route standing-data,
-- planespotters.net for photos). The browser NEVER calls those hosts; the
-- backend proxies and caches here so each airframe/callsign hits the
-- upstream once per TTL regardless of how many clients select it.
--
-- found=false rows are NEGATIVE cache entries (upstream said "unknown") so
-- unknown airframes don't hammer the APIs. TTLs are enforced at read time
-- in backend/enrich.py — no cron, no cleanup job needed at this volume.

CREATE TABLE aircraft_info (
    icao24            TEXT PRIMARY KEY,
    found             BOOLEAN NOT NULL,
    registration      TEXT,
    aircraft_type     TEXT,
    icao_type         TEXT,
    manufacturer      TEXT,
    operator          TEXT,
    operator_country  TEXT,
    photo_thumb       TEXT,
    photo_page        TEXT,
    photographer      TEXT,
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE flight_routes (
    callsign            TEXT PRIMARY KEY,
    found               BOOLEAN NOT NULL,
    airline_name        TEXT,
    airline_icao        TEXT,
    origin_icao         TEXT,
    origin_iata         TEXT,
    origin_name         TEXT,
    origin_municipality TEXT,
    origin_country      TEXT,
    origin_lat          DOUBLE PRECISION,
    origin_lon          DOUBLE PRECISION,
    dest_icao           TEXT,
    dest_iata           TEXT,
    dest_name           TEXT,
    dest_municipality   TEXT,
    dest_country        TEXT,
    dest_lat            DOUBLE PRECISION,
    dest_lon            DOUBLE PRECISION,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
