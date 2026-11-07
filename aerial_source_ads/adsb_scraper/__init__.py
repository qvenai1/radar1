"""ADS-B Exchange scraper — a production-grade, long-running data collector.

The package is organised as a small Clean Architecture:

* ``models`` / ``geo``      — the pure domain (no I/O).
* ``config`` / ``logger``   — cross-cutting startup concerns.
* ``parser`` / ``fetcher`` / ``browser`` / ``persistence`` — adapters to the
  outside world, each hidden behind a ``Protocol`` so it can be mocked.
* ``accumulator`` / ``recovery`` / ``diagnostics`` — application services.
* ``orchestrator``          — coordinates a single collection cycle.
* ``main``                  — the composition root that wires everything.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "3.0.0"
