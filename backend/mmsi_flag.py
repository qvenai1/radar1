"""Deterministic FLAG STATE (country of registration) from an MMSI.

Maritime analog of backend.icao_country (the aircraft icao24 -> registration
country layer): a LOCAL lookup, NOT a network call and NOT a guess. Per ITU
Rec. M.585 / Radio Regulations Appendix 43, a SHIP-STATION MMSI (first digit
2-7) is nine digits whose LEADING THREE are the Maritime Identification Digits
(MID) — an ITU-assigned country code = the vessel's flag state.

Other MMSI formats are NOT individual vessels being flagged; they are identified
by station_type and given NO flag country (never a forced/wrong one):
  * 00MIDxxxx  coast station              -> station_type 'coast'
  * 0MIDxxxxx  group of ship stations     -> 'other'
  * 111MIDxxx  SAR aircraft               -> 'sar'
  * 99MIDxxxx  aid to navigation (AtoN)   -> 'aid-to-navigation'
  * 98MIDxxxx  craft associated w/ parent -> 'other'
  * 970xxxxxx  AIS-SART (SAR transmitter) -> 'sar'
  * 972/974…   MOB / EPIRB-AIS            -> 'other'
  * 8xxxxxxxx  handheld VHF/DSC           -> 'other'
  * anything not a 9-digit number         -> 'other' (invalid)

For a ship station whose MID is unallocated/unknown, the country is None (never
a guess) while station_type stays 'ship'.

RESEARCH-INTEGRITY CAVEAT: the MMSI flag is the CURRENT registration and CHANGES
on re-flagging — it is a POINT-IN-TIME flag, not a permanent identity. The IMO
number (permanent, hull-linked; arrives in a later identity layer) is the
durable link. Consumers must treat flag_country as time-of-observation state.

The MID table (backend/data/mmsi_mid.json) is a committed static asset, loaded
once and NEVER fetched at runtime; a missing/malformed asset fails fast.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DEFAULT_ASSET = Path(__file__).resolve().parent / "data" / "mmsi_mid.json"
#: MID -> ISO 3166-1 alpha-2 (MID-ISO-1). A SECOND asset, joined on the MID —
#: never on the country name. See its own header for provenance and licence.
ISO_ASSET = Path(__file__).resolve().parent / "data" / "mid_iso.json"

#: Provenance label stored alongside every derived flag/station fact.
MID_SOURCE = "mmsi-mid"

#: The station-type vocabulary (closed set).
ST_SHIP = "ship"
ST_COAST = "coast"
ST_SAR = "sar"
ST_ATON = "aid-to-navigation"
ST_OTHER = "other"
STATION_TYPES = frozenset({ST_SHIP, ST_COAST, ST_SAR, ST_ATON, ST_OTHER})


class MidAssetError(RuntimeError):
    """The MID asset is missing, unreadable, or malformed (fail fast)."""


def _load_mids(asset_path: Path) -> dict[str, str]:
    if not asset_path.exists():
        raise MidAssetError(
            f"MID asset not found: {asset_path} (committed, never fetched)"
        )
    try:
        raw = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MidAssetError(f"MID asset unreadable: {asset_path}: {exc}") from exc
    mids = raw.get("mids") if isinstance(raw, dict) else None
    if not isinstance(mids, dict) or not mids:
        raise MidAssetError(f"MID asset has no 'mids' mapping: {asset_path}")
    table: dict[str, str] = {}
    for mid, country in mids.items():
        if not (isinstance(mid, str) and len(mid) == 3 and mid.isdigit() and mid[0] in "234567"):
            raise MidAssetError(f"malformed MID key {mid!r} (want 3 digits starting 2-7)")
        if not isinstance(country, str) or not country:
            raise MidAssetError(f"malformed country for MID {mid!r}: {country!r}")
        table[mid] = country
    return table


@lru_cache(maxsize=1)
def _mid_map() -> dict[str, str]:
    """Process-wide load-once singleton (fail-fast on first use)."""
    return _load_mids(DEFAULT_ASSET)


def _digits(mmsi: object) -> str | None:
    """A 9-digit MMSI string, or None if the input is not one. Leading zeros are
    significant (coast/AtoN formats), so the input must already be 9 characters —
    an int that dropped leading zeros is not silently re-padded."""
    if isinstance(mmsi, str):
        s = mmsi.strip()
    elif isinstance(mmsi, int):
        s = str(mmsi)
    else:
        return None
    return s if (len(s) == 9 and s.isdigit()) else None


def mmsi_to_flag(mmsi: object) -> tuple[str | None, str]:
    """(flag_country | None, station_type) for an MMSI.

    Deterministic; no I/O beyond the one-time asset load. flag_country is set
    ONLY for a ship station whose MID is allocated; every other case returns a
    None country with an honest station_type (see module docstring).
    """
    s = _digits(mmsi)
    if s is None:
        return (None, ST_OTHER)          # not a 9-digit MMSI
    d0 = s[0]
    if d0 in "234567":                    # ship station: leading 3 digits = MID
        return (_mid_map().get(s[:3]), ST_SHIP)
    if d0 == "0":                         # 00MID… coast, 0MID… group ship
        return (None, ST_COAST if s[1] == "0" else ST_OTHER)
    if d0 == "1":                         # 111MID… SAR aircraft
        return (None, ST_SAR if s.startswith("111") else ST_OTHER)
    if d0 == "9":
        if s.startswith("970"):           # AIS-SART (SAR transmitter)
            return (None, ST_SAR)
        if s.startswith("99"):            # aid to navigation
            return (None, ST_ATON)
        return (None, ST_OTHER)           # 98 craft-assoc, 972 MOB, 974 EPIRB, other 9xx
    return (None, ST_OTHER)               # 8xxxxxxxx handheld, and anything else


@lru_cache(maxsize=1)
def _mid_iso_map() -> dict[str, str]:
    """MID -> ISO alpha-2, loaded once from the committed asset (MID-ISO-1)."""
    with ISO_ASSET.open(encoding="utf-8") as f:
        data = json.load(f)
    mids = data.get("mids")
    if not isinstance(mids, dict) or not mids:
        raise RuntimeError(f"{ISO_ASSET}: missing or empty 'mids'")
    return {str(k): str(v) for k, v in mids.items() if v}


def mmsi_to_iso(mmsi: object) -> str | None:
    """ISO 3166-1 alpha-2 of a ship station's FLAG STATE, or None.

    ⛔ RESOLVED MID -> ISO DIRECTLY from the MMSI's leading three digits. The country NAME
    is never an intermediary: name matching fails silently on exactly the rows that matter
    (measured — 8 of 292 names differ in form alone between the two assets, e.g.
    Syria / Syrian Arab Republic, United States / United States of America).

    ⛔ DESCRIPTIVE ONLY, AND DELIBERATELY NOT `country_iso`. That module owns the SEVEN AOI
    countries and governs scoping and filtering — an answer to "is this one of the seven?".
    This answers "which registry is this hull flagged to?", which is global and says nothing
    about where the vessel is. Merging the two would let a Panamanian registration read as a
    country of interest.

    None for anything that is not a ship station with an allocated MID — the same rule
    `mmsi_to_flag` applies, so the two can never disagree about what a ship station is.
    """
    s = _digits(mmsi)
    if s is None or s[0] not in "234567":
        return None
    return _mid_iso_map().get(s[:3])
