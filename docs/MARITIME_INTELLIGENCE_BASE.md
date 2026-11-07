# Atheer RADAR (scraplan) Maritime Intelligence Base — Architecture & Methodology

**Status:** capstone-complete (eight layers + composite) · **Reference snapshot:**
frozen DB state **2026-07-20 00:03:36 UTC** (the last archived AIS position; all G0–G5
GNSS measurements below were taken on this exact snapshot).
**Scope:** a research + intelligence foundation for regional (Eastern-Mediterranean /
Middle-East AOI) maritime-activity analysis, from live AIS to cross-pattern behaviour.

> **Snapshot note (feed state).** This reference snapshot **precedes the upstream feed's
> return**: the `aisstream` feed disconnected at **2026-07-20 00:04:01 UTC** — 26 s after
> the last archived position — and had **not** reconnected as of this documentation pass.
> The snapshot is therefore the last data point before an open, ongoing feed outage (§6,
> §4.5). Every count below is exact to this frozen state.

This is the citable reference for scraplan's maritime intelligence base. It documents
what each layer is, where its facts come from and at what vintage, how the layers join
on the **IMO spine**, and — critically for an intelligence base — the **methodological
discipline** a study must follow to use it correctly. It is accurate to the implemented
schema (migrations `021`–`030` over the AIS substrate `007`/`008`) and to the detector
thresholds in `backend/` (read from source, not invented).

> **One-line citation form:** "scraplan maritime intelligence base, layer `<name>`
> (`source=<…>` / `method=<…>`, vintage `<date>`), reference snapshot 2026-07-20 00:03:36."

> **The one principle that governs everything below:** every derived signal is an
> **investigation lead, not proof**. The behavioural layers flag *candidates for
> investigation*; the composite compounds leads into a **stronger lead, still not
> proof**. Nothing here asserts wrongdoing. See §4.

---

## 1. Overview & purpose

The base turns a live terrestrial-AIS feed into a **layered, provenance-tracked,
IMO-anchored, join-ready** picture of vessel activity over the seven-country AOI
(Syria, Turkey, Iraq, Jordan, Lebanon, Israel, Cyprus). It exists to support
**analyses and studies**, not just a live map. The pipeline is:

```
live AIS (aisstream)  →  ENRICHMENT (M1 geo · M2a flag · M2b identity spine)
                       →  POSITION QUALITY (G · per-fix GNSS plausibility verdict)
                       →  BEHAVIOURAL ANALYSIS (A1 gaps · A2 rendezvous · A3 identity change
                                                · G-episodes GNSS anomaly)
                       →  CROSS-PATTERN COMPOSITE (IMO-stitched intelligence view, 4 patterns)

   plus two ON-QUERY reference layers over committed gazetteers, computing nothing at rest:
        zones × dwell (§2.10, admits a strong subset)  ·  port visits (§2.11, admits nothing)
```

The **G layer** sits between enrichment and behaviour by design: it stamps a per-position
plausibility verdict that the behavioural detectors then **consume** (A1/A2 exclude
implausible fixes; A1 gains a fifth confound from it) — data-quality flagged as a first-class
signal, never silently dropped.

**Core design principles:**

- **Layered, independently-sourced facts.** Each analytical concern is its own layer
  (its own columns/table), never silently merged. Location (M1), registration (M2a),
  self-reported identity (M2b), position quality (G), and each behaviour (A1/A2/A3) are
  distinct.
- **Provenance on every derived fact** — a `source`/`method` label + a vintage/derived
  stamp — so a study can cite exactly which layer, method, and date produced a value.
- **The IMO number is the durable spine.** MMSI, flag, and name are *point-in-time*
  and change (sale, re-flag, rename); the IMO is the permanent hull id. The identity
  spine (M2b) anchors on it, and the composite **stitches a vessel's events across its
  disguised identities by IMO** — the payoff the whole stack was built for (§3).
- **One generalized events table.** All four behavioural detectors (A1/A2/A3 + the GNSS
  episodes) write to `maritime_events` (`event_type` + typed columns + JSONB `detail`); a
  new pattern is a new `event_type`, not a new table (§2.8).
- **Honest by construction.** Every detector removes its dominant confounds *before*
  emitting a candidate, and every surface carries the "lead, not proof" caveat.

The base is **regional and terrestrial-AIS-scoped**, and **Class-A-focused** — it is a
*sampling frame*, not a census of all vessels (see §4, §6).

---

## 2. The layers

Eleven layers build on the AIS substrate (`vessel_states` + `vessel_snapshots`, migration
`007`; the vessel-photo cache `vessel_info`, `008`). The substrate is the moved-vessel
delta from the `aisstream` collector (`tracker_maritime.py`): one row per
`(mmsi, ts, source)` position fix, knots-native, nulls = "not reported". (The last three —
**IMO integrity** §2.9 (2026-07-25), **surveillance zones × dwell** §2.10 (2026-07-26) and
**port visits** §2.11 (2026-07-27) — are post-snapshot additions; their counts are live,
not frozen-2026-07-20.)

| Layer | Table / columns | Migration | Source / derivation | Provenance | Refresh |
|---|---|---|---|---|---|
| **M1 · Territorial-sea geo** | `vessel_states.{country, near_country, distance_km, geo_source}` | 021 | derived — `geo_area.py` (AEQD) | `geo_source='scraplan-aoi'` | at ingest + backfill |
| **M2a · Flag (MMSI/MID)** | `vessel_identity.{flag_country, station_type, flag_source, derived_at}` | 022 | deterministic — ITU MID table | `flag_source='mmsi-mid'`, `derived_at` | at ingest + backfill |
| **M2b · Identity spine** | `vessel_identity.{name, ship_type, ship_type_code, callsign, imo, length_m, beam_m, identity_source, identity_updated_at}` | 023 | AIS static (type 5) | `identity_source='ais-static'`, `identity_updated_at` | at ingest + backfill |
| **G · GNSS position quality** | `vessel_states.{position_quality, anomaly_flags, implied_speed_kn, inland_km, quality_method, quality_computed_at}` + `maritime_events` (`event_type='gnss_anomaly'`) | 027 (columns) · 028 (episode tiers) · 030 (A1 confound) | derived — `gnss_anomaly.py` (per fix) + `gnss_anomaly_events.py` (episodes) | `quality_method='gnss-quality-v1'` (fix) · `method='gnss-anomaly-detector-v1'` (episode) | timer 15 min, `scraplan-quality` (fix) · on-demand detector (episodes) |
| **A1 · AIS gaps** | `maritime_events` (`event_type='ais_gap'`) | 024 | derived — `gap_detect.py` | `method='ais-gap-detector-v1'`, `computed_at` | timer, 15 min |
| **A2 · Rendezvous** | `maritime_events` (`event_type='rendezvous'`) + partner index | 025 | derived — `rendezvous.py` | `method='rendezvous-detector-v1'` | timer, 20 min |
| **A3 · Identity change** | `maritime_events` (`event_type='identity_change'`) | *(none — reuses 024)* | derived — `identity_change.py` | `method='identity-change-detector-v1'` | timer, 30 min |
| **Composite** | views `maritime_hull_events`, `maritime_hull_composite` | 026 · 029 (admit GNSS) · 036 (IMO hull-key + collision) | aggregation — `composite.py` | `method='hull-composite-v1'` | live view |
| **IMO integrity** *(post-snapshot)* | `vessel_identity.{imo_quality, imo_flags, imo_method, imo_computed_at}` + the composite hull-key rule + `maritime_events` (`event_type='imo_integrity'`) | 035 (axis) · 036 (hull-key + collision disjunct) | derived — `imo_integrity.py` (pure) | `imo_method='imo-integrity-v1'` | backfill (tiny table, no timer) |
| **Zones × dwell** *(post-snapshot)* | `maritime_events` (`event_type='zone_dwell'`) + committed asset `backend/data/zones_aoi.json` | 037 (composite disjunct + `n_zone_dwell`) | derived — `zone_dwell.py` (pure) + `zones.py` loader | `method='zone-dwell-v1'` | on-demand detector (strong subset) + on-query `/maritime/zone-dwell` |
| **Port visits** *(post-snapshot)* | *(no table — on-query only)* + committed asset `backend/data/ports_aoi.json` | *(none — writes nothing)* | derived — `port_visit.py` (pure) + `ports.py` loader | `method='port-visit-v1'` | **on-query only** (`/maritime/port-visits`, one resolved port) — no timer, no stored layer |

Provenance legend: a `source`/`method` label, a *derived/updated* stamp
(`derived_at` / `identity_updated_at` / `computed_at`), and — for behavioural events —
a `classification` (candidate vs the removed confounds). Every one is on the row.

---

### 2.1 M1 — Territorial-sea geo classification (`vessel_states`, migration 021)

- **Provides:** where each positioned vessel sits relative to the seven countries'
  **territorial seas**: `country` (the country whose territorial sea the vessel is in,
  ≤ 12 NM of its coast; else NULL), `near_country` (nearest of the seven when *outside*
  every territorial sea), `distance_km` (**true kilometric distance to the nearest
  country's coast**, AEQD-projected; 0 on/inside a polygon), `geo_source`.
- **Source / derivation:** the **shared** `backend/geo_area.py` classifier — the same
  seven-country + AEQD machinery the aircraft/satellite layers use — with a maritime
  threshold: **12 NM = 22.224 km** (UNCLOS Art. 3 territorial sea), NOT the aircraft
  50 km land buffer. Configurable via `SCRAPLAN_TERRITORIAL_SEA_KM`. `country` and
  `near_country` are **mutually exclusive** per positioned row; `country IS NOT NULL`
  ⇔ `distance_km ≤ 22.224`.
- **Note (deliberate divergence):** M1's `distance_km` is distance-to-coast (the
  quantity the 12 NM test is computed from), unlike the aircraft layer's
  envelope-centre provenance distance. Same column name, per-table semantics.
- **Egypt/Iran are NOT in the AOI** — a vessel off a non-AOI coast classifies as
  *outside*, nearest-of-seven (documented deferral, §6).
- **Refresh:** classified at ingest (`maritime/db.py::vessel_row`) and by a one-off
  scoped-per-chunk backfill (`scripts/backfill_vessel_geo.py`). Additive; the natural
  key `(mmsi, ts, source)` is untouched.

### 2.2 M2a — Flag state from MMSI / MID (`vessel_identity`, migration 022)

- **Provides:** the vessel's **flag state** (country of registration), derived
  DETERMINISTICALLY and LOCALLY from the MMSI: `flag_country`, `station_type`
  (`ship`/`coast`/`sar`/`aid-to-navigation`/`other`), `flag_source`, `derived_at`.
  The maritime analog of the aircraft icao24→registration layer.
- **Source:** the leading three digits of a ship-station MMSI (first digit 2–7) are the
  ITU **Maritime Identification Digits (MID)** = the flag state. Asset
  `backend/data/mmsi_mid.json` — **292 assigned MIDs**, ITU Radio Regulations Appendix
  43 / Rec. ITU-R M.585, **retrieved 2026-07-19** (committed, never fetched at runtime).
  Only ship stations get a flag; coast/SAR/AtoN/other are typed honestly with **no
  forced flag**; an unallocated MID → NULL (never a guess).
- **DB-level guarantees:** CHECK constrains `station_type` to the closed set, and CHECK
  that `flag_country` may exist **only** for a ship station.
- **Point-in-time:** the MMSI/flag reflects the **current** registration and **changes
  on re-flagging** — a time-of-observation flag, not permanent identity (A3 detects the
  change; IMO is the anchor).
- **Refresh:** at ingest + `scripts/backfill_vessel_flag.py` (idempotent, `ON CONFLICT
  DO NOTHING`).

### 2.3 M2b — AIS-static identity spine (`vessel_identity`, migration 023)

- **Provides:** the durable identity spine — `name, ship_type, ship_type_code,
  callsign`, **`imo`** (the permanent hull id, **indexed** cross-reference),
  `length_m`/`beam_m` (from the AIS `Dimension` field), `identity_source`,
  `identity_updated_at`. Promoted from the **already-arriving Class-A AIS static
  messages** (ShipStaticData / AIS type 5) that the collector receives.
- **Source / semantics:** latest-wins per field (COALESCE keeps a known value when a
  later report omits it; a WHERE-changed guard avoids churn). **Point-in-time:**
  name/type/callsign are current-best and change on rename/re-registration; **IMO is
  the stable anchor** within the spine and across the whole base.
- **Voyage state is deliberately excluded** from the spine (`destination`, `draught`,
  `eta` change per voyage and stay on `vessel_states`); DB CHECKs `length_m/beam_m > 0`.
- **Refresh:** promoted at ingest + `scripts/backfill_vessel_identity.py`
  (latest-non-null per MMSI). **Dimensions are parse-forward-only** — they were dropped
  at parse time before M2b, so historical dimensions are unrecoverable and accumulate
  from ingest going forward (§6).

### 2.4 A1 — AIS-gap detection (`maritime_events` `event_type='ais_gap'`, migration 024)

- **Provides:** "going dark" **candidates** — an underway vessel that stopped
  transmitting. Typed columns `start_ts/end_ts` (the gap window), `imo`, start/end
  lat/lon, `duration_s`, `classification`; `detail` carries `distance_at_start_km,
  nav_status, speed_at_last_fix, coverage_cell_density, start_country/near_country` (M1).
- **The value is confound removal.** A gap is `classification='candidate'` **only** if,
  in priority order: it does **not** overlap a `feed_disconnect` outage window
  (reconstructed from `signal_events`); it is **not** a GNSS-anomaly artifact
  (`jamming-excluded`, the fifth confound — see below and §2.7); the vessel was
  **underway** (nav_status = 0 AND speed > 1 kn); and it started in **dense coverage** (a
  **data-driven coverage mask**, `coverage_mask.py`, 0.1°≈10 km cells, dense ≥ **100
  positions/cell** — this handles Cyprus/mid-sea receivers, NOT naive coast-distance).
  Otherwise the gap is stored with its honest reason, never discarded. Threshold: **≥ 1 h**
  (`GAP_THRESHOLD_S=3600`, from the p95≈33 min → p99≈4.5 h knee).
- **The waterfall (from `gap_detect.py`):** `outage-excluded` > `jamming-excluded` >
  `stationary` > `edge` > `candidate`. The **fifth confound** (`jamming-excluded`,
  migration 030, G4b) is a gap that the G layer explains: the vessel transmitted
  **implausible/spoofed positions during it** (so the plausible-stream silence is not real
  going-dark) and/or the gap sits in an **active area-jamming zone** (the G4a affected-zone
  product, outage-subtracted). It is a **deliberately distinct value from `outage-excluded`**
  — a feed outage (the receiver was down) and a GNSS denial (the feed was up; the vessel's
  GNSS was jammed) are mechanistically different, and a study filtering feed-outage must
  not silently mix externally-induced jamming with feed drops. Both are external
  interruptions, so both exclude high in the waterfall. On the frozen snapshot: **112**
  gaps are `jamming-excluded`.
- **Refresh:** `scripts/detect_ais_gaps.py`, run by `scraplan-gaps.timer` every **15
  min** (idempotent recompute-and-replace on the PK). PK `(event_type, mmsi, start_ts)`.

### 2.5 A2 — Rendezvous (`maritime_events` `event_type='rendezvous'`, migration 025)

- **Provides:** two vessels loitering together in open sea (potential ship-to-ship
  transfer). **One canonical row per encounter** (`mmsi` = the *smaller* MMSI); the
  partner + measurements in `detail` (`partner_mmsi/imo/name/flag/ship_type`,
  `min_distance_m, max_duration_min, both_max_speed, offshore_km, suspicion_rank`,
  `start_country`). Migration 025 adds the `((detail->>'partner_mmsi'))` index so
  "events involving vessel X" is cheap from either side.
- **Signature (thresholds from data):** both vessels **≤ 3 kn**, **true pairwise
  distance ≤ 500 m** (great-circle, time-matched fixes within ±5 min), **contiguous
  ≥ 1 h**. **Confound exclusion:** the encounter cell must be outside the **port/
  anchorage mask** (0.01°≈1 km cells with ≥ **5** distinct *stationary* vessels), the
  **busy/lane mask** (≥ **8** distinct vessels), and be **> 10 km offshore** (M1). The
  naive "two slow vessels close" signal is ~**99.9 % innocent** (ports/anchorages).
- **Prioritisation, not accusation:** `suspicion_rank` — tanker+tanker or
  flags-of-convenience cargo/tanker → high; offshore-support ("other")/ferry/service
  → low; else medium. Even clean candidates are **mostly legitimate** (offshore-energy
  drillship+supply clusters, ferries).
- **Compute:** SQL cell/time pre-filter → Python exact distance (reuse `geo_area`) →
  contiguous grouping. **No PostGIS, no O(n²); ~2 s.**
- **Refresh:** `scripts/detect_rendezvous.py`, `scraplan-rendezvous.timer` every **20
  min**.

### 2.6 A3 — Identity change (`maritime_events` `event_type='identity_change'`, no migration)

- **Provides:** a vessel broadcasting a different name/flag/MMSI over time (potential
  disguise). **The pattern the IMO spine was built to enable — needs no migration**
  (reuses `maritime_events` + the `imo` index). Two `change_type`s in `detail`:
  **`reflag`** (same IMO under a new MMSI + flag/name, with a **clean temporal switch**)
  and **`rename`/`retype`** (a confirmed value change under one MMSI). `detail` carries
  `old/new_value, old/new_mmsi/flag/name, last_old_ts, gap_hours, sustained_new_rows,
  suspicion_rank, correlated, correlated_events`.
- **Noise filter (confirmed change only):** the new value must be **sustained** (≥ **10**
  rows for rename/retype; ≥ **2** for a re-flag, whose confidence is the IMO + clean
  switch), **not a near-duplicate** (normalized Levenshtein ≥ **0.85** drops
  "EL QAHER I"/"EL QAHERI"), and **not garbage/placeholder** (control chars, symbol
  runs, generic MMSIs like `270000000`). Re-flag clean switch: old ends before new
  begins (≤ 6 h backward overlap tolerated; heavier overlap = an IMO collision, skipped).
- **Correlation (intel multiplier):** each candidate is joined to A1/A2 events by
  IMO/MMSI. **Ranking:** grey/FoC/**sanctions-associated (`Russia`)** re-flag, OR
  correlation with a gap/rendezvous, OR multiple changes → high; non-grey re-flag →
  medium; a plain same-flag rename → low.
- **Honest limitation:** renaming/sale/re-flagging is **usually LEGAL** — a documented
  change for investigation, NOT proven evasion.
- **Refresh:** `scripts/detect_identity_change.py`, `scraplan-identity.timer` every **30
  min**.

### 2.7 G — GNSS position-quality layer (`vessel_states` + `maritime_events`, migrations 027/028/030)

The maritime realisation of the position-quality axis that migration 012 established for
the aircraft `state_vectors` and that migration 021 **deliberately deferred** for vessels
("AIS spoof/identity checks belong to a later phase"). This is that phase. Its discipline
is **data-quality-as-event**: a spoofed/impossible position is **flagged, never deleted**,
so downstream layers can exclude it explicitly and a study can still see it.

**(a) The per-position quality axis** (`vessel_states`, migration 027, additive/nullable):

- **Provides:** a per-fix plausibility verdict + evidence. `position_quality`
  (`plausible` | `suspect` | `implausible`; **NULL = not yet checked**, distinct from
  `plausible` = checked-and-clean); `anomaly_flags` (the reasons that fired, from the
  **closed set** `{impossible_speed, on_land, teleport_return, jamming_zone}`);
  `implied_speed_kn` (evidence — the **position-derived** incoming speed, haversine/Δt,
  **never** the reported AIS SOG); `inland_km` (evidence — depth inside the land mass, NULL
  at sea); `quality_method`/`quality_computed_at` (provenance, `gnss-quality-v1`).
- **DB-level guarantees (migration 027 CHECKs):** `position_quality` is closed to the
  three values; `anomaly_flags` is array-contained (`<@`) in the closed flag set
  (`jamming_zone` is admitted by the constraint but is **reserved** — never emitted by the
  per-fix detector); `implied_speed_kn ≥ 0`; `inland_km ≥ 0`; and a **verdict↔reasons
  consistency** CHECK makes "a verdict without reasons, or reasons without a verdict"
  **unrepresentable** — `suspect`/`implausible` requires ≥ 1 flag; `plausible` requires
  no flags; a NULL verdict is unconstrained (not yet checked).
- **A separate axis from `maritime_events.classification`.** This is the **per-fix quality
  verdict** on `vessel_states` (is *this position* physically plausible?). It is **not** the
  **event tier** axis (`classification` = candidate vs an excluded confound) that A1/A2/A3
  and the GNSS episodes carry. Two orthogonal axes: one grades a position, the other grades
  an event. Do not conflate them.
- **Frozen state:** of **690,685** fixes — **686,488 plausible · 209 suspect · 3,988
  implausible** (0 unchecked; the layer is fully backfilled to the snapshot).

**(b) The two rules — R1 & R2 (frozen thresholds from `gnss_anomaly.py`).** Independent by
design — each catches a spoofing mode the other misses:

- **R1 — impossible kinematics.** Implied speed over consecutive fixes (shared
  `haversine_km`). A **dual condition** guards every speed threshold: it fires only when the
  jump distance is **also ≥ 500 m** (`JUMP_MIN_DISTANCE_M`) — this immunises the rule
  against short-Δt GPS jitter (G0/V3: kills 10–11 % of 40–100 kn noise, < 1 % of the real
  > 100 kn teleports). Verdicts: incoming **> 60 kn** (`SPEED_HIGH_KN`, covers > 100)
  → **implausible**; **40–60 kn** (`SPEED_SUSPECT_KN`) → **suspect**, *unless* the vessel is
  a **fast craft** (`passenger`/`high_speed_craft`) at ≤ 60 kn (`FAST_CRAFT_ALLOWANCE_KN`)
  — exempt in that band, never above it. Incoming **and** outgoing both > 100 kn
  (`SPEED_IMPOSSIBLE_KN`) additionally raises the **`teleport_return`** flag (jumped far and
  snapped back — the cleanest signature). Reported AIS SOG is **never** a rule input (it
  carries the `102.2`/`1023` "not available" sentinel and zeros under spoofing;
  `clean_sog` nulls the sentinel wherever SOG is echoed as evidence).
- **R2 — union-boundary inland depth.** A fix inside the seven-country land mass is on land
  — impossible for a ship beyond a harbour fringe. Depth is distance to the **dissolved
  union boundary** (`geo_area.AreaOfInterest.inland_depth_km`, internal borders dissolved):
  **> 5 km → implausible** (`INLAND_IMPLAUSIBLE_KM`); **2–5 km → suspect**
  (`INLAND_SUSPECT_KM`); **< 2 km → plausible** (quay/port). Verdict composition is
  **worst-wins** across R1 and R2; `anomaly_flags` is their union.
- **R1 and R2 are complementary, NOT redundant.** R1 catches teleports/scatter; R2 catches
  a track whose *shape and speeds are all realistic* but is coherently displaced onto land.
  The proof is the **Lebanon coherent-offset finding** (G0/V1): a whole track offset inland
  — **R2 catches it, R1 is silent** (no impossible segment ever forms). Neither rule alone
  would cover both modes. (Documented residual R2 blind spot: the union's outer edge
  includes land borders with non-AOI neighbours — Turkey–Iran, Iraq–Saudi, Israel–Egypt
  near Rafah — so a fix coherently offset to within ~2 km of such a border reads a small
  depth and evades R2; scatter there is still caught by R1.)

**(c) The GNSS-anomaly episodes** (`maritime_events` `event_type='gnss_anomaly'`, migration
028; detector `gnss_anomaly_events.py`). Per-vessel **episodes** = contiguous runs of
implausible fixes (the same grouping discipline as A1's gaps; a run breaks on a recovery to
a plausible fix or a > 3600 s break, `EPISODE_GAP_TOL_S`). Each episode gets a **three-way
classification** (the `classification` tier) with its **subtype in `detail.gnss_type`**
(exactly as A3 puts `change_type` in `detail`):

- **`candidate`** (`gnss_type='individual'`) — a single vessel with a **coherent plausible
  track punctuated by implausible fixes**: a real vessel spoofed/jammed (actor **or**
  victim → an investigation lead).
- **`area-jamming`** (`gnss_type='area'`) — an **environmental-jamming victim**: the episode
  sits in a cell × hourly window with **≥ 10 distinct implausible vessels**
  (`AREA_MIN_VESSELS`; Step-0 area cells peaked 15–112 vessels/cell·hour, the next tier
  ≤ 9, so 10 sits in a clean gap).
- **`glitch`** (`gnss_type='glitch'`) — a source with **no plausible track ever** (all its
  fixes implausible): a fixed/test/malfunctioning transmitter (e.g. `ELSEC_TEST`) or a
  single-fix on-land ghost.

The **frozen boundaries** (from `classify_episode`, precedence **glitch > area >
individual**): `glitch` ⇔ the source has no plausible track at all; `area` ⇔ the episode's
cell × hour has ≥ 10 distinct implausible vessels; `individual` otherwise. On the frozen
snapshot: **805 episodes = 276 candidate (individual) · 504 area-jamming (area) · 25 glitch**.

**Only `candidate` (individual) enters the composite.** Migration 029's admitting clause is
`event_type='gnss_anomaly' AND classification='candidate'` — mirroring the existing
`ais_gap … candidate` clause — so **`area-jamming` and `glitch` are composite-excluded FOR
FREE** by the same `= 'candidate'` condition that already excludes A1's
outage-excluded/edge/stationary. The **victim/actor distinction is made structural**: an
environmental-jamming victim and a faulty transmitter never enter the actor-centric
composite; only a real vessel whose own coherent track was spoofed/jammed does.

**(d) A1's fifth confound — `jamming-excluded`** (migration 030). Documented in §2.4: a gap
the G layer explains (spoofed positions transmitted during it, and/or an active area-jamming
zone). It is placed **high in A1's waterfall** (`outage-excluded > jamming-excluded >
stationary > edge > candidate`) and is a **deliberately distinct value from
`outage-excluded`** — provenance-axis cleanliness, so a study filtering feed-outage cannot
silently mix externally-induced jamming with feed drops. This is a **methodological
precedent** for future layers: when a new exclusion reason is mechanistically distinct, it
earns its own honest label rather than overloading an existing one.

**(e) The two zone products + the map (G5).** "Where GPS was being denied," as 0.1° cell ×
hourly-window distinct-vessel counts (`/maritime/jamming-zones`, served from persisted data
— no detector re-run; a Leaflet choropleth on the maritime tab):

- **`broadcast`** — **where spoofed positions LAND** (built from implausible broadcasts; the
  classification input). Confirmed **naturally disjoint from feed silence** (G4-pre:
  2/3,988 implausible positions fall in any outage window, 0 in the open one), so it needs
  **no outage subtraction**. Frozen peak: the **Amman-airport cell (0.1° cell 317,359,
  centre 31.75/35.95)** at **112 distinct vessels** in the 2026-07-19 15:00 window (22 cell
  × windows reach the ≥ 10 area threshold).
- **`affected`** — **where vessels ACTUALLY were** when jamming began (the last-good position
  before each episode). Because it reasons from the *onset* of implausibility, it **must
  structurally subtract feed-outage windows** (`reconstruct_outage_windows`, the A1
  machinery imported not reimplemented) — **a feed-outage silence must never masquerade as a
  jam**. This outage subtraction is a **binding correctness requirement**, verified against
  the 44.5 h+ frozen outage window: it contributes **zero** affected cells. Both variants
  bucket through the same tested pure aggregator (`affected_zone_cells`); `broadcast` simply
  passes an empty outage-window list.

**(f) The Amman decoy — one number, five measures (disambiguation).** The dominant spoofing
signature in the AOI is a mass spoof-to-fixed-point at the Amman-airport cell. It is
reported **five different ways across the build**, which look contradictory but each counts
a **different thing**. On the frozen snapshot:

| Build phase — measure | What it counts | Frozen-DB value |
|---|---|---|
| **G0 · raw implausible positions** | individual implausible **position fixes** landing in the decoy | **1,188** in the Amman cell; **726** on the exact modal point (31.7171, 35.9994). *(The G0 assessment cited **169** on its pre-backfill sample; superseded by the full-window backfill — see note.)* |
| **G4a · area episodes aggregated** | area-jamming **episodes** attributed to the decoy | **380** episodes land in the Amman cell (of 504 area episodes). *(A G4a Step-0 comment cites **156** vessels/cell·hour as the peak; the frozen recompute makes that peak **112**, = the broadcast peak below — see note.)* |
| **G4a · `decoy_match` episodes** | episodes whose fixes hit a shared **on-land** area cell | **456** (`decoy_match=true`) |
| **G5b · broadcast distinct-vessels-in-cell** | distinct **vessels** in the peak broadcast **cell × hour** | **112** (cell 317,359 @ 2026-07-19 15:00) |
| **G5a · visible-cluster distinct vessels** | distinct **vessels** across all area-jamming episodes (the **rendered cluster** on the map) | **211** |

So they are **not** contradictory: *raw positions* (1,188 / 726) ≠ *aggregated episodes*
(380 / 456) ≠ *distinct vessels in one cell×hour* (112) ≠ *distinct vessels in the rendered
area cluster* (211). A citation must name **which** measure it uses.

> **Note on the two pre-freeze figures.** The **169** (G0) and **156** (G4a Step-0) were
> recorded earlier in the build and do **not** reproduce on the frozen snapshot: G0's raw
> count predates the full-window quality backfill (the frozen cell now holds 1,188 raw
> implausible fixes / 726 on the modal point), and the "156 vessels/cell·hour" peak in the
> `gnss_anomaly_events.py` / migration-028 comments is a stale Step-0 reading of the very
> quantity the broadcast zone now measures as **112**. The three frozen-verified figures
> (112, 456, 211) are read directly from the reference DB.

### 2.8 The composite — cross-pattern IMO view (views, migrations 026 + 029 + 037)

- **Provides:** the capstone — per-hull aggregation of the **four** detectors into one
  picture, **stitched by IMO**. Two **live SQL views**:
  - `maritime_hull_events` normalizes each event to a hull key
    **`CASE WHEN spine.imo_quality='malformed' THEN mmsi ELSE COALESCE(event.imo,
    spine.imo, mmsi) END`** (migration 036, IMO-2) — so a re-flagged hull's events under
    **both** MMSIs unify under one IMO (an IMO-null vessel falls back to MMSI, honestly
    **not** stitched across re-flags), **and a `malformed` IMO also falls back to the MMSI**
    — a decode artifact is not a hull identity (§2.9c). A rendezvous is attributed to
    **both** participants. **Only the candidate/signal tier is composed** (`ais_gap`,
    `gnss_anomaly`, and `imo_integrity` where `classification='candidate'`;
    rendezvous/identity_change are candidate-only). **`port_visit` is deliberately NOT a
    pattern type and never will be** — admitting it would flip 96 hulls from 1 to 2
    `n_pattern_types` at even the tightest bar, manufacturing HIGH-rank hulls out of routine
    commerce, and would dilute a **scarcity** signal whose live maximum is 4 (§2.11k).
  - `maritime_hull_composite` GROUP BY hull → per-pattern counts (incl. `n_gnss_anomaly`,
    migration 029), **`n_pattern_types`** (the composite signal, `count(DISTINCT
    event_type)`), event span, MMSIs.
- **Now 4-pattern (migration 029, G4b).** Admitting `gnss_anomaly` candidates makes
  `n_pattern_types` reach **4** — a hull that went dark **and** met another **and** changed
  identity **and** was spoofed/jammed. The `gnss_anomaly` **individual** tier is a genuine
  fourth pattern, but carries a **LOW underlying baseline** (ordinal 1, deliberately below
  `ais_gap`'s 2): a spoofed vessel is victim-leaning, so on its own it must not inflate a
  hull to high rank — the victim/actor distinction extended into ranking. It **does** count
  toward `n_pattern_types` and multi-pattern rank.
- **Now admits `zone_dwell` too (migration 037, ZONES-3) — post-snapshot 2026-07-26.** The
  strong subset of infrastructure dwell (§2.10e — `safety_zone_intrusion` only; standoff deferred,
  field never) enters via the same one-disjunct `='candidate'` pattern. `zone_dwell` is the **sixth**
  composable `event_type` — `imo_integrity` (§2.9d) was already the fifth, though it remains a **latent
  0-instance** guard, so `zone_dwell` is the **fifth pattern with live instances** (the ZONES-4 brief's
  "5-pattern" counts those; the disjunct ceiling is six). **Measured admission effect** (drift-free
  counterfactual, with vs without `zone_dwell` at one instant): **1,539 → 1,541 hulls**, **61 → 62
  multi-pattern**; **4** hulls gained a `zone_dwell` pattern — **2 lone** (LOW, no inflation) and **2
  promoted medium → high, both genuine and explained**: `9689201` (gap + rendezvous + intrusion → **3**
  patterns) and `9732759` (grey-flag + gap + intrusion → **2** patterns + grey). The **capstone
  IMO 9895915 is unchanged at 4** (no zone_dwell). *(The ZONES-3 brief cited 1,529 → 1,531 — the same
  delta at an earlier instant; the DB has since grown, the delta has not.)*
- **Structural property this exposed (stated so it stays legible).** Because rank derives from
  `count(DISTINCT event_type)`, **every new pattern layer mechanically promotes hulls that already
  carry patterns** — a hull with a gap that now also shows a dwell rises. This is the composite's
  **intended behaviour, not drift**: a hull exhibiting more distinct patterns *is* a stronger lead.
  It must be **stated** so a future layer's rank changes read as the designed effect, not unexplained
  inflation — and admission is gated to the strong subset precisely so routine dwell cannot inflate
  ranks wholesale.
- **Ranking (`composite.py`, computed in the API):** ≥ three pattern types → high; two types
  with a grey/sanctions flag or a high underlying event → high; two benign types → medium;
  a lone event carries its own rank (a lone GNSS-individual episode → **low**; a lone `zone_dwell`
  intrusion → **low**, ordinal 0 — it must add a pattern type to an *already*-patterned hull to
  matter). Reuses A3's `GREY_FLAGS` (flags-of-convenience + Russia) — **one source of truth, no flag
  list duplicated into SQL**.
- **View, not materialized:** the aggregation is a live view over **1,345** hulls (no
  staleness, no refresh) — read-only over the events, **no new detector, no timer**. Frozen
  state: **60 multi-pattern hulls** (54 two-type · 5 three-type · **1 four-type**); **47**
  hulls show `gnss_anomaly` alongside ≥ 1 other pattern.

### 2.9 IMO integrity — the identity-spine quality axis (`vessel_identity` + composite + `maritime_events`, migrations 035/036) *(post-snapshot, 2026-07-25)*

The IMO number is the spine the composite stitches evidence on (§2.8, §3), but it is
**self-reported AIS static (type 5), therefore spoofable** — the shadow fleet practises
"IMO laundering". This layer is the identity-spine analog of the GNSS quality axis (§2.7):
a per-identity **internal-consistency verdict** on each stored IMO, **flagged, never
deleted**. Sized by measurement, not threat assumption: on live data the actual poisoning
of the strongest leads is **~0** (see (e)) — so it is **hygiene plus a structural guard**,
built lean.

**(a) The per-identity quality axis** (`vessel_identity`, migration 035, additive/nullable):

- **Provides:** a per-identity verdict + reasons. `imo_quality`
  (`valid` | `checksum_fail` | `malformed` | `contested`; **NULL = not-yet-checked, and the
  resting state of a NULL-IMO identity** — "no IMO reported" ≠ "reported and checked");
  `imo_flags` (the reasons, from the **closed set** `{checksum_fail, wrong_length,
  non_numeric, placeholder, reflag_clean, mmsi_collision, mmsi_multi_imo,
  dimension_conflict}`); `imo_method`/`imo_computed_at` (provenance, `imo-integrity-v1`).
- **DB-level guarantees (migration 035 CHECKs):** `imo_quality` is closed to the four
  values; `imo_flags` is array-contained (`<@`) in the closed set — including the **latent
  guards** `mmsi_collision`/`mmsi_multi_imo`, **admitted by the constraint but not emitted**
  (0 instances — exactly as the GNSS axis admitted `jamming_zone` before G3 emitted it); and
  a **verdict↔flags consistency** CHECK. That CHECK carries a **documented reconciliation**:
  the spec required both that `reflag_clean` be *emitted* and that its verdict be `valid`,
  which collides with a literal "valid ⟹ no flags"; it is resolved as **`valid` ⟹
  `imo_flags ⊆ {reflag_clean}`** (the one benign annotation), any non-valid verdict ⟹ ≥ 1
  flag, NULL unconstrained. **`cardinality()` is used, not `array_length()`** — the G1
  lesson: `array_length('{}',1)` returns NULL and would let an empty-flags non-valid row
  slip the guard.
- **A THIRD distinct axis.** This grades an **identity's IMO** (is the number well-formed and
  non-conflicting?). It is **not** `vessel_states.position_quality` (the per-fix GNSS axis,
  §2.7) and **not** `maritime_events.classification` (the event tier). Three orthogonal axes
  — a position, an event, an identity — never conflated.
- **Live state (2026-07-25):** of **3,776** identities — **2,875 valid · 10 checksum_fail ·
  36 malformed · 0 contested**; **854 NULL-IMO** (no IMO reported, ~23 %) stay `imo_quality`
  NULL. Invariant: the backfill **never stamps a NULL-IMO row** (`imo_method` NULL ⟺ imo NULL
  immediately post-backfill; on the live feed a handful of fresh identities sit un-backfilled
  until the next run). *(Updated 2026-07-29: of **3,442** imo-bearing identities of 4,452 —
  **3,379+ valid · 12 checksum_fail · 39 malformed · 0 contested**; ~1,009 NULL-IMO. The
  2026-07-25 figures are a snapshot, not a ceiling.)*
- **Refresh (post-snapshot, 2026-07-29):** the backfill is no longer manual — it runs as a
  **second `ExecStart` in `scraplan-identity.service`**, i.e. every **30 minutes** right after
  the A3 detector (which can surface identities worth stamping in the same cycle). Measured
  **0.61 s** for a full recompute over the whole table, and the WHERE-changed write guard makes
  a no-op run write nothing. It was left manual until then, and that is precisely how it drifted
  four days and left **509** imo-bearing identities unstamped.

**(a.1) The axis is idempotent but NOT append-only — a verdict can be re-evaluated when the
spine gains evidence, by design.** Measured 2026-07-29: a re-run changed **517** rows while only
**509** were newly stamped, so **8 already-stamped rows were revised**. Investigated against the
pre-run state (extracted from that morning's `pg_dump`, read-only) and the cause was unanimous:

- In all 8 the **IMO and the verdict were unchanged** (`valid` → `valid`). The *only* change was
  `imo_flags`: **NULL → `{reflag_clean}`**.
- `classify_shared` requires **`len(members) ≥ 2`** — an IMO carried by more than one MMSI. All
  8 were **single-MMSI** at the previous stamp (2026-07-25 08:27:38), so the shared-IMO path
  could not fire and correctly emitted no flag.
- Every one of the 8 then **gained a same-IMO sibling MMSI after that stamp**: five siblings did
  not exist in the spine at all that morning, and four existed but had their identity fields
  last updated **2026-07-26/27** — i.e. after the previous computation. IMO `9241346` gained
  **two** siblings (`314163009` 07-27, `314163000` 07-29).
- **Not one case produced a different output from identical input.** Idempotency was confirmed
  directly: two back-to-back runs changed **7** rows then **0**.

So a re-run is *not* purely additive, and a stored verdict or flag set is **as of its
`imo_computed_at`**, not permanent. This is the intended behaviour of a recompute-and-replace
axis over a parse-forward-only spine: a hull that looks single-MMSI today is legitimately
annotated `reflag_clean` tomorrow when its next MMSI appears. **A study must therefore cite
`imo_computed_at` alongside any `imo_quality`/`imo_flags` claim** — and must not treat an absent
`reflag_clean` as evidence that no re-flag exists, only that none was visible when it was
stamped.

**(a.2) The latent collision guard is no longer latent — first live `mmsi_collision`
(2026-07-29 16:00:26Z).** Every prior run reported *"imo_integrity collision events: 0 (latent
guard)"*. One now exists, and it arrived through exactly the escalation §2.9(a.1) predicts —
**the same identity moved single-MMSI → `reflag_clean` → `contested` inside one day.**

| Measured | Value |
|---|---|
| IMO | **9221566** — `CELESTYAL DISCOVERY` (cruise) |
| Conflicting MMSIs | **256667000** (Malta) · **310872000** (Bermuda) |
| Overlap window | **2026-07-25 21:42:16 → 2026-07-29 15:20:50** (~3.7 d) |
| Verdicts | both `valid` → **`contested`**, `imo_flags` → `{mmsi_collision}` |
| Event | `event_type='imo_integrity'`, `classification='candidate'`, `method='imo-integrity-v1'` |
| `dimension_conflict` | **false** — both report **203 m × 28 m**, and the same name |
| Fixes | Malta **271** over 4 days · Bermuda **336** over 9 days |
| True simultaneity | both transmitted **within the same hour** (2026-07-29 15:00 — Malta 6 fixes, Bermuda 8) |
| Closest-in-time pair | **8.5 min apart, 4.13 km apart** |

**Bounded reading — this is the weakest form of the signal, and the layer says so.** The rule
that fired is purely temporal (overlapping windows on one IMO ⟹ CONTESTED), and it fired
correctly: while both MMSIs are live, the IMO **cannot** be attributed to one hull. But every
corroborating axis points *away* from false attribution — identical name, identical dimensions
(`dimension_conflict=false`), and a 4.13 km / 8.5 min separation that is simply **one cruise ship's
own motion** (~15 kn ⇒ ~3.8 km in 8.5 min), not two vessels. The consistent explanation is a
**dual broadcast during a Bermuda → Malta re-flag**, i.e. benign — which is why the event is a
`candidate`, never a verdict, and why `dimension_conflict` exists as a separate corroboration
axis rather than being folded into the verdict.

**Do not publish this as a spoof or a false-attribution finding.** It is a genuine
*attribution-ambiguity* window on one hull, with no corroborating conflict. It also demonstrates
the operational point: MMSI `310872000` was one of the eight rows revised in (a.1) — it held
`reflag_clean` at 15:15 and `contested` by 15:44, on the same data source, purely because the
overlap became visible. **Cite `imo_computed_at`.**

**(b) Two verification weapons, asymmetric.** Both pure functions in `imo_integrity.py`:

- **The IMO check digit (the strong weapon).** The first six digits × weights **7,6,5,4,3,2**,
  summed; the last digit of that sum must equal the seventh. Well-formedness first (exactly 7
  digits, numeric, in `[1000000, 9999999]`, not a placeholder), then the check digit.
  Separate malformed flags for `wrong_length` / `non_numeric` / `placeholder`.
- **Dimension consistency (the weak weapon, documented honestly).** For an IMO carried by
  several MMSIs, do the AIS static `length_m`/`beam_m` agree? **Coverage is only ~34 %** of
  identities (dimensions are parse-forward-only), and **zero contradictions were observed**
  among shared IMOs (the comparable pairs all agreed to the metre), so the threshold is
  **under-determined**. It therefore **never fires alone** — `dimension_conflict` only
  **corroborates** a collision (b→(d)), it never raises one. Stated as a coverage limit, not
  a silent gap.

**(c) The hull-key rule — the layer's core decision** (migration 036). The `imo_quality`
verdict changes how the composite (§2.8) keys a hull, three ways:

- **`malformed` → falls back to the MMSI key.** The value is not an identity at all (a decode
  artifact), so it keys on the MMSI exactly as a NULL IMO does — nothing dropped. On live
  data **10 composite hulls** were keyed on a malformed IMO; all re-key to their own MMSI
  (all single-MMSI, so a relabel, not a split — total hull count unchanged). Were a generic
  artifact ever **shared** across two transmitters (e.g. `65535`), the fallback would **split
  the phantom hull into its real constituents** — the structural guard (proven in tests).
- **`checksum_fail` → keeps its key, annotated.** A well-formed 7-digit value that fails only
  the check digit is plausibly a **real, mis-decoded identity that still consistently
  identifies one hull**; re-keying would **fragment that vessel's own evidence**. So it stays
  the key and is surfaced as **"spine: checksum-fail"** (composite/search/alert cards).
- **`reflag_clean` / shared valid IMO → untouched.** The stitching is **intended** (A3-detected
  re-flags — §2.6); fragmenting it would destroy working intelligence. **Verified regression:**
  hull `9232931` (MONOLIT/NARCISSUS, a real re-flag) and the capstone **IMO 9895915** keep
  their IMO keys under this change.

**(d) The latent collision guard** (`event_type='imo_integrity'`, migration 036 disjunct). A
genuine **`mmsi_collision`** — time-overlapping MMSIs claiming one IMO — writes a
`maritime_events` row (`classification='candidate'`, subtype `detail.integrity_type=
'mmsi_collision'` + the conflicting MMSIs + overlap window + dimension evidence), entering the
composite as a **fifth pattern type** via the same `= 'candidate'` disjunct. **This is the
false-attribution signature A3 structurally silences**: A3 rejects heavy time-overlap as "a
likely IMO collision (two different vessels), not a re-flag" and emits nothing — so the guard
exists precisely to catch what A3 refuses. **Zero instances today** — a guard, not a populated
detector (`contested` count 0).

**(e) Exposure (the "so what").** Of the **62** multi-pattern composite hulls (the analyst-
facing leads), exactly **1** rests on a questionable spine (`9789930`, checksum_fail; a decode
typo, single-source), **0** on a malformed key (those re-keyed to MMSI), and **0** on a
collision. The capstone `9895915` spine is `valid`. The strongest-leads poisoning is
**negligible** — the layer's value is the hygiene axis + the structural/latent guards, not a
response to a live threat.

### 2.10 Surveillance zones × dwell — infrastructure dwell (`backend/data/zones_aoi.json` + `maritime_events` `event_type='zone_dwell'`, migration 037) *(post-snapshot, 2026-07-26)*

A vessel loitering over offshore infrastructure is meaningful **without a statistical
baseline**. Unlike a before/after bracket, a feed gap **truncates** a dwell rather than
falsifying it, so dwell survives the ~55 % outage as a **lower bound**. Roadmap priority 4,
built in three phases (asset → on-query dwell → events + composite), documented here (ZONES-4).

**(a) The reference asset** (`backend/data/zones_aoi.json` — committed, dated, in-process, the
`airports.py` precedent, **NEVER fetched at runtime**; loaded by `backend/zones.py`, an
`lru_cache` fail-fast singleton reusing `geo_area.haversine_km`, **no second geo stack**):

- **36 zones — 7 gas-field centroids + 29 platform points**, East-Med theater. Gas fields from
  **GEM GOGET** (CC-BY-4.0 reservoir centroids: Leviathan/Tamar/Karish/Tanin [Israel] ·
  Aphrodite/Calypso/Glaucus [Cyprus]); platforms from **OpenStreetMap** (ODbL: **26** Egyptian
  Nile-Delta · **3** Israeli Levantine). Per-feature provenance — `source`, `license`,
  `source_vintage`, `source_ref` (the gem.wiki / openstreetmap element URL). *(The GOGET `.xlsx`
  is email-gated; the seven centroids were read from GEM's public gem.wiki field pages — same
  tracker — each feature cited individually.)*
- **ODbL obligation carried:** attribution *"© OpenStreetMap contributors"* in the manifest **and**
  every OSM feature individually tagged `source='OSM'` with its element URL, so attribution +
  share-alike are traceable per feature.

**(b) The evidence-strength axis — the layer's defining honesty.** Every zone (and every dwell
session) carries `radius_basis` and `coord_precision`:

- **platform → `sourced`, 500 m** (UNCLOS Art. 60(5) offshore-installation safety-zone radius) on a
  **surveyed point**.
- **gas_field → `assumed`, 5 km** on a GEM **reservoir centroid** — not a structure; ~20–35 km
  cross-source centroid ambiguity (GEM vs Wikipedia).

So a field dwell is **visibly weaker evidence** than a platform intrusion — the distinction rides
in the data, not in a rank number. One OSM node (*"Tamar Gas Platform"*, 33.17/33.62) is marked
**`detectable: false`** — an upstream `fixme` ~100 km from the real Tamar platform (a zone over open
water); kept as a **faithful record** but excluded from dwell (real Tamar is covered by its GEM
gas_field zone). **28 of 29 platform nodes carry no accuracy warning.**

**(c) Three signal classes — the ZONES-1 finding that inverted the design.** Measured: **~half the
platform 500 m cores are empty** (**15 of 28** detectable over the 30-day window; ZONES-1 measured
18/29 over its narrower window) while **none is isolated** — the law keeps the safety zone clear
(Art. 60(5)). That inverts the reading, so the layer produces three classes, not one threshold
(over the 30-day window):

- **`safety_zone_intrusion`** (dwell inside the 500 m core) — **rare by construction, strong on its
  own: 12**.
- **`standoff_dwell`** (500 m–3 km working band) — abundant, largely legitimate: **230**.
- **`field_dwell`** (a gas_field's 5 km assumed centroid) — the single weakest class: **66**.

**Merging them into one threshold would have buried the rare in the routine.** Speed is
position-derived `implied_speed_kn` (never crew-entered SOG; the 102.2 kn sentinel filtered); the G3
rule drops implausible fixes; sessions are 30-min gap-split; **duration is a lower bound**
(`duration_is_lower_bound`).

**(d) Gating that ranks down, never deletes** (the on-query annotation surface `/maritime/zone-dwell`,
pure logic in `backend/zone_dwell.py`). standoff/field carry the full stack: subtract the A2
port/busy **anchorage cells** (§2.5, **reused not duplicated**), exclude short (< 1 h), and **DEMOTE —
never drop —** a self-reported low-suspicion type. The gated-out set stays queryable. **Exclusion
accounting** is reported per zone (`type_gated` / `in_anchorage_mask` / `below_duration_threshold`),
distinguishing **"excluded by a self-reported type" from "no dwell observed"** (the DRAUGHT-1 lesson).
Over 30 days: **308 sessions → 64 leads · 28 demoted · 216 excluded**.

**(e) Admission policy — only the strong subset** (`scripts/detect_zone_dwell.py`, the
`detect_rendezvous.py` discipline: `--dry-run`, recompute-and-replace idempotent, time-bounded).
Written to `maritime_events` as `event_type='zone_dwell'`, `classification='candidate'`:

- **`safety_zone_intrusion` → admitted** (**12**; surveyed points, rare, strong). Migration **037**
  adds one sig-CTE disjunct (the **G4b pattern**, mirroring 029/036) + an `n_zone_dwell` count
  column; `classification='candidate'` is already in `maritime_events_class_ck` (no new CHECK; there
  is no `event_type` CHECK). The composite timeline gains a `_zone_dwell_summary` branch so a
  zone_dwell does **not** fall through to `_idc_summary` (the G4b lesson).
- **`standoff_dwell` → DEFERRED** pending a sound per-zone familiarity baseline (below).
  **`field_dwell` → never** (weakest class).
- **The allowlist, rejected.** ZONES-2 had proposed allowlisting the busiest platform (it carried 11
  of the 12 standoff leads). Rejected on **principle** — this project flags-never-deletes and
  filters-at-consumption; an allowlist silences a location *a priori* — and on **risk**: a loading
  point is exactly where an undeclared transfer would occur, so allowlisting it would **blind the
  layer at its single highest-activity site**. The chosen alternative is an **empirical per-zone
  baseline** (`backend/zone_baseline.py`, the `coverage_mask.py` mould): vessels-seen + type-mix +
  duration-distribution; a session is *unfamiliar* if the vessel is new to the zone, the type novel,
  or the duration > p90.
- **Baseline readiness — a VISIBLE state, not dormant** (the ZONES-4 dormancy fix). Sufficiency bars:
  **≥ 30 days AND ≥ 10 vessels AND ≥ 20 sessions** (a floor below priority 6's ~90-day target).
  Currently **0 of 17 trafficked zones are sound** — the **`days` bar binds** (best span **13.6 days**,
  gap **16.4**; zones meeting days/vessels/sessions = **0/1/2**). Surfaced on `/maritime/zone-dwell`
  (`readiness`) and the runner's output, so standoff admission **announces itself** the moment the
  window is long enough — no dormant logic without a wake-up trigger.

**(f) What the class actually captures (measured, not assumed).** The 12 admitted
`safety_zone_intrusion` events involve **pleasure_craft (7) · other/offshore-support (4) ·
high_speed_craft (1)** — **recreational and small-craft activity dominates, not commercial
suspicion**. The class flags *"a vessel loitered inside a platform's legal safety zone"*, which small
craft do; read it as a **lead surface over all such activity**, not a shadow-fleet detector. It
matters in the composite only where an intrusion sits on a hull that **also** carries other patterns
— see §2.8.

### 2.11 Port visits — the port as a positive fact (`backend/data/ports_aoi.json` + on-query `/maritime/port-visits`, **no migration, no events**) *(post-snapshot, 2026-07-27)*

The port was **negative space** everywhere else in this base: A2's `port_mask`/`busy_mask`
**subtract** it (§2.5), the zones layer subtracts anchorage cells (§2.10d), M1 classifies
territorial sea (§2.1), and A1 **down-classifies** a berthed vessel's silence to `stationary`
(§2.4). Nothing emitted a positive *"vessel X was inside port Y from t1 to t2"*. This layer
does — and that is its whole contribution: it **converts a signal A1 discards as a confound
into a positive measured fact**. Roadmap priority 5, built in two phases (asset+loader →
on-query sessionization) and documented here (PORT-4).

It is deliberately **the weakest-claiming layer in the base**: a measurement-and-context
surface answering *"where was this hull before and after its gap"*, **not a lead generator**.
It writes no events, enters no composite, feeds no alert, and carries no suspicion rank —
each a **measured** decision, below. That is a genuine demotion from the roadmap's framing,
and it is what the data supports.

**(a) The reference asset** (`backend/data/ports_aoi.json` — committed, dated, in-process,
the `airports.py`/`zones.py` precedent, **NEVER fetched at runtime**; loaded by
`backend/ports.py`, an `lru_cache` fail-fast singleton reusing `geo_area.haversine_km`, **no
second geo stack**). **190 features**:

- **180 from the NGA World Port Index (Pub 150)** — filtered to the collector bbox
  (12–42 °N, 24–63.5 °E). **US-Government public domain**, quoted on every feature from the
  publication's front matter: *"© COPYRIGHT 2015 BY THE UNITED STATES GOVERNMENT / NO
  COPYRIGHT CLAIMED UNDER TITLE 17 U.S.C."* — **no attribution obligation, no share-alike**,
  materially cleaner than every other asset in this base. Per-feature provenance is the WPI
  `globalId` GUID plus the stable `portNumber` in `port_id`. **The API payload carries no
  edition and no `Last-Modified`**, so the asset stamps its own `captured_at` and **cites no
  edition** (the printed volume is the 24th Edition, 2015).
- **10 from OpenStreetMap** (ODbL-1.0 — attribution **and** share-alike, carried per feature
  with the `openstreetmap.org` element URL in `source_ref` and the originating tags in
  `osm_tags`, exactly as `zones_aoi.json` does).

By country: Turkey 53 · Greece 28 · Egypt 25 · Iran 12 · Saudi Arabia 10 · UAE 9 · **Cyprus
8** · Iraq/Kuwait/**Lebanon**/Qatar 6 each · Israel 5 · Bahrain/Oman 4 · **Syria 3** · Yemen 3
· Sudan 2.

**(b) The Syria/Lebanon supplement — why it was targeted, not opportunistic.** WPI, the
public-domain spine, carries **zero Syrian ports** (no Latakia, no Tartus, no Baniyas), **one**
Lebanese entry (**Sayda**, `harborSize='V'` very small, `harborType='OR'` open roadstead) and
**no Beirut**. In a base whose AOI centres on Syria, the spine was **blind to its own coast**.
So the OSM supplement is aimed at exactly that hole: **Syria 0 → 3**, **Lebanon 1 → 6**,
adding Latakia, Tartus, Baniyas (Syria) and Beirut, Tripoli, Tyr, Jiyyeh, Al Abdeh (Lebanon),
plus two East-Med harbours WPI lacks (Vasilikos, Moni — Cyprus). All resolve by **both Arabic
and English** name (`بيروت`, `اللاذقية`, `طرطوس`, `بانياس`), and all three Syrian/Lebanese
**commercial** ports carry **measured traffic** — Tartus **49** · Beirut **45** · Latakia
**21** distinct vessels inside the radius over 16 days.

Selection was disciplined, and the exclusions are recorded in the manifest with their
measurements — an AOI-wide sweep returned 180 elements (134 named, non-marina) and **only 2
of the 59 non-duplicates were imported**:

- **Pure marinas excluded on the TAG, never the name.** Two candidates whose *Arabic* names
  read as ports (**Arwad** مرفأ أرواد, **Ras al-Basit** ميناء صيد راس البسيط) are OSM
  `leisure=marina` + `seamark:harbour:category=marina` and were dropped.
- **Inland "dry ports" excluded**, measured not assumed: Günaydın Group Lojistik Merkezi at
  **`inland_depth_km = 56.4`** (via `geo_area`), Riyadh Dry Port / SAUDIA CARGO / Khazaen Dry
  Port at 455–1,158 km from the nearest AOI coast. All are tagged `industrial=port`.
- **Terminal-level fragments excluded** — 7 Khalifa Port features, 4 Jebel Ali container
  terminals, 3 duplicate Ras Tanura nodes — which would fragment one port into overlapping
  circles and flood `merges_with`.

**(c) The three honesty fields — this layer's defining weakness, stated not buried.**

- **`radius_basis` is `'assumed'` on ALL 190 features.** No source in this asset publishes a
  measured harbour extent, so **a port radius is a search aperture, not evidence**. This is
  **weaker than the zones layer**, where **29 of 36** radii were `'sourced'` (the UNCLOS Art.
  60(5) 500 m safety zone on surveyed platform points). `RADIUS_BASES` is a **closed set of
  one**, so a future `'sourced'` claim must be a reviewed change. Scale from WPI
  `harborSize`: **V 1.5 · S 2.5 · M 3.5 · L 5.0 km** — and the **1.5 km floor is set by the
  coordinate precision, not by harbour size** (below).
- **`coord_precision`, per feature.** WPI positions are **degrees-and-minutes by publication
  design** — Pub 150 §3: *"The position of each port, expressed in degrees and minutes, is
  generally obtained from the best-scale chart available."* Verified against that claim: **356
  of 360** AOI coordinate components carry `seconds = 00` → **178 `arcminute`**; the two
  exceptions (**Al Fujayrah**, **Khawr Fakkan**) carry whole non-zero seconds and are labelled
  **`arcsecond`** rather than blanket-tagged. OSM features carry their own (**7
  `polygon_centroid`** · **3 `node`**). No datum is stated anywhere. That is **±~0.93 km lat /
  ±0.78 km lon at 33 °N** — and **108 of the 190 features sit at the 1.5 km floor**, so for
  the majority of the gazetteer **the positional uncertainty is comparable to the radius
  itself**. The radius there is an *uncertainty budget*, not an extent.
- **`merges_with` — attribution ambiguity returned as DATA.** **15 pairs across 26 features**
  lie within 6 km (tightest **Gebze ↔ Kaba Burnu at 1.40 km**; also Borusan Fertilizer Jetty ↔
  Gemlik 1.41, Antalya ↔ Antalya Offshore Terminal 2.37, Haydarpasa ↔ Istanbul 4.59). Any
  usable radius merges distinct terminals, so `ports_containing` returns **every** match and
  each session carries **`candidate_ports`** — never an arbitrary first pick. Proven reciprocal
  on the live surface: of Istanbul's 66 visits **34** name both Istanbul and Haydarpasa, and of
  Haydarpasa's 37 visits **36** do, **with the queried port leading in each direction** and the
  same hulls claimable from both sides. That *is* the ambiguity, expressed rather than resolved.

**(d) The visit bar, and the transit/visit funnel as exclusion accounting.** A presence test
alone measures **traffic, not visits**: **61 %** of raw circle-crossings are transits, with a
raw p50 of **0.37 h**. So a **visit** is a gap-split session with **≥ 2 fixes**, **duration
≥ 1 h**, a **contiguous low-speed span ≥ 2 h**, and a **majority** of speed-bearing fixes
below **1 kn**. That population's dwell distribution is credible where the raw one is not —
measured over the whole gazetteer: **p25 4.1 h · p50 8.9 h · p75 18.2 h · p90 37.2 h · max
65.6 h**. Speed is **position-derived `implied_speed_kn` only**, never reported SOG and never
`nav_status` (both crew-entered — the project's distrusted-field discipline); SOG rides along
as corroboration with the **102.2 kn** sentinel filtered. A **`None` implied speed never
counts as slow** — absent evidence must not manufacture dwell. Each dropped crossing is
charged to the **first** bar it fails (`single_fix` / `below_min_duration` /
`below_low_speed_span` / `not_majority_low`) and reported per port, so a survivor count is
never published alone (the DRAUGHT-1 lesson).

**Boundary observation is deliberately NOT a bar.** Gating on "arrival and departure both
seen" would discard **~82 %** of real visits for a *coverage* reason — conflating **blindness
with absence**, which is the error this whole base is built to avoid.

**A session ends when the vessel leaves the radius**, not only on a time gap. Splitting on
time alone merged a depart-and-return into one session and then misreported it three ways:
`duration_s` spanned the excursion; the interval **bridging** it was credited to
`low_speed_span_s` whenever the returning fix was slow, so **a fast transit outside the radius
counted as berthed dwell** and could push a non-visit over the bar; and the excursion's own
fixes sat inside `[start, end]` where they cannot flank it, **losing an observed
arrival/departure pair**. Fixed at PORT-4; the effect on the headline is in (f).

**(e) The stricter quality filter — and why NULL must pass.** This layer drops **`suspect` as
well as `implausible`**, deliberately **stricter than the standard G3 consumption rule**
(§2.7), because **a harbour radius is exactly where on-land GNSS decoy positions land**:
PORT-pre measured 79 suspect-tier fixes across 38 sessions, concentrated at **Haifa** — 26 of
them flagged `on_land` *inside* a 3 km harbour core — in a theater where **46.2 %** of A1
`jamming-excluded` gaps start within 15 km of a port. Live, the tightening removes **203**
fixes across the nine sampled ports, **173 of them at Haifa**, matching the G layer's finding
that jamming here is coastal.

But **NULL passes**. `position_quality` is written by a timer running every **15 minutes**, so
the newest fixes are always NULL and NULL means **"not yet checked"**, not "bad". Filtering it
would systematically **truncate in-progress sessions** and manufacture false *"no departure
observed"*. The rule is therefore `position_quality NOT IN ('suspect','implausible')` **with
NULL passing** — `accept_fix()` is its single definition, the SQL mirrors it, the rule text is
returned on every response, and the dropped count is reported per session and per port so the
tightening stays **visible**.

**(f) Four first-class completeness states — the incomplete ones are the NORM.** Measured over
the **full 190-port gazetteer**, 384 h window, per-port radii (1.5–5.0 km, all `assumed`),
gap-split 6 h, low-speed 1.0 kn, span ≥ 2 h + majority-low, flank 6 h, pad 10 km — **1,690
visits over 695 vessels**:

| State | Count | Share | Meaning |
|---|---|---|---|
| `complete` | 306 | **18.1 %** | arrival **and** departure observed |
| `open_ended` | 282 | 16.7 % | arrival observed, **no departure** |
| `truncated` | 270 | 16.0 % | departure observed, **no arrival** |
| `presence_only` | 832 | **49.2 %** | **neither** boundary observed |
| *`arrival_ts_is_unknown`* | *1,102* | ***65.2 %*** | *the two entry-missing states* |

Stated plainly: **for the majority of sessions we know a vessel was present, but not when it
arrived or when it left.** Nearly half the time we see neither boundary. Each missing side
carries **why** — `outage` / `vessel_silence` / `window_edge` — determined against
**reconstructed `signal_events` outage windows**, reusing `gap_detect.reconstruct_outage_windows`
(the G3-addendum machinery, §4.5, **imported never reimplemented**), with the overlap seconds
recorded. `window_edge` is checked **first**: a boundary we never had the chance to observe must
not be blamed on the vessel or on an outage that merely happens to be nearby. The two sides are
attributed **independently** (`entry_outage_gap_s` / `exit_outage_gap_s`), so an exit-side outage
can never render behind an arrival note.

Every session carries **`duration_is_lower_bound`**; the two **entry-missing** states
additionally carry **`arrival_ts_is_unknown`**, because a lower bound on a session with **no
observed arrival** is a lower bound on an **unknown quantity** — a strictly weaker claim that
must not read as the same flag. The rendered `boundary_note` says *"no departure observed —
feed down 4.6 h"* and can **never** say *"still in port"* (the DRAUGHT-1 `insufficient_data`
lesson, asserted in tests on the rendered string).

**(g) Completeness is PARAMETER-DEPENDENT and must never be published bare.** Measured in two
independent directions:

- **Core radius:** PORT-pre measured `complete` moving **20.9 % → 54.9 %** on the radius alone.
- **A defect, not a parameter — the correction that produced (f).** PORT-2 reported `complete`
  at **9.5 %** and diagnosed the gap to PORT-pre's 19.7 % as the flank pad. **That diagnosis
  was wrong**, and verifying it for this document is what caught it. Each candidate was
  measured and ruled out: core radius (uniform 3 km **20.3 %** vs per-port **20.6 %**),
  box/pad (28 km **20.6 %** vs 13 km **20.3 %**), port composition (the nine-port sample's six
  WPI ports **19.4 %**), the stricter quality filter (**18.9 %** vs **18.7 %**) — all ≈ 20 %.
  The real cause was the **excursion-merge defect** in (d). Correcting it moved the nine-port
  sample from `complete` **9.5 % → 14.5 %** and Haifa from **13.0 % → 23.5 %**. **PORT-2's
  reported four-state distribution is superseded by (f).**
- **The flank pad is now a request parameter** (`flank_pad_km`, 0–50 km, default **10 km**
  unchanged) rather than a widened default: raising a completeness figure with no change in the
  world is **tuning a metric, not measuring**. It widens only the **aperture** searched for the
  arrival/departure fix — never the port — so it cannot create, move or extend a visit, only
  reveal a boundary already there. Measured effect is small: 10 → 28 → 50 km moves Haifa's
  `complete` **13.0 → 13.5 → 13.5 %** (637 → 611 → 634 ms) and Tartus **0.0 → 1.3 → 1.3 %**.
  It does raise `vessels_outside_radius`, so the Ambarlı ratio in (h) is itself pad-dependent.

Because of all this, **every completeness figure in this document states the geometry beside
it**, and the endpoint returns `geometry` (radius, `radius_basis`, `coord_precision`,
gap-split, speed threshold, the bar, flank, pad, and the quality rule) on every response for
the same reason. A completeness number without its geometry is meaningless.

**(h) The Ambarlı rule — a low count can be a GEOMETRY signal, not a traffic signal.** WPI
`harborSize` can be **stale**, and the radius is scaled from it, so a small radius at a
genuinely busy port **under-covers** it. WPI rates **Ambarlı `'V'`** (very small → the 1.5 km
floor) although it sits in the collector's **densest 1° cell**. Live: **26 vessels inside the
radius, 542 outside it** in the padded box. `geometry_under_covers` flags exactly that — keyed
on the **measured ratio plus the size class, never on a port name** (the ZONES-3
no-allowlist principle: suppress the routine by measurement, never by silencing a location *a
priori*). It discriminates: **Ambarlı 26/542 → true**, **Gebze 0/35 → true**, **Haifa 119/10 →
false**.

**(i) The coverage asymmetry — the layer is weakest exactly where the AOI centres.** This
deserves its own statement, not a footnote. Over the same 384 h window and geometry:

| Port | Source | Visits | `complete` | `presence_only` |
|---|---|---|---|---|
| Haifa | WPI (M, 3.5 km) | 213 | **23.5 %** | 35.2 % |
| Limassol | WPI (S, 2.5 km) | 66 | 22.7 % | 47.0 % |
| Istanbul | WPI (L, 5 km) | 66 | 15.2 % | 71.2 % |
| Bur Sa'id | WPI (L, 5 km) | 61 | 6.6 % | 68.9 % |
| **Port of Beirut** | **OSM (3 km)** | 65 | **7.7 %** | 55.4 % |
| **Tartus Port** | **OSM (3 km)** | 78 | **0.0 %** | **93.6 %** |
| **Port of Latakia** | **OSM (3 km)** | 22 | **0.0 %** | **90.9 %** |
| Ambarli | WPI (V, 1.5 km) | 7 | 0.0 % | 71.4 % | 
| Sayda | WPI (V, 1.5 km) | 0 | — | — |

**Tartus and Latakia produce ZERO complete sessions** — ~91–94 % `presence_only` — against
Haifa's 23.5 % complete and 35.2 % `presence_only`. We can say a vessel was *there*; we
essentially never see it **arrive or leave**.

This is the **second independent manifestation of one underlying reality**, the first being
the gazetteer hole in (b): **open maritime data — both reference and observational — is thin
off the Syrian coast.** WPI omits the ports; the AIS feed under-observes their approaches. And
it **bounds the layer's stated value proposition precisely where the project's interest is
highest**: *"where was this hull before and after its gap"* is answerable at Haifa and
Limassol, and largely **not** answerable at Tartus and Latakia. A study must not read the
Syrian ports' `presence_only` dominance as vessels behaving differently there — it is the
observation that is different.

**(j) The A1 reconciliation — a standing invariant, not a note.** A berthed vessel's silence
and a port visit are **ONE SILENCE SEEN TWICE**. Measured on the corrected visit population
over the full gazetteer: **1,446 of 1,690 visits (85.6 %) temporally overlap an A1 gap for the
same vessel**, and the overlapping gaps are **`stationary` 2,787 (61.6 %) · `outage-excluded`
1,610 (35.6 %) · `candidate` 71 (1.6 %) · `jamming-excluded` 50 · `edge` 4**. From A1's side,
of gaps starting within 15 km of a port **59.3 % are `stationary`** and only **5.4 %
`candidate`**. *(PORT-pre measured **55.8 %** on the **raw-crossing** population — a different,
looser population than the visits the layer actually emits; the visit population overlaps far
more strongly, because a real multi-hour berthed call is much likelier to contain a
transmission gap than a brief transit.)*

Therefore **a port visit and an A1 gap on the same hull are NOT independent observations**, and
a study that counts both as corroborating evidence is **double-counting one physical event**.
This layer's contribution is precisely that it makes A1's discarded confound *measurable* — it
does not add a second, independent signal.

`test_port_visit_a1_overlap_is_expected` is a **tripwire guarding a research finding, not just
code**: it asserts the overlap stays non-zero **and** stays dominated by
`stationary`/`outage-excluded` rather than `candidate`, and its failure message states that if
`candidate` ever dominates, **the Q4 null in (l) must be re-measured** — because that would
mean A1's waterfall had changed underneath the conclusion.

**(k) Why this layer is deliberately OUTSIDE the composite and the alerts feed.** Not an
omission — a measurement. PORT-pre quantified admission at three bars:

| Admission bar | Vessels | Hulls touched | `1 → 2` pattern flips | New hulls | Composite growth |
|---|---|---|---|---|---|
| all raw sessions | 1,179 | 641 | 604 | 538 | **68 %** |
| the visit bar | 629 | 327 | 305 | 302 | 36 % |
| **visits + `complete` only** | 160 | 106 | **96** | 54 | **9 %** |

Even **the tightest defensible bar** flips **96 hulls from 1 to 2 `n_pattern_types`**, and
`composite_rank` (§2.8) promotes a two-type hull to **HIGH** whenever a grey/sanctions flag or
a high underlying event is present — so admission would **manufacture HIGH-rank hulls out of a
cargo ship calling at Haifa**. And `n_pattern_types` is a **scarcity** signal: with the live
maximum on any hull at **4** (of five event types present in `maritime_events`), a pattern
that fires for a third of all observed vessels would **destroy the count's meaning**. Port
visits are **routine commerce** — measured type mix over the full gazetteer: **cargo 37.0 % ·
passenger 13.0 % · tug 11.7 % · pleasure_craft 9.9 % · tanker 7.3 % · other 5.3 % · sailing
3.5 %**, i.e. **~41 % ferry, leisure and port-service traffic**.

So: **no migration, no `event_type='port_visit'`, no `n_port_visit` column, no sig-CTE
disjunct, no alerts surface, no `suspicion_rank`** — there is nothing to rank. The guard is
**structural, not documentary**: `test_endpoint_writes_nothing_annotation_only` asserts that
querying the endpoint leaves `maritime_events` row-count unchanged, that no `port_visit`
`event_type` exists, and that the composite carries no port column.

**(l) The two recorded nulls — measured dead, so they are never re-proposed.**

**(l.1) The gap-toward-a-port signal is DEAD, and anti-correlated.** The roadmap's second
port product was *"a vessel going dark en route to a listed port"*. Its mechanical half was
measured and is **worse than absent — it points the wrong way**:

| Measure | A1 `candidate` gaps | Null baseline (all plausible fixes) |
|---|---|---|
| start within **15 km** of a port | **8.2 %** | **19.14 %** |
| end within 15 km | 7.6 % | 19.14 % |
| start within **50 km** | **25.1 %** | **36.91 %** |

Candidate gaps are **depleted** near ports by **~2.3×** at 15 km. Direction adds nothing
either: of the 842 candidate gaps starting within 50 km of a port, **26.0 %** had a last-fix
course within ±45° of the port bearing — and a ±45° window is **25.0 %** of the circle. That
is **chance**.

The cause is **structural**: A1's waterfall (§2.4) has *already* removed coastal gaps —
**59.3 %** of gaps starting within 15 km of a port are classified `stationary` — leaving
`candidate` an **open-sea phenomenon** (median distance-to-coast **92.1 km**; p25 20.0, p75
175.8). **Both halves are null simultaneously**, so the pattern's intelligence value depended
entirely on the *listed* dimension — which is (l.2). *(Recorded aside, a real finding about the
G layer rather than about ports: `jamming-excluded` gaps **are** strongly port-clustered —
46.2 % start within 15 km — GNSS denial in this theater is a coastal phenomenon.)*

**(l.2) The listed-port dimension is UNBUILDABLE, not merely unbuilt.** Re-confirmed at
PORT-pre from the primary documents, and the case has since **strengthened**:

- **No open sanctions list enumerates ports as PLACES.** OFAC's Syria shipping advisories
  (e.g. 2024-09-11) designate **vessels** — an annex of hulls on the SDN List — not locations.
  So a frozen citations-only snapshot would be a snapshot of prose that names **hulls**: it
  would yield **no port features at all**. There is nothing to freeze.
- **Designation is a dated legal state that moved twice in seven months**: Syria's core
  sanctions EO was **revoked 2025-06-30**, and the **Caesar Act was repealed 2025-12-18** by
  the FY2026 NDAA.

Should a port-keyed source ever appear, the **required framing is fixed in advance** (the
UCS-2023 badge precedent): a **frozen, dated snapshot**, every entry citing its **issuing body
and document date**, rendering only *"named in [document], [date]"* — **never** *"sanctioned
port"* as a standing fact.

**(m) Compute and refresh.** **On-query, one resolved port at a time** — there is no timer, no
detector script and no stored layer. Ports resolve **in-process** from the committed gazetteer
(no DB identity table, the airport precedent) by name, `alternate_name` (WPI files Port Said as
*"Bur Sa'id"*, so a name-only search misses it) or UN-LOCODE. Visits derive over the port's
padded vicinity box on the `vessel_states` **GiST** path. Measured **274–637 ms per port**
end-to-end (fix fetch + outage reconstruction + sessionization; the fix query alone is
~167 ms). **A full 190-port sweep is ~30 s and is deliberately not an endpoint** — the surface
takes a resolved port, never the gazetteer. Co-claimant ports for `candidate_ports` are
computed **once** per query rather than per fix (per-fix gazetteer scans cost 3.5 s at Haifa).

**(n) What the layer measures, honestly.** **34 of 190** ports produced any visit over 16 days
*(and 43 of WPI's 180 produced any raw crossing at a uniform 3 km core, PORT-pre)*. **A silent
port is a COVERAGE fact, never "no commerce"** — Sayda returns zero visits, and that cannot be
separated from its own suspect coordinate (below) or its "very small open roadstead" class.

### The generalized `maritime_events` design (why one table)

All behavioural patterns share the shape — a subject vessel, a time window, a location,
a classification, and provenance — so they share **one table**: typed columns
(`event_type, mmsi, start_ts, end_ts, imo, start/end lat/lon, duration_s,
classification, method, computed_at`) + a JSONB `detail` for pattern-specifics. **PK
`(event_type, mmsi, start_ts)`** gives idempotent recompute-and-replace; a CHECK
constrains `classification` (widened by migrations 028/030 to admit the GNSS tiers
`area-jamming`/`glitch` and A1's `jamming-excluded` — each addition a cheap drop-and-re-add
that validates instantly, existing rows being a strict subset). Indexes: `(event_type,
classification, start_ts DESC)`, `imo`, `((detail->>'partner_mmsi'))`. A **new pattern is a
new `event_type`** (and a new `detail` shape) — no new table, often no migration (A3 added
none; the GNSS episodes reused the table, needing only a classification-CHECK widening).
This mirrors `signal_events` (the operational log) and is why the composite can compose all
**four** patterns uniformly.

---

## 3. The identity spine & join model

### MMSI vs IMO — point-in-time vs permanent

| | **MMSI** | **IMO** |
|---|---|---|
| what | radio/station id (9 digits) | hull id (7 digits) |
| lifetime | **changes** on re-flag / reassignment | **permanent** for the hull's life |
| carries | the flag (via MID → M2a) | nothing derivable — an anchor |
| role here | the AIS-native key of `vessel_states`/`vessel_identity` | the **spine** that unifies a hull across its disguises |

A vessel can change MMSI, flag, and name together (A3's re-flag) while the IMO stays
constant. Studies must therefore treat **name/flag as time-stamped**, and use the IMO to
follow a hull. The base has **no foreign keys** — layers join by convention (`LEFT JOIN`)
so coverage gaps stay explicit.

### The join model

```
vessel_states (mmsi, ts, source)              -- position + M1 geo + G quality (position_quality/…)
   ├─ (mmsi) ── vessel_identity                -- M2a flag + M2b identity(name/imo/…)
maritime_events (event_type, mmsi, start_ts)   -- A1/A2/A3 + gnss_anomaly events; imo + partner in detail
   └─ views maritime_hull_events / _composite  -- hull = COALESCE(event.imo, spine.imo, mmsi)
```

- `vessel_states.mmsi` → `vessel_identity.mmsi` attaches flag + name + IMO to each fix.
- `maritime_events` carries `imo` (from the spine at detection time) + `mmsi`; the
  composite view resolves the **hull key** so events under a re-flagged vessel's old and
  new MMSIs unify under one IMO.

### Example cross-layer queries

**(a) A vessel's full current identity** (location context + registration + spine):

```sql
SELECT DISTINCT ON (vs.mmsi)
       vs.mmsi, vi.name, vi.imo,           -- M2b identity (point-in-time name, permanent IMO)
       vi.flag_country, vi.station_type,   -- M2a flag (mmsi-mid)
       vs.country, vs.near_country, vs.distance_km  -- M1 geo (territorial waters / offshore)
FROM vessel_states vs
LEFT JOIN vessel_identity vi USING (mmsi)
WHERE vs.mmsi = $1
ORDER BY vs.mmsi, vs.ts DESC;             -- latest fix
```

**(b) Named vessels currently in a country's territorial waters, by flag** (M1 × M2a × M2b):

```sql
WITH latest AS (
  SELECT DISTINCT ON (mmsi) mmsi, country
  FROM vessel_states WHERE country = 'Syria' ORDER BY mmsi, ts DESC)
SELECT vi.name, vi.ship_type, vi.flag_country, vi.imo
FROM latest l JOIN vessel_identity vi USING (mmsi);
```

**(c) THE capstone — a hull's cross-pattern picture, stitched by IMO across its
identities** (the composite views doing the join):

```sql
SELECT c.hull, c.n_pattern_types,
       c.n_gaps, c.n_rendezvous, c.n_identity_changes, c.n_gnss_anomaly,
       e.event_type, e.start_ts, e.detail ->> 'suspicion_rank' AS rank
FROM maritime_hull_composite c
JOIN maritime_hull_events    e USING (hull)
WHERE c.n_pattern_types >= 2                     -- multi-pattern hulls only
ORDER BY c.n_pattern_types DESC, e.start_ts;
```

On the reference snapshot the single **4-pattern hull, IMO 9895915** (EUROSAILOR, Malta),
is the payoff: **all four** patterns in one IMO-stitched timeline — an AIS **gap** (×2), an
**identity-change** (the EUROSAILOR→ROSA rename), a **rendezvous** (×2), **and** a
**gnss-anomaly** (×6) — `n_pattern_types = 4`, composite rank **HIGH** (≥ 3 types → high).
Just as the classic **IMO 9352195** re-flag+gap example (Cameroon→Russia,
SANDHYA→VYCHEGDA) unifies events under both MMSIs, 9895915 is the first hull to reach four
distinct pattern types — the capstone the whole stack was built toward. It is one of **47**
hulls that show a GNSS anomaly alongside at least one other pattern.

*(Post-snapshot: the composite now composes a further pattern type, `zone_dwell` (§2.8, §2.10,
migration 037). The **capstone 9895915 is unchanged at 4** — it carries no infrastructure dwell —
but two other hulls were genuinely promoted to HIGH by gaining an intrusion pattern; see §2.8 for
the before/after and the structural "every new pattern promotes already-patterned hulls" property.)*

---

## 4. Methodological guidance for studies (critical)

A study using this base **must** state the following. These define what the data can and
cannot support.

1. **"Investigation lead, not proof" — and it COMPOUNDS.** Every behavioural candidate
   (A1/A2/A3) is a *lead*, never proof of intent. The composite compounds leads into a
   **stronger lead — still not proof**: a hull that went dark AND met another AND
   changed identity has more reasons to investigate, but **legitimate explanations
   exist** (a sold + re-flagged tanker that anchored in a sparse-coverage area and did
   offshore supply work could trip all three innocently). Report candidates as
   prioritised leads; never as accusation.

2. **Confound removal is the method — describe it, don't bypass it.** Each detector's
   value is *what it removes*:
   - **A1 (gaps):** **four** confounds are subtracted before a gap is a candidate — (i)
     **feed outages** (the collector/receiver being down; *every* vessel gaps then,
     reconstructed from `signal_events`; see §4.5); (ii) **GNSS jamming/spoofing**
     (`jamming-excluded`, the fifth-in-precedence confound the G layer supplies — spoofed
     positions during the gap and/or an active area-jamming zone; **112** on the frozen
     snapshot); (iii) **stationary/dedup** (the archive is a moved-vessel delta —
     anchored/moored vessels report sparsely; ~46 % of rows are near-stationary); (iv)
     **coverage geography** via a **data-driven coverage mask** (position density per
     0.1° cell, itself excluding implausible fixes), NOT coast-distance. Only ~**12 %**
     of gaps survive as candidates (**2,367 of 19,313**); use
     `classification='candidate'`.
   - **A2 (rendezvous):** ports/anchorages and busy lanes are excluded (data-driven
     masks) + a > 10 km offshore floor; **~99.9 %** of naive proximity is innocent, and
     even clean candidates are **mostly legitimate** (offshore energy, ferries) — use
     `suspicion_rank`, don't treat every candidate as suspect.
   - **A3 (identity change):** a noise filter removes corruption, single-message blips,
     spacing/typo variants, and placeholder MMSIs; and a rename/re-flag is **usually
     legal**. Only *confirmed* changes are stored.
   - **G (GNSS anomaly):** implausibility is **not** a suspicion ranking the way a
     gap/rendezvous is — a spoofed vessel is usually a **victim**, not an evader. The
     detector separates the **victim/actor** modes structurally: `area-jamming` (an
     environmental victim among many, ≥ 10 vessels/cell·hour) and `glitch` (a source with
     no real track) are **classified out** of the composite; only the `individual`
     (`candidate`) tier — a real vessel whose own coherent track was spoofed/jammed — is a
     lead, and even it carries only a **low** baseline rank. A GNSS verdict flags a
     data-quality event; it does not accuse.

3. **Point-in-time identity.** MMSI, flag, and name are time-of-observation state and
   change (A3). **IMO is the only permanent anchor.** A study must treat identity as
   time-stamped (use `identity_updated_at`, `derived_at`, the A3 `last_old_ts`/
   `first_new_ts`), and follow a hull by IMO — never assume a name/flag is fixed.

4. **Terrestrial AIS limitations (the sampling frame).**
   - **Coverage is terrestrial** (~40–60 NM offshore effective); the open sea is dark by
     design. Absence of a vessel/position is **not** evidence of absence.
   - **AIS is self-reported and spoofable** — a broadcast identity or position can be
     falsified; the base observes *what was transmitted*, not ground truth.
   - **Short window (~8–9 days, 2026-07-11 → 2026-07-20).** Rare patterns (re-flags,
     rendezvous) are sparse; **A3 and the composite are partly forward-looking
     infrastructure** — they accumulate signal as the collector runs. Report the window.
   - **Class-A focus.** The collector subscribes to `PositionReport` + `ShipStaticData`
     (Class A / AIS types 1/2/3/5); **Class B (types 18/19/24) is deferred** — small
     craft are under-represented (§6).

5. **The feed-outage confound is a DATA-QUALITY caveat any study must state.** A large
   share of the observation window has **no data at all** (collector/receiver outages).
   Any temporal analysis (activity rates, "when did X happen") must subtract the outage
   windows (`signal_events` `feed_disconnect`→`feed_connect`) or explicitly caveat them,
   exactly as A1 does. A raw gap/activity count that ignores them is wrong.
   - **Outage recompute (post-snapshot, 2026-07-25 — the G6-deferred figure, now computed).**
     The snapshot ended into an open outage (feed down **2026-07-20 00:04:01 UTC**, 26 s after
     the last archived position); the recompute deferred while it was open is now done, over
     the **full observation window from `signal_events`**. Window **2026-07-11 19:10:01 →
     2026-07-25 11:07:31 = 328.0 h (13.7 d)**; **40** paired `feed_disconnect→feed_connect`
     outage windows total **172.0 h = 52.4 % of the window** — over **half**, up from the
     frozen-snapshot **"≈ ¼"** (which held over the shorter 8.5-day window; two subsequent
     multi-day outages dominate the extension). The **longest single outage is now 68.5 h
     (2026-07-22 09:00:56 → 2026-07-25 05:33:28)** — it **supersedes** both that open outage
     (now closed at **52.9 h**, 2026-07-20 00:04:01 → 07-22 04:55:48, the *second* longest) and
     the prior *closed* record (**44.5 h**, 2026-07-16 13:22:10 → 07-18 09:53:31). *(G6-discipline
     flag: the 52.9 h window is **not** "the longest on record" — the later 68.5 h window is;
     every boundary was read from `signal_events`.)* Any temporal analysis must still subtract
     these windows (as A1 does), and the chronic upstream-TLS failure mode (§6) is why the
     outage share roughly doubled: three multi-day outages in six days.

6. **Provenance — cite per-layer, never collapse.** Every derived fact carries a
   `source`/`method` + vintage. A correct citation names the layer *and* its method/date
   (e.g. "flag as of MMSI-MID; identity as of `identity_updated_at`; gap by
   `ais-gap-detector-v1`"). Do not present a joined row as if all facts share one basis.

---

## 5. Reproducibility

Each layer rebuilds deterministically from **ground truth** (the append-only AIS JSONL)
or from a **committed dated asset**, so a study's state is reconstructible.

> **Host, service, timer and storage facts: [`ENVIRONMENT.md`](ENVIRONMENT.md) is the measured authority** — it supersedes any hardware, network, port, cadence or free-space figure stated elsewhere in this document, including the "Runtime timers" note below.

| Layer | Rebuild command | Ground truth / asset (version) |
|---|---|---|
| AIS substrate | `python tracker_maritime.py` (live) · `python tracker_maritime.py backfill --archive maritime_output` | `maritime_output/ais-signals.jsonl` (append-only; every new signal) |
| M1 geo | `python scripts/backfill_vessel_geo.py` | derived (`geo_area.py`, 12 NM); asset `backend/data/aoi_countries.geojson` |
| M2a flag | `python scripts/backfill_vessel_flag.py` | committed `backend/data/mmsi_mid.json` — **ITU RR App. 43 / Rec. ITU-R M.585, 292 MIDs, retrieved 2026-07-19** |
| M2b identity | `python scripts/backfill_vessel_identity.py` | derived from `vessel_states` static fields (dimensions parse-forward-only) |
| G · GNSS quality (per fix) | `python scripts/backfill_position_quality.py` (full) · `--since-minutes 45` (runner, overlap window per MMSI) | derived (`gnss_anomaly.py`, R1/R2); asset `aoi_countries.geojson` (inland depth) |
| G · GNSS anomaly (episodes) | `python scripts/detect_gnss_anomaly.py [--dry-run] [--since …]` | derived over `vessel_states.position_quality` + `signal_events` (affected-zone subtraction) |
| A1 gaps | `python scripts/detect_ais_gaps.py [--since …]` | derived over `vessel_states` + `signal_events` outages |
| A2 rendezvous | `python scripts/detect_rendezvous.py` | derived over `vessel_states` (masks from history) |
| A3 identity change | `python scripts/detect_identity_change.py` | derived over `vessel_states` + `vessel_identity` |
| Composite | *(none — live views)* · `SELECT * FROM maritime_hull_composite` | reads `maritime_events` + `vessel_identity` |
| IMO integrity *(post-snapshot)* | `python scripts/backfill_imo_integrity.py [--dry-run]` (tiny table → full recompute each run; `--dry-run` first, WHERE-changed guard → idempotent) | derived — pure `imo_integrity.py` (check digit + malformed + reflag/collision); reads `vessel_identity` + `vessel_states` windows |
| Zones × dwell *(post-snapshot)* | `python scripts/detect_zone_dwell.py [--dry-run] [--hours 720]` (writes the strong subset — `safety_zone_intrusion` — as `zone_dwell` candidates; recompute-and-replace → idempotent) · on-query annotation surface `/maritime/zone-dwell` | committed dated asset `backend/data/zones_aoi.json` (GEM GOGET CC-BY-4.0 + OSM ODbL, retrieved 2026-07-26); derived — pure `zone_dwell.py` + in-process `zone_baseline.py` / A2 masks over `vessel_states` |
| Port visits *(post-snapshot)* | *(nothing to rebuild — **on-query only**, no stored layer and no detector script)* · `GET /api/v1/maritime/port-visits?q=<port>&hours=<h>[&flank_pad_km=<km>]` — **one resolved port per call** (~274–637 ms; a full 190-port sweep is ~30 s and is deliberately not an endpoint) | committed dated asset `backend/data/ports_aoi.json` (**NGA WPI Pub 150, US-Gov public domain, 180 features** + **OSM ODbL, 10 targeted features**, retrieved 2026-07-27); derived — pure `port_visit.py` + in-process `ports.py`; outage windows reused from `gap_detect.reconstruct_outage_windows` |

Notes:
- **Ground truth:** `maritime_output/ais-signals.jsonl` (every new-information AIS
  signal) is the durable record; the DB is a rebuildable derived layer. Large runtime
  outputs are **gitignored** (fetched/produced, not committed) — record the version used.
- **Migrations** `021`–`037` define the schema; apply with `python migrate.py` (each runs
  once, checksum-verified). A3 added **no** migration (reuses the generalized table); the
  GNSS layer added `027` (quality columns) `028` (episode tiers) `029` (composite admits
  GNSS) `030` (A1 fifth confound); the **IMO-integrity layer** added `035` (the identity
  quality axis, mirroring 027) and `036` (the composite hull-key rule + the `imo_integrity`
  collision disjunct); the **zones × dwell layer** added `037` (the composite admits
  `zone_dwell` — one sig disjunct + `n_zone_dwell`, mirroring 029/036) — the **zone asset is a
  committed file, not a migration**. *(`031`–`034` belong to other layers: `031` the air/sea GiST
  for area search; `032`–`034` the parallel MarineTraffic source + `vessel_xref`.)*
- **Detectors are deterministic + idempotent** (recompute-and-replace); their thresholds
  are the constants in §2 (`GAP_THRESHOLD_S=3600`; A2 `≤500 m / ≤3 kn / ≥1 h`, masks
  `≥5 stationary / ≥8 distinct / >10 km`; A3 `≥10 rows / ≥2 reflag / 0.85 similarity`;
  coverage mask `0.1° / ≥100`; **GNSS R1** `>60 kn`/`>100 kn` impossible · `40–60 kn`
  suspect · dual `≥500 m` jump floor · fast-craft allowance `≤60 kn` · SOG sentinel
  `102.2`; **GNSS R2** `>5 km` implausible / `2–5 km` suspect / `<2 km` plausible;
  **episodes** `EPISODE_GAP_TOL_S=3600` · `AREA_MIN_VESSELS=10` · `ZONE_CELL_DEG=0.1`).
  Pin them (and the DB snapshot) for a fixed study.
- **GNSS zone products are in-process artifacts, not tables.** The broadcast/affected
  jamming zones are aggregations rebuilt each run from `vessel_states` (like the coverage
  mask) — never a stored table/view; the same pure aggregator (`affected_zone_cells`)
  serves the detector and the API. The episode detector honours the **`--dry-run`
  discipline** (report the funnel, write nothing) — run it dry before any real
  recompute-and-replace.
- **Runtime timers** (production currency, all `Persistent=true`): `scraplan-maritime`
  (live collector) · `scraplan-quality.timer` **15 min** (per-fix GNSS quality,
  `--since-minutes 45` overlap) · `scraplan-gaps.timer` **15 min** ·
  `scraplan-rendezvous.timer` **20 min** · `scraplan-identity.timer` **30 min** · the GNSS
  **episode** detector runs **on demand** (idempotent recompute-and-replace; no deployed
  timer at the snapshot) · the composite is a **live view** (no timer). The **zone-dwell writer**
  (`detect_zone_dwell.py`, post-snapshot) is **recommended timer-driven** alongside the siblings —
  now that only the small strong subset is written, the flood risk that made ZONES-2 on-query is
  gone — but has **no deployed timer yet** (run on demand, like the GNSS episode detector).

---

## 6. Known limitations & gaps (frank)

- **Terrestrial-AIS coverage only** (~40–60 NM offshore). The open sea is dark; absence
  is not evidence of absence. Coverage is uneven — the A1 coverage mask is an *empirical*
  density proxy (it conflates traffic with coverage), not a guarantee.
- **Feed outages — over half the window (recomputed).** A large share of the window has no
  data at all. **Post-snapshot recompute (2026-07-25, §4.5):** over the full window
  (2026-07-11 → 07-25, 328 h) feed outages total **172 h = 52.4 %**, and the **longest single
  outage is 68.5 h** (2026-07-22 → 07-25) — superseding the snapshot's now-closed 52.9 h outage
  and the prior 44.5 h record. A data-quality confound every temporal study must subtract or
  caveat.
- **`aisstream` is a single-provider dependency with a documented chronic failure mode.**
  The live feed's intelligence layers have one upstream provider, and the recurring outage
  cause is an **upstream TLS certificate expiry** — documented recurrences on **2026-05-20,
  2026-06-23, and 2026-07-19** (and the two multi-day outages since — §4.5). This reframes
  `aisstream` from "reliable primary" to **"primary with a known chronic failure mode"**: a
  single-point-of-failure risk for currency. **A second AIS source now exists (post-snapshot):
  a committed MarineTraffic integration** (browser-scraped `mtscraper` — the public tile view,
  **not** the paid API — via `maritime/adapters/marinetraffic*.py`, migration 032) with an
  **AIS × MarineTraffic cross-source correlation** layer (`vessel_xref`, migrations 033/034).
  It is a **parallel, quarantined source**: **3,455** rows in its own `mt_vessel_states`
  (`source='marinetraffic'`) + **128** `vessel_xref` correlation links, and **no MarineTraffic
  fact feeds the core intelligence** — `vessel_states` is **100 % `aisstream`**, `vessel_identity`
  has **0** MT-sourced rows, and every M1/M2/A1–A3/GNSS/composite provenance statement above
  **remains correct** (re-verified 2026-07-27: `vessel_states` is **100 % `aisstream`**, and no
  intelligence module — gap/rendezvous/identity/gnss/imo-integrity/zones/composite — references
  MarineTraffic). **But that isolation is in COMPUTATION, not PRESENTATION, and the difference must
  be stated:** the maritime tab's headline vessel-count tile is computed over the **blended** feed
  (`api.maritime("all")` merges `vessel_states` + `mt_vessel_states`, each row carrying its own
  `source`), so the headline **total** can carry MarineTraffic **unmarked** — a *displayed* figure
  is not source-pure even though every *computed* fact is. A feed-meta line does break it into
  `AISStream N · MarineTraffic M` when any MT row is present, so MT is marked *nearby* but not *in*
  the headline number. **Intended end state (the UI window's UI-2):** the headline counts **AIS only**,
  with MarineTraffic a **marked secondary** figure. **Landed (UI-2, commit `02a7cc8`):** the headline
  tile now counts **AIS only**, MarineTraffic shown as a separate marked figure — the presentation
  defect is fixed on the display side.
  **MT store is STATIC and STALE (MT-FRESH, 2026-07-29):** `mt_vessel_states` is **3,455** rows,
  **128** `vessel_xref` links, and its last row is **2026-07-25 10:26 UTC** — **no systemd service
  updates it** (run manually), so at measurement it was **~4 days stale**. This exposed a *contract*
  honesty defect worse than the presentation one: `source=all` falls back to the DB (latest fix per
  `ship_id`, `ts > now()−60 days`, **no freshness filter**), so it served **~2,616 four-day-old MT
  vessels beside ~240 live AIS** — ~92 % of the blend stale — while `data_vintage` reported the
  **max/freshest** ts across tables (the AIS ~seconds value), declaring an ~11 s vintage for a
  mostly-four-day-old payload. **Fixed (MT-FRESH, contract, additive):** the response now returns
  per-source **`sources[]`** (each source's `last_fix`/`age_seconds`/`rows`/`stale`) and `data_vintage`
  is redefined to the **OLDEST contributing source's vintage** (worst-case — never over-claims; single-
  source unchanged). No rows are dropped (flag-not-delete, G3); each vessel already carries its own
  `timestamp` + `source`, so per-row age is derivable. **MT recommended OPT-IN** (off by default — the
  GNSS-zones / dark-vessels pattern): the backend already defaults `source=aisstream`; the frontend
  should keep MT a toggled overlay that reads its age from `sources[]`.
  **MT carries no MMSI — un-investigable (MT-FRESH):** `mt_vessel_states.mmsi` is **0 non-null**;
  MarineTraffic's public tile view is **`ship_id`-keyed, MMSI-less** (`tracker_marinetraffic.py`:
  "a different identity space (ship_id, not MMSI)"). This is **structural** (an MMSI would need a
  per-vessel detail-page scrape, not the position feed) — so MT positions **cannot be identity-resolved
  and are unreachable from the E1 search surface**; their analytical value is bounded to the
  **`vessel_xref` cross-reference product** (`ship_id → mmsi`, correlation-derived) alone.
  It still has **no systemd service** (run manually, not continuously deployed), so it has not grown
  and does not remove the currency SPOF. *(Provenance note: this base
  otherwise sources only the live AIS feed + committed public assets; MarineTraffic is the
  first commercial-aggregator source, taken from its public view by scraping — a distinct
  sourcing decision recorded here, not softened. The earlier "self-reception + AISHub"
  second-source roadmap is separate and still open.)*
- **Global Fishing Watch (GFW) is a quarantined analytical OVERLAY, not a second source *(post-snapshot, 2026-07-27; GFW-pre)*.** A committed, live adapter (`maritime/adapters/gfw.py`, the on-query `/maritime/dark-vessels` endpoint, the `DarkVessels` panel) surfaces GFW's **satellite-AIS dark-vessel (AIS-gap "disabling") events** where free terrestrial AIS is blind (Gulf / Red Sea / open ocean). Its posture is deliberately the strictest in this base, and every clause was verified from source:
  - **Licence: CC-BY-NC 4.0, non-redistributable** (*"the Services are provided for Non-Commercial use only… CC BY-NC 4.0"*; *"you may not… copy… transfer… or otherwise dispose of… content"*). It is therefore **not** a closed commercial source (not auto-excluded) but **not** an open one either. Consequence, enforced by design: **nothing is archived** (no JSONL ground-truth store) and **no asset is committed** — the integration is a **zero-storage on-query overlay** (no table, 30-min in-process cache), even more isolated than the MarineTraffic quarantine (which at least has `mt_vessel_states`).
  - **Derived analytics only.** GFW exposes **no raw AIS positions** through this path — only its own *derived* gap detections (each requiring ≥14 satellite positions before the gap). The adapter imports the inference, clearly labelled a lead (`intentional` is GFW's own likelihood flag, not a verdict).
  - **≥72 h latency floor.** Every GFW product carries a ~72-hour (up to ~96 h) processing delay; GFW itself states it is unsuitable for live enforcement. So **every dark-vessel event shown is at least three days old — historical, not live.**
  - **Satellite AIS (via Kpler) — the one real capability.** GFW's positions are satellite-sourced, so they genuinely see **beyond terrestrial receiver range** (the ~40–60 NM blind spot above). Its failure surface is **independent of `aisstream`** (Kpler's commercial satellite+terrestrial stack vs aisstream's community/terrestrial WebSocket whose outages are TLS-cert expiry) — but GFW carries its **own** single-vendor (Kpler) concentration risk and publishes no SLA, and its 72 h latency means it can **never** be a live failover.
  - **GFW's own SAR (radar) vessel-detection layer has been offline since 2026-07-03** (ESA Sentinel-1A retirement; ≥1-month gap pending the Sentinel-1C/1D pipeline). Not used here, recorded for currency.
  - **Measured East-Med coverage (probe, 2026-07-27, Jan–Jul window):** dark-vessel events across the **open East Med** (~30, flag-of-convenience-heavy — Togo/Liberia/Cyprus/Marshall Is./Malta) and the Levantine basin (~15), but **zero off the Syrian coast** (Tartus/Latakia) — so GFW **does not fill the PORT-2/4 coastal weakness** (§2.11i); it lights the open basin, not the coastal strip.
  - **It is NOT a live second source and must never be documented as one.** **No core fact — M1/M2, A1–A3, GNSS, IMO-integrity, zones, ports, or the composite — derives from GFW** (verified: no intelligence module references it); it is display-only corroboration, attributed CC-BY-NC, and the live-currency SPOF (§ the aisstream bullet) remains open, awaiting AISHub / self-reception, not GFW.
- **Deep open-sea ship-to-ship is outside the sampling frame.** Terrestrial-AIS coverage
  (~40–60 NM) means A2 (rendezvous) sees **near-coastal encounters only**. A deep open-sea
  STS transfer — beyond receiver range — is **not in the frame at all**: A2's silence
  there is a coverage fact, not evidence of absence. State this plainly in any STS study.
- **Short observation window (~8–9 days).** Rare patterns are sparse; A3 (7 re-flags, 2
  renames, 2 retypes) and the composite (**54 two-pattern, 5 three-pattern, 1 four-pattern
  hull**) are **real now but also forward-looking infrastructure** — coverage grows with
  runtime.
- **GNSS implausibility is not intent, and is often victimhood.** The G layer detects that
  a *position is wrong* (spoofing, area jamming, a GPS glitch, or an AIS data error), not
  *who did it* — a vessel broadcasting spoofed positions is usually a **victim** of area
  jamming. `area-jamming`/`glitch` are excluded from the actor-centric composite for
  exactly this reason; even an `individual` lead carries only a low baseline. R2 has a
  documented residual blind spot at non-AOI land borders (§2.7b).
- **Class-B excluded.** Only Class-A `PositionReport` + `ShipStaticData` are subscribed;
  Class-B position (18/19) and static (24) are deferred — small/pleasure/fishing craft
  are under-represented.
- **AIS is spoofable and error-prone.** Identity, position, and even IMO can be
  falsified or corrupted; the base observes transmissions. A3's noise filter removes
  *accidental* corruption, not deliberate spoofing.
- **Egypt / Iran (and other non-AOI coasts) are NOT in the AOI.** M1 classifies only the
  seven countries' territorial seas; a vessel off Alexandria reads as *outside*
  (nearest-of-seven). AOI expansion is a deliberate single future change.
- **IMO-null vessels are not stitchable.** ~⅓ of vessels lack a broadcast IMO in the
  spine; their events aggregate by MMSI in the composite and **cannot** be unified across
  a re-flag (honest, documented). Dimensions are **parse-forward-only** (historically
  unrecoverable).
- **IMO integrity verifies consistency, NOT ownership (the hard limit of §2.9).** A valid
  check digit proves the number is **well-formed**, not that this vessel **owns** it — a
  **fabricated IMO with a correct check digit passes**. Without an authoritative registry —
  and **closed commercial registries (IHS Markit, paid Equasis) are excluded by policy** —
  the layer verifies **internal consistency** (well-formedness, non-conflicting attribution,
  dimensional coherence), never identity. Two measured facts frame it honestly: (i) the **36
  malformed IMOs arrive already malformed from upstream** — confirmed in the JSONL ground
  truth **before our parser** (a vessel persistently rebroadcasts a bad value for days), so
  they are **received attributes, not our decode bug**; the ingest decision is therefore
  **record-then-flag, never null at parse** (nulling would erase "no IMO reported" vs
  "reported corrupt" and kill the `malformed` verdict for exactly its cases). (ii)
  Characterisation of the 36 (Step-0, live): **0 equal the transmitter's own MMSI and 0 equal
  any other known MMSI** — so the common "MMSI-in-the-IMO-field" misconfiguration is **not**
  what this is, and none pose a false-attribution risk; they are **2 bit-pattern artifacts**
  (`65535`=0xFFFF, `167772160`=0x0A000000), **1 sequential placeholder** (`123456789`), and
  **33 wrong-length numeric values** (mostly 9–10 digit, only 3 even MMSI-shaped). *(This
  refines an earlier "predominantly MMSI-in-IMO-field" guess — flagged, not reproduced.)*
- **Zones × dwell — every dwell has legitimate explanations (§2.10).** Offshore supply/standby
  work, drilling, fishing, waiting at anchor for a berth, survey, and cable maintenance all produce
  low-speed presence at infrastructure. It is a **ranked lead surface, never an alarm**. Specific
  measured limits: **gas-field zones are assumed-radius reservoir centroids, not structures**
  (`radius_basis='assumed'`, ~20–35 km ambiguity — the weakest class, annotation-only, never
  composite-admitted); **duration is a lower bound** under the ~55 % feed outage; and the admitted
  intrusions are **recreational/small-craft-dominated** (pleasure_craft 7 · other 4 · high_speed_craft
  1 of 12) — the class captures small-craft loitering in a safety zone, **not** only commercial
  suspicion.
- **The zone type-gate rests on self-reported `ship_type` — a structural cloaking vector.** A vessel
  that falsely declares a low-suspicion type (tug/offshore) is **demoted** by the standoff gate, so a
  false declaration cloaks against **this layer specifically**. Measured exposure (demoted sessions on
  an unverifiable/inconsistent identity — no hull dimensions, or `imo_quality` checksum_fail/malformed/
  contested, or `dimension_conflict`): **2 of 28 demoted sessions over the 30-day window** *(the ZONES-2
  7-day measurement found 0 of 10 — flagged, not reproduced)*. The vector is **structural, not a
  confirmed exploit**; it is surfaced per session (`cloaking_exposed`) and counted, never silently
  trusted. This is also why only `safety_zone_intrusion` (which is **not** type-gated) is composite-
  admitted; the type-gated `standoff_dwell` stays annotation-only.
- **Three zone-type deferrals are SCOPE choices, not coverage gaps (documented in the asset manifest).**
  (i) The **Gulf/Iran theater** is deferred **with** the Egypt/Iran geographic expansion — though the
  collector observes to **59.29°E**, so it is deliberately blind to Gulf infrastructure dwell despite
  the data existing. (ii) **Submarine cables**: no redistributable open geometry exists — TeleGeography's
  CC grant covers the **map image only** (data subscriber-gated, repo removed), EMODnet/OSM are near-empty
  in the AOI; the **1 NM buffer spec is recorded ready** if a licensed source appears. (iii) **Listed
  ports**: no open sanctions list enumerates **ports as places**, and designation is a **dated legal
  state** (Syria's core sanctions were revoked **2025-06-30**), so it could only ever be a citations-only
  *"named in [list] as of [date]"* asset — never a standing *"sanctioned port"*. *(Priority 5 closed on this
  same finding, now recorded with its numbers in **§2.11l.2**: OFAC advisories designate
  **hulls, not places**, so a citations-only snapshot would yield **no port features at all**;
  and the legal state moved **twice in seven months** — EO revoked **2025-06-30**, Caesar Act
  **repealed 2025-12-18**. The companion **gap-toward-a-port** signal was measured
  **anti-correlated** and dropped — **§2.11l.1**.)*
- **The port layer's geometry is an aperture, not evidence (§2.11).** `radius_basis` is
  **`'assumed'` on all 190 features** — no open source publishes a measured harbour extent, so
  extents are **unmeasured** (weaker than the zones layer's 29/36 `'sourced'`). WPI coordinates
  are **degrees-and-minutes by publication design** (Pub 150 §3; ±~0.93 km lat / ±0.78 km lon
  at 33 °N) with **no datum stated**, and **108 of 190 features sit at the 1.5 km radius
  floor**, so for most of the gazetteer the positional uncertainty is **comparable to the
  radius itself**. **15 pairs merge** under any usable radius (tightest Gebze ↔ Kaba Burnu at
  1.40 km), so a session can belong to more than one port — reported as `candidate_ports`,
  never resolved by picking one. And WPI `harborSize` can be **stale**, which is why a low
  in-radius count may be a geometry signal (the Ambarlı rule, §2.11h).
- **The port gazetteer is thinnest where the AOI centres (§2.11b, §2.11i).** WPI carries
  **zero Syrian ports** and **one** Lebanese entry, so the Levant coast exists in this asset
  only via a targeted OSM supplement — and two of its entries are openly compromised:
  **Baniyas** is an `amenity=ferry_terminal` **node**, not a harbour polygon, with its
  **offshore SPM oil terminal absent from OSM entirely** and therefore *not represented*; and
  **Sayda**'s WPI point sits **7.3 km** from the OSM harbour polygon — **7× its own stated
  precision** — so that entry looks misplaced rather than rounded, and its zero visits cannot
  be separated from its "very small open roadstead" class. The **same thinness recurs in the
  observations**: Tartus and Latakia yield **0 % `complete`** sessions (~91–94 %
  `presence_only`) against Haifa's 23.5 %. Open maritime data off the Syrian coast is thin in
  **both** reference and observation, and that bounds the layer exactly where interest is
  highest.
- **A port visit is routine commerce, and it is not independent of A1 (§2.11j/k).** The
  measured type mix is **cargo 37.0 % · passenger 13.0 % · tug 11.7 % · pleasure_craft 9.9 % ·
  tanker 7.3 %** — **~41 % ferry/leisure/port-service**. And **85.6 %** of visits overlap an A1
  gap for the same vessel (61.6 % of those gaps `stationary`, 35.6 % `outage-excluded`): a
  berthed vessel's silence and a port visit are **one silence seen twice**, so a study must
  **not** count them as corroborating evidence. **34 of 190** ports produced any visit over 16
  days — a silent port is **coverage**, never "no commerce". Every completeness figure is
  **parameter-dependent** (`complete` moves 20.9 % → 54.9 % on radius alone) and is meaningless
  without the geometry beside it.
- **"Leads, not proof" is a hard ceiling on every claim.** No layer establishes intent
  or wrongdoing. The behavioural candidates and the composite prioritise for
  investigation; a study must not present them as proven activity.

---

## Appendix — reference-snapshot state (frozen 2026-07-20 00:03:36 UTC)

| Layer / table | State |
|---|---|
| `vessel_states` | **690,685** position rows · **3,525** distinct MMSIs · window 2026-07-11 → 2026-07-20 00:03:36 |
| M1 geo | **307,144** positions in a territorial sea · 376,581 outside (nearest-of-seven) |
| `vessel_identity` (M2a+M2b) | **3,525** vessels · flag **100 %** · name **2,846 (81 %)** · **IMO 2,719 (77 %)** · station types: ship 3,515 / other 10 |
| M2a flag asset | `mmsi_mid.json` — 292 MIDs (ITU, 2026-07-19) |
| G · GNSS quality (`vessel_states`) | **686,488 plausible · 209 suspect · 3,988 implausible** (of 690,685; 0 unchecked) |
| G · GNSS episodes (`maritime_events`) | **805** episodes — **276 candidate (individual)** / **504 area-jamming (area)** / **25 glitch** · 456 `decoy_match` |
| G · jamming zones (in-process) | broadcast peak **112** distinct vessels/cell·hour (Amman cell 317,359 @ 2026-07-19 15:00) · 22 area cell-windows (≥10) · affected variant outage-subtracted (open window → 0 cells) |
| `maritime_events` · A1 gaps | **2,367 candidate** (of 19,313: outage-excluded 5,658 / **jamming-excluded 112** / stationary 9,322 / edge 1,854) |
| `maritime_events` · A2 rendezvous | **35** (all candidate) |
| `maritime_events` · A3 identity_change | **11** (7 re-flag / 2 rename / 2 retype) |
| Composite (`maritime_hull_composite`) | **1,345** hulls · **60 multi-pattern** (54 two-type · 5 three-type · **1 four-type**) · the 4-pattern hull **IMO 9895915** (gap + identity-change + rendezvous + gnss-anomaly, HIGH) · 47 hulls with GNSS + ≥1 other |
| Feed outages (`signal_events`) | **recomputed post-snapshot (2026-07-25, §4.5):** full window 2026-07-11 → 07-25 = **328 h**; **40** outage windows total **172 h = 52.4 %**; **longest 68.5 h** (07-22 → 07-25), superseding the now-closed 52.9 h (07-20 → 07-22) and the 44.5 h (07-16 → 07-18) |
| **IMO integrity** (`vessel_identity`) *(live, 2026-07-25 — post-snapshot)* | of **3,776** identities: **2,875 valid · 10 checksum_fail · 36 malformed · 0 contested**; **854 NULL-IMO** (`imo_quality` NULL) · invariant: no NULL-IMO row is ever stamped · dimension coverage **34 %**, 0 contradictions · composite exposure **1/62** multi-pattern hulls on a soft spine (`9789930`), **0** on a malformed key, capstone `9895915` `valid` |
| **IMO hull-key re-keying** (migration 036) *(live)* | **10** composite hulls re-keyed IMO→MMSI (all single-MMSI): `1042859000`→256586000 · `65535`→213987000 · `730323100`→671909000 · `898934300`→677061000 · `899218200`→271001066 · `911413900`→372676000 · `915957900`→671505000 · `916156000`→209653000 · `946061700`→212422000 · `952294600`→538008779 |
| **MarineTraffic source** (`mt_vessel_states`, `vessel_xref`) *(post-snapshot; §6; MT-FRESH 2026-07-29)* | **3,455** MT rows / **2,616** distinct `ship_id` (`source='marinetraffic'`, browser-scraped, own table) · **128** AIS×MT `vessel_xref` links · **0** MT facts in `vessel_states`/`vessel_identity` (core stays AIS-only) · **STATIC + STALE**: last row **2026-07-25 10:26 UTC**, **no systemd service** · **0 non-null MMSI** → positions un-investigable (value bounded to `vessel_xref`) · MT-FRESH added per-source `sources[]` vintage + made `data_vintage` the oldest-contributing (blend never over-claims) · MT recommended **opt-in** |
| **Zones asset** (`backend/data/zones_aoi.json`) *(§2.10; live 2026-07-26)* | **36 zones** = **7 gas_field** (GEM GOGET CC-BY-4.0 centroids: 4 Israel · 3 Cyprus) + **29 platform** (OSM ODbL points: 26 Egyptian Nile-Delta · 3 Israeli) · radius **500 m `sourced`** (platform, UNCLOS 60(5)) / **5 km `assumed`** (gas_field centroid) · **1** node `detectable:false` (mis-sited, faithful record) · 28/29 platforms no accuracy warning |
| **Zones × dwell** (`maritime_events` `event_type='zone_dwell'`) *(§2.10; live 2026-07-26, 30-day window)* | **308** dwell sessions — **12 safety_zone_intrusion · 230 standoff_dwell · 66 field_dwell**; gating → **64 leads · 28 demoted · 216 excluded**; cloaking exposure **2/28** demoted · **admitted: 12** (all `safety_zone_intrusion`, `classification='candidate'`) — intrusion type mix **pleasure_craft 7 · other 4 · high_speed_craft 1** |
| **Zones baseline readiness** (`zone_baseline.py`, in-process) *(§2.10e; live 2026-07-26)* | `baseline_ready` **false** · **0 of 17** trafficked zones sound · binding bar **`days`** (best span **13.6 d**, gap **16.4**; zones meeting days/vessels/sessions = **0/1/2**, best 13.6 d / 10 / 189) · **`standoff_dwell` deferred** until a zone is sound |
| **Composite — `zone_dwell` admission effect** (migration 037) *(§2.8; drift-free counterfactual, live)* | with vs without `zone_dwell`: **1,539 → 1,541 hulls**, **61 → 62 multi-pattern** · **4** hulls gained `zone_dwell` (2 lone LOW; **2 promoted medium→high**: `9689201` gap+rendezvous+intrusion, `9732759` grey-flag+gap+intrusion) · capstone **9895915 unchanged at 4** |
| **Ports asset** (`backend/data/ports_aoi.json`) *(§2.11a–c; live 2026-07-27)* | **190 features** = **180 WPI** (NGA Pub 150, **US-Gov public domain**, no attribution obligation) + **10 OSM** (ODbL, per-feature element URL + tags) · by country Turkey 53 · Greece 28 · Egypt 25 · Iran 12 · Saudi 10 · UAE 9 · Cyprus 8 · Iraq/Kuwait/**Lebanon**/Qatar 6 · Israel 5 · Bahrain/Oman 4 · **Syria 3** · Yemen 3 · Sudan 2 · **`radius_basis='assumed'` on ALL 190** (V 1.5 / S 2.5 / M 3.5 / L 5.0 km; **108 at the 1.5 km floor**) · `coord_precision` **178 arcminute · 2 arcsecond** (Al Fujayrah, Khawr Fakkan) · **7 polygon_centroid · 3 node** · **15 `merges_with` pairs over 26 features** (tightest Gebze ↔ Kaba Burnu **1.40 km**) |
| **Ports — Syria/Lebanon supplement** *(§2.11b; live 2026-07-27)* | WPI alone: **Syria 0 · Lebanon 1** (Sayda, "very small" open roadstead; **no Beirut**) → after the targeted OSM supplement **Syria 3 · Lebanon 6** (Latakia, Tartus, Baniyas · Beirut, Tripoli, Tyr, Jiyyeh, Al Abdeh) + Cyprus Vasilikos/Moni · all resolve by **Arabic and English** name · measured traffic **Tartus 49 · Beirut 45 · Latakia 21** distinct vessels · exclusions measured: marinas dropped on the **tag** (Arwad, Ras al-Basit = `leisure=marina`), inland dry ports (Günaydın **`inland_depth_km`=56.4**), terminal fragments (7 Khalifa, 4 Jebel Ali) |
| **Port visits — four-state distribution** *(§2.11f; live 2026-07-27, **all 190 ports**, 384 h; geometry: per-port radius 1.5–5.0 km all `assumed` · gap-split 6 h · low-speed 1.0 kn · span ≥ 2 h + majority-low · flank 6 h · pad 10 km)* | **1,690 visits · 695 vessels · 34/190 ports** — **`complete` 306 (18.1 %)** · `open_ended` 282 (16.7 %) · `truncated` 270 (16.0 %) · **`presence_only` 832 (49.2 %)** · **`arrival_ts_is_unknown` 1,102 (65.2 %)** · low-speed span **p25 4.1 · p50 8.9 · p75 18.2 · p90 37.2 · max 65.6 h** · type mix cargo 37.0 % / passenger 13.0 % / tug 11.7 % / pleasure_craft 9.9 % / tanker 7.3 % (**~41 % ferry/leisure/port-service**) |
| **Port visits — coverage asymmetry** *(§2.11i; same window/geometry)* | `complete` / `presence_only`: **Haifa 23.5 % / 35.2 %** (213 visits) · Limassol 22.7 % / 47.0 % (66) · Istanbul 15.2 % / 71.2 % (66) · Bur Sa'id 6.6 % / 68.9 % (61) · **Beirut 7.7 % / 55.4 %** (65) · **Tartus 0.0 % / 93.6 %** (78) · **Latakia 0.0 % / 90.9 %** (22) · Ambarli 0.0 % / 71.4 % (7) · **Sayda 0 visits** · quality-filter drops **203 fixes** across the nine, **173 at Haifa** |
| **Port visits — the recorded nulls** *(§2.11l; live 2026-07-27)* | **gap-toward-a-port DEAD**: A1 `candidate` gaps start within 15 km of a port **8.2 %** vs a **19.14 %** null baseline (~**2.3× depleted**), within 50 km **25.1 %** vs **36.91 %**; course within ±45° of the port bearing **26.0 %** vs **25.0 %** chance (n=842) — cause structural: **59.3 %** of gaps starting within 15 km of a port are already `stationary`, leaving `candidate` open-sea (median **92.1 km** from coast) · **listed ports UNBUILDABLE**: OFAC designates **hulls not places**; EO revoked **2025-06-30**, Caesar Act **repealed 2025-12-18** |
| **GFW dark-vessel overlay** (`maritime/adapters/gfw.py`, `/maritime/dark-vessels`) *(§6; post-snapshot, 2026-07-27)* | quarantined analytical **overlay, not a source** — **CC-BY-NC 4.0, non-redistributable → zero-storage** (no table, no JSONL archive, no committed asset; 30-min on-query cache) · **derived gap events only** (GFW exposes no raw AIS) · **≥72 h latency** (every event historical, not live) · **satellite AIS via Kpler** (sees beyond the ~40–60 NM terrestrial range; failure surface independent of `aisstream`) · GFW's own **SAR layer offline since 2026-07-03** · measured East-Med coverage (probe 2026-07-27): dark-vessel events in the **open basin** (~30, FoC-heavy) + Levantine (~15), **0 off the Syrian coast** — does **not** fill the PORT-2/4 weakness (§2.11i) · **0 core facts derive from it** (no intelligence module references GFW) |
| **Port visits — A1 non-independence + composite exclusion** *(§2.11j/k; live 2026-07-27)* | **1,446/1,690 visits (85.6 %)** overlap an A1 gap for the same vessel — overlapping gaps `stationary` **2,787 (61.6 %)** · `outage-excluded` **1,610 (35.6 %)** · `candidate` **71 (1.6 %)** *(PORT-pre's 55.8 % was the looser raw-crossing population)* · **composite admission refused**: tightest bar (visits + `complete`) would flip **96 hulls 1→2 pattern types** and grow the composite **9 %** (all raw sessions **68 %**), and `n_pattern_types == 2` promotes to HIGH on a grey flag — live max on any hull is **4** · guard test asserts the endpoint writes **nothing** |

**Layer commits:** `m1` (9d44739) · `m2a` (c768d9b) · `m2b` (e7d64c2) · `a1` (d715702) ·
`a2` (91c140a) · `a3` (5176c6d) · `composite` (c6a601e).

**Port layer commits (PORT-1/PORT-2/PORT-4):** `port-1` asset+manifest (ea8ce4c) · loader
(9faa71a) · loader tests + sanity measurement (a322bfa) · `port-2` pure module + A1
reconciliation (311ef35) · on-query endpoint + guards (cdd9f6f) · `port-4` `flank_pad_km`
parameter + the excursion-merge correction (ec2c11a) · plus the standing red-test fix that
gated the phase, `test_airport_fallback_at_queen_alia` re-asserted on the
elevation-relative property instead of a coverage-drifting ratio (da80538).

**GNSS layer commits (G1–G5b):** `g1` per-fix quality/migration 027 (945cb51) · `g2`
backfill+runner / deploy (bd38033 · 304c4e6 · 4e08428) · `g3` consumers — coverage mask /
A1 / A2 / test (09ffa05 · 66797f7 · 21a2f51 · 46a8873) · `g3+` outage-window integrity
(2bb6c65 · bbd11de) · `g4a` episode tiers/migration 028 · detector · test (2bc3ad0 ·
3c793fe · 88bb6e8) · `g4b` composite admit/migration 029 · API summary+low rank · fifth
confound/migration 030 (280cc0d · 81b5052 · b81b934) · `g5a` gnss-anomalies endpoint +
frontend + test (bbea5ad · d566dd3 · 540f6fe · 42810a7) · `g5b` jamming-zones endpoint +
frontend + test (ccb5347 · 340ed0e · b5e809f · c8ce2a2).

**IMO-integrity commits (IMO-1/IMO-2, post-snapshot):** `imo-1` axis/migration 035 · pure
module · backfill · tests (e88893b · e726fab · e1aed82 · 4cb2813) · `imo-2` view/migration
036 · collision path · annotation · tests (4ff5da2 · 8725614 · 78d86f5 · 96da069).
**MarineTraffic** (parallel source, §6): migrations 032–034 + adapter/`vessel_xref` (f7be9d7).

**Zones × dwell commits (ZONES-1/2/3/4, post-snapshot, §2.10):** `zones-1` asset/manifest · loader ·
tests (c7a7780 · c605b65 · 45ca321) · `zones-2` mis-sited-node fix + de-flake · dwell module (two
signals/gating/cloaking) · on-query endpoint (5db6bd4 · e45ac04 · bad4b6d) · `zones-3` familiarity
baseline + admission policy · event writer + composite migration 037 (dbd7141 · 68b8b78) · `zones-4`
baseline-readiness surfacing (d759a98) · docs (d1b09cf).

**GFW dark-vessel overlay + DOCS-1 (§6, post-snapshot):** the adapter, the `/maritime/dark-vessels`
endpoint, and the `DarkVessels` panel were committed in **a1dde61** (bundled with UI/NavBar work — the
one-concern-per-commit exception, noted at GFW-pre). Its **§6 provenance section**, the MarineTraffic
**computation-vs-presentation** distinction, the **ERD-range correction** (035–037, not 032–037), and
the §2.8-heading fix are **DOCS-1 (this commit)**.

**Published ERD artifact — behind migrations 035–037 (range corrected 2026-07-27, DOCS-1).**
The Arabic ER-diagram artifact (`scraplan-db-erd.md`) is a **hand-maintained** Mermaid document —
there is **no generator script**, so "regenerating" it means editing the `.md` by hand from the
live schema (`\d vessel_identity`, etc.). Re-checked against the file: it **already carries
`mt_vessel_states` + `vessel_xref`** (migrations 032–034, its migration list ends at `034`), so the
prior note's "behind 032–037" was **itself stale** — the MarineTraffic tables are present. It is
actually **behind 035–037**: the **IMO-integrity columns** on `vessel_identity`
(`imo_quality`/`imo_flags`/`imo_method`/`imo_computed_at`, migration 035) are **absent**, and the
composite **view** edits (036 hull-key rule, 037 `zone_dwell` disjunct + `n_zone_dwell`) are not
reflected — though the ERD diagrams **tables, not views**, so the view edits are a migration-list
note, not a diagram change. The port layer (§2.11) added **no migration and no table**, so priority 5
did not move the ERD — which is itself the point of an on-query, annotation-only layer.
Regeneration is doable from the repo/DB (a bounded manual edit: add the four `imo_integrity`
columns to the `vessel_identity` entity + its column table, and extend the migration list with
035/036/037). **Not published here** — an outward-facing Arabic publish; prepared/reported only,
the redeploy is the owner's decision.

**Shared AOI:** `backend/geo_area.py` — seven countries (Syria, Turkey, Iraq, Jordan,
Lebanon, Israel, Cyprus), AEQD projection, `geo_source='scraplan-aoi'`; maritime
territorial-sea band **12 NM = 22.224 km** (UNCLOS Art. 3).
