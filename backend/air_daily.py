"""DASH-BOARD-3b — the daily air summary: one exact distinct-airframe count per day.

⛔ ONE DEFINITION OF THE AGGREGATE, USED BY BOTH READERS. :func:`agg_sql` is the single place
that says what "an airframe over a country, from one feed, in one bucket" means; the live
endpoint (`/air/series/country`) and this nightly job both call it. A second hand-written
query would be free to drift on the country classification, on the distinct key, or on the
`country IS NOT NULL` filter — and the two answers would then disagree with nobody noticing.
The equivalence test (tests/test_air_daily.py) pins them together on a real day.

⛔⛔ THE UNIT. One row is an EXACT `count(DISTINCT icao24)` for ONE day — not an estimate.
But SUMMING ROWS ACROSS DAYS GIVES **HULL-DAYS**, not distinct airframes: an airframe present
on ten days contributes 10. There is no path back to a multi-day distinct count from this
table. ⚠ That is not a degradation — for "which countries lead over time" hull-days are
arguably the better measure, because they weight presence over time rather than mere
appearance. It is a DIFFERENT unit, and it is named as one everywhere it surfaces.

⛔ MISSING DAYS STAY MISSING. A day the host was off gets no row, never a zero: a zero reads
"no aircraft flew", an absent row reads "we did not observe". The table's
`distinct_hulls > 0` CHECK makes a zero-fill fail loudly rather than pass quietly.

Run:
    python -m backend.air_daily --yesterday          # the nightly unit's invocation
    python -m backend.air_daily --backfill           # every complete day with data
    python -m backend.air_daily --day 2026-07-20
    python -m backend.air_daily --day 2026-07-20 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone

import asyncpg
from dotenv import load_dotenv

log = logging.getLogger("air_daily")

#: ⛔ THE SINGLE DEFINITION. `{bucket}` is the only thing that varies between the live
#: endpoint (hour/day/week/month) and this job (always 'day'). Everything that decides WHAT is
#: counted — the AOI-classified `country`, the `icao24` identity, the per-source split — is
#: fixed here and nowhere else.
#:
#: `country IS NOT NULL` is the AOI polygon classifier's own verdict, written by ingest.py on
#: the way in. ⛔ It is NOT re-derived here: re-implementing "inside Syria" against the
#: polygons would be a second answer to a question already answered upstream.
_AGG_SQL = """
SELECT date_trunc('{bucket}', ts) AS bk,
       country,
       source,
       count(DISTINCT icao24) AS distinct_hulls,
       count(*)               AS rows
FROM state_vectors
WHERE {where}
GROUP BY 1, 2, 3
HAVING count(DISTINCT icao24) >= {min_param}
ORDER BY 1, 4 DESC
"""


def agg_sql(bucket: str, where: list[str], min_param: str) -> str:
    """Render the one aggregate definition for a caller's predicates.

    `where` clauses and `min_param` arrive already parameterised by the caller ($1, $2 …);
    only their PRESENCE varies, so an absent window leaves no clause for the planner to
    reason about rather than a runtime-nullable OR.
    """
    return _AGG_SQL.format(bucket=bucket, where=" AND ".join(where), min_param=min_param)


#: The base predicate every caller shares: a classified position, and nothing else.
BASE_WHERE = "country IS NOT NULL"


async def compute_day(pool, day: date) -> list[dict]:
    """The exact per-country, per-source distinct-airframe counts for ONE complete UTC day."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rows = await pool.fetch(
        agg_sql("day", [BASE_WHERE, "ts >= $1", "ts < $2"], "$3"),
        start, start + timedelta(days=1), 1)
    return [dict(r) for r in rows]


async def upsert_day(pool, day: date, dry_run: bool = False) -> int:
    """Recompute one day and REPLACE its rows.

    ⛔ UPSERT, NOT INSERT — a missed run is made up on the next one and a re-run never
    duplicates. `computed_at` is refreshed so the row always says when it was last derived,
    which is what the coverage contract reads.

    ⛔ A day that produced NOTHING writes NOTHING and DELETES nothing: the absence of a row is
    the signal. (A day whose rows vanished from the source would keep its old rows; that is
    the conservative direction — we never turn observed history into silence.)
    """
    rows = await compute_day(pool, day)
    if dry_run:
        log.info("--dry-run: %s would upsert %d rows", day, len(rows))
        return len(rows)
    if not rows:
        log.info("%s produced no rows — NOTHING written (absence is the signal, not a zero)", day)
        return 0
    await pool.executemany(
        "INSERT INTO air_country_daily (day, country, source, distinct_hulls, rows, computed_at) "
        "VALUES ($1, $2, $3, $4, $5, now()) "
        "ON CONFLICT (day, country, source) DO UPDATE SET "
        "  distinct_hulls = EXCLUDED.distinct_hulls, "
        "  rows = EXCLUDED.rows, "
        "  computed_at = EXCLUDED.computed_at",
        [(day, r["country"], r["source"], r["distinct_hulls"], r["rows"]) for r in rows])
    log.info("%s → %d rows upserted", day, len(rows))
    return len(rows)


async def available_days(pool) -> list[date]:
    """Every COMPLETE UTC day that actually has data.

    ⛔ Derived from the data, not from a generated calendar: a `generate_series` over the span
    would hand back the four host-outage days and invite a zero-fill. Today is excluded — a
    partial day plotted beside complete ones reads as a collapse.
    """
    rows = await pool.fetch(
        "SELECT DISTINCT date_trunc('day', ts)::date AS d FROM state_vectors "
        "WHERE ts < date_trunc('day', now()) ORDER BY 1")
    return [r["d"] for r in rows]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--yesterday", action="store_true", help="the last COMPLETE UTC day")
    g.add_argument("--backfill", action="store_true", help="every complete day that has data")
    g.add_argument("--day", help="one day, YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="compute and report, write nothing")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s",
                        datefmt="%H:%M:%S")
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL is not set")
        return 2
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        if a.day:
            days = [date.fromisoformat(a.day)]
        elif a.yesterday:
            days = [(datetime.now(timezone.utc) - timedelta(days=1)).date()]
        else:
            days = await available_days(pool)
            log.info("backfill: %d complete days with data (%s → %s)",
                     len(days), days[0] if days else "-", days[-1] if days else "-")
        total = 0
        for d in days:
            total += await upsert_day(pool, d, dry_run=a.dry_run)
        log.info("done: %d day(s), %d row(s)%s",
                 len(days), total, " (dry run)" if a.dry_run else "")
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
