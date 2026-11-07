#!/usr/bin/env python3
"""
Atheer RADAR — API server entry point
=======================================
Full backend: REST API v1 + WebSocket stream over TimescaleDB, with the
raw-archive (latest.json) fallback. The heavy lifting lives in backend/;
this file is the process entry plus the static mount for the React SPA
(frontend/dist, built with `npm run build` — see README-backend.md).

The pre-v1 /api/latest alias and the embedded smoke-test UI were REMOVED
when the React frontend shipped; /api/v1/* is the only API surface.

API docs (auto-generated from the Pydantic contract): GET /api/docs

Usage
    python server.py --output opensky_output --host 0.0.0.0 --port 8000
Requires DATABASE_URL (env or .env). The server starts and serves the
archive fallback + honest /api/v1/health even when the DB is down.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


def build_app():
    """Deferred import so --output can set OPENSKY_OUTPUT_DIR before
    backend.config freezes its settings."""
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    from backend.app import create_app

    app = create_app()

    if (FRONTEND_DIST / "index.html").exists():
        # API routes are matched first; everything else is the SPA.
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
    else:
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def no_frontend() -> HTMLResponse:
            return HTMLResponse(
                "<h1>Atheer RADAR API</h1>"
                "<p>Frontend not built. Run <code>cd frontend && npm install && "
                "npm run build</code>, then restart the server. "
                'API docs: <a href="/api/docs">/api/docs</a></p>',
                status_code=503,
            )

    return app


def main() -> int:
    p = argparse.ArgumentParser(description="Atheer RADAR API server (REST v1 + WS).")
    p.add_argument("--output", type=Path, default=Path("opensky_output"),
                   help="Collector output directory (latest.json fallback)")
    p.add_argument("--host", default="127.0.0.1",
                   help="Bind host. Use 0.0.0.0 to expose on the LAN.")
    p.add_argument("--port", type=int, default=8000, help="Bind port")
    a = p.parse_args()

    os.environ["OPENSKY_OUTPUT_DIR"] = str(a.output)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    import uvicorn
    app = build_app()

    if a.host == "0.0.0.0":
        print("NOTE: serving on all interfaces — reachable from other LAN devices.")
    print(f"Live view: http://{a.host}:{a.port}   API docs: http://{a.host}:{a.port}/api/docs")
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
