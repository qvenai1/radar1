-- 033_vessel_xref.sql
--
-- Identity bridge between the two maritime identity spaces:
--   * AIS keys on MMSI            (vessel_states, vessel_identity)
--   * MarineTraffic keys on SHIP_ID (mt_vessel_states, migration 032)
--
-- A vessel exists in both worlds but the tile feed usually omits MMSI, so the
-- two cannot be joined directly. This table records the link WHENEVER it is
-- genuinely established — never guessed — so an AIS×MarineTraffic cross-view
-- (e.g. "MarineTraffic sees a hull AIS has gone dark on", or corroborating a
-- suspected AIS spoof) becomes a plain join once a link exists.
--
-- WHY A DEDICATED TABLE (the established scraplan pattern): a cross-identity
-- fact with its own provenance does not belong on either time-series. It is the
-- sea-going analogue of the derived-facts tables (vessel_identity, satellite_*):
-- keyed on the natural id, carrying its own source + timestamps.
--
-- POPULATION (additive, latest-wins on real change):
--   * link_source='marinetraffic-feed' — the MarineTraffic record itself carried
--     an MMSI (the strongest, direct link).
--   * link_source='ais-correlation' (a LATER phase) — a derived match by
--     position/name/time; recorded with its own provenance so a derived link is
--     never mistaken for a fed one.
--
--   name / flag are the MarineTraffic-side identity kept here to aid correlation
--   and give the row meaning even before an mmsi is known (mmsi stays NULL then).
--
-- INTEGRITY: ship_id is the PK (a hull has one MarineTraffic id); a partial
-- index on mmsi supports the reverse lookup and keeps the (often NULL) column
-- cheap. mmsi is deliberately NOT UNIQUE — honest about the rare real-world case
-- of an id being re-pointed — and no FK is imposed on vessel_states (an MMSI may
-- be linked before AIS has ever seen it).
--
-- STRICTLY ADDITIVE: a NEW table only. Reversal: DROP TABLE vessel_xref.

CREATE TABLE vessel_xref (
    ship_id      TEXT PRIMARY KEY,                       -- MarineTraffic internal id
    mmsi         TEXT,                                   -- linked MMSI, else NULL (never guessed)
    name         TEXT,                                   -- MarineTraffic-side name (correlation aid)
    flag         TEXT,                                   -- MarineTraffic-reported flag
    link_source  TEXT NOT NULL DEFAULT 'marinetraffic-feed',  -- how the row/link was established
    linked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),     -- when a real mmsi link was first set
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()      -- last change to this row
);

CREATE INDEX vessel_xref_mmsi_idx ON vessel_xref (mmsi) WHERE mmsi IS NOT NULL;
