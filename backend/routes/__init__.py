"""
REST API v1 — the frozen contract (see README-backend.md, "API Contract v1"),
now one module per domain instead of one 4,000-line file (arch review finding 1).

Query-safety rules enforced uniformly across every module:
  * every time-range endpoint has a hard range cap (no unbounded hypertable scans)
  * every list endpoint is paginated (limit/offset + has_more)
  * `from`/`to`/`at` accept unix epoch seconds OR ISO-8601
  * stats come from the continuous aggregate, never raw rows
"""

from __future__ import annotations

from fastapi import APIRouter

from . import air, alerts, enrichment, maritime, search, series, space, system, zones

router = APIRouter(prefix="/api/v1")
for _m in (air, search, maritime, alerts, space, enrichment, system, zones, series):
    router.include_router(_m.router)
