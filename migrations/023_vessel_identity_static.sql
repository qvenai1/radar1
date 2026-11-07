-- 023_vessel_identity_static.sql
--
-- Maritime enrichment M2b — promote the already-arriving Class-A AIS STATIC
-- identity (ShipStaticData / AIS type 5) into a durable, queryable identity
-- SPINE on vessel_identity (M2a's table). This is a PERSISTENCE + PROMOTION
-- layer, not a re-subscription: the collector already receives and merges
-- static messages; M2b materializes the current-best identity per MMSI so it is
-- queryable and joinable, instead of scattered across the vessel_states time
-- series (where it lands only when a static happened to precede an archived
-- position — the ~81% vessel-level scatter M2b consolidates).
--
-- COLUMNS (additive, all NULLABLE — identity accumulates over time):
--   * name, ship_type, ship_type_code, callsign — slow-changing identity.
--   * imo — the PERMANENT hull id and the DURABLE cross-reference: it survives
--     re-flagging (which changes the MMSI but not the IMO) and will key M2c
--     external-registry lookups. INDEXED (not PK — mmsi stays the AIS-native PK;
--     imo is nullable because ~23% of Class-A MMSIs have not broadcast it yet /
--     do not carry one — stored NULL, never guessed; coverage accumulates).
--   * length_m, beam_m — from the AIS Dimension field (A+B / C+D), which was in
--     the message all along and merely dropped before M2b (parser now extracts
--     it). Metres; only positive values are meaningful (a DB CHECK enforces it).
--   * identity_source — provenance of the identity block = 'ais-static',
--     DISTINCT from flag_source ('mmsi-mid', M2a) and from vessel_states.source
--     (the feed). A later layer (M2c) may override selected fields from an
--     external registry and would then record its own per-field source.
--   * identity_updated_at — LATEST-WINS timestamp: set only when a promoted
--     value actually changes (the ingest/backfill upserts carry a WHERE-changed
--     guard), so re-ingest / re-backfill causes 0 spurious writes.
--
-- VOYAGE STATE (destination, draught, eta) is DELIBERATELY EXCLUDED from the
-- spine: it changes per voyage and stays on the vessel_states time series. The
-- spine is slow-changing identity only.
--
-- POINT-IN-TIME CAVEAT (documented in code + here): name / ship_type / callsign
-- are CURRENT-BEST at time of observation — a vessel can be renamed or
-- re-registered. IMO is the stable anchor within the spine; treat the textual
-- identity as time-of-observation state, not a permanent label.
--
-- STRICTLY ADDITIVE: columns on vessel_identity only. The M2a columns
-- (flag_country, station_type, flag_source, derived_at) and its CHECKs, plus
-- vessel_states and its key (mmsi, ts, source) and every other table, are
-- UNTOUCHED. Reversal (forward-only migrations): DROP COLUMN name, ship_type,
-- ship_type_code, callsign, imo, length_m, beam_m, identity_source,
-- identity_updated_at (and DROP INDEX vessel_identity_imo_idx).

ALTER TABLE vessel_identity
    ADD COLUMN name                TEXT,
    ADD COLUMN ship_type           TEXT,
    ADD COLUMN ship_type_code      SMALLINT,
    ADD COLUMN callsign            TEXT,
    ADD COLUMN imo                 TEXT,                 -- durable hull id (indexed cross-ref)
    ADD COLUMN length_m            DOUBLE PRECISION,     -- A+B (metres)
    ADD COLUMN beam_m              DOUBLE PRECISION,     -- C+D (metres)
    ADD COLUMN identity_source     TEXT,                 -- 'ais-static'
    ADD COLUMN identity_updated_at TIMESTAMPTZ,          -- latest-wins (on change)
    ADD CONSTRAINT vessel_identity_length_ck CHECK (length_m IS NULL OR length_m > 0),
    ADD CONSTRAINT vessel_identity_beam_ck   CHECK (beam_m   IS NULL OR beam_m   > 0);

-- IMO is the durable cross-reference the behavioural / M2c layers will key on.
CREATE INDEX vessel_identity_imo_idx ON vessel_identity (imo);
