"""Best-effort forensic dumps written when a cycle goes wrong.

Never raises: diagnostics must not become a second failure on top of the first.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from . import constants as C
from .logger import get_logger
from .models import RawResponse

_log = get_logger("diagnostics")


class DiagnosticsWriter:
    def __init__(self, output_dir: Path) -> None:
        self._dir = output_dir / C.DIAGNOSTICS_DIRNAME
        self._dir.mkdir(parents=True, exist_ok=True)
        self._unknown_dir = self._dir / C.DIAG_UNKNOWN_PAYLOAD_DIR
        self._unknown_seq = 0

    def dump_tile(self, payload: Any) -> None:
        self._safe_write(C.DIAG_LAST_TILE_JSON, lambda p: p.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2)[:200_000], encoding="utf-8"
        ))

    def dump_html(self, html: str | None) -> None:
        if html is None:
            return
        self._safe_write(C.DIAG_LAST_HTML, lambda p: p.write_text(html, encoding="utf-8"))

    def dump_exception(self, exc: BaseException) -> None:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._safe_write(C.DIAG_LAST_EXCEPTION_LOG, lambda p: p.write_text(text, encoding="utf-8"))

    def save_unknown_payload(self, response: RawResponse) -> None:
        """Archive an undecodable/unusable response so nothing is lost for debugging."""
        self._unknown_seq += 1
        seq = self._unknown_seq
        stem = f"payload_{seq:06d}_status{response.status}_{response.size}b"

        def _write(_: Path) -> None:
            self._unknown_dir.mkdir(parents=True, exist_ok=True)
            (self._unknown_dir / f"{stem}.bin").write_bytes(response.body)
            meta = {
                "url": response.url,
                "status": response.status,
                "source": response.source,
                "content_type": response.content_type,
                "size": response.size,
                "headers": dict(response.headers),
            }
            (self._unknown_dir / f"{stem}.meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        self._safe_write(f"{stem}.bin", _write)

    def _safe_write(self, name: str, write: Any) -> None:
        try:
            write(self._dir / name)
        except Exception as exc:
            _log.debug("failed writing diagnostic %s: %s", name, exc)
