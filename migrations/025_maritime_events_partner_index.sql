-- 025_maritime_events_partner_index.sql
--
-- Behavioural A2 (rendezvous) support — a rendezvous is a PAIR, stored as ONE
-- canonical row in maritime_events: event_type='rendezvous', mmsi = the SMALLER
-- of the two MMSIs (deterministic, so a pair maps to exactly one row and the
-- recompute-replace PK (event_type, mmsi, start_ts) stays valid), with the other
-- vessel in detail JSONB (partner_mmsi, partner_imo, partner_name, partner_flag,
-- partner_ship_type, min_distance_m, max_duration_min, both_max_speed,
-- offshore_km, suspicion_rank).
--
-- This index makes "events involving vessel X" cheap from EITHER side: X may be
-- the canonical `mmsi` (already indexed by the PK) OR the `partner_mmsi` inside
-- detail — this expression index covers the latter.
--
-- STRICTLY ADDITIVE: an index only, no column/row/key change; maritime_events
-- and every other table are untouched. Reversal (forward-only): DROP INDEX
-- maritime_events_partner_idx.

CREATE INDEX maritime_events_partner_idx
    ON maritime_events ((detail ->> 'partner_mmsi'));
