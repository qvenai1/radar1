"""Forensics and observability.

Two collaborators live here because they answer the same operational question —
*"what is this long-running process actually doing?"* — from two angles:

* :class:`DiagnosticsRecorder` captures the **last-known state** on disk so that
  an incident discovered days later can still be debugged. Nothing is ever
  swallowed: an undecodable payload is archived, not dropped.
* :class:`MetricsCollector` accumulates **aggregate counters** over the whole
  run for health dashboards and end-of-run summaries.

Both are deliberately synchronous and lock-free. They are only ever touched by
the single orchestrator coroutine between ``await`` points, so no locking is
needed; the orchestrator offloads the (small, truncated) disk writes to a
thread to keep the event loop responsive.
"""

from __future__ import annotations

import io
import json
import os
import traceback
from collections.abc import Callable
from pathlib import Path

import zstandard

from . import constants as C
from .config import DiagnosticsConfig
from .logger import get_logger
from .models import MetricsSnapshot, RawResponse
from .utils import Clock, MovingAverage, SystemClock

_log = get_logger("diagnostics")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically via a temp file + ``os.replace``.

    A reader therefore never observes a half-written diagnostic file.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _hex_ascii(data: bytes, count: int) -> tuple[str, str]:
    """Return ``(space-separated hex, printable ASCII)`` of the first ``count`` bytes."""
    chunk = data[:count]
    hexed = chunk.hex(" ")
    ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    return hexed, ascii_


def analyze_raw_payload(response: RawResponse) -> str:
    """Produce a human-readable forensic report for one raw payload.

    This is *observation only* — it never feeds the production decode chain. It
    reports the headers, the first bytes, and — critically — what a Zstandard
    decompress actually yields, so we can see whether the inner data is JSON,
    another compression magic number, or a binary format (protobuf/binCraft/...).
    """
    body = response.body
    raw_hex, raw_ascii = _hex_ascii(body, 16)
    lines = [
        "=== RAW INTERCEPTED PAYLOAD ANALYSIS ===",
        f"source        : {response.source}",
        f"url           : {response.url}",
        f"status        : {response.status}",
        f"size_bytes    : {response.size}",
        f"content-type  : {response.headers.get('content-type', '(none)')!r}",
        f"content-enc   : {response.headers.get('content-encoding', '(none)')!r}",
        "all headers   :",
        json.dumps(dict(response.headers), indent=4, ensure_ascii=False),
        f"first16 hex   : {raw_hex}",
        f"first16 ascii : {raw_ascii}",
    ]

    method, decompressed, error = _probe_zstd(body)
    if decompressed is not None:
        inner_hex, inner_ascii = _hex_ascii(decompressed, 64)
        looks_json = decompressed.lstrip()[:1] in (b"{", b"[")
        lines += [
            f"zstd_decompress: SUCCESS via {method}",
            f"inner_size     : {len(decompressed)} bytes",
            f"inner_looks_json: {looks_json}",
            f"inner64 hex    : {inner_hex}",
            f"inner64 ascii  : {inner_ascii}",
        ]
    else:
        lines += [f"zstd_decompress: FAILED ({error})"]
    return "\n".join(lines)


def _probe_zstd(body: bytes) -> tuple[str | None, bytes | None, str | None]:
    """Attempt a zstd decompress both ways. Returns ``(method, bytes, error)``."""
    dctx = zstandard.ZstdDecompressor()
    try:
        return ("embedded-size", dctx.decompress(body), None)
    except zstandard.ZstdError as embedded_error:
        try:
            with dctx.stream_reader(io.BytesIO(body)) as reader:
                return ("stream-reader", reader.read(), None)
        except zstandard.ZstdError as stream_error:
            return (None, None, f"embedded={embedded_error}; streaming={stream_error}")


class DiagnosticsRecorder:
    """Persists the most recent response, headers, status, HTML and errors."""

    __slots__ = ("_cfg", "_dir", "_dumps", "_seq", "_unknown_dir")

    #: Safety cap so a persistent failure can't fill the disk with raw dumps.
    _MAX_RAW_DUMPS = 5

    def __init__(self, cfg: DiagnosticsConfig, directory: Path) -> None:
        self._cfg = cfg
        self._dir = directory
        self._unknown_dir = directory / C.DIAG_UNKNOWN_PAYLOAD_DIR
        self._seq = 0
        self._dumps = 0
        if cfg.enabled:
            self._dir.mkdir(parents=True, exist_ok=True)

    def dump_raw_payload(self, response: RawResponse, timestamp: str) -> Path | None:
        """DEBUG: dump the *exact* intercepted bytes plus a forensic analysis.

        Enabled by ``diagnostics.dump_raw`` (``--dump-raw`` / ``ADSB_DIAGNOSTICS_DUMP_RAW``).
        The ``.bin`` is written verbatim — no decode is attempted on it. A sibling
        ``.analysis.txt`` records the hex/headers/zstd probe, and the same report
        is logged at WARNING so it shows up in ``-v`` runs.
        """
        if not self._cfg.enabled or self._dumps >= self._MAX_RAW_DUMPS:
            return None
        self._dumps += 1
        stem = f"debug_payload_{timestamp}_{response.source}"
        bin_path = self._dir / f"{stem}.bin"
        report = analyze_raw_payload(response)
        try:
            _atomic_write_bytes(bin_path, response.body)  # verbatim, no truncation
            _atomic_write_bytes((self._dir / f"{stem}.analysis.txt"), report.encode("utf-8"))
        except OSError as exc:
            _log.warning("failed to write raw payload dump: %s", exc)
            return None
        _log.warning("RAW PAYLOAD DUMP -> %s\n%s", bin_path, report)
        return bin_path

    def _write(self, filename: str, data: bytes | str) -> None:
        if not self._cfg.enabled:
            return
        payload = data.encode("utf-8") if isinstance(data, str) else data
        if len(payload) > self._cfg.max_snapshot_bytes:
            payload = payload[: self._cfg.max_snapshot_bytes]
        try:
            _atomic_write_bytes(self._dir / filename, payload)
        except OSError as exc:
            # Diagnostics failing must never crash the collector; log and move on.
            _log.warning("failed to write diagnostic %s: %s", filename, exc)

    def record_response(self, response: RawResponse, decoded: object | None) -> None:
        """Snapshot everything about the latest response."""
        if not self._cfg.enabled:
            return
        self._write(C.DIAG_LAST_STATUS_TXT, f"{response.status} {response.url}\n")
        self._write(C.DIAG_RESPONSE_SIZE_TXT, f"{response.size}\n")
        self._write(
            C.DIAG_LAST_HEADERS_JSON,
            json.dumps(dict(response.headers), indent=2, ensure_ascii=False),
        )
        # Raw body as text (best effort) always; structured JSON only if decoded.
        self._write(C.DIAG_LAST_RESPONSE_TXT, response.body.decode("utf-8", "replace"))
        if decoded is not None:
            try:
                self._write(
                    C.DIAG_LAST_RESPONSE_JSON,
                    json.dumps(decoded, indent=2, ensure_ascii=False, default=str),
                )
            except (TypeError, ValueError) as exc:
                _log.debug("decoded payload not JSON-serialisable: %s", exc)

    def record_html(self, html: str) -> None:
        self._write(C.DIAG_LAST_HTML, html)

    def record_exception(self, context: str, exc: BaseException) -> None:
        """Append a full traceback with context to the exception log."""
        if not self._cfg.enabled:
            return
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        block = f"=== {context} ===\n{tb}\n"
        try:
            with open(self._dir / C.DIAG_LAST_EXCEPTION_LOG, "a", encoding="utf-8") as fh:
                fh.write(block)
        except OSError as write_exc:
            _log.warning("failed to write exception log: %s", write_exc)

    def save_unknown_payload(self, response: RawResponse, reason: str) -> Path | None:
        """Archive a payload we could not interpret, for offline analysis."""
        if not self._cfg.enabled or not self._cfg.archive_unknown:
            return None
        self._unknown_dir.mkdir(parents=True, exist_ok=True)
        self._seq += 1
        # Sequence + status keeps names unique and ordered without a wall clock.
        stem = f"payload_{self._seq:06d}_status{response.status}_{response.size}b"
        body_path = self._unknown_dir / f"{stem}.bin"
        meta_path = self._unknown_dir / f"{stem}.meta.json"
        try:
            _atomic_write_bytes(body_path, response.body[: self._cfg.max_snapshot_bytes])
            meta = {
                "url": response.url,
                "status": response.status,
                "source": response.source,
                "content_type": response.content_type,
                "size": response.size,
                "reason": reason,
                "headers": dict(response.headers),
            }
            _atomic_write_bytes(
                meta_path, json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8")
            )
        except OSError as exc:
            _log.warning("failed to archive unknown payload: %s", exc)
            return None
        _log.warning("archived unknown payload -> %s (%s)", body_path, reason)
        return body_path


class MetricsCollector:
    """Accumulates aggregate runtime counters and rolling averages."""

    __slots__ = (
        "_aircraft_per_cycle",
        "_clock",
        "_cycle_ms",
        "_response_bytes",
        "_started_at",
        "_unique_supplier",
        "browser_restarts",
        "page_reloads",
        "total_aircraft_seen",
        "total_cycles",
        "total_failures",
        "total_requests",
        "total_retries",
    )

    def __init__(
        self,
        clock: Clock | None = None,
        avg_window: int = 256,
    ) -> None:
        self._clock: Clock = clock or SystemClock()
        self._started_at = self._clock.monotonic()
        self._cycle_ms = MovingAverage(avg_window)
        self._response_bytes = MovingAverage(avg_window)
        self._aircraft_per_cycle = MovingAverage(avg_window)
        self.total_cycles = 0
        self.total_requests = 0
        self.total_failures = 0
        self.total_retries = 0
        self.browser_restarts = 0
        self.page_reloads = 0
        self.total_aircraft_seen = 0
        # Injected by the orchestrator so the snapshot can report unique count
        # without this class owning the dedup set (single responsibility).
        self._unique_supplier: Callable[[], int] = lambda: 0

    def bind_unique_supplier(self, supplier: Callable[[], int]) -> None:
        self._unique_supplier = supplier

    def record_request(self) -> None:
        self.total_requests += 1

    def record_failure(self) -> None:
        self.total_failures += 1

    def record_retry(self) -> None:
        self.total_retries += 1

    def record_browser_restart(self) -> None:
        self.browser_restarts += 1

    def record_page_reload(self) -> None:
        self.page_reloads += 1

    def record_cycle(self, *, duration_s: float, response_bytes: int, aircraft_seen: int) -> None:
        self.total_cycles += 1
        self.total_aircraft_seen += aircraft_seen
        self._cycle_ms.add(duration_s)
        self._response_bytes.add(float(response_bytes))
        self._aircraft_per_cycle.add(float(aircraft_seen))

    def snapshot(self) -> MetricsSnapshot:
        return MetricsSnapshot(
            uptime_seconds=self._clock.monotonic() - self._started_at,
            total_cycles=self.total_cycles,
            total_requests=self.total_requests,
            total_failures=self.total_failures,
            total_retries=self.total_retries,
            browser_restarts=self.browser_restarts,
            page_reloads=self.page_reloads,
            total_aircraft_seen=self.total_aircraft_seen,
            unique_aircraft=self._unique_supplier(),
            avg_cycle_seconds=self._cycle_ms.value,
            avg_response_bytes=self._response_bytes.value,
            avg_aircraft_per_cycle=self._aircraft_per_cycle.value,
        )
