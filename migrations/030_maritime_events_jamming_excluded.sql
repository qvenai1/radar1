-- 030_maritime_events_jamming_excluded.sql
--
-- Maritime layer G4b — admit 'jamming-excluded' into maritime_events_class_ck, the
-- classification value for A1's FIFTH confound: a gap that is a GNSS-anomaly artifact
-- (the vessel transmitted implausible/spoofed positions during it, so the plausible-
-- stream silence is not real going-dark, and/or it sits in an active area-jamming
-- zone). It excludes right after outage in A1's waterfall.
--
-- WHY A DISTINCT VALUE (not detail-subtype on 'outage-excluded'): A1's classification
-- values are PEER REASONS — each a distinct why-not-candidate (outage-excluded vs
-- stationary vs edge), not subtypes of one. 'outage-excluded' has specific documented
-- semantics (a feed_disconnect outage, with detail outage_basis recorded/inferred/
-- open); a GNSS-jamming/spoofing artifact is a mechanistically different reason (the
-- feed was UP; the vessel's GNSS was denied). Overloading 'outage-excluded' would
-- make a study filtering it silently mix feed drops with jammed/spoofed vessels — an
-- interpretation hazard. A distinct honest label keeps the two separable. It is
-- composite-excluded FOR FREE (the composite admits ais_gap only at
-- classification='candidate'), exactly like outage-excluded/edge/stationary.
--
-- Drop + re-add is transactional; every existing row is in the prior six-value set
-- (a strict subset), so ADD CONSTRAINT validates instantly. maritime_events is
-- uncompressed. Reversal (forward-only): re-add the constraint without this value
-- (only valid once no row uses 'jamming-excluded').

ALTER TABLE maritime_events DROP CONSTRAINT maritime_events_class_ck;

ALTER TABLE maritime_events ADD CONSTRAINT maritime_events_class_ck
    CHECK (classification IN (
        'candidate', 'outage-excluded', 'edge', 'stationary',   -- A1 (024)
        'area-jamming', 'glitch',                               -- GNSS episodes (028)
        'jamming-excluded'                                      -- A1 fifth confound (G4b)
    ));
