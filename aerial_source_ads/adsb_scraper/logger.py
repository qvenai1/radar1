"""Logging configuration and structured helpers.

Two formats are supported and chosen once at startup:

* **human** — a compact console/file line, good for tailing during development.
* **structured** — one JSON object per line, good for log shippers in
  production (``jq``, Loki, CloudWatch, ...).

The scraper never calls ``logging.getLogger`` with an ad-hoc name scattered
around the codebase; every module fetches its logger through :func:`get_logger`
so the hierarchy is consistent and configuration applied at the root sticks.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .config import LoggingConfig

ROOT_LOGGER_NAME = "adsb"

# Attributes present on every LogRecord; anything *else* an emitter attaches is
# treated as a structured field and included in the JSON output.
_STANDARD_LOGRECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {
    "message",
    "asctime",
    "taskName",
}


class _JsonFormatter(logging.Formatter):
    """Render each record as a single-line JSON document."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge any extra structured fields passed via ``logger.info(..., extra=)``.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOGRECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _human_formatter(verbose: bool) -> logging.Formatter:
    if verbose:
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
    else:
        fmt = "%(asctime)s | %(levelname)-8s | %(message)s"
    return logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")


def configure_logging(cfg: LoggingConfig, log_file: Path) -> logging.Logger:
    """Install console + file handlers on the package root logger.

    Idempotent: repeated calls replace existing handlers rather than stacking
    them, which matters when the process reconfigures after a config reload.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if cfg.verbose else logging.INFO

    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level)
    root.propagate = False
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)

    console_formatter: logging.Formatter
    file_formatter: logging.Formatter
    if cfg.structured:
        console_formatter = file_formatter = _JsonFormatter()
    else:
        console_formatter = _human_formatter(cfg.verbose)
        file_formatter = _human_formatter(True)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(console_formatter)
    root.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    file_handler.setFormatter(file_formatter)
    root.addHandler(file_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child of the package root logger (``adsb.<name>``)."""
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
