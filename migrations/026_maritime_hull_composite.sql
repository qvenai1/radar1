-- 026_maritime_hull_composite.sql
--
-- Cross-pattern intelligence — the IMO-centric COMPOSITE view. AGGREGATION +
-- SURFACE over the existing behavioural events (A1 ais_gap, A2 rendezvous, A3
-- identity_change) in maritime_events. It creates NO new events and is NOT a
-- detector: it composes what the three detectors already wrote, stitched by the
-- M2b IMO spine so a hull's events survive its MMSI/flag/name changes.
--
-- TWO VIEWS (live off the current events — no staleness, no materialization: the
-- aggregation measured ~14 ms over ~1,285 hulls, so a view is amply fast):
--
--   * maritime_hull_events — the normalized attribution: one row per (hull,
--     event). The hull key is COALESCE(e.imo, spine.imo, e.mmsi) — the event's
--     own IMO, else the IMO the identity spine knows for that MMSI (so a re-flag
--     hull's events under BOTH its MMSIs resolve to the SAME IMO — the whole
--     point), else the MMSI when no IMO exists (IMO-null vessels aggregate by
--     MMSI and honestly cannot be stitched across a re-flag). A RENDEZVOUS is
--     attributed to BOTH participants (primary and partner), so a meeting shows
--     in both hulls' pictures. Only the SIGNAL tier is composed: ais_gap rows are
--     included ONLY when classification='candidate' (the confound-removed ones);
--     rendezvous and identity_change are candidate-only by construction.
--
--   * maritime_hull_composite — GROUP BY hull: per-pattern counts, the number of
--     DISTINCT pattern types (the composite signal), the event time span, and the
--     set of MMSIs the hull broadcast under. The composite_rank (which elevates
--     multi-pattern hulls + grey/sanctions flags) and the identity/timeline
--     enrichment are computed in the API layer (backend/composite.py), reusing
--     the flag lists so there is ONE source of truth (no flag list duplicated
--     into SQL).
--
-- READ-ONLY over maritime_events / vessel_identity — no table, column, key, or
-- data change; the three detectors are untouched. Reversal (forward-only
-- migrations): DROP VIEW maritime_hull_composite; DROP VIEW maritime_hull_events.

CREATE VIEW maritime_hull_events AS
WITH sig AS (
    SELECT e.event_type, e.mmsi, e.imo, e.start_ts, e.end_ts, e.duration_s, e.detail,
           COALESCE(e.imo, vi.imo, e.mmsi) AS hull
    FROM maritime_events e
    LEFT JOIN vessel_identity vi ON vi.mmsi = e.mmsi
    WHERE e.event_type IN ('rendezvous', 'identity_change')
       OR (e.event_type = 'ais_gap' AND e.classification = 'candidate')
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
       count(DISTINCT event_type)                             AS n_pattern_types,
       min(start_ts)                                          AS first_event_ts,
       max(start_ts)                                          AS last_event_ts,
       array_agg(DISTINCT via_mmsi)                           AS mmsis
FROM maritime_hull_events
GROUP BY hull;
