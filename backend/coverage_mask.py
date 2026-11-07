"""Data-driven AIS coverage mask — where we ACTUALLY receive positions.

Terrestrial AIS coverage is NOT a simple function of distance-to-coast: Cyprus
sits mid-sea, so its receivers light up cells that are far from any mainland.
Using raw coast-distance to decide "dense vs sparse" would wrongly call
well-covered mid-sea waters an "edge". This mask instead bins the OBSERVED
position history into geo cells and calls a cell dense when it has carried at
least :data:`DENSE_MIN_POSITIONS` positions — so "dense" reflects real receiver
geography (mainland + Cyprus + shipping lanes).

Parameters (derived from the position-density distribution in vessel_states):
  * CELL_DEG = 0.1  (~10 km cells at AOI latitudes) — fine enough to resolve the
    coverage edge, coarse enough for a stable per-cell count.
  * DENSE_MIN_POSITIONS = 100 — per-cell count over the analysed history
    (~9 positions/cell/day over the ~11-day window = RELIABLY covered). The
    occupied-cell distribution is p50≈22, p75≈74, p90≈189; 100 sits near p85 and
    marks 832 cells (~19%) carrying ~87% of all positions as dense, so thin edge
    cells fall to sparse and a "dense" gap genuinely means "we should have seen
    it". A lower floor (e.g. 30) calls marginally-covered cells dense and inflates
    candidates; a higher one is stricter. It is an ABSOLUTE floor over the
    analysed window, rebuilt from current history each run so it tracks coverage
    as data accumulates.
    LIMITATION (documented): per-cell position count conflates TRAFFIC with
    COVERAGE — a genuinely-covered but low-traffic cell can read as sparse, so
    the mask errs CONSERVATIVE (a borderline gap is 'edge', not 'candidate').

Reusable by every behavioural pattern (gaps now; rendezvous/anomaly later):
build once, then call :meth:`is_dense`.

G3 — the density / port / busy queries EXCLUDE implausible fixes (see
:data:`PLAUSIBLE_FILTER`): a GNSS decoy cluster (the Amman-airport cell) must not
be counted as coverage or manufacture a phantom anchorage/lane. Flag, never delete;
filter at consumption. NULL and 'suspect' both pass — only 'implausible' subtracts.
"""

from __future__ import annotations

import math
from typing import Any

CELL_DEG = 0.1
DENSE_MIN_POSITIONS = 100

#: G3 CONSUMPTION FILTER (flag, never delete — filter at consumption, not storage).
#: Implausible fixes (GNSS spoof / teleport / on-land, migration 027) are EXCLUDED
#: from mask density AND from every behavioural detector's position stream, so a
#: decoy cluster (e.g. the Amman-airport cell) can never manufacture phantom
#: coverage or phantom port/lane cells. NULL (not-yet-checked) and 'suspect' both
#: PASS: quality stamping is eventually consistent (15-min timers, no enforced
#: ordering) and recompute-and-replace self-corrects; and G0 showed only the
#: 'implausible' verdict is strong enough to subtract evidence (the 40-60 kn and
#: 2-5 km bands are explainable edge cases). Rows keep everything; consumers ignore.
PLAUSIBLE_FILTER = "position_quality IS DISTINCT FROM 'implausible'"

# The vessel_states query that yields (cell_lat, cell_lon, positions) for the
# builder. Kept here so the mask owns its definition of a "cell".
CELL_COUNTS_SQL = f"""
SELECT floor(latitude / {CELL_DEG})::int  AS clat,
       floor(longitude / {CELL_DEG})::int AS clon,
       count(*)                           AS n
FROM vessel_states
WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND {PLAUSIBLE_FILTER}
GROUP BY 1, 2
"""


def _cell(lat: float, lon: float, cell_deg: float) -> tuple[int, int]:
    return (math.floor(lat / cell_deg), math.floor(lon / cell_deg))


class CoverageMask:
    """A set of dense geo cells with an ``is_dense(lat, lon)`` test.

    Density is a property of the CELL (a location's receiver coverage), not of
    any one vessel — many vessels contribute to a cell's count.
    """

    __slots__ = ("_cell_deg", "_counts", "_dense", "_threshold")

    def __init__(self, counts: dict[tuple[int, int], int],
                 threshold: int = DENSE_MIN_POSITIONS, cell_deg: float = CELL_DEG) -> None:
        self._counts = counts
        self._threshold = threshold
        self._cell_deg = cell_deg
        self._dense = {c for c, n in counts.items() if n >= threshold}

    def is_dense(self, lat: float | None, lon: float | None) -> bool:
        """True iff (lat, lon) falls in a cell with >= threshold observed positions."""
        if lat is None or lon is None:
            return False
        return _cell(lat, lon, self._cell_deg) in self._dense

    def cell_density(self, lat: float | None, lon: float | None) -> int:
        """Observed position count of the cell containing (lat, lon) — the raw
        evidence behind is_dense (stored in event detail for transparency)."""
        if lat is None or lon is None:
            return 0
        return self._counts.get(_cell(lat, lon, self._cell_deg), 0)

    @property
    def dense_cells(self) -> int:
        return len(self._dense)

    @property
    def params(self) -> dict[str, Any]:
        return {"cell_deg": self._cell_deg, "threshold": self._threshold,
                "occupied_cells": len(self._counts), "dense_cells": len(self._dense)}

    @classmethod
    def from_rows(cls, rows: Any, threshold: int = DENSE_MIN_POSITIONS,
                  cell_deg: float = CELL_DEG) -> "CoverageMask":
        """Build from CELL_COUNTS_SQL rows (each with clat, clon, n)."""
        counts = {(r["clat"], r["clon"]): r["n"] for r in rows}
        return cls(counts, threshold=threshold, cell_deg=cell_deg)


async def build_coverage_mask(conn: Any, threshold: int = DENSE_MIN_POSITIONS,
                              cell_deg: float = CELL_DEG) -> CoverageMask:
    """Build the mask from the live vessel_states history (one grouped read)."""
    rows = await conn.fetch(CELL_COUNTS_SQL)
    return CoverageMask.from_rows(rows, threshold=threshold, cell_deg=cell_deg)


# --------------------------------------------------------------------------
# A2 rendezvous masks — finer 0.01deg (~1 km) cells, membership sets. Same
# data-driven binning as the coverage mask, different metric: these mark the
# confound zones a rendezvous must be OUTSIDE of. Parameters from the A2
# assessment (26k naive proximities -> 429 open-sea pairs -> 29 clean).
# --------------------------------------------------------------------------

PROX_CELL_DEG = 0.01
#: A cell that has held >= this many DISTINCT stationary (speed<1 kn) vessels is
#: a port/anchorage/marina (assessment: 268 such cells; removes 97% of pairs).
PORT_MIN_STATIONARY = 5
#: A cell that has held >= this many DISTINCT vessels EVER is a lane / waiting
#: area / traffic node (assessment: ~3,675 cells).
BUSY_MIN_DISTINCT = 8
#: Open-sea floor: the encounter must be > this M1 distance-to-coast (km).
OFFSHORE_MIN_KM = 10.0

PORT_CELLS_SQL = f"""
WITH s AS (
  SELECT DISTINCT mmsi,
         floor(latitude / {PROX_CELL_DEG})::int  AS clat,
         floor(longitude / {PROX_CELL_DEG})::int AS clon
  FROM vessel_states
  WHERE speed < 1.0 AND latitude IS NOT NULL AND longitude IS NOT NULL AND {PLAUSIBLE_FILTER})
SELECT clat, clon FROM s GROUP BY 1, 2 HAVING count(*) >= {PORT_MIN_STATIONARY}
"""

BUSY_CELLS_SQL = f"""
WITH s AS (
  SELECT DISTINCT mmsi,
         floor(latitude / {PROX_CELL_DEG})::int  AS clat,
         floor(longitude / {PROX_CELL_DEG})::int AS clon
  FROM vessel_states
  WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND {PLAUSIBLE_FILTER})
SELECT clat, clon FROM s GROUP BY 1, 2 HAVING count(*) >= {BUSY_MIN_DISTINCT}
"""


class CellSet:
    """A membership set of 0.01deg cells with a ``contains(lat, lon)`` test —
    the confound zones (port/anchorage, busy lanes) an open-sea rendezvous must
    fall OUTSIDE of. Data-driven, rebuilt from current history each run."""

    __slots__ = ("_cell_deg", "_cells")

    def __init__(self, cells: set[tuple[int, int]], cell_deg: float = PROX_CELL_DEG) -> None:
        self._cells = cells
        self._cell_deg = cell_deg

    def contains(self, lat: float | None, lon: float | None) -> bool:
        if lat is None or lon is None:
            return False
        return _cell(lat, lon, self._cell_deg) in self._cells

    def __len__(self) -> int:
        return len(self._cells)

    @classmethod
    def from_rows(cls, rows: Any, cell_deg: float = PROX_CELL_DEG) -> "CellSet":
        return cls({(r["clat"], r["clon"]) for r in rows}, cell_deg=cell_deg)


async def build_port_mask(conn: Any) -> CellSet:
    """Ports/anchorages/marinas: cells with many distinct stationary vessels."""
    return CellSet.from_rows(await conn.fetch(PORT_CELLS_SQL))


async def build_busy_mask(conn: Any) -> CellSet:
    """Lanes / waiting areas / traffic nodes: cells with many distinct vessels."""
    return CellSet.from_rows(await conn.fetch(BUSY_CELLS_SQL))
