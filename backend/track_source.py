"""MT-DETECT-1 — which detector may read which track source, and why. PURE.

The detectors were written against ONE source: a direct AIS feed, keyed by MMSI. Migration
043 adds a second (MarineTraffic, keyed by `ship_id`) and a `vessel_tracks` view that
presents both in one shape. This module is the gate between them, and it exists because
"the query runs" and "the answer means what the label says" are different questions.

⛔ A DETECTOR CAN BE BLOCKED FOR TWO VERY DIFFERENT REASONS, AND ONLY ONE OF THEM IS ABOUT
DATA. A missing column is a data fact and shows up as an empty result. A changed MEANING
does not: the query succeeds, rows come back, and every one of them asserts something the
source cannot support. The second kind is why this file is a hand-written table with
reasons rather than a `NOT NULL` check — see AIS_GAP below, which has both problems and
would be fatal for the second even if the first were fixed.

MEASURED FIELD AVAILABILITY (this DB, 2026-08-15, over 24 h — 398,392 MT fixes / 3,612
vessels, against the last full AIS day before the feed stopped):

    field         AIS      MT      needed by
    latitude      100 %    100 %   every detector
    speed          99 %     96 %   rendezvous · zone dwell · gap
    nav_status    100 %      0 %   gap (underway test)
    draught        48 %      0 %   draught delta
    name          --        99 %   identity change
    flag          --       100 %   identity change
    mmsi          100 %      0 %   every AIS identity join
    imo           --         0 %   hull stitching
"""

from __future__ import annotations

from dataclasses import dataclass

AIS, MT = "aisstream", "marinetraffic"
SOURCES = (AIS, MT)

#: Fields each source actually carries, measured (see the module header). A field absent
#: here is NULL in `vessel_tracks` for that source.
#: ⚠ `implied_speed_kn` and `position_quality` are NOT raw feed fields — the quality tagger
#: (scripts/backfill_position_quality.py) writes them. MT-DETECT-2 taught it `--track
#: marinetraffic`, so both sources now carry them.
#:
#: ⛔ A PREDICTION I RECORDED AND THE MEASUREMENT OVERTURNED — kept beside the correction,
#: because a superseded figure is itself a finding. Migration 045 argued that MT's ~2.2 min
#: median cadence would make `impossible_speed` UNDER-detect, since implied speed is
#: distance/elapsed and a sparse pair yields a lower number. Measured after tagging 24 h of
#: each source:
#:
#:                    assessed    implausible          suspect
#:     AIS             185,158    1,722  (0.93 %)           47
#:     MarineTraffic   259,384    4,850  (1.87 %)        5,024
#:
#: MT flags anomalies at ~2x the AIS rate, and `suspect` at ~107x. The reasoning was sound
#: about SAMPLING and wrong about the SOURCE: sparse sampling does hide a vessel's own fast
#: excursions, but that effect is swamped by kinematic inconsistency in MT's aggregated
#: positions themselves (its flag mix is impossible_speed 7,081 / on_land 2,796 /
#: teleport_return 169). The practical consequence is the opposite of what was written: a
#: plausibility filter EXCLUDES about twice as much MT data as AIS data, so MT-derived
#: detections are scarcer than the raw row counts suggest — not more plentiful.
FIELDS: dict[str, frozenset[str]] = {
    # ⚠ `imo` belongs here: vessel_states carries it (detect_identity_change's IMO_MMSI_SQL
    # groups by it). Omitting it refused identity_change on its OWN source — caught by
    # test_every_detector_may_always_read_the_original_source, which exists precisely
    # because a field table is easy to under-fill while looking complete.
    AIS: frozenset({"latitude", "longitude", "speed", "course", "heading", "nav_status",
                    "draught", "name", "flag", "country", "near_country", "mmsi", "imo",
                    "ship_type", "implied_speed_kn", "position_quality"}),
    MT: frozenset({"latitude", "longitude", "speed", "course", "heading",
                   "name", "flag", "country", "near_country", "ship_id", "ship_type",
                   # MT-DETECT-2 (migration 045): the quality tagger now runs over
                   # mt_vessel_states, so these are real columns here too.
                   "implied_speed_kn", "position_quality"}),
}


@dataclass(frozen=True, slots=True)
class Gate:
    """One detector's admission rule."""
    detector: str
    requires: frozenset[str]
    #: source -> why it is refused DESPITE having the fields. Empty for a purely
    #: field-driven gate.
    semantic_refusals: dict[str, str]


#: ⛔ THE ONE THAT MATTERS MOST. The gap detector must NEVER run on MarineTraffic, and the
#: reason is not the missing `nav_status` — it is that "a gap" means something else entirely.
#:
#: A1's whole contract is confound removal against a DIRECT RECEIVER FEED (see
#: backend/gap_detect.py): `outage-excluded` means the collector was down, `edge` means the
#: vessel left dense receiver coverage per the data-driven CoverageMask, and `candidate`
#: means "an underway vessel in good coverage stopped reporting". Every one of those is a
#: statement about OUR receivers.
#:
#: MarineTraffic is a SCRAPED WEBSITE. A hole in that series means our scraper did not
#: observe the vessel — which folds together MT's own aggregation coverage, MT's rate
#: limiting, and our polling schedule. Measured on 24 h of MT data: median inter-fix gap
#: 2.2 min, p90 9.3 min, and a maximum of 1,403 MINUTES (23.4 h) for one ship_id. Fed to A1,
#: that single vessel would emit a 23-hour "went dark" candidate. The vessel may well have
#: been transmitting the entire time.
#:
#: So this is refused on MEANING. Restoring nav_status would not unblock it; only a
#: scraper-observation model — which does not exist — could.
GATES: dict[str, Gate] = {
    "ais_gap": Gate(
        "ais_gap", frozenset({"latitude", "longitude", "speed", "nav_status"}),
        {MT: "a gap in a SCRAPED series means our scraper did not observe the vessel, not "
             "that the vessel stopped transmitting. A1's confounds (feed outage, receiver "
             "coverage) are facts about our own AIS receivers and have no MT equivalent. "
             "Measured max inter-fix gap 1,403 min — a 23-hour false 'went dark'."}),

    # Two vessels close and slow for long enough. The claim is about GEOMETRY, and geometry
    # survives the source change intact: MT carries position and speed for 96 % of rows.
    # ⚠ What does NOT survive is the partner's identity — an MT partner is a ship_id with no
    # MMSI, so the event records it as such and cannot be joined to the AIS identity layer.
    "rendezvous": Gate(
        "rendezvous", frozenset({"latitude", "longitude", "speed"}), {}),

    # ⛔ THESE TWO WERE DECLARED WRONG AT FIRST, AND THE CORRECTION IS THE POINT. Their
    # `requires` sets were written from what the detectors CONCEPTUALLY need — "a position
    # and a speed" — and the gate then admitted both on MarineTraffic. Reading their actual
    # SQL showed otherwise:
    #
    #     detect_zone_dwell.py     reads implied_speed_kn, position_quality, ship_type
    #     detect_gnss_anomaly.py   reads implied_speed_kn, position_quality
    #
    # ⚠ AND BOTH MISSING FIELDS COME FROM THE SAME PLACE: the quality tagger
    # (scripts/backfill_position_quality.py), which computes `implied_speed_kn` from
    # consecutive fixes and writes `position_quality` — and which has only ever run over
    # `vessel_states`. So these detectors do not depend on a raw feed at all; they depend on
    # a PREPROCESSING STAGE that does not exist for MT.
    #
    # That is a real, fixable gap rather than a semantic impossibility — running the tagger
    # over mt_vessel_states would unblock both — but it is not done, so the gate refuses
    # them and says why. A gate written from intent instead of from the code is how a
    # detector gets admitted to a source it cannot actually read.
    "zone_dwell": Gate(
        "zone_dwell",
        frozenset({"latitude", "longitude", "speed", "implied_speed_kn",
                   "position_quality", "ship_type"}), {}),

    "gnss_anomaly": Gate(
        "gnss_anomaly",
        frozenset({"latitude", "longitude", "implied_speed_kn", "position_quality"}), {}),

    # ⛔ ALSO DECLARED WRONG AT FIRST, and for the same reason: `frozenset({"name", "flag"})`
    # described the detector I pictured, not the one in scripts/detect_identity_change.py.
    #
    # Its PRIMARY query is IMO_MMSI_SQL — group by (imo, mmsi) — and the signal it exists to
    # find is a RE-FLAG: one hull (stable IMO) appearing under a NEW MMSI. That is anchored
    # entirely on the IMO, and MT carries none (measured 0 of 3,612 vessels).
    #
    # ⚠ The name/type variation path WOULD run on MT, and admitting the detector on that
    # basis is precisely the trap: it would keep its name and its event_type while silently
    # losing the claim it is named for, so a "identity_change" event from MT would mean
    # something materially weaker than the same event from AIS. A name-only variant is a
    # DIFFERENT detector and should be built and labelled as one, not smuggled in under
    # this gate.
    "identity_change": Gate(
        "identity_change", frozenset({"name", "imo"}), {}),

    # ⛔ Draught delta is refused on DATA: `draught` is NULL in 100 % of MT rows (measured
    # over 24 h). There is nothing to difference.
    "draught_delta": Gate(
        "draught_delta", frozenset({"draught"}), {}),
}


def allowed(detector: str, source: str) -> bool:
    """May this detector read this source at all?"""
    g = GATES.get(detector)
    if g is None:
        # ⛔ FAIL CLOSED. An unregistered detector gets the ORIGINAL source only. A new
        # detector must opt in deliberately after someone has thought about whether its
        # claim survives the source change — never by defaulting into it.
        return source == AIS
    if source in g.semantic_refusals:
        return False
    return g.requires <= FIELDS.get(source, frozenset())


def sources_for(detector: str) -> list[str]:
    """Every source this detector may honestly read, in a stable order."""
    return [s for s in SOURCES if allowed(detector, s)]


def refusal(detector: str, source: str) -> str | None:
    """Why this detector may not read this source — for logs and for the operator.

    Returns None when it is allowed. Distinguishes the two causes, because they call for
    different responses: a MISSING FIELD might be fixable upstream, while a CHANGED MEANING
    is a reason the detector should never run there at all."""
    g = GATES.get(detector)
    if g is None:
        return (None if source == AIS else
                f"detector {detector!r} is not registered in track_source.GATES — it reads "
                f"the AIS track only until someone decides its claim survives {source}")
    if source in g.semantic_refusals:
        return g.semantic_refusals[source]
    missing = g.requires - FIELDS.get(source, frozenset())
    if missing:
        return (f"{source} does not carry {sorted(missing)} — measured, see "
                f"backend/track_source.py")
    return None
