"""Backend configuration — everything comes from the environment (.env aware)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    output_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("OPENSKY_OUTPUT_DIR", "opensky_output"))
    )
    maritime_output_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("MARITIME_OUTPUT_DIR", "maritime_output"))
    )
    marinetraffic_output_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MARINETRAFFIC_OUTPUT_DIR", "marinetraffic_output")
        )
    )
    stale_after_seconds: float = field(
        default_factory=lambda: float(os.environ.get("STALE_AFTER_SECONDS", "120"))
    )

    # --- Operator tunables (arch review finding 8) -------------------------------
    # One discoverable surface for the knobs that used to live as module constants
    # scattered across a dozen files. Defaults are the long-measured values; each
    # is env-overridable so a behavior change is a .env edit + restart, not a code
    # edit + redeploy. The consuming modules keep their historical constant names,
    # initialized from here.
    maritime_stale_s: float = field(
        default_factory=lambda: float(os.environ.get("MARITIME_STALE_S", "180"))
    )
    military_stale_s: float = field(
        default_factory=lambda: float(os.environ.get("MILITARY_STALE_S", "120"))
    )
    adsb_stale_s: float = field(
        default_factory=lambda: float(os.environ.get("ADSB_STALE_S", "120"))
    )
    mt_db_lookback_days: int = field(
        default_factory=lambda: int(os.environ.get("MT_DB_LOOKBACK_DAYS", "60"))
    )
    mt_latest_limit: int = field(
        default_factory=lambda: int(os.environ.get("MT_LATEST_LIMIT", "3000"))
    )
    dark_ttl_s: float = field(
        default_factory=lambda: float(os.environ.get("DARK_TTL_S", "1800"))
    )
    zone_mask_ttl_s: float = field(
        default_factory=lambda: float(os.environ.get("ZONE_MASK_TTL_S", "600"))
    )
    host_cache_s: float = field(
        default_factory=lambda: float(os.environ.get("HOST_CACHE_S", "600"))
    )
    host_gap_min_s: float = field(
        default_factory=lambda: float(os.environ.get("HOST_GAP_MIN_S", "900"))
    )

    @property
    def latest_json(self) -> Path:
        return self.output_dir / "latest.json"

    @property
    def vessels_json(self) -> Path:
        return self.maritime_output_dir / "vessels.json"

    @property
    def mt_vessels_json(self) -> Path:
        return self.marinetraffic_output_dir / "vessels.json"


settings = Settings()
