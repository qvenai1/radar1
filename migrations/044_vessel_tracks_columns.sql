-- 044_vessel_tracks_columns.sql
--
-- MT-DETECT-1 (follow-up) — two columns the detectors need that 043's view omitted.
--
-- `distance_km` (distance to coast) is what the rendezvous detector's OFFSHORE_MIN_KM gate
-- reads, and `position_quality` is what PLAUSIBLE_FILTER reads. Without them a detector
-- pointed at `vessel_tracks` would have to fall back to the per-source tables, which is
-- exactly the query fork the view exists to prevent.
--
-- ⚠ MEASURED: mt_vessel_states carries `distance_km` for 100 % of rows (it is geo-stamped
-- on ingest by the same classifier), and has NO `position_quality` column at all — the
-- GNSS quality tagger runs over vessel_states only. So the MT branch selects a NULL literal
-- for it, and that NULL is LOAD-BEARING:
--
--     PLAUSIBLE_FILTER = "position_quality IS DISTINCT FROM 'implausible'"
--     NULL IS DISTINCT FROM 'implausible'  ->  TRUE
--
-- i.e. MT rows PASS the plausibility filter because they were never assessed, not because
-- they were assessed and found sound. That is the correct behaviour — refusing unassessed
-- rows would silently drop the entire source — but it means an MT-derived event carries no
-- plausibility guarantee, and the detector must say so in `method` rather than let the
-- filter imply one.
--
-- Forward-only. Reversal: restore 043's view definition.

DROP VIEW vessel_tracks;

CREATE VIEW vessel_tracks AS
    SELECT 'aisstream'::text  AS source,
           'mmsi'::text       AS key_kind,
           vs.mmsi            AS vessel_key,
           vs.ts, vs.latitude, vs.longitude, vs.speed, vs.course, vs.heading,
           vs.nav_status, vs.draught, vs.name, vi.flag_country AS flag,
           vs.country, vs.near_country,
           vs.distance_km, vs.position_quality
      FROM vessel_states vs
      LEFT JOIN vessel_identity vi ON vi.mmsi = vs.mmsi
UNION ALL
    SELECT 'marinetraffic'::text,
           'ship_id'::text,
           mt.ship_id,
           mt.ts, mt.latitude, mt.longitude, mt.speed, mt.course, mt.heading,
           mt.nav_status, mt.draught, mt.name, mt.flag,
           mt.country, mt.near_country,
           mt.distance_km,
           -- ⛔ NOT a column on mt_vessel_states: the quality tagger only ever ran over the
           -- AIS table. NULL here means UNASSESSED, never "sound" — see the header.
           NULL::text AS position_quality
      FROM mt_vessel_states mt;

COMMENT ON VIEW vessel_tracks IS
    'MT-DETECT-1 — AIS and MarineTraffic tracks in one shape, tagged by source/key_kind. '
    'Fields a source does not carry are NULL (MT: nav_status, draught, mmsi, '
    'position_quality). NULL position_quality means UNASSESSED, not sound. A detector '
    'requiring an absent field must refuse that source — see backend/track_source.py.';
