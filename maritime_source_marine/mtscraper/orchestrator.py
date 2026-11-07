"""The collection loop — the one stateful, long-lived coordinator.

It owns the cadence (navigate/reload -> settle -> harvest -> parse -> validate ->
accumulate -> report -> sleep) and the operational muscle a long-running process
needs: health probing, gap journalling, a self-healing recovery ladder
(empty -> reload -> restart -> abort), lifetime metrics, and never letting one
bad cycle kill the process.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime

from .accumulator import Accumulator
from .browser import BrowserPort
from .config import Config
from .diagnostics import DiagnosticsWriter
from .enrichment import DetailAttempt, DetailOutcome, IdentityStore, VesselDetailEnricher
from .exceptions import BrowserError, ScraperError
from .fetcher import TileFetcher
from .logger import get_logger
from .metrics import MetricsTracker
from .models import CycleReport, Vessel
from .parser import VesselParser
from .recovery import RecoveryAction, RecoveryPolicy, StateRecovery
from .session import SessionRotator
from .validation import CollectionGapDetector, PositionQuality, RecordValidator

_log = get_logger("orchestrator")

#: Stop enriching for the cycle after this many consecutive misses (soft-blocks
#: or errors) — avoids hammering a Cloudflare-challenged endpoint.
_ENRICH_BACKOFF_MISSES = 3

#: Upper bound (in cycles) on the exponential cool-down after a fully-blocked
#: enrichment cycle.
_ENRICH_MAX_BACKOFF_CYCLES = 30

#: How many times a vessel may answer "no record" before it is parked. Some
#: SHIP_IDs on the live map genuinely have no detail page, and retrying them
#: forever is what starved enrichment of its budget.
_ENRICH_MAX_FAILURES = 3

#: How many blocked detail responses to archive per run. A handful is enough to
#: read the cause; a stalled endpoint would otherwise fill the disk with copies
#: of the same interstitial.
_ENRICH_ARCHIVE_LIMIT = 5


class Orchestrator:
    """Drive collection cycles until a stop condition is met."""

    def __init__(
        self,
        cfg: Config,
        browser: BrowserPort,
        fetcher: TileFetcher,
        parser: VesselParser,
        validator: RecordValidator,
        accumulator: Accumulator,
        diagnostics: DiagnosticsWriter,
        recovery: RecoveryPolicy,
        state_recovery: StateRecovery,
        gap_detector: CollectionGapDetector,
        metrics: MetricsTracker,
        enricher: VesselDetailEnricher | None = None,
        identity_store: IdentityStore | None = None,
        rotator: SessionRotator | None = None,
    ) -> None:
        self._cfg = cfg
        self._browser = browser
        self._fetcher = fetcher
        self._parser = parser
        self._validator = validator
        self._accumulator = accumulator
        self._diag = diagnostics
        self._recovery = recovery
        self._state_recovery = state_recovery
        self._gap_detector = gap_detector
        self._metrics = metrics
        self._enricher = enricher
        self._identity_store = identity_store
        self._rotator = rotator
        self._settle_s = cfg.settle_s
        self._cycle = 0
        self._stop = asyncio.Event()
        # passive-mode staleness + enrichment cross-cycle backoff state
        self._empty_streak = 0
        self._enrich_block_streak = 0
        self._enrich_resume_cycle = 0
        self._enrich_blocks_archived = 0
        self._enrich_failures: Counter[str] = Counter()
        self._metrics.bind_unique_supplier(lambda: self._accumulator.unique_count)

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        await self._browser.launch()
        try:
            while not self._stop.is_set():
                self._cycle += 1
                report = await self._run_cycle(first=self._cycle == 1)
                _log.info("cycle complete %s", report.as_log_fields())
                if self._cycle % 10 == 0:
                    _log.info("metrics %s", self._metrics.snapshot().as_log_fields())
                if self._cfg.max_cycles is not None and self._cycle >= self._cfg.max_cycles:
                    _log.info("reached max_cycles=%d; stopping", self._cfg.max_cycles)
                    break
                await self._sleep_or_stop(self._cfg.poll_interval_s)
        finally:
            _log.info("final metrics %s", self._metrics.snapshot().as_log_fields())
            await self._browser.shutdown()

    async def _run_cycle(self, *, first: bool) -> CycleReport:
        started = _now_monotonic()
        source: str | None = None
        parse_failed = False
        vessels: list[Vessel] = []
        tiles_seen = 0

        try:
            if not await self._browser.is_healthy():
                _log.warning("browser unhealthy; restarting")
                await self._restart()
            elif not first:
                await self._refresh_view()

            await self._sleep_or_stop(self._settle_s)

            outcomes = await self._fetcher.fetch()
            tiles_seen = len(outcomes)
            if outcomes:
                source = outcomes[0].response.source
                for outcome in outcomes:
                    vessels.extend(self._parser.parse(outcome.payload))
                self._diag.dump_tile(outcomes[0].payload)
            else:
                await self._handle_empty_cycle()
            self._empty_streak = 0 if vessels else self._empty_streak + 1
        except BrowserError as exc:
            parse_failed = True
            self._metrics.record_failure()
            _log.error("browser error during cycle %d: %s", self._cycle, exc)
            self._diag.dump_exception(exc)
        except ScraperError as exc:
            parse_failed = True
            self._metrics.record_failure()
            _log.error("scraper error during cycle %d: %s", self._cycle, exc)
            self._diag.dump_exception(exc)

        now = datetime.now(UTC)
        records = [self._validator.validate(v, source=source or "unknown") for v in vessels]

        gap = await self._gap_detector.observe(now)
        if gap is not None:
            self._metrics.record_gap()

        if vessels:
            self._recovery.on_productive_cycle()

        result = self._accumulator.accept(records, now.isoformat())
        if result.new_ids:
            await self._state_recovery.append_to_cache(result.new_ids)

        enriched = await self._enrich_pending()

        self._metrics.record_cycle(
            seconds=_now_monotonic() - started, tiles=tiles_seen, vessels=result.received
        )
        browser_ok = await self._browser.is_healthy()

        return CycleReport(
            cycle=self._cycle,
            source=source,
            tiles_seen=tiles_seen,
            vessels_received=result.received,
            vessels_in_area=result.in_area,
            near_border=sum(1 for r in records if r.is_near),
            new_vessels=result.new_vessels,
            duplicates=result.duplicates,
            out_of_area=result.out_of_area,
            invalid_identity=result.invalid_identity,
            suspect=sum(1 for r in records if r.position_quality is PositionQuality.SUSPECT),
            positions_written=result.positions_written,
            enriched=enriched,
            parse_failed=parse_failed,
            processing_seconds=_now_monotonic() - started,
            browser_healthy=browser_ok,
            total_known=result.total_known,
        )

    # -- enrichment (resolve SHIP_ID -> MMSI/IMO/…, throttled + capped) ----- #

    async def _enrich_pending(self) -> int:
        """Resolve identities for catalogued vessels not yet resolved.

        Capped per cycle and throttled (the endpoint is per-vessel and
        Cloudflare-fronted). Consecutive soft-block/misses trip a backoff so we
        never hammer a challenged endpoint. Positions collection is unaffected —
        this runs after the fast pipeline has already persisted the cycle.
        """
        if self._enricher is None or self._identity_store is None:
            return 0
        if self._cfg.enrich_max_per_cycle <= 0:
            return 0
        if self._cycle < self._enrich_resume_cycle:
            return 0  # in an exponential cool-down after a blocked cycle
        pending = self._enrich_queue()
        if not pending:
            return 0

        enriched = 0
        misses = 0
        outcomes: Counter[str] = Counter()
        fetched_at = datetime.now(UTC).isoformat()
        for ship_id in pending:
            if enriched >= self._cfg.enrich_max_per_cycle or self._stop.is_set():
                break
            if misses >= _ENRICH_BACKOFF_MISSES:
                _log.debug("enrichment backing off after %d consecutive misses", misses)
                break
            attempt = await self._enricher.attempt_detail(ship_id)
            outcomes[attempt.outcome.value] += 1
            if attempt.detail is None:
                misses += 1
                # Only a well-formed reply that simply has no record counts
                # against the vessel; a challenge or transport error says nothing
                # about it and must not push a resolvable id toward being parked.
                if attempt.outcome is DetailOutcome.NO_RECORD:
                    self._enrich_failures[ship_id] += 1
                self._archive_enrichment_block(attempt)
            else:
                misses = 0
                self._enrich_failures.pop(ship_id, None)
                if self._identity_store.add(attempt.detail, fetched_at):
                    enriched += 1
            # Throttle after EVERY attempt. Sleeping only after a success would
            # fire the misses back-to-back — no spacing at all on precisely the
            # path where a challenged endpoint is least tolerant of it.
            await self._sleep_or_stop(self._cfg.enrich_throttle_s)

        if enriched:
            self._enrich_block_streak = 0
            self._metrics.record_identities(enriched)
            _log.info(
                "enriched %d identities this cycle (%d resolved total)",
                enriched,
                self._identity_store.resolved_count,
            )
        elif misses:
            # A fully-blocked enrichment cycle: exponential cool-down + rotate.
            self._enrich_block_streak += 1
            cooldown = min(_ENRICH_MAX_BACKOFF_CYCLES, 2**self._enrich_block_streak)
            self._enrich_resume_cycle = self._cycle + cooldown
            # The outcome breakdown is the diagnosis: http_error points at the
            # address, challenge at the browser clearance, no_record at the rate.
            _log.warning(
                "enrichment blocked; cooling down %d cycles (%s)",
                cooldown,
                ", ".join(f"{name}={count}" for name, count in sorted(outcomes.items())),
            )
            await self._maybe_rotate("enrichment blocked")
        return enriched

    def _enrich_queue(self) -> list[str]:
        """Vessels still needing an identity, least-failed first.

        Ordering matters more than it looks. A plain ``sorted(pending)`` hands the
        same ids to every cycle, so a handful of vessels the endpoint has no record
        for sit at the head of the queue and consume the entire per-cycle miss
        budget before a resolvable vessel is ever reached — enrichment then reports
        itself blocked forever while the endpoint is in fact answering fine.
        (Observed live: 3% of the archive resolved, then a permanent stall on the
        same three ids.)

        Sorting by failure count puts never-tried vessels ahead of ones already
        known to fail, so every cycle makes progress, and ids that keep failing are
        dropped once they have had ``_ENRICH_MAX_FAILURES`` chances.
        """
        if self._identity_store is None:
            return []
        pending = self._accumulator.known_ids() - self._identity_store.resolved_ids()
        return sorted(
            (s for s in pending if self._enrich_failures[s] < _ENRICH_MAX_FAILURES),
            key=lambda s: (self._enrich_failures[s], s),
        )

    def _archive_enrichment_block(self, attempt: DetailAttempt) -> None:
        """Keep the first few blocked bodies so the cause can be read, not guessed."""
        if attempt.response is None or self._enrich_blocks_archived >= _ENRICH_ARCHIVE_LIMIT:
            return
        self._enrich_blocks_archived += 1
        self._diag.save_unknown_payload(attempt.response)

    # -- recovery orchestration ------------------------------------------- #

    async def _refresh_view(self) -> None:
        """Get fresh positions for the cycle, per the collection mode.

        * ``active``  — reload every cycle (freshest, more request traffic).
        * ``passive`` — never reload; harvest the live map's own refresh cadence
          (stealthier), reloading only when it has gone stale for too long.
        """
        if self._cfg.collection_mode == "active":
            await self._browser.reload()
            self._metrics.record_page_reload()
            return
        if self._empty_streak >= self._cfg.passive_reload_after_empty:
            _log.info("passive: staleness reload after %d empty cycles", self._empty_streak)
            await self._browser.reload()
            self._metrics.record_page_reload()
            self._empty_streak = 0

    async def _handle_empty_cycle(self) -> None:
        _log.warning("no usable tiles this cycle")
        if await self._browser.detect_cloudflare():
            _log.warning("Cloudflare interstitial present — data blocked this cycle")
            self._diag.dump_html(await self._browser.capture_html())
            if self._rotator is not None and self._rotator.size > 1:
                await self._maybe_rotate("cloudflare on tiles")
                return
        action = self._recovery.on_empty_cycle()
        await self._apply_action(action)

    async def _maybe_rotate(self, reason: str) -> None:
        """Switch to the next session identity and restart, if rotation is set up."""
        if self._rotator is None or self._rotator.size <= 1:
            return
        _log.warning("rotating identity (%s)", reason)
        self._rotator.rotate()
        # a fresh identity clears the enrichment cool-down — give it a chance
        self._enrich_block_streak = 0
        self._enrich_resume_cycle = 0
        await self._restart()

    async def _apply_action(self, action: RecoveryAction) -> None:
        if action is RecoveryAction.RELOAD:
            await self._browser.reload()
            self._metrics.record_page_reload()
        elif action is RecoveryAction.RESTART:
            await self._restart()
        elif action is RecoveryAction.ABORT:
            _log.error("recovery requested ABORT; stopping run")
            self.request_stop()

    async def _restart(self) -> None:
        try:
            await self._browser.restart()
        except BrowserError as exc:
            self._metrics.record_failure()
            action = self._recovery.on_restart_failed()
            _log.error("browser restart failed: %s (-> %s)", exc, action.value)
            if action is RecoveryAction.ABORT:
                self.request_stop()
            return
        self._metrics.record_browser_restart()
        self._recovery.on_restart_succeeded()

    async def _sleep_or_stop(self, seconds: float) -> None:
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return


def _now_monotonic() -> float:
    return asyncio.get_event_loop().time()
