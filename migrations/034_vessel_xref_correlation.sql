-- 034_vessel_xref_correlation.sql
--
-- Extend the identity bridge (migration 033) to hold DERIVED AIS×MarineTraffic
-- links, not only feed links. MarineTraffic almost never broadcasts an MMSI, so
-- the two sources are correlated by evidence they DO share — name (the strong
-- anchor) corroborated by position-over-time and dimensions (backend/vessel_crossref.py).
-- A derived link carries its confidence + evidence so it is never mistaken for a
-- broadcast fact.
--
-- link_source distinguishes the provenance already:
--   * 'marinetraffic-feed'  — MarineTraffic itself carried the MMSI (direct, strongest).
--   * 'ais-correlation'      — derived here; the columns below qualify it.
--
-- READING RULE (documented, and enforced by consumers, not the DB): a row with
-- match_confidence IN ('strong','probable') is a CONFIRMED derived link; 'weak'
-- is a RECORDED CANDIDATE (name matched but positions are kinematically
-- inconsistent, or dimensions conflict — a likely name collision / possible
-- spoof), NOT a confirmed identity. match_ambiguous flags a name that matched
-- more than one equally-good counterpart. So the same table honestly holds both
-- confident links and the interesting "could-not-confirm" leads.
--
-- A feed link is authoritative and must never be overwritten by a derived one —
-- the runner's upsert guards on link_source, and these columns stay NULL on a
-- feed row.
--
-- STRICTLY ADDITIVE: nullable columns only; vessel_xref's PK, its existing rows,
-- and the feed path (migration 033 / maritime.db) are untouched.
-- Reversal: DROP COLUMN match_confidence, match_score, match_distance_km,
-- match_dt_minutes, match_ambiguous, match_reason.

ALTER TABLE vessel_xref
    ADD COLUMN match_confidence  TEXT,              -- 'strong' | 'probable' | 'weak' (NULL for feed links)
    ADD COLUMN match_score       DOUBLE PRECISION,  -- 0..1 corroboration score (ranking / resolution)
    ADD COLUMN match_distance_km DOUBLE PRECISION,  -- separation at the closest-in-time fix pair
    ADD COLUMN match_dt_minutes  DOUBLE PRECISION,  -- time gap of that closest pair
    ADD COLUMN match_ambiguous   BOOLEAN,           -- name matched >1 equally-good counterpart
    ADD COLUMN match_reason      TEXT;              -- human-readable basis of the tier

ALTER TABLE vessel_xref
    ADD CONSTRAINT vessel_xref_match_confidence_ck
        CHECK (match_confidence IS NULL OR match_confidence IN ('strong','probable','weak'));

-- Query dimension: confirmed derived links, best first.
CREATE INDEX vessel_xref_confidence_idx ON vessel_xref (match_confidence, match_score DESC)
    WHERE match_confidence IS NOT NULL;
