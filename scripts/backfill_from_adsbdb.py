#!/usr/bin/env python3
"""Enrichment layer E3: TARGETED adsbdb gap-fill + per-field refresh.

The freshest layer. For aircraft that have actually appeared IN-REGION (inside a
country or near a border, per the P3 geo columns on state_vectors) and that
either still lack type/operator OR carry those facts only from opensky_csv (E2),
query adsbdb once and apply, PER FIELD, the precedence:

    adsbdb (freshest) > opensky_csv (E2) > empty    -- value-present-only

  * adsbdb returns a value for a field  -> it wins: overwrite, field source
    becomes 'adsbdb', bump the timestamp.
  * adsbdb returns NO value for a field -> LEAVE the existing value + source
    (never blank a good CSV/adsbdb fact just because adsbdb lacks it).
  * a field already 'adsbdb' stays 'adsbdb'.
E1's registration_country* is independent and NEVER touched here.

Respectful of a FREE API: rate-limited (--delay), a slow background job by
design; per-call failures are isolated; negative cache reuses enrich.py's pattern
(found=false + recent fetched_at, TTL_NOT_FOUND) so an unknown hex is not
re-queried every run; UPSTREAM failure keeps existing data (no blank-out, no
negative-cache of a network blip).

Idempotent + resumable via the DATA: a filled hex leaves the targeting set
(facts now 'adsbdb'); a not-found hex is fresh-negative-cached and excluded until
TTL; so a re-run (after interruption/power loss) processes only the unprocessed
tail with zero redundant API calls.

Client reuse: uses backend.enrich.Enricher's configured aiohttp session and the
ADSBDB_AIRCRAFT URL (the substantive client, with the right UA/timeout). The
tiny adsbdb field mapping is mirrored here (parse_adsbdb_aircraft) rather than
importing enrich.py's inline block, so the interactive on-click enricher is not
modified and its planespotters call is not incurred. Keep the mapping in sync
with backend.enrich._fetch_aircraft.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import asyncpg  # noqa: E402
from backend.enrich import (  # noqa: E402
    ADSBDB_AIRCRAFT,
    TTL_AIRCRAFT_FOUND,
    TTL_NOT_FOUND,
    Enricher,
)

logger = logging.getLogger("backfill_adsbdb")
SOURCE = "adsbdb"

# in-region + (gap OR still opensky_csv-sourced) - freshly cached (adsbdb already
# answered within TTL). $1 = negative-cache TTL secs (TTL_NOT_FOUND), $2 =
# positive-cache TTL secs (TTL_AIRCRAFT_FOUND). Excluding a FRESH positive cache
# is what makes the run resumable: once adsbdb answers a hex (found=true,
# fetched_at=now), it is skipped even if some CSV field remained (adsbdb won't
# have more within TTL) — so a re-run does 0 redundant calls for processed hexes.
# E2/E1 rows are found=false with fetched_at=epoch (ancient) -> not fresh ->
# correctly still targeted.
TARGET_SQL = """
WITH inregion AS (
    SELECT DISTINCT icao24 FROM state_vectors
    WHERE country IS NOT NULL OR near_country IS NOT NULL
)
SELECT ai.icao24,
       (ai.aircraft_type IS NULL OR ai.operator IS NULL) AS is_gap
FROM inregion r
JOIN aircraft_info ai ON ai.icao24 = r.icao24
WHERE (
        ai.aircraft_type IS NULL OR ai.operator IS NULL
        OR ai.aircraft_type_source = 'opensky_csv'
        OR ai.operator_source      = 'opensky_csv'
        OR ai.registration_source  = 'opensky_csv'
        OR ai.manufacturer_source  = 'opensky_csv'
        OR ai.icao_type_source     = 'opensky_csv'
      )
  AND NOT (ai.found = true  AND ai.fetched_at > now() - make_interval(secs => $2))
  AND NOT (ai.found = false AND ai.fetched_at > now() - make_interval(secs => $1))
ORDER BY ai.icao24
"""

# Per-field precedence upsert: COALESCE(new, existing) keeps a good value when
# adsbdb returns null; the source flips to 'adsbdb' ONLY for fields adsbdb
# actually returned. registration_country* (E1) is never referenced here.
FILL_SQL = """
UPDATE aircraft_info SET
    found = true,
    fetched_at = now(),
    registration         = COALESCE($2, registration),
    registration_source  = CASE WHEN $2 IS NOT NULL THEN 'adsbdb' ELSE registration_source END,
    aircraft_type        = COALESCE($3, aircraft_type),
    aircraft_type_source = CASE WHEN $3 IS NOT NULL THEN 'adsbdb' ELSE aircraft_type_source END,
    icao_type            = COALESCE($4, icao_type),
    icao_type_source     = CASE WHEN $4 IS NOT NULL THEN 'adsbdb' ELSE icao_type_source END,
    manufacturer         = COALESCE($5, manufacturer),
    manufacturer_source  = CASE WHEN $5 IS NOT NULL THEN 'adsbdb' ELSE manufacturer_source END,
    operator             = COALESCE($6, operator),
    operator_source      = CASE WHEN $6 IS NOT NULL THEN 'adsbdb' ELSE operator_source END,
    operator_country     = COALESCE($7, operator_country),
    source = 'adsbdb',
    source_updated_at = now()
WHERE icao24 = $1
"""

# Negative cache: adsbdb has no data for this hex -> mark found=false + fresh
# fetched_at so E3 and the on-click enricher skip it for TTL_NOT_FOUND. Airframe
# facts (e.g. from E2) are left intact.
NEGCACHE_SQL = "UPDATE aircraft_info SET found = false, fetched_at = now() WHERE icao24 = $1"


def _v(x: Any) -> str | None:
    """adsbdb value -> trimmed str or None (empty string is 'no value')."""
    if x is None:
        return None
    s = str(x).strip()
    return s or None


def parse_adsbdb_aircraft(data: Any) -> dict[str, Any] | None:
    """adsbdb /v0/aircraft JSON -> airframe facts, or None if not found.
    Mirrors backend.enrich._fetch_aircraft's adsbdb mapping (keep in sync)."""
    resp = (data or {}).get("response") if isinstance(data, dict) else None
    if not (isinstance(resp, dict) and isinstance(resp.get("aircraft"), dict)):
        return None  # e.g. response == "unknown aircraft"
    a = resp["aircraft"]
    facts = {
        "registration": _v(a.get("registration")),
        "aircraft_type": _v(a.get("type")),
        "icao_type": _v(a.get("icao_type")),
        "manufacturer": _v(a.get("manufacturer")),
        "operator": _v(a.get("registered_owner")),
        "operator_country": _v(a.get("registered_owner_country_name")),
    }
    return facts if any(facts.values()) else None


async def _fetch(session: aiohttp.ClientSession, hx: str) -> tuple[str, dict | None]:
    """Return ('fill', facts) | ('notfound', None) | ('unreachable', None).
    404/quoted-unknown -> notfound (negative-cache); 429/5xx/timeout/error ->
    unreachable (keep existing, retry later)."""
    try:
        async with session.get(ADSBDB_AIRCRAFT.format(hex=hx)) as resp:
            if resp.status == 200:
                facts = parse_adsbdb_aircraft(await resp.json())
                return ("fill", facts) if facts else ("notfound", None)
            if resp.status == 404:
                return ("notfound", None)
            return ("unreachable", None)  # 429 / 5xx — retry next run
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return ("unreachable", None)


async def run(dsn: str, delay: float, limit: int | None, db_batch: int, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    enricher = Enricher()
    try:
        targets = await conn.fetch(
            TARGET_SQL, TTL_NOT_FOUND.total_seconds(), TTL_AIRCRAFT_FOUND.total_seconds())
        logger.info("=" * 60)
        logger.info("E3 PLAN — targeted adsbdb gap-fill + refresh")
        logger.info("  targeted (in-region, gap-or-csv, not neg-cached): %d", len(targets))
        logger.info("  rate delay: %.2fs/call   limit: %s", delay, limit if limit else "ALL")
        logger.info("=" * 60)
        if dry_run:
            logger.info("--dry-run: nothing queried or written.")
            return 0
        if not targets:
            logger.info("Nothing to do.")
            return 0
        if limit:
            targets = targets[:limit]

        session = await enricher._http()
        filled = refreshed = notfound = unreachable = 0
        fills: list[tuple] = []
        negs: list[str] = []

        async def flush() -> None:
            if fills:
                await conn.executemany(FILL_SQL, fills)
                fills.clear()
            if negs:
                await conn.executemany(NEGCACHE_SQL, [(h,) for h in negs])
                negs.clear()

        for i, row in enumerate(targets, 1):
            hx, is_gap = row["icao24"], row["is_gap"]
            outcome, facts = await _fetch(session, hx)
            if outcome == "fill":
                fills.append((hx, facts["registration"], facts["aircraft_type"],
                              facts["icao_type"], facts["manufacturer"],
                              facts["operator"], facts["operator_country"]))
                if is_gap:
                    filled += 1
                else:
                    refreshed += 1
            elif outcome == "notfound":
                negs.append(hx)
                notfound += 1
            else:
                unreachable += 1  # keep existing data; retry next run

            if len(fills) + len(negs) >= db_batch:
                await flush()
            if i % 25 == 0 or i == len(targets):
                logger.info("  %d/%d | filled=%d refreshed=%d notfound=%d unreachable=%d | remaining=%d",
                            i, len(targets), filled, refreshed, notfound, unreachable, len(targets) - i)
            if delay and i < len(targets):
                await asyncio.sleep(delay)

        await flush()
        logger.info("=" * 60)
        logger.info("DONE: processed=%d filled=%d refreshed=%d notfound=%d unreachable=%d",
                    len(targets), filled, refreshed, notfound, unreachable)
        logger.info("=" * 60)
        return 0
    finally:
        await enricher.close()
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="E3 targeted adsbdb gap-fill + refresh")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between adsbdb calls (polite)")
    p.add_argument("--limit", type=int, default=None, help="process only the first N targets")
    p.add_argument("--db-batch", type=int, default=25)
    p.add_argument("--dry-run", action="store_true", help="report the targeted set size, do nothing")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.delay, a.limit, a.db_batch, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
