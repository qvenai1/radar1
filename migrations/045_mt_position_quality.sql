-- 045_mt_position_quality.sql
--
-- MT-DETECT-2 — the quality columns the MarineTraffic track needs to be analysable.
--
-- MT-DETECT-1 admitted only ONE detector (rendezvous) to the MarineTraffic track. Two more
-- were refused, and reading their SQL showed both were blocked on the same two fields:
--
--     detect_zone_dwell.py     implied_speed_kn, position_quality, ship_type
--     detect_gnss_anomaly.py   implied_speed_kn, position_quality
--
-- ⛔ NEITHER IS A BROADCAST FIELD. Both are written by the quality tagger
-- (scripts/backfill_position_quality.py), which had only ever run over `vessel_states`. So
-- those detectors were never blocked by MarineTraffic's data at all — they were blocked by
-- a PREPROCESSING STAGE that did not exist for it. This migration gives that stage
-- somewhere to write.
--
-- ✅ AND THE STAGE'S LOGIC TRANSFERS INTACT, which is why this is safe. `classify_fix`
-- (backend/gnss_anomaly.py) takes only prev/cur/nxt timestamps and positions, plus an
-- optional ship_type and an inland depth the runner computes from geography. It reads no
-- nav_status, no speed, no MMSI — it is pure kinematics and geography, and MT carries
-- everything it needs (ts, lat/lon 100 %, ship_type).
--
-- ⚠ BUT THE VERDICTS ARE MORE CONSERVATIVE ON MT, AND THAT ASYMMETRY MUST BE UNDERSTOOD
-- RATHER THAN AVERAGED AWAY. MT's median inter-fix gap is 2.2 minutes against AIS's
-- seconds, with a measured maximum of 1,403 minutes:
--
--   on_land          UNAFFECTED — a position inside a landmass is implausible whatever the
--                    sampling rate. A geometric fact about one fix.
--   impossible_speed UNDER-DETECTS — implied speed is distance/elapsed, so a sparse pair
--                    yields a LOWER speed. Sparse sampling hides fast excursions; it does
--                    not invent them.
--   teleport_return  UNDER-DETECTS — the pattern needs a fix on each side of the excursion,
--                    and a 2.2-minute cadence will miss short ones.
--
-- So an MT fix marked implausible is at least as trustworthy as an AIS one; an MT fix NOT
-- marked implausible is weaker evidence of soundness than the same verdict on AIS. That is
-- the honest direction to fail in, and the opposite of the ais_gap case (MT-DETECT-1),
-- which would have produced positively FALSE claims rather than merely fewer true ones.
--
-- Forward-only. Reversal: drop the columns and restore migration 044's view.

ALTER TABLE mt_vessel_states ADD COLUMN position_quality     TEXT;
ALTER TABLE mt_vessel_states ADD COLUMN anomaly_flags        TEXT[];
ALTER TABLE mt_vessel_states ADD COLUMN implied_speed_kn     DOUBLE PRECISION;
ALTER TABLE mt_vessel_states ADD COLUMN inland_km            DOUBLE PRECISION;
ALTER TABLE mt_vessel_states ADD COLUMN quality_method       TEXT;
ALTER TABLE mt_vessel_states ADD COLUMN quality_computed_at  TIMESTAMPTZ;

COMMENT ON COLUMN mt_vessel_states.position_quality IS
    'MT-DETECT-2 — per-fix plausibility from the SAME classifier as vessel_states '
    '(backend/gnss_anomaly.classify_fix: pure kinematics + geography). ⚠ More conservative '
    'here: MT sampling is ~2.2 min median, so impossible_speed and teleport_return '
    'UNDER-detect. on_land is unaffected. NULL still means UNASSESSED.';

-- The detectors filter on it; a partial index keeps that predicate cheap without paying for
-- the overwhelming majority of rows that are plausible.
CREATE INDEX mt_vessel_states_quality_idx ON mt_vessel_states (position_quality)
    WHERE position_quality IS NOT NULL;

-- ---------------------------------------------------------------------------
-- vessel_tracks now carries the real columns for BOTH sources
-- ---------------------------------------------------------------------------
-- Migration 044 selected `NULL::text AS position_quality` for the MT branch because the
-- column did not exist. It does now, so the view stops asserting an absence that is no
-- longer true — and `implied_speed_kn` joins it, which is what unblocks the two detectors.
DROP VIEW vessel_tracks;

CREATE VIEW vessel_tracks AS
    SELECT 'aisstream'::text  AS source,
           'mmsi'::text       AS key_kind,
           vs.mmsi            AS vessel_key,
           vs.ts, vs.latitude, vs.longitude, vs.speed, vs.course, vs.heading,
           vs.nav_status, vs.draught, vs.name, vi.flag_country AS flag,
           vs.country, vs.near_country,
           vs.distance_km, vs.position_quality, vs.implied_speed_kn, vs.ship_type
      FROM vessel_states vs
      LEFT JOIN vessel_identity vi ON vi.mmsi = vs.mmsi
UNION ALL
    SELECT 'marinetraffic'::text,
           'ship_id'::text,
           mt.ship_id,
           mt.ts, mt.latitude, mt.longitude, mt.speed, mt.course, mt.heading,
           mt.nav_status, mt.draught, mt.name, mt.flag,
           mt.country, mt.near_country,
           mt.distance_km, mt.position_quality, mt.implied_speed_kn, mt.ship_type
      FROM mt_vessel_states mt;

COMMENT ON VIEW vessel_tracks IS
    'MT-DETECT-1/2 — AIS and MarineTraffic tracks in one shape, tagged by source/key_kind. '
    'Fields a source does not carry are NULL (MT: nav_status, draught, mmsi, imo). NULL '
    'position_quality means UNASSESSED, never sound. A detector requiring an absent field '
    'must refuse that source — see backend/track_source.py.';
