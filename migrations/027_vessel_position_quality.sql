-- 027_vessel_position_quality.sql
--
-- Maritime layer G1 — ADDITIVE per-position GNSS quality columns on
-- vessel_states, the maritime realisation of the position-quality axis that
-- migration 012 established for the aircraft state_vectors (position_quality /
-- validation_flags) and that migration 021 DELIBERATELY DEFERRED for vessels
-- ("AIS spoof/identity checks belong to a later phase"). This is that phase.
--
-- These columns are the per-fix plausibility verdict + evidence, produced by the
-- pure detector backend/gnss_anomaly.py (method 'gnss-quality-v1') and written by
-- the G2 backfill/runner. They are what the behavioural detectors will later
-- FILTER on (a spoofed/impossible position is FLAGGED, never deleted — the
-- data-quality-as-event discipline from the G0 assessment).
--
--   * position_quality — the worst verdict any rule produced for the fix:
--     'plausible' (checked, clean) | 'suspect' | 'implausible'. NULL = NOT YET
--     CHECKED (distinct from 'plausible' = checked-and-clean). CHECK-constrained
--     to that closed set.
--   * anomaly_flags    — the union of the reasons that fired, from the closed set
--     {impossible_speed, on_land, teleport_return, jamming_zone}, enforced by
--     array-containment (<@). jamming_zone is admitted by the constraint NOW but
--     is NOT emitted until G3 (area-jamming zones) — included here deliberately so
--     G3 needs no second migration on this constraint.
--   * implied_speed_kn — evidence: the POSITION-derived speed of the incoming
--     segment (haversine / Δt), never the reported AIS SOG (which carries the
--     102.2/1023 'not available' sentinel and zeros under spoofing). CHECK >= 0.
--   * inland_km        — evidence: depth of the fix inside the seven-country land
--     mass (distance to the UNION boundary; NULL at sea). CHECK >= 0.
--   * quality_method / quality_computed_at — provenance, identical discipline to
--     every other layer (M1 geo_source, A1/A2/A3 method).
--
-- CONSISTENCY (a verdict without reasons, or reasons without a verdict, is
-- UNREPRESENTABLE): if position_quality is 'suspect'/'implausible' then
-- anomaly_flags MUST be non-null and non-empty; if 'plausible' then anomaly_flags
-- MUST be null or empty. A NULL verdict (not-yet-checked) is unconstrained.
--
-- STRICTLY ADDITIVE — final, not a patch:
--   * every column is NULLABLE with no default, so all existing vessel_states rows
--     and the only current source ('aisstream') stay valid and untouched, reading
--     back NULL until G2 backfills;
--   * the natural key PRIMARY KEY (mmsi, ts, source) is NOT touched, so ingest
--     idempotency (ON CONFLICT DO NOTHING) is preserved exactly;
--   * the hypertable, compression settings and policy are NOT touched. Compression
--     IS enabled on this table (segmentby mmsi,source; 2 compressed chunks at
--     write time) — ADD COLUMN nullable-no-default is a catalog-only change and
--     the CHECK constraints validate trivially against the all-NULL new columns,
--     so this runs transactionally like migration 021 (no decompress dance).
--
-- NO PostGIS/geometry (same as every layer) — inland_km is a scalar computed
-- upstream in Python and archived here as its result.
--
-- Reversal (migrations are forward-only): DROP the six columns (their CHECK
-- constraints drop with them).

ALTER TABLE vessel_states
    ADD COLUMN position_quality    TEXT,               -- plausible|suspect|implausible (NULL=unchecked)
    ADD COLUMN anomaly_flags       TEXT[],             -- subset of the closed flag set
    ADD COLUMN implied_speed_kn    DOUBLE PRECISION,   -- position-derived incoming speed (knots), evidence
    ADD COLUMN inland_km           DOUBLE PRECISION,   -- depth inside the 7-country land mass (km), evidence
    ADD COLUMN quality_method      TEXT,               -- provenance: 'gnss-quality-v1'
    ADD COLUMN quality_computed_at TIMESTAMPTZ;         -- when the verdict was computed

ALTER TABLE vessel_states
    ADD CONSTRAINT vessel_states_position_quality_ck
        CHECK (position_quality IS NULL
               OR position_quality IN ('plausible', 'suspect', 'implausible')),
    ADD CONSTRAINT vessel_states_anomaly_flags_ck
        CHECK (anomaly_flags IS NULL
               OR anomaly_flags <@ ARRAY['impossible_speed', 'on_land',
                                         'teleport_return', 'jamming_zone']::text[]),
    ADD CONSTRAINT vessel_states_implied_speed_kn_ck
        CHECK (implied_speed_kn IS NULL OR implied_speed_kn >= 0),
    ADD CONSTRAINT vessel_states_inland_km_ck
        CHECK (inland_km IS NULL OR inland_km >= 0),
    ADD CONSTRAINT vessel_states_quality_consistency_ck
        CHECK (
            CASE
                WHEN position_quality IN ('suspect', 'implausible')
                    THEN anomaly_flags IS NOT NULL AND cardinality(anomaly_flags) >= 1
                WHEN position_quality = 'plausible'
                    THEN anomaly_flags IS NULL OR cardinality(anomaly_flags) = 0
                ELSE TRUE   -- NULL verdict = not yet checked; flags unconstrained
            END
        );
