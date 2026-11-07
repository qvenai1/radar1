"""Strongly typed, layered configuration.

Configuration is assembled from four sources, each overriding the previous:

    defaults  <  config file  <  environment variables  <  CLI arguments

A single ``FieldSpec`` table drives *all three* dynamic layers (file keys, env
names, CLI flags), so a new option is added in exactly one place and can never
drift out of sync between sources. Validation happens once, at construction of
the frozen dataclasses, via their ``__post_init__`` hooks — after
:func:`load_config` returns, every value in the tree is known-good.
"""

from __future__ import annotations

import argparse
import json
import os
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .constants import (
    AOI_ASSET_FILENAME,
    AOI_DATA_DIRNAME,
    COLLECTION_GAPS_FILENAME,
    COLLECTION_STATE_FILENAME,
    COMPLETED_CACHE_FILENAME,
    DEFAULT_AIRCRAFT_ENDPOINT,
    DEFAULT_GLOBE_URL,
    DEFAULT_USER_AGENT,
    DIAGNOSTICS_DIRNAME,
    ENV_PREFIX,
    LOG_FILENAME,
    RESULTS_FILENAME,
    STATE_VECTORS_FILENAME,
)
from .exceptions import ConfigError

# --------------------------------------------------------------------------- #
# Typed configuration sections
# --------------------------------------------------------------------------- #


def _default_asset_path() -> Path:
    """Package-relative path to the committed AOI boundary asset."""
    return Path(__file__).resolve().parent / AOI_DATA_DIRNAME / AOI_ASSET_FILENAME


@dataclass(frozen=True, slots=True)
class AreaConfig:
    """The area of interest: country polygons plus a border buffer.

    The neighborhood is the seven country polygons *plus* a geometric buffer of
    ``buffer_km`` around their borders (a true kilometric buffer of the polygons,
    not a flat margin on their bounding box). The fetch bounding box is derived
    from the envelope of that buffered geometry.
    """

    asset_path: Path = field(default_factory=_default_asset_path)
    buffer_km: float = 50.0
    persist_out_of_area: bool = False

    def __post_init__(self) -> None:
        if self.buffer_km < 0:
            raise ConfigError("area.buffer_km must be >= 0")


@dataclass(frozen=True, slots=True)
class TimingConfig:
    """All time-related tunables, in seconds unless noted."""

    fetch_interval: float = 15.0
    max_retries: int = 4
    map_load_wait: float = 8.0
    pan_settle_wait: float = 8.0
    data_wait_timeout: float = 15.0

    def __post_init__(self) -> None:
        if self.fetch_interval < 1.0:
            raise ConfigError("timing.fetch_interval must be >= 1")
        if self.max_retries < 1:
            raise ConfigError("timing.max_retries must be >= 1")
        for name in ("map_load_wait", "pan_settle_wait", "data_wait_timeout"):
            if getattr(self, name) < 0:
                raise ConfigError(f"timing.{name} must be >= 0")


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    """Everything the browser layer needs to launch and navigate."""

    headless: bool = True
    proxy: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    nav_timeout_s: float = 60.0
    viewport_width: int = 1920
    viewport_height: int = 1080
    globe_url: str = DEFAULT_GLOBE_URL
    aircraft_endpoint: str = DEFAULT_AIRCRAFT_ENDPOINT

    def __post_init__(self) -> None:
        if self.nav_timeout_s <= 0:
            raise ConfigError("browser.nav_timeout_s must be > 0")
        if self.viewport_width < 320 or self.viewport_height < 240:
            raise ConfigError("browser viewport is unreasonably small")
        if not self.globe_url.startswith(("http://", "https://")):
            raise ConfigError("browser.globe_url must be an http(s) URL")


@dataclass(frozen=True, slots=True)
class PersistenceConfig:
    """Durability and buffering behaviour of the JSONL sink."""

    batch_size: int = 50
    flush_interval: float = 5.0
    fsync: bool = True

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ConfigError("persistence.batch_size must be >= 1")
        if self.flush_interval <= 0:
            raise ConfigError("persistence.flush_interval must be > 0")


@dataclass(frozen=True, slots=True)
class RecoveryConfig:
    """Thresholds that drive automated self-healing."""

    empty_reload_threshold: int = 5
    empty_restart_threshold: int = 10
    parser_failure_threshold: int = 3
    max_restart_failures: int = 5

    def __post_init__(self) -> None:
        if not (1 <= self.empty_reload_threshold < self.empty_restart_threshold):
            raise ConfigError(
                "recovery: require 1 <= empty_reload_threshold < empty_restart_threshold"
            )
        if self.parser_failure_threshold < 1:
            raise ConfigError("recovery.parser_failure_threshold must be >= 1")
        if self.max_restart_failures < 1:
            raise ConfigError("recovery.max_restart_failures must be >= 1")


@dataclass(frozen=True, slots=True)
class DiagnosticsConfig:
    """Controls how much forensic data is retained on failure."""

    enabled: bool = True
    archive_unknown: bool = True
    max_snapshot_bytes: int = 5_000_000
    dump_raw: bool = False  # DEBUG: dump exact intercepted bytes on decode failure

    def __post_init__(self) -> None:
        if self.max_snapshot_bytes < 1024:
            raise ConfigError("diagnostics.max_snapshot_bytes must be >= 1024")


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Thresholds and policy for the data validity / normalization layer."""

    plausibility_max_gs_kt: float = 700.0
    plausibility_max_alt_ft: float = 60000.0
    gap_threshold_s: float = 1800.0

    def __post_init__(self) -> None:
        if self.plausibility_max_gs_kt <= 0:
            raise ConfigError("validation.plausibility_max_gs_kt must be > 0")
        if self.plausibility_max_alt_ft <= 0:
            raise ConfigError("validation.plausibility_max_alt_ft must be > 0")
        if self.gap_threshold_s <= 0:
            raise ConfigError("validation.gap_threshold_s must be > 0")


@dataclass(frozen=True, slots=True)
class StateVectorConfig:
    """The scraplan-shaped position time-series producer (P1).

    A SECOND archive alongside ``aircraft.jsonl``: one StateVector-shaped line per
    aircraft per cycle (no dedup). Enabled by default; disabling it leaves the
    discovery catalogue untouched.
    """

    enabled: bool = True
    filename: str = STATE_VECTORS_FILENAME

    def __post_init__(self) -> None:
        if not self.filename.strip():
            raise ConfigError("state_vectors.filename must be non-empty")


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Logging verbosity and format."""

    verbose: bool = False
    structured: bool = False


@dataclass(frozen=True, slots=True)
class AppConfig:
    """The fully assembled, validated configuration tree.

    All filesystem paths derive from ``output_dir`` so a single flag relocates
    the entire working set (results, cache, logs, diagnostics).
    """

    output_dir: Path = field(default_factory=lambda: Path("adsb_output"))
    area: AreaConfig = field(default_factory=AreaConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    diagnostics: DiagnosticsConfig = field(default_factory=DiagnosticsConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    state_vectors: StateVectorConfig = field(default_factory=StateVectorConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @property
    def results_file(self) -> Path:
        return self.output_dir / RESULTS_FILENAME

    @property
    def state_vectors_file(self) -> Path:
        return self.output_dir / self.state_vectors.filename

    @property
    def completed_cache_file(self) -> Path:
        return self.output_dir / COMPLETED_CACHE_FILENAME

    @property
    def collection_gaps_file(self) -> Path:
        return self.output_dir / COLLECTION_GAPS_FILENAME

    @property
    def collection_state_file(self) -> Path:
        return self.output_dir / COLLECTION_STATE_FILENAME

    @property
    def diagnostics_dir(self) -> Path:
        return self.output_dir / DIAGNOSTICS_DIRNAME

    @property
    def log_file(self) -> Path:
        return self.output_dir / LOG_FILENAME


# --------------------------------------------------------------------------- #
# Field specification table (single source of truth for the dynamic layers)
# --------------------------------------------------------------------------- #


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
    raise ConfigError(f"cannot interpret {value!r} as a boolean")


def _to_opt_str(value: object) -> str | None:
    text = str(value).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Describes one configurable field across every input layer."""

    section: str  # AppConfig attribute ("grid", ...) or "app" for top level
    name: str  # dataclass field name within the section
    convert: Callable[..., Any]  # builtins (int/float/Path) + our _to_* helpers
    cli_flag: str
    help: str
    is_bool: bool = False

    @property
    def env_var(self) -> str:
        if self.section == "app":
            return f"{ENV_PREFIX}{self.name.upper()}"
        return f"{ENV_PREFIX}{self.section.upper()}_{self.name.upper()}"

    @property
    def cli_dest(self) -> str:
        return f"{self.section}__{self.name}"


_FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("app", "output_dir", Path, "--output", "Output directory"),
    # area
    FieldSpec("area", "asset_path", Path, "--area-asset", "AOI boundary GeoJSON asset path"),
    FieldSpec(
        "area",
        "buffer_km",
        float,
        "--area-buffer-km",
        "Border buffer (km) around the country polygons counted as NEAR",
    ),
    FieldSpec(
        "area",
        "persist_out_of_area",
        _to_bool,
        "--persist-out-of-area",
        "Persist records flagged OUTSIDE_AREA (default: flag but skip)",
        is_bool=True,
    ),
    # timing
    FieldSpec("timing", "fetch_interval", float, "--interval", "Fetch interval (s)"),
    FieldSpec("timing", "max_retries", int, "--retries", "Max retries per fetch"),
    FieldSpec("timing", "map_load_wait", float, "--map-load-wait", "Initial map wait (s)"),
    FieldSpec("timing", "pan_settle_wait", float, "--pan-settle-wait", "Wait after pan (s)"),
    FieldSpec("timing", "data_wait_timeout", float, "--data-timeout", "Data wait timeout (s)"),
    # browser
    FieldSpec("browser", "headless", _to_bool, "--headless", "Run headless", is_bool=True),
    FieldSpec("browser", "proxy", _to_opt_str, "--proxy", "Proxy URL"),
    FieldSpec("browser", "user_agent", str, "--user-agent", "User-Agent header"),
    FieldSpec("browser", "nav_timeout_s", float, "--nav-timeout", "Navigation timeout (s)"),
    FieldSpec("browser", "viewport_width", int, "--viewport-width", "Viewport width"),
    FieldSpec("browser", "viewport_height", int, "--viewport-height", "Viewport height"),
    FieldSpec("browser", "globe_url", str, "--globe-url", "Globe front-end URL"),
    FieldSpec("browser", "aircraft_endpoint", str, "--aircraft-endpoint", "Aircraft JSON path"),
    # persistence
    FieldSpec("persistence", "batch_size", int, "--batch-size", "Write buffer size"),
    FieldSpec("persistence", "flush_interval", float, "--flush-interval", "Flush timer (s)"),
    FieldSpec("persistence", "fsync", _to_bool, "--fsync", "fsync on flush", is_bool=True),
    # recovery
    FieldSpec(
        "recovery",
        "empty_reload_threshold",
        int,
        "--empty-reload-threshold",
        "Empty cycles before reload",
    ),
    FieldSpec(
        "recovery",
        "empty_restart_threshold",
        int,
        "--empty-restart-threshold",
        "Empty cycles before restart",
    ),
    FieldSpec(
        "recovery",
        "parser_failure_threshold",
        int,
        "--parser-failure-threshold",
        "Parser failures before save+continue",
    ),
    FieldSpec(
        "recovery",
        "max_restart_failures",
        int,
        "--max-restart-failures",
        "Consecutive failed restarts before abort",
    ),
    # diagnostics
    FieldSpec(
        "diagnostics", "enabled", _to_bool, "--diagnostics", "Enable diagnostics", is_bool=True
    ),
    FieldSpec(
        "diagnostics",
        "archive_unknown",
        _to_bool,
        "--archive-unknown",
        "Archive unknown payloads",
        is_bool=True,
    ),
    FieldSpec(
        "diagnostics",
        "max_snapshot_bytes",
        int,
        "--max-snapshot-bytes",
        "Max diagnostic snapshot size (bytes)",
    ),
    FieldSpec(
        "diagnostics",
        "dump_raw",
        _to_bool,
        "--dump-raw",
        "DEBUG: dump exact intercepted bytes + analysis on decode failure",
        is_bool=True,
    ),
    # validation
    FieldSpec(
        "validation",
        "plausibility_max_gs_kt",
        float,
        "--plausibility-max-gs-kt",
        "Ground speed above this (kt) flags IMPLAUSIBLE_KINEMATICS",
    ),
    FieldSpec(
        "validation",
        "plausibility_max_alt_ft",
        float,
        "--plausibility-max-alt-ft",
        "Altitude above this (ft) flags IMPLAUSIBLE_KINEMATICS",
    ),
    FieldSpec(
        "validation",
        "gap_threshold_s",
        float,
        "--gap-threshold-s",
        "Collection gap (s) above which a collection_gap marker is recorded",
    ),
    # state_vectors
    FieldSpec(
        "state_vectors",
        "enabled",
        _to_bool,
        "--state-vectors",
        "Emit the scraplan StateVector time-series (state_vectors.jsonl)",
        is_bool=True,
    ),
    FieldSpec(
        "state_vectors",
        "filename",
        str,
        "--state-vectors-file",
        "Filename for the StateVector time-series archive",
    ),
    # logging
    FieldSpec("logging", "verbose", _to_bool, "--verbose", "Debug logging", is_bool=True),
    FieldSpec(
        "logging", "structured", _to_bool, "--structured-logs", "Emit logs as JSON", is_bool=True
    ),
)

_SECTION_TYPES: dict[str, type] = {
    "area": AreaConfig,
    "timing": TimingConfig,
    "browser": BrowserConfig,
    "persistence": PersistenceConfig,
    "recovery": RecoveryConfig,
    "diagnostics": DiagnosticsConfig,
    "validation": ValidationConfig,
    "state_vectors": StateVectorConfig,
    "logging": LoggingConfig,
}

# A layered overrides map: section -> field name -> raw (unconverted) value.
Overrides = dict[str, dict[str, Any]]


# --------------------------------------------------------------------------- #
# Individual layer loaders
# --------------------------------------------------------------------------- #


def _blank_overrides() -> Overrides:
    return {section: {} for section in ("app", *_SECTION_TYPES)}


def _load_file_layer(path: Path) -> Overrides:
    """Read a ``.toml`` or ``.json`` config file into an overrides map."""
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        if path.suffix == ".toml":
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        elif path.suffix == ".json":
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raise ConfigError(f"unsupported config file type: {path.suffix}")
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"failed to read config file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config file root must be a table/object")

    overrides = _blank_overrides()
    known = {spec.cli_dest: spec for spec in _FIELD_SPECS}
    # Top-level scalars belong to the synthetic "app" section.
    for key, value in raw.items():
        if isinstance(value, dict) and key in _SECTION_TYPES:
            for sub_key, sub_value in value.items():
                spec = known.get(f"{key}__{sub_key}")
                if spec is None:
                    raise ConfigError(f"unknown config option: {key}.{sub_key}")
                overrides[key][sub_key] = spec.convert(sub_value)
        else:
            spec = known.get(f"app__{key}")
            if spec is None:
                raise ConfigError(f"unknown top-level config option: {key}")
            overrides["app"][key] = spec.convert(value)
    return overrides


def _load_env_layer(environ: Mapping[str, str]) -> Overrides:
    overrides = _blank_overrides()
    for spec in _FIELD_SPECS:
        if spec.env_var in environ:
            overrides[spec.section][spec.name] = spec.convert(environ[spec.env_var])
    return overrides


def _load_cli_layer(namespace: argparse.Namespace) -> Overrides:
    overrides = _blank_overrides()
    for spec in _FIELD_SPECS:
        value = getattr(namespace, spec.cli_dest, None)
        if value is not None:  # None => flag not supplied => do not override
            overrides[spec.section][spec.name] = spec.convert(value)
    return overrides


def _merge(base: Overrides, layer: Overrides) -> None:
    for section, values in layer.items():
        base[section].update(values)


# --------------------------------------------------------------------------- #
# Argument parser (built from the spec table)
# --------------------------------------------------------------------------- #


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser. Every flag defaults to ``None`` so that an
    unsupplied flag never shadows a value from a lower-precedence layer."""
    parser = argparse.ArgumentParser(
        prog="adsb-scraper",
        description="Production-grade ADS-B Exchange area scraper.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a .toml or .json configuration file",
    )
    for spec in _FIELD_SPECS:
        flags = [spec.cli_flag]
        if spec.cli_flag == "--verbose":
            flags.insert(0, "-v")
        if spec.is_bool:
            parser.add_argument(
                *flags,
                dest=spec.cli_dest,
                action=argparse.BooleanOptionalAction,
                default=None,
                help=spec.help,
            )
        else:
            parser.add_argument(
                *flags,
                dest=spec.cli_dest,
                default=None,
                metavar=spec.cli_flag.lstrip("-").replace("-", "_").upper(),
                help=spec.help,
            )
    return parser


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def _build_app_config(overrides: Overrides) -> AppConfig:
    """Instantiate the typed tree, letting each section validate itself."""
    kwargs: dict[str, Any] = {}
    app_over = overrides.get("app", {})
    if "output_dir" in app_over:
        kwargs["output_dir"] = Path(app_over["output_dir"]).expanduser()

    for section, section_type in _SECTION_TYPES.items():
        section_over = overrides.get(section, {})
        valid_names = {f.name for f in fields(section_type)}
        unknown = set(section_over) - valid_names
        if unknown:
            raise ConfigError(f"unknown {section} options: {sorted(unknown)}")
        try:
            kwargs[section] = section_type(**section_over)
        except TypeError as exc:  # e.g. wrong keyword; convert to our hierarchy
            raise ConfigError(f"invalid {section} configuration: {exc}") from exc

    return AppConfig(**kwargs)


def load_config(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load and validate configuration from all layers.

    Precedence (low to high): defaults < file < environment < CLI. Raises
    :class:`ConfigError` on any invalid or unknown option.
    """
    environ = os.environ if environ is None else environ
    parser = build_arg_parser()
    namespace = parser.parse_args(argv)

    merged = _blank_overrides()

    config_path: Path | None = getattr(namespace, "config", None)
    if config_path is None:
        env_config = environ.get(f"{ENV_PREFIX}CONFIG")
        config_path = Path(env_config) if env_config else None
    if config_path is not None:
        _merge(merged, _load_file_layer(config_path))

    _merge(merged, _load_env_layer(environ))
    _merge(merged, _load_cli_layer(namespace))

    return _build_app_config(merged)
