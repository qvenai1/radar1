-- 043_events_ship_id.sql
--
-- MT-DETECT-1 — the detectors gain a SECOND vessel identifier: MarineTraffic's `ship_id`.
--
-- WHY NOW. AISStream has delivered nothing since 2026-08-05 (measured: `vessel_states`
-- max(ts) frozen there, 0 fixes in 48 h), while the MarineTraffic scraper is live and
-- carrying the region: 398,392 fixes in 24 h across 3,612 distinct vessels, newest ~200 s
-- old. Every detector reads `vessel_states` keyed by MMSI, so all of that is invisible to
-- the analysis layer.
--
-- ⛔ AND MT CANNOT BE JOINED TO AIS. Measured over 24 h of MT rows:
--       mmsi      NULL in 20,161 / 20,161      (100 %)
--       imo       NULL for all 3,612 vessels
--       callsign  NULL for all 3,612 vessels
--       name      present for 3,611 of 3,612
-- The ONLY shared field is the vessel NAME, which is user-entered, repeats across hulls and
-- changes on re-registration — not an identity key. So MT tracks are a SEPARATE identifier
-- space, and this migration models them as one rather than pretending they are MMSIs.
--
-- ⛔ WHAT THIS MIGRATION DELIBERATELY DOES NOT DO: admit MT events into the hull composite.
-- `maritime_hull_events` derives its hull as COALESCE(imo, vessel_identity.imo, mmsi) — all
-- three NULL for an MT row, so every MT event would collapse into ONE bogus `hull = NULL`
-- group and be counted as a single multi-pattern vessel. The composite's premise is a
-- STITCHED identity; ship_id cannot stitch to it. The view is therefore recreated below
-- with an explicit `e.source = 'aisstream'` guard — a guard, not a reliance on NULL
-- behaviour, because the failure it prevents is silent and corrupting.
--
-- Forward-only. Reversal: restore the 037 view definitions and drop the added columns.

-- ---------------------------------------------------------------------------
-- 1. maritime_events grows a second identifier space
-- ---------------------------------------------------------------------------
ALTER TABLE maritime_events ADD COLUMN ship_id TEXT;

-- Which track the event was derived from. DEFAULT keeps every existing row correct without
-- a backfill: everything recorded before this migration came from the AIS path.
ALTER TABLE maritime_events ADD COLUMN source TEXT NOT NULL DEFAULT 'aisstream';
ALTER TABLE maritime_events ADD CONSTRAINT maritime_events_source_ck
    CHECK (source IN ('aisstream', 'marinetraffic'));

-- ⚠ ORDER MATTERS AND POSTGRES ENFORCES IT: `mmsi` cannot lose NOT NULL while it is part of
-- the primary key ("column mmsi is in a primary key"). So the old key comes off first, and
-- the new one goes on after the generated column exists.
ALTER TABLE maritime_events DROP CONSTRAINT maritime_events_pkey;

-- mmsi stops being mandatory — but EXACTLY ONE key must be present, so a row can never be
-- keyless (unaddressable) or double-keyed (ambiguous about which space it lives in).
ALTER TABLE maritime_events ALTER COLUMN mmsi DROP NOT NULL;
ALTER TABLE maritime_events ADD CONSTRAINT maritime_events_one_key_ck
    CHECK (num_nonnulls(mmsi, ship_id) = 1);

-- ⚠ A GENERATED column, not a column the writers must remember to fill. The identifier a
-- row is addressed by is a FUNCTION of the two key columns, so deriving it here means the
-- primary key below cannot disagree with the keys it is built from — and no detector can
-- forget to populate it.
ALTER TABLE maritime_events
    ADD COLUMN vessel_key TEXT GENERATED ALWAYS AS (COALESCE(mmsi, ship_id)) STORED;

-- The primary key moves from (event_type, mmsi, start_ts) to include the source and the
-- generalised key. `source` is IN the key on purpose: an MT ship_id and an AIS MMSI are
-- different namespaces and could collide numerically, and two sources observing the same
-- physical encounter are two independent observations, not one row to merge.
ALTER TABLE maritime_events
    ADD CONSTRAINT maritime_events_pkey PRIMARY KEY (event_type, source, vessel_key, start_ts);

CREATE INDEX maritime_events_ship_id_idx ON maritime_events (ship_id) WHERE ship_id IS NOT NULL;

COMMENT ON COLUMN maritime_events.ship_id IS
    'MarineTraffic vessel id. A SEPARATE identifier space from mmsi — MT rows carry no '
    'mmsi/imo/callsign (measured 100 % NULL), so these events cannot be joined to the AIS '
    'identity layer and are excluded from the hull composite.';

-- ---------------------------------------------------------------------------
-- 2. The composite is GUARDED, not left to NULL semantics
-- ---------------------------------------------------------------------------
-- A dated, checksum-verified replacement of both views (the 029/036/037 precedent), not a
-- bare CREATE OR REPLACE. ONE semantic change: `AND e.source = 'aisstream'` in the sig CTE.
-- Everything else is migration 037's definition verbatim.
DROP VIEW maritime_hull_composite;
DROP VIEW maritime_hull_events;

CREATE VIEW maritime_hull_events AS
 WITH sig AS (
         SELECT e.event_type,
            e.mmsi,
            e.imo,
            e.start_ts,
            e.end_ts,
            e.duration_s,
            e.detail,
                CASE
                    WHEN vi.imo_quality = 'malformed'::text THEN e.mmsi
                    ELSE COALESCE(e.imo, vi.imo, e.mmsi)
                END AS hull
           FROM maritime_events e
             LEFT JOIN vessel_identity vi ON vi.mmsi = e.mmsi
          WHERE
            -- ⛔ MT-DETECT-1: AIS-derived events ONLY. An MT event has no mmsi and no imo,
            -- so its hull would be NULL and every such event would group into one bogus
            -- "hull". MT tracks are queryable in their own right (maritime_events.source)
            -- and are simply not hull-stitchable.
            e.source = 'aisstream'::text
            AND ((e.event_type = ANY (ARRAY['rendezvous'::text, 'identity_change'::text])) OR e.event_type = 'ais_gap'::text AND e.classification = 'candidate'::text OR e.event_type = 'gnss_anomaly'::text AND e.classification = 'candidate'::text OR e.event_type = 'imo_integrity'::text AND e.classification = 'candidate'::text OR e.event_type = 'zone_dwell'::text AND e.classification = 'candidate'::text)
        )
 SELECT sig.hull,
    sig.event_type,
    sig.mmsi AS via_mmsi,
    sig.imo,
    sig.start_ts,
    sig.end_ts,
    sig.duration_s,
    sig.detail,
    false AS as_partner
   FROM sig
UNION ALL
 SELECT
        CASE
            WHEN vq.imo_quality = 'malformed'::text THEN s.detail ->> 'partner_mmsi'::text
            ELSE COALESCE(vq.imo, s.detail ->> 'partner_mmsi'::text)
        END AS hull,
    'rendezvous'::text AS event_type,
    s.detail ->> 'partner_mmsi'::text AS via_mmsi,
    vq.imo,
    s.start_ts,
    s.end_ts,
    s.duration_s,
    s.detail,
    true AS as_partner
   FROM sig s
     LEFT JOIN vessel_identity vq ON vq.mmsi = (s.detail ->> 'partner_mmsi'::text)
  WHERE s.event_type = 'rendezvous'::text AND (s.detail ->> 'partner_mmsi'::text) IS NOT NULL;

CREATE VIEW maritime_hull_composite AS
 SELECT hull,
    count(*) FILTER (WHERE event_type = 'ais_gap'::text) AS n_gaps,
    count(*) FILTER (WHERE event_type = 'rendezvous'::text) AS n_rendezvous,
    count(*) FILTER (WHERE event_type = 'identity_change'::text) AS n_identity_changes,
    count(*) FILTER (WHERE event_type = 'gnss_anomaly'::text) AS n_gnss_anomaly,
    count(*) FILTER (WHERE event_type = 'imo_integrity'::text) AS n_imo_integrity,
    count(*) FILTER (WHERE event_type = 'zone_dwell'::text) AS n_zone_dwell,
    count(DISTINCT event_type) AS n_pattern_types,
    min(start_ts) AS first_event_ts,
    max(start_ts) AS last_event_ts,
    array_agg(DISTINCT via_mmsi) AS mmsis
   FROM maritime_hull_events
  GROUP BY hull;

-- ---------------------------------------------------------------------------
-- 3. One shape for both tracks
-- ---------------------------------------------------------------------------
-- The detectors read `vessel_states` in raw SQL. Rather than fork each of them, this view
-- presents BOTH sources in one shape, so a detector changes a table name and gains a
-- source filter instead of growing a second query to drift from the first.
--
-- ⚠ COLUMNS ABSENT FROM A SOURCE ARE NULL, NOT DEFAULTED. `nav_status` is NULL for every MT
-- row (measured 0 % populated) and `draught` likewise — and a detector that needs them must
-- REFUSE the source rather than treat NULL as "underway" or "unladen". That refusal is
-- explicit in backend/track_source.py; this view only makes the absence visible.
CREATE VIEW vessel_tracks AS
    -- ⚠ `name` comes from vessel_states itself, which carries it — the first draft joined
    -- vessel_identity for it and Postgres rejected the view as ambiguous. Only the
    -- registration FLAG needs the identity table, since vessel_states has no flag column.
    SELECT 'aisstream'::text  AS source,
           'mmsi'::text       AS key_kind,
           vs.mmsi            AS vessel_key,
           vs.ts, vs.latitude, vs.longitude, vs.speed, vs.course, vs.heading,
           vs.nav_status, vs.draught, vs.name, vi.flag_country AS flag,
           vs.country, vs.near_country
      FROM vessel_states vs
      LEFT JOIN vessel_identity vi ON vi.mmsi = vs.mmsi
UNION ALL
    SELECT 'marinetraffic'::text,
           'ship_id'::text,
           mt.ship_id,
           mt.ts, mt.latitude, mt.longitude, mt.speed, mt.course, mt.heading,
           mt.nav_status, mt.draught, mt.name, mt.flag,
           mt.country, mt.near_country
      FROM mt_vessel_states mt;

COMMENT ON VIEW vessel_tracks IS
    'MT-DETECT-1 — AIS and MarineTraffic tracks in one shape, tagged by source/key_kind. '
    'Fields a source does not carry are NULL (MT: nav_status, draught, imo, mmsi). A '
    'detector requiring such a field must refuse that source — see backend/track_source.py.';
