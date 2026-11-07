#!/usr/bin/env python3
"""Satellite RESEARCH layer Sat-UCS: bulk offline enrichment of STRATEGIC
classification (user class / purpose / operator) from the UCS Satellite Database
into satellite_strategic (migration 018). Keyed on norad_id.

UCS is a DATED RESEARCH SNAPSHOT (frozen 2023-05-01; the project paused updates).
This layer is for strategic/structural analysis and a historical baseline — NOT a
current-state source. Every row records source='ucs' + ucs_snapshot_date +
joined_via, so a study can cite "strategic classification as of 2023-05-01" and
never silently mix vintages. UCS and SATCAT are COMPLEMENTARY layers at distinct
vintages — this backfill NEVER touches satellite_info.

JOIN (verified against the real file): UCS carries both a NORAD Number and a
COSPAR Number (= international designator). We match each of OUR catalogued
objects (satellite_info: norad_id + international_designator) to a UCS row by
NORAD first (joined_via='norad'), else by COSPAR/INTLDES (joined_via='intldes').
An object absent from UCS stays unclassified (no guessing — expected for
post-2023 objects). Achievable coverage on the current catalog: ~703/908 (77%),
701 via NORAD + 2 via INTLDES.

Idempotent + resumable + blank-safe: COALESCE(EXCLUDED, existing) so an empty UCS
field never nulls a stored value; a DO UPDATE ... WHERE change-guard makes a
same-snapshot re-run a 0-row no-op.

Fetch/refresh the snapshot (gitignored under enrichment_data/, never committed):
    curl -sL -o enrichment_data/ucs-satellite-database.tsv https://www.ucs.org/media/11493
The file is ISO-8859 (latin-1), tab-delimited, quoted. Snapshot date is the UCS
version (2023-05-01); pass --snapshot-date to override on a future UCS release.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TSV = REPO_ROOT / "enrichment_data" / "ucs-satellite-database.tsv"
DEFAULT_SNAPSHOT = "2023-05-01"        # UCS DB version (its final snapshot)
SOURCE = "ucs"

logger = logging.getLogger("backfill_ucs")

# canonical key -> exact UCS header string (matched by name, robust to the file's
# trailing junk/duplicate columns). Only the fields this layer stores.
UCS_COLS = {
    "norad": "NORAD Number",
    "cospar": "COSPAR Number",
    "users": "Users",
    "purpose": "Purpose",
    "detailed_purpose": "Detailed Purpose",
    "operator": "Operator/Owner",
    "operator_country": "Country of Operator/Owner",
    "registry_country": "Country/Org of UN Registry",
    "orbit_class": "Class of Orbit",
    "orbit_type": "Type of Orbit",
    "launch_mass": "Launch Mass (kg.)",
    "power": "Power (watts)",
    "expected_lifetime": "Expected Lifetime (yrs.)",
    "contractor": "Contractor",
    "launch_site": "Launch Site",
}


def _clean(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v or None


def _num_ucs(v: Any) -> float | None:
    """UCS numeric cell → float. Strips thousands commas and units; 'NA'/blank →
    None; takes the leading number (e.g. '1,250' → 1250.0, '15 yrs' → 15.0)."""
    if not isinstance(v, str):
        return None
    s = v.strip().replace(",", "")
    if not s or s.upper() in ("NA", "N/A", "UNKNOWN", "-"):
        return None
    m = re.match(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def norm_cospar(v: Any) -> str | None:
    """Normalize a COSPAR / international designator for matching."""
    if not isinstance(v, str):
        return None
    return v.strip().upper().replace(" ", "") or None


def map_ucs_row(record: dict[str, Any]) -> dict[str, Any]:
    """UCS record (canonical keys → raw cell values) → strategic field dict.
    Pure: no norad_id/provenance here (the joiner sets OUR norad_id + joined_via)."""
    return {
        "users": _clean(record.get("users")),
        "purpose": _clean(record.get("purpose")),
        "detailed_purpose": _clean(record.get("detailed_purpose")),
        "operator": _clean(record.get("operator")),
        "operator_country": _clean(record.get("operator_country")),
        "registry_country": _clean(record.get("registry_country")),
        "orbit_class": _clean(record.get("orbit_class")),
        "orbit_type": _clean(record.get("orbit_type")),
        "launch_mass_kg": _num_ucs(record.get("launch_mass")),
        "power_watts": _num_ucs(record.get("power")),
        "expected_lifetime_yrs": _num_ucs(record.get("expected_lifetime")),
        "contractor": _clean(record.get("contractor")),
        "launch_site": _clean(record.get("launch_site")),
    }


def match_ucs(norad_id: int, intldes: str | None,
              by_norad: dict[int, dict], by_cospar: dict[str, dict]
              ) -> tuple[dict | None, str | None]:
    """Find the UCS record for one of our objects: NORAD direct first, else
    COSPAR/INTLDES. Returns (record, joined_via) or (None, None)."""
    rec = by_norad.get(norad_id)
    if rec is not None:
        return rec, "norad"
    key = norm_cospar(intldes)
    if key and key in by_cospar:
        return by_cospar[key], "intldes"
    return None, None


def load_ucs(path: Path) -> tuple[dict[int, dict], dict[str, dict]]:
    """Read the UCS file → (by_norad, by_cospar) lookups of canonical records.
    First occurrence wins for a duplicated key (deterministic)."""
    with path.open(encoding="latin-1", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(reader)]
        idx = {}
        for key, col in UCS_COLS.items():
            try:
                idx[key] = header.index(col)
            except ValueError as exc:
                raise RuntimeError(f"UCS column not found: {col!r}") from exc
        by_norad: dict[int, dict] = {}
        by_cospar: dict[str, dict] = {}
        dups = 0
        for cells in reader:
            if not any(c.strip() for c in cells):
                continue
            rec = {k: (cells[i] if i < len(cells) else "") for k, i in idx.items()}
            n = rec["norad"].strip()
            if n.isdigit():
                if int(n) in by_norad:
                    dups += 1
                else:
                    by_norad[int(n)] = rec
            c = norm_cospar(rec["cospar"])
            if c and c not in by_cospar:
                by_cospar[c] = rec
    logger.info("UCS loaded: %d by NORAD, %d by COSPAR (%d duplicate NORAD keys skipped)",
                len(by_norad), len(by_cospar), dups)
    return by_norad, by_cospar


UPSERT = """
INSERT INTO satellite_strategic
    (norad_id, users, purpose, detailed_purpose, operator, operator_country,
     registry_country, orbit_class, orbit_type, launch_mass_kg, power_watts,
     expected_lifetime_yrs, contractor, launch_site, source, ucs_snapshot_date,
     joined_via, fetched_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::date,$17, now())
ON CONFLICT (norad_id) DO UPDATE SET
    users            = COALESCE(EXCLUDED.users, satellite_strategic.users),
    purpose          = COALESCE(EXCLUDED.purpose, satellite_strategic.purpose),
    detailed_purpose = COALESCE(EXCLUDED.detailed_purpose, satellite_strategic.detailed_purpose),
    operator         = COALESCE(EXCLUDED.operator, satellite_strategic.operator),
    operator_country = COALESCE(EXCLUDED.operator_country, satellite_strategic.operator_country),
    registry_country = COALESCE(EXCLUDED.registry_country, satellite_strategic.registry_country),
    orbit_class      = COALESCE(EXCLUDED.orbit_class, satellite_strategic.orbit_class),
    orbit_type       = COALESCE(EXCLUDED.orbit_type, satellite_strategic.orbit_type),
    launch_mass_kg   = COALESCE(EXCLUDED.launch_mass_kg, satellite_strategic.launch_mass_kg),
    power_watts      = COALESCE(EXCLUDED.power_watts, satellite_strategic.power_watts),
    expected_lifetime_yrs = COALESCE(EXCLUDED.expected_lifetime_yrs, satellite_strategic.expected_lifetime_yrs),
    contractor       = COALESCE(EXCLUDED.contractor, satellite_strategic.contractor),
    launch_site      = COALESCE(EXCLUDED.launch_site, satellite_strategic.launch_site),
    source            = EXCLUDED.source,
    ucs_snapshot_date = EXCLUDED.ucs_snapshot_date,
    joined_via        = EXCLUDED.joined_via,
    fetched_at        = now()
WHERE (
    satellite_strategic.users            IS DISTINCT FROM COALESCE(EXCLUDED.users, satellite_strategic.users) OR
    satellite_strategic.purpose          IS DISTINCT FROM COALESCE(EXCLUDED.purpose, satellite_strategic.purpose) OR
    satellite_strategic.detailed_purpose IS DISTINCT FROM COALESCE(EXCLUDED.detailed_purpose, satellite_strategic.detailed_purpose) OR
    satellite_strategic.operator         IS DISTINCT FROM COALESCE(EXCLUDED.operator, satellite_strategic.operator) OR
    satellite_strategic.operator_country IS DISTINCT FROM COALESCE(EXCLUDED.operator_country, satellite_strategic.operator_country) OR
    satellite_strategic.registry_country IS DISTINCT FROM COALESCE(EXCLUDED.registry_country, satellite_strategic.registry_country) OR
    satellite_strategic.orbit_class      IS DISTINCT FROM COALESCE(EXCLUDED.orbit_class, satellite_strategic.orbit_class) OR
    satellite_strategic.orbit_type       IS DISTINCT FROM COALESCE(EXCLUDED.orbit_type, satellite_strategic.orbit_type) OR
    satellite_strategic.launch_mass_kg   IS DISTINCT FROM COALESCE(EXCLUDED.launch_mass_kg, satellite_strategic.launch_mass_kg) OR
    satellite_strategic.power_watts      IS DISTINCT FROM COALESCE(EXCLUDED.power_watts, satellite_strategic.power_watts) OR
    satellite_strategic.expected_lifetime_yrs IS DISTINCT FROM COALESCE(EXCLUDED.expected_lifetime_yrs, satellite_strategic.expected_lifetime_yrs) OR
    satellite_strategic.contractor       IS DISTINCT FROM COALESCE(EXCLUDED.contractor, satellite_strategic.contractor) OR
    satellite_strategic.launch_site      IS DISTINCT FROM COALESCE(EXCLUDED.launch_site, satellite_strategic.launch_site) OR
    satellite_strategic.joined_via       IS DISTINCT FROM EXCLUDED.joined_via OR
    satellite_strategic.ucs_snapshot_date IS DISTINCT FROM EXCLUDED.ucs_snapshot_date
)
"""


def strategic_row(norad_id: int, mapped: dict[str, Any], joined_via: str,
                  snapshot: date) -> tuple:
    return (
        norad_id, mapped["users"], mapped["purpose"], mapped["detailed_purpose"],
        mapped["operator"], mapped["operator_country"], mapped["registry_country"],
        mapped["orbit_class"], mapped["orbit_type"], mapped["launch_mass_kg"],
        mapped["power_watts"], mapped["expected_lifetime_yrs"], mapped["contractor"],
        mapped["launch_site"], SOURCE, snapshot, joined_via,
    )


async def run(dsn: str, tsv_path: Path, snapshot: str, dry_run: bool) -> int:
    if not tsv_path.exists():
        logger.error("UCS file not found: %s (fetch it first — see the docstring)", tsv_path)
        return 2
    try:
        snapshot_date = date.fromisoformat(snapshot)
    except ValueError:
        logger.error("--snapshot-date must be YYYY-MM-DD (the UCS DB version)")
        return 2

    by_norad, by_cospar = load_ucs(tsv_path)
    conn = await asyncpg.connect(dsn)
    try:
        catalog = await conn.fetch("SELECT norad_id, international_designator FROM satellite_info")
        logger.info("=" * 60)
        logger.info("Sat-UCS PLAN — strategic classification (RESEARCH snapshot)")
        logger.info("  UCS: %s  (vintage %s)", tsv_path, snapshot_date)
        logger.info("  catalogued objects to match: %d", len(catalog))
        logger.info("=" * 60)

        matched_norad = matched_intldes = unmatched = changed = 0
        for row in catalog:
            nid = row["norad_id"]
            rec, via = match_ucs(nid, row["international_designator"], by_norad, by_cospar)
            if rec is None:
                unmatched += 1
                continue
            if via == "norad":
                matched_norad += 1
            else:
                matched_intldes += 1
            if dry_run:
                continue
            mapped = map_ucs_row(rec)
            status = await conn.execute(UPSERT, *strategic_row(nid, mapped, via, snapshot_date))
            changed += int(status.rsplit(" ", 1)[-1])

        logger.info("=" * 60)
        logger.info("Sat-UCS COVERAGE (of %d catalogued objects):", len(catalog))
        logger.info("  matched via NORAD  : %d", matched_norad)
        logger.info("  matched via INTLDES: %d", matched_intldes)
        logger.info("  unmatched (post-2023 / not in UCS): %d", unmatched)
        logger.info("  rows inserted/updated this run: %d%s", changed,
                    " (dry-run: none)" if dry_run else "")
        logger.info("=" * 60)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="Sat-UCS bulk backfill from the UCS Satellite Database")
    p.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    p.add_argument("--snapshot-date", default=DEFAULT_SNAPSHOT,
                   help="YYYY-MM-DD UCS DB version (provenance; default 2023-05-01)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.tsv, a.snapshot_date, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
