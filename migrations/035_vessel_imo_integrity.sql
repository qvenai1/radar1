-- 035_vessel_imo_integrity.sql
--
-- Maritime layer IMO-1 — ADDITIVE per-identity IMO-integrity columns on
-- vessel_identity, the identity-spine analog of the position-quality axis that
-- migration 027 established for vessel_states (position_quality / anomaly_flags /
-- provenance). The IMO number is the spine the composite (026/029) stitches on —
-- COALESCE(e.imo, vi.imo, e.mmsi) — but it is SELF-REPORTED AIS static, therefore
-- spoofable. This axis is the internal-consistency verdict on each stored IMO.
--
-- These columns are the per-identity verdict + evidence, produced by the pure
-- detector backend/imo_integrity.py (method 'imo-integrity-v1') and written by the
-- backfill runner (scripts/backfill_imo_integrity.py). They are what the IMO-2
-- consumers (composite/API annotation, the latent collision event) will FILTER /
-- ANNOTATE on — a questionable spine is FLAGGED, never deleted (the flag-not-delete
-- discipline carried from G1/027).
--
-- SIZING (from IMO-pre + the IMO-1 Step-0 JSONL-archive scan — the numbers that
-- justify a LATENT guard, not a large detector): of 2,896 distinct IMOs, 2,850
-- pass checksum, 36 are wrong-length (AIS integer-decode artifacts, not laundering),
-- 10 fail checksum. 14 IMOs are shared across >1 MMSI — ALL clean temporal switches
-- (0 time-overlap collisions; these are the composite's intended re-flag stitching,
-- which A3 already records). The immutable archive (39,876 static records, 2,581
-- MMSIs) shows ZERO MMSIs broadcasting more than one distinct IMO. So the collision
-- and mmsi_multi_imo signals are genuinely 0 today — their flags are admitted here
-- as LATENT GUARDS (exactly as migration 027 admitted jamming_zone before G3 emitted
-- it), so no second migration is needed if the live data later produces one.
--
--   * imo_quality — the worst verdict for the stored IMO:
--       'valid'        — well-formed 7-digit, passes the check digit (+ possibly a
--                        benign 'reflag_clean' annotation; see the CHECK below);
--       'checksum_fail'— well-formed 7-digit, FAILS the check digit;
--       'malformed'    — not a well-formed IMO (wrong length / non-numeric /
--                        placeholder);
--       'contested'    — attribution anomaly (collision / mmsi_multi_imo — latent).
--     NULL = NOT YET CHECKED, and ALSO the resting state of a NULL-imo identity:
--     "no IMO reported" (848 rows, ~23%) is distinct from "reported and checked".
--     Mirroring 027's null-position rows, a NULL-imo identity keeps imo_method NULL
--     too (nothing to check) — the runner asserts imo_method IS NULL <=> imo IS NULL.
--   * imo_flags — the union of reasons, from the CLOSED set enforced by containment
--     (<@): the four value defects (checksum_fail / wrong_length / non_numeric /
--     placeholder), the benign re-flag annotation (reflag_clean), and the latent
--     attribution guards (mmsi_collision / mmsi_multi_imo / dimension_conflict).
--   * imo_method / imo_computed_at — provenance, identical discipline to 027/024.
--
-- CONSISTENCY (a verdict without justification, or an anomaly flag under a clean
-- verdict, is UNREPRESENTABLE): a 'valid' verdict may carry NO flags OR ONLY the
-- benign 'reflag_clean' (a clean re-flag is valid identity, not an anomaly — this is
-- the ONE reconciliation of the IMO-1 spec, which asked both that reflag_clean be
-- emitted AND that reflag_clean's verdict be 'valid'); any NON-valid verdict MUST
-- carry >= 1 flag; a NULL verdict is unconstrained. cardinality() is used, NOT
-- array_length() — the G1 lesson: array_length('{}',1) returns NULL and would let an
-- empty-flags non-valid row slip through the guard.
--
-- STRICTLY ADDITIVE — final, not a patch:
--   * every column is NULLABLE with no default, so all existing vessel_identity rows
--     stay valid and untouched, reading back NULL until the backfill runs;
--   * the natural key PRIMARY KEY (mmsi) and the M2a/M2b columns + their CHECKs are
--     NOT touched; vessel_identity is a tiny plain table (~3,758 rows) — no
--     hypertable / compression / chunk concerns (unlike 027 on vessel_states).
--
-- Reversal (migrations are forward-only): DROP the four columns (their CHECK
-- constraints drop with them).

ALTER TABLE vessel_identity
    ADD COLUMN imo_quality     TEXT,               -- valid|checksum_fail|malformed|contested (NULL=unchecked / no-imo)
    ADD COLUMN imo_flags       TEXT[],             -- subset of the closed flag set
    ADD COLUMN imo_method      TEXT,               -- provenance: 'imo-integrity-v1'
    ADD COLUMN imo_computed_at TIMESTAMPTZ;         -- when the verdict was computed

ALTER TABLE vessel_identity
    ADD CONSTRAINT vessel_identity_imo_quality_ck
        CHECK (imo_quality IS NULL
               OR imo_quality IN ('valid', 'checksum_fail', 'malformed', 'contested')),
    ADD CONSTRAINT vessel_identity_imo_flags_ck
        CHECK (imo_flags IS NULL
               OR imo_flags <@ ARRAY['checksum_fail', 'wrong_length', 'non_numeric',
                                     'placeholder', 'reflag_clean', 'mmsi_collision',
                                     'mmsi_multi_imo', 'dimension_conflict']::text[]),
    ADD CONSTRAINT vessel_identity_imo_consistency_ck
        CHECK (
            CASE
                WHEN imo_quality = 'valid'
                    -- clean: no flags, OR only the benign re-flag annotation
                    THEN imo_flags IS NULL OR imo_flags <@ ARRAY['reflag_clean']::text[]
                WHEN imo_quality IN ('checksum_fail', 'malformed', 'contested')
                    THEN imo_flags IS NOT NULL AND cardinality(imo_flags) >= 1
                ELSE TRUE   -- NULL verdict = unchecked / no IMO; flags unconstrained
            END
        );
