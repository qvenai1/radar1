"""Vessel identity enrichment — resolve SHIP_ID -> MMSI/IMO/callsign/....

The tile feed the scraper harvests is keyed by MarineTraffic's internal
``SHIP_ID`` and carries **no** standard maritime identity. This module fills that
gap by fetching, per vessel, the site's own detail endpoint from inside the
already-warmed browser session:

    GET {base}/en/vessels/{ship_id}/general
    -> {"shipId":…, "name":…, "imo":…, "mmsi":…, "callsign":…, "country":…, …}

Verified live. The endpoint is Cloudflare-fronted, so a call can occasionally
return an HTTP 200 that is *not* the real record (a soft-block during a challenge
window). :class:`VesselDetailEnricher` therefore treats a payload lacking the
``shipId`` key as a failure to be retried later, never a resolved vessel.

Enrichment is deliberately decoupled from the fast position pipeline: it is
throttled and capped per cycle (the endpoint is per-vessel and rate-limited), and
resolved identities are written to their own append-only catalogue keyed by
``ship_id`` — a join table for the positions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from . import constants as C
from .logger import get_logger
from .models import RawResponse
from .parser import ResponseDecoder
from .persistence import DurableJsonlWriter

_log = get_logger("enrichment")

_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300

#: Substrings that mark a Cloudflare interstitial served in place of the record.
#: Matched case-insensitively against the head of the body.
_CHALLENGE_MARKERS: Final = (
    b"just a moment",
    b"cf-browser-verification",
    b"challenge-platform",
    b"cf_chl_opt",
    b"attention required",
    b"enable javascript and cookies",
)
_CHALLENGE_SCAN_BYTES: Final = 4096


class DetailOutcome(StrEnum):
    """Why a lookup did or did not produce a record.

    The distinction is the whole point: a blocked endpoint returning HTTP 403, a
    Cloudflare interstitial, and a 200 whose body simply lacks the record all
    used to collapse into ``None``, which made a stalled enrichment run
    impossible to diagnose without guessing. Each implies a different remedy —
    an address-level block, a stale browser clearance, and a rate problem
    respectively.
    """

    RESOLVED = "resolved"
    TRANSPORT_ERROR = "transport_error"
    NO_RESPONSE = "no_response"
    EMPTY_BODY = "empty_body"
    HTTP_ERROR = "http_error"
    CHALLENGE = "challenge"
    UNDECODABLE = "undecodable"
    NO_RECORD = "no_record"


@dataclass(frozen=True, slots=True)
class DetailAttempt:
    """One identity lookup: the record, or a classified reason there is none."""

    outcome: DetailOutcome
    detail: VesselDetail | None = None
    status: int | None = None
    #: Retained on failure only, so a caller can archive the body for forensics.
    response: RawResponse | None = None

    @property
    def resolved(self) -> bool:
        return self.detail is not None


@dataclass(frozen=True, slots=True)
class VesselDetail:
    """The standard maritime identity for one vessel, resolved from MT details."""

    ship_id: str
    mmsi: str | None
    imo: str | None
    eni: str | None
    callsign: str | None
    name: str | None
    country: str | None
    country_code: str | None
    ship_type: str | None
    subtype: str | None
    grouped_type: str | None
    length_m: float | None
    width_m: float | None
    year_built: int | None
    transponder_class: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ship_id": self.ship_id,
            "mmsi": self.mmsi,
            "imo": self.imo,
            "eni": self.eni,
            "callsign": self.callsign,
            "name": self.name,
            "country": self.country,
            "country_code": self.country_code,
            "ship_type": self.ship_type,
            "subtype": self.subtype,
            "grouped_type": self.grouped_type,
            "length_m": self.length_m,
            "width_m": self.width_m,
            "year_built": self.year_built,
            "transponder_class": self.transponder_class,
        }


@runtime_checkable
class DetailFetcher(Protocol):
    """The single browser capability the enricher needs (an in-page fetch)."""

    async def evaluate_fetch(
        self, endpoint: str, headers: dict[str, str] | None = None
    ) -> RawResponse | None: ...


class VesselDetailEnricher:
    """Fetch and parse one vessel's identity from the details endpoint.

    Pure of scheduling/throttling — it resolves exactly one ``ship_id`` per call
    and returns ``None`` on any failure (non-2xx, undecodable, or a soft-block
    body without ``shipId``) so the caller can retry it later.
    """

    __slots__ = ("_base_url", "_browser", "_decoder")

    def __init__(
        self,
        browser: DetailFetcher,
        base_url: str = C.DEFAULT_BASE_URL,
        decoder: ResponseDecoder | None = None,
    ) -> None:
        self._browser = browser
        self._base_url = base_url
        self._decoder = decoder or ResponseDecoder()

    async def fetch_detail(self, ship_id: str) -> VesselDetail | None:
        """Resolve one identity, or None. Kept for callers that need no reason."""
        return (await self.attempt_detail(ship_id)).detail

    async def attempt_detail(self, ship_id: str) -> DetailAttempt:
        """Resolve one identity, reporting *why* when it fails."""
        url = f"{self._base_url}{C.VESSEL_DETAIL_PATH.format(ship_id=ship_id)}"
        try:
            resp = await self._browser.evaluate_fetch(url, headers=C.AJAX_HEADERS)
        except Exception as exc:
            _log.debug("detail fetch error for %s: %s", ship_id, exc)
            return DetailAttempt(DetailOutcome.TRANSPORT_ERROR)
        if resp is None:
            return DetailAttempt(DetailOutcome.NO_RESPONSE)
        if not (_HTTP_OK_MIN <= resp.status < _HTTP_OK_MAX):
            return DetailAttempt(DetailOutcome.HTTP_ERROR, status=resp.status, response=resp)
        if not resp.body:
            return DetailAttempt(DetailOutcome.EMPTY_BODY, status=resp.status, response=resp)
        # A challenge arrives as a 200 carrying an interstitial page, so it must be
        # recognised before decoding — otherwise it is indistinguishable from junk.
        if self._is_challenge(resp.body):
            return DetailAttempt(DetailOutcome.CHALLENGE, status=resp.status, response=resp)
        try:
            payload = self._decoder.decode(resp)
        except Exception:
            return DetailAttempt(DetailOutcome.UNDECODABLE, status=resp.status, response=resp)
        detail = self._parse(ship_id, payload)
        if detail is None:
            return DetailAttempt(DetailOutcome.NO_RECORD, status=resp.status, response=resp)
        return DetailAttempt(DetailOutcome.RESOLVED, detail=detail, status=resp.status)

    @staticmethod
    def _is_challenge(body: bytes) -> bool:
        head = body[:_CHALLENGE_SCAN_BYTES].lower()
        return any(marker in head for marker in _CHALLENGE_MARKERS)

    @classmethod
    def _parse(cls, ship_id: str, payload: Any) -> VesselDetail | None:
        # A genuine record has the shipId key; anything else is a soft-block.
        if not isinstance(payload, dict) or "shipId" not in payload:
            return None
        commercial = payload.get("commercial_type")
        grouped = None
        if isinstance(commercial, dict):
            grouped = commercial.get("market_name")
        grouped = payload.get("comFleetGroupedTypeName") or grouped
        return VesselDetail(
            ship_id=ship_id,
            mmsi=cls._id(payload.get("mmsi")),
            imo=cls._id(payload.get("imo")),
            eni=cls._id(payload.get("eni")),
            callsign=cls._text(payload.get("callsign")),
            name=cls._text(payload.get("name") or payload.get("aisName")),
            country=cls._text(payload.get("country")),
            country_code=cls._text(payload.get("countryCode")),
            ship_type=cls._text(payload.get("type")),
            subtype=cls._text(payload.get("subtype")),
            grouped_type=cls._text(grouped),
            length_m=cls._num(payload.get("length")),
            width_m=cls._num(payload.get("width")),
            year_built=cls._year(payload.get("yearBuilt")),
            transponder_class=cls._text(payload.get("aisTransponderClass")),
        )

    @staticmethod
    def _id(value: Any) -> str | None:
        """Identity codes (mmsi/imo/eni) are stored as strings, never arithmetic."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _num(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _year(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class IdentityStore:
    """Append-only, deduplicated catalogue of resolved vessel identities.

    Keyed by ``ship_id``. Resolved ids are loaded on open so a restart never
    re-resolves a vessel, and so the orchestrator can compute which catalogued
    vessels still need enrichment.
    """

    def __init__(self, path: Path) -> None:
        self._writer = DurableJsonlWriter(path)
        self._resolved: set[str] = self._load_resolved(path)

    @property
    def resolved_count(self) -> int:
        return len(self._resolved)

    def is_resolved(self, ship_id: str) -> bool:
        return ship_id in self._resolved

    def resolved_ids(self) -> set[str]:
        return set(self._resolved)

    def add(self, detail: VesselDetail, fetched_at: str) -> bool:
        """Persist a resolved identity if unseen. Returns True if newly added."""
        if detail.ship_id in self._resolved:
            return False
        record = {"fetched_at": fetched_at, **detail.to_dict()}
        self._writer.write(record)
        self._resolved.add(detail.ship_id)
        return True

    def close(self) -> None:
        self._writer.close()

    @staticmethod
    def _load_resolved(path: Path) -> set[str]:
        import json

        resolved: set[str] = set()
        if not path.exists():
            return resolved
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ship_id = json.loads(line).get("ship_id")
                except json.JSONDecodeError:
                    continue
                if isinstance(ship_id, str):
                    resolved.add(ship_id)
        _log.info("loaded %d resolved identities from %s", len(resolved), path.name)
        return resolved
