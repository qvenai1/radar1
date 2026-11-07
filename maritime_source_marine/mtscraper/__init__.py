"""Standalone MarineTraffic live-map vessel scraper.

A fully self-contained sibling of the ADS-B ``plandata`` project: it shares no
code and imports nothing from it. It loads the MarineTraffic live map in a real
browser (defeating Cloudflare), harvests the ``get_data_json_4`` vessel tiles,
and persists a deduplicated catalogue plus a position time-series.
"""

from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
