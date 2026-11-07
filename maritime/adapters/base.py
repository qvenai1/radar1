"""
The adapter contract every maritime source implements.

Two adapter kinds share it:
  * live adapters (AISStream) — long-running `run()` pushing events into
    the Fleet until stopped;
  * lookup adapters (GFW, Equasis, Copernicus) — request/response methods
    with their own rate limiting; `run()` is a no-op for them.

Keeping the contract this small is what makes sources freely pluggable:
tracker_maritime.py only knows `run(fleet)` / `stop()` / `health()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..fleet import Fleet


class MaritimeAdapter(ABC):
    #: machine name recorded into VesselState.source
    name: str = "base"
    #: "terrestrial" | "satellite" — recorded into VesselState.data_origin
    data_origin: str = "terrestrial"

    @abstractmethod
    async def run(self, fleet: Fleet) -> None:
        """Long-running feed loop (live adapters) or immediate return
        (lookup adapters). Must survive upstream failures internally —
        reconnect/backoff is the adapter's job, never the caller's."""

    @abstractmethod
    def stop(self) -> None:
        """Request a graceful shutdown of run()."""

    def health(self) -> dict[str, Any]:
        """Adapter-specific liveness/counters for the health endpoint."""
        return {"name": self.name, "data_origin": self.data_origin}
