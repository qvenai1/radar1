"""ATHEER_API_KEY — the opt-in READ gate (middleware + WS handshake).

Detonation tests, per the house rule that a guard is proven by firing it:
the armed gate must actually refuse; the unarmed gate must actually admit.

⚠ The WS success-path (valid key → accepted handshake) is NOT tested here:
it needs the lifespan-started Broadcaster and a live LISTEN connection, and
the open path is exercised by the live dashboard daily. What is tested is
the refusal, which never reaches app.state.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from starlette.websockets import WebSocketDisconnect

from backend.app import create_app

OPEN_PATH = "/api/openapi.json"  # under /api/, no DB, deterministic 200


def _transport():
    return ASGITransport(app=create_app())


@pytest.mark.asyncio
async def test_unset_key_leaves_reads_open_and_sets_headers(monkeypatch):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    async with AsyncClient(transport=_transport(), base_url="http://t") as c:
        r = await c.get(OPEN_PATH)
    assert r.status_code == 200
    # the browser-protection headers ride on every response
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"


@pytest.mark.asyncio
async def test_armed_key_refuses_missing_and_wrong(monkeypatch):
    monkeypatch.setenv("ATHEER_API_KEY", "k-secret")
    async with AsyncClient(transport=_transport(), base_url="http://t") as c:
        assert (await c.get(OPEN_PATH)).status_code == 401
        assert (await c.get(OPEN_PATH, headers={"x-atheer-key": "wrong"})).status_code == 401


@pytest.mark.asyncio
async def test_armed_key_admits_header_and_query_param(monkeypatch):
    monkeypatch.setenv("ATHEER_API_KEY", "k-secret")
    async with AsyncClient(transport=_transport(), base_url="http://t") as c:
        assert (await c.get(OPEN_PATH, headers={"x-atheer-key": "k-secret"})).status_code == 200
        assert (await c.get(OPEN_PATH, params={"api_key": "k-secret"})).status_code == 200


def test_armed_key_refuses_ws_handshake(monkeypatch):
    monkeypatch.setenv("ATHEER_API_KEY", "k-secret")
    client = TestClient(create_app())  # no `with` → no lifespan; refusal precedes app.state
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/stream"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/stream?api_key=wrong"):
            pass


# ---- the armed SUCCESS path — needs the real lifespan (Broadcaster + LISTEN) ----

import os  # noqa: E402

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@requires_db
def test_armed_ws_admits_valid_key_and_pushes_first_snapshot(monkeypatch):
    """Closes the gap the module docstring names: the armed gate must also ADMIT.

    `with client:` runs the real lifespan — Database + Broadcaster (a live LISTEN
    connection) — so this is the whole handshake path, not a fake. The first
    frame arrives immediately on connect (archive-first build_live_payload), so
    no waiting on a NOTIFY is involved.
    """
    monkeypatch.setenv("ATHEER_API_KEY", "k-secret")
    client = TestClient(create_app())
    with client:
        with client.websocket_connect("/api/v1/stream?api_key=k-secret") as ws:
            msg = ws.receive_json()
    assert msg["type"] == "snapshot"
    assert isinstance(msg["data"].get("states"), list)
    assert msg["data"]["snapshot_time"]
