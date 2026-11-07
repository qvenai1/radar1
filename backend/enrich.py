"""
Best-effort aircraft enrichment: airframe info + photo + standing route.

Sources (free, no auth):
  * adsbdb.com     — /v0/aircraft/{hex}   → registration/type/operator
                     /v0/callsign/{cs}    → airline + origin/destination
  * planespotters  — /pub/photos/hex/{hex} → real photo (REQUIRES a
                     descriptive User-Agent with a contact; attribution to
                     the photographer + link back is a term of use)

Behavior contract:
  * cache-first against aircraft_info / flight_routes (migration 004);
    TTLs: found 30d (aircraft) / 7d (routes), not-found 1d (negative cache)
  * upstream failure → serve stale cache if any, else found=false WITHOUT
    caching the failure (a network blip must not become a 1-day "unknown")
  * single-flight per key: N clients selecting the same aircraft trigger
    exactly one upstream call
  * route data is standing data (callsign → usual route) — best effort by
    nature; can be wrong for charters. Nothing here is authoritative.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import asyncpg

logger = logging.getLogger("opensky_api.enrich")

ADSBDB_AIRCRAFT = "https://api.adsbdb.com/v0/aircraft/{hex}"
ADSBDB_CALLSIGN = "https://api.adsbdb.com/v0/callsign/{cs}"
PLANESPOTTERS = "https://api.planespotters.net/pub/photos/hex/{hex}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# planespotters rejects generic UAs; contact is configurable via env.
USER_AGENT = os.environ.get(
    "ENRICH_CONTACT_UA", "AtheerRADAR/1.0 (+mailto:qvenai@gmail.com)"
)

TTL_AIRCRAFT_FOUND = timedelta(days=30)
TTL_ROUTE_FOUND = timedelta(days=7)
TTL_VESSEL_FOUND = timedelta(days=30)
TTL_NOT_FOUND = timedelta(days=1)
TTL_VESSEL_NOT_FOUND = timedelta(days=3)  # ship photo coverage is sparse+stable


class Enricher:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _lock(self, key: str) -> asyncio.Lock:
        # bounded: keys are icao24/callsigns of currently-selected aircraft;
        # prune opportunistically so the dict can't grow without limit
        if len(self._locks) > 512:
            self._locks = {k: v for k, v in self._locks.items() if v.locked()}
        return self._locks.setdefault(key, asyncio.Lock())

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8, connect=4),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self._session

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        """GET → parsed JSON; None on any failure (caller decides fallback)."""
        try:
            session = await self._http()
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.debug("enrich upstream %s -> %d", url, resp.status)
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            logger.debug("enrich upstream %s failed: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # aircraft info + photo
    # ------------------------------------------------------------------

    async def get_aircraft(self, pool: asyncpg.Pool, icao24: str) -> dict[str, Any]:
        icao24 = icao24.strip().lower()
        async with self._lock(f"a:{icao24}"):
            row = await pool.fetchrow(
                "SELECT * FROM aircraft_info WHERE icao24 = $1", icao24
            )
            if row is not None and _fresh(row, TTL_AIRCRAFT_FOUND if row["found"] else TTL_NOT_FOUND):
                return dict(row)

            fetched = await self._fetch_aircraft(icao24)
            if fetched is None:  # upstream unreachable — stale beats nothing
                return dict(row) if row is not None else {"icao24": icao24, "found": False}

            await pool.execute(
                """
                INSERT INTO aircraft_info (icao24, found, registration, aircraft_type,
                    icao_type, manufacturer, operator, operator_country,
                    photo_thumb, photo_page, photographer, fetched_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, now())
                ON CONFLICT (icao24) DO UPDATE SET
                    found=$2, registration=$3, aircraft_type=$4, icao_type=$5,
                    manufacturer=$6, operator=$7, operator_country=$8,
                    photo_thumb=$9, photo_page=$10, photographer=$11, fetched_at=now()
                """,
                icao24, fetched["found"], fetched.get("registration"),
                fetched.get("aircraft_type"), fetched.get("icao_type"),
                fetched.get("manufacturer"), fetched.get("operator"),
                fetched.get("operator_country"), fetched.get("photo_thumb"),
                fetched.get("photo_page"), fetched.get("photographer"),
            )
            fetched["icao24"] = icao24
            return fetched

    async def _fetch_aircraft(self, icao24: str) -> dict[str, Any] | None:
        """Combine adsbdb airframe data with a planespotters photo.
        Returns None only when BOTH upstreams are unreachable."""
        adsbdb, photos = await asyncio.gather(
            self._get_json(ADSBDB_AIRCRAFT.format(hex=icao24)),
            self._get_json(PLANESPOTTERS.format(hex=icao24)),
        )
        if adsbdb is None and photos is None:
            return None

        out: dict[str, Any] = {"found": False}
        ac = (adsbdb or {}).get("response")
        if isinstance(ac, dict) and isinstance(ac.get("aircraft"), dict):
            a = ac["aircraft"]
            out.update(
                found=True,
                registration=a.get("registration"),
                aircraft_type=a.get("type"),
                icao_type=a.get("icao_type"),
                manufacturer=a.get("manufacturer"),
                operator=a.get("registered_owner"),
                operator_country=a.get("registered_owner_country_name"),
                # adsbdb photo is the fallback if planespotters has none
                photo_thumb=a.get("url_photo_thumbnail"),
                photo_page=a.get("url_photo"),
                photographer=None,
            )
        ph = (photos or {}).get("photos") or []
        if ph:
            p = ph[0]
            out["found"] = True
            out["photo_thumb"] = (p.get("thumbnail_large") or p.get("thumbnail") or {}).get("src")
            out["photo_page"] = p.get("link")
            out["photographer"] = p.get("photographer")
        return out

    # ------------------------------------------------------------------
    # route (standing data by callsign)
    # ------------------------------------------------------------------

    async def get_route(self, pool: asyncpg.Pool, callsign: str) -> dict[str, Any]:
        callsign = callsign.strip().upper()
        async with self._lock(f"r:{callsign}"):
            row = await pool.fetchrow(
                "SELECT * FROM flight_routes WHERE callsign = $1", callsign
            )
            if row is not None and _fresh(row, TTL_ROUTE_FOUND if row["found"] else TTL_NOT_FOUND):
                return dict(row)

            data = await self._get_json(ADSBDB_CALLSIGN.format(cs=callsign))
            if data is None:
                return dict(row) if row is not None else {"callsign": callsign, "found": False}

            fetched = _parse_route(data)
            await pool.execute(
                """
                INSERT INTO flight_routes (callsign, found, airline_name, airline_icao,
                    origin_icao, origin_iata, origin_name, origin_municipality,
                    origin_country, origin_lat, origin_lon,
                    dest_icao, dest_iata, dest_name, dest_municipality,
                    dest_country, dest_lat, dest_lon, fetched_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18, now())
                ON CONFLICT (callsign) DO UPDATE SET
                    found=$2, airline_name=$3, airline_icao=$4,
                    origin_icao=$5, origin_iata=$6, origin_name=$7,
                    origin_municipality=$8, origin_country=$9, origin_lat=$10,
                    origin_lon=$11, dest_icao=$12, dest_iata=$13, dest_name=$14,
                    dest_municipality=$15, dest_country=$16, dest_lat=$17,
                    dest_lon=$18, fetched_at=now()
                """,
                callsign, fetched["found"], fetched.get("airline_name"),
                fetched.get("airline_icao"),
                fetched.get("origin_icao"), fetched.get("origin_iata"),
                fetched.get("origin_name"), fetched.get("origin_municipality"),
                fetched.get("origin_country"), fetched.get("origin_lat"),
                fetched.get("origin_lon"),
                fetched.get("dest_icao"), fetched.get("dest_iata"),
                fetched.get("dest_name"), fetched.get("dest_municipality"),
                fetched.get("dest_country"), fetched.get("dest_lat"),
                fetched.get("dest_lon"),
            )
            fetched["callsign"] = callsign
            return fetched

    # ------------------------------------------------------------------
    # vessel photo (best-effort, Wikimedia Commons — sparse coverage)
    # ------------------------------------------------------------------

    async def get_vessel_photo(self, pool: asyncpg.Pool, mmsi: str,
                               imo: str | None, name: str | None) -> dict[str, Any]:
        mmsi = mmsi.strip()
        async with self._lock(f"v:{mmsi}"):
            row = await pool.fetchrow("SELECT * FROM vessel_info WHERE mmsi = $1", mmsi)
            ttl = TTL_VESSEL_FOUND if (row and row["found"]) else TTL_VESSEL_NOT_FOUND
            if row is not None and _fresh(row, ttl):
                return dict(row)

            fetched = await self._fetch_vessel_photo(imo, name)
            if fetched is None:  # Commons unreachable — stale beats nothing
                return dict(row) if row is not None else {"mmsi": mmsi, "found": False}

            await pool.execute(
                """
                INSERT INTO vessel_info (mmsi, found, photo_thumb, photo_page,
                    photographer, license, matched_on, fetched_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7, now())
                ON CONFLICT (mmsi) DO UPDATE SET
                    found=$2, photo_thumb=$3, photo_page=$4, photographer=$5,
                    license=$6, matched_on=$7, fetched_at=now()
                """,
                mmsi, fetched["found"], fetched.get("photo_thumb"),
                fetched.get("photo_page"), fetched.get("photographer"),
                fetched.get("license"), fetched.get("matched_on"),
            )
            fetched["mmsi"] = mmsi
            return fetched

    async def _fetch_vessel_photo(self, imo: str | None, name: str | None) -> dict[str, Any] | None:
        """Search Commons by IMO (precise) then name. Returns None only when
        Commons is unreachable for every attempt tried (so we don't negative-
        cache a network blip); a real 'no photo' returns found=false."""
        attempts: list[tuple[str, str]] = []
        if imo:
            attempts.append((f"imo:{imo}", f"IMO {imo}"))
        if name and len(name.strip()) >= 3:
            attempts.append((f"name:{name.strip()}", f'"{name.strip()}" ship'))
        if not attempts:
            return {"found": False}

        any_reached = False
        for matched_on, query in attempts:
            data = await self._get_json(COMMONS_API, {
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": query, "gsrnamespace": "6", "gsrlimit": "1",
                "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": "360",
            })
            if data is None:
                continue
            any_reached = True
            pages = ((data.get("query") or {}).get("pages")) or {}
            for page in pages.values():
                ii = (page.get("imageinfo") or [{}])[0]
                thumb = ii.get("thumburl")
                if not thumb:
                    continue
                em = ii.get("extmetadata") or {}
                return {
                    "found": True,
                    "photo_thumb": thumb,
                    "photo_page": ii.get("descriptionurl"),
                    "photographer": _strip_html((em.get("Artist") or {}).get("value")),
                    "license": (em.get("LicenseShortName") or {}).get("value"),
                    "matched_on": matched_on,
                }
        return {"found": False} if any_reached else None


def _strip_html(v: Any) -> str | None:
    if not isinstance(v, str) or not v:
        return None
    text = re.sub(r"<[^>]+>", "", v).strip()
    return text[:80] or None


def _fresh(row: Any, ttl: timedelta) -> bool:
    fetched_at: datetime = row["fetched_at"]
    return datetime.now(timezone.utc) - fetched_at < ttl


def _parse_route(data: Any) -> dict[str, Any]:
    fr = None
    resp = (data or {}).get("response")
    if isinstance(resp, dict):
        fr = resp.get("flightroute")
    if not isinstance(fr, dict):
        return {"found": False}
    airline = fr.get("airline") or {}
    o = fr.get("origin") or {}
    d = fr.get("destination") or {}

    def airport(x: dict[str, Any], prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_icao": x.get("icao_code"),
            f"{prefix}_iata": x.get("iata_code"),
            f"{prefix}_name": x.get("name"),
            f"{prefix}_municipality": x.get("municipality"),
            f"{prefix}_country": x.get("country_name"),
            f"{prefix}_lat": x.get("latitude"),
            f"{prefix}_lon": x.get("longitude"),
        }

    return {
        "found": bool(o.get("icao_code") and d.get("icao_code")),
        "airline_name": airline.get("name"),
        "airline_icao": airline.get("icao"),
        **airport(o, "origin"),
        **airport(d, "dest"),
    }
