"""Runtime configuration, resolved from defaults <- environment <- CLI.

``Config`` is the single object the composition root builds and threads through
the system. It validates on construction so an ill-formed setup fails fast at
startup rather than mid-run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import constants as C
from .exceptions import ConfigError


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"{C.ENV_PREFIX}{name}", default)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{C.ENV_PREFIX}{name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{C.ENV_PREFIX}{name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    base_url: str = C.DEFAULT_BASE_URL
    headless: bool = False  # headless trips Cloudflare; headful is the default.
    user_agent: str = C.DEFAULT_USER_AGENT
    viewport_width: int = 1400
    viewport_height: int = 900
    nav_timeout_s: float = 45.0
    proxy: str | None = None
    locale: str = "en-US"
    timezone_id: str = "Asia/Nicosia"
    # A persistent user-data-dir keeps the Cloudflare ``cf_clearance`` cookie so
    # the challenge is solved once and reused — strongly recommended for
    # enrichment, whose per-vessel endpoint is more aggressively challenged.
    user_data_dir: str | None = None
    # Seconds to wait after navigation for a Cloudflare interstitial to auto-clear.
    challenge_wait_s: float = 30.0


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Thresholds for the per-record validity/plausibility checks."""

    plausibility_max_speed_kn: float = 60.0  # very few ships exceed ~45 kn
    stale_after_min: float = 180.0  # ELAPSED beyond this flags a stale position
    gap_threshold_s: float = 1800.0  # a cycle gap longer than this is journalled

    def __post_init__(self) -> None:
        if self.plausibility_max_speed_kn <= 0:
            raise ConfigError("validation.plausibility_max_speed_kn must be > 0")
        if self.stale_after_min <= 0:
            raise ConfigError("validation.stale_after_min must be > 0")
        if self.gap_threshold_s <= 0:
            raise ConfigError("validation.gap_threshold_s must be > 0")


@dataclass(frozen=True, slots=True)
class RecoveryConfig:
    """Escalation thresholds for the self-healing policy."""

    empty_reload_threshold: int = 5
    empty_restart_threshold: int = 10
    parser_failure_threshold: int = 3
    max_restart_failures: int = 5

    def __post_init__(self) -> None:
        if not (1 <= self.empty_reload_threshold < self.empty_restart_threshold):
            raise ConfigError("require 1 <= empty_reload_threshold < empty_restart_threshold")
        if self.parser_failure_threshold < 1:
            raise ConfigError("recovery.parser_failure_threshold must be >= 1")
        if self.max_restart_failures < 1:
            raise ConfigError("recovery.max_restart_failures must be >= 1")


@dataclass(frozen=True, slots=True)
class Config:
    """The fully-resolved configuration for one run."""

    center_lat: float
    center_lon: float
    zoom: int
    output_dir: Path
    poll_interval_s: float
    settle_s: float
    max_cycles: int | None
    persist_out_of_area: bool
    positions_enabled: bool
    position_heartbeat_s: float
    area_mode: str  # "polygon" | "bbox"
    area_buffer_km: float
    half_height_km: float
    half_width_km: float
    enrich_enabled: bool
    enrich_max_per_cycle: int
    enrich_throttle_s: float
    collection_mode: str  # "active" (reload each cycle) | "passive" (harvest own cadence)
    passive_reload_after_empty: int
    profile_count: int
    proxies_file: Path | None
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)

    def __post_init__(self) -> None:
        if self.zoom <= C.TILE_ZOOM_OFFSET:
            raise ConfigError(f"zoom must be > {C.TILE_ZOOM_OFFSET}, got {self.zoom}")
        if self.poll_interval_s < 0:
            raise ConfigError("poll_interval_s must be >= 0")
        if self.max_cycles is not None and self.max_cycles <= 0:
            raise ConfigError("max_cycles must be positive when set")
        if self.area_mode not in ("polygon", "bbox"):
            raise ConfigError(f"area_mode must be 'polygon' or 'bbox', got {self.area_mode!r}")
        if self.area_buffer_km < 0:
            raise ConfigError("area_buffer_km must be >= 0")
        if self.enrich_max_per_cycle < 0:
            raise ConfigError("enrich_max_per_cycle must be >= 0")
        if self.enrich_throttle_s < 0:
            raise ConfigError("enrich_throttle_s must be >= 0")
        if self.collection_mode not in ("active", "passive"):
            raise ConfigError(f"bad collection_mode {self.collection_mode!r} (active|passive)")
        if self.passive_reload_after_empty < 1:
            raise ConfigError("passive_reload_after_empty must be >= 1")
        if self.profile_count < 1:
            raise ConfigError("profile_count must be >= 1")
        # 0 disables suppression entirely (every observation written); negative is
        # meaningless — it would place the heartbeat deadline in the past.
        if self.position_heartbeat_s < 0:
            raise ConfigError("position_heartbeat_s must be >= 0")

    @property
    def map_url(self) -> str:
        path = C.DEFAULT_MAP_PATH.format(
            lon=f"{self.center_lon:g}", lat=f"{self.center_lat:g}", zoom=self.zoom
        )
        return f"{self.browser.base_url}{path}"

    @property
    def aoi_asset_path(self) -> Path:
        return Path(__file__).resolve().parent / C.AOI_DATA_DIRNAME / C.AOI_ASSET_FILENAME


def build_config(
    *,
    center_lat: float,
    center_lon: float,
    zoom: int,
    output_dir: Path,
    half_height_km: float,
    half_width_km: float,
    area_mode: str = "polygon",
    area_buffer_km: float | None = None,
    poll_interval_s: float | None = None,
    settle_s: float = 6.0,
    max_cycles: int | None = None,
    headless: bool | None = None,
    persist_out_of_area: bool | None = None,
    positions_enabled: bool | None = None,
    position_heartbeat_s: float | None = None,
    proxy: str | None = None,
    enrich_enabled: bool = False,
    enrich_max_per_cycle: int | None = None,
    enrich_throttle_s: float | None = None,
    profile_dir: str | None = None,
    collection_mode: str = "active",
    passive_reload_after_empty: int | None = None,
    profile_count: int | None = None,
    proxies_file: Path | None = None,
) -> Config:
    """Assemble a :class:`Config`, letting explicit args override env defaults."""
    browser = BrowserConfig(
        headless=_env_bool("HEADLESS", False) if headless is None else headless,
        proxy=_env("PROXY") if proxy is None else proxy,
        nav_timeout_s=_env_float("NAV_TIMEOUT_S", 45.0),
        user_data_dir=_env("PROFILE_DIR") if profile_dir is None else profile_dir,
    )
    return Config(
        center_lat=center_lat,
        center_lon=center_lon,
        zoom=zoom,
        output_dir=output_dir,
        poll_interval_s=_env_float("POLL_INTERVAL_S", 30.0)
        if poll_interval_s is None
        else poll_interval_s,
        settle_s=settle_s,
        max_cycles=max_cycles,
        persist_out_of_area=_env_bool("PERSIST_OUT_OF_AREA", False)
        if persist_out_of_area is None
        else persist_out_of_area,
        positions_enabled=_env_bool("POSITIONS_ENABLED", True)
        if positions_enabled is None
        else positions_enabled,
        position_heartbeat_s=_env_float("POSITION_HEARTBEAT_S", C.POSITION_HEARTBEAT_S)
        if position_heartbeat_s is None
        else position_heartbeat_s,
        area_mode=area_mode,
        area_buffer_km=_env_float("AREA_BUFFER_KM", 100.0)
        if area_buffer_km is None
        else area_buffer_km,
        half_height_km=half_height_km,
        half_width_km=half_width_km,
        enrich_enabled=_env_bool("ENRICH", False) if not enrich_enabled else True,
        enrich_max_per_cycle=_env_int("ENRICH_PER_CYCLE", 25)
        if enrich_max_per_cycle is None
        else enrich_max_per_cycle,
        enrich_throttle_s=_env_float("ENRICH_THROTTLE_S", 1.5)
        if enrich_throttle_s is None
        else enrich_throttle_s,
        collection_mode=collection_mode,
        passive_reload_after_empty=_env_int("PASSIVE_RELOAD_AFTER_EMPTY", 5)
        if passive_reload_after_empty is None
        else passive_reload_after_empty,
        profile_count=_env_int("PROFILE_COUNT", 1) if profile_count is None else profile_count,
        proxies_file=proxies_file,
        browser=browser,
        validation=ValidationConfig(
            plausibility_max_speed_kn=_env_float("MAX_SPEED_KN", 60.0),
            stale_after_min=_env_float("STALE_AFTER_MIN", 180.0),
            gap_threshold_s=_env_float("GAP_THRESHOLD_S", 1800.0),
        ),
        recovery=RecoveryConfig(),
    )
