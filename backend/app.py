"""FastAPI application factory — wires DB, REST v1, and the WS broadcaster."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import router as api_router
from .config import settings
from .db import Database
from .enrich import Enricher
from .stream import Broadcaster
from .stream import router as ws_router


def create_app(*, enricher: Enricher | None = None) -> FastAPI:
    """`enricher` is the injection seam (arch review finding 10): tests pass a
    fake so the two endpoints that reach third-party APIs are testable offline.
    Assigned at construction time — not in lifespan — so an app built without a
    running lifespan (the httpx ASGITransport test idiom) still has one. The
    real Enricher opens no connection until first use, so this costs nothing;
    lifespan still owns close() (an injected fake needs a close() too)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        broadcaster = Broadcaster(db)
        app.state.db = db
        app.state.broadcaster = broadcaster
        broadcaster.start()
        yield
        await broadcaster.stop()
        await app.state.enricher.close()
        await db.close()

    app = FastAPI(
        title="Atheer RADAR API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.enricher = enricher if enricher is not None else Enricher()
    @app.middleware("http")
    async def guard_and_headers(request: Request, call_next):
        # Opt-in READ gate (security review finding 1). Read from env PER REQUEST like
        # `_require_write_token`, so tests can monkeypatch.setenv and a restart is the
        # only deploy step. ⚠ Unlike the write token this deliberately does NOT fail
        # closed when unset: an open read surface is the documented current contract of
        # a LAN analyst tool, and refusing every read on the deployment that never set
        # a key would turn each restart into a dashboard outage. Set ATHEER_API_KEY to
        # arm it; the write guard stays fail-closed regardless.
        expected = (os.environ.get("ATHEER_API_KEY") or "").strip()
        if expected and request.url.path.startswith("/api/"):
            got = (request.headers.get("x-atheer-key")
                   or request.query_params.get("api_key") or "").strip()
            if not got or not hmac.compare_digest(got, expected):
                return JSONResponse({"detail": "missing or invalid API key"}, status_code=401)
        resp = await call_next(request)
        # Browser-protection headers (security review finding 6). frame-ancestors is the
        # CSP directive with zero breakage risk; a full CSP needs an inventory of every
        # external origin the map pulls (CARTO vector tiles, planespotters/Wikimedia
        # photos, …) — one wrong entry blanks the basemap silently, so it is not guessed
        # here (README-backend.md § Exposure documents the reverse-proxy option for
        # TLS + a full CSP).
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return resp

    app.include_router(api_router)
    app.include_router(ws_router)
    return app
