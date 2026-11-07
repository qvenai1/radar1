"""Unit tests for the debug raw-payload dump and analyzer."""

from __future__ import annotations

import json
from pathlib import Path

import zstandard

from adsb_scraper.config import DiagnosticsConfig
from adsb_scraper.diagnostics import DiagnosticsRecorder, analyze_raw_payload
from adsb_scraper.models import RawResponse


def _zstd(payload: bytes) -> bytes:
    return zstandard.ZstdCompressor().compress(payload)


def _resp(body: bytes, content_type: str = "application/zstd") -> RawResponse:
    return RawResponse(
        url="u",
        status=200,
        headers={"content-type": content_type},
        body=body,
        source="interception",
    )


def test_analyze_reports_json_when_inner_is_json() -> None:
    report = analyze_raw_payload(_resp(_zstd(b'{"aircraft": []}')))
    assert "zstd_decompress: SUCCESS" in report
    assert "inner_looks_json: True" in report


def test_analyze_reports_binary_when_inner_is_not_json() -> None:
    # A binCraft-like binary blob: decompresses fine but is not JSON.
    report = analyze_raw_payload(_resp(_zstd(bytes(range(64)))))
    assert "zstd_decompress: SUCCESS" in report
    assert "inner_looks_json: False" in report


def test_analyze_reports_failure_when_not_zstd() -> None:
    report = analyze_raw_payload(_resp(b"<html>not zstd</html>", "text/html"))
    assert "zstd_decompress: FAILED" in report


def test_dump_writes_verbatim_bytes_and_analysis(tmp_path: Path) -> None:
    body = _zstd(json.dumps({"aircraft": []}).encode())
    rec = DiagnosticsRecorder(DiagnosticsConfig(enabled=True, dump_raw=True), tmp_path)

    path = rec.dump_raw_payload(_resp(body), "20260101_000000_000000")

    assert path is not None
    assert path.read_bytes() == body  # exact bytes, no decode/truncation
    assert path.with_suffix(".analysis.txt").exists()


def test_dump_is_capped(tmp_path: Path) -> None:
    rec = DiagnosticsRecorder(DiagnosticsConfig(enabled=True, dump_raw=True), tmp_path)
    made = [rec.dump_raw_payload(_resp(_zstd(b"{}")), f"ts{i}") for i in range(10)]
    assert sum(p is not None for p in made) == DiagnosticsRecorder._MAX_RAW_DUMPS
