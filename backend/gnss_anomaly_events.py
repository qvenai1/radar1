"""Pure GNSS-anomaly EVENT logic — G4a. Aggregates a vessel's per-fix implausible
verdicts (position_quality/anomaly_flags, migration 027) into per-vessel EPISODES
(contiguous runs of implausible fixes, the same grouping discipline as A1's gaps),
and classifies each episode into the generalized maritime_events tiers — the Step-0
three-way rule, frozen from the measurement on the 2026-07-20-frozen set:

  * 'area-jamming' (gnss_type='area')    — the episode sits in a cell x window with
    >= AREA_MIN_VESSELS distinct implausible vessels: environmental jamming, the
    vessel is a VICTIM among many (the Amman decoy peaked at 156 vessels/cell·hour).
  * 'glitch' (gnss_type='glitch')        — the source has NO plausible track EVER
    (all its fixes implausible): a fixed/test/malfunctioning transmitter such as
    ELSEC_TEST (881 on-land fixes, 0 plausible), or a single-fix on-land ghost.
  * 'candidate' (gnss_type='individual') — a single vessel with a COHERENT plausible
    track punctuated by implausible fixes: a real vessel spoofed/jammed (actor OR
    victim → an investigation lead). The ONLY tier the (G4b) composite admits.

Precedence glitch > area > individual: a source with no real track is a glitch
wherever its bad fixes land; a real vessel in a dense jamming cell is an area victim;
otherwise it is an individual lead.

Pure + deterministic (mirrors rendezvous.py): NO I/O, NO DB, NO clock — all cell and
hourly-window bucketing happens here so the zone build and the episode
classification agree exactly (and no server-timezone date_trunc ambiguity). The
runner (scripts/detect_gnss_anomaly.py) owns all I/O and stamps computed_at.

Two zone variants (G4-pre / Step 3):
  * BROADCAST zone (:class:`JammingZone`) — distinct implausible-vessel count per
    cell x window built directly from implausible BROADCASTS (where spoofed positions
    LAND). It is the classification input. Naturally disjoint from feed silence
    (G4-pre: 2/3,988 implausible positions in any outage window, 0 in the open one),
    so it needs NO outage subtraction.
  * AFFECTED zone (:func:`affected_zone_cells`) — where vessels ACTUALLY were when
    jamming began, inferred from the last-good position before each episode. It
    reasons from the onset of implausibility, so it MUST structurally subtract feed
    outage windows: a feed-outage silence must never masquerade as a jam. This is the
    binding requirement (tested against the 33 h open window -> zero cells).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .gnss_anomaly import FLAG_ON_LAND

# --- frozen thresholds (Step-0 measurement on the frozen set) ----------------

#: A run of implausible fixes splits into a new episode when consecutive implausible
#: fixes are more than this apart, or when a plausible fix intervenes (recovery).
EPISODE_GAP_TOL_S = 3600.0
#: Zone cell size — the same 0.1° grid the coverage mask uses.
ZONE_CELL_DEG = 0.1
#: A cell x hourly-window with >= this many DISTINCT implausible vessels is an area
#: jamming zone. Step-0: area cells peaked 15–112 vessels/cell·hour; the next tier
#: peaked <= 9, so 10 sits in a clean gap.
AREA_MIN_VESSELS = 10

METHOD = "gnss-anomaly-detector-v1"

# classification tiers (maritime_events.classification, migration 028) + subtype
CANDIDATE, AREA_JAMMING, GLITCH = "candidate", "area-jamming", "glitch"
TYPE_INDIVIDUAL, TYPE_AREA, TYPE_GLITCH = "individual", "area", "glitch"


def zone_cell(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / ZONE_CELL_DEG), math.floor(lon / ZONE_CELL_DEG))


def window_key(ts: Any) -> Any:
    """Hourly window bucket (UTC) — deterministic, matches on both build paths."""
    return ts.replace(minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class Fix:
    ts: Any
    lat: float
    lon: float
    implausible: bool
    flags: tuple[str, ...] = ()
    implied_kn: float | None = None
    inland_km: float | None = None


@dataclass(frozen=True, slots=True)
class Episode:
    start_ts: Any
    end_ts: Any
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    n_fixes: int
    flags: tuple[str, ...]
    implied_speed_max: float | None
    inland_km_max: float | None
    cells_windows: tuple[tuple[int, int, Any], ...]        # (clat, clon, window) per fix
    on_land_cells_windows: tuple[tuple[int, int, Any], ...]  # subset where the fix was on_land


class JammingZone:
    """Distinct implausible-vessel count per (cell, hourly window) — the BROADCAST
    variant. ``is_area`` when the count reaches :data:`AREA_MIN_VESSELS`."""

    __slots__ = ("_counts",)

    def __init__(self, counts: dict[tuple[int, int, Any], int]) -> None:
        self._counts = counts

    def vessels(self, cw: tuple[int, int, Any]) -> int:
        return self._counts.get(cw, 0)

    def is_area(self, cw: tuple[int, int, Any]) -> bool:
        return self._counts.get(cw, 0) >= AREA_MIN_VESSELS

    @property
    def area_cells(self) -> int:
        return sum(1 for n in self._counts.values() if n >= AREA_MIN_VESSELS)

    @classmethod
    def from_positions(cls, positions: Any) -> "JammingZone":
        """Build from implausible-position rows (mmsi, ts, lat, lon) — Python
        bucketing so it agrees exactly with episode classification."""
        d: dict[tuple[int, int, Any], set] = defaultdict(set)
        for mmsi, ts, lat, lon in positions:
            clat, clon = zone_cell(lat, lon)
            d[(clat, clon, window_key(ts))].add(mmsi)
        return cls({k: len(v) for k, v in d.items()})


def _make_episode(run: list[Fix]) -> Episode:
    cws = [(*zone_cell(f.lat, f.lon), window_key(f.ts)) for f in run]
    on_land = [cw for f, cw in zip(run, cws, strict=True) if FLAG_ON_LAND in f.flags]
    implied = [f.implied_kn for f in run if f.implied_kn is not None]
    inland = [f.inland_km for f in run if f.inland_km is not None]
    return Episode(
        start_ts=run[0].ts, end_ts=run[-1].ts,
        start_lat=run[0].lat, start_lon=run[0].lon,
        end_lat=run[-1].lat, end_lon=run[-1].lon,
        n_fixes=len(run),
        flags=tuple(sorted({fl for f in run for fl in f.flags})),
        implied_speed_max=(round(max(implied), 2) if implied else None),
        inland_km_max=(round(max(inland), 2) if inland else None),
        cells_windows=tuple(dict.fromkeys(cws)),
        on_land_cells_windows=tuple(dict.fromkeys(on_land)),
    )


def episodes(fixes: list[Fix], gap_tol_s: float = EPISODE_GAP_TOL_S) -> list[Episode]:
    """Contiguous runs of implausible fixes per vessel (fixes sorted by ts). A run is
    broken by an intervening plausible fix (recovery) OR a > gap_tol_s break between
    consecutive implausible fixes. Only implausible fixes form episodes."""
    out: list[Episode] = []
    run: list[Fix] = []
    for f in fixes:
        if f.implausible:
            if run and (f.ts - run[-1].ts).total_seconds() > gap_tol_s:
                out.append(_make_episode(run))
                run = []
            run.append(f)
        elif run:                       # a plausible fix ends the current episode
            out.append(_make_episode(run))
            run = []
    if run:
        out.append(_make_episode(run))
    return out


def classify_episode(
    episode: Episode, zone: JammingZone, has_plausible_track: bool,
) -> tuple[str, str, dict[str, Any]]:
    """(classification, gnss_type, detail_extra) for one episode. Precedence
    glitch > area > individual."""
    if not has_plausible_track:
        # a source that has never broadcast a plausible position — a bad transmitter.
        return GLITCH, TYPE_GLITCH, {"neighbours_normal": None, "decoy_match": False}

    area_cws = [cw for cw in episode.cells_windows if zone.is_area(cw)]
    if area_cws:
        top = max(area_cws, key=zone.vessels)
        # a decoy is a shared ON-LAND area cell (spoof-to-fixed-point, e.g. Amman).
        decoy = any(zone.is_area(cw) for cw in episode.on_land_cells_windows)
        return AREA_JAMMING, TYPE_AREA, {
            "neighbours_normal": False,
            "decoy_match": decoy,
            "cell": [top[0], top[1]],
            "window": top[2].isoformat(),
            "distinct_vessels_in_cell": zone.vessels(top),
        }
    # a coherent real track punctuated by isolated implausible fixes -> a lead.
    return CANDIDATE, TYPE_INDIVIDUAL, {"neighbours_normal": True, "decoy_match": False}


def affected_zone_cells(
    origins: Any, outage_windows: Any,
) -> dict[tuple[int, int, Any], int]:
    """The AFFECTED-zone map: distinct vessels per (cell, window) of the REAL location
    where jamming began (the last-good position before each episode).

    ``origins`` is (mmsi, ts, lat, lon) of each episode's last plausible fix.
    ``outage_windows`` is a list of (start, end); an origin whose ts falls inside any
    outage window is EXCLUDED — a feed-outage silence must never be read as a jam
    (G3-addendum discipline). This is the binding subtraction requirement."""
    d: dict[tuple[int, int, Any], set] = defaultdict(set)
    for mmsi, ts, lat, lon in origins:
        if any(a <= ts <= b for a, b in outage_windows):
            continue
        d[(*zone_cell(lat, lon), window_key(ts))].add(mmsi)
    return {k: len(v) for k, v in d.items()}


#: Human-facing caveat — mirrors the layer's honest framing.
CANDIDATE_CAVEAT = (
    "Data-quality event, not proof of intent. gnss_type='area' marks an environmental-"
    "jamming VICTIM (many vessels implausible in one cell/window) — excluded from the "
    "actor-centric composite. 'glitch' marks a source with no real track (a bad/test "
    "transmitter) — excluded. Only 'individual' (classification='candidate') is an "
    "investigation lead: a real vessel whose coherent track was spoofed/jammed. GNSS "
    "implausibility can be deliberate spoofing, area jamming, or a receiver fault; "
    "this classifies the likely cause, it does not accuse."
)
