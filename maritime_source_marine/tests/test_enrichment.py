"""Enrichment tests, anchored on the real captured /general payload."""

from __future__ import annotations

import json
from pathlib import Path

from mtscraper.enrichment import DetailOutcome, IdentityStore, VesselDetailEnricher
from mtscraper.models import RawResponse

# Verbatim from a live capture of /en/vessels/6663445/general (MSC AMELIA).
REAL_DETAIL = {
    "shipId": 6663445,
    "name": "MSC AMELIA",
    "aisName": "MSC AMELIA",
    "imo": 9896995,
    "eni": None,
    "mmsi": 636020543,
    "length": 399.77,
    "width": 61.03,
    "callsign": "D5ZR4",
    "country": "LIBERIA",
    "countryCode": "LR",
    "type": "Cargo",
    "typeId": 70,
    "comFleetGroupedTypeName": "CONTAINER SHIPS",
    "subtype": "Container Ship",
    "yearBuilt": None,
    "aisTransponderClass": "Class A",
    "commercial_type": {"market_name": "CONTAINER SHIPS", "size_class_name": "ULCV"},
}


class FakeFetcher:
    """Returns a preset RawResponse (or None) for evaluate_fetch."""

    def __init__(self, body: bytes | None, status: int = 200) -> None:
        self._body = body
        self._status = status
        self.calls: list[str] = []

    async def evaluate_fetch(
        self, endpoint: str, headers: dict[str, str] | None = None
    ) -> RawResponse | None:
        self.calls.append(endpoint)
        self.last_headers = headers
        if self._body is None:
            return None
        return RawResponse(
            url=endpoint,
            status=self._status,
            headers={"content-type": "application/json"},
            body=self._body,
            source="direct_tile",
        )


async def test_resolves_real_identity() -> None:
    fetcher = FakeFetcher(json.dumps(REAL_DETAIL).encode())
    detail = await VesselDetailEnricher(fetcher).fetch_detail("6663445")
    assert detail is not None
    assert detail.mmsi == "636020543"
    assert detail.imo == "9896995"
    assert detail.eni is None
    assert detail.callsign == "D5ZR4"
    assert detail.name == "MSC AMELIA"
    assert detail.country_code == "LR"
    assert detail.length_m == 399.77
    assert detail.transponder_class == "Class A"
    assert "6663445/general" in fetcher.calls[0]
    # the detail endpoint 400s without this header — verified live
    assert fetcher.last_headers == {"X-Requested-With": "XMLHttpRequest"}


async def test_soft_block_body_returns_none() -> None:
    # 200 but not the real record (no shipId) => retryable miss, not resolved.
    fetcher = FakeFetcher(json.dumps({"rows": [], "areaShips": 0}).encode())
    assert await VesselDetailEnricher(fetcher).fetch_detail("6663445") is None


async def test_non_200_returns_none() -> None:
    fetcher = FakeFetcher(json.dumps(REAL_DETAIL).encode(), status=403)
    assert await VesselDetailEnricher(fetcher).fetch_detail("6663445") is None


# --- failure classification ------------------------------------------------- #
# Every one of these used to collapse into a bare None, which left a stalled
# enrichment run undiagnosable. Each now names a different remedy.


async def test_success_is_classified_and_carries_no_response() -> None:
    fetcher = FakeFetcher(json.dumps(REAL_DETAIL).encode())
    attempt = await VesselDetailEnricher(fetcher).attempt_detail("6663445")
    assert attempt.outcome is DetailOutcome.RESOLVED
    assert attempt.resolved
    assert attempt.response is None  # nothing to archive on success


async def test_http_error_is_classified_with_its_status() -> None:
    """A 403 points at the address, not the browser session — do not confuse them."""
    fetcher = FakeFetcher(b"forbidden", status=403)
    attempt = await VesselDetailEnricher(fetcher).attempt_detail("6663445")
    assert attempt.outcome is DetailOutcome.HTTP_ERROR
    assert attempt.status == 403
    assert attempt.response is not None  # retained for forensics


async def test_challenge_page_is_classified_despite_http_200() -> None:
    """Cloudflare serves the interstitial as a 200; only the body gives it away."""
    body = b"<!DOCTYPE html><title>Just a moment...</title><div id=cf-challenge>"
    attempt = await VesselDetailEnricher(FakeFetcher(body)).attempt_detail("6663445")
    assert attempt.outcome is DetailOutcome.CHALLENGE
    assert attempt.status == 200


async def test_soft_block_is_classified_as_no_record() -> None:
    fetcher = FakeFetcher(json.dumps({"rows": [], "areaShips": 0}).encode())
    attempt = await VesselDetailEnricher(fetcher).attempt_detail("6663445")
    assert attempt.outcome is DetailOutcome.NO_RECORD


async def test_undecodable_body_is_classified() -> None:
    attempt = await VesselDetailEnricher(FakeFetcher(b"\xff\xfe not json")).attempt_detail("1")
    assert attempt.outcome is DetailOutcome.UNDECODABLE


async def test_missing_response_is_classified() -> None:
    attempt = await VesselDetailEnricher(FakeFetcher(None)).attempt_detail("1")
    assert attempt.outcome is DetailOutcome.NO_RESPONSE
    assert attempt.response is None


async def test_transport_error_is_classified() -> None:
    class Exploding:
        async def evaluate_fetch(
            self, endpoint: str, headers: dict[str, str] | None = None
        ) -> RawResponse | None:
            raise RuntimeError("connection reset")

    attempt = await VesselDetailEnricher(Exploding()).attempt_detail("1")
    assert attempt.outcome is DetailOutcome.TRANSPORT_ERROR


async def test_missing_response_returns_none() -> None:
    assert await VesselDetailEnricher(FakeFetcher(None)).fetch_detail("6663445") is None


async def test_ship_without_mmsi_still_resolves() -> None:
    payload = dict(REAL_DETAIL, mmsi=None, imo=None)
    detail = await VesselDetailEnricher(FakeFetcher(json.dumps(payload).encode())).fetch_detail("1")
    assert detail is not None  # shipId present => resolved
    assert detail.mmsi is None


def test_identity_store_dedup_and_reload(tmp_path: Path) -> None:
    import asyncio

    path = tmp_path / "ids.jsonl"
    fetcher = FakeFetcher(json.dumps(REAL_DETAIL).encode())
    detail = asyncio.run(VesselDetailEnricher(fetcher).fetch_detail("6663445"))
    assert detail is not None

    store = IdentityStore(path)
    assert store.add(detail, "t1") is True
    assert store.add(detail, "t2") is False  # dedup by ship_id
    assert store.is_resolved("6663445")
    assert store.resolved_count == 1
    store.close()

    # reload: resolved ids survive a restart
    store2 = IdentityStore(path)
    assert store2.is_resolved("6663445")
    rec = json.loads(path.read_text().splitlines()[0])
    assert rec["mmsi"] == "636020543"
    assert rec["ship_id"] == "6663445"
