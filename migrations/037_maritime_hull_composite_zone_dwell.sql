-- 037_maritime_hull_composite_zone_dwell.sql
--
-- Maritime layer ZONES-3 — admit the STRONG subset of zone dwell into the cross-
-- pattern composite. A dated, checksum-verified replacement of the two composite views
-- (migration 036), NOT a bare CREATE OR REPLACE outside the migration system (the
-- 029/036 precedent).
--
-- ONE semantic change, the G4b/IMO-2 pattern: one disjunct in the sig CTE
--   OR (e.event_type = 'zone_dwell' AND e.classification = 'candidate')
-- mirroring the existing ais_gap/gnss_anomaly/imo_integrity ...='candidate' clauses. It
-- admits ONLY what the ZONES-3 runner (scripts/detect_zone_dwell.py) writes as
-- classification='candidate': the STRONG subset —
--   * safety_zone_intrusion (a low-speed dwell inside a platform's 500 m UNCLOS
--     Art. 60(5) safety zone; rare by construction, on a SURVEYED point), and
--   * baseline-UNFAMILIAR standoff_dwell, admitted only where the per-zone familiarity
--     baseline is data-sound (backend.zone_baseline) — under-determined today, so the
--     runner admits NONE of standoff and it stays annotation-only.
-- field_dwell (assumed-radius centroids, the weakest class) and every gated/demoted/
-- excluded session are NEVER written, so they cannot enter here — the ='candidate'
-- condition excludes them for free, exactly as it excludes A1's outage-excluded/edge/
-- stationary and A2's non-candidate confounds.
--
-- classification='candidate' is already in maritime_events_class_ck (NO new CHECK value;
-- there is no event_type CHECK at all). n_pattern_types = count(DISTINCT event_type)
-- picks up 'zone_dwell' as a genuine FIFTH pattern type with no further change — a hull
-- that dwelt at infrastructure AND went dark / met another / changed identity now shows
-- an extra pattern type. n_zone_dwell is added for surface parity with n_gaps/
-- n_rendezvous/n_identity_changes/n_gnss_anomaly/n_imo_integrity (COSMETIC — n_pattern_
-- types drives the rank).
--
-- Views only (read-only over maritime_events / vessel_identity): no table, column, key,
-- or data change; the detectors are untouched. Drop in dependency order (composite
-- depends on hull_events), recreate both. Reversal (forward-only): restore the
-- migration-036 definitions.

DROP VIEW maritime_hull_composite;
DROP VIEW maritime_hull_events;

CREATE VIEW maritime_hull_events AS
WITH sig AS (
    SELECT e.event_type, e.mmsi, e.imo, e.start_ts, e.end_ts, e.duration_s, e.detail,
           -- IMO-2 (1): a malformed spine is not a hull key -> fall back to the MMSI.
           CASE WHEN vi.imo_quality = 'malformed' THEN e.mmsi
                ELSE COALESCE(e.imo, vi.imo, e.mmsi) END AS hull
    FROM maritime_events e
    LEFT JOIN vessel_identity vi ON vi.mmsi = e.mmsi
    WHERE e.event_type IN ('rendezvous', 'identity_change')
       OR (e.event_type = 'ais_gap'       AND e.classification = 'candidate')
       OR (e.event_type = 'gnss_anomaly'  AND e.classification = 'candidate')  -- G4b
       OR (e.event_type = 'imo_integrity' AND e.classification = 'candidate')  -- IMO-2 (2)
       OR (e.event_type = 'zone_dwell'    AND e.classification = 'candidate')  -- ZONES-3: strong subset only
)
SELECT hull, event_type, mmsi AS via_mmsi, imo,
       start_ts, end_ts, duration_s, detail, false AS as_partner
FROM sig
UNION ALL
SELECT CASE WHEN vq.imo_quality = 'malformed' THEN s.detail ->> 'partner_mmsi'
            ELSE COALESCE(vq.imo, s.detail ->> 'partner_mmsi') END AS hull,
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
       count(*) FILTER (WHERE event_type = 'imo_integrity')   AS n_imo_integrity,
       count(*) FILTER (WHERE event_type = 'zone_dwell')      AS n_zone_dwell,
       count(DISTINCT event_type)                             AS n_pattern_types,
       min(start_ts)                                          AS first_event_ts,
       max(start_ts)                                          AS last_event_ts,
       array_agg(DISTINCT via_mmsi)                           AS mmsis
FROM maritime_hull_events
GROUP BY hull;
