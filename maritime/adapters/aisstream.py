"""
AISStream.io live adapter — the primary real-time maritime source.

Connects to wss://stream.aisstream.io/v0/stream, subscribes with the API
key from $AISSTREAM_API_KEY, and feeds PositionReport / ShipStaticData
events into the shared Fleet.

Resilience (spec requirement):
  * automatic reconnect with EXPONENTIAL BACKOFF on any disconnect;
  * every disconnect is LOGGED with duration — outages are analytically
    meaningful (they can mask "dark activity": a vessel going silent).
    Gaps are recorded to a JSONL so later analysis can tell a feed outage
    (all vessels drop) from a single vessel going dark (one MMSI drops).

HARD LIMIT — documented and surfaced in health():
  AISStream is TERRESTRIAL AIS only. Reliable coverage is ~40–60 NM off
  shore; the open ocean is invisible. data_origin is therefore always
  "terrestrial", and absence of a vessel is NOT evidence of absence.

Reconnect-backoff timing is injectable (sleep/connect) so tests can drive
the loop deterministically without a real socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ..fleet import Fleet
from .aisstream_parse import parse_envelope
from .base import MaritimeAdapter

logger = logging.getLogger("maritime.aisstream")

STREAM_URL = "wss://stream.aisstream.io/v0/stream"
GLOBAL_BBOX = [[[-90.0, -180.0], [90.0, 180.0]]]
BACKOFF_MIN = 1.0
BACKOFF_MAX = 60.0
FILTER_TYPES = ["PositionReport", "ShipStaticData"]


class AisStreamAdapter(MaritimeAdapter):
    name = "aisstream"
    data_origin = "terrestrial"  # free AIS never sees the open ocean

    def __init__(
        self,
        api_key: str,
        bounding_boxes: list | None = None,
        *,
        connect: Callable[[str], Awaitable[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("AISSTREAM_API_KEY is required")
        self._api_key = api_key
        self._bboxes = bounding_boxes or GLOBAL_BBOX
        # injected sleep (tests). Default is stop-aware so a long backoff is
        # interrupted the moment stop() is called.
        self._sleep = sleep if sleep is not asyncio.sleep else None
        self._connect = connect or self._default_connect
        self._stop = asyncio.Event()
        # health / gap-analysis counters
        self.connected = False
        self.messages = 0
        self.parse_failures = 0
        self.reconnects = 0
        self.backoff_history: list[float] = []  # observable in tests
        self._connected_at: float | None = None
        self._last_disconnect: dict[str, Any] | None = None
        # True once this session emitted feed_connect — so run()'s finally can
        # GUARANTEE a matching feed_disconnect even on a clean/normal closure.
        self._connect_emitted = False
        # operational signals (feed_connect / feed_disconnect) buffered for the
        # collector to drain into the unified signal log — no separate file.
        self._signals: list[tuple[str, dict[str, Any]]] = []

    # -- lifecycle --------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    async def run(self, fleet: Fleet) -> None:
        backoff = BACKOFF_MIN
        while not self._stop.is_set():
            opened_at = datetime.now(timezone.utc)
            try:
                ws = await self._connect(STREAM_URL)
            except Exception as exc:  # noqa: BLE001 — reconnect on any failure
                logger.warning("AISStream connect failed: %s — retry in %.0fs", exc, backoff)
                self.reconnects += 1
                await self._backoff_wait(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue

            clean = False
            self._connect_emitted = False
            session_error: str | None = None
            try:
                await self._session(ws, fleet)
                clean = True
            except Exception as exc:  # noqa: BLE001
                session_error = str(exc)
                logger.warning(
                    "AISStream session ended (%s) — reconnect in %.0fs", exc, backoff
                )
                self.reconnects += 1
            finally:
                self.connected = False
                # GUARANTEE a feed_disconnect for every session that emitted a
                # feed_connect — whether it ended by exception OR by a clean/normal
                # closure (async-for exhaustion on a 1000/1001 close). Emitting only
                # on the exception path let clean closures end a session SILENTLY,
                # leaving an unpaired feed_connect on the next reconnect (G3 Finding
                # B). The reason distinguishes clean from error for provenance.
                if self._connect_emitted:
                    self._record_gap(opened_at, session_error or "connection closed (clean; no error)")
                with _suppress():
                    await ws.close()

            if self._stop.is_set():
                break
            # Every continuation waits through one stop-aware backoff, so the
            # loop can never busy-spin (e.g. a server that closes the socket
            # immediately) and always honors stop() promptly. A clean session
            # resets to the minimum interval; an error grows it exponentially.
            if clean:
                backoff = BACKOFF_MIN
            await self._backoff_wait(backoff)
            if not clean:
                backoff = min(backoff * 2, BACKOFF_MAX)

        logger.info("AISStream adapter stopped")

    async def _backoff_wait(self, seconds: float) -> None:
        self.backoff_history.append(seconds)
        if self._sleep is not None:      # test-injected sleep
            await self._sleep(seconds)
            return
        with _suppress():                # stop-aware default
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _session(self, ws: Any, fleet: Fleet) -> None:
        await ws.send(json.dumps({
            "APIKey": self._api_key,
            "BoundingBoxes": self._bboxes,
            "FilterMessageTypes": FILTER_TYPES,
        }))
        self.connected = True
        self._connected_at = datetime.now(timezone.utc).timestamp()
        self._signals.append(("feed_connect", {"bboxes": len(self._bboxes)}))
        self._connect_emitted = True   # finally must now guarantee a feed_disconnect
        logger.info("AISStream connected — subscribed (terrestrial coverage only)")

        async for raw in ws:
            if self._stop.is_set():
                break
            self._ingest(raw, fleet)

    def _ingest(self, raw: Any, fleet: Fleet) -> None:
        self.messages += 1
        try:
            envelope = json.loads(raw)
        except (ValueError, TypeError):
            self.parse_failures += 1
            return
        # AISStream signals subscription/auth errors as {"error": "..."}
        if isinstance(envelope, dict) and envelope.get("error"):
            self.parse_failures += 1
            logger.error("AISStream server error: %s", envelope["error"])
            return
        parsed = parse_envelope(envelope) if isinstance(envelope, dict) else None
        if parsed is None:
            return
        kind, mmsi, fields = parsed
        if kind == "position":
            fleet.apply_position(mmsi, fields, self.name)
        else:
            fleet.apply_static(mmsi, fields, self.name)

    # -- gap logging (dark-activity analytics) ---------------------------

    def _record_gap(self, opened_at: datetime, reason: str) -> None:
        """Buffer a feed_disconnect signal (with session duration — the
        dark-activity signal). The collector drains it into the unified
        signal log; no separate gap file."""
        closed_at = datetime.now(timezone.utc)
        gap = {
            "connected_at": opened_at.isoformat(),
            "disconnected_at": closed_at.isoformat(),
            "session_seconds": round((closed_at - opened_at).total_seconds(), 1),
            "reason": reason,
        }
        self._last_disconnect = gap
        self.connected = False
        self._connect_emitted = False   # matched — do not emit twice
        self._signals.append(("feed_disconnect", gap))

    def drain_signals(self) -> list[tuple[str, dict[str, Any]]]:
        """Operational signals since the last drain (feed_connect/disconnect)."""
        out = self._signals
        self._signals = []
        return out

    async def _default_connect(self, url: str) -> Any:
        import websockets  # imported lazily so tests need no network stack
        return await websockets.connect(url, ping_interval=20, ping_timeout=20)

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_origin": self.data_origin,
            "connected": self.connected,
            "messages": self.messages,
            "parse_failures": self.parse_failures,
            "reconnects": self.reconnects,
            "last_disconnect": self._last_disconnect,
            "note": "terrestrial AIS only (~40-60 NM off shore); open ocean is dark",
        }


class _suppress:
    """tiny contextlib.suppress(Exception) without the import churn."""
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: Any) -> bool:
        return True
