"""
Copernicus Data Space — Sentinel-1 SAR / Sentinel-2 imagery availability.

Purpose: given a coordinate + time, find whether satellite imagery exists to
VISUALLY confirm a vessel that had switched its AIS off — the second source
that turns a spoofable AIS track into something verifiable. SAR (Sentinel-1)
matters most here: it images through cloud and darkness and shows ship hulls
as bright returns regardless of AIS.

Free with registration; credentials from $COPERNICUS_CLIENT_ID /
$COPERNICUS_CLIENT_SECRET (OData/OpenSearch catalogue is anonymous for
discovery, OAuth2 only for product download).

Status: STUB by design (spec allows deferring full download to phase 2).
`search_availability()` — catalogue query for scenes overlapping a point at
a time — is the phase-2 entry; the interface is fixed so alerting code can
target it now.

Docs: https://dataspace.copernicus.eu/
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..fleet import Fleet
from .base import MaritimeAdapter

logger = logging.getLogger("maritime.copernicus")

CATALOGUE_ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"


class CopernicusAdapter(MaritimeAdapter):
    name = "copernicus"
    data_origin = "satellite"

    def __init__(self) -> None:
        self._client_id = os.getenv("COPERNICUS_CLIENT_ID", "")
        self._client_secret = os.getenv("COPERNICUS_CLIENT_SECRET", "")

    @property
    def enabled(self) -> bool:
        # catalogue discovery is anonymous; download needs creds
        return True

    async def run(self, fleet: Fleet) -> None:  # noqa: ARG002 — lookup adapter
        return

    def stop(self) -> None:
        return

    async def search_availability(
        self, lat: float, lon: float, when: str,
        *, collection: str = "SENTINEL-1", window_hours: float = 24.0,
    ) -> list[dict[str, Any]]:
        """Scenes from `collection` overlapping (lat, lon) within
        ±window_hours of `when` (UTC ISO). Returns scene metadata
        (id, sensing time, footprint, product type) — NOT pixels."""
        raise NotImplementedError(
            "Copernicus catalogue search lands in phase 2 (OData spatial+"
            "temporal filter on CATALOGUE_ODATA). Interface fixed for the "
            "AIS-off verification flow."
        )

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "data_origin": self.data_origin,
                "download_creds": bool(self._client_id and self._client_secret),
                "status": "stub (phase 2) — SAR verification of dark vessels"}
