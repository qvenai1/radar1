"""Centralised logging setup.

A single configured logger tree under the ``mtscraper`` namespace. Handlers are
attached once (idempotent) so importing this module from anywhere is safe.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT_NAME = "mtscraper"
_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: int = logging.INFO, log_file: Path | None = None) -> None:
    """Attach a stderr handler (and optionally a file handler) exactly once."""
    global _CONFIGURED
    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level)
    if not _CONFIGURED:
        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)
        root.propagate = False
        _CONFIGURED = True
    if log_file is not None:
        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``mtscraper`` namespace."""
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
