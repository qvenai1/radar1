-- 036_maritime_hull_composite_imo_integrity.sql
--
-- Maritime layer IMO-2 — the composite CONSUMES the IMO-integrity axis (migration
-- 035). A dated, checksum-verified replacement of the two composite views (029),
-- NOT a bare CREATE OR REPLACE outside the migration system (the 029 precedent).
--
-- TWO semantic changes, both in the sig CTE:
--
--  (1) MALFORMED IMOs must not serve as a hull key. A 'malformed' imo_quality means
--      the value is a decode artifact (0xFFFF, 0x0A000000, an 8-/10-digit over-read),
--      NOT a hull identity — so it must fall back to the MMSI key exactly as a NULL
--      IMO already does (IMO-pre Q1d: null-IMO hulls group under MMSI, nothing
--      dropped). Implemented as a join-side guard on the identity's verdict:
--          CASE WHEN vi.imo_quality = 'malformed' THEN e.mmsi
--               ELSE COALESCE(e.imo, vi.imo, e.mmsi) END
--      Only 'malformed' re-keys. 'checksum_fail' KEEPS its key (deliberate — a
--      well-formed 7-digit that fails the check digit is plausibly a real, mis-
--      decoded identity that still consistently identifies ONE hull; re-keying would
--      fragment that vessel's own evidence; it is ANNOTATED questionable-spine at the
--      API layer instead). 'reflag_clean' / shared IMOs are UNTOUCHED — the stitching
--      is intended (A3-detected re-flags); re-keying would fragment working
--      intelligence. This is the flag-not-delete discipline: annotate + fall back on a
--      non-identity, never delete or re-key what is legitimately stitched.
--
--      Live sizing (IMO-2 Step-0, on live data): 36 malformed identities, ALL
--      single-MMSI (0 shared across MMSIs => 0 false merges today), and 10 composite
--      hulls currently keyed on a malformed IMO. This edit re-keys those 10 to their
--      own MMSI — a structural guard: no false merge exists today, but a shared
--      generic artifact (e.g. two transmitters both emitting 65535) would otherwise
--      merge two unrelated vessels into one phantom hull. Nothing is dropped.
--
--  (2) The 'imo_integrity' event type joins the composite (one disjunct, the G4b
--      pattern). A genuine mmsi_collision (time-overlapping MMSIs claiming one IMO —
--      the false-attribution signature A3 structurally silences) is written to
--      maritime_events by the IMO-integrity runner as event_type='imo_integrity',
--      classification='candidate' (already in maritime_events_class_ck — NO new CHECK
--      value; there is no event_type CHECK at all). It is an ACTOR-side integrity
--      anomaly, so it belongs in the actor-centric composite as a genuine pattern
--      type; count(DISTINCT event_type) picks it up automatically. n_imo_integrity is
--      added for surface parity (cosmetic, like n_gnss_anomaly). ZERO instances today
--      — a LATENT guard folded in now so no third view migration is needed when the
--      live data first produces a collision.
--
-- Views only (read-only over maritime_events / vessel_identity): no table, column,
-- key, or data change; the detectors are untouched. Drop in dependency order
-- (composite depends on hull_events), recreate both. Reversal (forward-only): restore
-- the migration-029 definitions.

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
       OR (e.event_type = 'imo_integrity' AND e.classification = 'candidate')  -- IMO-2 (2): the latent collision
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
       count(DISTINCT event_type)                             AS n_pattern_types,
       min(start_ts)                                          AS first_event_ts,
       max(start_ts)                                          AS last_event_ts,
       array_agg(DISTINCT via_mmsi)                           AS mmsis
FROM maritime_hull_events
GROUP BY hull;
