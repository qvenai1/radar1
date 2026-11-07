# Backend Changelog

## 2026-09-01 — the caseKey anchor caught two live split-regressions the hour it was written

BATCH-1 item 2 set out to pin the SECOND hand mirror (client `caseKey` ↔
`notify.identity.dedup_key`, the key alert_triage / case_items / the bot ledger
all share) the way the orbital anchor pinned the first. Writing it surfaced
three real defects:

- **The mirror HAD drifted:** `maritime_single` was missing its `:summary` part
  — the dashboard merged a ship's rendezvous and zone_dwell into ONE triage
  identity while the server kept them apart, so that family's triage writes
  could never render back. Zero orphan rows in the live DB (checked both
  tables) — caught before any data was written wrong.
- **Two split-relocated imports were dead:** `from .notify.identity import …`
  inside `backend/routes/alerts.py` resolved to nonexistent
  `backend.routes.notify` since the split, and the handlers' DELIBERATE
  fail-open excepts swallowed the ModuleNotFoundError — `/alerts` served every
  alert `triage=None` and `/alerts/case` reported every item
  `still_live=False`, silently. Fixed to `..notify`; verified live
  (236/236 alerts now carry a key).
- **The class is now guarded, not just the instance:** the structural fix is
  `Alert.dedup_key` (v1 ADDITIVE) — the server publishes its own identity key
  beside `change_sig`, the client prefers it and keeps the (corrected) mirror
  only as fallback; the parity anchors (`tests/test_case_key_parity.py` ↔
  `frontend/src/api/caseKey.test.ts`, ⛔ byte-identical vectors) pin the
  fallback, and a routes-wide test fails on any relative import that resolves
  to a ghost module.

## 2026-09-01 — Mypy adopted on the backend at zero errors (lenient tier)

The second half of tech-stack finding 4. `[tool.mypy]` in the root pyproject
covers `backend/` (63 files) with the **pydantic plugin** — mandatory, since
without it every `Field(None, …)` default reads as a missing constructor
argument (that alone was 37 of the original 74 errors, all false).

What the real 37 fixed, by kind:
- **Five genuine annotation bugs:** every `_*_ar()` newsroom renderer declared
  `tuple[str, str, str]` and returned FOUR strings (headline/why/caveat/lede).
- **One latent-crash made fail-closed:** `zone_dwell` now SKIPS a run whose
  closest approach falls outside every band (no band = no signal = no session)
  instead of feeding `signal=None` into a `str` field.
- The `(xs[:1] or [None])[0]` idiom → a typed `_first()`; the `...` filter
  sentinel in notify typed as `EllipsisType`; `dict.get(None)` sites → `or ""`
  (byte-identical results); two invariants the comments already claimed turned
  into asserts (imo verdict, replay's required `at`); a shadowed `age`
  renamed `src_age`.
- ⛔ Strictness is deliberately the LENIENT tier (no --strict, constructor args
  unchecked): adopted at zero so any new error is a regression; the path up is
  per-module strict flags, sub-packages already run --strict.

## 2026-09-01 — /military gains the archive-first + DB-fallback ladder (v1 ADDITIVE)

The one overlay with no fallback (arch review finding 11) now mirrors /live:
fresh `military.json` serves as before; a stale/missing file falls through to
the newest `airplanes_live` snapshot in `state_vectors` — including the
airframe extras (`actype`/`acdesc`/`reg`) via the new `Database.military_states_at`
— and DB trouble returns the file verdict, never a 500 ("never an error" stays
this endpoint's contract). `MilitaryResponse.source` (archive|db) is v1
ADDITIVE. Five-rung detonation in `tests/test_military_fallback.py`.

⚠ **Live finding the moment it shipped:** the ladder surfaced that the military
feed has been dead since ~2026-08-12 — `api.airplanes.live/v2/mil` answers
**403 Forbidden**; the collector is healthy and backing off correctly, the
UPSTREAM is refusing. Operator decision needed (request access / drop the
layer); the overlay now reports this honestly instead of rendering blank.

## 2026-09-01 — Enricher injection seam + the first offline tests for the enrichment surface

Arch review finding 10: no test substituted `app.state.enricher`, so the two
endpoints that reach third-party APIs at request time had no isolated coverage.

- `create_app(*, enricher=...)` is now the explicit seam, assigned at
  construction time (NOT in lifespan) so the httpx ASGITransport test idiom —
  which never runs lifespan — gets one automatically. The real Enricher opens
  no connection until first use, so eager construction costs nothing; lifespan
  still owns `close()`.
- `tests/test_enricher.py` detonates the three cache branches by faking the
  upstream seam (`_fetch_aircraft`) and the pool: fresh hit never calls
  upstream, **stale beats nothing** when both upstreams are unreachable (and
  must NOT overwrite the cache), no-row + unreachable degrades to
  `found=False`, a fetch upserts and returns.
- `tests/test_enrichment_api.py` proves the seam at wire level: photo
  attribution and route mapping, not-found → nulls never errors, and the
  identifier guards (non-hex icao24, path-chars callsign, non-digit mmsi)
  firing as 422s.

## 2026-09-01 — Ruff adopted on the backend at zero findings (F + B)

Tech-stack review finding 4: the strictest tooling guarded the smallest 10 % of
the code while `backend/` had none. Root `pyproject.toml` now runs ruff over
`backend/ *.py tests/ scripts/` with the CORRECTNESS families only — F (pyflakes)
and B (bugbear) — adopted at zero findings, so any new finding is a regression.

- ⛔ Style families stay out deliberately: E501 (2,893 long lines — Arabic
  rationale comments are house style), E7 (compact-math semicolons), I (import
  order). Widen only with a green tree.
- What adoption fixed: 13 unused imports, 1 unused variable, 3 unused loop vars,
  8 `raise` sites now chain explicitly (`from exc` on the 503 degradations,
  `from None` where the re-raise message is complete), and every `zip()` now
  DECLARES intent — `strict=True` where lengths are equal by construction
  (alert headline packs, column transposes), `strict=False` on the pairwise
  `zip(xs, xs[1:])` idiom.
- `ruff>=0.6` joins requirements-dev; constraints.txt regenerated (60 pins).

## 2026-09-01 — api.py split into backend/routes/ — one module per domain

Architecture review finding 1, the god-module half. The 4,261-line `api.py`
(44 routes, 33 helpers, five unrelated domains) is now ten domain modules under
`backend/routes/` — air 263 · search 641 · maritime 1,228 · alerts 669 ·
space 189 · enrichment 195 · system 64 · zones 522 · series 648 · shared 46 —
aggregated by the package `__init__` under the `/api/v1` prefix.

- ⛔ **The frozen contract is proven, not asserted: the OpenAPI schema before and
  after the split is byte-identical** (44 operations, full JSON compare).
- Segment moves were verbatim (ast-boundary cuts keeping each definition's
  attached comment block); imports per module were trimmed mechanically
  (ruff F401) and cross-module names resolved from F821 — the surface came out
  small and acyclic: everyone ← `shared` (`_db`/`parse_time`/`resolve_range`),
  `alerts` ← `maritime` + `air` (three names), `search` ← `space` (one name).
- `backend/api.py` is now a compatibility shim re-exporting every name tests
  and consumers historically imported. ⚠ `monkeypatch.setattr` on the shim
  rebinds only the shim — patch the domain module instead (the one affected
  test, dark-vessels, was re-pointed; two source-text assertions in
  test_track_source now read the domain files).
- 1,062 tests green; live server restarted and smoke-checked
  (/live /maritime /alerts /coverage → 200).

## 2026-09-01 — Opt-in read gate (ATHEER_API_KEY) + browser-protection headers

Security review findings 1+6, sized for what this deployment IS: a LAN analyst
dashboard on `0.0.0.0:8000`, plain HTTP, and — measured 2026-09-01 — **no armed
firewall** (`ufw` unit active but `ufw.conf ENABLED=no`; `systemctl is-active`
alone is a trap).

- One HTTP middleware in `app.py`: when `ATHEER_API_KEY` is set, `/api/*` requires
  `x-atheer-key` (or `?api_key=`), `hmac.compare_digest`, env read per request —
  the exact `_require_write_token` pattern. WS gets the same check in `stream.py`
  BEFORE `accept()` (HTTP middleware does not cover WebSocket routes; browsers
  cannot set WS headers, hence the query param).
- ⚠ Unset key = OPEN READS, the deliberate opposite of the write token's
  fail-closed rule: refusing every read on the deployment that never set a key
  turns each restart into a dashboard outage. The write guard stays fail-closed.
- Headers on every response: `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, CSP `frame-ancestors 'none'`. ⛔ No full CSP
  from the app — one wrong origin entry blanks the basemap silently; the
  reverse-proxy is the right place (README-backend § Exposure).
- SPA side: `apiKey()`/`setApiKey()` mirror the write-token localStorage contract;
  `get()`/`post()` attach the header, the WS URL carries the param.
- Detonation-proven in `tests/test_api_key.py`: armed gate refuses (401 / rejected
  handshake), unarmed gate admits, headers present.

## 2026-09-01 — Input hardening: identifier charsets pinned, window params bounded

Security review findings 3+5. The identifiers were validated by LENGTH only, yet
`icao24`/`callsign` are interpolated into upstream URL paths (`enrich.py` `.format`)
— `/ . ? # %` reshaped the request to adsbdb/planespotters (path injection / quota
burn). The window params had floors but no ceilings.

- `icao24` → `[0-9a-f]{4,10}` (the 422 message always SAID hex; now the check means it).
- `callsign` → `[A-Z0-9-]{1,12}` or 422, before it reaches `get_route`.
- `mmsi` → digits required, both `/vessel/{mmsi}` and `/vessel/{mmsi}/track`.
- `/air/series/country` `hours` ≤ **8,784 (366 d)** — ⛔ deliberately NOT lower: the
  in-code costing documents that long windows are answered slowly with `cost_note`,
  full year ≈ measured ~11.5 s. The ceiling closes only the unbounded case.
- `/air/series/country-daily` `days` → `Query(365, ge=1, le=730)` (summary table, cheap).

## 2026-09-01 — Event loop unblocked: journalctl and snapshot reads moved off the async path

Architecture review findings 2+3, severity corrected by measurement on this host
(measured 2026-09-01): the JSON snapshot parse is 0.6–5.2 ms (291/124/420 KiB files)
— real but small; `journalctl --list-boots` is **5.62 s cold** / 0.06 s warm with a
20 s timeout — a genuine whole-server freeze on every 600 s cache expiry, since the
single-worker event loop serves REST + WS together.

- Every sync call on an async path now goes through `asyncio.to_thread` (the pattern
  already in-tree at the A2 rendezvous shapely call): `read_latest_json` at its six
  handler sites, `_read_snapshot` at both call sites, and `host_gaps` in
  `build_series_coverage`.
- `_read_snapshot` itself stays sync and stays THE one snapshot reader; the wrap is
  at the call sites so the reader contract is untouched.
- ⛔ Rule going forward: an `async def` handler may not call `open()`, `json.load`,
  `subprocess.run`, or `.stat()` directly — wrap in `asyncio.to_thread`.

The free terrestrial AIS feed is blind in the Persian Gulf / Red Sea (no volunteer
receivers) and on the open ocean. Global Fishing Watch fills that blind spot with
SATELLITE-AIS gap events — a vessel that WENT DARK — so the sparse regions finally
have a dark-activity signal. The adapter was a documented stub ("phase 2, don't guess
endpoints"); with a real token in hand it is now implemented and LIVE-VERIFIED.

- **`maritime/adapters/gfw.py` `find_ais_gaps`** — queries `events.get_all_events`
  (dataset `public-global-gaps-events:latest`) via the official `gfwapiclient`,
  filtered by a GeoJSON `region` and/or a vessel (mmsi/imo → GFW vesselId). Response
  is PINNED to the live v3 shape (verified against a real token, not guessed): each gap
  → `{start, end, duration_h, lat, lon, mmsi, name, flag, intentional, distance_km,
  data_origin='satellite'}`. A LEAD, not proof — a gap can be a receiver outage or a
  legitimate silence; `intentional` is GFW's own likelihood flag, not a verdict.
- **Live-verified:** real dark-vessel events returned for the Persian Gulf (flags
  PLW/BHS/PAN) and the Red Sea — exactly the waters terrestrial AIS can't see.
- Adapter cleanly disabled without a token (`enabled=False`, clear error on call);
  `gfwapiclient` lazily imported so the layer runs without it. `get_track` stays a
  documented deferral (GFW serves tracks via a separate binned endpoint; the
  dark-vessel signal is the value here). Tests **+3** (mapping on the real shape +
  disabled contract); `gfw-api-python-client>=1.4` added to requirements. Suite green
  **536 passed**.
- **Surfaced (same day):** `GET /api/v1/maritime/dark-vessels?region=gulf|redsea|all`
  wraps `find_ais_gaps` with a region polygon + a TTL cache (GFW is historical +
  rate-limited), guarded (no token / unreachable → available=false, never 500), deduped
  + positioned-only, most-recent first. Default `limit=50` — GFW's events API returns an
  empty page at limit=100 over the large 'all' polygon (observed live), ≤50 is reliable.
  **Two classes of on-land marker, handled differently** (they were all plotting inland):
  (1) NON-vessel MMSIs — AtoN 99x / SAR 111x / base 00x — legitimately sit on land and are
  NOISE, so they are dropped (SHIP stations only: MMSI first digit 2-7). (2) SHIP stations
  reporting an on-land fix are a SUSPECT / spoofed AIS position — a signal, not noise — so
  they are KEPT and flagged `on_land=true` via a land/sea mask (`global-land-mask`), and
  the response carries a `suspect` count. Frontend: a «سفن صامتة» layer in the maritime
  «التحليل» group — at-sea events render as violet dots, on-land (suspect/spoofed) events
  as ORANGE DASHED rings + a «موقع بري» tag + an inline note. NOT a verdict: a coarse
  (~1.8 km) mask can also flag a berth at the coastal edge, stated in the caveat.
  Live: 'all' = 42 events (21 at-sea + 21 on-land suspect). Tests +3 (AtoN dropped +
  on-land ship kept & flagged).

## 2026-07-25 — AIS × MarineTraffic cross-source correlation (migration 034)

With two independent maritime sources now covering the AOI, this links them: which
MarineTraffic `ship_id` and which AIS `mmsi` are the SAME hull. They share no key
(MarineTraffic carries no MMSI), so the link is DERIVED from the evidence both feeds
DO share — and every link is a LEAD, not proof.

- **`backend/vessel_crossref.py`** (pure core, 13 tests) — name-anchored matching:
  normalize names (strip AIS '@' padding + punctuation), then corroborate with
  **position-over-time** and **dimensions**. Honest confidence tiers: `strong`
  (true co-observation, OR name + matching dimensions, OR co-location), `probable`
  (name matches, position non-contradictory), `weak` (dimension conflict far apart,
  or a kinematically impossible separation → likely name collision / possible spoof).
  One-to-one resolution (best per ship_id AND per mmsi); ties flagged `ambiguous`.
- **Design correction found by inspecting real output:** the first cut let
  "kinematically consistent over a large time gap" mean `strong` — but over many
  hours almost any two AOI points are 'consistent', which would inflate confidence on
  the name alone. Now position earns `strong` only via true co-observation or
  co-location; name+dimensions is the other `strong` path. And **co-location dominates
  a dimension conflict** — two hulls can't share a point, so SANTORINI / ENERGEAN POWER
  at 0 km with conflicting dimensions are the SAME vessel with a flagged *dimension
  discrepancy* (a data-quality / possible-spoof signal), not a rejected match.
- **Migration 034** — extends `vessel_xref` additively with `match_confidence`
  (CHECK strong|probable|weak), `match_score`, `match_distance_km`, `match_dt_minutes`,
  `match_ambiguous`, `match_reason`. The runner's upsert **never overwrites a
  `marinetraffic-feed` link** (an authoritative broadcast MMSI) with a derived one.
- **Runner `correlate_and_store`** + `tracker_marinetraffic.py correlate`. The AIS
  scan is padded ±2 days around the MarineTraffic window — the scrape and the AIS
  fixes for a hull are often offset by hours, so a scan bounded to the scrape span
  alone finds nothing (this was caught live: `ais_vessels: 0` until padded).
- **`GET /api/v1/maritime/crossref`** — the derived links with confidence + evidence,
  a coverage **summary computed over ALL links** (matched vs MarineTraffic-only — not
  just the displayed page), and the flagged discrepancies. `min_confidence` filter,
  honest caveat, guarded (DB down → available=false, never 500).
- **Verified live:** 258 MarineTraffic vessels → **128 links (122 strong, 6 probable,
  0 weak), 130 MarineTraffic-only, 3 dimension discrepancies** (SANTORINI, ENERGEAN
  POWER, MARLA ORCA — same berth, conflicting dimensions). Idempotent. Tests **+15**
  (core 13, API 2); suite green **488 passed**.
- **Honest limitation:** derived links are leads. A name collision, a stale scrape, or
  an AIS spoof can all produce a match or a discrepancy; the tiers + evidence
  prioritise review, they never assert identity.

## 2026-07-25 — MarineTraffic as an independent live maritime source (migrations 032/033)

Linked the standalone `mtscraper` project (a browser-interception scraper of the
MarineTraffic live map) into scraplan as a **second, independent live maritime
source** alongside AISStream — WITHOUT coupling the codebases. The adapter pattern
the maritime layer was built for absorbs it; the only new structural problem was
IDENTITY, solved additively rather than by a hack.

- **Isolation preserved (a hard requirement from `mtscraper`'s design):** scraplan
  imports NONE of mtscraper's code and never launches it; Playwright/Chromium stays
  out of scraplan's collector processes. The ONLY coupling is the data contract —
  mtscraper's `positions.jsonl` — read by a **file-tail adapter**. mtscraper stays a
  black box that produces a file; scraplan consumes it. Break either, the other is
  unaffected.
- **`maritime/adapters/marinetraffic_translate.py`** (pure, 16 tests) — one
  positions.jsonl line → the unified `VesselState`. Resolves two impedance
  mismatches: (1) identity — `ship_id` is carried first-class, `mmsi` passes through
  only when genuinely present (never faked); (2) fix time — the true position-fix
  time is `fetched_at − elapsed_min` (fetched_at is the collection instant,
  elapsed_min the report's age then). **ship_type** (corrected after a live audit):
  MarineTraffic's 1-digit `shiptype` is a PROPRIETARY coarse summary, NOT the AIS
  decade (verified: digit 3 = drillships/pipelayers, not fishing; digit 2 =
  supply/anchor-handling, not wing-in-ground). Only the high-confidence buckets
  6/7/8 → passenger/cargo/tanker are labelled specifically; 0/1 (VTS/markers) → not
  a vessel type; every other real vessel → the honest bucket "other" (never a
  specific-but-wrong label). `ship_type_code` is always NULL — no ITU code is given.
- **`maritime/adapters/marinetraffic.py`** (12 tests) — the live tail adapter
  (`MaritimeAdapter` contract, `name='marinetraffic'`). Survives a missing / rotated
  / truncated / half-written file: processes only COMPLETE lines (offset advances to
  the last newline), re-reads from the top if the file shrinks, and every poll is
  stop-aware. Feeds the Fleet under the ship_id identity.
- **Fleet/schema generalization (backward-compatible):** `VesselState` gained
  `ship_id` + an `identity_key` property + a source-reported `flag`; `mmsi` is now
  optional. `Fleet` keys on the identity handle (MMSI for AIS, ship_id for MT) via
  new `apply_*_keyed` methods — the historical `apply_position/apply_static(mmsi,…)`
  are unchanged thin wrappers, so **the entire AIS path and its tests are untouched**
  (465 passed, 0 regressions).
- **Migration 032 — `mt_vessel_states`** — a DEDICATED hypertable keyed
  `(ship_id, ts, source)`, the mature scraplan pattern for a source whose identity
  differs (rather than mutating the frozen `(mmsi, ts, source)` key or faking an
  mmsi, which would corrupt `mmsi_to_flag`/`vessel_identity`). Same semantics as
  `vessel_states` (knots, nulls-not-zeros, per-row identity), same shared geo
  classifier (12 NM territorial sea, `geo_source='scraplan-aoi'`), + a nullable
  `mmsi` enrichment column and a `flag` column. The count-over-time series REUSES
  `vessel_snapshots` with `source='marinetraffic'`.
- **Migration 033 — `vessel_xref`** — a `ship_id ↔ mmsi` identity bridge, populated
  only on a genuine link (`link_source='marinetraffic-feed'`); COALESCE never wipes
  a known value and a WHERE-changed guard keeps re-links no-ops. Enables a future
  AIS×MarineTraffic cross-view (dark-vessel corroboration, spoof cross-check).
- **`maritime/db.py`** (additive; the aisstream path untouched) — `MtVesselIngester`,
  `mt_vessel_row`, `mt_xref_row`, `parse_mt_lines`, `backfill_mt`. Live and backfill
  share ONE row-builder (via `to_vessel_dict`) so they produce byte-identical rows.
- **`tracker_marinetraffic.py`** — the collector composition root (its own process,
  own Fleet, own output dir — as the schema intends: `source` in every key). Same
  failure-tolerant contract: JSONL archive first, DB best-effort; a DB outage never
  stops archiving, and `backfill` covers the gap.
- **Verified live on the real DB + real mtscraper output:** 1068 lines → 1068 rows,
  0 invalid, 258 distinct ships, all geo-classified (in-territorial-sea: Cyprus 427,
  Lebanon 167, Syria 135, Israel 78). backfill idempotency proven (2nd run: 0 new,
  1068 duplicates). Live collector smoke-tested: 258 vessels in the snapshot,
  `source='marinetraffic'`. Tests **+34** (translate 16, adapter 12, db 6); suite
  green **465 passed, 7 skipped**.
- **Legal:** MarineTraffic's ToS prohibit scraping (documented in mtscraper's
  README); every row is provenance-tagged `source='marinetraffic'` so the origin is
  never ambiguous.
- **Frontend (done same day):** the بحري tab now requests `source=all`; MarineTraffic
  vessels render in a distinct amber glyph, keyed by a shared `vesselKey` (MMSI or
  ship_id), with an MT source chip and a per-source split in the sidebar. See the
  frontend changelog.
- **DB fallback so the source is actually VISIBLE (fix):** MarineTraffic is a PERIODIC
  scrape, not a continuous feed — the live overlay reads a file only the running
  collector writes, so with the collector off nothing showed. `/maritime` now falls
  back to the latest position per ship_id from `mt_vessel_states` when no fresh scrape
  file exists (AIS keeps its stale=empty behavior). Verified live: `source=all` → 461
  vessels (203 AISStream + 258 MarineTraffic) rendered together, MarineTraffic in amber
  off the Levant coast.
- **Multi-region coverage (Gulf / Red Sea / …):** one MarineTraffic viewport covers one
  patch of sea, so `tracker_marinetraffic.py --mt-output` now accepts SEVERAL mtscraper
  output dirs — one tail adapter per dir, all feeding the shared Fleet (ship_id-keyed,
  regions merge without collision). Run one mtscraper instance per region (e.g. E. Med +
  Persian Gulf `--lat 27 --lon 51` + Red Sea `--lat 15 --lon 42`, each
  `--persist-out-of-area` since they're outside mtscraper's default 6-country AOI).
  **Verified with a REAL live browser scrape** (headful chromium under xvfb, defeated
  Cloudflare): one Persian-Gulf-centred cycle captured 5,323 vessels; filtered to the
  ME region and ingested → `mt_vessel_states` now holds **2,616 MarineTraffic vessels**
  spanning the Persian Gulf (1,282), Red Sea (269), Gulf of Oman (334) and E. Med
  (453) — the map fills the Gulf + Red Sea densely (was empty before). **AIS needs no
  change** — `scraplan-maritime` already subscribes `--bbox
  12 24 42 63.5` (covers the Gulf + Red Sea); the gap there is free terrestrial-AIS
  receiver sparsity, not the box — the real fix is satellite AIS (GFW / paid).
- **Not yet done (next):** the AIS×MarineTraffic cross-view over `vessel_xref`
  (dark-vessel corroboration / spoof cross-check).

## 2026-07-20 — Cross-pattern composite: the IMO-centric intelligence view (migration 026, views only)

The capstone the whole maritime stack was building toward — an AGGREGATION + SURFACE
over the three behavioural detectors (A1 gap, A2 rendezvous, A3 identity_change),
NOT a new detector. It composes the EXISTING events per hull, **stitched by the M2b
IMO spine** across a vessel's broadcast identities, so "went dark AND met another AND
changed identity" reads as ONE picture.

- **Migration 026 — two SQL VIEWS (no table/data/materialization):**
  `maritime_hull_events` normalizes each event to a hull key
  `COALESCE(event.imo, spine.imo, mmsi)` — so a re-flagged hull's events under BOTH
  MMSIs unify under one IMO (an IMO-null vessel falls back to MMSI, honestly not
  stitched across re-flags); a rendezvous is attributed to BOTH participants; only
  the candidate/signal tier is composed. `maritime_hull_composite` GROUP BY hull →
  per-pattern counts, `n_pattern_types`, event span, MMSIs. **Live** (no staleness):
  the aggregation is **~14 ms** over ~1,285 hulls, so a view beats a materialized
  cache — verified live (a new event appears in the view with no rebuild).
- **`backend/composite.py`** (pure, unit-tested) — `composite_rank`: all three
  pattern types → **high**; two types with a grey/sanctions flag or a high
  underlying event → high; two benign types → medium; a lone event carries its own
  rank. Reuses A3's `GREY_FLAGS` (FoC + Russia) — one source of truth, no flag list
  duplicated into SQL (rank is computed in the API layer).
- **`GET /api/v1/maritime/composite`** — hulls ranked by multi-pattern signal, each
  with all its identities, the AOI waters it touched, its rendezvous partners, and a
  time-ordered **timeline** ("went dark 4h in Turkey waters" · "re-flagged
  Cameroon→Russia (SANDHYA→VYCHEGDA)" · "met CAESAR (7 m, 936 min)"). Minimal
  expandable frontend panel. Read-only over the events — no detector/timer.
- **Composite payoff (capstone):** **18 hulls with 2 pattern types, 1 with all 3**.
  The 3-type hull **9895915 (EUROSAILOR→ROSA, Malta)** — gapped, renamed, AND met
  VTS AMMOCHOSTOS. The **Russia shadow-fleet hull 9352195** — re-flagged
  Cameroon→Russia AND went dark, **shown as one stitched picture across its
  disguised identities** (proven: a gap under the Cameroon MMSI unifies under the
  Russia hull's IMO). Tankers that rendezvoused (STS) AND gapped rank high
  (KASTOS I/CAESAR, MAPLE CASTOR/HIGH FREEDOM, BALTIC ICE/NEXUS); ferries/offshore
  are medium.
- **Honest limitation — COMPOUNDS (surfaced):** each underlying event is already
  "lead, not proof"; a composite of leads is a STRONGER lead, still NOT proof —
  legitimate explanations exist (a sold + re-flagged vessel that anchored in sparse
  coverage doing offshore supply could trip all three innocently). Prioritises for
  review; never accuses.
- **Tests +12** (`test_composite.py`: the ranking across 1/2/3 pattern types + grey;
  underlying-rank ordinal; grey membership). IMO-stitching / MMSI-fallback / view
  liveness verified live. Suite green: **356 passed**.
- **Scope:** the composite view only. The anomaly detector NOT built (next, after a
  documentation pause). Underlying maritime_events + the three detectors untouched.

## 2026-07-19 — Behavioural A3: identity-change candidates (the IMO-spine payoff, NO migration)

The third behavioural pattern — a vessel broadcasting a different name/flag/MMSI
over time (potential disguise). **The pattern the M2b IMO spine was built to
enable**, and it needs **no migration**: it reuses `maritime_events`
(event_type='identity_change') and the existing `imo` index — the generalized
table working exactly as designed.

- **`backend/identity_change.py`** (pure, unit-tested) — the noise filter + rank.
  A change is CONFIRMED only when the new value is **sustained** (≥10 rows for
  rename/retype; ≥2 for a re-flag, whose confidence is the IMO+clean-switch),
  **not a near-duplicate** (normalized Levenshtein ≥0.85 drops "EL QAHER I"/"EL
  QAHERI"), and **not garbage/placeholder** (control chars / symbol runs / generic
  MMSIs like 270000000). `identity_suspicion_rank`: grey/FoC/sanctions
  (Russia) re-flag, or correlation with an A1 gap / A2 rendezvous, or multiple
  changes → **high**; non-grey re-flag → medium; plain same-flag rename → low.
- **`scripts/detect_identity_change.py`** — two cheap SQL passes over
  vessel_states (no PostGIS, no O(n²)): **RE-FLAG** = `GROUP BY imo HAVING
  count(DISTINCT mmsi)>1` with a different flag/name and a **clean temporal
  switch** (old ends before new begins — no overlap = not an IMO collision);
  **RENAME/RETYPE** = a confirmed value change under one MMSI. **Correlation**:
  each candidate is joined to A1/A2 events by IMO/MMSI (the intel multiplier).
  Canonical row (mmsi = new/subject, imo = anchor, start_ts = first_new_ts).
  Idempotent recompute-and-replace.
- **`GET /api/v1/maritime/identity-changes`** — OLD→NEW identity, IMO, change_type,
  gap, rank, `correlated_events`; ordered high→low; always the caveat. Minimal
  frontend panel. Timer `deploy/systemd/scraplan-identity.{service,timer}` (every
  30 min, idempotent).
- **Detection results:** **11 candidates** — **7 re-flags**, 2 confirmed renames,
  2 retypes (high 9 / medium 1 / low 1), idempotent. Noise correctly filtered
  (7 raw multi-name MMSIs → 2 renames; blips, the "EL QAHER I"/"EL QAHERI" typo,
  and placeholder 270000000 all dropped). **Shadow-fleet payoff:** the re-flags
  include `SAVITRI`/Sierra Leone → `PASSAT`/Russia, `SANDHYA`/Cameroon →
  `VYCHEGDA`/Russia, `GURU`/Cameroon → `SUKHONA`/Russia, `NARCISSUS`/Panama →
  `MONOLIT`/Russia — the classic grey→Russia re-flag, caught only because the
  **IMO stayed constant** across the MMSI/flag/name switch.
- **Correlation payoff:** hull IMO 9352195 (SANDHYA/Cameroon → VYCHEGDA/Russia)
  shows **9 events joined by IMO** — a re-flag PLUS a candidate AIS gap (it went
  dark AND changed identity). `EUROSAILOR`→`ROSA` renamed AND rendezvoused →
  correlated → HIGH.
- **Honest limitation (surfaced):** renaming / sale / re-flagging is **usually
  LEGAL** — a candidate is a documented change for investigation, NOT proven
  evasion. AIS static is spoofable and error-prone (typos, corruption, IMO
  collisions); ranking prioritises, does not accuse.
- **Tests +30** (`test_identity_change.py`: near-duplicate, garbage, placeholder,
  clean-switch, and the full ranking incl. grey/sanctions/correlation/multiple/
  plain-rename). Suite green: **344 passed**.
- **Scope:** A3 only. The anomaly pattern NOT built.

## 2026-07-19 — Behavioural A2: rendezvous candidates (open-sea meetings, migration 025)

The second behavioural pattern — two vessels loitering together in open sea
(potential ship-to-ship transfer) — reusing the generalized `maritime_events`
table. Value = confound removal **and honest prioritisation**: the naive signal is
~99.9% ports/anchorages, and even clean candidates are mostly legitimate.

- **Port/busy masks** (`backend/coverage_mask.py`, reusing A1's cell binning at
  0.01°≈1km): **port/anchorage** = cells with ≥5 distinct *stationary* (<1kn)
  vessels (268 cells; removes 97% of pairs); **busy/lane** = cells with ≥8
  distinct vessels ever (~3,691); **open-sea floor** = M1 `distance_km` > 10 km.
  A candidate must be outside all three.
- **`backend/rendezvous.py`** (pure, unit-tested): the signature — both ≤3 kn,
  **true pairwise distance ≤500 m** (great-circle, time-matched fixes |Δt|<5min),
  **contiguous ≥1h** (duration ranks confidence). `suspicion_rank` PRIORITISES,
  never accuses: tanker+tanker or flags-of-convenience cargo/tanker → **high**;
  offshore-support/ferry/service (incl. AIS "other" = drillships/PSVs) → **low**;
  else medium.
- **`scripts/detect_rendezvous.py`** — SQL pre-filter `GROUP BY (10-min bucket,
  0.01° cell)` → ~2,164 pair-cells → 118 pairs after port/busy exclusion → exact
  time-matched distance pass → contiguous encounters → **canonical pair row**
  (mmsi = smaller MMSI; partner in `detail`). **No PostGIS, no O(n²); runs in
  ~2 s.** Recompute-and-replace (idempotent).
- **Migration 025** — expression index `((detail->>'partner_mmsi'))` so "events
  involving vessel X" is cheap from EITHER side of the pair. **APPLIED to live DB.**
- **`GET /api/v1/maritime/rendezvous`** — both vessels' identity (spine join on
  `mmsi` and on `partner_mmsi`), min distance, duration, offshore km, AOI waters
  (M1), suspicion rank; ordered high→low then longest; always returns the
  **caveat**. Minimal frontend panel on the maritime tab. Timer
  `deploy/systemd/scraplan-rendezvous.{service,timer}` (every 20 min, idempotent).
- **Detection results:** 118 pairs → 67 encounters (≥1h) → **32 candidates**
  (high 10 / medium 4 / low 18) — refining the assessment's 29. Idempotent
  (re-run 0 change). The prioritisation works: **HIGH = the tanker-STS profiles**
  — `KASTOS I` (Panama) + `CAESAR` (Liberia), two FoC tankers **7 m apart for
  15.6 h off Cyprus**; `HIGH FREEDOM`+`MAPLE CASTOR` (both Liberia tankers), etc.
  — while offshore-energy (drillship+supply) and ferries are the 18 LOW. True
  distances of 2–40 m confirm genuine alongside detection.
- **Honest limitation (surfaced):** a candidate = "two low-speed vessels loitered
  close, away from port, in open sea" — an investigation lead, NOT proof of a
  transfer; most are legitimate offshore work / scheduled traffic. AIS spoofable,
  identity point-in-time. Ranking prioritises, does not accuse.
- **Tests +18** (`test_rendezvous.py`: ranking incl. tanker-STS/FoC/offshore/
  ferry; time-matched distance + skip; encounter close/slow/sustained, too-far/
  too-fast/too-brief/gap-split; mask exclusion). Suite green: **314 passed**.
- **Scope:** A2 only. Identity-change and anomaly NOT built.

## 2026-07-19 — Behavioural A1: AIS-gap candidates (confound-removed, migration 024)

The first BEHAVIOURAL-ANALYSIS pattern and the first use of a generalized events
table. Detects "going dark" **candidates** — but the whole value is CONFOUND
REMOVAL: a raw long gap is not intel. A gap is a `candidate` ONLY after removing
the three confounds the data assessment found.

- **`migration 024 maritime_events`** — ONE generalized table for all behavioural
  patterns (ais_gap now; rendezvous/identity_change/anomaly later), mirroring
  `signal_events`' typed-columns + JSONB shape: `event_type, mmsi, start_ts,
  end_ts, imo, start/end lat/lon, duration_s, classification, method, detail
  (jsonb), computed_at`. **Recompute-and-replace key** PK `(event_type, mmsi,
  start_ts)` → re-detection UPSERTs (replace, never duplicate; open→closed
  updates in place). CHECK on the classification set; indexes on
  `(event_type, classification, start_ts DESC)` and `imo`. **APPLIED to live DB.**
- **`backend/coverage_mask.py`** — data-driven `is_dense(lat,lon)`: bins position
  history into **0.1° (~10 km) cells**, dense = **≥100 positions/cell** (~p85;
  832 cells / 87% of positions). This handles **Cyprus/mid-sea receivers** —
  a cell far from any mainland but full of positions is dense, which naive
  coast-distance would wrongly call an edge. Reusable by later patterns.
- **`backend/gap_detect.py`** (pure, unit-tested) — classify each gap with
  priority **outage-excluded > stationary > edge > candidate**: (a) overlaps a
  `feed_disconnect` window (reconstructed from `signal_events`) → outage-excluded;
  (b) not underway (nav_status≠0 / speed≤1) → stationary; (c) underway but start
  not in dense coverage → edge; (d) else → candidate. Threshold **≥1h** (from the
  assessment's p95≈33m→p99≈4.5h knee).
- **`scripts/detect_ais_gaps.py`** — batch: LAG per mmsi for closed gaps ≥1h,
  join outage windows + coverage mask + nav_status/speed, classify, attach
  identity (imo/name/flag via the M2a/M2b spine) + M1 geo (whose waters), UPSERT.
  Idempotent (`--dry-run`, `--since` window). **Incremental** = a systemd timer
  (`deploy/systemd/scraplan-gaps.{service,timer}`, every 15 min, `Persistent=true`)
  re-running the idempotent recompute — a gap becomes a candidate the first run
  after it closes.
- **`GET /api/v1/maritime/gaps`** — candidates (default), joined to identity + M1,
  ordered by duration (longest first), `min_hours` filter. Always returns the
  **honest-limitation `caveat`**: an investigation lead, NOT proof of intent
  (terrestrial AIS is spoofable, coverage imperfect). Minimal frontend panel on
  the maritime tab surfaces the list with the caveat visible.
- **Detection results** (18,415 closed gaps): **candidate 2,251**, edge 1,777,
  stationary 8,884, outage-excluded 5,503 — i.e. **~78% of raw gaps are removed
  as confounds** (48% stationary, 30% outage). Actionable tail: candidates ≥6h
  476, ≥12h 306, ≥24h 85. (The assessment's naive-<12km proxy gave 451; the
  data-driven mask correctly reclassifies Cyprus/shipping-lane coverage, so the
  candidate count is higher and honest.)
- **Intel payoff** (candidate ≥6h, by duration): PAQIZE (Malta, IMO 9457206) went
  dark **54h in Turkish waters**; SALEH (Egypt, IMO 9893747) 47h in Israeli
  waters; SEA NAVIGATOR (Marshall Is.) 46h in Turkish waters — full identity via
  the spine + whose waters via M1.
- **Honest limitation (surfaced):** we cannot prove intent — a candidate is
  "underway vessel in good coverage stopped reporting ≥1h, not a feed outage".
  The coverage mask conflates traffic with coverage (errs conservative).
- **Tests +11** (`test_gap_detect.py`: mask is-data-driven incl. Cyprus mid-sea;
  outage-window reconstruction/overlap; the four classifications + priority;
  underway rule). Suite green: **296 passed**.
- **Scope:** A1 (ais_gap) only. Rendezvous / identity-change / anomaly NOT built.

## 2026-07-19 — Maritime M2b: AIS-static identity spine (migration 023)

Promotes the already-arriving Class‑A AIS **static** identity into a durable,
queryable identity **spine** on `vessel_identity` (M2a's table). A persistence +
promotion layer — NOT a re‑subscription: the collector already receives and
merges ShipStaticData (type 5); M2b materializes the current‑best identity per
MMSI instead of leaving it scattered across the `vessel_states` time series.

- **Dimensions unlocked:** `maritime/adapters/aisstream_parse.py` now extracts
  the AIS `Dimension` field (A+B→`length_m`, C+D→`beam_m`) — it was in every
  static message and merely dropped. Added to `VesselState` + `merge_static` +
  the Fleet change‑detection. All‑zero dims → NULL (never zero‑filled).
- **Migration 023 — additive on `vessel_identity`:** `name, ship_type,
  ship_type_code, callsign, imo, length_m, beam_m, identity_source
  ('ais-static'), identity_updated_at`. **IMO is the durable cross‑reference**
  (INDEXED, not PK — survives re‑flagging; nullable, ~23% haven't broadcast it).
  **DB guarantees:** CHECK `length_m/beam_m > 0`; the M2a CHECKs + key untouched.
  Voyage state (destination/draught/eta) **deliberately excluded** — it stays on
  `vessel_states`. **APPLIED to the live DB.**
- **Ingest (additive to M1/M2a):** `maritime/db.py::ingest` gained an
  `identities` arg; `_promote_identities` runs after the M2a flag upsert (rows
  guaranteed) and UPDATEs the spine — **COALESCE** keeps known values when a
  later report omits a field, a **WHERE‑changed guard** makes it a no‑op when
  nothing changed (latest‑wins, stable `identity_updated_at`, 0 spurious writes).
  `tracker_maritime.py` passes `identities=positions`.
- **`scripts/backfill_vessel_identity.py`:** latest‑non‑null‑per‑mmsi from
  `vessel_states`, self‑sufficient upsert (derives station/flag via the M2a
  function on INSERT; touches only the identity block on conflict), idempotent.
- **Point‑in‑time caveat (documented):** name/type/callsign are current‑best and
  change on rename/re‑registration; IMO is the stable anchor within the spine.
- **Backfill coverage** (3,458 vessels): name 80.7% · ship_type 80.7% · callsign
  80.4% · **imo 77.1% (2,665 vessels, 2,659 distinct IMO)** · length_m/beam_m
  **0% from backfill** — dimensions were dropped at parse time historically and
  are **unrecoverable** from stored data; they accumulate from ingest going
  forward (the parser now keeps them). Reported, not silently skipped.
- **Analytical payoff (M1 geo × M2a flag × M2b identity):** the grey‑fleet
  vessels in **Syrian territorial waters are now NAMED** — AB AYANA (Sierra
  Leone), ALFA WATAN (Togo), AYSHA M (Comoros), ANNABELLA (Guinea‑Bissau) — old
  cargo ships under obscure flags. Named tankers/cargo in AOI waters lead with
  Panama/Liberia/Turkey.
- **Verified:** ingest lands dimensions (183×27 m) + full stack in one call;
  rename → name updates, IMO stable, dims preserved, `identity_updated_at`
  advances; identical re‑ingest → 0 writes (idempotent); voyage fields absent
  from the spine; `vessel_states` untouched (21 cols, 0 dup keys); M1/M2a
  undisturbed. Tests **+9** (dimension parse/zero/missing, Fleet merge, spine
  includes‑identity/excludes‑voyage, no‑IMO). Suite green: **285 passed**.
- **Scope:** M2b only. M2c (external registry) and the behavioural layer NOT
  built. Class‑B (type 24 / 18 / 19) deferred — Class‑A is the domain.

## 2026-07-19 — Maritime M2a: flag state from MMSI MID (migration 022)

Maritime identity, built in layers like the aircraft layers. M2a derives each
vessel's FLAG STATE (country of registration) DETERMINISTICALLY from its MMSI —
the sea-going analog of the aircraft E1 layer (icao24 → registration country).
NOT an external API and NOT a guess: the leading 3 digits of a ship-station MMSI
are the ITU Maritime Identification Digits (MID) = the flag state.

- **`backend/data/mmsi_mid.json`** — committed static ITU MID asset (ITU Radio
  Regulations Appendix 43 / Rec. ITU-R M.585), **292 assigned MIDs → 236 flags**,
  retrieved 2026-07-19 from the machine-readable consolidation at
  github.com/michaeljfazio/MIDs (cross-checked against Wikipedia, which had
  extraction errors the machine-readable source corrected: missing 211/238,
  wrong 332/529/745/750). NEVER fetched at runtime. AOI flag names normalized to
  match `backend/geo_area.py` (only `Syrian Arab Republic → Syria` differed).
- **`backend/mmsi_flag.py`** — pure `mmsi_to_flag(mmsi) → (country|None,
  station_type)`. Only ship-station MMSIs (first digit 2-7) carry a leading MID →
  flag; other formats are typed HONESTLY and NEVER mis-flagged: coast (00…),
  group (0…), SAR aircraft (111…) + AIS-SART (970…), aid-to-navigation (99…),
  and other (98 craft-assoc / 972 MOB / 974 EPIRB / 8 handheld / invalid).
  Unallocated MID on a ship station → None country (never guessed). Load-once
  asset singleton, fail-fast — mirrors `backend/icao_country.py`.
- **Migration 022 `vessel_identity`** — a DEDICATED derived-facts table keyed on
  mmsi (recommended over extending the photo cache `vessel_info`, mirroring the
  satellite `satellite_info/orbit/strategic` dedicated-table pattern; it is also
  the home for M2b/M2c later). Columns: `flag_country`, `station_type`,
  `flag_source='mmsi-mid'`, `derived_at`. **DB-level guarantees:** CHECK on the
  closed station-type set, and CHECK that a `flag_country` may exist ONLY for a
  ship station. Additive — vessel_states and its key `(mmsi, ts, source)` and
  every other table UNTOUCHED. **APPLIED to the live DB.**
- **Ingest (additive to M1):** `maritime/db.py::ingest` now derives each batch
  MMSI's flag/station locally and upserts `vessel_identity` (ON CONFLICT DO
  NOTHING) inside the same transaction, without disturbing the M1 vessel_states
  write. Verified end-to-end (rolled back): one ingest does both M1 geo and M2a
  flag. Activates on the next `scraplan-maritime.service` restart.
- **`scripts/backfill_vessel_flag.py`** — derives the flag for ALL distinct
  MMSIs (local, no network), idempotent/resumable (deterministic + DO NOTHING;
  re-run inserts only new MMSIs, 0 spurious changes — verified: run 2 inserted 0).
- **Backfill result (3,447 distinct MMSIs):** 3,438 ship (3,432 flagged, 6
  unallocated-MID → None), 9 other. **Top flags:** Panama 373, Liberia 338,
  Turkey 330, Greece 248, Malta 248, Marshall Is. 244 — a textbook Eastern-Med
  mix of convenience + regional flags. **AOI-flagged:** Turkey 330, Cyprus 55,
  Israel 22, Lebanon 16, Jordan 3, Syria 1, Iraq 0.
- **Cross-view (M1 geo × M2a flag)** — vessels currently in each AOI country's
  territorial waters, by flag: **Syrian waters (90 vessels)** are dominated by
  obscure/grey-fleet flags (Comoros 12, St Kitts 12, Togo 10, Palau 8) with NO
  major flag — a real signal; **Turkish waters (1,489)** lead with domestic
  Turkey 287 then Panama/Liberia/Marshall/Malta/Russia.
- **Research-integrity caveat (documented in code + migration):** the MMSI flag
  is CURRENT registration and CHANGES on re-flagging — a point-in-time flag, not
  a permanent identity; IMO (M2b) is the durable hull-linked identifier.
- **Tests:** +43 (`test_mmsi_flag.py`: known MIDs incl. the seven AOI + flags of
  convenience, non-ship station typing, unallocated→None, invalid input,
  determinism, asset well-formedness/fail-fast). Suite green: **276 passed**.
- **Scope:** M2a only. M2b (name/type/IMO from AIS static) and M2c (external
  registry) NOT built.

## 2026-07-19 — Maritime M1: vessel territorial-sea geo classification (migration 021)

The maritime (AIS) layer's missing geo layer — vessel_states was bbox-scoped but
UNCLASSIFIED. Now every vessel position is classified against the SAME seven-country
classifier the aircraft/satellite layers use (`backend/geo_area.py`), ADAPTED for
the sea rather than copied.

- **Maritime semantics (why not the aircraft model):** a ship is essentially never
  INSIDE a country polygon, so the aircraft INSIDE / NEAR(50 km) / OUTSIDE model
  does not transfer. The meaningful question is "is the vessel in a country's
  **TERRITORIAL SEA**?" — UNCLOS Art. 3 sets that at **12 NM = 22.224 km** from the
  coast. That legal, citable limit is the maritime band, NOT the aircraft 50 km land
  buffer (which would flag nearly every coastal vessel and lose meaning). Configurable
  via `SCRAPLAN_TERRITORIAL_SEA_KM` (`backend.geo_area.territorial_sea_km`).
- **Reuse, not a second implementation:** the 12 NM test uses geo_area's EXISTING
  AEQD projected `geom_m.distance` machinery (factored into a shared `_nearest`
  helper used by both the aircraft NEAR test and the new maritime test). New:
  `classify_maritime_columns()`, `nearest_country_km()`, `MaritimeGeoColumns`.
- **Columns (migration 021, additive on vessel_states):** `country` = territorial-sea
  country when ≤12 NM (the sea analogue of aircraft INSIDE), else NULL; `near_country`
  = nearest of the seven when OUTSIDE every territorial sea (the analogue of aircraft
  NEAR) — so `country`/`near_country` are MUTUALLY EXCLUSIVE per positioned row;
  `distance_km` = **TRUE km to the nearest coast** (deliberately DIVERGING from the
  aircraft envelope-centre provenance distance — for a vessel, distance-to-coast is
  the analytically meaningful value and is exactly what the 12 NM test computes from);
  `geo_source` = `'scraplan-aoi'` (classifier identity, distinct from the feed
  `source`). Aircraft-only `position_quality`/`validation_flags` INTENTIONALLY OMITTED
  (do not map to AIS). NULLABLE, no default; natural key `(mmsi, ts, source)`,
  hypertable, compression and policy all UNTOUCHED. **APPLIED to the live DB.**
- **Egypt note (surfaced, not hidden):** the reused asset is the SEVEN-country AOI
  (Syria, Turkey, Iraq, Jordan, Lebanon, Israel, Cyprus) — **Egypt is not in it**, so a
  vessel off Alexandria classifies OUTSIDE (nearest of the seven = Israel, ~410 km).
  This is the honest consequence of the mandated reuse, not a bug.
- **Ingest classification:** `maritime/db.py::vessel_row` now classifies every
  positioned row at write time (live mirror + backfill share the one path). Activates
  on the next `scraplan-maritime.service` restart (running collector holds old code;
  the archive-first + backfill-covers-gap design means no data is lost meanwhile).
- **`scripts/backfill_vessel_geo.py`** — one-off historical backfill, same
  scoped-per-chunk discipline as `backfill_geo.py` (decompress → literal-bounds
  scoped UPDATE → recompress; idempotent/resumable via `geo_source IS DISTINCT FROM
  'scraplan-aoi'`). **Maritime-specific fix:** ts is carried as `timestamptz`
  end-to-end, NOT via `extract(epoch)::bigint` — AIS fix times have sub-second
  precision (every row), which the integer-epoch round-trip used by the aircraft
  backfill would truncate, matching zero rows.
- **Backfill result:** 615,967 positioned rows classified in **56 s** (~11k rows/s),
  0 remaining; compressed chunk decompressed→updated→recompressed, posture restored
  (1 compressed). Idempotent: a stable historical chunk's geo-hash is byte-identical
  across re-runs. **Coverage:** 45.9% in territorial waters (Turkey 161,998 · Israel
  59,038 · Cyprus 43,309 · Lebanon 13,111 · Syria 4,631 · Jordan 390 · Iraq 1),
  54.1% OUTSIDE (nearest coast recorded). Invariants verified: never both/neither set,
  `country⇔distance≤22.224`, 0 duplicate keys.
- **Tests:** +19 (17 classifier: territorial waters per country, open-sea/Egypt
  OUTSIDE, real-km boundary, mutual-exclusivity, determinism, aircraft-distance
  unchanged; 2 vessel_row geo). Suite green: **233 passed**.
- **Scope:** M1 only. M2 (identity enrichment) and M3 (GFW second source) NOT built.

## 2026-07-19 — Sat-Passes: AOI ground-track passes (SGP4, migration 020)

The REGIONAL CAPSTONE — which tracked satellites pass over the seven-country AOI
and when, computed from current TLEs (python-sgp4) and joinable to every prior
layer by norad_id. This is the query the whole base was built for: "which MILITARY
satellites overfly the region in the next 24 h."

- **Pass definition (documented, observer-independent):** a contiguous interval
  where the satellite's SUB-SATELLITE GROUND-TRACK POINT is INSIDE a seven-country
  polygon OR NEAR it (geo_area's 50 km buffer — the SAME classifier every geo
  layer uses). Sub-point = SGP4 TEME → ECEF (GMST) → WGS-84 geodetic; validated
  against skyfield to < 0.001°. Time-step 60 s over a forward 24 h window
  (configurable). Chosen over a visibility/elevation definition because it is
  observer-independent and reproducible.
- **GEO special case (documented):** a geosync sub-point is ~equatorial, never
  directly over the northern AOI, so the polygon test is inapplicable. Instead a
  GEO is flagged "stationed over the AOI longitude band" (a point at the AOI's
  central latitude and the satellite's stationing longitude, classified by
  geo_area) and emits AT MOST ONE row (is_persistent=TRUE) — never per-step spam.
- **`backend/passes_derive.py`** — pure gmst/teme_to_geodetic + `classify_passes`
  (contiguous-run grouping for non-GEO, one persistent row for GEO). No I/O.
- **Migration 020 `satellite_passes`** — FORWARD-WINDOW CACHE (recomputed +
  REPLACED each run, DELETE+INSERT), PK (norad_id, start_time); per-pass countries
  (TEXT[] + GIN index), near_countries, closest_km, is_persistent, duration; join-
  ready to identity/strategic/orbit by norad_id. Provenance source='derived-sgp4',
  tle_epoch, window_start/end, computed_at. Additive; all other satellite tables +
  keys UNTOUCHED. **ALREADY APPLIED to the live DB** (during the build).
- **`scripts/backfill_passes.py`** — ONE batched SatrecArray propagation over the
  whole time grid + vectorized numpy sub-points + a numpy bounding-box prefilter
  (drops satellites never near the AOI) so the shapely classify runs only on
  candidates. Runtime: **~1.3 s** for 781 satellites × 1441 samples (24 h/60 s).
- **Auto-recompute wiring:** `scraplan-satellites.service` gains a second
  ExecStartPost (after the orbit recompute) so passes refresh every TLE cycle:
  tracker_satellites → backfill_orbit → backfill_passes. Idempotent for the same
  TLEs + window (the forward window slides with wall-clock by design).
- **Capstone results (24 h window):** 176 satellites overfly the AOI (355 passes,
  31 GEO-persistent). By UCS user: Government 47, Commercial 22, Military-involved
  21, plus 76 post-2023 (no UCS). Military-involved by country: USA 12, France 2,
  Russia 2, Germany 1, France/Italy 1, Egypt 1, Canada 1. Sample all-layers row:
  NAVSTAR 56 (USA 180) — SATCAT id + UCS Military/Commercial GPS + live MEO orbit
  + a pass over Cyprus & Turkey. Suite 214 passed (+8 tests).

## 2026-07-18 — Sat-Orbit: deterministic orbit classification (SGP4, migration 019)

The LIVE orbital layer — orbit class + derived elements COMPUTED from the TLEs we
already hold, via the reference SGP4 implementation (python-sgp4). Always current
(recomputed from the latest TLE), the live complement to UCS's frozen 2023 orbit
fields — a study can keep BOTH and compare.

- **`backend/orbit_derive.py`** — pure `tle_to_orbit(line1, line2)` (no I/O):
  SGP4 (WGS-72) → apogee_km, perigee_km, eccentricity, inclination_deg,
  period_min, mean_motion, semimajor_axis_km, plus flags + tle_epoch. Invalid /
  decayed / unparseable elements return None (skipped with a logged reason —
  never a garbage row). Alpha-5 6-digit ids derive fine (SGP4 is satnum-agnostic;
  reuses the S2c parse_3le fix upstream).
- **Documented, citable thresholds** (evaluated in order): HEO if ecc ≥ 0.25;
  else LEO if apogee < 2000 km; else GEO if |mean altitude − 35786 km| ≤ 1500 km;
  else MEO if 2000 ≤ mean altitude < 34286 km; else 'other'. Flags:
  is_geostationary = GEO + inclination ≤ 1° + ecc ≤ 0.01 + period within 30 min
  of one sidereal day (1436.07 min); is_sun_synchronous computed ONLY for LEO
  (nodal precession matching the mean-solar rate 0.98565°/day within 1° of the
  required inclination) — NULL for non-LEO (not applicable, not guessed).
- **Migration 019 `satellite_orbit`** — CACHE of the latest derivation, PK
  norad_id; a refreshed TLE (new epoch) recomputes and UPDATES the row (unlike
  the append-only history). Provenance source='derived-sgp4', tle_epoch (the
  source TLE's epoch), computed_at. Additive; all other satellite tables + keys
  UNTOUCHED, no FK. **ALREADY APPLIED to the live DB** (during the build).
- **`scripts/backfill_orbit.py`** — derives from the freshest TLE per object in
  satellite_elements; idempotent (same TLE → 0 change; new epoch → update).
- **Auto-recompute wiring:** `scraplan-satellites.service` gains an
  `ExecStartPost` that runs `backfill_orbit.py` immediately after each successful
  CelesTrak TLE refresh — so the derived layer is genuinely always-current, never
  stale until a manual run. Same-invocation (no scheduling gap), DB-only, power-
  loss resilience inherited from the timer (Persistent=true). Verified in situ:
  a service run logged the refresh then the recompute, advancing tle_epoch.
- **Coverage/distribution (781 derived, 0 skipped):** GEO 570, LEO 134, MEO 64,
  HEO 12, other 1. Cross-check vs UCS-2023: strong agreement (GEO 477, LEO 65,
  MEO 52, HEO≈Elliptical 9); meaningful divergences the live layer catches —
  e.g. GALILEO 5/6 (the mis-launched-then-raised sats) are UCS-2023 'Elliptical'
  but live-derived MEO.
- **Deps:** `sgp4>=2.23` added to requirements.txt. Suite 206 passed (+9 tests).

## 2026-07-18 — Sat-UCS: strategic classification (UCS DB, migration 018)

Add the STRATEGIC axis (user class / purpose / operator) the identity layers lack,
from the UCS (Union of Concerned Scientists) Satellite Database — turning the
satellite layer into a multi-layer RESEARCH base.

- **VINTAGE — read this before using the layer:** UCS is FROZEN at **2023-05-01**
  (the project paused updates). This layer is for STRATEGIC/STRUCTURAL analysis
  and a historical baseline — it is **NOT a current-state source**. Every row
  carries `source='ucs'` + `ucs_snapshot_date` so any study can cite "strategic
  classification as of 2023-05-01" and never silently mix vintages. Post-2023
  objects are unmatched by design (unclassified, not guessed).
- **Migration 018 `satellite_strategic`** — a SEPARATE table (not columns on
  satellite_info), deliberately: UCS's 2023 vintage must not be mixed into
  satellite_info's 2026 SATCAT identity. PK `norad_id`. Fields: users, purpose,
  detailed_purpose, operator, operator_country, registry_country, orbit_class,
  orbit_type, launch_mass_kg, power_watts, expected_lifetime_yrs, contractor,
  launch_site + provenance (source, ucs_snapshot_date, joined_via). Additive;
  satellite_info / elements / history / reentry UNTOUCHED, no FK.
- **`scripts/backfill_from_ucs.py`** — offline bulk from the gitignored UCS
  tab-delimited file (ISO-8859, 7560 rows). JOIN: NORAD Number direct
  (joined_via='norad'), else COSPAR Number → international designator via
  satellite_info (joined_via='intldes'); an object absent from UCS stays
  unclassified. Idempotent + blank-safe (COALESCE + change-guard).
- **Join coverage (honest):** of 908 catalogued objects, **703 matched** (701 via
  NORAD, 2 via INTLDES), **205 unmatched** (post-2023 objects UCS never saw).
- **Research payoff:** tracked satellites are now classifiable by user
  (Commercial 385 / Government 159 / Military 70 + military-combos 65 / Civil 4),
  purpose (Communications 489, Navigation 73, Earth Observation 68 …), and
  operator-country (USA 274, China 85, ESA 37, Russia 34 …); cross-tab e.g.
  military-involved by country (USA 80, China 21, Russia 7, UK 5, Israel 3 …).
- **Verification:** 10 new pure-function tests green; idempotent (2nd run 0
  changed); satellite_info/elements/history/reentry counts + keys unchanged.

## 2026-07-18 — S2c: reentry/decay (TIP) ingest, geo-linked (migration 017)

Ingest Space-Track reentry predictions and link them to scraplan's seven-country
AOI — the regionally-relevant half of the Space-Track work (S2b conjunctions
deferred). Builds on S2a's authenticated client; reuses geo_area.py and S1.

- **Alpha-5 fix (prerequisite):** `tracker_satellites.parse_3le` now decodes the
  catalog field via `alpha5_to_int` (S2a helper) instead of `int(l1[2:7])`, so a
  6-digit id (catalog > 99999, Alpha-5 in fixed-width TLE since 2026-07-11)
  round-trips instead of being silently dropped. The S2a strict-xfail is now a
  passing test. Only `parse_3le` was affected; behavior is identical for the
  current <99999 tracked groups.
- **Migration 017 `satellite_reentry`** — additive table, PK = TIP message `id`
  (append-only: each TIP message is a row, so the refinement history is kept, like
  satellite_history preserves each epoch; a refined prediction is a new message =
  new row; re-fetch is idempotent via ON CONFLICT (id) + change-guard). Carries
  the TIP fields (decay_epoch, window_min, lat/lon, direction, incl, next_report,
  high_interest) + geo columns from the shared classifier + provenance
  (source='spacetrack_tip'). ALREADY APPLIED to the live DB (applied during the
  verified S2c build) — same discipline as prior migrations.
- **`tracker_reentry.py`** — daily collector: S2a client pulls the `tip` class
  (the geo-bearing reentry class; `decay` has no lat/lon), writes
  satellite_output/reentry.jsonl FIRST (ground truth), parses, geo-classifies the
  NOMINAL predicted point with `backend/geo_area.py` (seven-country INSIDE/NEAR +
  50 km buffer — the same classifier aircraft use), and mirrors idempotently.
  LON normalized 0..360→[-180,180]; window_min/next_report preserved so the
  large uncertainty is visible, not hidden. `backfill` rebuilds from JSONL. Runs
  at most ONCE DAILY (tip/decay are ~1/day classes); the S2a client owns auth,
  session reuse, rate limiting, and backoff.
- **S1 widened:** the SATCAT backfill needed-set now UNIONs `satellite_reentry`
  (guarded for DBs predating mig 017), so reentry objects (decaying debris not in
  the curated tracked groups) gain SATCAT identity and the reentry→identity join
  is real (verified 125/125 enriched). satellite_info grew 783→908.
- **`GET /api/v1/reentry`** (`?region=all|aoi`) — latest prediction per object,
  joined to S1 identity, soonest-first. region='aoi' is the seven-country watch
  list (each object's latest IN-REGION prediction; the append history makes this
  answerable even after a later message moves outside).
- **Verification:** live pull 808 TIP messages / 125 objects, INSIDE=3 NEAR=2
  (message level); AOI watch-list 3 objects with identity (STARLINK-1911/6063
  INSIDE Syria, STARLINK-11462 NEAR Turkey). Idempotent (2nd pull 0 changed).
  Session reused (1 login, 0 throttles). Suite 187 passed (+11 reentry tests,
  Alpha-5 xfail→pass).

## 2026-07-18 — S1: SATCAT identity enrichment (satellite_info, migration 016)

Bring the satellite layer up to the aircraft standard: identity enrichment keyed
on the NORAD catalog id, the orbital equivalent of the aircraft
aircraftDatabase.csv layer (migration 014 / `scripts/backfill_from_opensky_csv.py`).
Same discipline, keyed on `norad_id` instead of `icao24`.

- **Migration 016 `satellite_info`** — additive cache table, PK `norad_id`;
  identity (`international_designator`, `object_name`, `object_type`, `owner`,
  `owner_name`, `launch_date`, `launch_site`, `decay_date`, `ops_status`,
  `rcs_m2`) + per-row provenance (`source`, `satcat_snapshot_date`,
  `source_updated_at`, `fetched_at`). `satellite_elements`/`satellite_history`
  and their keys are UNTOUCHED (no FK; coverage reconciled by the backfill).
  **NOTE: migration 016 is ALREADY APPLIED to the live DB** (applied during the
  verified S1 build, before this commit) — same reality as the aircraft P0
  geo-classification migration; this commit brings git in line with the DB.
- **Source: CelesTrak SATCAT** (`https://celestrak.org/pub/satcat.csv`) — the
  same keyless provider as the TLE collector. Verified against the LIVE header
  (2026-07-17): `OBJECT_NAME,OBJECT_ID,NORAD_CAT_ID,OBJECT_TYPE,OPS_STATUS_CODE,
  OWNER,LAUNCH_DATE,LAUNCH_SITE,DECAY_DATE,…,RCS,…`. The dump is fetched to the
  gitignored `enrichment_data/` (never committed); the HTTP Last-Modified date is
  recorded as `satcat_snapshot_date`.
- **`scripts/backfill_from_satcat.py`** — streams the ~70k-row catalog, keeps only
  rows whose `norad_id` is tracked (`satellite_elements ∪ satellite_history`), and
  writes an idempotent UPSERT. `COALESCE(EXCLUDED, existing)` on every fact means
  a blank SATCAT field NEVER nulls a stored value; a `DO UPDATE … WHERE`
  change-guard makes a same-snapshot re-run a 0-row no-op (source_updated_at
  bumped only on a real change) — resumable after power loss. Provenance
  `source='celestrak_satcat'`.
- **`backend/data/satcat_sources.json`** — 132 authoritative CelesTrak owner-code
  → country/organization names (scraped once from CelesTrak's published sources
  table), so `owner` (raw code) gets a decoded `owner_name`; unknown codes stay
  NULL (never guessed). Mirrors the repo's static-reference pattern
  (`icao24_allocations.json`).
- **`GET /api/v1/satellite/{norad_id}`** (`SatelliteEnrichment`) — a pure local
  read from `satellite_info`; `available=false` when a norad has no SATCAT row;
  never fetches upstream, never errors. Mirrors `/aircraft/{icao24}` and
  `/vessel/{mmsi}`. The FROZEN `/satellites` contract is untouched.
- **Verification (783 tracked objects):** 783/783 matched SATCAT (0 unmatched);
  coverage 0%→100% owner/owner_name/launch_date/object_type, 53.5% RCS (RCS is
  genuinely sparse in SATCAT). Idempotent double-run = 0 rows changed;
  `satellite_elements`/`satellite_history` counts + PKs unchanged; blank-never-
  overwrites proven with the production UPSERT in a rolled-back transaction; 13
  new pure-function tests (`tests/test_satcat.py`) green.

## 2026-07-15 — P4-A: operational systemd services (power-loss resilient)

Make the full ADS-B pipeline run unattended and auto-recover after reboot /
power loss (the ThinkCentre is power-loss prone) — the central P4 requirement.

- `adsb-scraper-collector.service` (new) — drives the EXTERNAL adsb_scraper
  project (`python -m adsb_scraper --output $ADSB_OUTPUT`), Restart=always,
  After/Wants=network-online. Kept in scraplan's deploy tree but clearly labelled
  as the external collector; scraplan neither imports nor modifies adsb_scraper.
  Restart safety is the archive's: adsb_scraper's JsonlSink fsyncs each flush
  and truncates a trailing partial line on open, so a hard kill resumes writing
  valid JSONL with NO manual cleanup. Headless by default (the only mode a
  boot-time service can use); headed alternatives for Cloudflare (user service
  under the graphical session, or `xvfb-run --no-headless`) documented in the
  unit + runbook.
- `scraplan-adsb-ingest.service` — hardened to Restart=always (was on-failure),
  After=network-online + the collector; the DB dependency stays the ExecStartPre
  wait (the DB may be a container/remote). The --state-dir offset persists under
  scraplan and survives reboot; on restart `follow` resumes from it and
  re-ingests nothing (idempotent natural key as the backstop).
- `deploy/systemd/OPERATIONS.md` (new) — runbook: enable/start/stop/status, the
  power-loss recovery model (archive = ground truth; ingest idempotent + offset
  persistence; DB derived/rebuildable), and recovery-verification commands.
- Verification: `systemd-analyze verify` clean on both units; simulated
  power-loss (ingest, hard-kill, restart) resumed from the persisted offset
  (EOF) with 0 re-ingested rows, and a forced full re-scan re-ingested 0
  (idempotent).

## 2026-07-15 — P4-B: adsbexchange UI source integration (+ /adsb overlay)

Make the adsbexchange feed visible and filterable in the UI, matching how
opensky/airplanes_live are presented; surface the P3 geo classification.

- New DB-backed overlay endpoint `GET /api/v1/adsb` (mirrors `/military`, but
  the source has no latest.json — it lives in the DB). Serves the latest
  adsbexchange snapshot BOUNDED to the AOI (INSIDE a country or NEAR a border)
  via the P3 geo columns, so a globally-configured feed still shows only
  relevant traffic and every row carries country/near_country/position_quality.
  `AdsbResponse` model; `available=false` when stale (>120 s) — never a 500.
- `STATE_COLUMNS` now selects the geo columns (additive; NULL for pre-backfill
  opensky/airplanes_live); `states_at(aoi_only=)` + `latest_source_snapshot()`
  added. The `source` query param on /history,/snapshots,/replay,/stats widened
  to accept `adsbexchange` so its trails/replay/stats work.
- Frontend: an ADS-B toggle beside the military one (live view), a distinct
  ORANGE glyph (`--glyph-adsb`) + live counter, an `adsb-tag` in the contact
  list, and the overlay merged into the map/list after filters (dedup: opensky
  > military > adsbexchange). The FlightStrip surfaces country / near-border /
  position-quality when the selected row carries them. Frozen contract kept
  (geo fields optional/additive). `tsc -b && vite build` passes.

## 2026-07-15 — P3: source-neutral geo classification for ALL feeds

Geo classification is now a property of POSITION, computed by scraplan for
every source at ingest — not a value only adsbexchange rows carried. A query
like "aircraft near the Syrian border" no longer silently misses opensky /
airplanes_live observations.

- `backend/geo_area.py`: ONE classifier, lifted near-verbatim from
  adsb_scraper/area.py (seven countries + a true kilometric AEQD border
  buffer, prepared polygons, load-once + fail-fast). Committed asset
  `backend/data/aoi_countries.geojson` (never fetched at runtime). No PostGIS.
  `classify(lat, lon)` reads ONLY degrees — scraplan's metric altitude/velocity
  never enter it. buffer_km (default 50) and asset path are env-configurable
  (`SCRAPLAN_AOI_BUFFER_KM`, `SCRAPLAN_AOI_ASSET`).
- Wired into `parse_record`, so EVERY ingest path (opensky/military collectors,
  adsb follow/backfill) classifies from the row's own lat/lon: country,
  near_country, position_quality (no_position vs confirmed), distance_km, and
  geo_source=`scraplan-aoi`. adsbexchange rows are RE-classified (single source
  of truth = scraplan's asset), so feeds never disagree at the same point. An
  incoming `suspect` is preserved (a kinematic verdict the producing feed made
  in its native knots/feet; scraplan does not recompute it). validation_flags
  are left as the source supplied them.
- Deterministic → idempotent: same row classifies identically, natural key
  `(icao24, ts, source)` and ON CONFLICT DO NOTHING untouched. shapely+pyproj
  added to requirements (ingestion/collector processes only; the read-only API
  server does not import the classifier).
- Verification (live stack): full suite 92 passed (18 new/updated). Mixed live
  batch: opensky and adsbexchange rows at Damascus both → country Syria,
  confirmed, geo_source scraplan-aoi, identical distance; an adsb row's stale
  incoming country="Iraq"/geo_source="adsbexchange" were overridden; a
  no-position row → no_position with NULL country/near/distance; a mid-Atlantic
  opensky row → OUTSIDE (country NULL). Re-insert → 0 new rows, 0 duplicate
  keys. Historical backfill of the 2.5M pre-P3 rows is a SEPARATE, unapproved
  step (see README) — P3 only makes NEW ingested rows uniformly classified.

## 2026-07-15 — P2: ADS-B Exchange consumer (adsb_scraper archive → DB)

adsb_scraper (external project) is now a third aircraft feed
(`source=adsbexchange`) consumed WITHOUT a parallel insert path — through the
existing idempotent ingest (`parse_record` + `INSERT_SQL`, natural key
`(icao24, ts, source)`). Builds on P0's migration 012.

- `adsbexchange` is a registered source (`ARCHIVE_PREFIX` → `state_vectors*.jsonl`).
  adsb_scraper's collector is external and cannot mirror to our DB (opensky/
  military do via `--db-url`), so the feed uses scraplan's already-shipped
  consumer `ingest.py follow`/`backfill` — same tailer, crash-repair (trailing
  partial line left for the next poll), byte-offset persistence. `TailState`
  gained `--state-dir` so scraplan keeps its offset under its own tree, never
  inside the foreign output dir. New unit `scraplan-adsb-ingest.service` runs
  the follower. adsbexchange is the FIRST feed that fills the six geo columns;
  the others stay NULL.
- Verification (live stack): full suite 74 passed (8 new P2 tests). Backfill of
  the real 61,568-line archive → 61,568 inserted, 0 invalid, 0 duplicates;
  sample rows show `validation_flags` as native `text[]` (`{outside_area}`,
  `{implausible_kinematics,outside_area}`), `geo_source`=adsbexchange,
  `position_quality` confirmed/suspect. Second backfill of the same file → 0
  newly inserted, 61,568 duplicates skipped (idempotency). Zero duplicate
  `(icao24, ts, source)`; opensky/airplanes_live keep 0 non-NULL geo columns;
  follow-mode consumed the whole file (offset = file size) and wrote its state
  under scraplan, not adsb.

## 2026-07-15 — P0: migration 012, geo-classification columns (additive)

Six NULLABLE geo-classification/validation columns on `state_vectors` —
`country`, `near_country`, `position_quality`, `validation_flags TEXT[]`,
`distance_km`, `geo_source`. Explicit columns, no JSONB, no PostGIS. PK +
`ts=to_timestamp(snapshot_time)` CHECK untouched; legacy rows and opensky/
airplanes_live read them back NULL. `parse_record`/`INSERT_SQL`/`StateVector`
extended to CARRY them (NULL when absent) — prep for the adsbexchange feed (P2)
that populates them.

## 2026-07-13 — comprehensive archival ("leave no signal"), unified

Goal: archive every data point AND operational signal across all three layers,
via one principled, extensible model — not per-source hacks. Delivered in 4
committed phases (S1–S4).

- **Principle (now universal):** append-only raw JSONL logs = ground truth
  (rotated daily + gz, never dropped); the DB = derived, source-keyed,
  rebuildable hypertables, compressed old, KEPT FOREVER; every table
  rebuildable from its raw log via idempotent backfill; one shared signal
  emitter for all collectors.
- **S1 `archive.py` + migration 010:** `RawLog` (rotation-safe append) +
  `emit_signal` (raw log always + `signal_events` hypertable best-effort).
  `signal_events` (JSONB detail, source+kind segmented compression, no
  retention) unifies feed connect/disconnect, AIS gaps, upstream throttles,
  mirror failures, collector runs.
- **S2 migration 011 `satellite_history`:** satellites were current-only;
  now every fetched TLE is appended (key `(norad_id, epoch, grp)`) + raw
  `tle.jsonl`; `tracker_satellites.py backfill` rebuilds it. Enables
  past-instant orbit replay later.
- **S3 maritime full capture:** Fleet now buffers EVERY new-information AIS
  signal (position moved / static changed; identical repeats dropped) →
  raw `ais-signals.jsonl` (position + static) + `vessel_states` (positions).
  AIS feed connect/disconnect route through `emit_signal` (replaced the
  standalone `ais_gaps.jsonl`).
- **S4 universalization:** `rotate_archive.sh` rotates every raw log across
  all output dirs; compression confirmed on all 4 data hypertables; the ONLY
  retention job is TimescaleDB's internal job-stat history (1 month) — no
  data is ever dropped. Nightly `pg_dump` covers all new tables.
- Disk: 103 GB free, DB ~0.6 GB after 5 days — sustainable for years with
  compression. 63 tests green.

## 2026-07-11 — maritime DB integration (migration 007)

- `vessel_states` hypertable + `vessel_snapshots` table (migration 007),
  natural key `(mmsi, ts, source)` — same source-in-key pattern as 006.
  `ts` = per-vessel position-fix time (AIS is an event stream, not shared
  snapshots). speed kept in KNOTS (AIS-native, not normalized to m/s).
  Compression policy `segmentby (mmsi, source)`, 7-day threshold.
- `maritime/db.py`: `VesselIngester` + `vessel_row`/`parse_lines` +
  `backfill_vessels` (dir/gz aware) — mirrors ingest.py's idempotent path.
- `tracker_maritime.py`: `--db-url` live mirror (archive-first,
  failure-tolerant — identical contract to the aviation collector) and a
  `backfill` subcommand. `scraplan-maritime.service` now passes
  `--db-url ${DATABASE_URL}` + DB-wait ExecStartPre.
- Verified live: backfill 1303→0 on re-run (idempotent); live mirror grows
  vessel_states (653+ vessels) and writes vessel_snapshots (~435/snapshot)
  with zero mirror failures. 54 tests green (4 new DB-row tests).
- vessel_snapshots count comes from the collector's live fleet size (the
  archive stores only the moved-vessel delta), documented in the migration.

## 2026-07-09 — unified multi-source archive (migration 006)

- **Schema**: `state_vectors` natural key is now `(icao24, ts, source)` and
  gains nullable military attributes (`actype`, `acdesc`, `reg`); `snapshots`
  keyed `(snapshot_time, source)`; the 1-minute continuous aggregate is
  source-aware (`GROUP BY bucket, source`). A same-(icao24, second) collision
  between two feeds is now structurally impossible, not a silent race.
- **Rebuild, not surgery**: 006 drops+recreates the derived index layer
  rather than altering a live compressed hypertable's PK — safe because the
  JSONL archive is ground truth. Re-backfilled 505,083 OpenSky + 783 military
  rows; `/live` served from latest.json throughout.
- **Ingestion**: `ingest.py` gains `--source` (records self-describe via a
  `source` key; OpenSky records default to `opensky`); snapshot upsert and
  completeness are scoped per source. `tracker_airplanes.py` gains a
  failure-tolerant `--db-url` live mirror (source=airplanes_live).
- **API (additive)**: `/history`, `/replay`, `/snapshots`, `/stats` take an
  optional `source` defaulting to `opensky` — frozen contract preserved;
  `source=airplanes_live` unlocks archived military trails/replay/stats.
- Verified: idempotent re-backfill, EXPLAIN uses the `(icao24, source)` PK
  index, `/stats` numbers unchanged by default, military `/history` returns
  real trails, 37 pytest green.

## 2026-07-09 — hardening plan (phases A–D)

- **A**: systemd user units (server/collector, restart-on-failure, linger)
  — kill-test verified; nightly pg_dump backups (14 d retention).
- **B**: daily archive rotation (gzip; collector recreates the file on next
  flush — verified live); `ingest.py backfill` reads directories + rotated
  .gz (recovered 9,129 rows lost during a session gap); TimescaleDB
  compression on chunks >7 d (44→19 MB on first chunk); ERD artifact
  redeployed for migrations 001–005.
- **C** (frontend): emergency-squawk alerts, `?icao=` deep links,
  display-only altitude plausibility cap (60k ft), replay speeds ×1/×2/×5.
- **D**: pytest suite — 31 tests (unit: parse_record, SQL splitter, time
  guards, enrich parsers on probed fixtures; integration: contract shapes,
  auto-skip when the stack is down). Public-exposure recipe documented,
  deliberately unbuilt (LAN-only decision).

## 2026-07-08 — aircraft enrichment (photo + standing route)

- New additive endpoint `GET /api/v1/aircraft/{icao24}?callsign=` (see
  README § API Contract): airframe/operator from adsbdb, photo from
  planespotters (attribution included), standing route by callsign.
- `backend/enrich.py`: cache-first against `aircraft_info`/`flight_routes`
  (migration 004), read-time TTLs (30 d / 7 d, negative 1 d), per-key
  single-flight locks, upstream failure → stale-if-any else nulls without
  caching the failure. The browser never talks to the external hosts.
- planespotters requires a descriptive User-Agent with contact
  (`ENRICH_CONTACT_UA` env, defaults to the project contact) — generic UAs
  get 403 (verified live).
- Verified: first call ~4 s upstream, repeat 9 ms from cache; unknown hex →
  200 all-nulls; route PGT972 = EDDS/STR → LTFJ/SAW with coordinates.

## 2026-07-08 — Backend v1 (Phases 0–4)

### Decisions

- **Migration tool:** plain SQL files in `migrations/`, applied in lexical
  order by `migrate.py`, tracked in `schema_migrations` with sha256
  checksums (drift = hard error). Chosen over Alembic because the schema is
  SQL-first (hypertable, continuous aggregate, CHECK constraints) and there
  is no ORM layer to keep in sync. `-- migrate:no-transaction` directive
  supports statements PostgreSQL forbids inside transactions.
- **DB driver:** `asyncpg` directly (no SQLAlchemy). Small fixed query
  surface, typed records, LISTEN/NOTIFY support needed for the WS push.
- **Natural-key enforcement:** `PRIMARY KEY (icao24, ts)` on the hypertable
  plus `CHECK (ts = to_timestamp(snapshot_time))` — TimescaleDB requires the
  partition column in unique indexes, and the CHECK pins the ts ↔
  snapshot_time bijection, so the PK is exactly the natural key
  `(icao24, snapshot_time)` demanded by the spec. All inserts are
  `ON CONFLICT DO NOTHING`; no application-level dedup anywhere.
- **Live ingestion path (Phase 2):** initially implemented as a separate
  DB-writer (`ingest.py follow` tailing the JSONL); **revised on user
  decision to option 1 — a flag-gated mirror inside the collector**
  (`--db-url` / `$OPENSKY_DB_URL`, off by default). The extension is one
  `DatabaseSink` class + one call after the archive writes; it lazy-imports
  `ingest.py`, reusing the exact same idempotent ingestion path as backfill.
  Polling logic untouched; DB failures are logged and swallowed (archiving
  never stops); gap coverage = `ingest.py backfill`. A structural bonus over
  the tailer: the mirror writes each snapshot whole, in one transaction,
  marked `complete` immediately — no partial-snapshot window on the live
  path, and WS pushes land ~1s after collection instead of one JSONL-flush
  cycle later. `follow` mode is kept as an optional alternative writer for
  DB-agnostic-collector setups.
- **WS push wiring:** the ingestion path (collector sink or follow mode)
  fires `NOTIFY new_snapshot` after each committed snapshot; a single
  Broadcaster task per API process holds a LISTEN connection (15s watchdog
  as missed-NOTIFY fallback) and fans out to per-client bounded queues
  (size 2, drop-oldest — slow clients never stall anyone). Backfill
  deliberately does NOT notify (a historical load must not flood live
  clients).
- **snapshots.count** is always recomputed from stored `state_vectors` rows
  in the same transaction — never trusted from the writer — so partial
  batches and re-runs converge to the truth.
- **bbox** is nullable on `snapshots`: raw JSONL rows don't carry it; it is
  captured from `latest.json` when the ingested snapshot matches. Documented
  in the contract (`"bbox": null` possible on historical snapshots).

### Dev vs production constraints (called out per spec)

- Docker Compose + port 5435 + `latest-pg17`-era pinned image
  (`timescale/timescaledb:2.28.2-pg17`) are DEV conveniences; production
  points `DATABASE_URL` at a managed instance. Nothing in schema/app code
  assumes the container.
- 1-day hypertable chunks and the 2h cagg refresh window were sized for
  production cadence (~30s polls, hundreds of vectors/snapshot), not for the
  dev machine.

### Fixed during gate testing

- `asyncpg.InterfaceError` (client-side "connection is closed") escaped the
  tailer's retry net and crashed it during the DB-outage test — added to the
  retriable set alongside `PostgresError`/`OSError`/`ConnectionError`.
- Stats endpoint: interval parameter must be passed as `timedelta`
  (asyncpg encodes Python types, not SQL literals).
- Broadcaster originally tracked only `snapshot_time`; with the tailer
  writer, the collector's JSONL buffering split a snapshot across two
  ingestion batches — the first (partial) push then suppressed the completed
  snapshot. Structural fix in three parts: (1) `snapshots.complete` column
  maintained at the DB level inside the ingestion transaction (migration
  003) — `/replay` and the DB fallback of `/live` only ever serve complete
  snapshots; (2) `/live` and the WS payload serve `latest.json` FIRST (it is
  atomically replaced and always whole — the DB derives from the archive and
  can never be ahead), with the DB as complete-only fallback; (3) the
  broadcaster keys pushes on `(snapshot_time, count)` of the payload and
  initializes its baseline without pushing, so fresh WS clients get no
  immediate duplicate. With the collector `--db-url` mirror the partial
  window doesn't arise at all; the machinery still protects the
  follow-writer and crash-recovery paths.

### Gate results

- **Gate 0:** migrations empty→current clean & re-runnable; hypertable,
  cagg (`materialized_only=false`), refresh policy verified via
  `timescaledb_information`.
- **Gate 1:** backfill run twice over the real archive: run 1 inserted 1896,
  run 2 inserted 0 (identical row counts). Nulls preserved (362 null
  baro_altitude, 1 null velocity, 3 empty callsigns verified in DB).
- **Gate 2 (re-run after the option-1 revision):** with `--db-url`, each
  snapshot is mirrored whole ~0.1s after the archive write and marked
  `complete` immediately. DB stopped mid-collection → collector logged
  `DB write failed (archive unaffected...)` per snapshot and kept archiving
  (exit 0); DB restarted → sink reconnected on the very next snapshot;
  `ingest.py backfill` inserted exactly the 377 gap rows (2 snapshots).
  Final reconciliation: 6807 archive keys = 6807 DB rows, 43 = 43 snapshots,
  all complete.
- **Gate 3:** all endpoints typed & documented; EXPLAIN verified — history
  = Index Only Scan on PK, replay = ts-index scan, stats = cagg
  materialized chunks + real-time tail only. Range caps enforced (422),
  replay-before-archive → 404.
- **Gate 4 (re-run after the option-1 revision):** two concurrent WS clients
  each received the on-connect snapshot immediately and every new snapshot
  with its FULL state set ~1s after collection (181/179/179 vectors);
  no duplicate initial push; REST fallback unchanged; disconnects
  unsubscribe via `finally` (bounded queues are dropped with the client).
