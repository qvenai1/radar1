"""
WebSocket live push — WS /api/v1/stream.

Design
------
* One Broadcaster task per process holds a dedicated LISTEN connection on
  the 'new_snapshot' channel (fired by ingest.py follow mode after each
  committed snapshot). A 15s watchdog re-checks the DB even without a
  NOTIFY, covering missed notifications across reconnects.
* Bounded fan-out: each client gets an asyncio.Queue(maxsize=2). A slow
  client never blocks the broadcaster or other clients — when its queue is
  full the OLDEST payload is dropped and replaced (only the latest snapshot
  matters for a live view). Disconnects always unsubscribe via finally.
* Contract (frozen): every message is
      {"type": "snapshot", "data": <same payload as GET /api/v1/live>}
  The first message arrives immediately on connect; afterwards one message
  per newly ingested snapshot. Clients that cannot use WebSocket keep
  polling GET /api/v1/live — the REST path is the documented fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os

import asyncpg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .live import build_live_payload
from .db import Database

logger = logging.getLogger("opensky_api.stream")

NOTIFY_CHANNEL = "new_snapshot"
WATCHDOG_SECONDS = 15.0
CLIENT_QUEUE_SIZE = 2

router = APIRouter(prefix="/api/v1")


class Broadcaster:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._clients: set[asyncio.Queue] = set()
        self._wakeup = asyncio.Event()
        # (snapshot_time, count). Tracking count too matters: the collector's
        # JSONL writer flushes a snapshot across two file writes, so the
        # tailer ingests it in two batches — the count change is what tells
        # us to re-push the same snapshot_time with the full state set.
        self._last_sent: tuple[int, int] | None = None
        self._task: asyncio.Task | None = None

    # -- client registry --------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        self._clients.add(q)
        logger.info("WS client subscribed (%d total)", len(self._clients))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)
        logger.info("WS client unsubscribed (%d total)", len(self._clients))

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def _broadcast(self, payload: dict) -> None:
        for q in self._clients:
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()          # drop oldest — latest wins
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(payload)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="snapshot-broadcaster")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(self.db.dsn)
                await conn.add_listener(NOTIFY_CHANNEL, self._on_notify)
                logger.info("Broadcaster listening on '%s'", NOTIFY_CHANNEL)
                backoff = 1.0
                while True:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._wakeup.wait(), timeout=WATCHDOG_SECONDS)
                    self._wakeup.clear()
                    if conn.is_closed():
                        raise ConnectionError("LISTEN connection lost")
                    await self._push_if_newer()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Broadcaster reconnecting in %.0fs: %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if conn is not None and not conn.is_closed():
                    with contextlib.suppress(Exception):
                        await conn.close()

    def _on_notify(self, _conn, _pid, _channel, _payload: str) -> None:
        self._wakeup.set()

    async def _push_if_newer(self) -> None:
        # Key off the live payload itself (latest.json-first, same source
        # clients get on connect) — the NOTIFY is only a wake-up signal.
        try:
            payload = await build_live_payload(self.db)
        except Exception as exc:
            logger.debug("Broadcaster payload build failed: %s", exc)
            return
        key = (payload["snapshot_time"], payload["count"])
        if self._last_sent is None:
            # Baseline only: whoever is connected already received the
            # current snapshot on connect — pushing it again is noise.
            self._last_sent = key
            return
        if key[0] < self._last_sent[0] or key == self._last_sent:
            return
        self._last_sent = key
        if not self._clients:
            return
        self._broadcast({"type": "snapshot", "data": payload})
        logger.info("Pushed snapshot %d (count=%d) to %d client(s)",
                    key[0], key[1], len(self._clients))


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    # Same opt-in read gate as the HTTP middleware (which does not cover WebSocket
    # routes). Checked BEFORE accept, so an unauthenticated handshake is refused
    # outright. Browsers cannot set headers on WebSocket, hence the query param;
    # the header form is for non-browser clients.
    expected = (os.environ.get("ATHEER_API_KEY") or "").strip()
    if expected:
        got = (ws.query_params.get("api_key") or ws.headers.get("x-atheer-key") or "").strip()
        if not got or not hmac.compare_digest(got, expected):
            await ws.close(code=4401)
            return
    broadcaster: Broadcaster = ws.app.state.broadcaster
    db: Database = ws.app.state.db
    await ws.accept()
    q = broadcaster.subscribe()
    try:
        try:
            payload = await build_live_payload(db)
            await ws.send_json({"type": "snapshot", "data": payload})
        except Exception:
            # No snapshot anywhere yet — stay connected; pushes will follow.
            pass
        while True:
            msg = await q.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unsubscribe(q)
