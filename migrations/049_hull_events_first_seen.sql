-- 049_hull_events_first_seen.sql
--
-- DASH-3 — carry `first_seen_at` (migrations 047/048) up into `maritime_hull_events`, so the
-- alert layer can answer "have I seen this before?" without querying the base table again.
--
-- ⛔ THE VIEW IS RECREATED VERBATIM APART FROM THE ADDED COLUMN. Its logic is load-bearing and
-- subtle, and none of it is touched here:
--   * the hull key falls back to MMSI when `imo_quality = 'malformed'` (the IMO-2 hull-key
--     fallback) — unchanged in BOTH branches;
--   * the `candidate`-only filter per event type — unchanged;
--   * the UNION ALL that re-emits a rendezvous from the PARTNER's side with `as_partner=true`
--     — unchanged, and it is why one event can legitimately appear under two hulls.
--
-- ⚠ `first_seen_at` IS TAKEN FROM THE UNDERLYING EVENT IN BOTH BRANCHES, INCLUDING THE PARTNER
-- BRANCH — deliberately. The partner row is the SAME detected event seen from the other
-- vessel; it was first written at the same instant, and giving it its own clock would let one
-- event be "new" for one hull and "old" for the other.
--
-- ⚠ NULL PROPAGATES, AND THAT IS CORRECT. The pre-047 archive carries NULL and it stays NULL
-- through the view: unknown novelty, never "new" (see 047's column comment).
--
-- ⛔ TWO VIEWS ARE REBUILT, NOT ONE — and the first attempt at this migration found that out
-- by failing: `maritime_hull_composite` SELECTs from `maritime_hull_events`, so the DROP was
-- refused ("cannot drop view ... because other objects depend on it"). The transaction rolled
-- back with nothing changed, which is the runner behaving exactly as designed.
-- ⚠ CASCADE was the other option and is REJECTED: it would drop the dependent view silently
-- and leave whatever else may come to depend on it broken with no error. Both definitions are
-- restated here in full, so the dependency is visible in the file rather than in a hint.
--
-- ⛔ THE COMPOSITE GAINS ITS OWN AGGREGATE: `min(first_seen_at)` — the EARLIEST first-sighting
-- among the hull's events, i.e. "when did this hull first become a composite lead?".
-- ⚠ `min()` IGNORES NULLS, which is the wrong default here and is corrected by the count
-- beside it: a hull whose events are all pre-047 yields NULL (honest), but a hull with ONE new
-- event among ten archived ones would otherwise report that one date as if the whole picture
-- were that recent. `n_events_first_seen` lets a consumer see how much of the hull is actually
-- covered before trusting the date.
--
-- Forward-only; view definitions only — no table, index, constraint or row is touched.
-- Reversal: restore the definitions captured in this file's git history.

DROP VIEW maritime_hull_composite;
DROP VIEW maritime_hull_events;

CREATE VIEW maritime_hull_events AS
 WITH sig AS (
         SELECT e.event_type, e.mmsi, e.imo, e.start_ts, e.end_ts, e.duration_s, e.detail,
                e.first_seen_at,
                CASE WHEN vi.imo_quality = 'malformed'::text THEN e.mmsi
                     ELSE COALESCE(e.imo, vi.imo, e.mmsi) END AS hull
           FROM maritime_events e
           LEFT JOIN vessel_identity vi ON vi.mmsi = e.mmsi
          WHERE e.source = 'aisstream'::text
            AND ((e.event_type = ANY (ARRAY['rendezvous'::text, 'identity_change'::text]))
                 OR e.event_type = 'ais_gap'::text       AND e.classification = 'candidate'::text
                 OR e.event_type = 'gnss_anomaly'::text  AND e.classification = 'candidate'::text
                 OR e.event_type = 'imo_integrity'::text AND e.classification = 'candidate'::text
                 OR e.event_type = 'zone_dwell'::text    AND e.classification = 'candidate'::text)
        )
 SELECT sig.hull, sig.event_type, sig.mmsi AS via_mmsi, sig.imo,
        sig.start_ts, sig.end_ts, sig.duration_s, sig.detail, sig.first_seen_at,
        false AS as_partner
   FROM sig
UNION ALL
 SELECT CASE WHEN vq.imo_quality = 'malformed'::text THEN s.detail ->> 'partner_mmsi'::text
             ELSE COALESCE(vq.imo, s.detail ->> 'partner_mmsi'::text) END AS hull,
        'rendezvous'::text AS event_type,
        s.detail ->> 'partner_mmsi'::text AS via_mmsi, vq.imo,
        s.start_ts, s.end_ts, s.duration_s, s.detail, s.first_seen_at,
        true AS as_partner
   FROM sig s
   LEFT JOIN vessel_identity vq ON vq.mmsi = (s.detail ->> 'partner_mmsi'::text)
  WHERE s.event_type = 'rendezvous'::text AND (s.detail ->> 'partner_mmsi'::text) IS NOT NULL;

CREATE VIEW maritime_hull_composite AS
 SELECT hull,
    count(*) FILTER (WHERE event_type = 'ais_gap'::text)         AS n_gaps,
    count(*) FILTER (WHERE event_type = 'rendezvous'::text)      AS n_rendezvous,
    count(*) FILTER (WHERE event_type = 'identity_change'::text) AS n_identity_changes,
    count(*) FILTER (WHERE event_type = 'gnss_anomaly'::text)    AS n_gnss_anomaly,
    count(*) FILTER (WHERE event_type = 'imo_integrity'::text)   AS n_imo_integrity,
    count(*) FILTER (WHERE event_type = 'zone_dwell'::text)      AS n_zone_dwell,
    count(DISTINCT event_type)                                   AS n_pattern_types,
    min(start_ts)                                                AS first_event_ts,
    max(start_ts)                                                AS last_event_ts,
    -- DASH-3 additions (see the header note on min() and NULLs)
    min(first_seen_at)                                           AS first_seen_at,
    count(first_seen_at)                                         AS n_events_first_seen,
    count(*)                                                     AS n_events,
    array_agg(DISTINCT via_mmsi)                                 AS mmsis
   FROM maritime_hull_events
  GROUP BY hull;
