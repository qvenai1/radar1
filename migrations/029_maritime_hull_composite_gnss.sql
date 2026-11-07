-- 029_maritime_hull_composite_gnss.sql
--
-- Maritime layer G4b — admit the GNSS-anomaly INDIVIDUAL tier into the cross-pattern
-- composite. A dated, checksum-verified replacement of the two composite views
-- (migration 026), NOT a bare CREATE OR REPLACE outside the migration system.
--
-- The single semantic change is one disjunct in the sig CTE:
--   OR (e.event_type = 'gnss_anomaly' AND e.classification = 'candidate')
-- mirroring the existing ais_gap ... candidate clause. It admits ONLY the individual
-- tier (G4a classification='candidate'); 'area-jamming' (environmental victim) and
-- 'glitch' (bad transmitter) are excluded FOR FREE by the same ='candidate' condition
-- that already excludes A1's outage-excluded/edge/stationary. A jamming victim and a
-- faulty transmitter never enter the actor-centric composite.
--
-- n_pattern_types = count(DISTINCT event_type) then picks up 'gnss_anomaly' as a
-- genuine FOURTH pattern type with no further change — a hull that went dark AND was
-- spoofed/jammed now shows two pattern types. n_gnss_anomaly is added to
-- maritime_hull_composite for surface parity with n_gaps/n_rendezvous/
-- n_identity_changes; it is COSMETIC (n_pattern_types drives the rank), but keeps the
-- per-type count columns complete.
--
-- Views only (read-only over maritime_events / vessel_identity): no table, column,
-- key, or data change; the four detectors are untouched. Drop in dependency order
-- (composite depends on hull_events), recreate both. Reversal (forward-only): restore
-- the migration-026 definitions.

DROP VIEW maritime_hull_composite;
DROP VIEW maritime_hull_events;

CREATE VIEW maritime_hull_events AS
WITH sig AS (
    SELECT e.event_type, e.mmsi, e.imo, e.start_ts, e.end_ts, e.duration_s, e.detail,
           COALESCE(e.imo, vi.imo, e.mmsi) AS hull
    FROM maritime_events e
    LEFT JOIN vessel_identity vi ON vi.mmsi = e.mmsi
    WHERE e.event_type IN ('rendezvous', 'identity_change')
       OR (e.event_type = 'ais_gap'      AND e.classification = 'candidate')
       OR (e.event_type = 'gnss_anomaly' AND e.classification = 'candidate')  -- G4b: individual tier only
)
SELECT hull, event_type, mmsi AS via_mmsi, imo,
       start_ts, end_ts, duration_s, detail, false AS as_partner
FROM sig
UNION ALL
SELECT COALESCE(vq.imo, s.detail ->> 'partner_mmsi') AS hull,
       'rendezvous'::text, (s.detail ->> 'partner_mmsi'), vq.imo,
       s.start_ts, s.end_ts, s.duration_s, s.detail, true AS as_partner
FROM sig s
LEFT JOIN vessel_identity vq ON vq.mmsi = s.detail ->> 'partner_mmsi'
WHERE s.event_type = 'rendezvous' AND s.detail ->> 'partner_mmsi' IS NOT NULL;

CREATE VIEW maritime_hull_composite AS
SELECT hull,
       count(*) FILTER (WHERE event_type = 'ais_gap')         AS n_gaps,
       count(*) FILTER (WHERE event_type = 'rendezvous')      AS n_rendezvous,
       count(*) FILTER (WHERE event_type = 'identity_change') AS n_identity_changes,
       count(*) FILTER (WHERE event_type = 'gnss_anomaly')    AS n_gnss_anomaly,
       count(DISTINCT event_type)                             AS n_pattern_types,
       min(start_ts)                                          AS first_event_ts,
       max(start_ts)                                          AS last_event_ts,
       array_agg(DISTINCT via_mmsi)                           AS mmsis
FROM maritime_hull_events
GROUP BY hull;
