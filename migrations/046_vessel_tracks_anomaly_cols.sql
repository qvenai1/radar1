-- 046_vessel_tracks_anomaly_cols.sql
--
-- MT-DETECT-2 (follow-up) — the last two columns detect_gnss_anomaly.py reads.
--
-- Migration 045 added `anomaly_flags` and `inland_km` to mt_vessel_states but did not
-- expose them through `vessel_tracks`, so the detector could not be pointed at the view
-- without falling back to the per-source tables — the query fork the view exists to prevent.
--
-- ⚠ FOUND THE SAME WAY THE EARLIER GATE ERRORS WERE: by reading the detector's actual SQL
-- rather than reasoning about what it "needs". That is now three times in this workstream
-- that the code disagreed with the intent, and each time the code was right.
--
-- Forward-only. Reversal: restore migration 045's view definition.

DROP VIEW vessel_tracks;

CREATE VIEW vessel_tracks AS
    SELECT 'aisstream'::text  AS source,
           'mmsi'::text       AS key_kind,
           vs.mmsi            AS vessel_key,
           vs.ts, vs.latitude, vs.longitude, vs.speed, vs.course, vs.heading,
           vs.nav_status, vs.draught, vs.name, vi.flag_country AS flag,
           vs.country, vs.near_country,
           vs.distance_km, vs.position_quality, vs.implied_speed_kn, vs.ship_type,
           vs.anomaly_flags, vs.inland_km
      FROM vessel_states vs
      LEFT JOIN vessel_identity vi ON vi.mmsi = vs.mmsi
UNION ALL
    SELECT 'marinetraffic'::text,
           'ship_id'::text,
           mt.ship_id,
           mt.ts, mt.latitude, mt.longitude, mt.speed, mt.course, mt.heading,
           mt.nav_status, mt.draught, mt.name, mt.flag,
           mt.country, mt.near_country,
           mt.distance_km, mt.position_quality, mt.implied_speed_kn, mt.ship_type,
           mt.anomaly_flags, mt.inland_km
      FROM mt_vessel_states mt;

COMMENT ON VIEW vessel_tracks IS
    'MT-DETECT-1/2 — AIS and MarineTraffic tracks in one shape, tagged by source/key_kind. '
    'Fields a source does not carry are NULL (MT: nav_status, draught, mmsi, imo). NULL '
    'position_quality means UNASSESSED, never sound. A detector requiring an absent field '
    'must refuse that source — see backend/track_source.py.';
