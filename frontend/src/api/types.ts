/**
 * API Contract v1 — types derived verbatim from README-backend.md (FROZEN).
 * This module and client.ts are the only places that know the wire shapes.
 * snake_case is the contract; do not camelize.
 */

export interface StateVector {
  icao24: string;
  callsign: string; // may be "" or padded with trailing spaces — trim for display
  origin_country: string;
  time_position: number | null;
  last_contact: number | null;
  longitude: number | null;
  latitude: number | null;
  baro_altitude: number | null; // meters
  geo_altitude: number | null; // meters
  on_ground: boolean;
  velocity: number | null; // m/s
  true_track: number | null; // degrees, 0 = north
  vertical_rate: number | null; // m/s
  squawk: string;
  spi: boolean;
  position_source: number | null;
  category: number | null;
  snapshot_time: number;
  fetched_at: string | null;
  // geo classification (P3) — present on classified rows (adsbexchange today;
  // null on pre-backfill opensky/airplanes_live). Optional: /live omits them.
  country?: string | null;
  near_country?: string | null;
  position_quality?: string | null; // 'confirmed' | 'no_position' | 'suspect'
  distance_km?: number | null;
  geo_source?: string | null;
}

export interface BBox {
  lamin: number;
  lomin: number;
  lamax: number;
  lomax: number;
}

export interface LivePayload {
  snapshot_time: number;
  ts: string;
  bbox: BBox | null;
  count: number;
  source: "db" | "archive";
  states: StateVector[];
}

export interface HistoryResponse {
  icao24: string;
  from: number;
  to: number;
  count: number;
  limit: number;
  offset: number;
  has_more: boolean;
  points: StateVector[]; // ASC by time
}

export type StatsBucketSize = "1m" | "5m" | "15m" | "1h" | "1d";

export interface StatsBucket {
  bucket_start: string; // ISO
  state_count: number;
  ground_count: number;
  airborne_count: number;
  max_baro_altitude: number | null;
  max_geo_altitude: number | null;
  max_velocity: number | null;
  snapshot_count: number;
  avg_aircraft_per_snapshot: number | null;
}

export interface StatsResponse {
  from: number;
  to: number;
  bucket: StatsBucketSize;
  buckets: StatsBucket[]; // only non-empty buckets — never assume zero-fill
}

export interface SearchResponse {
  snapshot_time: number;
  source: "db" | "archive";
  count: number;
  states: StateVector[];
}

export interface HealthResponse {
  ok: boolean;
  db: {
    connected: boolean;
    latest_snapshot_time: number | null;
    snapshot_age_seconds: number | null;
    stale: boolean;
  };
  archive: { latest_json_present: boolean; age_seconds: number | null };
  ingestion: { fresh: boolean; lag_seconds: number | null };
}

export interface StreamMessage {
  type: "snapshot";
  data: LivePayload;
}

/* --- v1 additive: best-effort enrichment (adsbdb + planespotters) ---
 * Everything nullable; route is STANDING data (callsign → usual route),
 * not the actual filed flight plan. */

export interface AirportInfo {
  icao: string | null;
  iata: string | null;
  name: string | null;
  municipality: string | null;
  country: string | null;
  lat: number | null;
  lon: number | null;
}

export interface RouteInfo {
  callsign: string;
  airline: string | null;
  origin: AirportInfo;
  destination: AirportInfo;
}

export interface PhotoInfo {
  thumb: string;
  page: string | null; // photo page — attribution link (Planespotters terms)
  photographer: string | null;
}

/** Military overlay state — StateVector + airframe extras. */
export interface MilStateVector extends StateVector {
  source: string;
  actype: string | null;
  acdesc: string | null;
  reg: string | null;
}

export interface MilitaryResponse {
  available: boolean;
  snapshot_time: number | null;
  age_seconds: number | null;
  count: number;
  states: MilStateVector[];
}

/** ADS-B Exchange overlay (adsb_scraper) — DB-backed, AOI-bounded; every row
 * carries the P3 geo classification (country/near_country/position_quality). */
export interface AdsbResponse {
  available: boolean;
  snapshot_time: number | null;
  age_seconds: number | null;
  count: number;
  states: StateVector[];
}

/** Maritime AIS overlay — MMSI-keyed vessels, knots-native speed.
 * data_origin is terrestrial for free AIS (open ocean is dark); AIS is
 * self-reported and spoofable. */
export interface Vessel {
  mmsi: string | null; // AIS identity; null for MarineTraffic (ship_id-keyed)
  ship_id?: string | null; // MarineTraffic internal id (mmsi-less source)
  lat: number | null;
  lon: number | null;
  speed: number | null; // SOG, knots
  course: number | null; // COG, degrees
  heading: number | null;
  nav_status: number | null;
  timestamp: string | null;
  imo: string | null;
  name: string | null;
  callsign: string | null;
  ship_type_code: number | null;
  ship_type: string | null;
  destination: string | null;
  draught: number | null;
  flag?: string | null; // source-reported flag (MarineTraffic); AIS derives via MID
  source: string;
  data_origin: string;
}

/** Per-source freshness for a maritime payload (MT-FRESH, backend 3ec3468).
 *
 * A blended `source=all` response mixes a LIVE aisstream feed with a possibly-STALE
 * MarineTraffic store, so one scalar cannot describe the payload honestly. This is the
 * AUTHORITATIVE freshness structure — read this, never `data_vintage`, whenever more
 * than one source can contribute.
 *
 * NOTE: the backend builds this `for s in picked`, so it only ever describes the
 * REQUESTED sources. A source that was not asked for has no entry (not a zero row) —
 * so an un-requested source's age is simply unknown to the client. */
export interface SourceVintage {
  source: string; // 'aisstream' | 'marinetraffic'
  last_fix: string | null; // freshest fix in this source's store, ISO
  age_seconds: number | null; // now − last_fix
  rows: number; // rows THIS source contributed to THIS payload
  stale: boolean; // age beyond the live-overlay window (180 s) — not live data
}

export interface MaritimeResponse {
  available: boolean;
  snapshot_time: number | null;
  age_seconds: number | null;
  count: number;
  /** WORST CASE only: the OLDEST contributing source's max ts. Never read this as "the"
   *  vintage of a blend — it deliberately under-claims so it can't mislead. Use sources[]. */
  data_vintage: string | null;
  /** the authoritative per-source freshness + contribution */
  sources: SourceVintage[];
  states: Vessel[];
}

/** DASH-4 — ONE vessel's live payload, keyed by MMSI (`GET /maritime/vessel?mmsi=`).
 *
 * This is what closed the gap DASH-2f had to work around: aircraft and satellites already
 * had a single-entity route to their LIVE contract, vessels did not, so the entity panel
 * rendered the SUMMARY (`/search/vessel`) and NAMED the four fields it could not reach.
 * `vessel` here is the SAME `Vessel` the full `/maritime` list carries — backend-verified
 * byte-identical for the same hull, not a look-alike.
 *
 * ⚠ 19 FIELDS, not 20. The backend model drops `length_m`/`beam_m` that exist in the
 * collector's snapshot, so they never arrive on this contract either. The four fields 2f
 * named — heading · destination · nav_status · draught — are all inside the 19 and do
 * arrive. Do not wait for two fields that are not served.
 *
 * ⛔ EVERY FIELD IS REQUIRED, because the backend emits every key on both states (nulls,
 * not omissions) — verified against the live responses for `live` and `last_known` alike.
 * Optional-marking a key that is always present would let a consumer treat "the server
 * said null" and "the server said nothing" as the same thing, which is the distinction
 * this whole contract exists to keep (2c′/2e). */
export interface VesselLiveResponse {
  mmsi: string; // the MMSI queried, echoed back
  /** ⛔ A CLAIM, not decoration. `live` = in the current snapshot inside the 180 s window.
   *  `last_known` = the MMSI is KNOWN but is NOT transmitting now, and `vessel` is its last
   *  stored fix. Rendering the two identically re-creates the confusion the panel exists to
   *  avoid — a real position from hours ago is not a current one. */
  state: "live" | "last_known";
  /** the SAME contract `/maritime` serves — never a second vessel shape */
  vessel: Vessel;
  /** now − vessel.timestamp. What keeps a `last_known` fix legible as old rather than current. */
  age_seconds: number | null;
  snapshot_time: number | null; // live only; null on last_known
  snapshot_age_seconds: number | null; // live only; age of the snapshot FILE
  /** last_known only — the vintage of the STATIC identity (name/imo/callsign/ship_type).
   *  A SECOND vintage: AIS position reports carry no static data, so the identity is not
   *  from the same moment as the fix. Shown, never blended into it. */
  identity_as_of: string | null;
  /** ⛔ Contract fields NULL because the DEVICE did not report them — computed per vessel,
   *  which is what replaces 2f's hand-written absence list. A hull that broadcasts its
   *  draught no longer reads as missing it.
   *  ⚠ `ship_id` and `flag` are deliberately ABSENT from this list: they are null for every
   *  AIS vessel by source shape (backend-measured 0/556), so they are structural nulls, not
   *  device silence. Never render them as "not reported". */
  not_reported: string[];
  n_fixes: number | null; // last_known only — stored fixes behind the claim
}

/** A1 — AIS gap: an underway vessel that stopped transmitting for ≥1h in good
 * AIS coverage, not explained by a receiver/feed outage. Behavioural analysis —
 * an INVESTIGATION LEAD, never proof of "going dark". snake_case is the wire. */
export interface GapEvent {
  mmsi: string;
  imo: string | null;
  name: string | null;
  flag_country: string | null;
  ship_type: string | null;
  start_ts: string; // ISO — last fix before the gap
  end_ts: string | null; // ISO — first fix after (null = still dark)
  duration_s: number | null;
  start_lat: number | null;
  start_lon: number | null;
  end_lat: number | null;
  end_lon: number | null;
  start_country: string | null; // whose territorial waters it went dark in (M1)
  start_near_country: string | null;
  distance_at_start_km: number | null;
  speed_at_last_fix: number | null;
  nav_status: number | null;
  coverage_cell_density: number | null;
  classification: string; // "candidate"
  method: string;
}

export interface GapsResponse {
  available: boolean;
  count: number;
  classification: string;
  method: string;
  caveat: string; // honest limitation — MUST be shown to the user
  events: GapEvent[];
}

/** G4a — a per-vessel GNSS-anomaly episode. classification is the tier:
 * 'candidate' = individual (a real vessel spoofed/jammed, a lead); 'area-jamming' =
 * an environmental-jamming victim; 'glitch' = a bad/test transmitter. start_lat/lon
 * is the first implausible fix (the decoy landing for decoy episodes). A data-quality
 * event, not proof of intent. snake_case is the wire. */
export interface GnssEvent {
  mmsi: string;
  imo: string | null;
  name: string | null;
  flag_country: string | null;
  ship_type: string | null;
  start_ts: string;
  end_ts: string | null;
  duration_s: number | null;
  start_lat: number | null;
  start_lon: number | null;
  end_lat: number | null;
  end_lon: number | null;
  classification: string; // candidate | area-jamming | glitch
  gnss_type: string | null; // individual | area | glitch
  decoy_match: boolean | null;
  n_implausible_fixes: number | null;
  implied_speed_max: number | null;
  inland_km_max: number | null;
  affected_lat: number | null; // inferred real position (last-good before the episode)
  affected_lon: number | null;
  method: string;
}

export interface GnssAnomaliesResponse {
  available: boolean;
  count: number;
  method: string;
  caveat: string; // honest limitation — MUST be shown to the user
  anomalies: GnssEvent[];
}

/** G5b — a jamming-zone cell × hourly window (0.1° cell). variant='broadcast' = where
 * spoofed positions LANDED (the decoy); 'affected' = where vessels actually WERE when
 * jammed (outage-subtracted). "Where GPS was denied" — not proof of intent. */
export interface ZoneCell {
  clat: number;
  clon: number;
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
  window_start: string; // ISO — the hourly window
  distinct_vessels: number;
  variant: string; // broadcast | affected
}

export interface JammingZonesResponse {
  variant: string;
  count: number;
  min_vessels: number;
  caveat: string; // honest limitation — MUST be shown to the user
  cells: ZoneCell[];
}

/** A2 — rendezvous: ONE vessel in a loitering pair (≤500 m, slow, ≥1h, offshore).
 * Identity only; the behaviour lives on the parent event. snake_case is the wire. */
export interface RendezvousVessel {
  mmsi: string;
  imo: string | null;
  name: string | null;
  flag_country: string | null;
  ship_type: string | null;
}

/** A2 — rendezvous: TWO vessels that loitered close together in open sea away
 * from ports — a POTENTIAL ship-to-ship transfer. Behavioural analysis, never
 * proof: each row is an INVESTIGATION LEAD (most are legitimate). suspicion_rank
 * PRIORITISES review order (high→low); it does NOT accuse. */
export interface RendezvousEvent {
  primary: RendezvousVessel;
  partner: RendezvousVessel;
  start_ts: string; // ISO
  end_ts: string | null; // ISO — null = still together
  duration_min: number | null;
  min_distance_m: number | null;
  both_max_speed: number | null;
  offshore_km: number | null;
  start_country: string | null; // whose AOI waters it began in (M1)
  start_near_country: string | null;
  start_lat: number | null;
  start_lon: number | null;
  suspicion_rank: "high" | "medium" | "low";
  classification: string; // "candidate"
  method: string;
}

export interface RendezvousResponse {
  available: boolean;
  count: number;
  rank: "all" | "high" | "medium" | "low";
  method: string;
  caveat: string; // honest limitation — MUST be shown to the user
  events: RendezvousEvent[];
}

/** A3 — identity change: a vessel broadcasting a different name/flag/MMSI over
 * time (potential disguise). `reflag` = same IMO (the permanent anchor) under a
 * new MMSI+flag — the shadow-fleet signature; `rename`/`retype` = a confirmed
 * name/type change under one MMSI. Behavioural analysis, never proof: a
 * documented change for investigation — a rename/re-flag is USUALLY LEGAL.
 * suspicion_rank PRIORITISES review order (high→low); it does NOT accuse. */
export interface IdentityChangeEvent {
  mmsi: string;
  imo: string | null; // the permanent hull anchor (present on reflag)
  change_type: "reflag" | "rename" | "retype";
  field: string | null;
  old_value: string | null; // e.g. "SANDHYA / Cameroon" — rename shows a name
  new_value: string | null; // e.g. "VYCHEGDA / Russia"
  old_mmsi: string | null; // reflag only
  new_mmsi: string | null; // reflag only
  old_flag: string | null; // reflag only
  new_flag: string | null; // reflag only
  old_name: string | null; // reflag only
  new_name: string | null; // reflag only
  last_old_ts: string | null; // ISO — last fix under the old identity
  first_new_ts: string; // ISO — first fix under the new identity
  gap_hours: number | null;
  sustained_new_rows: number | null;
  suspicion_rank: "high" | "medium" | "low";
  correlated: boolean; // shares an A1 gap / A2 rendezvous on the same hull
  correlated_events: Record<string, number>; // event_type:classification -> count
  classification: string; // "candidate"
  method: string;
}

export interface IdentityChangesResponse {
  available: boolean;
  count: number;
  rank: "all" | "high" | "medium" | "low";
  method: string;
  caveat: string; // honest limitation — MUST be shown to the user
  events: IdentityChangeEvent[];
}

/** CAPSTONE — hull composite: aggregates the three behavioural patterns
 * (A1 gap / A2 rendezvous / A3 identity change) per hull, STITCHED BY IMO
 * across a vessel's identities — a hull that went dark AND met another AND
 * changed identity is ONE picture. composite_rank PRIORITISES (multi-pattern +
 * grey/sanctions → high); it does NOT accuse. A composite of leads is a STRONGER
 * lead, still NOT proof. snake_case is the wire. */
export interface CompositeIdentity {
  mmsi: string;
  name: string | null;
  flag_country: string | null;
  ship_type: string | null;
}

/** One cross-pattern event on the hull's timeline. `summary` is already a
 * human-readable phrase ("went dark 4.0h in Turkey waters") — display as-is. */
export interface CompositeTimelineEntry {
  event_type: "ais_gap" | "rendezvous" | "identity_change";
  ts: string; // ISO
  summary: string;
  rank: string | null;
}

/** IMO-2 spine-integrity verdict (migration 035); null = no IMO / not-yet-checked. */
export type ImoQuality = "valid" | "checksum_fail" | "malformed" | "contested";

export interface CompositeHull {
  hull: string; // the IMO (or MMSI when no IMO)
  is_imo: boolean;
  imo: string | null;
  identities: CompositeIdentity[]; // may be >1 if re-flagged
  n_gaps: number;
  n_rendezvous: number;
  n_identity_changes: number;
  n_pattern_types: number; // up to 5 (gap/rendezvous/identity/gnss/imo_integrity)
  composite_rank: "high" | "medium" | "low";
  involves_grey: boolean;
  aoi_waters: string[]; // AOI countries whose waters the hull's events touched
  rendezvous_partners: string[];
  first_event_ts: string | null;
  last_event_ts: string | null;
  timeline: CompositeTimelineEntry[];
  imo_quality: ImoQuality | null; // IMO-2: spine caveat on the lead's reliability
  imo_flags: string[];
}

export interface CompositeResponse {
  available: boolean;
  count: number;
  min_pattern_types: number;
  method: string;
  caveat: string; // honest limitation — MUST be shown to the user
  hulls: CompositeHull[];
}

/** Best-effort vessel photo from Wikimedia Commons (sparse coverage). */
export interface VesselEnrichment {
  mmsi: string;
  photo: PhotoInfo | null;
  license: string | null;
}

/** Orbital elements — the client propagates position locally (SGP4). */
export interface SatelliteElement {
  norad_id: number;
  name: string;
  tle1: string;
  tle2: string;
  epoch: string | null;
}

export interface SatellitesResponse {
  available: boolean;
  group: string;
  count: number;
  fetched_at: string | null;
  elements: SatelliteElement[];
}

export interface SatelliteGroup {
  group: string;
  count: number;
  fetched_at: string | null;
}

export interface SatelliteGroupsResponse {
  groups: SatelliteGroup[];
}

/** SATCAT identity for one satellite (CelesTrak SATCAT, cached offline).
 * available=false when the norad_id has no SATCAT row yet. All facts carry
 * provenance (source + satcat_snapshot_date). Orbital motion is NOT here —
 * the client derives it from the TLE (orbit.ts); this is identity only. */
export interface SatelliteEnrichment {
  norad_id: number;
  available: boolean;
  international_designator: string | null;
  object_name: string | null; // SATCAT's name (may differ from TLE)
  object_type: string | null; // raw code: PAY | R/B | DEB | UNK
  object_type_label: string | null; // payload | rocket_body | debris | unknown
  owner: string | null; // CelesTrak owner code (e.g. US, PRC)
  owner_name: string | null; // decoded country/organization; null if unknown
  launch_date: string | null; // ISO date
  launch_site: string | null;
  decay_date: string | null; // ISO date; null = on-orbit
  ops_status: string | null;
  rcs_m2: number | null; // radar cross-section, m²
  source: string | null; // 'celestrak_satcat'
  satcat_snapshot_date: string | null; // ISO date — SATCAT dump version
}

/** A predicted satellite reentry (Space-Track TIP), joined to SATCAT identity.
 * lat/lon is the NOMINAL predicted impact point — a single point with LARGE
 * uncertainty (window_min = ± minutes around decay_epoch). country/near_country
 * classify that nominal point against the seven-country AOI. */
export interface ReentryEvent {
  norad_id: number;
  object_name: string | null;
  owner_name: string | null;
  object_type: string | null;
  decay_epoch: string | null; // ISO — predicted nominal reentry time
  window_min: number | null; // ± minutes
  lat: number | null;
  lon: number | null;
  direction: string | null;
  next_report: number | null;
  high_interest: boolean | null;
  country: string | null; // AOI INSIDE
  near_country: string | null; // AOI NEAR
  distance_km: number | null;
  aoi: boolean; // INSIDE or NEAR the region
  msg_epoch: string | null;
  source: string | null;
}

export interface ReentryResponse {
  available: boolean;
  count: number;
  inside: number;
  near: number;
  region: string;
  events: ReentryEvent[];
}

export interface AircraftEnrichment {
  icao24: string;
  registration: string | null;
  aircraft_type: string | null;
  operator: string | null;
  operator_country: string | null;
  photo: PhotoInfo | null;
  route: RouteInfo | null;
}

/* --- v1 additive: entity search (E1 — vessel) ---
 * The search/monitoring tab's first entity. A VesselSummary is the collapsed
 * "everything about this vessel" card; selecting it expands to the full track
 * (VesselTrackPoint[]). Quality columns travel WITH each fix so a spoofed fix
 * stays visible in the track. snake_case is the wire. */

export interface VesselTrackPoint {
  ts: string; // ISO-8601 UTC
  lat: number | null;
  lon: number | null;
  speed: number | null; // SOG, knots
  course: number | null; // COG, degrees
  heading: number | null;
  nav_status: number | null;
  position_quality: string | null; // plausible | suspect | implausible
  anomaly_flags: string[] | null; // e.g. ['impossible_speed','teleport_return']
  source: string;
  data_origin: string;
}

export interface VesselTrackResponse {
  mmsi: string;
  from: number | null; // window start (epoch s), null = open
  to: number | null;
  count: number;
  limit: number;
  offset: number;
  has_more: boolean; // true → narrow the window
  points: VesselTrackPoint[]; // ASC by time
}

/** Collapsed vessel card: identity + last known fix (the data vintage) +
 * behavioural event counts. Counts tally investigation leads, not proof. */
export interface VesselSummary {
  mmsi: string;
  imo: string | null;
  name: string | null;
  flag_country: string | null;
  ship_type: string | null;
  ship_type_code: number | null;
  callsign: string | null;
  length_m: number | null;
  beam_m: number | null;
  station_type: string | null;
  last_ts: string | null; // last fix — data vintage
  last_lat: number | null;
  last_lon: number | null;
  last_speed: number | null;
  last_position_quality: string | null;
  last_anomaly_flags: string[] | null;
  imo_quality: ImoQuality | null; // IMO-2: spine-integrity verdict on this IMO
  imo_flags: string[] | null;
  n_fixes: number; // track density over the frozen span
  n_gaps: number;
  n_rendezvous: number;
  n_identity_changes: number;
  n_gnss: number;
  n_pattern_types: number; // how many of the 4 patterns are present
}

export interface VesselSearchResponse {
  query: string;
  entity: string; // "vessel" (E1)
  count: number;
  caveat: string; // honest limitation — MUST be shown to the user
  matches: VesselSummary[];
}

/* --- v1 additive: entity search (E2 — aircraft) ---
 * Air has no behavioural-event layer, so an AircraftSummary is a PRESENCE card
 * (identity + last-seen + AOI status); the track is the expansion, served by the
 * existing /history. callsign is the current/last flight id (not the airframe). */
export interface AircraftSummary {
  icao24: string;
  callsign: string | null;
  registration: string | null;
  aircraft_type: string | null;
  icao_type: string | null;
  operator: string | null;
  operator_country: string | null;
  origin_icao: string | null; // standing route (flight_routes by callsign)
  origin_name: string | null;
  dest_icao: string | null;
  dest_name: string | null;
  last_ts: string | null; // last-seen — LIVE for an airborne aircraft
  last_lat: number | null;
  last_lon: number | null;
  baro_altitude: number | null; // meters
  geo_altitude: number | null;
  on_ground: boolean | null;
  velocity: number | null; // m/s
  true_track: number | null;
  country: string | null; // AOI classification of the last fix (P3)
  near_country: string | null;
  position_quality: string | null;
  distance_km: number | null;
  source: string | null; // feed of the last fix
  first_seen: string | null; // "tracked since"
}

export interface AircraftSearchResponse {
  query: string;
  entity: string; // "aircraft" (E2)
  count: number;
  caveat: string; // honest limitation — MUST be shown to the user
  matches: AircraftSummary[];
}

/* --- v1 additive: entity search (E3 — satellite) ---
 * No stored fixes: the summary composes layers whose vintages DIFFER (identity
 * SATCAT ~2026 / orbit derived-SGP4 live / strategic UCS FROZEN 2023 / passes
 * forward ~24 h prediction). The TLE travels with the summary so the client
 * propagates the ground-track (SGP4, lib/orbit) — the "track" is computed. */
export interface SatelliteSummary {
  norad_id: number;
  object_name: string | null;
  // identity (SATCAT), vintage identity_vintage
  international_designator: string | null;
  object_type: string | null;
  object_type_label: string | null;
  owner_name: string | null;
  launch_date: string | null;
  identity_vintage: string | null; // satcat_snapshot_date
  // orbit (derived-SGP4), vintage orbit_vintage
  orbit_class: string | null;
  apogee_km: number | null;
  perigee_km: number | null;
  inclination_deg: number | null;
  period_min: number | null;
  is_geostationary: boolean | null; // GEO → ground-track is a stationed band
  orbit_vintage: string | null; // tle_epoch of the derived orbit
  // strategic (UCS), vintage strategic_vintage (FROZEN 2023)
  users: string | null;
  purpose: string | null;
  operator_country: string | null;
  strategic_vintage: string | null; // ucs_snapshot_date
  // AOI passes (forward-window prediction)
  n_passes: number;
  is_persistent: boolean;
  pass_window_start: string | null;
  pass_window_end: string | null;
  // TLE (for client-side ground-track propagation)
  tle1: string | null;
  tle2: string | null;
  tle_epoch: string | null;
}

export interface SatelliteSearchResponse {
  query: string;
  entity: string; // "satellite" (E3)
  count: number;
  caveat: string; // honest limitation — MUST be shown to the user
  matches: SatelliteSummary[];
}

/* --- v1 additive: entity search (E4 — airport → landings) ---
 * The one-to-many entity: airport (from the committed gazetteer) → the aircraft
 * that landed there (DERIVED candidates, not proven landings; coverage-bounded). */
export interface AirportSummary {
  icao: string;
  iata: string | null;
  name: string | null;
  municipality: string | null;
  country: string | null;
  lat: number;
  lon: number;
  elevation_ft: number | null;
  type: string | null;
}

export interface LandingCandidate {
  icao24: string;
  callsign: string | null;
  registration: string | null;
  landing_ts: string; // ISO
  signal: "grounded" | "approach";
  min_altitude_m: number | null;
  n_fixes: number;
  source: string | null;
}

export interface AirportSearchResponse {
  query: string;
  entity: string; // "airport" (E4)
  count: number; // airports resolved
  caveat: string; // honest limitation — MUST be shown to the user
  window_hours: number;
  matches: AirportSummary[]; // the result list
  airport: AirportSummary | null; // the resolved airport whose landings are returned
  candidates: LandingCandidate[];
}

/* --- v1 additive: area-search (D4 — draw a box → what crossed it) ---
 * NOT an entity — a MODE. Each entry is a light discovery row; opening it reuses
 * the E1–E3 search-by-id endpoints for the full summary + detail. */
export interface AreaEntry {
  id: string; // icao24 | mmsi | NORAD (as str)
  label: string;
  sublabel: string | null;
  lat: number | null;
  lon: number | null;
  ts: string | null;
}

export interface AreaSearchResponse {
  box: number[]; // [lat_min, lon_min, lat_max, lon_max]
  since: number | null;
  until: number | null;
  window_hours: number;
  caveat: string; // honest limitation — MUST be shown to the user
  air: AreaEntry[];
  sea: AreaEntry[];
  space: AreaEntry[];
}

// --- D5 alerts (the monitor's resting dashboard) ---------------------------

export type AlertFamily = "emergency" | "military" | "maritime" | "implausible";
export type AlertSeverity = "high" | "medium" | "low" | "info";
export type AlertDomain = "air" | "sea" | "space";

/** The seven AOI countries, ISO 3166-1 alpha-2 (backend/country_iso.py AOI_NAME_TO_ISO).
 *
 * Use this for the REQUEST side only — `?country=` is validated against exactly these
 * and 400s otherwise. The RESPONSE side (`Alert.countries`) is deliberately `string[]`,
 * NOT this union: `to_iso` passes an unrecognised country name through VERBATIM rather
 * than dropping it (flag-never-delete), so a value outside the seven is possible and
 * must not be a type error. */
export type AoiCountry = "SY" | "TR" | "IQ" | "JO" | "LB" | "IL" | "CY";

/** Which axis ordered `AlertsResponse.alerts`. The request param is regex-validated to
 *  exactly these two (api.py), and the response echoes it back. */
export type AlertSort = "severity" | "importance";

export interface AlertEntityRef {
  kind: "aircraft" | "vessel" | "satellite";
  id: string; // icao24 | mmsi | hull(IMO) | NORAD (as str)
  label: string | null;
  /**
   * DASH-1: the key that addresses the LIVE payload — MMSI for a vessel, icao24 for an
   * aircraft, NORAD for a satellite. `id` is the DURABLE identity, which for a maritime
   * composite is the IMO; every live vessel payload is MMSI-keyed, so `id` alone cannot
   * open it. For a vessel this is the MMSI with the most recent fix (IMO→MMSI is
   * one-to-many across a re-flag). Null = nothing heard from any of the hull's MMSIs,
   * i.e. there is honestly no live payload to open.
   */
  live_key: string | null;
  /** DASH-1: the hull's OTHER MMSIs, most-recent first — a re-flagged vessel's earlier
   *  identities are real history, carried rather than discarded. */
  alt_keys: string[];
}

/** DASH-5 — one kept piece of evidence. ⛔ A SNAPSHOT, not a reference: the feed is stateless
 *  and rebuilt per request, so an item collected today is absent once its window rolls past.
 *  The claim is stored as it WAS when collected. */
export interface CaseItem {
  dedup_key: string;
  label: string;
  domain?: string | null;
  signal_type?: string | null;
  severity?: string | null;
  headline?: string | null;
  why?: string | null;
  /** ⛔ Kept with the rest and never dropped — a lead in a report without what it does NOT mean
   *  is the failure mode models.py names, made durable. */
  caveat?: string | null;
  countries?: string[];
  alert_ts?: string | null;
  note?: string | null;
  added_at: string;
  /** Whether the same key is in the CURRENT feed. ⚠ INFORMATION, never a correction: an item
   *  that has left the window has not been disproven, and the snapshot is not refreshed. */
  still_live?: boolean;
}

export interface PatternSpan {
  type: string;
  start: string;
  /** null = the interval is still OPEN, not zero-length. */
  end?: string | null;
}

export interface Alert {
  /* ── DASH-3: the epistemic axis. Two clocks, never one. ──────────────────────────────
   * `ts` is unchanged and every existing consumer keeps working; these sit beside it.
   * ⛔ WHY: every family put an OBSERVATION-shaped time in `ts`, and the satellite family
   * put the FUTURE there (the pass start). Sorting one list by `ts` interleaves a prediction
   * about tomorrow with an observation from last week and calls both "when it happened".
   * OGC O&M splits `phenomenonTime` (when it applies) from `resultTime` (when it was
   * produced) for exactly this, and gives `resultTime < phenomenonTime` as the machine test
   * for a prediction — which is what `is_prediction` is. */
  phenomenon_start?: string | null;
  /** ⛔ null = an INSTANT, or an interval still OPEN. Never "zero length", never "unknown". */
  phenomenon_end?: string | null;
  result_time?: string | null;
  /** ⛔ null = UNKNOWN novelty. Never "new", never the epoch — rendering unknown as new is
   *  the fail-open reading, and this project's rule is that a guard fails closed. */
  first_seen_at?: string | null;
  /** Age of the underlying observation in seconds. NEGATIVE for a future satellite pass —
   *  do not clamp it, the sign is the information. */
  observed_age_s?: number | null;
  /** ⛔ A LIST. A hull may meet several vessels; a single slot would silently drop the rest.
   *  Empty on every non-rendezvous family. NEVER inferred from proximity in a list. */
  counterparts?: AlertEntityRef[];
  /** ⛔ ONE WINDOW PER PATTERN. The alert's own phenomenon_start/_end is the hull's ENVELOPE —
   *  measured on the live feed, ZEYNEP I spans 29 days while its three patterns are three
   *  separate moments inside it. Rendered as a lane the envelope says all three ran all month.
   *  ⚠ `end: null` means OPEN (an unclosed gap), never zero-length. */
  pattern_spans?: PatternSpan[];
  /** True total; `pattern_spans` is capped at 60. A consumer showing the cap without this
   *  number would imply it saw everything. */
  n_pattern_spans?: number;
  is_prediction?: boolean;

  /* ── DASH-4a: collective triage. Shared by every analyst — this deployment has no
   * per-analyst identity by decision, so `reviewing` means SOMEONE is on it. ────────────── */
  /** null = OPEN, and open is the ABSENCE of a stored row, not a third value. */
  triage?: "reviewing" | "closed" | null;
  triage_note?: string | null;
  /** ⛔ The thing CHANGED after it was triaged. A closed alert whose signature moved is not
   *  still closed — it is a new statement about a known object, and hiding it behind a stale
   *  verdict is how a triage system starts suppressing the updates it exists to surface. */
  triage_stale?: boolean;
  /** The server's own `notify.identity.change_sig`. ⛔ ECHO IT BACK when setting a state —
   *  never re-derive it in the browser. Mirroring per-family Python rules here is how the
   *  first version made every triaged item instantly stale. */
  change_sig?: string;
  /** v1 ADDITIVE: the server-computed identity key — ALWAYS prefer it over the local mirror. */
  dedup_key?: string;

  domain: AlertDomain;
  family: AlertFamily;
  signal_type: string;
  severity: AlertSeverity;
  severity_basis: string; // WHY this severity — ALWAYS shown (keeps info/low honest)
  entity_ref: AlertEntityRef;
  /**
   * DASH-1: the AOI countries this alert TOUCHES, ISO alpha-2. A LIST, never a scalar —
   * a hull's events span several AOI waters and a satellite pass crosses borders, so
   * collapsing it would drop observed facts (measured: 35 of 213 alerts carry two).
   *
   * A GEOGRAPHIC fact — WHERE the thing is. Never confuse it with `flag_country` /
   * `origin_country` / `operator_country`, which say WHOSE it is.
   *
   * Empty = not attributable to an AOI country (outside the seven, or no position to
   * classify). Empty is an honest absence, not "unknown". Typed `string[]` and not
   * AoiCountry[] on purpose — see AoiCountry.
   */
  countries: string[];
  /**
   * DASH-1: a DISPLAY-ORDERING rank (lower sorts first), ORTHOGONAL to `severity`.
   * Severity = "how dangerous is this claim?"; importance = "how much does this deserve
   * the analyst's eye in a country window?". Both are true at once: a military alert is
   * severity="info" AND importance=1.
   *
   * ⚠ NOT a threat score. Do not colour a card by it, do not sum it, never present it as
   * risk — `severity` + `severity_basis` remain the only honest register for that.
   */
  importance: number;
  ts: string | null;
  /**
   * ⚠ ENGLISH, and NOT for display on a maritime card any more (DASH-CARD).
   * `"2 gaps · Syria waters · Togolese Republic"` — unreadable for this UI's audience, and
   * measured dishonest on 71/400: it lists only the patterns it counted while
   * `n_pattern_types` counted more, so it showed ONE reason for a rank caused by two.
   * Maritime cards render `headline`/`why`/`caveat` instead. The other families still have
   * nothing else, so they still show this — see the three fields below.
   */
  summary: string;
  /**
   * BE-SUMMARY-1 — the Arabic register. Present on `family: "maritime"` ONLY.
   *
   * ⛔ NULL MEANS "NO ARABIC RECORD YET", NEVER "NO WARNING APPLIES". Measured 2026-08-02:
   * 106/106 maritime alerts carry all three; 0/218 of the other families carry any. When
   * they are null the card must SAY NOTHING in their place — it must not fall back to the
   * English `summary` for a maritime row, and must not synthesise text.
   *
   *  headline — the occurrence as observed: what, where, how many.
   *  why      — why it ranks here, carrying `severity_basis` in readable Arabic.
   *  caveat   — what this does NOT mean. The project's written commitment; ⛔ never
   *             truncated, never folded below the second layer.
   */
  headline: string | null;
  /** NEWS-1's short news title, composed in alerts.py from the same structured facts the
   *  headline states at length. ⚠ Empty for a family that has none — a renderer falls back to
   *  `headline` rather than inventing one. It IS on the wire and was simply never declared. */
  lede?: string | null;
  why: string | null;
  caveat: string | null;
  source: string;
  vintage: string | null; // freshness of the underlying data
  live: boolean; // is the underlying feed live right now?
  matched_query: string | null; // additive seam (search↔alert) — null today
  /**
   * MID-ISO-1: the registry's ISO alpha-2, derived from the MMSI's ITU MID.
   *
   * ⚠ Synced by DASH-CARD-2 — the backend shipped this field and the frontend contract had
   * not caught up, so the card could not draw a flag glyph it was already being sent.
   * Measured coverage on live alerts: maritime 114/114, implausible 106/107, and correctly
   * NULL on both military types (an aircraft and a satellite have no vessel registry).
   *
   * ⛔ It is the glyph's source ONLY, never the label. «علم التسجيل: <name>» stays mandatory
   * beside it: a flag reads as a LOCATION faster than a country name does, and a registry is
   * an administrative fact about ownership, not a position (§3).
   */
  flag_iso: string | null;
}

export interface AlertCounts {
  emergency: number;
  military: number;
  maritime: number;
  implausible: number;
  total: number;
}

export interface AlertDomainStat {
  domain: AlertDomain;
  count: number;
  live: boolean;
  vintage: string | null;
  source: string;
}

export interface AlertsResponse {
  generated_at: string;
  window_hours: number;
  counts: AlertCounts;
  /**
   * DASH-1: the AOI country the feed was restricted to (ISO alpha-2), or null for all.
   * Echoed so a filtered view can never be mistaken for the whole picture — and so a
   * client can tell "no alerts in Syria" from "no filter was applied".
   *
   * ⚠ It scopes `alerts` ONLY. `counts` and `domains` stay GLOBAL under a filter
   * (measured: ?country=SY → alerts 211→13, counts.total 211, air 520 unchanged).
   */
  countries_filtered: string | null;
  /** DASH-1: which axis produced `alerts` — echoed so a client never has to guess. */
  sorted_by: AlertSort;
  domains: AlertDomainStat[];
  caveat: string; // honest limitation — MUST be shown to the user
  alerts: Alert[];
}

/** AIS × MarineTraffic cross-source view (migration 034). A derived link is a
 *  LEAD, not proof — confidence + evidence travel with each. */
export interface CrossMatch {
  ship_id: string;
  mmsi: string | null;
  name: string | null;
  flag: string | null;
  confidence: string | null; // 'strong' | 'probable' | 'weak'
  score: number | null;
  distance_km: number | null;
  dt_minutes: number | null;
  ambiguous: boolean;
  discrepancy: boolean; // co-located name match with conflicting dimensions
  reason: string | null;
  link_source: string;
}

export interface CrossrefSummary {
  matched: number;
  strong: number;
  probable: number;
  weak: number;
  ambiguous: number;
  discrepancies: number;
  mt_vessels: number;
  mt_matched: number;
  mt_only: number;
}

export interface CrossrefResponse {
  available: boolean;
  generated_at: string | null;
  summary: CrossrefSummary | null;
  caveat: string;
  matches: CrossMatch[];
}

/** GFW satellite-AIS dark-vessel event — a vessel that stopped transmitting.
 *  A LEAD, not proof; `intentional` is GFW's own likelihood flag. */
export interface DarkVessel {
  mmsi: string | null;
  name: string | null;
  flag: string | null;
  lat: number | null;
  lon: number | null;
  start: string | null;
  end: string | null;
  duration_h: number | null;
  intentional: boolean | null;
  distance_km: number | null;
  on_land: boolean; // reported position falls on land — a SUSPECT/spoofed AIS fix (not a verdict)
}

export interface DarkVesselsResponse {
  available: boolean;
  region: string | null;
  generated_at: string | null;
  count: number;
  suspect: number; // of `count`, how many carry an on-land (suspect/spoofed) position
  caveat: string;
  reason: string | null; // why unavailable (e.g., no GFW token)
  vessels: DarkVessel[];
}

/** Stable per-vessel key across sources: MMSI for AIS, a namespaced ship_id for
 *  MarineTraffic (which has no MMSI). Used for map markers, list rows, and
 *  selection so the two identity spaces never collide. */
export function vesselKey(v: Vessel): string {
  return v.mmsi ?? `mt:${v.ship_id ?? ""}`;
}

/* =============================================================================================
 * DASH-BOARD-0/2/3 — THE SEVEN COVERAGE-BEARING CONTRACTS (UI-3)
 *
 * ⛔ THESE EXISTED IN THE BACKEND WITH ZERO FRONTEND SURFACE. Measured in DASH-BOARD-UI-pre: all
 * seven routes shipped and neither this file nor client.ts referenced one of them. Not a contract
 * that had drifted — a contract that had never been reachable.
 *
 * ⛔ EVERY KEY IS REQUIRED; ONLY VALUES ARE NULLABLE (the 2a rule). These models are pydantic with
 * defaults, and FastAPI serialises defaults, so every field is ALWAYS present in the body. An
 * optional `?` here would describe a wire format that does not exist and would let a consumer skip
 * a field the contract guarantees — which for `coverage` means skipping the holes. Where the
 * backend declares `X | None` the value is `X | null` and the KEY still arrives.
 *
 * ⛔ AND OPEN SETS STAY `string`. `kind` is the exception, closed by the backend's own three-way
 * documentation; `unit`, `basis`, `classification`, `split_by`, `attribution_field` and the flag
 * names are open vocabularies the backend can extend without a frontend release, so a closed union
 * would turn new data into a type error instead of a value.
 * ============================================================================================= */

/** One period in which a stream was NOT observed, and why. */
export interface CoverageGap {
  start: string;
  end: string;
  /** ⛔ THE LOAD-BEARING FIELD, and the one closed union in this block. `host` = the machine was
   *  off (global, every stream at once) · `source` = the upstream feed dropped while the host ran
   *  (per stream) · `detector` = the phenomenon happened and nothing was looking (per detector).
   *  Three causes, three remedies; a consumer that collapses them draws a deliberate shutdown and
   *  a dead detector as the same event. */
  kind: "host" | "source" | "detector";
  /** what proved this gap: journal-boot-table · recorded · recorded-inferred · timer-enable-mtime.
   *  ⚠ Open on purpose — the backend adds bases without a frontend release. */
  basis: string;
  source: string;
  seconds: number;
  note: string;
}

/** ⛔ EVERY TIME SERIES MUST BE READ WITH THIS, AND NOT WITHOUT IT. */
export interface SeriesCoverage {
  stream: string;
  domain: string;
  /** ⛔ what ONE value of this series counts. Air counts airframe reports, sea counts detector
   *  events, space counts passes — no total crosses them. See `lib/series/prepare`. */
  unit: string;
  source: string;
  series_start: string | null;
  series_end: string | null;
  rows: number | null;
  computed_at: string;
  host_gaps: CoverageGap[];
  gaps: CoverageGap[];
  host_gap_basis: string;
  /** ⚠ For AIR this reads UNAVAILABLE: an upstream air outage that did not coincide with a host
   *  shutdown is not represented AT ALL, and `host_gaps` alone protects the series. */
  gap_basis: string;
  observed_fraction: number | null;
  /** ⛔ UI-0 — what the fraction above counted. On a composite series the contributors' detector
   *  windows are in `gaps` but excluded from the fraction. Never show the fraction without this. */
  observed_fraction_basis: string;
  /** ⛔ AIR ONLY (the backend leaves it null elsewhere): ratios may not cross this instant. */
  ratio_cutoff: string | null;
  ratio_cutoff_note: string;
  /** ⛔ When set, the stream is not a time series at all and must not be drawn as one. */
  not_a_series: string | null;
  caveats: string[];
}

export interface CoverageResponse {
  available: boolean;
  generated_at: string;
  frame_boundary: string | null;
  caveat: string;
  streams: SeriesCoverage[];
}

/** One time bucket of one SPLIT of a series. ⛔ `key` is never empty on a split series. */
export interface SeriesBucket {
  bucket: string;
  key: string;
  count: number;
}

/** A bucketed maritime series (questions ب and د) and the coverage it must be read with. */
export interface MaritimeSeries {
  available: boolean;
  question: string;
  bucket: string;
  unit: string;
  source: string;
  method: string;
  caveat: string;
  /** ⛔ When set, never sum `buckets` across different `key`s. */
  split_by: string | null;
  split_note: string;
  totals: Record<string, number>;
  buckets: SeriesBucket[];
  coverage: SeriesCoverage | null;
}

/** One candidate meeting — a LEAD, never a confirmed rendezvous. */
export interface RendezvousPoint {
  mmsi: string;
  start_ts: string | null;
  end_ts: string | null;
  duration_s: number | null;
  start_lat: number | null;
  start_lon: number | null;
  end_lat: number | null;
  end_lon: number | null;
  classification: string;
}

/** ⛔ POINTS AS THEY ARE — NO DENSITY SURFACE (question ج). */
export interface RendezvousPointsResponse {
  available: boolean;
  question: string;
  count: number;
  span_days: number | null;
  per_day: number | null;
  unit: string;
  source: string;
  method: string;
  caveat: string;
  /** ⛔ Non-empty means the backend REFUSED to bin these and says why. A client that bins anyway
   *  has broken the contract, not read it. */
  density_refused: string;
  points: RendezvousPoint[];
  coverage: SeriesCoverage | null;
}

/** One registry flag's share. ⛔ `hulls` is the number; `pct` is derived and needs `denominator`. */
export interface FlagSlice {
  flag: string;
  hulls: number;
  pct: number;
  attributed: boolean;
}

/** ⛔ REGISTRY FLAGS OF THE MULTI-PATTERN HULLS — with its denominator in the open (question هـ). */
export interface FlagDistributionResponse {
  available: boolean;
  question: string;
  denominator: number;
  denominator_note: string;
  /** ⛔ WHICH derivation produced these flags. The project has two that disagree by ~6 points. */
  attribution_field: string;
  attribution_note: string;
  attributed: number;
  unattributed: number;
  unit: string;
  source: string;
  method: string;
  caveat: string;
  slices: FlagSlice[];
  coverage: SeriesCoverage | null;
}

/** Distinct AIRFRAMES over one country, in one bucket, from ONE named population. */
export interface CountryBucket {
  bucket: string;
  country: string;
  /** ⛔ PART OF THE KEY, never folded away: opensky / adsbexchange / airplanes_live are different
   *  communities with different coverage (74.46 % · 41.19 % · 70.88 % measured). */
  source: string;
  airframes: number;
}

/** ⛔ AIR COUNTRY × TIME, served ON DEMAND (question أ). Unit: airframes. */
export interface AirCountrySeries {
  available: boolean;
  question: string;
  bucket: string;
  unit: string;
  method: string;
  caveat: string;
  split_by: string;
  split_note: string;
  populations: string[];
  population_note: string;
  storage_note: string;
  window_hours: number;
  cost_note: string;
  totals: Record<string, number>;
  buckets: CountryBucket[];
  coverage: SeriesCoverage | null;
}

/** One (day, country, source) row. `distinct_hulls` is EXACT for that day alone. */
export interface HullDayBucket {
  day: string;
  country: string;
  source: string;
  distinct_hulls: number;
  rows: number;
}

/** ⛔ THE LONG AIR TREND — AND ITS UNIT IS **HULL-DAYS**, NOT AIRFRAMES. An airframe present on ten
 *  days contributes 10. ⚠ Not a degraded substitute: the backend calls hull-days the better measure
 *  for "which countries lead over time", because it weights PRESENCE OVER TIME rather than mere
 *  appearance. Read as a choice, not a shortfall. */
export interface AirCountryDaily {
  available: boolean;
  question: string;
  unit: string;
  unit_warning: string;
  source: string;
  method: string;
  caveat: string;
  table_note: string;
  days_present: number;
  /** ⛔ Days the host was off are ABSENT from `buckets`, never zero. */
  days_absent_note: string;
  query_ms: number;
  totals: Record<string, number>;
  buckets: HullDayBucket[];
  coverage: SeriesCoverage | null;
}
