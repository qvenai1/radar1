"""Compatibility shim — the REST surface lives in backend/routes/ now.

One 4,200-line module holding five unrelated domains was architecture review
finding 1; the split is one module per domain under backend/routes/, with the
frozen-contract rules documented on the package docstring there.

Everything consumers and tests historically imported from `backend.api` is
re-exported below so no caller breaks; NEW code should import from the domain
module directly. ⚠ One exception, by nature: `monkeypatch.setattr(api, name, …)`
rebinds only this module's attribute, not the domain module's global — patch the
domain module (e.g. `backend.routes.maritime._DARK_CACHE`) instead.
"""

from __future__ import annotations

from .db import DB_ERRORS  # noqa: F401  (historical home; real home is db.py)
from .live import build_live_payload, iso  # noqa: F401
from .routes import router  # noqa: F401
from .routes.maritime import (  # noqa: F401
    MARITIME_STALE_S,
    _DARK_CACHE,
    _MARITIME_SOURCES,
    _VESSEL_REPORTABLE,
    _gnss_summary,
    _idc_summary,
    _read_snapshot,
)
from .routes.series import _clip, _union_seconds, build_series_coverage  # noqa: F401
from .routes.shared import _db, parse_time, resolve_range  # noqa: F401
