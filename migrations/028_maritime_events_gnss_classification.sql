-- 028_maritime_events_gnss_classification.sql
--
-- Maritime layer G4a — admit the GNSS-anomaly EXCLUDED tiers into the
-- maritime_events.classification CHECK. The generalized events table gains a new
-- event_type='gnss_anomaly' (per-vessel implausible episodes); this migration only
-- widens the shared classification constraint so those rows can be written.
--
-- The classification AXIS is unchanged (established by migration 024): it is the
-- TIER (candidate vs excluded-confound), never the pattern subtype — the subtype
-- lives in detail->>'gnss_type' (exactly as A3 put change_type in detail). The
-- three GNSS tiers, from the G4a Step-0 measurement on the frozen set:
--   * 'candidate'    — an INDIVIDUAL vessel: a coherent real track punctuated by
--                      implausible fixes (a real vessel spoofed/jammed; actor OR
--                      victim → an investigation lead). detail gnss_type='individual'.
--   * 'area-jamming' — an environmental-jamming VICTIM: the episode sits in a
--                      cell x window with many distinct implausible vessels (>=10;
--                      the Amman decoy peaked at 156). detail gnss_type='area'.
--   * 'glitch'       — a source with NO plausible track ever (all fixes implausible;
--                      the ELSEC_TEST fixed transmitter + single-fix on_land ghosts,
--                      6 sources / 886 positions). detail gnss_type='glitch'.
--
-- 'candidate' remains the ONLY value the (future G4b) composite view clause will
-- admit (`event_type='gnss_anomaly' AND classification='candidate'`), so BOTH new
-- excluded values ('area-jamming', 'glitch') are composite-excluded FOR FREE by the
-- same rule that already excludes A1's outage-excluded/edge/stationary. A jamming
-- VICTIM and a bad transmitter never enter the actor-centric composite.
--
-- Drop + re-add is transactional (migrate.py default) and cheap: every existing
-- maritime_events row carries a classification in the OLD set
-- (candidate/outage-excluded/edge/stationary), which is a strict subset of the new
-- set, so ADD CONSTRAINT validates instantly with no row rewrite. maritime_events
-- is a plain table (no compression), so no decompress dance. Reversal (forward-only
-- migrations): drop and re-add the constraint with the original four values.

ALTER TABLE maritime_events DROP CONSTRAINT maritime_events_class_ck;

ALTER TABLE maritime_events ADD CONSTRAINT maritime_events_class_ck
    CHECK (classification IN (
        'candidate', 'outage-excluded', 'edge', 'stationary',  -- existing tiers (024)
        'area-jamming', 'glitch'                               -- G4a GNSS excluded tiers
    ));
