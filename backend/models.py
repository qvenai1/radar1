"""
Pydantic response models — API Contract v1 (FROZEN).

These shapes are the contract the React frontend is built against. Additive
changes only; anything breaking goes to /api/v2.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BBox(BaseModel):
    lamin: float
    lomin: float
    lamax: float
    lomax: float


class StateVector(BaseModel):
    """One aircraft state. Nulls mean 'not reported' — never zero-filled."""
    icao24: str
    callsign: str = ""
    origin_country: str = ""
    time_position: int | None = None
    last_contact: int | None = None
    longitude: float | None = None
    latitude: float | None = None
    baro_altitude: float | None = Field(None, description="meters")
    geo_altitude: float | None = Field(None, description="meters")
    on_ground: bool
    velocity: float | None = Field(None, description="m/s")
    true_track: float | None = Field(None, description="degrees, 0=north")
    vertical_rate: float | None = Field(None, description="m/s")
    squawk: str = ""
    spi: bool = False
    position_source: int | None = None
    category: int | None = None
    snapshot_time: int
    fetched_at: str | None = None
    # v1 ADDITIVE (migration 012): geo-classification + validation provenance.
    # Null for feeds that do not classify (opensky, airplanes_live) and for all
    # legacy rows — never zero-filled. geo_source names the classifying feed and
    # is distinct from the natural-key `source`.
    country: str | None = Field(None, description="seven-country INSIDE match, else null")
    near_country: str | None = Field(None, description="nearest country when NEAR the border, else null")
    position_quality: str | None = Field(None, description="'confirmed' | 'no_position' | 'suspect'")
    validation_flags: list[str] | None = None
    distance_km: float | None = Field(None, description="great-circle distance provenance, km")
    geo_source: str | None = Field(None, description="feed that produced the classification")


class LiveResponse(BaseModel):
    snapshot_time: int
    ts: str = Field(description="ISO-8601 UTC of snapshot_time")
    bbox: BBox | None = Field(None, description="null when not recorded for this snapshot")
    count: int
    source: str = Field(description="'db' | 'archive' (latest.json fallback)")
    states: list[StateVector]


class ReplayResponse(LiveResponse):
    requested_at: int = Field(description="the 'at' the client asked for; "
                                          "snapshot_time is the resolved snapshot <= at")


class HistoryResponse(BaseModel):
    icao24: str
    from_time: int = Field(alias="from")
    to_time: int = Field(alias="to")
    count: int
    limit: int
    offset: int
    has_more: bool
    points: list[StateVector] = Field(description="ordered by snapshot_time ASC")

    model_config = {"populate_by_name": True}


class SnapshotInfo(BaseModel):
    snapshot_time: int
    ts: str
    count: int
    complete: bool = Field(description="all rows of this snapshot are in the DB; "
                                       "replay only serves complete snapshots")
    bbox: BBox | None = None


class SnapshotsResponse(BaseModel):
    from_time: int = Field(alias="from")
    to_time: int = Field(alias="to")
    count: int
    limit: int
    offset: int
    has_more: bool
    snapshots: list[SnapshotInfo] = Field(description="ordered by snapshot_time ASC")

    model_config = {"populate_by_name": True}


class StatsBucket(BaseModel):
    bucket_start: str = Field(description="ISO-8601 UTC start of the bucket")
    state_count: int
    ground_count: int
    airborne_count: int
    max_baro_altitude: float | None = None
    max_geo_altitude: float | None = None
    max_velocity: float | None = None
    snapshot_count: int
    avg_aircraft_per_snapshot: float | None = None


class StatsResponse(BaseModel):
    from_time: int = Field(alias="from")
    to_time: int = Field(alias="to")
    bucket: str
    buckets: list[StatsBucket]

    model_config = {"populate_by_name": True}


class SearchResponse(BaseModel):
    snapshot_time: int
    source: str
    count: int
    states: list[StateVector]


class DbHealth(BaseModel):
    connected: bool
    latest_snapshot_time: int | None = None
    snapshot_age_seconds: float | None = None
    stale: bool = True


class ArchiveHealth(BaseModel):
    latest_json_present: bool
    age_seconds: float | None = None


class IngestionHealth(BaseModel):
    fresh: bool = Field(description="DB latest snapshot within stale window of archive latest")
    lag_seconds: float | None = Field(None, description="archive latest - DB latest")


class HealthResponse(BaseModel):
    ok: bool
    db: DbHealth
    archive: ArchiveHealth
    ingestion: IngestionHealth


# --- v1 ADDITIVE: military overlay (airplanes.live, unfiltered) ------------

class MilStateVector(StateVector):
    """StateVector + airframe extras the military feed carries."""
    source: str = "airplanes_live"
    actype: str | None = None
    acdesc: str | None = None
    reg: str | None = None


class MilitaryResponse(BaseModel):
    """Live military overlay. available=false (empty states) when the
    military collector is off or its snapshot is stale — never an error.
    v1 ADDITIVE 2026-09-01: `source` names the path that served — "archive"
    (military.json) or "db" (airplanes_live rows in state_vectors), mirroring
    /live's archive-first + DB-fallback model (arch review finding 11)."""
    available: bool
    snapshot_time: int | None = None
    age_seconds: float | None = None
    count: int = 0
    source: str | None = None
    states: list[MilStateVector] = []


# --- v1 ADDITIVE: ADS-B Exchange overlay (adsb_scraper, DB-backed) ----------

class AdsbResponse(BaseModel):
    """Live ADS-B Exchange overlay, served from the DB (the source has no
    latest.json — it is ingested to state_vectors by the follow-ingester).
    Bounded to the AOI (INSIDE a country or NEAR a border) using the P3 geo
    columns, so every row carries country/near_country/position_quality.
    available=false when the latest adsbexchange snapshot is stale — never an
    error."""
    available: bool
    snapshot_time: int | None = None
    age_seconds: float | None = None
    count: int = 0
    states: list[StateVector] = []


# --- v1 ADDITIVE: maritime AIS overlay (aisstream) -------------------------

class Vessel(BaseModel):
    """One vessel in the live maritime overlay. Nulls mean 'not reported'.
    speed is KNOTS (AIS-native, not metric). data_origin is terrestrial for
    free AIS — the open ocean is dark; AIS is also spoofable (verify).

    Identity is source-shaped: AIS vessels carry `mmsi`; MarineTraffic vessels
    carry `ship_id` and usually no mmsi (both optional, additive since 032). A
    client keys on whichever is present, scoped by `source`."""
    mmsi: str | None = None
    ship_id: str | None = Field(None, description="MarineTraffic internal id (mmsi-less source)")
    lat: float | None = None
    lon: float | None = None
    speed: float | None = Field(None, description="SOG, knots")
    course: float | None = Field(None, description="COG, degrees true")
    heading: float | None = None
    nav_status: int | None = None
    timestamp: str | None = Field(None, description="last fix, ISO 8601 UTC")
    imo: str | None = None
    name: str | None = None
    callsign: str | None = None
    ship_type_code: int | None = None
    ship_type: str | None = None
    destination: str | None = None
    draught: float | None = None
    flag: str | None = Field(None, description="source-reported flag (MarineTraffic); AIS derives via MID")
    source: str = "aisstream"
    data_origin: str = "terrestrial"


class SourceVintage(BaseModel):
    """Per-source freshness for a maritime payload (MT-FRESH). A blended `source=all`
    response mixes a LIVE aisstream feed with a possibly-STALE MarineTraffic store, so a
    single scalar cannot describe the whole payload honestly — this names each source's
    own freshness and how many rows it contributed HERE. This is the authoritative
    freshness structure a client should read (the freshness strip / per-source tiles)."""
    source: str = Field(description="'aisstream' | 'marinetraffic'")
    last_fix: str | None = Field(None, description="freshest fix in this source's store, ISO — null if none / DB down")
    age_seconds: float | None = Field(None, description="now − last_fix; how old this source's freshest data is")
    rows: int = Field(0, description="rows THIS source contributed to this payload")
    stale: bool = Field(False, description="age beyond the live-overlay freshness window (180 s) — not live data")


class MaritimeResponse(BaseModel):
    """Live maritime overlay from the collectors. available=false (empty states) when
    the collector is off or its snapshot is stale — never an error. File-based like the
    military overlay.

    FRESHNESS (MT-FRESH): `sources[]` is the authoritative per-source freshness — read it,
    not the scalar, whenever `source=all`, because that payload BLENDS a live aisstream feed
    with a possibly-stale MarineTraffic store (each vessel also carries its own `timestamp`
    + `source`, so per-row age is derivable). `data_vintage` is kept for back-compat but is
    now the OLDEST (worst-case) source vintage among contributing sources — so it can never
    over-claim freshness for a blend (previously it was the max/freshest across tables, which
    reported an ~11 s vintage for a payload that was ~92 % four days old). For a single-source
    request it is unchanged (that source's max ts). Null if the DB is unreachable."""
    available: bool
    snapshot_time: int | None = None
    age_seconds: float | None = None
    count: int = 0
    data_vintage: str | None = Field(None, description="OLDEST contributing source's max ts, ISO — worst-case payload vintage (never over-claims); see sources[]")
    sources: list[SourceVintage] = Field(default_factory=list, description="per-source freshness + contribution — authoritative for a blend")
    states: list[Vessel] = []


class VesselLiveResponse(BaseModel):
    """ONE vessel's live payload, keyed by MMSI (DASH-4). Closes the gap DASH-2f
    measured and worked around: aircraft and satellites had a single-entity path to
    their LIVE contract (`/search?q=<icao24>` → StateVector[], `/search/satellite?q=`
    → the TLE element); vessels did not — the live `Vessel` contract existed only
    inside `/maritime`'s full list, so the left panel rendered the SUMMARY contract
    and named the four fields it could not reach (heading · destination · nav_status
    · draught). It now reaches all of them.

    ⛔ `vessel` IS the `Vessel` contract, unchanged and unextended — the same model
    `/maritime` serves. There is no second vessel shape. For a LIVE vessel the object
    is built from the SAME snapshot dict `/maritime` reads, so the two are identical
    field-for-field; that equality is asserted by a test, not assumed.

    TWO STATES, never conflated (a third — MMSI never seen — is a 404, not an object):

    - `state="live"`   — present in the current AIS snapshot, inside the 180 s
      freshness window. `timestamp` is this fix.
    - `state="last_known"` — the MMSI is known but is NOT transmitting now. The
      payload is the last stored fix; `age_seconds` says how old. It is never
      presented as current — that is what `state` is for.

    ⚠ TWO VINTAGES IN THE `last_known` STATE, both named rather than blended.
    Dynamic + voyage fields (position, speed, course, heading, nav_status,
    destination, draught) come from ONE stored fix, stamped `timestamp`. The static
    identity (name, imo, callsign, ship_type, ship_type_code) comes from the identity
    spine, stamped `identity_as_of`. They are separate because AIS itself separates
    them: position reports (types 1/2/3) carry no static data, so the latest stored
    row has NULL statics for **~79 %** of vessels (measured: 665/3,223 latest rows
    carry a name). Reading statics from the spine is not enrichment — it is the same
    AIS type-5 data, persisted (`identity_source='ais-static'`), and it is exactly
    what the live snapshot does internally when the collector merges statics onto the
    current position. Measured: the spine strictly dominates — `coalesce(fix, spine)`
    resolves 2,835 names, identical to the spine alone, so nothing is lost by
    single-sourcing it and no per-field vintage mixing is needed.

    ⚠ `not_reported` names the contract fields that are NULL because the device did
    not report them — the difference between "not observed" and "does not exist"
    (the 2c′/2e rule). `draught` is the standing example: measured, ~77 % of vessels
    have no draught on their latest stored fix. A null there is AIS silence, not a
    hole in the database. `ship_id` and `flag` are deliberately EXCLUDED from this
    list: they are null for every AIS vessel by source shape (measured 0/556 in the
    live snapshot), so calling them "not reported" would misdescribe them."""
    mmsi: str = Field(description="the MMSI queried, echoed back")
    state: str = Field(description="'live' | 'last_known' — never blended")
    vessel: Vessel = Field(description="the SAME Vessel contract /maritime serves")
    age_seconds: float | None = Field(None, description="now − vessel.timestamp; how old this fix is")
    snapshot_time: int | None = Field(None, description="live only: the snapshot's own stamp")
    snapshot_age_seconds: float | None = Field(None, description="live only: age of the snapshot FILE")
    identity_as_of: str | None = Field(None, description="last_known only: vintage of the static identity, ISO — a SECOND vintage, not the fix's")
    not_reported: list[str] = Field(default_factory=list, description="contract fields NULL because the device did not report them (excludes ship_id/flag — null by source shape)")
    n_fixes: int | None = Field(None, description="last_known only: stored fixes for this MMSI — how much history backs the claim")


class CrossMatch(BaseModel):
    """One derived AIS×MarineTraffic identity link (migration 034). A LEAD, not
    proof: `confidence` is strong/probable/weak; `discrepancy` marks a vessel
    co-located under one name but with conflicting dimensions (a data-quality or
    possible-spoof signal). `ambiguous` marks a name that matched >1 counterpart."""
    ship_id: str
    mmsi: str | None = None
    name: str | None = None
    flag: str | None = None
    confidence: str | None = None       # 'strong' | 'probable' | 'weak'
    score: float | None = None
    distance_km: float | None = None
    dt_minutes: float | None = None
    ambiguous: bool = False
    discrepancy: bool = False
    reason: str | None = None
    link_source: str


class CrossrefSummary(BaseModel):
    matched: int          # confirmed derived links (strong + probable)
    strong: int
    probable: int
    weak: int
    ambiguous: int
    discrepancies: int    # co-located name matches with conflicting dimensions
    mt_vessels: int       # distinct MarineTraffic vessels in the store
    mt_matched: int       # of them, linked to an AIS MMSI
    mt_only: int          # seen by MarineTraffic, no AIS match found


class CrossrefResponse(BaseModel):
    """AIS × MarineTraffic cross-source view. available=false when the cross-ref
    has not been computed / DB is down — never an error."""
    available: bool
    generated_at: str | None = None
    summary: CrossrefSummary | None = None
    caveat: str = ""
    matches: list[CrossMatch] = []


class DarkVessel(BaseModel):
    """One AIS-gap (dark-vessel) event from GFW satellite AIS — a vessel that
    stopped transmitting. A LEAD, not proof: `intentional` is GFW's own likelihood
    flag. `lat`/`lon` is where it went dark. `data_origin` is always satellite."""
    mmsi: str | None = None
    name: str | None = None
    flag: str | None = None
    lat: float | None = None
    lon: float | None = None
    start: str | None = None
    end: str | None = None
    duration_h: float | None = None
    intentional: bool | None = None
    distance_km: float | None = None
    # SUSPECT-1 (additive): the reported dark position falls on LAND (land/sea mask).
    # For a ship-station MMSI that is a likely SPOOFED / erroneous AIS fix — surfaced
    # as a SIGNAL, not hidden. NOT a verdict: a coarse (~1.8 km) mask can also flag a
    # berth/anchorage right at the coastal edge. False = the position is at sea.
    on_land: bool = False


class DarkVesselsResponse(BaseModel):
    """GFW satellite-AIS dark-vessel view. available=false (with `reason`) when
    the GFW token is absent or the API is unreachable — never a 500."""
    available: bool
    region: str | None = None
    generated_at: str | None = None
    count: int = 0
    suspect: int = 0        # of `count`, how many carry an ON-LAND (suspect/spoofed) position
    caveat: str = ""
    reason: str | None = None
    vessels: list[DarkVessel] = []


class SatelliteElement(BaseModel):
    """One satellite's orbital elements. The client propagates position from
    the TLE locally (SGP4) — the backend never computes positions."""
    norad_id: int
    name: str = ""
    tle1: str
    tle2: str
    epoch: str | None = Field(None, description="TLE epoch, ISO 8601 UTC")


class SatellitesResponse(BaseModel):
    """Orbital elements for one CelesTrak group, cached from CelesTrak.
    Positions are computed client-side (satellite.js). available=false when
    the group hasn't been fetched yet or the DB is down — never an error."""
    available: bool
    group: str
    count: int = 0
    fetched_at: str | None = None
    elements: list[SatelliteElement] = []


class SatelliteGroup(BaseModel):
    group: str
    count: int
    fetched_at: str | None = None


class SatelliteGroupsResponse(BaseModel):
    groups: list[SatelliteGroup] = []


class SatelliteEnrichment(BaseModel):
    """SATCAT identity for one satellite (CelesTrak SATCAT, migration 016 /
    satellite_info). Bulk-cached offline — this endpoint is a pure local read,
    NOT a per-object fetch. available=false when the norad_id has no SATCAT row
    yet; never an error. Every fact carries provenance (source + snapshot date).
    Orbital params (inclination/period/altitude) are NOT here — the client
    derives those from the TLE (orbit.ts); this layer is IDENTITY only."""
    norad_id: int
    available: bool = True
    international_designator: str | None = None
    object_name: str | None = None            # SATCAT's name (may differ from TLE)
    object_type: str | None = None            # raw SATCAT code: PAY | R/B | DEB | UNK
    object_type_label: str | None = None      # payload | rocket_body | debris | unknown
    owner: str | None = None                  # CelesTrak owner/source code (e.g. US, PRC)
    owner_name: str | None = None             # decoded country/organization; null if unknown
    launch_date: str | None = None            # ISO date
    launch_site: str | None = None            # CelesTrak launch-site code
    decay_date: str | None = None             # ISO date; null = on-orbit
    ops_status: str | None = None             # OPS_STATUS_CODE
    rcs_m2: float | None = None               # radar cross-section, m^2
    source: str | None = None                 # 'celestrak_satcat'
    satcat_snapshot_date: str | None = None   # ISO date — the SATCAT dump version


class SatelliteReentry(BaseModel):
    """One predicted reentry (Space-Track TIP, migration 017), joined to S1
    identity. lat/lon is the NOMINAL predicted impact point (single point, LARGE
    uncertainty — window_min is ± minutes around decay_epoch, next_report=0 means
    final). country/near_country classify that nominal point against the shared
    seven-country AOI (same classifier as aircraft)."""
    norad_id: int
    object_name: str | None = None       # from satellite_info (S1 join)
    owner: str | None = None
    owner_name: str | None = None
    object_type: str | None = None
    decay_epoch: str | None = None       # predicted nominal reentry time, ISO
    window_min: int | None = None        # ± uncertainty window, minutes
    lat: float | None = None             # nominal predicted impact latitude
    lon: float | None = None             # nominal predicted impact longitude (-180..180)
    direction: str | None = None
    next_report: int | None = None
    high_interest: bool | None = None
    country: str | None = None           # AOI INSIDE match (nominal point)
    near_country: str | None = None      # AOI NEAR match
    distance_km: float | None = None
    aoi: bool = False                    # INSIDE or NEAR the seven-country region
    msg_epoch: str | None = None         # when this prediction was issued
    source: str | None = None            # 'spacetrack_tip'


class ReentryResponse(BaseModel):
    """Latest reentry prediction per object (newest TIP message wins). Predicted
    impact is imprecise by nature — window_min/next_report carry the uncertainty.
    available=false when there are no matching predictions."""
    available: bool
    count: int = 0
    inside: int = 0                      # predictions INSIDE the AOI
    near: int = 0                        # predictions NEAR the AOI
    region: str = "all"                  # 'all' | 'aoi'
    events: list[SatelliteReentry] = []


class VesselEnrichment(BaseModel):
    """Best-effort vessel photo from Wikimedia Commons (searched by IMO then
    name). Coverage is SPARSE (no dense free ship-photo API exists), so photo
    is null for most vessels — never an error. Attribution (photographer +
    Commons page link + license) is carried when present."""
    mmsi: str
    photo: PhotoInfo | None = None
    license: str | None = None


# --- v1 ADDITIVE: best-effort enrichment (external standing data) ----------

class AirportInfo(BaseModel):
    icao: str | None = None
    iata: str | None = None
    name: str | None = None
    municipality: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None


class RouteInfo(BaseModel):
    callsign: str
    airline: str | None = None
    origin: AirportInfo
    destination: AirportInfo


class PhotoInfo(BaseModel):
    thumb: str
    page: str | None = Field(None, description="photo page — link for attribution")
    photographer: str | None = None


class AircraftEnrichment(BaseModel):
    """Best-effort data from free external sources (adsbdb, planespotters).
    Everything nullable: unknown airframes/callsigns and upstream outages
    yield nulls, never errors. Route is STANDING data (callsign → usual
    route) — not the actual filed flight plan."""
    icao24: str
    registration: str | None = None
    aircraft_type: str | None = None
    operator: str | None = None
    operator_country: str | None = None
    photo: PhotoInfo | None = None
    route: RouteInfo | None = None


class GapEvent(BaseModel):
    """One AIS-gap behavioural event (migration 024, maritime_events), joined to
    the identity spine. classification is HONEST: 'candidate' only after the
    three confounds are removed (feed outage / stationary / coverage edge);
    otherwise 'outage-excluded' / 'stationary' / 'edge' — stored, not hidden.
    A 'candidate' is an INVESTIGATION LEAD (see GapsResponse.caveat), NEVER a
    proven 'went dark'."""
    mmsi: str
    imo: str | None = None               # durable anchor (M2b spine); null where not broadcast
    name: str | None = None              # M2b identity (point-in-time)
    flag_country: str | None = None      # M2a flag
    ship_type: str | None = None
    start_ts: str                        # last fix before the gap, ISO
    end_ts: str | None = None            # first fix after (null = still open)
    duration_s: float | None = None
    start_lat: float | None = None       # where it went dark
    start_lon: float | None = None
    end_lat: float | None = None         # where it reappeared
    end_lon: float | None = None
    start_country: str | None = None     # M1 — whose territorial waters it went dark in
    start_near_country: str | None = None
    distance_at_start_km: float | None = None
    speed_at_last_fix: float | None = None
    nav_status: int | None = None
    coverage_cell_density: int | None = None   # positions in the start cell (the 'dense' evidence)
    classification: str
    method: str


class GapsResponse(BaseModel):
    """AIS-gap events from the behavioural layer (A1). Default classification is
    'candidate'. `caveat` MUST be surfaced — a candidate is an investigation lead,
    not proof of intent. available=false when detection has not run."""
    available: bool
    count: int = 0
    classification: str = "candidate"
    method: str | None = None
    caveat: str = ""
    events: list[GapEvent] = []


class GnssEvent(BaseModel):
    """One per-vessel GNSS-anomaly EPISODE (G4a, maritime_events event_type=
    'gnss_anomaly'). classification is the tier: 'candidate' = an INDIVIDUAL vessel
    spoofed/jammed (an investigation lead); 'area-jamming' = an environmental-jamming
    VICTIM (many vessels in one cell/window); 'glitch' = a source with no real track
    (a bad/test transmitter). gnss_type mirrors that in detail. start_lat/start_lon is
    the episode's first implausible fix — for a decoy episode that is the decoy landing
    point (e.g. the Amman airport cell). affected_lat/lon is the inferred real position
    (last-good fix before the episode). A data-quality event, not proof of intent."""
    mmsi: str
    imo: str | None = None
    name: str | None = None
    flag_country: str | None = None
    ship_type: str | None = None
    start_ts: str
    end_ts: str | None = None
    duration_s: float | None = None
    start_lat: float | None = None       # first implausible fix (decoy landing for decoy episodes)
    start_lon: float | None = None
    end_lat: float | None = None
    end_lon: float | None = None
    classification: str                  # candidate | area-jamming | glitch
    gnss_type: str | None = None         # individual | area | glitch (detail subtype)
    decoy_match: bool | None = None
    n_implausible_fixes: int | None = None
    implied_speed_max: float | None = None
    inland_km_max: float | None = None
    affected_lat: float | None = None    # inferred real position (last-good before the episode)
    affected_lon: float | None = None
    method: str


class GnssAnomaliesResponse(BaseModel):
    """GNSS-anomaly episodes (G4a). `caveat` MUST be surfaced — implausibility can be
    deliberate spoofing, area jamming, or a receiver fault; a spoofed vessel is usually
    a VICTIM. available=false when detection has not run."""
    available: bool
    count: int = 0
    method: str | None = None
    caveat: str = ""
    anomalies: list[GnssEvent] = []


class ZoneCell(BaseModel):
    """One jamming-zone cell x hourly window (G5b). 0.1° cell bounds + the distinct
    implausible-vessel count. variant='broadcast' = WHERE spoofed positions landed
    (the decoy, e.g. the Amman cell); variant='affected' = WHERE vessels actually were
    when jamming began (inferred, feed-outage windows subtracted). A data-quality
    signal — where GPS was being denied — not proof of intent."""
    clat: int
    clon: int
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    window_start: str            # ISO — the hourly window
    distinct_vessels: int
    variant: str                 # broadcast | affected


class JammingZonesResponse(BaseModel):
    """Jamming-zone cells (G5b). `caveat` MUST be surfaced — a dense cell shows where
    GPS was denied, not an accusation. available/count as the siblings."""
    variant: str
    count: int = 0
    min_vessels: int = 10
    caveat: str = ""
    cells: list[ZoneCell] = []


class RendezvousVessel(BaseModel):
    """One side of a rendezvous, identity from the spine (M2a flag + M2b name/imo)."""
    mmsi: str
    imo: str | None = None
    name: str | None = None
    flag_country: str | None = None
    ship_type: str | None = None


class RendezvousEvent(BaseModel):
    """A rendezvous candidate (A2, maritime_events event_type='rendezvous') — two
    vessels that loitered close together in open sea. suspicion_rank PRIORITISES
    (high = tanker+tanker / flags-of-convenience; low = offshore-support / ferry /
    service) — it does NOT accuse. See RendezvousResponse.caveat."""
    primary: RendezvousVessel
    partner: RendezvousVessel
    start_ts: str
    end_ts: str | None = None
    duration_min: float | None = None
    min_distance_m: float | None = None    # closest true approach (great-circle)
    both_max_speed: float | None = None
    offshore_km: float | None = None       # M1 distance-to-coast at the meeting
    start_country: str | None = None       # AOI waters the meeting was in (M1)
    start_near_country: str | None = None
    start_lat: float | None = None
    start_lon: float | None = None
    suspicion_rank: str                    # 'high' | 'medium' | 'low' (prioritisation only)
    classification: str
    method: str
    # DRAUGHT-1: corroborating draught-delta across the encounter (draught-delta-v1).
    # {primary,partner}.{state(changed|unchanged|insufficient_data),reason,before,after,
    # delta} + top-level transfer_shaped. insufficient_data means BLIND, not "no change".
    draught: dict | None = None


class RendezvousResponse(BaseModel):
    """Rendezvous candidates (A2), ordered high→low suspicion then longest first.
    `caveat` MUST be surfaced — a candidate is an investigation lead; most are
    legitimate offshore work / scheduled traffic; a transfer is NOT proven."""
    available: bool
    count: int = 0
    rank: str = "all"                      # 'all' | 'high' | 'medium' | 'low'
    method: str | None = None
    caveat: str = ""
    events: list[RendezvousEvent] = []


class IdentityChangeEvent(BaseModel):
    """A confirmed identity change (A3, maritime_events event_type='identity_change')
    — a vessel broadcasting a different name/flag/MMSI over time, IMO-anchored.
    change_type: 'reflag' (same IMO, new MMSI+flag), 'rename', or 'retype'.
    suspicion_rank PRIORITISES (grey/sanctions flag, correlation with A1/A2,
    multiple changes → high); it does NOT accuse — renaming/re-flagging is usually
    legal. See IdentityChangesResponse.caveat."""
    mmsi: str                              # the new/subject MMSI
    imo: str | None = None                 # permanent hull anchor (the whole point)
    change_type: str
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    old_mmsi: str | None = None            # re-flag only
    new_mmsi: str | None = None
    old_flag: str | None = None
    new_flag: str | None = None
    old_name: str | None = None
    new_name: str | None = None
    last_old_ts: str | None = None
    first_new_ts: str                      # the transition time (= start_ts)
    gap_hours: float | None = None
    sustained_new_rows: int | None = None
    suspicion_rank: str                    # 'high' | 'medium' | 'low'
    correlated: bool = False               # shares an A1 gap / A2 rendezvous (same hull)
    correlated_events: dict[str, int] = {}
    classification: str
    method: str


class IdentityChangesResponse(BaseModel):
    """Identity-change candidates (A3), ordered high→low suspicion. `caveat` MUST
    be surfaced — a rename/re-flag is usually LEGAL; a candidate is a documented
    change for investigation, NOT proven evasion."""
    available: bool
    count: int = 0
    rank: str = "all"
    method: str | None = None
    caveat: str = ""
    events: list[IdentityChangeEvent] = []


class CompositeIdentity(BaseModel):
    """One of a hull's broadcast identities (it may have several if it re-flagged)."""
    mmsi: str
    name: str | None = None
    flag_country: str | None = None
    ship_type: str | None = None


class CompositeTimelineEntry(BaseModel):
    """One event in a hull's cross-pattern timeline — the analyst's narrative."""
    event_type: str                        # ais_gap | rendezvous | identity_change
    ts: str
    summary: str                           # human-readable, e.g. "went dark 46h in Turkish waters"
    rank: str | None = None


class CompositeHull(BaseModel):
    """The cross-pattern intelligence picture for ONE hull, stitched by IMO across
    all its broadcast identities. An aggregation of existing leads — see
    CompositeResponse.caveat; a composite of leads is a STRONGER lead, still NOT
    proof."""
    hull: str                              # the IMO (or MMSI when no IMO)
    is_imo: bool                           # False = IMO-null, aggregated by MMSI (not stitched across re-flags)
    imo: str | None = None
    identities: list[CompositeIdentity] = []
    n_gaps: int = 0
    n_rendezvous: int = 0
    n_identity_changes: int = 0
    n_pattern_types: int = 0
    composite_rank: str
    # RANK-S2 ADDITIVE: WHICH condition produced `composite_rank` — the branch that fired,
    # from the one function that owns the rule (composite.rank_with_reason). Carried rather
    # than re-derived because the Arabic `why` line must name the condition that ACTUALLY
    # determined the rank: two pattern types alone are MEDIUM, so a sentence crediting the
    # pattern count for a HIGH hull names an insufficient cause (it did, on 172 of 200 live
    # alerts). ⚠ Empty string = not computed by this caller; consumers must tolerate it.
    rank_reason: str = ""
    involves_grey: bool = False
    aoi_waters: list[str] = []             # AOI countries whose waters the hull's events touched (M1)
    rendezvous_partners: list[str] = []    # partner names it met
    #: DASH-3 — the same partners as IDENTIFIED entities, not display names. `rendezvous_
    #: partners` above is prose for a card; this is what lets a consumer OPEN the other vessel.
    #: ⚠ Kept alongside rather than replacing it: the names list is already on the wire and
    #: removing it is a separate, breaking decision.
    counterparts: list[AlertEntityRef] = []
    #: ⛔ EVERY pattern's OWN window, so co-occurrence can be READ rather than asserted. See
    #: PatternSpan for why the hull envelope cannot stand in for these.
    #: ⚠ Capped at 60 per hull: a lane strip is a shape, and a hull with 400 gaps renders the
    #: same shape at 60. The cap is stated on the wire (`n_pattern_spans`) so a consumer can
    #: say "showing 60 of 400" rather than silently implying it saw everything.
    pattern_spans: list[PatternSpan] = []
    n_pattern_spans: int = 0
    #: Earliest first-sighting among this hull's events (maritime_event_sightings, migration
    #: 050), and how many of its events carry one. ⛔ READ THEM TOGETHER: a hull with one
    #: newly-seen event among ten archived ones would otherwise report that one date as if the
    #: whole picture were that recent.
    first_seen_at: str | None = None
    n_events_first_seen: int = 0
    first_event_ts: str | None = None
    last_event_ts: str | None = None
    timeline: list[CompositeTimelineEntry] = []
    # IMO-2: the integrity verdict on this hull's SPINE (migration 035), the worst
    # imo_quality across its identities — a caveat on the LEAD's reliability, never a
    # reason to hide the hull. NULL = no IMO / not-yet-checked. A malformed spine has
    # already been re-keyed to the MMSI (migration 036); this surfaces why.
    imo_quality: str | None = None         # valid|checksum_fail|malformed|contested
    imo_flags: list[str] = []


class CompositeResponse(BaseModel):
    """Cross-pattern composite (capstone): hulls ranked by multi-pattern signal.
    `caveat` MUST be surfaced and COMPOUNDS — a composite of investigation leads
    is a stronger lead, still NOT proof; legitimate explanations exist."""
    available: bool
    count: int = 0
    min_pattern_types: int = 1
    method: str | None = None
    caveat: str = ""
    hulls: list[CompositeHull] = []


# --- v1 ADDITIVE: entity search (E1 — vessel) ------------------------------
# The search/monitoring tab's first entity: interrogate ONE vessel by
# MMSI/IMO/name → summary (identity + last fix + event counts) that expands to
# its full track. All PK-backed over vessel_states/vessel_identity (no spatial
# risk). Data is FROZEN (dark feed) — every fix/summary carries its own ts so
# the analyst reads the data vintage. Additive to the frozen contract.

class VesselTrackPoint(BaseModel):
    """One vessel fix in a track (migration 007/021/027 — vessel_states). Quality
    columns TRAVEL WITH the fix (never dropped): position_quality
    ('plausible'|'suspect'|'implausible') + anomaly_flags mark a spoofed/jammed
    fix so it is VISIBLE in the track, not silently hidden (the G3 principle).
    speed is knots (AIS-native). Nulls mean 'not reported'."""
    ts: str                              # ISO-8601 UTC of the fix
    lat: float | None = None
    lon: float | None = None
    speed: float | None = Field(None, description="SOG, knots")
    course: float | None = Field(None, description="COG, degrees true")
    heading: float | None = None
    nav_status: int | None = None
    position_quality: str | None = None  # plausible | suspect | implausible | null
    anomaly_flags: list[str] | None = None  # e.g. ['impossible_speed','teleport_return']
    source: str                          # feed that produced the fix (aisstream, …)
    data_origin: str                     # terrestrial | …


class VesselTrackResponse(BaseModel):
    """Ordered fix sequence for ONE MMSI over an optional window (default the
    full frozen span). PK-backed on vessel_states (mmsi, ts, source) — always
    cheap, bounded by that vessel's fixes. Capped at `limit` rows (has_more when
    more exist — narrow the window). points are ASC by time."""
    mmsi: str
    from_time: int | None = Field(None, alias="from", description="window start (epoch s), null = open")
    to_time: int | None = Field(None, alias="to", description="window end (epoch s), null = open")
    count: int
    limit: int
    offset: int
    has_more: bool
    points: list[VesselTrackPoint] = Field(default_factory=list, description="ordered by ts ASC")

    model_config = {"populate_by_name": True}


class VesselSummary(BaseModel):
    """The collapsed 'everything about this vessel' card: identity + last known
    fix + behavioural event COUNTS. The counts reuse the existing per-type
    semantics (A1 gap candidate, A2 rendezvous — INCLUDING where this hull was
    the partner, A3 identity_change, G4a GNSS individual/candidate), stitched by
    MMSI ∪ IMO so a re-flagged hull's events unify (the composite principle).
    A count is a tally of INVESTIGATION LEADS, never proof — see
    VesselSearchResponse.caveat. The track (full fix sequence) is the expansion,
    served by /vessel/{mmsi}/track."""
    mmsi: str
    imo: str | None = None
    name: str | None = None
    flag_country: str | None = None
    ship_type: str | None = None
    ship_type_code: int | None = None
    callsign: str | None = None
    length_m: float | None = None
    beam_m: float | None = None
    station_type: str | None = None
    # last known fix (the vessel's data vintage) — from vessel_states
    last_ts: str | None = None
    last_lat: float | None = None
    last_lon: float | None = None
    last_speed: float | None = None
    last_position_quality: str | None = None
    last_anomaly_flags: list[str] | None = None
    # IMO-2: the integrity verdict on this vessel's IMO (migration 035) — a spine
    # caveat, shown alongside the identity; NULL = no IMO / not-yet-checked.
    imo_quality: str | None = None       # valid|checksum_fail|malformed|contested
    imo_flags: list[str] | None = None
    n_fixes: int = 0                     # track density over the frozen span
    # behavioural event counts (candidate/individual tiers) — the summary signal
    n_gaps: int = 0
    n_rendezvous: int = 0
    n_identity_changes: int = 0
    n_gnss: int = 0
    n_pattern_types: int = 0             # how many of the 4 patterns are present (multi-pattern signal)


class VesselSearchResponse(BaseModel):
    """DB-backed vessel lookup (distinct from the snapshot-only /search). Resolves
    q against vessel_identity by MMSI (exact), IMO (exact), or name (substring),
    plus a bare tracked MMSI present in vessel_states. Each match is a summary; the
    track is the expansion. `caveat` MUST be surfaced — the event counts are tallies
    of investigation leads, not proof."""
    query: str
    entity: str = "vessel"
    count: int = 0
    caveat: str = ""
    matches: list[VesselSummary] = []


# --- v1 ADDITIVE: entity search (E2 — aircraft) ----------------------------
# The search tab's second entity, plugged into the E1 shell. Unlike the sea, the
# air domain has NO behavioural-event layer — so an aircraft summary is a PRESENCE
# card (identity + last-seen + AOI status), and the track (served by the existing
# /history) is the expansion. Air data is LIVE (three feeds), so the last fix is
# the current position for an airborne aircraft, and last_ts shows how fresh it is.

class AircraftSummary(BaseModel):
    """The collapsed 'everything about this airframe' PRESENCE card. There are no
    air behavioural events (cf. the maritime event counts), so this is identity +
    last-known fix + AOI classification, honestly — the track is the expansion
    (/history?icao24=). callsign is the CURRENT/last flight identifier (one icao24
    flies many callsigns over time); registration/type/operator come from
    aircraft_info (fallback to the denormalised fix columns); origin/dest are the
    STANDING route (flight_routes by callsign), not the filed flight plan."""
    icao24: str
    callsign: str | None = None          # current/last callsign — a flight id, not the airframe
    registration: str | None = None
    aircraft_type: str | None = None     # human type (aircraft_info) or acdesc (fix)
    icao_type: str | None = None         # ICAO type code (aircraft_info) or actype (fix)
    operator: str | None = None
    operator_country: str | None = None
    origin_icao: str | None = None       # standing route (flight_routes by callsign)
    origin_name: str | None = None
    dest_icao: str | None = None
    dest_name: str | None = None
    # last known fix — LIVE for an airborne aircraft; the presence signal
    last_ts: str | None = None
    last_lat: float | None = None
    last_lon: float | None = None
    baro_altitude: float | None = Field(None, description="meters")
    geo_altitude: float | None = Field(None, description="meters")
    on_ground: bool | None = None
    velocity: float | None = Field(None, description="m/s")
    true_track: float | None = Field(None, description="degrees, 0=north")
    # AOI classification of the last fix (P3 geo)
    country: str | None = None
    near_country: str | None = None
    position_quality: str | None = None
    distance_km: float | None = None
    source: str | None = None            # feed of the last fix (opensky/adsbexchange/airplanes_live)
    first_seen: str | None = None        # earliest fix ts — "tracked since"


class AircraftSearchResponse(BaseModel):
    """DB-backed aircraft lookup (distinct from the snapshot-only /search). Resolves
    q by icao24 (exact, PK), registration (substring, aircraft_info), and callsign
    (substring, over a RECENT window — a callsign is a live flight id, not searched
    across all history). Each match is a presence summary; the track is the
    expansion (/history). `caveat` MUST be surfaced."""
    query: str
    entity: str = "aircraft"
    count: int = 0
    caveat: str = ""
    matches: list[AircraftSummary] = []


# --- v1 ADDITIVE: entity search (E3 — satellite) ---------------------------
# The search tab's third entity. The conceptual difference from vessel/aircraft:
# a satellite has NO stored position fixes — its position is computed from its TLE
# via SGP4 at any instant, and its "track" is a derived ground-track, not a stored
# sequence. So the summary composes the existing satellite LAYERS, each with its
# OWN vintage (the satellite base's provenance discipline — vintages are NOT
# collapsed): identity (SATCAT, ~2026) / orbit (derived-SGP4, live) / strategic
# (UCS, FROZEN 2023) / AOI passes (forward ~24 h prediction). The TLE travels with
# the summary so the client propagates the ground-track (no stored fixes to pull).

class SatelliteSummary(BaseModel):
    """The collapsed 'everything about this satellite' card, composed from the
    satellite layers WITHOUT collapsing their vintages (the analyst must see
    'identity 2026 / orbit live / strategic 2023'). No stored fixes: the TLE
    (tle1/tle2/tle_epoch) travels with the summary and the client propagates the
    ground-track (SGP4) — the expansion is COMPUTED, not pulled."""
    norad_id: int
    object_name: str | None = None
    # identity (satellite_info — CelesTrak SATCAT); vintage = identity_vintage
    international_designator: str | None = None
    object_type: str | None = None
    object_type_label: str | None = None
    owner_name: str | None = None
    launch_date: str | None = None
    identity_vintage: str | None = None        # satcat_snapshot_date (ISO date)
    # orbit (satellite_orbit — derived SGP4); vintage = orbit_vintage (tle_epoch)
    orbit_class: str | None = None
    apogee_km: float | None = None
    perigee_km: float | None = None
    inclination_deg: float | None = None
    period_min: float | None = None
    is_geostationary: bool | None = None       # GEO → ground-track is a stationed band, not a moving track
    orbit_vintage: str | None = None           # tle_epoch of the derived orbit (ISO)
    # strategic (satellite_strategic — UCS); vintage = strategic_vintage (FROZEN 2023)
    users: str | None = None
    purpose: str | None = None
    operator_country: str | None = None
    strategic_vintage: str | None = None       # ucs_snapshot_date (ISO date) — flag as frozen-2023
    # AOI passes (satellite_passes — forward-window PREDICTION); the window is stated
    n_passes: int = 0
    is_persistent: bool = False                # a GEO/geosync stationing over the AOI band
    pass_window_start: str | None = None
    pass_window_end: str | None = None
    # TLE (satellite_elements) — the client propagates the ground-track from this
    tle1: str | None = None
    tle2: str | None = None
    tle_epoch: str | None = None               # epoch of the TLE used for propagation (ISO)


class SatelliteSearchResponse(BaseModel):
    """DB-backed satellite lookup. Resolves q by NORAD id (exact) or object_name
    (substring). Each match composes the satellite layers with their vintages kept
    DISTINCT. The ground-track is computed client-side from the carried TLE (SGP4).
    `caveat` MUST be surfaced — vintages differ (strategic is frozen 2023), passes
    are a forward prediction, and SGP4 accuracy decays from the TLE epoch."""
    query: str
    entity: str = "satellite"
    count: int = 0
    caveat: str = ""
    matches: list[SatelliteSummary] = []


# --- v1 ADDITIVE: entity search (E4 — airport → landings) ------------------
# The one-to-many entity: airport → the aircraft that landed there. Resolved from
# a committed OurAirports gazetteer (in-process, no DB identity table), then the
# landings are DERIVED on-query from state_vectors (no stored "landed" fact). Each
# is a CANDIDATE, not a proven landing; quality is coverage-bounded.

class AirportSummary(BaseModel):
    """One airport from the committed gazetteer (OurAirports, filtered to the AOI).
    elevation_ft is MSL — the landing detector's altitude ceiling is RELATIVE to it."""
    icao: str
    iata: str | None = None
    name: str | None = None
    municipality: str | None = None
    country: str | None = None            # ISO 3166 alpha-2
    lat: float
    lon: float
    elevation_ft: int | None = None
    type: str | None = None               # large_airport | medium_airport


class LandingCandidate(BaseModel):
    """One aircraft's landing CANDIDATE at an airport (derived, not proven). signal
    'grounded' = an on_ground fix in the vicinity (surface contact — could be an
    arrival, departure, or parked); 'approach' = a descending track that terminated
    near field elevation then dropped below the ADS-B floor (elevation-relative)."""
    icao24: str
    callsign: str | None = None
    registration: str | None = None
    landing_ts: str                        # ISO — on_ground fix (grounded) or lowest fix (approach)
    signal: str                            # grounded | approach
    min_altitude_m: int | None = None      # lowest baro in vicinity (approach evidence)
    n_fixes: int = 0                       # vicinity fixes seen (evidence)
    source: str | None = None              # feed of the last fix


class AirportSearchResponse(BaseModel):
    """Airport lookup (gazetteer-resolved) + its landing candidates over a recent
    window. `matches` are the airports resolved from q; `airport` + `candidates` are
    filled for the FIRST/selected match (the one-to-many detail). `caveat` MUST be
    surfaced — candidates, not proven landings; quality is coverage-bounded."""
    query: str
    entity: str = "airport"
    count: int = 0                         # number of airports resolved
    caveat: str = ""
    window_hours: int = 24
    matches: list[AirportSummary] = []     # all airports matching q (the result list)
    airport: AirportSummary | None = None  # the resolved airport whose landings are returned
    candidates: list[LandingCandidate] = []


# --- v1 ADDITIVE: area-search (D4 — draw a box → what crossed it) -----------
# NOT an entity: a MODE. "draw a box → every air/sea/space entity that crossed it
# in a window." Air uses the native-point GiST (migration 031); sea the SQL box
# (+ its GiST); space an SGP4 recompute over the box×window (not satellite_passes,
# which is AOI-scoped + forward-only). Each entry is a LIGHT discovery row (id +
# label + last-in-box position); selecting it reuses the E1–E3 search-by-id
# endpoints for the full summary + detail.

class AreaEntry(BaseModel):
    """One entity that crossed the box (a discovery row, not a full summary)."""
    id: str                                # icao24 | mmsi | NORAD (as str)
    label: str                             # callsign / vessel name / object_name (or the id)
    sublabel: str | None = None            # the raw id / a type hint
    lat: float | None = None               # last in-box fix (air/sea) or crossing sub-point (space)
    lon: float | None = None
    ts: str | None = None                  # ISO — last in-box fix / first crossing time


class AreaSearchResponse(BaseModel):
    """Multi-domain area-search result. box is the normalised [lat_min, lon_min,
    lat_max, lon_max]. Each domain is a list of AreaEntry (empty if that domain was
    filtered out or nothing crossed). `caveat` MUST be surfaced — mixed liveness
    (sea frozen; air live), and the space list is an SGP4 sampling (brief crossings
    of a very small box between samples can be missed)."""
    box: list[float]
    since: int | None = None
    until: int | None = None
    window_hours: float = 2.0
    caveat: str = ""
    air: list[AreaEntry] = []
    sea: list[AreaEntry] = []
    space: list[AreaEntry] = []


# --- v1 ADDITIVE: alerts (D5 — the multi-domain classification feed) --------
# The monitor's default (resting) state is a LIVE DASHBOARD, and the alert feed is
# its heart: one unified Alert shape over FOUR families read live from the SAME
# signal sources the app already serves (no stored alert table, no new detector —
# read-over-signals). Severity is per-family and HONEST (emergency high / military
# info / maritime = its composite rank / implausible low) so a presence signal never
# reads as a threat. `matched_query` is the ADDITIVE seam for a future search↔alert
# join; it is always null today.

class AlertEntityRef(BaseModel):
    """The entity an alert is about — enough to open it on the map + reuse the
    existing E1–E3 search-by-id detail (kind → which engine; id → its key).

    ⚠ `id` is the entity's DURABLE identity, which for a maritime composite is the
    **IMO** — deliberately, because an IMO survives a re-flag and is what stitches a
    hull's history together. But every live vessel payload is **MMSI-keyed**
    (`Vessel.mmsi`, `/vessel/{mmsi}`, `/vessel/{mmsi}/track`), so an IMO alone cannot
    open the current payload. `live_key` closes that gap.

    `live_key` — the key that addresses the LIVE payload for this entity: the MMSI
    currently transmitting this hull, `icao24` for aircraft, NORAD for satellites.
    For a vessel it is resolved as **the MMSI with the most recent fix**, because
    IMO → MMSI is genuinely ONE-TO-MANY across a re-flag and only one of them is
    current. Null when nothing has been heard from any of the hull's MMSIs (the
    honest answer: there is no live payload to open).

    `alt_keys` — the hull's OTHER MMSIs, most-recent first. A re-flagged vessel's
    earlier identities are still real history, so they are carried rather than
    discarded; a caller wanting the full track walks these too."""
    kind: str                              # aircraft | vessel | satellite
    id: str                                # icao24 | mmsi | hull(IMO) | NORAD (as str)
    label: str | None = None               # human label (callsign / vessel name / object name)
    live_key: str | None = None            # key for the LIVE payload (MMSI for a vessel)
    alt_keys: list[str] = []               # the hull's other MMSIs, most-recent first


class Alert(BaseModel):
    """One alert — a live read over an existing signal, never a stored row. The
    honesty fields (severity_basis, vintage, live) travel WITH each alert so a card
    can never present military presence or an implausible position as a high threat,
    and so a stale feed reads as stale.

    `countries` — the AOI countries the alert TOUCHES, as ISO 3166-1 alpha-2 codes
    (backend/country_iso.py). It is a LIST, never a scalar, because two families are
    genuinely multi-country: a hull's events can span several AOI waters
    (`aoi_waters`) and a satellite pass crosses several borders. Collapsing that to
    one value would silently drop observed facts.

    It is a GEOGRAPHIC fact — where the thing is — and must never be confused with
    `flag_country` (a vessel's registration) or `origin_country`/`operator_country`
    (an aircraft's or satellite's registration). Those describe WHOSE it is, not
    WHERE it is; they stay in `summary` where they already were.

    Empty means "not attributable to an AOI country" — outside the seven, or (for a
    live feed) no position to classify. Empty is NOT "unknown-and-probably-somewhere":
    absence here is the same honest absence the rest of the base records.

    `importance` — a DISPLAY-ORDERING rank (lower sorts first), and a SECOND AXIS
    that is orthogonal to `severity`. Severity answers "how dangerous is this claim?";
    importance answers "how much does this deserve the analyst's eye in a country
    window?". Both are true at once: a military alert is `severity="info"` AND
    `importance=1`, because presence is the salient fact of a country watch and is
    still not a threat.

    ⚠ **`importance` is NOT a threat score.** Do not colour a card by it, do not sum
    it, and never present it as risk — `severity` + `severity_basis` remain the only
    honest register for that, and they are unchanged. This field exists so one view
    can order a list without the project asserting a threat it has committed to not
    asserting (see backend/alerts.py IMPORTANCE_ORDER)."""
    domain: str                            # air | sea | space
    family: str                            # emergency | military | maritime | implausible
    signal_type: str                       # emergency_squawk | military_aircraft | military_satellite | maritime_composite | implausible_position
    severity: str                          # high | medium | low | info
    severity_basis: str                    # WHY this severity — always shown (keeps info/low honest)
    entity_ref: AlertEntityRef
    countries: list[str] = []              # AOI countries this alert TOUCHES, ISO alpha-2 (see below)
    importance: int = 9                    # DISPLAY-ordering rank, orthogonal to severity (see below)
    ts: str | None = None                  # ISO — when the signal is from / as-of
    summary: str = ""                      # terse factual one-liner (frontend localises the framing)
    source: str = ""                       # the underlying feed (opensky / airplanes.live / aisstream+vessel_states / celestrak+ucs)
    vintage: str | None = None             # ISO/human — freshness of the underlying data (a hull's last event, a TLE epoch, …)
    live: bool = False                     # is the underlying feed live right now?
    matched_query: str | None = None       # ADDITIVE seam (search↔alert join) — always null today

    # ---- BASELINE-1: how this compares with the ENTITY'S OWN usual behaviour ----
    # `routine` | `notable` | `unknown` | None (family has no baseline concept).
    #
    # ⛔ A THIRD AXIS, AND NOT A THREAT ONE. `severity` says how dangerous the claim is and
    # is untouched by this — a satellite pass stays INFO whether routine or notable.
    # `importance` orders a country card. This says only "is this typical for this object?",
    # and atypical is not dangerous. Do not colour by it, do not sum it, do not rank by it.
    #
    # ⛔ `unknown` IS A REAL VALUE AND MUST BE RENDERED AS ITSELF. A missing or thin
    # baseline silently reading as `routine` is how a system starts asserting a normality it
    # never measured — the exact failure the baseline was built to prevent. Consumers must
    # not treat null/unknown as "fine".
    #
    # Populated for `military_satellite` (backend/pass_baseline.py, migration 041). Null for
    # every other family, which has no baseline yet — null means "not assessed", never "normal".
    baseline_verdict: str | None = None

    # ---- NEWS-1: the news-brief LEDE ----
    # A short title stating WHAT was observed and WHERE, composed in alerts.py from the same
    # structured facts the `headline` states at length. It exists because a news layout needs
    # a title and `headline` is a full sentence.
    #
    # ⛔ IT CHARACTERISES NOTHING. No «لافتة», no «غير اعتيادية», no adjective of concern —
    # the severity badge beside it is the only ranking this layer may show, and «unusual» in
    # particular is a comparison against a norm that exists ONLY for satellites
    # (backend/pass_baseline.py). Empty for a family with no lede yet; renderers fall back to
    # the headline rather than inventing one.
    lede: str = ""

    # ---- BE-SUMMARY-1: the readable Arabic register (maritime family first) ----
    # THREE fields, not one, because they answer three different questions and collapsing
    # them is how a caveat gets dropped:
    #   headline — the occurrence AS OBSERVED (what, where, how often). No interpretation.
    #   why      — why it ranks here. Carries `severity_basis` in readable Arabic; it
    #              restates the existing verdict and never forms a new one.
    #   caveat   — what this does NOT mean. MANDATORY for maritime, and the reason the
    #              field exists: a lead read as a finding is the failure mode this project
    #              is built to avoid.
    # ⚠ Empty string = this family has no Arabic register yet. Populated for `maritime`
    # (BE-SUMMARY-1) and `implausible` (BE-SUMMARY-2, separately calibrated — an
    # implausible fix is a DATA DEFECT, so its sentences describe the observation and
    # never make the vessel the actor). `emergency`, `military` and the satellite family
    # are deliberately still NOT generalised to: each needs its own tone (a reported
    # emergency is a stated fact, military is INFO by commitment, and a satellite pass is
    # a forward PREDICTION, so «رُصد» would be false for it). `summary` (English, terse)
    # is unchanged and still authoritative for every family, so nothing downstream breaks
    # on the empty default.
    headline: str = ""
    why: str = ""
    caveat: str = ""
    # ---- BE-SUMMARY-2: the registration flag as a CODE, beside the name ----
    # ⚠ ISO 3166-1 alpha-2, or None — never guessed. It exists because the card could not
    # render a flag glyph from a name: the frontend correctly refused to invent a
    # ~200-entry name→ISO table the backend had not shipped.
    # ⚠ COVERAGE IS PARTIAL BY CONSTRUCTION, and that is the honest state, not a bug. The
    # only name→ISO source this project owns is `country_iso` — the SEVEN AOI countries.
    # The committed MID asset (`backend/data/mmsi_mid.json`) maps MID → country NAME and
    # carries NO ISO column, so a Panama- or Liberia-flagged hull resolves to None rather
    # than to a code someone authored by hand. A registry flag is a global fact; the seven
    # AOI codes are not a substitute for an ITU/ISO join, and pretending otherwise would
    # put a wrong flag on a card.
    flag_iso: str | None = None

    # ======================================================================================
    # DASH-3 — THE EPISTEMIC AXIS. Two clocks, never one.
    #
    # ⛔ WHY `ts` WAS NOT ENOUGH, MEASURED ACROSS THE FIVE BUILDERS IN alerts.py: every
    # family sets `ts` to an OBSERVATION-SHAPED time, and one of them sets it to the FUTURE.
    #     emergency / military air  ts = the feed snapshot        (recent past)
    #     maritime composite        ts = the hull's last event    (historical, arrives LATE)
    #     implausible / MT          ts = the last / first fix     (historical)
    #     military satellite        ts = the pass START           ⛔ AHEAD OF NOW
    # Sorting one list by `ts` therefore interleaves a prediction about tomorrow with an
    # observation from last week and calls both "when it happened".
    #
    # ⛔ FOUR INDEPENDENT STANDARDS BODIES REFUSE TO COLLAPSE THESE, and this project follows
    # the oldest of them. OGC Observations & Measurements (Abstract Topic 20, 20-082r4) splits
    # `phenomenonTime` — "the time to which the result applies" — from `resultTime`, "the
    # instant when the result became available", and its own EXAMPLE 5 is literally this case:
    # "Simulations can be used to estimate the values for phenomena in the future... the
    # phenomenonTime is the time to which the result applies, while the resultTime is the time
    # at which the simulation was executed." GTFS-Realtime carries `NO_DATA` as a state
    # distinct from "on schedule"; NWS gives forecasts an INTERVAL validTime and observations
    # an instant; STIX 2.1 separates an `indicator` (a pattern) from a `sighting` (an
    # assertion that it was seen).
    #
    # ⚠ `ts` IS UNCHANGED AND IS NOT DEPRECATED. Every existing consumer keeps working and no
    # value moves. These fields are ADDITIVE and sit beside it.
    # ======================================================================================

    #: When the reported thing APPLIES — its start. Equal to `ts` for the families whose
    #: signal is an instant; the FUTURE pass start for a satellite; the event's own start for
    #: a maritime behaviour, which is a duration and not a moment.
    phenomenon_start: str | None = None

    #: When it STOPS applying. ⛔ `None` is NOT "zero length" and NOT "unknown" — it means the
    #: signal is an INSTANT (an emergency squawk, a position fix) or the interval is still
    #: OPEN (`maritime_events.end_ts IS NULL` — a gap that has not closed). Shneiderman's own
    #: definition of temporal data is that "items have a start and finish time and that items
    #: may overlap"; a renderer that draws a ribbon needs both ends or it must not draw one.
    phenomenon_end: str | None = None

    #: When this result was COMPUTED or RECEIVED — the detector run, the feed snapshot, the
    #: TLE propagation. ⚠ Distinct from `vintage`, which describes the freshness of the
    #: UNDERLYING data; this is when THIS statement came into existence.
    result_time: str | None = None

    #: When a detector FIRST wrote this event (maritime only today, from
    #: `maritime_events.first_seen_at`, migration 047/048).
    #: ⛔ `None` MEANS UNKNOWN NOVELTY, NEVER "NEW" AND NEVER THE EPOCH. The pre-migration
    #: archive carries NULL, and rendering unknown as new is the fail-OPEN reading — this
    #: project's rule is that a guard fails CLOSED.
    first_seen_at: str | None = None

    #: Age of the underlying observation, in seconds, at the moment this alert was generated.
    #: ⛔ REQUIRED ON EVERY LIVE ROW, not an optional extra. Kay, Kola, Hullman & Munson
    #: (CHI 2016) found that for OneBusAway's expert users "one of the only available signals
    #: ... to assess risk is the freshness of the data", and MarineTraffic states outright
    #: that its "Data received on" stamp is what "allows you to quickly assess how current the
    #: vessel's position is BEFORE relying on it for any decision". `None` only when no
    #: underlying timestamp exists to measure against.
    observed_age_s: float | None = None

    #: ⛔ THE COUNTERPART, AND WHY IT EXISTS. A rendezvous is a statement about TWO vessels,
    #: and this model could only name one — so no consumer could render the relationship the
    #: signal is about, even though the pairing has always been in the database
    #: (`maritime_events.detail ->> 'partner_mmsi'`, served by /api/v1/rendezvous).
    #: ⚠ A LIST, AND THE SINGULAR WAS WRONG. This field was first declared
    #: `counterpart: AlertEntityRef | None`, which quietly assumed one. A maritime alert is
    #: about a HULL that may carry several rendezvous — `CompositeHull.n_rendezvous` counts
    #: them — so a single slot would have had to pick one and silently drop the rest, or go
    #: None and drop all of them. Corrected before anything consumed it.
    #: ⚠ Empty on every other family, and on a rendezvous whose partner was not resolved.
    #: ⛔ NEVER inferred from proximity in a list: two vessels sharing a digest is not a
    #: rendezvous, and asserting one is exactly the claim CAVEAT_RENDEZVOUS denies.
    counterparts: list[AlertEntityRef] = []
    #: ⛔ Per-pattern windows (see PatternSpan). Empty on every non-composite family: an
    #: emergency squawk is one instant and a satellite pass is one interval, and neither has
    #: patterns to lay out beside each other.
    pattern_spans: list[PatternSpan] = []
    n_pattern_spans: int = 0

    #: ⛔ DASH-4a — COLLECTIVE TRIAGE. `None` means OPEN, and open is the ABSENCE of a row in
    #: `alert_triage`, never a stored value (migration 051). Shared by every analyst: this
    #: deployment has no per-analyst identity by the owner's decision, so "reviewing" means
    #: SOMEONE is on it — which is the whole point, since two analysts opening the same vessel
    #: is the waste a shared screen otherwise invites.
    triage: str | None = None
    #: A human note left with the state. Carries whatever the analyst chooses to say — there
    #: is no `analyst` column, and its absence is the decision, not an omission.
    triage_note: str | None = None
    #: ⛔ TRUE WHEN THE THING CHANGED AFTER IT WAS TRIAGED — the stored `change_sig` no longer
    #: matches the live one. A closed alert whose signature moved is NOT still closed: it is a
    #: new statement about a known object, and hiding it because of a stale verdict is how a
    #: triage system starts suppressing the updates it exists to surface.
    triage_stale: bool = False
    #: ⛔ THE SIGNATURE, ON THE WIRE, SO A CLIENT CAN ECHO IT BACK RATHER THAN RE-DERIVE IT.
    #: `notify.identity.change_sig` is per-family Python (maritime is `ts|severity`, military
    #: air is a presence constant, …) and a browser mirroring those rules would drift the day
    #: one changed. The first version of the triage UI sent `severity_basis` instead and every
    #: triaged item came back `triage_stale=true` immediately — the feature inverted by a
    #: guess. Echoing removes the second definition entirely.
    change_sig: str = ""
    #: v1 ADDITIVE 2026-09-01 — the alert's OWN identity key (`notify.identity.dedup_key`),
    #: published so clients never re-derive it. The dashboard used to mirror the key rules
    #: by hand and had silently drifted on maritime_single (missing the `:summary` part) —
    #: a triage write under the drifted key could never be matched back at render time.
    #: The server key is authoritative; the client mirror survives only as a fallback,
    #: pinned by the caseKey parity anchors (tests/test_case_key_parity.py ↔ caseKey.test.ts).
    dedup_key: str = ""

    @property
    def is_prediction(self) -> bool:
        """True when this statement was made BEFORE the thing it describes.

        ⛔ COMPUTED, NEVER STORED. OGC gives the machine test as `resultTime < phenomenonTime`
        and this is that test, evaluated from the two fields rather than duplicated into a
        third one that could drift out of agreement with them.
        ⚠ FAILS CLOSED: missing either clock returns False. "We cannot tell" must not render
        as "this is a prediction" — an unlabelled observation is a smaller error than an
        observation labelled as a forecast."""
        if not self.result_time or not self.phenomenon_start:
            return False
        return self.result_time < self.phenomenon_start


class AlertCounts(BaseModel):
    """True per-family counts (NOT the length of the returned `alerts` list, which
    may be per-family capped) + the total."""
    emergency: int = 0
    military: int = 0
    maritime: int = 0
    implausible: int = 0
    total: int = 0


class AlertDomainStat(BaseModel):
    """A domain stat card for the dashboard header — a lightweight live count with
    its own liveness/vintage (the mixed-liveness honesty). Reuses the counts the app
    already serves (no new stats engine): air = /live states, sea = live AIS snapshot
    (+ last-fix vintage), space = tracked TLE catalogue."""
    domain: str                            # air | sea | space
    count: int = 0
    live: bool = False
    vintage: str | None = None             # ISO — snapshot time / last fix / TLE fetch
    source: str = ""


class TriageRequest(BaseModel):
    """DASH-4a — set (or clear) the collective triage state of one alert.

    ⚠ `state=None` CLEARS the row, i.e. returns the alert to OPEN. Open is the absence of a
    row (migration 051), so "reopen" is a DELETE and not a third stored value — there is
    exactly one representation of open, and it cannot drift from the other."""
    dedup_key: str
    #: reviewing | closed | null(=reopen). Validated against the table's CHECK server-side.
    state: str | None = None
    note: str | None = None
    #: ⛔ THE SIGNATURE THE ANALYST WAS LOOKING AT, sent by the client rather than re-derived
    #: on the server. Re-deriving would mean rebuilding the alert server-side — a second
    #: computation that could disagree with the one on screen, so a "closed" verdict could be
    #: recorded against a state the analyst never saw. Empty is allowed and simply disables
    #: the staleness check for that row (`triage_stale` stays false), which is the honest
    #: degradation: unknown staleness must not render as "changed".
    change_sig: str | None = None


class PatternSpan(BaseModel):
    """ONE behaviour pattern's own time window on one hull.

    ⛔ WHY THE ALERT'S OWN `phenomenon_start`/`_end` IS NOT ENOUGH. Those are the hull's
    ENVELOPE — first event to last — so a hull whose gap was on Monday and whose rendezvous
    was on Friday reports one five-day block. Read as a lane that is a lie: it says both
    patterns ran all week. Co-occurrence is the whole claim a composite makes, and it can
    only be READ if each pattern keeps its own bar.
    ⚠ `end` is nullable and null means OPEN, not zero-length — `maritime_events.end_ts IS
    NULL` is a gap that has not closed. A renderer that draws null as a point asserts the
    thing ended."""
    type: str                              # ais_gap | rendezvous | identity_change | …
    start: str
    end: str | None = None


class CaseItem(BaseModel):
    """One kept piece of evidence — the claim AS IT WAS WHEN COLLECTED.

    ⛔ A SNAPSHOT, NOT A REFERENCE. The alert feed is stateless and rebuilt per request, so an
    item collected today is simply absent once its window rolls past. Storing only a key would
    empty the case file as the feed moved on and leave a report citing rows nobody can produce.
    ⚠ `still_live` says whether the same `dedup_key` is in the CURRENT feed. It is INFORMATION,
    not a correction: an item that has left the window has not been disproven, and the snapshot
    is deliberately not refreshed from it."""
    dedup_key: str
    label: str
    domain: str | None = None
    signal_type: str | None = None
    severity: str | None = None
    headline: str | None = None
    why: str | None = None
    #: ⛔ Kept with the rest and never dropped — a lead copied into a report without what it
    #: does NOT mean is the failure mode models.py names.
    caveat: str | None = None
    countries: list[str] = []
    alert_ts: str | None = None
    note: str | None = None
    added_at: str
    still_live: bool = False


class CaseRequest(BaseModel):
    """Add one alert to the shared case file, or remove it.

    ⚠ `remove=True` needs only `dedup_key`; every other field is the snapshot and is ignored."""
    dedup_key: str
    remove: bool = False
    label: str | None = None
    domain: str | None = None
    signal_type: str | None = None
    severity: str | None = None
    headline: str | None = None
    why: str | None = None
    caveat: str | None = None
    countries: list[str] = []
    alert_ts: str | None = None
    note: str | None = None


class AlertsResponse(BaseModel):
    """The D5 alert feed + the dashboard's domain stats, in one envelope so a single
    fetch fills the resting dashboard. `counts` are the TRUE per-family totals;
    `alerts` is sorted most-severe first and per-family capped. `caveat` MUST be
    surfaced — severity is per-family and honest, and liveness is mixed."""
    generated_at: str
    window_hours: int = 24
    counts: AlertCounts = AlertCounts()
    #: The AOI country the feed was restricted to (ISO alpha-2), or null for all.
    #: Echoed so a filtered view can never be mistaken for the whole picture — and
    #: so a client can tell "no alerts in Syria" from "no filter was applied".
    countries_filtered: str | None = None
    #: Which ordering produced `alerts` — "severity" (default, the honest threat
    #: order) or "importance" (the country card's display order). Echoed so a client
    #: never has to guess which axis it is looking at.
    sorted_by: str = "severity"
    domains: list[AlertDomainStat] = []
    caveat: str = ""
    alerts: list[Alert] = []


# --- v1 ADDITIVE: zone dwell (ZONES-2 — on-query, annotation-only) -----------
# Ranked dwell at published offshore infrastructure. NOT stored (no maritime_events
# row, no composite — that is ZONES-3, gated behind seeing this measured output).
# Two signals: safety_zone_intrusion (platform 500 m core — rare, strong, NOT type-
# suppressed) and standoff_dwell (500 m..3 km working band — fully gated); gas_fields
# give a single weaker field_dwell (assumed radius over a centroid). radius_basis +
# coord_precision ride on every row so evidence strength is explicit, not implicit.

class ZoneDwellSession(BaseModel):
    """One vessel's low-speed dwell at one zone. Nothing is silently dropped:
    demoted/excluded sessions are returned with their verdict so the gated-out set
    stays queryable. duration is a LOWER BOUND under feed outage."""
    zone_id: str
    zone_name: str | None = None
    zone_type: str                         # gas_field | platform
    radius_basis: str                      # sourced | assumed (evidence strength)
    coord_precision: str | None = None     # surveyed | exact | approximate
    signal: str                            # safety_zone_intrusion | standoff_dwell | field_dwell
    mmsi: str
    imo: str | None = None
    name: str | None = None
    flag_country: str | None = None
    ship_type: str | None = None           # SELF-REPORTED (see ship_type_caveat)
    ship_type_caveat: str = ""             # trust caveat on the self-reported type
    start_ts: str
    end_ts: str | None = None
    duration_s: float
    duration_is_lower_bound: bool = True
    n_fixes: int = 0
    min_distance_m: float                  # closest approach to the zone centre
    mean_implied_speed_kn: float | None = None   # position-derived (NOT reported SOG)
    min_implied_speed_kn: float | None = None
    reported_sog_kn: float | None = None   # corroboration only, sentinel-filtered
    verdict: str                           # intrusion | lead | demoted_type | excluded_anchorage | excluded_short
    exclusion_reasons: list[str] = []      # type_gated | in_anchorage_mask | below_duration_threshold
    cloaking_exposed: bool = False         # demoted by a type that is unverifiable/inconsistent


class ZoneDwellAccounting(BaseModel):
    """Per-zone exclusion accounting (the DRAUGHT-1 insufficient_data lesson): what the
    gate removed, and — distinctly — whether NO dwell was observed at all."""
    zone_id: str
    zone_name: str | None = None
    zone_type: str
    sessions_total: int = 0
    intrusions: int = 0
    leads: int = 0
    demoted_type: int = 0
    excluded_anchorage: int = 0
    excluded_short: int = 0
    no_dwell_observed: bool = False        # distinct from "excluded by a self-reported type"


class ZoneDwellSummary(BaseModel):
    """Signal-class totals + the cloaking-exposure number (the honest blind-spot size)."""
    safety_zone_intrusion: int = 0
    standoff_dwell: int = 0
    field_dwell: int = 0
    leads: int = 0
    demoted: int = 0
    excluded: int = 0
    cloaking_exposed_sessions: int = 0
    cloaking_note: str = ""


class ZoneReadiness(BaseModel):
    """One zone's baseline-readiness counts against the sufficiency bars."""
    zone_id: str
    span_days: float = 0.0
    vessels: int = 0
    sessions: int = 0
    sound: bool = False


class ZoneDwellReadiness(BaseModel):
    """Baseline-readiness (ZONES-4 dormancy fix): standoff_dwell admission is deferred
    while no zone is data-sound; this makes the deferral a VISIBLE state — which BAR binds
    (days | vessels | sessions), the gap to it, and per-zone progress — so it announces
    itself the moment the bars are met rather than staying silently dormant."""
    baseline_ready: bool = False
    window_hours: int | None = None
    zones_with_history: int = 0
    zones_sound: int = 0
    bars: dict = {}                        # {min_days, min_vessels, min_sessions}
    zones_meeting: dict = {}               # how many zones meet each bar
    best_observed: dict = {}               # best span_days / vessels / sessions across zones
    binding_bar: str | None = None         # the bar met by the fewest zones (the systemic blocker)
    gap_to_binding: float | None = None    # how far the best-positioned zone is from that bar
    per_zone: list[ZoneReadiness] = []


class ZoneDwellResponse(BaseModel):
    """On-query zone dwell over a recent window, ranked (intrusions first, then leads
    by duration, demoted lower, excluded last). `caveat` MUST be surfaced — every dwell
    has legitimate explanations and duration is a lower bound. available=false when the
    DB is unreachable."""
    available: bool = True
    window_hours: int = 168
    zones_queried: int = 0
    count: int = 0
    method: str = ""
    caveat: str = ""
    summary: ZoneDwellSummary = ZoneDwellSummary()
    readiness: ZoneDwellReadiness = ZoneDwellReadiness()
    accounting: list[ZoneDwellAccounting] = []
    sessions: list[ZoneDwellSession] = []


class PortSummary(BaseModel):
    """One gazetteer entry, with the geometry honesty that rides on every use of it:
    radius_basis is 'assumed' on EVERY feature (a search aperture, not evidence),
    coord_precision is 'arcminute' for WPI (degrees-and-minutes by publication design,
    ±~1 km, no datum stated), and merges_with names the ports it cannot be cleanly
    separated from."""
    port_id: str
    port_name: str
    alternate_name: str | None = None
    unlocode: str | None = None
    country: str | None = None
    lat: float
    lon: float
    radius_m: float
    radius_basis: str = "assumed"           # ALWAYS 'assumed' — no measured extent exists
    coord_precision: str | None = None      # arcminute | arcsecond | polygon_centroid | node
    harbor_size: str | None = None          # WPI V/S/M/L — can be STALE (the Ambarlı rule)
    harbor_type: str | None = None
    harbour_category: str | None = None     # OSM-derived features only
    source: str = ""                        # WPI | OSM
    license: str = ""
    source_ref: str = ""
    merges_with: list[str] = []
    source_note: str | None = None          # e.g. Baniyas: a ferry_terminal node, no SPM


class PortVisitSession(BaseModel):
    """One vessel's visit to one port. The four completeness states are FIRST-CLASS —
    only ~1 in 5 visits has both boundaries observed, so the incomplete states are the
    majority of the output. `boundary_note` is the rendered phrasing and can never read
    as "still in port" (the DRAUGHT-1 lesson)."""
    port_id: str
    port_name: str
    mmsi: str
    imo: str | None = None
    name: str | None = None
    flag_country: str | None = None
    ship_type: str | None = None            # SELF-REPORTED (routine commerce, not a lead)
    start_ts: str
    end_ts: str
    duration_s: float
    duration_is_lower_bound: bool = True
    low_speed_span_s: float = 0.0
    n_fixes: int = 0
    n_low_speed_fixes: int = 0
    frac_low_speed: float = 0.0
    min_distance_m: float = 0.0
    lat: float
    lon: float
    mean_implied_speed_kn: float | None = None   # position-derived, NEVER reported SOG
    min_implied_speed_kn: float | None = None
    reported_sog_kn: float | None = None         # corroboration only, sentinel-filtered
    # completeness — the majority of the output, not an edge case
    completeness: str                       # complete | open_ended | truncated | presence_only
    entry_ts: str | None = None
    exit_ts: str | None = None
    entry_missing_reason: str | None = None      # outage | vessel_silence | window_edge
    exit_missing_reason: str | None = None
    entry_outage_gap_s: float | None = None   # kept separate so an exit-side outage
    exit_outage_gap_s: float | None = None    # never renders behind an arrival note
    arrival_ts_is_unknown: bool = False     # a lower bound on an UNKNOWN quantity
    boundary_note: str = ""
    # geometry that produced this session (completeness is parameter-dependent)
    radius_m: float = 0.0
    radius_basis: str = "assumed"
    coord_precision: str | None = None
    candidate_ports: list[str] = []         # EVERY containing port, never a first pick
    dropped_fixes: int = 0                  # suspect/implausible removed in this span


class PortVisitFunnel(BaseModel):
    """Exclusion accounting for one port — how many raw crossings fell at each bar, so a
    survivor count is never reported alone (the DRAUGHT-1 lesson). Distinguishes
    "excluded by a bar" from "nothing observed at all"."""
    port_id: str
    port_name: str
    raw_crossings: int = 0
    visits: int = 0
    dropped_at: dict = {}                   # single_fix | below_min_duration | ...
    dropped_fixes_quality: int = 0
    vessels_in_radius: int = 0
    vessels_outside_radius: int = 0
    no_presence_observed: bool = False      # distinct from "excluded by a bar"
    geometry_under_covers: bool = False     # the Ambarlı rule: geometry, not traffic


class PortVisitGeometry(BaseModel):
    """The parameters that produced every number in this response. PORT-pre measured
    `complete` moving 20.9 % → 54.9 % on core radius alone, so a completeness figure
    without its geometry is meaningless — they are always returned together."""
    radius_m: float = 0.0
    radius_basis: str = "assumed"
    coord_precision: str | None = None
    gap_split_s: float = 0.0
    low_speed_kn: float = 0.0
    min_duration_s: float = 0.0
    min_low_speed_span_s: float = 0.0
    majority_low_frac: float = 0.0
    flank_s: float = 0.0
    flank_pad_km: float = 0.0
    quality_rule: str = ""                  # the stricter-than-G3 rule, stated


class PortVisitCompleteness(BaseModel):
    """The four-state distribution — returned beside the geometry, never alone."""
    complete: int = 0
    open_ended: int = 0
    truncated: int = 0
    presence_only: int = 0
    arrival_unknown: int = 0                # truncated + presence_only
    missing_entry_reasons: dict = {}        # outage | vessel_silence | window_edge
    missing_exit_reasons: dict = {}


class PortVisitCoverage(BaseModel):
    """Gazetteer coverage context. `ports_observed_reference` is a CITED MEASUREMENT, not
    a live recount — recounting means sweeping all 190 ports (~30 s), which this endpoint
    deliberately refuses. A silent port is a COVERAGE fact, never "no commerce"."""
    ports_in_gazetteer: int = 0
    ports_queried: int = 0
    ports_with_visits: int = 0
    ports_observed_reference: str = ""


class PortVisitResponse(BaseModel):
    """PORT-2 — on-query port visits for ONE resolved port. Measurement and context,
    annotation-only: no port_visit events, no composite admission, no alerts, no
    suspicion rank (all measured decisions — see backend/port_visit.py). `caveat` MUST be
    surfaced. available=false when the DB is unreachable."""
    available: bool = True
    query: str = ""
    count: int = 0            # sessions measured (before the display cap)
    returned: int = 0         # sessions in this response body
    window_hours: int = 168
    method: str = ""
    caveat: str = ""
    matches: list[PortSummary] = []
    port: PortSummary | None = None
    geometry: PortVisitGeometry = PortVisitGeometry()
    completeness: PortVisitCompleteness = PortVisitCompleteness()
    coverage: PortVisitCoverage = PortVisitCoverage()
    funnel: list[PortVisitFunnel] = []
    sessions: list[PortVisitSession] = []


# ---------- DASH-BOARD-0: the coverage contract every time series carries ----------

class CoverageGap(BaseModel):
    """One period in which a stream was NOT observed, and why.

    ⛔ `kind` is the load-bearing field. `host` = the machine was off (global, every stream
    at once); `source` = the upstream feed dropped while the host ran (per stream);
    `detector` = the phenomenon happened and nothing was looking (per detector). Three
    different causes with three different remedies — a consumer that collapses them will
    draw a deliberate shutdown and a dead detector as the same event.
    `basis` names the evidence, so a gap can always be traced back to what proved it."""
    start: datetime
    end: datetime
    kind: str                 # host | source | detector
    basis: str                # journal-boot-table | recorded | recorded-inferred | timer-enable-mtime
    source: str = ""
    seconds: float = 0.0
    note: str = ""


class SeriesCoverage(BaseModel):
    """⛔ EVERY TIME SERIES MUST BE READ WITH THIS, AND NOT WITHOUT IT.

    The consumer contract, in one line: **before drawing anything, read `series_start` and
    the two gap lists, and break the line across every window in them.** A connected line
    across a gap asserts a measurement that was never taken. `not_a_series` overrides
    everything — when it is set the stream must not be drawn as a stream at all.

    `unit` exists so that no total is ever taken across domains: air counts airframe
    reports, maritime counts detector events, space counts passes."""
    stream: str
    domain: str
    unit: str
    source: str
    series_start: datetime | None = None
    series_end: datetime | None = None
    rows: int | None = None
    computed_at: datetime
    host_gaps: list[CoverageGap] = []
    gaps: list[CoverageGap] = []
    host_gap_basis: str = ""
    gap_basis: str = ""
    observed_fraction: float | None = None
    #: ⛔ UI-0 — WHAT THE FRACTION ABOVE COUNTED. Not decoration: on a COMPOSITE series (one
    #: counting across several detectors) the contributing detectors' windows are listed in
    #: `gaps` but excluded from the fraction, because each blinds one axis rather than the
    #: series. A fraction whose inputs are not stated is a number a consumer will read as
    #: whatever it assumes — see `coverage.fraction_gaps` for the measurement that forced this.
    observed_fraction_basis: str = ""
    ratio_cutoff: datetime | None = None
    ratio_cutoff_note: str = ""
    not_a_series: str | None = None
    caveats: list[str] = []


class CoverageResponse(BaseModel):
    """All streams' coverage, or one. `available=false` when the DB is unreachable."""
    available: bool = True
    generated_at: datetime
    frame_boundary: datetime | None = None
    caveat: str = ""
    streams: list[SeriesCoverage] = []


# ---------- DASH-BOARD-2: the four maritime question contracts ----------

class SeriesBucket(BaseModel):
    """One time bucket of one SPLIT of a series.

    ⛔ `key` is never empty on a split series. Collapsing the split into a single number is
    the specific dishonesty these contracts exist to prevent — see `split_by`."""
    bucket: datetime
    key: str = ""
    count: int = 0


class MaritimeSeries(BaseModel):
    """⛔ A BUCKETED MARITIME SERIES, AND THE COVERAGE IT MUST BE READ WITH.

    CONSUMPTION LINE: read `coverage.series_start` and BOTH `coverage.host_gaps` and
    `coverage.gaps`, and break the line across every window in them — a connected line across
    a gap asserts a measurement nobody took. Never sum `buckets` across different `key`s when
    `split_by` is set. Never add this series to an air or space series: read `unit`.

    `coverage` is the DASH-BOARD-0 contract verbatim, embedded rather than recomputed."""
    available: bool = True
    question: str = ""          # which owner question this answers
    bucket: str = "day"
    unit: str = ""
    source: str = ""
    method: str = ""
    caveat: str = ""
    split_by: str | None = None
    split_note: str = ""
    totals: dict[str, int] = {}
    buckets: list[SeriesBucket] = []
    coverage: SeriesCoverage | None = None


class RendezvousPoint(BaseModel):
    """One candidate meeting — a LEAD, never a confirmed rendezvous."""
    mmsi: str = ""
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    duration_s: float | None = None
    start_lat: float | None = None
    start_lon: float | None = None
    end_lat: float | None = None
    end_lon: float | None = None
    classification: str = ""


class RendezvousPointsResponse(BaseModel):
    """⛔ POINTS AS THEY ARE — NO DENSITY SURFACE.

    `density_refused` states why in the payload rather than in a code comment, so a client
    cannot decide to bin these behind the contract's back."""
    available: bool = True
    question: str = ""
    count: int = 0
    span_days: float | None = None
    per_day: float | None = None
    unit: str = ""
    source: str = ""
    method: str = ""
    caveat: str = ""
    density_refused: str = ""
    points: list[RendezvousPoint] = []
    coverage: SeriesCoverage | None = None


class FlagSlice(BaseModel):
    """One registry flag's share of the multi-pattern population.

    ⛔ `hulls` is the number that matters; `pct` is derived and must be read against
    `denominator`, which the response carries precisely because it is small."""
    flag: str = ""
    hulls: int = 0
    pct: float = 0.0
    attributed: bool = True


class FlagDistributionResponse(BaseModel):
    """⛔ REGISTRY FLAGS OF THE MULTI-PATTERN HULLS — with its denominator in the open.

    `attribution_field` names WHICH derivation produced these flags. The project has two that
    disagree by ~6 points (`vessel_identity.flag_country` vs `flag_iso` from the MMSI MID
    prefix); presenting one as the other would be a silent substitution."""
    available: bool = True
    question: str = ""
    denominator: int = 0
    denominator_note: str = ""
    attribution_field: str = ""
    attribution_note: str = ""
    attributed: int = 0
    unattributed: int = 0
    unit: str = ""
    source: str = ""
    method: str = ""
    caveat: str = ""
    slices: list[FlagSlice] = []
    coverage: SeriesCoverage | None = None


# ---------- DASH-BOARD-3: air country × time (ON DEMAND — no new storage) ----------

class CountryBucket(BaseModel):
    """Distinct AIRFRAMES seen over one country, in one bucket, from ONE named population.

    ⛔ `source` is part of the key, never folded away. opensky / adsbexchange / airplanes_live
    are different communities with different coverage (74.46 % · 41.19 % · 70.88 % measured),
    and a single summed line across them is the `/adsb` trap this project already recorded."""
    bucket: datetime
    country: str = ""
    source: str = ""
    airframes: int = 0


class AirCountrySeries(BaseModel):
    """⛔ AIR COUNTRY × TIME — the answer to owner question (أ), served ON DEMAND.

    CONSUMPTION LINE: read `coverage.series_start` and BOTH gap lists and break the line across
    every window. ⛔ Then read `coverage.gap_basis` — for AIR it is UNAVAILABLE, meaning an
    upstream air outage that did NOT coincide with a host shutdown is **not represented at
    all**; only `host_gaps` protect this series. Never sum across `source`. Never compare a
    RATIO across `coverage.ratio_cutoff`. Never add these values to a sea or space series:
    the unit is airframes, not events and not passes."""
    available: bool = True
    question: str = ""
    bucket: str = "day"
    unit: str = ""
    method: str = ""
    caveat: str = ""
    split_by: str = "country × source"
    split_note: str = ""
    populations: list[str] = []
    population_note: str = ""
    storage_note: str = ""
    window_hours: int = 0
    cost_note: str = ""
    totals: dict[str, int] = {}
    buckets: list[CountryBucket] = []
    coverage: SeriesCoverage | None = None


# ---------- DASH-BOARD-3b: the daily air summary (unit: HULL-DAYS when summed) ----------

class HullDayBucket(BaseModel):
    """One (day, country, source) row of the summary table.

    `distinct_hulls` is EXACT for that day alone. ⛔ Summing it across days gives HULL-DAYS."""
    day: datetime
    country: str = ""
    source: str = ""
    distinct_hulls: int = 0
    rows: int = 0


class AirCountryDaily(BaseModel):
    """⛔ THE LONG AIR TREND — AND ITS UNIT IS **HULL-DAYS**, NOT AIRFRAMES.

    ⛔ `totals` and every multi-day sum in this payload are HULL-DAYS: an airframe present on
    ten days contributes 10. They are NOT distinct airframes and must never be labelled as
    such, nor placed beside `/air/series/country`'s distinct counts without the difference
    being visible. Two units. Adding them is the /adsb trap in another costume.

    ⚠ Hull-days are not a degraded substitute — for "which countries lead over time" they are
    arguably the better measure, because they weight PRESENCE OVER TIME rather than mere
    appearance. Read as a choice, not a shortfall.

    CONSUMPTION LINE: read `coverage` first and break the line across every gap window. ⛔ Days
    the host was off are ABSENT from `buckets`, never zero — an absent day means "not
    observed", and filling it with 0 would assert "no aircraft flew". Never sum across
    `source`."""
    available: bool = True
    question: str = ""
    unit: str = ""
    unit_warning: str = ""
    source: str = ""
    method: str = ""
    caveat: str = ""
    table_note: str = ""
    days_present: int = 0
    days_absent_note: str = ""
    query_ms: float = 0.0
    totals: dict[str, int] = {}
    buckets: list[HullDayBucket] = []
    coverage: SeriesCoverage | None = None
