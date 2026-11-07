-- 018_satellite_strategic.sql
--
-- Satellite RESEARCH layer Sat-UCS: STRATEGIC CLASSIFICATION from the UCS (Union
-- of Concerned Scientists) Satellite Database — the analytical axis (who operates
-- a satellite, for what purpose, for which user class) that SATCAT/Space-Track
-- lack. This is a DATED RESEARCH SNAPSHOT, not a current-state source.
--
-- A SEPARATE table (not columns on satellite_info) is the correct design here,
-- for research integrity:
--   * DIFFERENT VINTAGE — UCS is frozen at 2023-05-01; satellite_info is SATCAT
--     as of its own snapshot (2026-07). Mixing vintages in one row would let a
--     study silently cite 2023 strategic data as if current. Keeping them in
--     separate, independently-dated tables forces every query to join
--     deliberately: "SATCAT identity (2026) + UCS strategic class (2023)".
--   * DIFFERENT SOURCE + large field set — UCS adds ~13 strategic fields at a
--     distinct provenance; a separate table keeps satellite_info authoritative
--     for current identity and this table authoritative for the 2023 strategic
--     baseline. Neither overwrites the other.
--
-- KNOWN, DOCUMENTED LIMITATION: UCS paused updates; 2023-05-01 is the final
-- snapshot. This layer is for STRATEGIC/STRUCTURAL analysis and a historical
-- baseline — explicitly NOT current state. The vintage is stored on EVERY row
-- (ucs_snapshot_date) so downstream studies handle it correctly and never mix
-- data of different vintages.
--
-- JOIN (verified against the live UCS file, 7560 rows, all with numeric NORAD +
-- COSPAR): primary key is NORAD Number (direct join to our norad_id); COSPAR
-- Number (= international designator) is the fallback via satellite_info.
-- joined_via records WHICH key matched each row, so a study knows the match
-- basis. Coverage of our 908 catalogued objects: 701 via NORAD, 2 via INTLDES,
-- 205 unmatched (post-2023 objects UCS never saw — left unclassified, no guess).
--
-- PROVENANCE (a methodological requirement, not a nicety): every row carries
-- source='ucs', ucs_snapshot_date, joined_via. UCS and SATCAT are complementary
-- layers at distinct vintages — this table NEVER overwrites SATCAT identity.
--
-- STRICTLY ADDITIVE: a new table only. satellite_info (S1) / satellite_elements
-- / satellite_history / satellite_reentry (S2c) and their keys are UNTOUCHED. No
-- FK (a study may keep strategic rows even if catalog membership changes).
-- Reversal (forward-only migrations): DROP TABLE satellite_strategic.

CREATE TABLE satellite_strategic (
    norad_id              INTEGER PRIMARY KEY,
    users                 TEXT,              -- civil | commercial | government | military (+combos) — key axis
    purpose               TEXT,              -- e.g. Communications, Earth Observation, Navigation
    detailed_purpose      TEXT,
    operator              TEXT,              -- Operator/Owner
    operator_country      TEXT,              -- Country of Operator/Owner
    registry_country      TEXT,              -- Country/Org of UN Registry (country of registration)
    orbit_class           TEXT,              -- UCS Class of Orbit (LEO/MEO/GEO/Elliptical)
    orbit_type            TEXT,              -- UCS Type of Orbit (e.g. Sun-Synchronous)
    launch_mass_kg        DOUBLE PRECISION,
    power_watts           DOUBLE PRECISION,
    expected_lifetime_yrs DOUBLE PRECISION,
    contractor            TEXT,
    launch_site           TEXT,
    -- provenance (research integrity)
    source                TEXT NOT NULL DEFAULT 'ucs',
    ucs_snapshot_date     DATE,              -- the UCS DB version (2023-05-01)
    joined_via            TEXT,              -- 'norad' | 'intldes' — how this row matched
    fetched_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX satellite_strategic_users_idx     ON satellite_strategic (users);
CREATE INDEX satellite_strategic_purpose_idx   ON satellite_strategic (purpose);
CREATE INDEX satellite_strategic_opcountry_idx ON satellite_strategic (operator_country);
