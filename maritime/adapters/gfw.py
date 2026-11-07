"""
Global Fishing Watch adapter — satellite AIS gap events (dark-vessel detection).

REST, free with registration; token from $GFW_API_TOKEN. This is a LOOKUP
adapter (request/response), not a live feed — run() is a no-op so it plugs
into the same interface without pretending to stream.

WHY GFW HERE. The free terrestrial AIS feed (AISStream) is blind in the Persian
Gulf / Red Sea (no volunteer receivers) and on the open ocean. GFW's events are
built from SATELLITE AIS, so it can surface what terrestrial AIS cannot: a vessel
that WENT DARK (stopped transmitting) in those very waters — the primary
dark-activity signal. data_origin is therefore "satellite".

The official client (`gfwapiclient`) is imported lazily inside the methods so the
maritime layer runs without it installed; absence degrades to a clear disabled
state, never an import crash.

Response shape is PINNED to the live v3 API (verified against a real token, not
guessed): each gap event carries `start`, `end`, `position{lat,lon}`,
`gap{duration_hours, intentional_disabling, distance_km}`, and
`vessel{ssvid(=MMSI), name, flag, type}`.

Docs: https://globalfishingwatch.org/our-apis/
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..fleet import Fleet
from .base import MaritimeAdapter

logger = logging.getLogger("maritime.gfw")

GAPS_DATASET = "public-global-gaps-events:latest"


def _f(v: Any) -> float | None:
    """GFW returns some numerics as strings — coerce, or None."""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class GfwAdapter(MaritimeAdapter):
    name = "gfw"
    data_origin = "satellite"  # gap events are built from satellite AIS

    def __init__(self, token: str | None = None, min_interval_s: float = 2.0) -> None:
        self._token = token or os.getenv("GFW_API_TOKEN", "")
        self._min_interval = min_interval_s  # client-side rate limit floor
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def run(self, fleet: Fleet) -> None:  # noqa: ARG002 — lookup adapter
        if not self.enabled:
            logger.info("GFW adapter disabled (no GFW_API_TOKEN)")
        return

    def stop(self) -> None:
        return

    def _gfw(self) -> Any:
        """Lazily build (and cache) the official client. Import is deferred so the
        maritime layer runs without gfwapiclient installed."""
        if self._client is None:
            import gfwapiclient as gfw  # lazy: optional dependency
            self._client = gfw.Client(access_token=self._token)
        return self._client

    async def find_ais_gaps(self, *, mmsi: str | None = None, imo: str | None = None,
                            start: str, end: str,
                            region: dict[str, Any] | None = None,
                            limit: int = 100) -> list[dict[str, Any]]:
        """AIS-off events (a vessel went dark) over [start, end] (UTC dates) —
        the core dark-activity signal, from SATELLITE AIS. Filter by a GeoJSON
        `region` (e.g. a Persian-Gulf polygon) and/or a specific vessel
        (mmsi/imo). Each returned gap:
            {start, end, duration_h, lat, lon, mmsi, name, flag,
             intentional (bool | None), distance_km, data_origin}.
        A LEAD, not proof — a gap can be a receiver outage or a legitimate
        silence; `intentional` is GFW's own likelihood flag, not a verdict."""
        self._require()
        client = self._gfw()
        kwargs: dict[str, Any] = {"datasets": [GAPS_DATASET],
                                  "start_date": start, "end_date": end, "limit": limit}
        if region is not None:
            kwargs["geometry"] = region
        if mmsi or imo:
            vid = await self._resolve_vessel_id(mmsi=mmsi, imo=imo)
            if vid is None:
                return []          # unknown vessel → no gaps to report
            kwargs["vessels"] = [vid]

        res = await client.events.get_all_events(**kwargs)
        out: list[dict[str, Any]] = []
        for ev in res.data():
            d = ev.model_dump() if hasattr(ev, "model_dump") else dict(ev)
            gap = d.get("gap") or {}
            pos = d.get("position") or {}
            ves = d.get("vessel") or {}
            out.append({
                "start": str(d.get("start")) if d.get("start") else None,
                "end": str(d.get("end")) if d.get("end") else None,
                "duration_h": _f(gap.get("duration_hours")),
                "lat": _f(pos.get("lat")),
                "lon": _f(pos.get("lon")),
                "mmsi": ves.get("ssvid"),
                "name": ves.get("name"),
                "flag": ves.get("flag"),
                "intentional": gap.get("intentional_disabling"),
                "distance_km": _f(gap.get("distance_km")),
                "data_origin": "satellite",
            })
        return out

    async def _resolve_vessel_id(self, *, mmsi: str | None = None,
                                 imo: str | None = None) -> str | None:
        """MMSI/IMO → GFW vesselId (needed to filter events by vessel)."""
        query = mmsi or imo
        if not query:
            return None
        res = await self._gfw().vessels.search_vessels(
            query=str(query),
            datasets=["public-global-vessel-identity:latest"], limit=1)
        rows = res.data()
        if not rows:
            return None
        d = rows[0].model_dump() if hasattr(rows[0], "model_dump") else dict(rows[0])
        # search returns combinedSourcesInfo[].vesselId; fall back to a top-level id
        info = (d.get("combined_sources_info") or d.get("combinedSourcesInfo") or [])
        if info and isinstance(info, list):
            vid = info[0].get("vessel_id") or info[0].get("vesselId")
            if vid:
                return str(vid)
        return d.get("id") or d.get("vessel_id")

    async def get_track(self, *, mmsi: str | None = None, imo: str | None = None,
                        start: str, end: str) -> list[dict[str, Any]]:
        """Historical positions for one vessel over [start, end]. Deferred: GFW
        serves tracks through a separate binned endpoint; the dark-activity value
        this project needs is `find_ais_gaps`, which is live. Wire on demand."""
        self._require()
        raise NotImplementedError(
            "GFW per-vessel track fetch is deferred; use find_ais_gaps for the "
            "dark-vessel signal (live)."
        )

    def _require(self) -> None:
        if not self.enabled:
            raise RuntimeError("GFW adapter is disabled — set GFW_API_TOKEN")

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "data_origin": self.data_origin,
                "enabled": self.enabled,
                "status": "active (satellite AIS gap events)" if self.enabled
                          else "disabled (no GFW_API_TOKEN)"}
