"""StateVector-shaped time-series producer (scraplan integration, P1).

adsb_scraper keeps two archives with distinct roles:

* ``aircraft.jsonl`` — the DISCOVERY CATALOGUE: one row per unique ICAO,
  first-seen-wins (the accumulator dedups). Untouched by this module.
* ``state_vectors.jsonl`` — the POSITION TIME-SERIES this module produces: one
  scraplan-``StateVector``-shaped line PER AIRCRAFT PER CYCLE, **no dedup**. This
  is what scraplan will later ingest.

The mapping is unit-exact against scraplan's ``StateVector`` model and the
``state_vectors`` table (feet→metres, knots→m/s, the ``alt == "ground"`` sentinel
→ ``on_ground``), and the emitted keys match scraplan's ``ingest.parse_record``.

Source restriction (dedup safety): adsb_scraper can optionally fetch from
OpenSky, but scraplan already has its own ``opensky`` source. Emitting OpenSky
observations here under ``source='adsbexchange'`` would double-feed OpenSky under
two tags. So the producer emits ONLY genuine ADS-B Exchange observations
(browser / interception / ADSBx REST) and never OpenSky ones.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import constants as C
from .persistence import DurableJsonlWriter
from .utils import Clock
from .validation import ValidatedRecord

#: Unit conversions (correctness traps — exact factors).
FEET_TO_METRES = 0.3048
KNOTS_TO_MPS = 0.514444

#: Constant tags for every row this producer emits (see module docstring).
STATEVECTOR_SOURCE = "adsbexchange"

#: Fetch-strategy source names that ARE ADS-B Exchange. These mirror the
#: ``.name`` of the browser/interception/ADSBx strategies in ``fetcher.py``;
#: ``opensky_rest`` is deliberately excluded (see the source restriction above).
#: A test asserts this set stays in sync with the strategy names.
ADSBEXCHANGE_SOURCES = frozenset({"browser_evaluate", "interception", "adsbx_api"})


def is_adsbexchange_source(source: str) -> bool:
    """Whether an observation's fetch source may be emitted to the archive."""
    return source in ADSBEXCHANGE_SOURCES


def _on_ground(raw: Mapping[str, Any]) -> bool:
    """The ADS-B ``"ground"`` altitude sentinel, read from the raw observation.

    The parser coerces ``"ground"`` to a null numeric altitude, so the sentinel
    survives only in the untouched raw element — check every altitude alias.
    """
    return any(raw.get(alias) == "ground" for alias in C.AIRCRAFT_FIELD_ALIASES["altitude"])


def _squawk(raw: Mapping[str, Any]) -> str:
    value = raw.get("squawk")
    return str(value) if value not in (None, "") else ""


def to_state_vector(record: ValidatedRecord, snapshot_time: int) -> dict[str, Any]:
    """Map one validated observation to a scraplan ``StateVector``-shaped dict.

    ``snapshot_time`` is the integer UNIX second of the cycle, so scraplan's
    ``ts = to_timestamp(snapshot_time)`` CHECK holds at second granularity.
    """
    ts = datetime.fromtimestamp(snapshot_time, tz=UTC).isoformat()
    on_ground = _on_ground(record.raw)

    baro_altitude: float | None = None
    if not on_ground and record.altitude is not None:
        baro_altitude = record.altitude * FEET_TO_METRES

    velocity = None if record.ground_speed is None else record.ground_speed * KNOTS_TO_MPS

    return {
        "snapshot_time": snapshot_time,
        "ts": ts,
        "icao24": record.icao,
        "callsign": record.callsign or "",
        "origin_country": "",  # no ADS-B equivalent
        "time_position": None,
        "last_contact": None,
        "longitude": record.longitude,
        "latitude": record.latitude,
        "baro_altitude": baro_altitude,  # metres, or NULL / "ground"
        "geo_altitude": None,  # no reliable equivalent -> NULL
        "on_ground": on_ground,
        "velocity": velocity,  # m/s
        "true_track": record.track,  # degrees, unchanged
        "vertical_rate": None,
        "squawk": _squawk(record.raw),
        "spi": False,
        "position_source": None,
        "category": None,
        "fetched_at": ts,
        "source": STATEVECTOR_SOURCE,
        # geo-classification provenance (scraplan migration 012 columns)
        "country": record.country,
        "near_country": record.near,
        "position_quality": record.position_quality.value,
        "validation_flags": sorted(flag.value for flag in record.flags),
        "distance_km": record.distance_km,
        "geo_source": STATEVECTOR_SOURCE,
    }


@runtime_checkable
class StateVectorProducer(Protocol):
    """Per-cycle time-series writer the orchestrator feeds validated records to."""

    async def write_snapshot(
        self, records: Iterable[ValidatedRecord], snapshot_time: int
    ) -> int: ...


class StateVectorSink:
    """Durable, append-only (NO dedup) StateVector time-series sink.

    Reuses :class:`~adsb_scraper.persistence.DurableJsonlWriter` for identical
    buffering/fsync/crash-repair discipline, and writes one line per eligible
    aircraft per cycle. Ineligible observations (OpenSky-sourced, lacking a
    valid ICAO, or OUTSIDE the area of interest) are skipped, never written.

    SAMPLING FRAME, NOT A CENSUS. The time-series is bounded to the seven-country
    neighborhood (``AOI_COUNTRIES``) plus the ``--area-buffer-km`` border buffer:
    the same frame the ICAO catalogue has always used. Measured before this gate
    existed, **~95 % of every line written here was global traffic** far outside
    the neighborhood (96.65 % with no AOI country, 94.53 % beyond 1000 km, out to
    8586 km) — the archive grew ~9.4 GB/day to record aircraft the project does
    not study.

    The area decision itself is NOT made here and is not recomputed: the
    validator stamps ``ValidationFlag.OUTSIDE_AREA`` once per record
    (``validation.py``), and this gate reads the same ``record.outside_area``
    property, under the same ``persist_out_of_area`` override, that the
    accumulator has always applied to the catalogue. Only the *place* it takes
    effect is new — previously the time-series was written before that gate ran,
    which is why a scraper with a correct AOI still produced a global archive.

    NEAR records are kept: they carry ``NEAR_BORDER``, not ``OUTSIDE_AREA``, and
    the neighborhood is the point. Positionless records are unclassified and so
    are also kept — identical to the catalogue's behaviour.

    Set ``persist_out_of_area=True`` to restore the unbounded global time-series.
    """

    def __init__(
        self,
        path: Path,
        *,
        batch_size: int = 100,
        flush_interval: float = 5.0,
        fsync: bool = True,
        persist_out_of_area: bool = False,
        clock: Clock | None = None,
    ) -> None:
        self._persist_out_of_area = persist_out_of_area
        self._skipped_out_of_area = 0
        self._writer = DurableJsonlWriter(
            path,
            name="state-vectors",
            batch_size=batch_size,
            flush_interval=flush_interval,
            fsync=fsync,
            clock=clock,
        )

    async def __aenter__(self) -> StateVectorSink:
        await self._writer.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._writer.aclose()

    async def open(self) -> None:
        await self._writer.open()

    async def aclose(self) -> None:
        await self._writer.aclose()

    async def flush(self) -> None:
        await self._writer.flush()

    async def write_snapshot(self, records: Iterable[ValidatedRecord], snapshot_time: int) -> int:
        """Append one StateVector line per eligible record. Returns lines written."""
        written = 0
        for record in records:
            # icao24 is the scraplan natural key — a record without a valid ICAO
            # cannot be a StateVector.
            if record.icao is None or not record.has_valid_identity:
                continue
            # NEVER emit OpenSky-sourced observations into the adsbexchange feed.
            if not is_adsbexchange_source(record.source):
                continue
            # Sampling frame: the same OUTSIDE_AREA policy the accumulator applies
            # to the catalogue, applied here BEFORE the line is written. Not a
            # recomputation — the validator already stamped the flag.
            if record.outside_area and not self._persist_out_of_area:
                self._skipped_out_of_area += 1
                continue
            await self._writer.write_line(to_state_vector(record, snapshot_time))
            written += 1
        return written

    @property
    def skipped_out_of_area(self) -> int:
        """Records withheld from the time-series by the sampling frame."""
        return self._skipped_out_of_area

    @property
    def total_written(self) -> int:
        return self._writer.total_written
