# DASH-BOARD-pre — assessment for turning «لوحة» into the analyst's full workbench

> **Scope.** The owner's decision: «لوحة» becomes the analyst's complete surface — pattern
> over TIME (trends, aggregations, country comparisons), while the home stays the quick look.
> This is the pre-build measurement, not a design. ⛔ No chart types are chosen here, on
> purpose: what the data supports has to be known first.
>
> **Everything below was measured against the live database on 2026-08-04 ~05:00–05:40 UTC.**
> Where a number contradicts an earlier note, the earlier note is corrected in place and the
> contradiction is called out rather than smoothed over.

---

## 0. Three premises that were checked and did not survive

These are recorded first because they change what the phase should build.

### 0.1 ⛔ `state_vectors` was NOT truncated. The 67.9 M rows are still in the database.

The task premise reads DISK-2 as having moved the global era out of the database. Measured,
that is not what happened:

| measure | value |
|---|---|
| `state_vectors` rows | **77,159,029** |
| `state_vectors` size | **9,246 MB** (25 chunks, **17 compressed**) |
| range | **2026-07-08 09:59:16Z → now**, i.e. **27 days, unbroken at the ends** |
| rows before the 2026-07-29 13:55:36Z frame boundary | **73,659,477** |
| rows after it | 4,378,855 |
| retention policies on any hypertable | **none** — jobs are 5× compression, 1× cagg refresh, 1× telemetry, 1× job-log retention |

`state_vectors_pre-narrowing_2026-07-29.jsonl.gz` (3,475,344,889 B) is a **copy of the JSONL
collector log**, not an export-and-delete of table rows. Its 67.9 M lines line up with the
67,884,838 pre-frame `adsbexchange` rows still sitting in the hypertable.

**So the air questions are not range-limited at all.** The compressed archive never has to be
queried, and the "is it queryable, at what cost" question is moot for the dashboard — it would
only matter for a window earlier than 2026-07-08, which no source in this project has.

⛔ The real air constraint is a different one, and it is a *comparability* limit, not a
*retention* limit — see §0.2.

### 0.2 ⚠ The frame boundary does NOT step the per-country counts — but it does destroy every ratio

Measured, Syria, distinct hulls/day, `source='opensky'`, across 2026-07-29 13:55:36Z:

```
07-25 303   07-26 408   07-27 367   [07-28 absent]   07-29 306
07-30 400   07-31 394   08-01 334   08-02 361
```

No step. The classifier ran the same way in both eras, so a hull inside Syria was counted in
both. **What the boundary destroys is every denominator**: pre-frame, `country IS NULL` on
`adsbexchange` covers 96.05 % of rows (369,358 of 9,345,862 on 2026-07-20 carry a country);
post-frame it covers 29.6 % (1,419,838 of 2,015,968). Any share, percentage or "fraction of
traffic" crossing the boundary compares two different populations.

⇒ **Absolute per-country counts are safe to plot across it. Ratios are not.**

### 0.3 ⛔ THE AIR STREAM HAS A COVERAGE HOLE THE SIZE OF THE AIS ONE, AND IT WAS NOT ON THE LIST

The task names AISStream's 45.46 % outage as the coverage hazard. Measured today, the **air**
side is in the same condition and nothing had recorded it:

| stream | span | hours with data | **coverage** |
|---|---|---|---|
| air — any source | 2026-07-08 → 08-04 (643 h) | 483 | **75.08 %** |
| air — `opensky` | | 479 | 74.46 % |
| air — `airplanes_live` | | 456 | 70.88 % |
| air — `adsbexchange` | | 265 | **41.19 %** |
| maritime — `vessel_states` | 2026-07-11 → 08-04 (562 h) | 346 | **61.55 %** |

Four **whole days are missing** from the air series: **2026-07-17, 07-23, 07-24, 07-28**. Only
11 of 27 days have all 24 hours present. Partial days measured at 6–23 hours: 07-08 (15),
07-12 (23), 07-14 (13), 07-15 (17), 07-16 (11), 07-18 (15), 07-22 (10), 07-25 (19), 07-27 (20),
07-29 (18).

⇒ The coverage contract the task demands for maritime is **equally mandatory for air**, and a
line chart of air activity would otherwise draw four days of "no aircraft" that mean "no
collector".

---

## 1. The existing aggregation layer — one, and it does not reach these questions

⛔ There is exactly **one** continuous aggregate in the database. Anything this phase builds
must extend that pattern, not sit beside it.

```
state_vectors_1m  →  _materialized_hypertable_6
  SELECT time_bucket('1 minute', ts) AS bucket, source,
         count(*), sum(on_ground), max(baro_altitude), max(geo_altitude), max(velocity)
  FROM state_vectors GROUP BY bucket, source
  refresh policy: every 1 min, start_offset 2 h, end_offset 1 min
```

⚠ **It carries no country, no icao24 and no geography.** It answers "how much traffic, by
source, by minute" and nothing this dashboard asks. It cannot be reused for question (أ); it
can only be a *model* for how a new aggregate should be declared.

Everything else in the project aggregates **on demand, in SQL, at request time** —
`maritime_events` is the analytic event store and every `/maritime/*` contract reads it live.
There is no summary table anywhere. ⇒ **The house pattern is: on-demand SQL over
`maritime_events`, and a Timescale continuous aggregate only where the base table is a
hypertable large enough to need it.** Only `state_vectors` qualifies.

### The analytic event store

`maritime_events` — 50,173 rows, 54 MB, plain table (not a hypertable), `start_ts`
**2026-07-11 → 2026-08-04**:

| event_type | classification | rows | first → last event |
|---|---|---|---|
| `ais_gap` | stationary | 21,990 | 07-11 → 08-04 |
| `ais_gap` | outage-excluded | 16,882 | 07-11 → 08-03 |
| `ais_gap` | candidate | 7,402 | 07-11 → 08-04 |
| `ais_gap` | edge | 2,646 | 07-11 → 08-04 |
| `gnss_anomaly` | area-jamming | 504 | 07-12 → **07-19** |
| `ais_gap` | jamming-excluded | 301 | 07-12 → 08-04 |
| `gnss_anomaly` | candidate | 276 | 07-11 → **07-19** |
| `zone_dwell` | candidate | 57 | 07-11 → 08-03 |
| `rendezvous` | candidate | 47 | 07-11 → 08-03 |
| `identity_change` | candidate | 42 | 07-11 → 08-03 |
| `gnss_anomaly` | glitch | 25 | 07-11 → **07-19** |
| `imo_integrity` | candidate | 1 | 07-25 only |

Columns: `event_type, mmsi, start_ts, end_ts, imo, start_lat, start_lon, end_lat, end_lon,
duration_s, classification, method, detail(jsonb), computed_at`.

⛔ **There is no `country` column.** Country is derived per request from lat/lon through the
shared AOI classifier (`backend/alerts.py`, `backend/geo_area.py`, `backend/data/aoi_countries.geojson`)
— the same polygons the `/alerts?country=` filter uses. Any country dimension on this
dashboard inherits that cost and that method.

### ⚠ A detector is dead, and its series would read as good news

`gnss_anomaly` last ran **2026-07-21 11:35:03Z — 13 d 17 h ago**, and its newest event is
**2026-07-19**. Every other detector is fresh (~18 min): `ais_gap` 05:15, `identity_change`
05:00, `imo_integrity` 05:00, `rendezvous` 05:00, `zone_dwell` 05:00.

⇒ A "GNSS anomalies over time" line would fall to zero on 2026-07-19 and stay there. That
reads «الشذوذ توقّف» and means «الكاشف توقّف». ⛔ This must be fixed or explicitly excluded
before any chart touches `gnss_anomaly`; it is not a display problem.

---

## 2. The five questions

| # | question | what the base supports today | what needs building | ⛔ what the data does NOT support | cost (measured) | mixing warning that applies |
|---|---|---|---|---|---|---|
| **أ** | which countries lead events, and how the ranking moves over time | **Air:** `state_vectors.country` / `near_country`, 2026-07-08 → now, 27 d, 77.2 M rows. **Maritime:** `maritime_events.start_lat/lon` → AOI polygons at request time. **Space:** `satellite_passes.countries[]` — but see the ⛔ column. | A country×time contract. Air needs a **second continuous aggregate** on `state_vectors` (bucket, country, source → distinct hulls) modelled on `state_vectors_1m`; maritime is an on-demand query. ⛔ Not a summary table — the house has none. | ⛔ **No cross-domain "events" total exists.** Air counts hulls, maritime counts detector events, space counts passes — three different units. A single "country leaderboard" would be adding aircraft to gap-events. ⛔ Ratios cannot cross 2026-07-29 13:55Z (§0.2). ⛔ 4 missing days + 24.92 % air outage (§0.3). | air 1 d **244 ms**, 7 d **366 ms**, one pre-frame day (9.75 M rows) **748 ms**; full 27 d ≈ **8 s** extrapolated | `country` is a **classified position**, not a registry. Air must not mix `opensky` with `adsbexchange` (§6.11 — two communities). Space is «أجسام لها ممرّ», never «أقمار متتبَّعة». `?country=` scoping only applies where DASH-3 wired it. |
| **ب** | is the frequency of AIS outages rising or falling | ✅ **Fully supported.** `maritime_events` where `event_type='ais_gap'`, 49,221 rows, 5 classifications, 07-11 → 08-04. | Nothing structural — an on-demand grouped query. Only a **contract** to expose it. | ⛔ The series measures **our detector's output**, not the world: `outage-excluded` (16,882) and `jamming-excluded` (301) are gaps the detector *removed* as our own outages. Plotting raw `ais_gap` without the classification breakdown counts our downtime as vessel behaviour. ⛔ And the 61.55 % feed coverage means a quiet day may be a silent feed. | **18 ms** over the full 24 d | none of the five directly — but `classification` is the honesty axis and must never be collapsed. |
| **ج** | where do rendezvous cluster geographically | `maritime_events` where `event_type='rendezvous'`: `start_lat/lon`, `end_lat/lon`, `duration_s`, `detail`. | Nothing — an on-demand query returning points. | ⛔ **N = 47 over 24 days.** That is not a density surface; it is a scatter of individual leads. Any binning/heat treatment would manufacture a pattern out of ~2 events/day. ⛔ `rendezvous` last event 08-03 and the detector aged before TIMERS-1/2, so early-window counts are detector-limited. | **< 50 ms** (47 rows) | `classification='candidate'` on all 47 — «دليل تحقيق لا إثبات». Never presented as confirmed meetings. |
| **د** | how many hulls carry more than one pattern, and how the count moves | `maritime_events` grouped by `mmsi` with `count(distinct event_type)`; `backend/composite.py` already ranks exactly this (`hull-composite-v1`) and `/maritime/composite` already serves it. | A **time-bucketed** version of an existing contract — `/maritime/composite` today answers "who", not "how many, over time". Small extension, on-demand. | ⛔ Measured weekly: **5 / 35 / 7** multi-pattern hulls (weeks of 07-20, 07-27, 08-03). That series is **dominated by detector availability**, not by hull behaviour: `gnss_anomaly` stopped on 07-19, `identity`/`zone_dwell` aged before TIMERS-1/2, and the 08-03 week is one day long. ⛔ It must not be drawn as a trend until every contributing detector has a stated uptime window. | **46 ms** over the full range | Each underlying event is a lead; a composite of leads is a **stronger lead, still not proof** (composite.py's own words). `importance`/rank is display order, never danger. |
| **هـ** | flag distribution among hulls with simultaneous patterns | `vessel_identity.flag_country` — 5,249 rows, **99.39 %** populated, 116 distinct flags. On the actual (هـ) population — the **306** multi-pattern hulls — attribution is **306/306 = 100 %**. | Nothing structural — a join, on demand. | ⚠ **The expected 93.7 % hole is not where the task expected it.** 93.7 % is `flag_iso` on the *alerts* contract, derived by `mmsi_to_iso()` from the MMSI's MID prefix (MID-ISO-1) — a **different derivation** from `vessel_identity.flag_country`, which is 99.39 %. ⛔ The «غير منسوب» slice is still mandatory, for the honest reason rather than the assumed one: the population changes, 32 of 5,249 identity rows carry no flag, and the two derivations disagree by ~6 points, so whichever the contract picks must be **named** and its misses shown. ⛔ 306 hulls is a small denominator — a flag with 13 hulls is 4 %, and one more hull moves it by 0.3 pt. | **42 ms** | ⛔ **Flag is REGISTRY, not location.** «علم التسجيل» must stay in the label; a flag glyph reads as a position faster than a name does (DASH-CARD-2's rule). Grey/sanctions-flag weighting lives in `identity_change.GREY_FLAGS` and is a prioritisation, not an accusation. |

### ⛔ Space has no time series at all

`satellite_passes`: **359 rows, ONE window** — `window_start = window_end = 2026-08-04 04:20:49Z`,
covering 04:20:49 → next-day 04:17:49. It is **recomputed, not accumulated**. There is no
history of passes, so question (أ)'s space dimension can only ever be "now", and «كيف يتغيّر
الترتيب عبر الزمن» has no space component until passes are retained. ⛔ Retaining them is a
build item with its own storage decision, not a dashboard query.

---

## 3. Cost, against the host

The measured host constraint is 16 threads, no GPU, database on a shared disk, and a
collector that must not be starved.

| workload | measured | year-window verdict |
|---|---|---|
| every maritime aggregation (ب · ج · د · هـ) | **18–46 ms** over the full 24 d | ✅ Safe. `maritime_events` grows ~2,090 rows/day; a year ≈ 763 k rows ≈ **15×** today. Still sub-second on demand. No aggregate needed. |
| air, 1 day | 244 ms | ✅ |
| air, 7 days | 366 ms | ✅ |
| air, 1 pre-frame day (9.75 M rows) | 748 ms | ⚠ |
| air, full 27 d (77.2 M rows) | ≈ 8 s extrapolated | ⛔ **Not acceptable interactively**, and it competes with the collector for the shared disk. |
| air, 1 year at post-frame rate (730 k rows/day ≈ 267 M rows) | not run — extrapolates to ~30 s | ⛔ **Rejected as an on-demand query.** |

⇒ **Air is the only workload that needs a continuous aggregate, and it needs one.** Maritime
needs none, and building one for it would be the second aggregation layer the task forbids.

---

## 4. Recommended phase split

**Backend first, and in this order:**

1. **DASH-BOARD-0 — the coverage contract.** ⛔ Before any series ships. Every time-series
   response carries, beside its values: `series_start` (when collection began for that
   stream), `gaps[]` (the measured no-data windows), `source`, and the detector's `computed_at`
   where the series is detector-derived. The numbers in §0.3 exist; they are not yet a
   contract. Nothing else in this phase is safe without it.
2. **DASH-BOARD-1 — fix or fence `gnss_anomaly`.** 13 d 17 h stale. Either the timer is
   restored or the family is excluded from every series with a stated reason. ⛔ Not a
   display decision.
3. **DASH-BOARD-2 — maritime series contracts (ب · ج · د · هـ).** All four are on-demand SQL
   over `maritime_events`, all measured under 50 ms, no new storage. Cheapest real value in
   the phase.
4. **DASH-BOARD-3 — the air country aggregate (أ).** A second continuous aggregate on
   `state_vectors`, modelled on `state_vectors_1m`: bucket × country × source → distinct
   hulls. ⛔ Sized and reviewed on its own, because it is the only new storage the phase adds
   and `state_vectors` is 9.2 GB of a 15 GB database.
5. **DASH-BOARD-4 — decide whether `satellite_passes` is retained.** Owner's call: without it
   question (أ) has no space dimension, ever.

**Then the frontend**, once §2's ⛔ column is what the contracts actually say.

⚠ **The long-window rule cuts both ways.** The schema and contracts should carry
year-and-longer windows from now — but every one of them must also carry its coverage, because
the honest answer for most of these series today is **27 days at 75 % (air) and 24 days at
62 % (maritime)**, with four missing days and one dead detector. A contract that can express a
year but cannot express «هذه الفترة غير مرصودة» is the more dangerous of the two.
