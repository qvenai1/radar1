# mtscraper — standalone MarineTraffic vessel scraper

A self-contained scraper for the **MarineTraffic live map**. It is a sibling of
the ADS-B `plandata` project but **shares no code with it** and imports nothing
from it — running or breaking one cannot affect the other.

It works the same way that reliably defeats Cloudflare in `plandata`: it opens
the real live map in a browser (which establishes the Cloudflare/bot-management
session), then **intercepts the vessel-tile responses the page fetches on its
own** (`getData/get_data_json_4/...`). It persists a deduplicated vessel
catalogue plus a full position time-series.

## Why a browser (and why headful)

Two facts were verified against the live site before this was built:

1. The live map is a **React SPA** — a plain HTTP GET of the page returns an
   empty shell; all vessel data loads client-side.
2. The tile data endpoint returns `{"rows":[],"areaShips":0}` to a cold request
   — it is gated behind a real browser session and Cloudflare's `__cf_bm`
   cookie. **Headless** Chromium trips Cloudflare's "attention required"
   interstitial; **headful** does not. Headful is therefore the default.

A single zoom-7 load fetches a tile grid covering the whole eastern
Mediterranean, so the scraper just loads → intercepts → filters to your area.

## Legal / ToS note

MarineTraffic's Terms of Service prohibit scraping, and the site is actively
bot-protected. This tool is provided for your own use; you are responsible for
complying with their terms and applicable law. For production/commercial needs,
their official API or a dedicated AIS feed (e.g. AISStream) is the sanctioned path.

## Install

```bash
cd ~/Desktop/mtscraper
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
# Chromium is reused from ~/.cache/ms-playwright if already present, else:
.venv/bin/playwright install chromium
```

## Run

Defaults match the URL you started from (`centerx:35.2/centery:33.2/zoom:7` —
the eastern Mediterranean off Cyprus/Lebanon/Syria):

```bash
# visible browser (default), one cycle, into ./mt_output
.venv/bin/python -m mtscraper --max-cycles 1

# run continuously, every 60s, over a 500x500 km box around the centre
.venv/bin/python -m mtscraper --interval 60 --half-height-km 250 --half-width-km 250

# headless background run on a server (no display) via a virtual framebuffer
xvfb-run -a .venv/bin/python -m mtscraper --interval 60
```

### Key options

| Flag | Meaning | Default |
|------|---------|---------|
| `--lat` / `--lon` | map **view** centre (note: `--lon` is MarineTraffic's `centerx`) | 33.2 / 35.2 |
| `--zoom` | MarineTraffic map zoom | 7 |
| `--area` | in-area filter: `polygon` (coastal AOI) or `bbox` | polygon |
| `--buffer-km` | polygon mode: km buffer around each coast that counts as NEAR | 100 |
| `--half-height-km` / `--half-width-km` | bbox mode: half-size of the rectangle | 250 / 250 |
| `--interval` | seconds between cycles | 30 |
| `--max-cycles` | stop after N cycles | run forever |
| `--settle` | seconds to wait for tiles after each load | 6 |
| `--headful` / `--headless` | browser mode | headful |
| `--persist-out-of-area` | also keep vessels outside the area | off |
| `--no-positions` | disable the time-series archive | off |
| `--position-heartbeat` | re-write an unchanged vessel this often (0 = store every observation) | 3600 |
| `--enrich` | resolve MMSI/IMO per vessel (see below) | off |
| `--enrich-per-cycle` | max identities resolved per cycle | 25 |
| `--enrich-throttle` | seconds between detail calls | 1.5 |
| `--profile-dir` | persistent browser profile dir | auto when `--enrich` |
| `--passive` | harvest the map's own refresh cadence (stealthier) | off (active) |
| `--proxies` | proxy list file (one per line) → rotation | none |
| `--profile-count` | number of rotating session identities | 1 |
| `--output` | output directory | `./mt_output` |

Every flag has a `MT_`-prefixed environment equivalent for the common ones
(`MT_POLL_INTERVAL_S`, `MT_HEADLESS`, `MT_NAV_TIMEOUT_S`, `MT_PROXY`,
`MT_AREA_BUFFER_KM`, `MT_MAX_SPEED_KN`, `MT_STALE_AFTER_MIN`, `MT_GAP_THRESHOLD_S`, …).

**Fetch vs filter (important):** `--lat/--lon/--zoom` decide *where the browser
looks* (the fetch mechanism — the site only serves tiles). The `--area` polygon
decides *what counts as in-area* (the filter). They are separate concerns.

## Output

Written under `--output` (default `mt_output/`):

- **`vessels.jsonl`** — SHIP_ID-deduplicated catalogue, one line per distinct
  vessel ever seen. Restarts reload known ids and don't re-emit them.
- **`positions.jsonl`** — position time-series (your movement history). A line is
  written when a vessel's report actually changes, plus a heartbeat every
  `--position-heartbeat` seconds (default 3600) so an idle vessel keeps proving it
  is still there. See [Time-series volume](#time-series-volume) for why.
- **`vessel_identities.jsonl`** — resolved identity join table (only with
  `--enrich`): `ship_id -> {mmsi, imo, eni, callsign, name, country, type,
  subtype, length_m, width_m, transponder_class}`. One line per distinct
  resolved vessel; join to positions by `ship_id`.
- **`collection_gaps.jsonl`** — append-only journal of downtime gaps (power loss
  etc.) longer than `gap_threshold_s`, so a real "signal lost" can be told apart
  from a gap on our side. Purely observational.
- **`.completed_ship_cache.txt`** / **`.collection_state.json`** — fast resume
  cache + last-cycle cursor (operational state, not archive).
- **`mtscraper.log`** — run log (includes periodic + final lifetime metrics).
- **`diagnostics/`** — last tile payload, last page HTML (on a blocked cycle),
  last exception, and `unknown_payloads/` (archived undecodable responses).

Each record carries an additive `validation` block:

```json
{"ship_id":"6663445","fetched_at":"2026-07-21T…","source":"interception",
 "in_area":true,"distance_km":12.3,
 "data":{"ship_id":"6663445","mmsi":null,"latitude":40.89,"longitude":27.46,
         "speed_knots":12.3,"course":277.0,"heading":200.0,"shipname":"MSC AMELIA",
         "shiptype":"7","flag":"LR","destination":"TRTEK","length_m":399.0,
         "width_m":61.0,"elapsed_min":2.0},
 "validation":{"position_quality":"confirmed","flags":["near_border"],
               "country":null,"near":"Turkey"}}
```

## Time-series volume

**The upstream refresh rate is the ceiling on track resolution, not the poll
rate.** Measured over 28,218 real position updates for moving vessels in this
archive:

| | |
|---|---|
| Upstream refresh, moving vessel | **9.3 min** median (25th pct 9.2, 75th 13.8) |
| Age of a fix on arrival (`elapsed_min`) | **8–9 min** median |
| Collection cycle | ~2.3 min |

So the scraper asks about 4x more often than the source has anything new to say,
and polling faster cannot improve a track — a vessel at 12 kn moves ~3.4 km
between genuinely distinct fixes, and that granularity is fixed upstream.

What the oversampling *did* cost was disk: 83% of a naive archive is the previous
line restated. `PositionSink` therefore writes on change plus a periodic
heartbeat. Replaying 400,000 real records from this archive through it:

```
written    68,685 (17.2%)      # 15.3% genuinely distinct + heartbeats
suppressed 331,315 (82.8%)
size       195 MB -> 33 MB     # 5.8x, no position lost
```

Fast polling is still worth keeping: it catches each new fix within ~2 min of it
appearing rather than up to 9 min late. Only the redundant *storage* was waste.

Set `--position-heartbeat 0` (or `MT_POSITION_HEARTBEAT_S=0`) to restore
store-every-observation behaviour.

## Validation & area (parity with the ADS-B project)

A validation stage sits between parse and persist. It is **non-destructive** —
nothing is dropped; every observation is a record carrying the reasons a
consumer needs:

- **Position quality**: `confirmed` / `no_position` / `suspect`.
- **Flags**: `outside_area`, `near_border`, `invalid_identity`,
  `placeholder_name`, `quantized_position`, `implausible_kinematics` (speed
  `> MT_MAX_SPEED_KN`, or course/heading out of range), `stale_position`
  (ELAPSED `> MT_STALE_AFTER_MIN`).
- **Area** (polygon mode): each vessel is classified against the coastal AOI —
  `INSIDE` a country (in port), `NEAR` a coast (within `--buffer-km`, attributed
  to the nearest country), or `OUTSIDE` (open sea). Ships are on water, so NEAR
  is the common bucket. The AOI is a true kilometric buffer of the country
  polygons in a local AEQD projection (not a degree offset).

The AOI countries (Turkey, Syria, Lebanon, Israel, Egypt, Cyprus) come from a
committed Natural Earth trim at `mtscraper/data/aoi_countries.geojson`,
regenerated via `python scripts/build_aoi_asset.py` (dev-time only, never fetched
at runtime).

## Identity enrichment (`--enrich`) — resolving MMSI/IMO

The tile feed is keyed by MarineTraffic's internal `SHIP_ID` and carries **no**
MMSI/IMO. `--enrich` resolves the standard maritime identity per vessel from the
site's own detail endpoint, fetched **inside the warmed browser session**:

```
GET /en/vessels/{ship_id}/general   (with header X-Requested-With: XMLHttpRequest)
-> {"shipId":…, "name":…, "imo":…, "mmsi":…, "callsign":…, "country":…, …}
```

```bash
.venv/bin/python -m mtscraper --enrich --enrich-per-cycle 20 --interval 60
```

Design (honest about the constraints):

- **Decoupled & throttled** — enrichment runs *after* the fast position pipeline,
  capped at `--enrich-per-cycle` vessels (default 25) with `--enrich-throttle`
  seconds between calls. The endpoint is per-vessel and Cloudflare-fronted, so a
  full area of thousands of vessels fills in **gradually over many cycles**, not
  at once. Resolved ids are cached (`vessel_identities.jsonl`) and never re-fetched.
- **Persistent profile** — enrichment needs a stable Cloudflare `cf_clearance`,
  so `--enrich` auto-uses a persistent browser profile under
  `<output>/.browser_profile` (override with `--profile-dir`). The first launch
  solves the challenge once; subsequent runs reuse it.
- **Soft-block aware** — a call that returns HTTP 200 without a real record (a
  challenge-window blip) is treated as a retryable miss, and consecutive misses
  trip a per-cycle backoff so a challenged endpoint is never hammered.
- **Classified failures** — `attempt_detail` reports *why* a lookup produced
  nothing (`http_error`, `challenge`, `no_record`, `undecodable`, …) and a blocked
  cycle logs the breakdown. The distinction is the diagnosis: `http_error` points
  at the address, `challenge` at the browser clearance, `no_record` at the vessel
  or the rate. The first few blocked bodies are archived under `diagnostics/`.
- **Least-failed-first queue** — vessels never tried come before ones already
  known to fail, and an id that answers "no record" three times is parked.

> **Why that queue rule exists.** With a plain `sorted(pending)`, every cycle
> handed the endpoint the same ids. A handful of SHIP_IDs that genuinely have no
> detail page sat at the head of the queue and consumed the whole per-cycle miss
> budget before any resolvable vessel was reached. Enrichment reported itself
> blocked, with an exponential cool-down, while the endpoint was answering
> perfectly well — observed live as a slide from 605 identities/day to 17, then a
> full stall at 3% of the archive resolved. It reads exactly like escalating
> bot-blocking, and it is not. Check the logged outcome breakdown before
> concluding you have been banned.

**What this does and does not solve.** It solves the *identity* gap: positions
(keyed by `ship_id`) can now be joined to real MMSI/IMO. It does **not** turn a
snapshot source into a live stream — enrichment is still periodic polling and
does not fill a live-feed (e.g. AISStream) outage in real time.

## Continuous archiving, anti-ban & the map

Built for sustained archiving without tripping bans — the honest failure mode
(observed live: Cloudflare escalates to blocking even the map under aggressive
polling) is designed around, not ignored.

### Passive collection (`--passive`)

Instead of forcing a page reload every cycle (active mode), passive mode leaves
the session open and harvests the tiles the live map fetches **on its own
cadence**, reloading only after `MT_PASSIVE_RELOAD_AFTER_EMPTY` (default 5)
consecutive empty cycles. This looks like a browser tab left open — a far
gentler footprint for 24/7 running than bursty polling.

```bash
# gentle 24/7 archiving: passive + enrich, one persistent identity
xvfb-run -a .venv/bin/python -m mtscraper --passive --enrich --interval 120
```

### Session rotation (proxies + profiles)

For a lower ban profile, rotate identity. A `Session` pairs an egress **proxy**
with its own **persistent browser profile** (so a rotation swaps both network
egress and the stored Cloudflare clearance). Supply a proxy list and/or ask for
several profiles:

```bash
# proxies.txt: one per line, e.g.  http://user:pass@host:port
.venv/bin/python -m mtscraper --passive --enrich \
    --proxies proxies.txt --profile-count 4 --interval 120
```

The orchestrator **rotates automatically** when Cloudflare blocks the tiles or
when enrichment gets fully blocked (with an exponential per-cycle cool-down so a
challenged endpoint is never hammered). Profiles live under
`<output>/.profiles/session_<i>`.

> **Honest caveat.** The rotation *logic* is built, wired, and unit-tested, but
> its real anti-ban *efficacy* depends on genuine residential proxies you supply
> — that can only be validated in your production over time. With no proxy list
> it falls back to a single session (still works, weaker anti-ban). Rotation
> reduces ban risk; it never eliminates it. MarineTraffic's ToS prohibit
> scraping — you are responsible for compliance.

### Context map (`mtscraper.mapgen`)

Render the archive as a **self-contained** HTML map (inline SVG, no external
tiles/scripts — opens offline, CSP-safe):

```bash
.venv/bin/python -m mtscraper.mapgen mt_output --out mt_output/map.html
```

It draws the AOI coastline, one dot per vessel (teal = MMSI resolved, grey =
position-only, gold ring = moving), a hover tooltip with name/MMSI/IMO/speed/
last-update, and a prominent **"⚠ NOT LIVE — snapshot"** banner with the latest
snapshot time — because the data is periodic snapshots, disclosed as such to the
viewer, never presented as real-time.

## Deployment (24/7 as a systemd user service)

A terminal run dies with the terminal. `deploy/` installs the scraper as a
**systemd user service** instead — no root needed, because collection runs as you
and needs only a browser and a virtual display:

```bash
./deploy/install.sh
systemctl --user start mtscraper
journalctl --user -u mtscraper -f      # watch cycles live
systemctl --user stop mtscraper        # graceful: SIGTERM flushes the writers
```

To also survive reboot with no graphical login (one privileged call, printed by
the installer if it is not already on):

```bash
sudo loginctl enable-linger $USER
```

All tuning lives in `~/.config/mtscraper/mtscraper.env`, outside the unit — the
installer never overwrites it, so edits survive a re-install. Change a setting,
then `systemctl --user restart mtscraper`.

The service runs `--passive --enrich` at a 120s interval: the measured sweet spot
where the ~37s enrichment spends per cycle still fits inside the interval.

### The disk is the real operational risk

Measured over 3.8 days of live service: **488 B per position record** and a real
cycle period of **~138s** once the browser is warm — note that `--interval` is a
sleep *after* processing, not a fixed period. That was **373 MB/day** while every
observation was stored; with change-detection (see
[Time-series volume](#time-series-volume)) it is **~64 MB/day**. Two guards exist
anyway, because an unattended archiver that fills its filesystem takes the whole
host with it:

- **`MT_MIN_FREE_GB`** (default 5, ~2.5 months of runway at the measured rate) —
  `deploy/preflight.sh` refuses to start below this floor. systemd retries per
  `RestartSec`, so the service recovers by itself once space is freed.
- **`MT_REQUIRE_MOUNT`** — assert a mountpoint is a live filesystem before
  starting. **Set this whenever the archive lives on a removable drive.** An
  unmounted drive leaves its mountpoint behind as an ordinary empty directory, so
  without the assertion the archive silently writes to the internal disk and
  looks perfectly healthy while filling the very filesystem the move avoided.

`MemoryMax=4G` in the unit turns the classic days-long browser leak into an
automatic recycle rather than host-wide memory pressure; the restart resumes from
the ship-id cache and costs one cycle.

## Resilience (long unattended runs)

- **Self-healing ladder**: consecutive empty cycles escalate reload → restart;
  repeated parse failures archive the payload then reload; repeated restart
  failures abort. Thresholds live in `RecoveryConfig`.
- **Resume**: known SHIP_IDs load from a fast cache (rebuilt from the archive if
  stale) so a restart continues where it left off.
- **Metrics**: lifetime counters (cycles, failures, restarts, reloads, gaps,
  unique vessels, averages) logged every 10 cycles and at shutdown.

## Data notes (verified from a live capture)

- Identity key is **`SHIP_ID`** (MarineTraffic's internal id). MMSI is usually
  *not* present in the tile feed, so `mmsi` is normally `null`.
- `SPEED` arrives in **tenths of a knot** (`"123"` → `12.3 kn`); `HEADING == 511`
  means "not available" and is stored as `null`.
- The full untouched source row is available in memory (`Vessel.raw`) but is not
  written to disk by default — the typed fields are.

## Architecture

Clean, swappable layers; the browser and network format never leak upward:

```
constants ── exceptions ── models ── config ── logger
     │
   tiles ── geo           parser (decoder + vessel schema)
     │                       │
  browser (Playwright, headful interception) ── fetcher (interception → direct-tile)
     │                       │
  accumulator (in-area filter, SHIP_ID dedup) ── persistence (durable JSONL)
     │
  orchestrator (cycle loop, health, restart) ── main (CLI / composition root)
```

## Develop

```bash
.venv/bin/ruff check mtscraper tests
.venv/bin/mypy mtscraper
.venv/bin/pytest
```

The suite runs entirely offline (a fake browser stands in for Playwright), and
the tile-math + parser tests are pinned against a real captured tile.
