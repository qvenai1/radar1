"""
In-memory fleet state — the bridge between AIS's continuous event stream
and the project's snapshot architecture.

The aviation pipeline polls whole snapshots; AIS pushes single-vessel
events. Fleet accumulates those events (merging PositionReport and
ShipStaticData per MMSI) so the collector can emit a coherent fleet
snapshot every N seconds — after which the existing storage pattern
(atomic latest + JSONL archive + optional DB mirror) applies unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .schema import VesselState


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


class Fleet:
    def __init__(self) -> None:
        self._vessels: dict[str, VesselState] = {}
        self.position_count = 0
        self.static_count = 0
        # Every NEW signal (a report carrying new information — position moved
        # or static identity changed) is buffered here as an event dict with a
        # "kind" of "position" | "static". The collector drains this each tick
        # to the raw log (all events) and vessel_states (position events).
        # Identical repeats carry no new info and are NOT buffered.
        self._events: list[dict[str, Any]] = []

    def __len__(self) -> int:
        return len(self._vessels)

    def _get(self, key: str, source: str, *,
             mmsi: str | None = None, ship_id: str | None = None) -> VesselState:
        """Fetch (or create) the state accumulated under `key`. `key` is the
        vessel's identity handle — its MMSI for AIS, its ship_id for MarineTraffic.
        The identity fields recorded on a newly-created state default to the key
        (the MMSI case), or are set explicitly for a ship_id-keyed source."""
        v = self._vessels.get(key)
        if v is None:
            if ship_id is not None:
                # ship_id-keyed source (MarineTraffic): mmsi is exactly what was
                # passed — usually None, and must NOT be back-filled from the key.
                v = VesselState(mmsi=mmsi, ship_id=ship_id, source=source)
            else:
                # mmsi-keyed source (AIS): the key IS the mmsi.
                v = VesselState(mmsi=mmsi if mmsi is not None else key, source=source)
            self._vessels[key] = v
        return v

    def apply_position(self, mmsi: str, pos: dict[str, Any], source: str) -> bool:
        """MMSI-keyed position merge (AIS). Thin wrapper over the keyed form —
        the historical AIS-facing API, unchanged."""
        return self.apply_position_keyed(mmsi, pos, source, mmsi=mmsi)

    def apply_static(self, mmsi: str, static: dict[str, Any], source: str) -> bool:
        """MMSI-keyed static merge (AIS). Thin wrapper over the keyed form."""
        return self.apply_static_keyed(mmsi, static, source, mmsi=mmsi)

    def apply_position_keyed(self, key: str, pos: dict[str, Any], source: str, *,
                             mmsi: str | None = None, ship_id: str | None = None) -> bool:
        """Merge a position report under an explicit identity `key`. Returns True
        (and buffers a 'position' event) only if it carries NEW information —
        position/speed/course/heading/status changed. Identical repeats dropped.

        A ship_id-keyed source (MarineTraffic) passes ship_id (+ mmsi if it has
        one); an MMSI source lets mmsi default to the key."""
        v = self._get(key, source, mmsi=mmsi, ship_id=ship_id)
        before = (v.lat, v.lon, v.speed, v.course, v.heading, v.nav_status)
        v.merge_position(pos)
        v.source = source
        self.position_count += 1
        after = (v.lat, v.lon, v.speed, v.course, v.heading, v.nav_status)
        if before == after:
            return False
        ev = v.to_dict()
        ev["kind"] = "position"
        self._events.append(ev)
        return True

    def apply_static_keyed(self, key: str, static: dict[str, Any], source: str, *,
                           mmsi: str | None = None, ship_id: str | None = None) -> bool:
        """Merge static data under an explicit identity `key`. Returns True (and
        buffers a 'static' event) only if identity actually changed."""
        v = self._get(key, source, mmsi=mmsi, ship_id=ship_id)
        before = (v.imo, v.name, v.callsign, v.ship_type_code, v.destination,
                  v.draught, v.length_m, v.beam_m)
        v.merge_static(static)
        self.static_count += 1
        after = (v.imo, v.name, v.callsign, v.ship_type_code, v.destination,
                 v.draught, v.length_m, v.beam_m)
        if before == after:
            return False
        ev = v.to_dict()
        ev["kind"] = "static"
        self._events.append(ev)
        return True

    def snapshot(self, max_age_s: float = 600.0) -> list[dict[str, Any]]:
        """Current fleet picture: vessels WITH a position fix newer than
        max_age_s. Stale vessels are evicted (bounded memory — the AIS
        equivalent of the aviation replace-wholesale rule)."""
        now = datetime.now(timezone.utc).timestamp()
        out: list[dict[str, Any]] = []
        stale: list[str] = []
        for key, v in self._vessels.items():
            fix = _parse_iso(v.timestamp)
            if fix is None or v.lat is None or v.lon is None:
                continue
            if now - fix > max_age_s:
                stale.append(key)
                continue
            out.append(v.to_dict())
        for key in stale:
            del self._vessels[key]
        return out

    def drain_events(self) -> list[dict[str, Any]]:
        """Every new-information signal since the last drain (position + static
        events, in arrival order). The collector writes them all to the raw
        log and the position ones to vessel_states. Cleared after draining."""
        out = self._events
        self._events = []
        return out
