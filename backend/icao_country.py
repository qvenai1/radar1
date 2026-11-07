"""Deterministic country-of-registration from the ICAO 24-bit address (icao24).

This is a LOCAL lookup, not an inference and not a network call: ICAO assigns
each State one or more blocks of the 24-bit address space (Annex 10, Vol III),
so an aircraft's registration country is a function of its icao24 hex alone. It
is distinct from operator/owner country — a plane may be owned in one country
and registered (address-allocated) in another.

The allocation asset (backend/data/icao24_allocations.json) is committed and
loaded once; it is NEVER fetched at runtime. Some States' overseas territories
and special administrative regions are NESTED as narrower ranges inside a broader
State block (UK overseas territories inside 400000-43FFFF; Hong Kong inside
China). The ranges are therefore flattened into a non-overlapping interval map
where each elementary interval takes the NARROWEST (most specific) covering
range, so a nested territory wins over its enclosing State. Lookup is an O(log n)
binary search over that map; an unallocated/unknown address returns None (never
a guess).
"""

from __future__ import annotations

import json
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

DEFAULT_ASSET = Path(__file__).resolve().parent / "data" / "icao24_allocations.json"

#: Provenance label stored alongside the derived fact.
REGISTRATION_COUNTRY_SOURCE = "icao-24bit-allocation"

_ADDR_MIN, _ADDR_MAX = 0x000000, 0xFFFFFF


class AllocationAssetError(RuntimeError):
    """The ICAO allocation asset is missing, unreadable, or malformed (fail fast)."""


def _load_ranges(asset_path: Path) -> list[tuple[int, int, str]]:
    if not asset_path.exists():
        raise AllocationAssetError(
            f"ICAO allocation asset not found: {asset_path} (committed, never fetched)"
        )
    try:
        raw = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AllocationAssetError(f"ICAO allocation asset unreadable: {asset_path}: {exc}") from exc
    entries = raw.get("ranges") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise AllocationAssetError(f"ICAO allocation asset has no ranges: {asset_path}")
    ranges: list[tuple[int, int, str]] = []
    for e in entries:
        try:
            start = int(e["start"], 16)
            end = int(e["end"], 16)
            country = str(e["country"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AllocationAssetError(f"malformed allocation entry {e!r}: {exc}") from exc
        if not (_ADDR_MIN <= start <= end <= _ADDR_MAX):
            raise AllocationAssetError(f"allocation entry out of range / inverted: {e!r}")
        ranges.append((start, end, country))
    return ranges


def _flatten(ranges: list[tuple[int, int, str]]) -> tuple[list[int], list[tuple[int, str]]]:
    """Flatten possibly-overlapping ranges into a sorted non-overlapping interval
    map, each elementary interval taking the NARROWEST covering range so nested
    territories win. Returns (sorted_starts, [(end_inclusive, country), ...])."""
    bounds: set[int] = set()
    for start, end, _ in ranges:
        bounds.add(start)
        bounds.add(end + 1)  # half-open upper edge
    points = sorted(bounds)

    intervals: list[tuple[int, int, str]] = []  # (lo, hi_inclusive, country)
    for i in range(len(points) - 1):
        lo, hi = points[i], points[i + 1] - 1
        best_country: str | None = None
        best_width: int | None = None
        for start, end, country in ranges:
            if start <= lo and hi <= end:
                width = end - start
                if best_width is None or width < best_width:
                    best_width, best_country = width, country
        if best_country is not None:
            intervals.append((lo, hi, best_country))

    # Merge adjacent, contiguous intervals with the same country.
    merged: list[tuple[int, int, str]] = []
    for lo, hi, country in intervals:
        if merged and merged[-1][2] == country and merged[-1][1] + 1 == lo:
            merged[-1] = (merged[-1][0], hi, country)
        else:
            merged.append((lo, hi, country))

    starts = [lo for lo, _, _ in merged]
    payload = [(hi, country) for _, hi, country in merged]
    return starts, payload


class _AllocationMap:
    __slots__ = ("_payload", "_starts")

    def __init__(self, ranges: list[tuple[int, int, str]]) -> None:
        self._starts, self._payload = _flatten(ranges)

    def country(self, addr: int) -> str | None:
        i = bisect_right(self._starts, addr) - 1
        if i < 0:
            return None
        hi, country = self._payload[i]
        return country if addr <= hi else None


@lru_cache(maxsize=1)
def _allocation_map() -> _AllocationMap:
    """Process-wide load-once singleton (fail-fast on first use)."""
    return _AllocationMap(_load_ranges(DEFAULT_ASSET))


def icao24_to_registration_country(hex_addr: str | None) -> str | None:
    """Country/authority of registration for an icao24 hex, or None if the
    address is unallocated, unknown, or not a valid 24-bit hex. Deterministic;
    no I/O beyond the one-time asset load."""
    if not hex_addr or not isinstance(hex_addr, str):
        return None
    try:
        addr = int(hex_addr.strip(), 16)
    except ValueError:
        return None
    if not (_ADDR_MIN <= addr <= _ADDR_MAX):
        return None
    return _allocation_map().country(addr)
